from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from packaging_tools.artifact_manifest import (
    ARTIFACT_MANIFEST_SCHEMA,
    ArtifactManifest,
    ArtifactManifestError,
    create_artifact_manifest,
    write_artifact_manifest,
)
from packaging_tools.contracts import canonical_json_bytes, sha256_file


def _entry(manifest: ArtifactManifest, path: str) -> dict[str, object]:
    return next(
        entry
        for entry in manifest.as_json_object()["entries"]
        if entry["path"] == path
    )


def _symlink_or_skip(link: Path, target: str) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")


def test_manifest_has_exact_canonical_sorted_fields_and_candidate_id(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "candidate"
    internal = artifact / "_internal"
    internal.mkdir(parents=True)
    internal.chmod(0o750)
    payload = internal / "payload.bin"
    payload.write_bytes(b"payload")
    payload.chmod(0o640)
    link = artifact / "payload-link"
    _symlink_or_skip(link, "_internal/payload.bin")

    manifest = create_artifact_manifest(artifact)
    expected = {
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "entries": [
            {
                "entry_type": "directory",
                "mode": f"{internal.lstat().st_mode & 0o7777:04o}",
                "path": "_internal",
            },
            {
                "entry_type": "regular-file",
                "mode": f"{payload.lstat().st_mode & 0o7777:04o}",
                "path": "_internal/payload.bin",
                "sha256": hashlib.sha256(b"payload").hexdigest(),
                "size": 7,
            },
            {
                "entry_type": "symlink",
                "mode": f"{link.lstat().st_mode & 0o7777:04o}",
                "path": "payload-link",
                "size": link.lstat().st_size,
                "target": "_internal/payload.bin",
            },
        ],
    }

    assert manifest.as_json_object() == expected
    assert manifest.canonical_bytes == canonical_json_bytes(expected)
    assert manifest.candidate_id == hashlib.sha256(manifest.canonical_bytes).hexdigest()
    assert create_artifact_manifest(artifact).canonical_bytes == manifest.canonical_bytes
    assert b"mtime" not in manifest.canonical_bytes
    assert str(tmp_path).encode() not in manifest.canonical_bytes


def test_manifest_is_independent_of_creation_order_and_root_name(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first-root"
    second = tmp_path / "second-root"
    for root, names in ((first, ("b.bin", "a.bin")), (second, ("a.bin", "b.bin"))):
        data = root / "data"
        data.mkdir(parents=True)
        data.chmod(0o755)
        for name in names:
            path = data / name
            path.write_bytes(name.encode("ascii"))
            path.chmod(0o644)

    first_manifest = create_artifact_manifest(first)
    second_manifest = create_artifact_manifest(second)

    assert first_manifest.canonical_bytes == second_manifest.canonical_bytes
    assert first_manifest.candidate_id == second_manifest.candidate_id


def test_file_and_directory_mtime_do_not_change_candidate_id(tmp_path: Path) -> None:
    artifact = tmp_path / "candidate"
    directory = artifact / "data"
    directory.mkdir(parents=True)
    payload = directory / "payload.bin"
    payload.write_bytes(b"stable")
    before = create_artifact_manifest(artifact)

    os.utime(payload, ns=(1_800_000_000_000_000_000,) * 2)
    os.utime(directory, ns=(1_700_000_000_000_000_000,) * 2)
    after = create_artifact_manifest(artifact)

    assert after.candidate_id == before.candidate_id


def test_directory_symlink_is_recorded_once_and_never_traversed(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "candidate"
    real_directory = artifact / "real-directory"
    real_directory.mkdir(parents=True)
    (real_directory / "payload.bin").write_bytes(b"payload")
    directory_link = artifact / "directory-link"
    _symlink_or_skip(directory_link, "real-directory")

    manifest = create_artifact_manifest(artifact)
    paths = [entry["path"] for entry in manifest.as_json_object()["entries"]]

    assert paths == sorted(paths)
    assert "directory-link/payload.bin" not in paths
    assert _entry(manifest, "directory-link")["entry_type"] == "symlink"
    assert "sha256" not in _entry(manifest, "directory-link")


@pytest.mark.parametrize("absolute", [False, True])
def test_manifest_rejects_root_escaping_or_absolute_symlink(
    tmp_path: Path,
    absolute: bool,
) -> None:
    artifact = tmp_path / "candidate"
    artifact.mkdir()
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside-secret")
    target = str(outside) if absolute else "../outside.bin"
    _symlink_or_skip(artifact / "escape", target)

    with pytest.raises(ArtifactManifestError, match="stay within the artifact root"):
        create_artifact_manifest(artifact)

    assert outside.read_bytes() == b"outside-secret"


def test_manifest_rejects_special_files(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable on this platform")
    artifact = tmp_path / "candidate"
    artifact.mkdir()
    fifo = artifact / "named-pipe"
    try:
        os.mkfifo(fifo)
    except OSError as exc:
        pytest.skip(f"FIFO creation is unavailable on this filesystem: {exc}")

    with pytest.raises(ArtifactManifestError, match="unsupported entry type"):
        create_artifact_manifest(artifact)


def test_manifest_rejects_windows_casefold_collision(tmp_path: Path) -> None:
    artifact = tmp_path / "candidate"
    artifact.mkdir()
    upper = artifact / "README"
    lower = artifact / "readme"
    upper.write_bytes(b"upper")
    lower.write_bytes(b"lower")
    if upper.read_bytes() == lower.read_bytes():
        pytest.skip("filesystem is case-insensitive")

    with pytest.raises(ArtifactManifestError, match="Windows case collision"):
        create_artifact_manifest(artifact)


def test_same_size_same_mode_restored_mtime_rewrite_changes_candidate_id(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "candidate"
    artifact.mkdir()
    payload = artifact / "payload.bin"
    payload.write_bytes(b"baseline")
    original = payload.stat()
    before = create_artifact_manifest(artifact)

    payload.write_bytes(b"tampered")
    payload.chmod(original.st_mode)
    current = payload.stat()
    os.utime(payload, ns=(current.st_atime_ns, original.st_mtime_ns))
    after = create_artifact_manifest(artifact)

    assert payload.stat().st_size == original.st_size
    assert payload.stat().st_mtime_ns == original.st_mtime_ns
    assert after.candidate_id != before.candidate_id


def test_manifest_rejects_entry_replaced_while_it_is_hashed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "candidate"
    artifact.mkdir()
    payload = artifact / "payload.bin"
    payload.write_bytes(b"baseline")
    original_sha256_file = sha256_file

    def hash_then_replace(path: Path) -> str:
        digest = original_sha256_file(path)
        replacement = Path(path).with_name("replacement.bin")
        replacement.write_bytes(b"tampered")
        os.replace(replacement, path)
        return digest

    monkeypatch.setattr(
        "packaging_tools.artifact_manifest.sha256_file",
        hash_then_replace,
    )

    with pytest.raises(ArtifactManifestError, match="changed while manifesting"):
        create_artifact_manifest(artifact)


def test_manifest_writer_is_canonical_exclusive_and_outside_candidate(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "candidate"
    artifact.mkdir()
    (artifact / "payload.bin").write_bytes(b"payload")
    manifest = create_artifact_manifest(artifact)
    output = tmp_path / "artifact-manifest.json"

    candidate_id = write_artifact_manifest(output, manifest)

    assert candidate_id == manifest.candidate_id
    assert sha256_file(output) == candidate_id
    assert output.read_bytes() == manifest.canonical_bytes
    with pytest.raises(ArtifactManifestError, match="already exists"):
        write_artifact_manifest(output, manifest)
    with pytest.raises(ArtifactManifestError, match="outside the artifact root"):
        write_artifact_manifest(artifact / "manifest.json", manifest)


def test_manifest_rejects_file_or_symlink_root(tmp_path: Path) -> None:
    file_root = tmp_path / "file.bin"
    file_root.write_bytes(b"not-a-directory")
    with pytest.raises(ArtifactManifestError, match="regular directory"):
        create_artifact_manifest(file_root)

    directory = tmp_path / "directory"
    directory.mkdir()
    symlink_root = tmp_path / "directory-link"
    _symlink_or_skip(symlink_root, directory.name)
    with pytest.raises(ArtifactManifestError, match="regular directory"):
        create_artifact_manifest(symlink_root)
