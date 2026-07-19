from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

import build as build_script
from packaging_tools.artifact_manifest import create_artifact_manifest
from packaging_tools.contracts import (
    BuildReceipt,
    HashedFile,
    ReleaseContractError,
    ReleaseInputBundle,
    RuntimeIdentity,
    ValidatedReleaseInputs,
    canonical_json_bytes,
    sha256_file,
)


REQUIRED_TOCS = (
    "Analysis-00.toc",
    "PYZ-00.toc",
    "PKG-00.toc",
    "EXE-00.toc",
    "COLLECT-00.toc",
)


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _reference(label: str, path: Path, relative_path: str | None = None) -> HashedFile:
    return HashedFile(
        label=label,
        relative_path=relative_path or path.name,
        path=path,
        sha256=sha256_file(path),
    )


def _source_archive(
    root: Path,
    policy: dict[str, object],
    *,
    notice_bytes: bytes = b"Exact fixture notice\n",
) -> Path:
    source = root / "source-tree"
    for relative in (
        "build.py",
        "packaging_tools/contracts.py",
        "packaging_tools/artifact_manifest.py",
    ):
        _write(
            source / relative,
            (build_script.SCRIPT_DIR / relative).read_bytes(),
        )
    _write(source / "easyqc.spec", b"# exact fixture spec\n")
    _write(source / "easyqc.py", b"print('fixture')\n")
    _write(source / "packaging/licenses/fixture.txt", notice_bytes)
    _write(
        source / "packaging/component_policy.json",
        canonical_json_bytes(policy),
    )
    archive_path = root / "inputs/source/easyqc.tar"
    archive_path.parent.mkdir(parents=True)
    with tarfile.open(archive_path, "w") as archive:
        for item in sorted(source.rglob("*")):
            archive.add(item, arcname=item.relative_to(source).as_posix(), recursive=False)
    return archive_path


def _validated_inputs(
    root: Path,
    *,
    notice_bytes: bytes = b"Exact fixture notice\n",
    expected_notice_sha256: str | None = None,
) -> ValidatedReleaseInputs:
    notice_source = root / "source-tree/packaging/licenses/fixture.txt"
    expected_digest = expected_notice_sha256
    if expected_digest is None:
        _write(notice_source, notice_bytes)
        expected_digest = sha256_file(notice_source)
    policy: dict[str, object] = {
        "schema": "easyqc-component-policy-v1",
        "version": 1,
        "targets": ["linux-x86_64"],
        "rules": [
            {
                "component_id": "application:easyqc-fixture@1.0.0",
                "notice_sources": [
                    {
                        "source_path": "packaging/licenses/fixture.txt",
                        "destination_path": "THIRD_PARTY_LICENSES/fixture.txt",
                        "sha256": expected_digest,
                    }
                ],
            }
        ],
        "non_component_rules": [],
    }
    source_archive = _source_archive(root, policy, notice_bytes=notice_bytes)

    input_root = root / "inputs"
    policy_path = _write(
        input_root / "component-policy.json",
        canonical_json_bytes(policy),
    )
    release_input_path = _write(input_root / "release-input.json", b"fixture\n")
    runtime_identity_path = _write(
        input_root / "runtime-identity.json",
        canonical_json_bytes({"schema": "fixture-runtime"}),
    )
    runtime_executable = Path(sys.executable).resolve()
    runtime_artifact = _reference(
        "runtime_identity.artifact",
        runtime_executable,
        "runtime/python",
    )
    runtime_identity_file = _reference(
        "runtime_identity",
        runtime_identity_path,
        "runtime-identity.json",
    )
    runtime = RuntimeIdentity(
        implementation="CPython",
        version="3.10.17",
        target="linux-x86_64",
        target_triple="x86_64-manylinux_2_34",
        provider="fixture exact executable",
        source_provenance="fixture://current-executable",
        artifact=runtime_artifact,
        identity_file=runtime_identity_file,
    )

    lock_files: dict[str, Path] = {}
    lock_references: dict[str, HashedFile] = {}
    for scope in ("runtime", "build", "test"):
        lock = _write(input_root / f"locks/{scope}.txt", f"{scope}\n".encode())
        lock_files[scope] = lock
        lock_references[scope] = _reference(
            f"locks.{scope}", lock, f"locks/{scope}.txt"
        )

    cursor = _write(input_root / "target/cursor.deb", b"fixture cursor deb")
    bundle = ReleaseInputBundle(
        root=input_root,
        document_path=release_input_path,
        document_sha256=sha256_file(release_input_path),
        target="linux-x86_64",
        version="1.0.0",
        source_revision="a" * 40,
        source_archive=_reference(
            "source.archive", source_archive, "source/easyqc.tar"
        ),
        runtime_identity_file=runtime_identity_file,
        lock_files=lock_references,
        component_policy=_reference(
            "component_policy", policy_path, "component-policy.json"
        ),
        target_extension={
            "linux_cursor_deb": _reference(
                "target_extension.linux_cursor_deb",
                cursor,
                "target/cursor.deb",
            )
        },
    )
    return ValidatedReleaseInputs(
        bundle=bundle,
        runtime_identity=runtime,
        source_archive=source_archive,
        lock_files=lock_files,
        component_policy=policy_path,
        linux_cursor_deb=cursor,
    )


def _install_mock_build(
    monkeypatch: pytest.MonkeyPatch,
    *,
    omit_toc: str | None = None,
    build_error: Exception | None = None,
) -> list[str]:
    events: list[str] = []

    cursor_library = Path("/fixture/libxcb-cursor.so.0")
    cursor_notice = Path("/fixture/copyright")
    monkeypatch.setattr(
        build_script,
        "verify_linux_cursor_deb",
        lambda _path, *, build_root=None: build_script.VerifiedLinuxCursorRuntime(
            cursor_library,
            cursor_notice,
        ),
    )
    monkeypatch.setattr(
        build_script,
        "_release_tool_versions",
        lambda _runtime: {"python": "3.10.17", "pyinstaller": "6.21.0"},
    )
    monkeypatch.setattr(
        build_script,
        "_capture_distribution_metadata",
        lambda: {
            "schema": "easyqc-release-distribution-metadata-v1",
            "distribution_count": 1,
            "distributions": [
                {
                    "canonical_name": "fixture",
                    "name": "fixture",
                    "version": "1.0.0",
                }
            ],
        },
    )

    def fake_pyinstaller(
        cursor_runtime=None,
        *,
        python_executable=None,
        source_root=None,
        work_path=None,
        dist_path=None,
    ) -> None:
        events.append("pyinstaller")
        assert cursor_runtime is not None
        assert Path(python_executable).resolve() == Path(sys.executable).resolve()
        assert (Path(source_root) / "easyqc.spec").is_file()
        if build_error is not None:
            raise build_error
        work = Path(work_path) / "easyqc"
        work.mkdir(parents=True)
        for name in REQUIRED_TOCS:
            if name != omit_toc:
                _write(work / name, f"{name}\n".encode())
        _write(work / "warn-easyqc.txt", b"fixture warning\n")
        artifact = Path(dist_path) / "EasyQC"
        _write(artifact / "EasyQC", b"fixture executable\n").chmod(0o755)

    monkeypatch.setattr(build_script, "run_pyinstaller", fake_pyinstaller)
    monkeypatch.setattr(
        build_script,
        "smoke_test",
        lambda *_args, **_kwargs: pytest.fail("release build must not run smoke"),
    )
    monkeypatch.setattr(
        build_script,
        "verify_linux_bundle",
        lambda *_args, **_kwargs: pytest.fail(
            "release build must not make native dependency claims"
        ),
    )
    original_manifest = create_artifact_manifest

    def ordered_manifest(artifact: Path):
        events.append("manifest")
        assert (artifact / "THIRD_PARTY_LICENSES/fixture.txt").is_file()
        assert (
            artifact / "THIRD_PARTY_LICENSES/libxcb-cursor0-PROVENANCE.txt"
        ).is_file()
        return original_manifest(artifact)

    monkeypatch.setattr(build_script, "create_artifact_manifest", ordered_manifest)
    return events


def test_build_candidate_finalizes_retains_and_publishes_receipt_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated = _validated_inputs(tmp_path)
    events = _install_mock_build(monkeypatch)
    packet_root = tmp_path / "packet"

    receipt = build_script.build_candidate(validated, packet_root)

    artifact = packet_root / receipt.artifact_path
    assert receipt.status == "PASS"
    assert receipt.evidence_class == "release"
    assert receipt.candidate_id == create_artifact_manifest(artifact).candidate_id
    assert (artifact / "THIRD_PARTY_LICENSES/fixture.txt").read_bytes() == (
        b"Exact fixture notice\n"
    )
    assert events == ["pyinstaller", "manifest"]

    build_dir = packet_root / "build"
    assert not (build_dir / "source").exists()
    assert not (build_dir / "pyinstaller-work").exists()
    assert not (build_dir / "pyinstaller-dist").exists()
    for name in REQUIRED_TOCS:
        assert (build_dir / "evidence/pyinstaller" / name).read_bytes() == (
            f"{name}\n".encode()
        )

    evidence_index = json.loads(
        (build_dir / "pyinstaller-evidence-index.json").read_text(encoding="utf-8")
    )
    assert [item["kind"] for item in evidence_index["tocs"]] == [
        "Analysis",
        "PYZ",
        "PKG",
        "EXE",
        "COLLECT",
    ]
    assert evidence_index["warning"]["disposition"] == "RETAINED_REVIEW_REQUIRED"
    receipt_path = build_dir / "build-receipt.json"
    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_payload == receipt.as_json_object()
    assert receipt_payload["status"] == "PASS"
    assert set(receipt_payload["evidence"]) == {
        "toc_index",
        "distribution_metadata",
    }


@pytest.mark.parametrize("failure", ["pyinstaller", "missing-toc", "notice-hash"])
def test_build_candidate_failure_never_emits_a_pass_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    wrong_notice_hash = "f" * 64 if failure == "notice-hash" else None
    validated = _validated_inputs(
        tmp_path,
        expected_notice_sha256=wrong_notice_hash,
    )
    _install_mock_build(
        monkeypatch,
        omit_toc=("COLLECT-00.toc" if failure == "missing-toc" else None),
        build_error=(
            ReleaseContractError("fixture PyInstaller failure")
            if failure == "pyinstaller"
            else None
        ),
    )
    packet_root = tmp_path / "packet"

    with pytest.raises(ReleaseContractError):
        build_script.build_candidate(validated, packet_root)

    assert not (packet_root / "build/build-receipt.json").exists()


def test_build_candidate_refuses_an_existing_packet_before_pyinstaller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validated = _validated_inputs(tmp_path)
    packet_root = tmp_path / "packet"
    packet_root.mkdir()
    monkeypatch.setattr(
        build_script,
        "run_pyinstaller",
        lambda *_args, **_kwargs: pytest.fail("existing packet must fail first"),
    )

    with pytest.raises(ReleaseContractError, match="packet root"):
        build_script.build_candidate(validated, packet_root)

    assert not (packet_root / "build/build-receipt.json").exists()


def test_release_cli_rejects_legacy_smoke_bypass_before_loading_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        build_script,
        "load_release_input",
        lambda *_args, **_kwargs: pytest.fail("invalid CLI mix must fail first"),
        raising=False,
    )

    with pytest.raises(SystemExit):
        build_script.main(
            [
                "--release-input",
                str(tmp_path / "release-input.json"),
                "--release-input-sha256",
                "a" * 64,
                "--packet-root",
                str(tmp_path / "packet"),
                "--skip-smoke",
            ]
        )

    assert not (tmp_path / "packet").exists()


def test_run_pyinstaller_uses_explicit_paths_without_a_resolver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _write(source / "easyqc.spec", b"# fixture spec\n")
    observed: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(build_script, "SYSTEM", "Windows")
    monkeypatch.setenv("PYTHONPATH", "/untrusted/pythonpath")
    monkeypatch.setenv("PYTHONHOME", "/untrusted/pythonhome")
    monkeypatch.setenv("PYTHONSTARTUP", "/untrusted/startup.py")

    def fake_run(command, **kwargs):
        safe_kwargs = {key: value for key, value in kwargs.items() if key != "env"}
        environment = kwargs["env"]
        safe_kwargs["env"] = {
            name: environment.get(name)
            for name in (
                "PYTHONPATH",
                "PYTHONHOME",
                "PYTHONSTARTUP",
                "PYTHONNOUSERSITE",
            )
        }
        observed.append((command, safe_kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(build_script.subprocess, "run", fake_run)
    build_script.run_pyinstaller(
        None,
        python_executable=Path("/exact/python"),
        source_root=source,
        work_path=tmp_path / "work",
        dist_path=tmp_path / "dist",
    )

    command, kwargs = observed[0]
    assert command[:3] == ["/exact/python", "-m", "PyInstaller"]
    assert command.count(str(source / "easyqc.spec")) == 1
    assert command[command.index("--workpath") + 1] == str(tmp_path / "work")
    assert command[command.index("--distpath") + 1] == str(tmp_path / "dist")
    assert not {"pip", "uv", "install"}.intersection(command)
    assert kwargs["cwd"] == str(source)
    assert kwargs["shell"] is False
    assert kwargs["env"].get("PYTHONPATH") is None
    assert kwargs["env"].get("PYTHONHOME") is None
    assert kwargs["env"].get("PYTHONSTARTUP") is None
    assert kwargs["env"]["PYTHONNOUSERSITE"] == "1"


def test_notice_finalization_never_follows_candidate_parent_symlinks(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    notice = _write(source / "licenses/fixture.txt", b"fixture notice\n")
    policy = {
        "schema": "easyqc-component-policy-v1",
        "version": 1,
        "targets": ["windows-x86_64"],
        "rules": [
            {
                "component_id": "fixture",
                "notice_sources": [
                    {
                        "source_path": "licenses/fixture.txt",
                        "destination_path": (
                            "THIRD_PARTY_LICENSES/nested/fixture.txt"
                        ),
                        "sha256": sha256_file(notice),
                    }
                ],
            }
        ],
        "non_component_rules": [],
    }
    archived_policy = _write(
        source / "packaging/component_policy.json",
        canonical_json_bytes(policy),
    )
    policy_input = _write(
        tmp_path / "component-policy.json",
        archived_policy.read_bytes(),
    )
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (artifact / "THIRD_PARTY_LICENSES").symlink_to(
        outside,
        target_is_directory=True,
    )

    with pytest.raises(ReleaseContractError, match="symlink"):
        build_script.finalize_candidate_notices(
            artifact,
            policy_input,
            source,
            "windows-x86_64",
            None,
        )

    assert list(outside.iterdir()) == []


def test_source_archive_extraction_rejects_link_entries(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "source.tar"
    with tarfile.open(archive_path, "w") as archive:
        link = tarfile.TarInfo("easyqc.spec")
        link.type = tarfile.SYMTYPE
        link.linkname = "outside.spec"
        archive.addfile(link)

    with pytest.raises(ReleaseContractError, match="unsupported source archive"):
        build_script._extract_source_archive(archive_path, tmp_path / "source")


def test_release_orchestrator_must_match_the_authenticated_source_archive(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _write(source / "build.py", b"different build orchestrator\n")
    for relative in (
        "packaging_tools/contracts.py",
        "packaging_tools/artifact_manifest.py",
    ):
        _write(
            source / relative,
            (build_script.SCRIPT_DIR / relative).read_bytes(),
        )

    with pytest.raises(ReleaseContractError, match="orchestrator differs"):
        build_script._verify_release_orchestrator_source(source)


def test_distribution_metadata_capture_is_canonical_and_deterministic() -> None:
    first = build_script._capture_distribution_metadata()
    second = build_script._capture_distribution_metadata()

    assert first["schema"] == "easyqc-release-distribution-metadata-v1"
    assert first["distribution_count"] == len(first["distributions"])
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert str(Path(sys.prefix)) not in canonical_json_bytes(first).decode("utf-8")


def test_build_receipt_rejects_non_pass_or_uncontained_references() -> None:
    values = {
        "status": "PASS",
        "evidence_class": "release",
        "target": "linux-x86_64",
        "candidate_id": "a" * 64,
        "artifact_path": "artifact/EasyQC",
        "artifact_manifest_path": "build/artifact-manifest.json",
        "artifact_manifest_sha256": "a" * 64,
        "component_policy_path": "build/component-policy.json",
        "component_policy_sha256": "b" * 64,
        "toc_index_path": "build/pyinstaller-evidence-index.json",
        "toc_index_sha256": "c" * 64,
        "distribution_metadata_path": "build/distribution-metadata.json",
        "distribution_metadata_sha256": "d" * 64,
        "source_revision": "e" * 40,
        "source_archive_sha256": "f" * 64,
        "runtime_identity_sha256": "1" * 64,
        "locks": {"runtime": "2" * 64, "build": "3" * 64, "test": "4" * 64},
        "tools": {"python": "3.10.17", "pyinstaller": "6.21.0"},
    }

    with pytest.raises(ReleaseContractError, match="status"):
        BuildReceipt(**{**values, "status": "FAIL"})
    with pytest.raises(ReleaseContractError, match="canonical contained"):
        BuildReceipt(**{**values, "artifact_path": "../outside"})
