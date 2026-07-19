from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from packaging_tools.contracts import (
    APPROVED_TARGET_LOCK_SHA256,
    RELEASE_INPUT_SCHEMA,
    TARGET_TRIPLES,
    ReleaseContractError,
    canonical_json_bytes,
    load_release_input,
    sha256_file,
    validate_release_inputs,
    write_canonical_json,
)


def _write_file(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _file_reference(path: Path, root: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
    }


def _build_release_input(
    tmp_path: Path,
    easyqc_root: Path,
    *,
    target: str = "linux-x86_64",
    runtime_overrides: dict[str, object] | None = None,
    release_overrides: dict[str, object] | None = None,
) -> tuple[Path, str, dict[str, object], dict[str, object]]:
    root = tmp_path / "inputs"
    root.mkdir(parents=True)

    source_archive = root / "source/easyqc.tar"
    _write_file(source_archive, b"clean-source-archive")

    runtime_artifact = root / "runtime/python-runtime.bin"
    _write_file(runtime_artifact, b"exact-target-runtime")
    runtime_payload: dict[str, object] = {
        "implementation": "CPython",
        "version": "3.10.17",
        "target": target,
        "target_triple": TARGET_TRIPLES[target],
        "provider": "fixture-provider",
        "source_provenance": "fixture://approved-runtime",
        "artifact": _file_reference(runtime_artifact, root),
    }
    if runtime_overrides:
        runtime_payload.update(runtime_overrides)
    runtime_identity = root / "runtime-identity.json"
    runtime_identity.write_bytes(canonical_json_bytes(runtime_payload))

    lock_references: dict[str, dict[str, str]] = {}
    source_lock_root = (
        easyqc_root / "packaging/locks/python-3.10.17" / target
    )
    for scope in ("runtime", "build", "test"):
        source_lock = source_lock_root / f"{scope}.txt"
        destination = root / "locks" / f"{scope}.txt"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_lock, destination)
        lock_references[scope] = _file_reference(destination, root)

    component_policy = root / "component-policy.json"
    component_policy.write_bytes(
        canonical_json_bytes(
            {"schema": "easyqc-component-policy-v1", "rules": []}
        )
    )

    target_extension: dict[str, object] = {}
    if target == "linux-x86_64":
        cursor_deb = root / "target/libxcb-cursor0.deb"
        _write_file(cursor_deb, b"fixture-cursor-deb")
        target_extension = {
            "linux_cursor_deb": _file_reference(cursor_deb, root)
        }

    release_payload: dict[str, object] = {
        "schema": RELEASE_INPUT_SCHEMA,
        "target": target,
        "version": "1.0.0",
        "source": {
            "revision": "a" * 40,
            "archive": _file_reference(source_archive, root),
        },
        "runtime_identity": _file_reference(runtime_identity, root),
        "locks": lock_references,
        "component_policy": _file_reference(component_policy, root),
        "target_extension": target_extension,
    }
    if release_overrides:
        release_payload.update(release_overrides)

    release_input = root / "release-input.json"
    release_input.write_bytes(canonical_json_bytes(release_payload))
    return (
        release_input,
        sha256_file(release_input),
        release_payload,
        runtime_payload,
    )


def test_approved_python310_locks_are_persisted_with_exact_hashes(
    easyqc_root: Path,
) -> None:
    lock_root = easyqc_root / "packaging/locks/python-3.10.17"

    assert set(APPROVED_TARGET_LOCK_SHA256) == {
        "linux-x86_64",
        "windows-x86_64",
        "macos-arm64",
    }
    for target, scopes in APPROVED_TARGET_LOCK_SHA256.items():
        assert set(scopes) == {"runtime", "build", "test"}
        for scope, expected_sha256 in scopes.items():
            lock_path = lock_root / target / f"{scope}.txt"
            text = lock_path.read_text(encoding="utf-8")

            assert sha256_file(lock_path) == expected_sha256
            assert text.startswith(
                "--index-url https://pypi.org/simple\n--only-binary :all:\n"
            )
            assert "--hash=sha256:" in text


def test_canonical_json_bytes_are_stable_unicode_safe_and_strict() -> None:
    first = canonical_json_bytes({"z": 2, "message": "质控", "a": 1})
    second = canonical_json_bytes({"a": 1, "message": "质控", "z": 2})

    assert first == second
    assert first == '{"a":1,"message":"质控","z":2}\n'.encode()
    with pytest.raises(ReleaseContractError, match="finite JSON"):
        canonical_json_bytes({"invalid": float("nan")})


def test_write_canonical_json_creates_once_and_refuses_overwrite_or_symlink(
    tmp_path: Path,
) -> None:
    output = tmp_path / "receipt.json"

    digest = write_canonical_json(output, {"schema": "fixture", "value": 1})

    assert digest == sha256_file(output)
    assert output.read_bytes() == b'{"schema":"fixture","value":1}\n'
    with pytest.raises(ReleaseContractError, match="already exists"):
        write_canonical_json(output, {"schema": "fixture", "value": 2})

    symlink_destination = tmp_path / "symlink.json"
    symlink_destination.symlink_to(output.name)
    with pytest.raises(ReleaseContractError, match="already exists"):
        write_canonical_json(
            symlink_destination,
            {"schema": "fixture", "value": 3},
        )


def test_write_canonical_json_does_not_overwrite_a_racing_creator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "receipt.json"
    original_link = os.link

    def create_destination_before_link(source: Path, destination: Path) -> None:
        Path(destination).write_bytes(b"created-by-another-process")
        original_link(source, destination)

    monkeypatch.setattr(
        "packaging_tools.contracts.os.link",
        create_destination_before_link,
    )

    with pytest.raises(ReleaseContractError, match="already exists"):
        write_canonical_json(output, {"schema": "fixture", "value": 1})

    assert output.read_bytes() == b"created-by-another-process"


def test_canonical_self_contained_linux_release_input_loads_and_validates(
    tmp_path: Path,
    easyqc_root: Path,
) -> None:
    release_input, digest, _payload, _runtime = _build_release_input(
        tmp_path,
        easyqc_root,
    )

    bundle = load_release_input(release_input, digest)
    validated = validate_release_inputs(bundle)

    assert bundle.target == "linux-x86_64"
    assert bundle.source_revision == "a" * 40
    assert validated.runtime_identity.version == "3.10.17"
    assert validated.runtime_identity.target_triple == TARGET_TRIPLES[bundle.target]
    assert set(validated.lock_files) == {"runtime", "build", "test"}
    assert validated.linux_cursor_deb is not None


def test_release_input_rejects_wrong_document_hash(
    tmp_path: Path,
    easyqc_root: Path,
) -> None:
    release_input, _digest, _payload, _runtime = _build_release_input(
        tmp_path,
        easyqc_root,
    )

    with pytest.raises(ReleaseContractError, match="release-input SHA-256"):
        load_release_input(release_input, "0" * 64)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"schema": "easyqc-release-input-v2"}, "schema"),
        ({"unexpected": "value"}, "fields"),
    ],
)
def test_release_input_rejects_unknown_schema_and_extra_fields(
    tmp_path: Path,
    easyqc_root: Path,
    mutation: dict[str, object],
    message: str,
) -> None:
    release_input, _digest, payload, _runtime = _build_release_input(
        tmp_path,
        easyqc_root,
    )
    payload.update(mutation)
    release_input.write_bytes(canonical_json_bytes(payload))

    with pytest.raises(ReleaseContractError, match=message):
        load_release_input(release_input, sha256_file(release_input))


def test_release_input_rejects_noncanonical_json(
    tmp_path: Path,
    easyqc_root: Path,
) -> None:
    release_input, _digest, payload, _runtime = _build_release_input(
        tmp_path,
        easyqc_root,
    )
    release_input.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(ReleaseContractError, match="canonical JSON"):
        load_release_input(release_input, sha256_file(release_input))


def test_release_input_rejects_relative_path_escape(
    tmp_path: Path,
    easyqc_root: Path,
) -> None:
    release_input, _digest, payload, _runtime = _build_release_input(
        tmp_path,
        easyqc_root,
    )
    escaped = deepcopy(payload)
    escaped["source"]["archive"]["path"] = "../outside.tar"  # type: ignore[index]
    release_input.write_bytes(canonical_json_bytes(escaped))

    with pytest.raises(ReleaseContractError, match="source.archive.path"):
        load_release_input(release_input, sha256_file(release_input))


def test_release_input_rejects_symlinked_referenced_file(
    tmp_path: Path,
    easyqc_root: Path,
) -> None:
    release_input, digest, payload, _runtime = _build_release_input(
        tmp_path,
        easyqc_root,
    )
    root = release_input.parent
    policy_path = root / payload["component_policy"]["path"]  # type: ignore[index]
    policy_path.unlink()
    outside = tmp_path / "outside-policy.json"
    outside.write_bytes(b"{}\n")
    policy_path.symlink_to(outside)

    bundle = load_release_input(release_input, digest)
    with pytest.raises(ReleaseContractError, match="component_policy.path"):
        validate_release_inputs(bundle)


@pytest.mark.parametrize(
    ("runtime_overrides", "message"),
    [
        ({"version": "3.13.13"}, "3.10.17"),
        ({"implementation": "PyPy"}, "CPython"),
        ({"target_triple": "wrong-target"}, "target_triple"),
    ],
)
def test_runtime_identity_must_match_approved_python_and_target(
    tmp_path: Path,
    easyqc_root: Path,
    runtime_overrides: dict[str, object],
    message: str,
) -> None:
    release_input, digest, _payload, _runtime = _build_release_input(
        tmp_path,
        easyqc_root,
        runtime_overrides=runtime_overrides,
    )

    bundle = load_release_input(release_input, digest)
    with pytest.raises(ReleaseContractError, match=message):
        validate_release_inputs(bundle)


def test_linux_requires_cursor_extension_and_foreign_targets_require_empty_extension(
    tmp_path: Path,
    easyqc_root: Path,
) -> None:
    linux_input, _digest, linux_payload, _runtime = _build_release_input(
        tmp_path / "linux",
        easyqc_root,
    )
    linux_payload["target_extension"] = {}
    linux_input.write_bytes(canonical_json_bytes(linux_payload))
    with pytest.raises(ReleaseContractError, match="linux_cursor_deb"):
        load_release_input(linux_input, sha256_file(linux_input))

    for target in ("windows-x86_64", "macos-arm64"):
        release_input, digest, payload, _runtime = _build_release_input(
            tmp_path / target,
            easyqc_root,
            target=target,
        )
        validated = validate_release_inputs(load_release_input(release_input, digest))

        assert validated.linux_cursor_deb is None
        payload["target_extension"] = {
            "linux_cursor_deb": {
                "path": "target/libxcb-cursor0.deb",
                "sha256": "0" * 64,
            }
        }
        release_input.write_bytes(canonical_json_bytes(payload))
        with pytest.raises(ReleaseContractError, match="target_extension fields"):
            load_release_input(release_input, sha256_file(release_input))


def test_validation_rejects_tampering_after_release_input_load(
    tmp_path: Path,
    easyqc_root: Path,
) -> None:
    release_input, digest, payload, _runtime = _build_release_input(
        tmp_path,
        easyqc_root,
    )
    bundle = load_release_input(release_input, digest)
    source_archive = (
        release_input.parent / payload["source"]["archive"]["path"]  # type: ignore[index]
    )
    source_archive.write_bytes(b"tampered-source-archive")

    with pytest.raises(ReleaseContractError, match="source.archive SHA-256"):
        validate_release_inputs(bundle)
