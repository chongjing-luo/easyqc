from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

import build as build_script
from packaging_tools import component_inventory as component_inventory_module
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
RELEASE_ORCHESTRATOR_FILES = (
    "build.py",
    "packaging_tools/contracts.py",
    "packaging_tools/artifact_manifest.py",
    "packaging_tools/component_evidence.py",
    "packaging_tools/component_evidence_io.py",
    "packaging_tools/component_inventory.py",
    "packaging_tools/conda_component_evidence.py",
    "packaging_tools/debian_component_evidence.py",
)


def test_tracked_component_policy_is_canonical_metadata_capture_seed() -> None:
    policy_path = build_script.SCRIPT_DIR / "packaging/component_policy.json"
    policy_bytes = policy_path.read_bytes()
    policy = json.loads(policy_bytes)

    assert policy_bytes == canonical_json_bytes(policy)
    assert policy["schema"] == "easyqc-component-policy-v1"
    assert policy["version"] == 1
    assert policy["targets"] == ["linux-x86_64"]
    assert policy["non_component_rules"] == []
    assert [rule["component_id"] for rule in policy["rules"]] == [
        "application:easyqc@1.0.0",
        "library:libxcb-cursor0@0.1.1-4ubuntu1",
        "library:platformdirs@4.10.1",
    ]
    for rule in policy["rules"]:
        assert rule["file_paths"] == []
        assert rule["toc_sources"] == []
        assert rule["origin_evidence"] == []
        assert len(rule["notice_sources"]) == 1
        notice = rule["notice_sources"][0]
        assert rule["notice_paths"] == [notice["destination_path"]]
        assert rule["notice_sha256"] == {
            notice["destination_path"]: notice["sha256"]
        }
        assert sha256_file(build_script.SCRIPT_DIR / notice["source_path"]) == (
            notice["sha256"]
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
    for relative in RELEASE_ORCHESTRATOR_FILES:
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
                "type": "application",
                "name": "EasyQC fixture",
                "version": "1.0.0",
                "purl": "pkg:generic/easyqc-fixture@1.0.0",
                "provider": "synthetic exact policy",
                "file_paths": [],
                "toc_sources": [],
                "origin_evidence": [],
                "dependencies": [],
                "license_declared": "MIT",
                "license_concluded": "MIT",
                "notice_paths": ["THIRD_PARTY_LICENSES/fixture.txt"],
                "notice_sha256": {
                    "THIRD_PARTY_LICENSES/fixture.txt": expected_digest,
                },
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
    def fake_component_evidence_v3(
        artifact,
        manifest,
        evidence_index_path,
        build_dir,
        runtime_identity,
        source_root,
        cursor_runtime,
        release_version,
        source_revision,
    ):
        events.append("component-evidence-v3")
        assert Path(artifact).is_dir()
        assert manifest.candidate_id == create_artifact_manifest(artifact).candidate_id
        assert Path(evidence_index_path).is_file()
        assert Path(build_dir).name == "build"
        assert runtime_identity.version == "3.10.17"
        assert (Path(source_root) / "easyqc.spec").is_file()
        assert cursor_runtime is not None
        assert release_version == "1.0.0"
        assert source_revision == "a" * 40
        return {
            "schema": "easyqc-release-distribution-metadata-v3",
            "collect_entry_count": 1,
            "component_count": 0,
            "assigned_collected_entry_count": 0,
            "unassigned_collected_entry_count": 1,
            "components": [],
            "unassigned_collected_entries": [
                {
                    "entry_type": "regular-file",
                    "final_path": "EasyQC",
                    "raw_source_text_sha256": "a" * 64,
                    "source_identity": {"entry_type": "unavailable"},
                    "source_locator": "unassigned-source/" + "a" * 64,
                    "toc_type": "EXECUTABLE",
                }
            ],
        }

    monkeypatch.setattr(
        build_script,
        "capture_component_evidence_v3",
        fake_component_evidence_v3,
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
    retained_policy = json.loads(
        (packet_root / receipt.component_policy_path).read_text(encoding="utf-8")
    )
    assert retained_policy["rules"][0]["notice_paths"] == [
        "THIRD_PARTY_LICENSES/fixture.txt"
    ]
    assert events == ["pyinstaller", "manifest", "component-evidence-v3"]

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
    distribution_metadata = json.loads(
        (build_dir / "distribution-metadata.json").read_text(encoding="utf-8")
    )
    assert distribution_metadata["schema"] == (
        "easyqc-release-distribution-metadata-v3"
    )
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


@pytest.mark.parametrize(
    "changed_relative",
    [
        "build.py",
        "packaging_tools/component_evidence.py",
        "packaging_tools/conda_component_evidence.py",
        "packaging_tools/debian_component_evidence.py",
    ],
)
def test_release_orchestrator_must_match_the_authenticated_source_archive(
    tmp_path: Path,
    changed_relative: str,
) -> None:
    source = tmp_path / "source"
    for relative in RELEASE_ORCHESTRATOR_FILES:
        _write(
            source / relative,
            (build_script.SCRIPT_DIR / relative).read_bytes(),
        )
    _write(source / changed_relative, b"different build orchestrator\n")

    with pytest.raises(ReleaseContractError, match="orchestrator differs"):
        build_script._verify_release_orchestrator_source(source)


def test_distribution_metadata_capture_is_canonical_and_deterministic() -> None:
    first = build_script._capture_distribution_metadata()
    second = build_script._capture_distribution_metadata()

    assert first["schema"] == "easyqc-release-distribution-metadata-v1"
    assert first["distribution_count"] == len(first["distributions"])
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert str(Path(sys.prefix)) not in canonical_json_bytes(first).decode("utf-8")


def _path_distribution(
    site: Path,
    *,
    name: str,
    version: str,
    owned_module: str,
) -> object:
    dist_info = site / f"{name}-{version}.dist-info"
    metadata = (
        "Metadata-Version: 2.4\n"
        f"Name: {name}\n"
        f"Version: {version}\n"
        "Author: Demo Provider\n"
        "Author-email: qc@example.invalid\n"
        "License-Expression: MIT\n"
        "License-File: LICENSE\n"
        "Classifier: License :: OSI Approved :: MIT License\n"
        "Requires-Python: >=3.10\n"
        "\n"
    ).encode()
    _write(dist_info / "METADATA", metadata)
    _write(dist_info / "LICENSE", b"Demo retained MIT license\n")
    _write(
        dist_info / "RECORD",
        (
            f"{owned_module},,\n"
            f"{dist_info.name}/LICENSE,,\n"
            f"{dist_info.name}/METADATA,,\n"
            f"{dist_info.name}/RECORD,,\n"
        ).encode(),
    )
    return build_script.importlib_metadata.PathDistribution(dist_info)


def _python_capture_packet(
    packet_root: Path,
    *,
    owned_source: Path,
    unowned_source: Path,
    owned_toc_type: str = "DATA",
    warning_bytes: bytes = b"fixture warning\n",
) -> tuple[Path, object, Path, Path]:
    artifact = packet_root / "artifact/EasyQC-fixture"
    _write(artifact / "_internal/demo.py", owned_source.read_bytes())
    _write(artifact / "EasyQC", b"fixture executable\n").chmod(0o755)
    manifest = create_artifact_manifest(artifact)

    build_dir = packet_root / "build"
    work = build_dir / "pyinstaller-work/easyqc"
    for name in REQUIRED_TOCS:
        content = b"[]\n"
        if name == "COLLECT-00.toc":
            content = repr(
                ([
                    ("demo.py", str(owned_source), owned_toc_type),
                    ("EasyQC", str(unowned_source), "EXECUTABLE"),
                ],)
            ).encode()
        _write(work / name, content)
    _write(work / "warn-easyqc.txt", warning_bytes)
    evidence_index = build_script.retain_pyinstaller_evidence(
        build_dir / "pyinstaller-work",
        build_dir,
    )
    return artifact, manifest, evidence_index, build_dir


def _raw_collect_row(
    final_path: str,
    *,
    raw_source: str,
    toc_type: str,
) -> dict[str, object]:
    return {
        "entry_type": "symlink" if toc_type == "SYMLINK" else "regular-file",
        "final_path": final_path,
        "raw_source": raw_source,
        "raw_source_text_sha256": build_script._raw_source_text_sha256(raw_source),
        "toc_type": toc_type,
    }


def _manifest_regular_row(
    manifest: object,
    final_path: str,
    *,
    source_kind: str = "python-distribution-file",
) -> dict[str, object]:
    entry = next(item for item in manifest.entries if item.path == final_path)
    assert entry.entry_type == "regular-file"
    return {
        "entry_type": "regular-file",
        "final_path": final_path,
        "package_path": final_path,
        "raw_source_text_sha256": "a" * 64,
        "source_identity": {
            "entry_type": "regular-file",
            "sha256": entry.sha256,
            "size": entry.size,
        },
        "source_kind": source_kind,
        "source_locator": f"fixture-source/{final_path}",
        "target_final_path": None,
        "toc_type": "DATA",
    }


def _authenticated_component(
    component_id: str,
    *,
    ecosystem: str,
    component_type: str,
    name: str,
    version: str,
    collected_files: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "collected_files": list(collected_files or []),
        "component_id": component_id,
        "ecosystem": ecosystem,
        "evidence_files": [{"kind": "fixture-authenticated-evidence"}],
        "license_candidates": [{"field": "fixture", "value": "MIT"}],
        "name": name,
        "provider_candidates": [
            {"field": "fixture", "value": "authenticated fixture"}
        ],
        "purl": f"pkg:generic/{name.lower()}@{version}",
        "type": component_type,
        "version": version,
    }


def test_python_symlink_aliases_inherit_only_exact_manifest_target_component(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    target = _write(artifact / "_internal/demo/libdemo.so", b"python target\n")
    alias = artifact / "_internal/demo/aliases/libdemo.so"
    alias.parent.mkdir(parents=True)
    try:
        alias.symlink_to("../libdemo.so")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")
    manifest = create_artifact_manifest(artifact)
    component_id = "library:demo@1.0"
    components = {
        component_id: _authenticated_component(
            component_id,
            ecosystem="python",
            component_type="library",
            name="demo",
            version="1.0",
            collected_files=[
                _manifest_regular_row(manifest, target.relative_to(artifact).as_posix())
            ],
        )
    }
    collect_rows = [
        _raw_collect_row(
            "_internal/demo/libdemo.so",
            raw_source=str(target),
            toc_type="BINARY",
        ),
        _raw_collect_row(
            "_internal/demo/aliases/libdemo.so",
            raw_source="../libdemo.so",
            toc_type="SYMLINK",
        ),
    ]
    before_components = canonical_json_bytes(components)
    before_collect = canonical_json_bytes(collect_rows)

    assignments = build_script._assign_python_symlink_aliases(
        collect_rows,
        manifest,
        components,
        {component_id},
    )

    assert assignments == [
        {
            "collected_file": {
                "entry_type": "symlink",
                "final_path": "_internal/demo/aliases/libdemo.so",
                "package_path": None,
                "raw_source_text_sha256": build_script._raw_source_text_sha256(
                    "../libdemo.so"
                ),
                "source_identity": {
                    "entry_type": "symlink",
                    "size": len("../libdemo.so".encode()),
                    "target_text_sha256": build_script._raw_source_text_sha256(
                        "../libdemo.so"
                    ),
                },
                "source_kind": "python-symlink-alias",
                "source_locator": (
                    "python-symlink-alias/_internal/demo/aliases/libdemo.so"
                ),
                "target_final_path": "_internal/demo/libdemo.so",
                "toc_type": "SYMLINK",
            },
            "component_id": component_id,
        }
    ]
    assert canonical_json_bytes(components) == before_components
    assert canonical_json_bytes(collect_rows) == before_collect


@pytest.mark.parametrize(
    "problem",
    [
        "absolute",
        "escape",
        "missing",
        "directory",
        "cycle",
        "cross-component",
        "duplicate-owner",
    ],
)
def test_python_symlink_aliases_reject_invalid_target_or_component(
    tmp_path: Path,
    problem: str,
) -> None:
    if problem in {"absolute", "escape"}:
        target = "/outside.so" if problem == "absolute" else "../../../outside.so"
        with pytest.raises(ReleaseContractError, match="absolute|escapes"):
            build_script._normalize_manifest_link_target(
                "_internal/demo/alias.so",
                target,
            )
        return

    artifact = tmp_path / "artifact"
    owned = _write(artifact / "_internal/demo/owned.so", b"owned\n")
    alias = artifact / "_internal/demo/alias.so"
    if problem == "missing":
        target_text = "missing.so"
    elif problem == "directory":
        (artifact / "_internal/demo/directory").mkdir(parents=True)
        target_text = "directory"
    elif problem == "cycle":
        target_text = "cycle.so"
        try:
            (artifact / "_internal/demo/cycle.so").symlink_to("alias.so")
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks are unavailable on this platform: {exc}")
    else:
        target_text = "owned.so"
    try:
        alias.symlink_to(target_text)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")

    manifest = create_artifact_manifest(artifact)
    owner_id = "library:foreign@1.0"
    approved_id = "library:approved@1.0"
    owner_ecosystem = "conda" if problem == "cross-component" else "python"
    components = {
        owner_id: _authenticated_component(
            owner_id,
            ecosystem=owner_ecosystem,
            component_type="library",
            name="foreign",
            version="1.0",
            collected_files=[
                _manifest_regular_row(manifest, owned.relative_to(artifact).as_posix())
            ],
        ),
        approved_id: _authenticated_component(
            approved_id,
            ecosystem="python",
            component_type="library",
            name="approved",
            version="1.0",
        ),
    }
    if problem == "duplicate-owner":
        components[approved_id]["collected_files"] = [
            _manifest_regular_row(manifest, owned.relative_to(artifact).as_posix())
        ]
    collect_rows = [
        _raw_collect_row(
            "_internal/demo/owned.so",
            raw_source=str(owned),
            toc_type="BINARY",
        ),
        _raw_collect_row(
            "_internal/demo/alias.so",
            raw_source=target_text,
            toc_type="SYMLINK",
        ),
    ]
    python_ids = (
        {approved_id}
        if problem == "cross-component"
        else {owner_id, approved_id}
        if problem == "duplicate-owner"
        else {owner_id}
    )

    with pytest.raises(
        ReleaseContractError,
        match=(
            "regular manifest target|authenticated Python component|"
            "duplicate authenticated component owner"
        ),
    ):
        build_script._assign_python_symlink_aliases(
            collect_rows,
            manifest,
            components,
            python_ids,
        )


def _explicit_build_role_fixture(
    tmp_path: Path,
    *,
    problem: str | None = None,
) -> tuple[
    object,
    list[dict[str, object]],
    dict[str, dict[str, object]],
    dict[str, str],
]:
    artifact = tmp_path / "artifact"
    paths = [
        "EasyQC",
        "_internal/base_library.zip",
        "_internal/libxcb-cursor.so.0",
        "_internal/template/hcpall_template.scene",
    ]
    if problem == "missing":
        paths.remove("_internal/template/hcpall_template.scene")
    elif problem == "renamed":
        paths[-1] = "_internal/template/hcpall_template.scenex"
    elif problem == "fifth":
        paths.append("_internal/unmatched.bin")
    for path in paths:
        _write(artifact / path, f"fixture {path}\n".encode())
    manifest = create_artifact_manifest(artifact)
    rows = [
        _raw_collect_row(
            path,
            raw_source=f"/authenticated/build/{path}",
            toc_type="EXECUTABLE" if path == "EasyQC" else "DATA",
        )
        for path in paths
    ]
    if problem == "duplicate":
        rows.append(dict(rows[0]))

    app_id = "application:easyqc@1.0.0"
    python_id = "library:python@3.10.17"
    cursor_id = "library:libxcb-cursor0@0.1.1-4ubuntu1"
    components = {
        app_id: _authenticated_component(
            app_id,
            ecosystem="easyqc",
            component_type="application",
            name="EasyQC",
            version="1.0.0",
        ),
        python_id: _authenticated_component(
            python_id,
            ecosystem="conda",
            component_type="library",
            name="python",
            version="3.10.17",
        ),
        cursor_id: _authenticated_component(
            cursor_id,
            ecosystem="debian",
            component_type="library",
            name="libxcb-cursor0",
            version="0.1.1-4ubuntu1",
        ),
    }
    role_component_ids = {
        "EasyQC": app_id,
        "_internal/base_library.zip": python_id,
        "_internal/libxcb-cursor.so.0": cursor_id,
        "_internal/template/hcpall_template.scene": app_id,
    }
    if problem == "pre-owned":
        components[app_id]["collected_files"] = [
            _manifest_regular_row(
                manifest,
                "EasyQC",
                source_kind="pyinstaller-generated",
            )
        ]
    elif problem == "missing-component":
        role_component_ids["_internal/base_library.zip"] = "library:missing@3.10.17"
    elif problem == "wrong-component-kind":
        components[python_id]["ecosystem"] = "generic-runtime"
    elif problem == "wrong-python-version":
        components[python_id]["version"] = "3.10.16"
    return manifest, rows, components, role_component_ids


def test_explicit_build_roles_bind_exact_authenticated_components(
    tmp_path: Path,
) -> None:
    manifest, rows, components, role_component_ids = _explicit_build_role_fixture(
        tmp_path
    )
    before_components = canonical_json_bytes(components)
    before_rows = canonical_json_bytes(rows)

    assignments = build_script._assign_explicit_build_roles(
        rows,
        manifest,
        components,
        role_component_ids,
    )

    assert [item["collected_file"]["final_path"] for item in assignments] == [
        "EasyQC",
        "_internal/base_library.zip",
        "_internal/libxcb-cursor.so.0",
        "_internal/template/hcpall_template.scene",
    ]
    assert [item["collected_file"]["source_kind"] for item in assignments] == [
        "pyinstaller-generated",
        "pyinstaller-generated",
        "verified-cursor-deb",
        "easyqc-source-file",
    ]
    assert assignments[0]["component_id"] == assignments[3]["component_id"]
    assert assignments[1]["component_id"] == "library:python@3.10.17"
    assert assignments[2]["component_id"] == (
        "library:libxcb-cursor0@0.1.1-4ubuntu1"
    )
    for assignment in assignments:
        row = assignment["collected_file"]
        entry = next(item for item in manifest.entries if item.path == row["final_path"])
        assert row["source_identity"] == {
            "entry_type": "regular-file",
            "sha256": entry.sha256,
            "size": entry.size,
        }
        assert row["package_path"] is None
        assert row["target_final_path"] is None
    assert canonical_json_bytes(components) == before_components
    assert canonical_json_bytes(rows) == before_rows


@pytest.mark.parametrize(
    ("problem", "message"),
    [
        ("missing", "exact unmatched build roles"),
        ("renamed", "exact unmatched build roles"),
        ("duplicate", "duplicate COLLECT final path"),
        ("fifth", "exact unmatched build roles"),
        ("pre-owned", "exact unmatched build roles"),
        ("missing-component", "authenticated component"),
        ("wrong-component-kind", "Conda Python"),
        ("wrong-python-version", "Conda Python"),
    ],
)
def test_explicit_build_roles_reject_missing_renamed_duplicate_or_fifth_role(
    tmp_path: Path,
    problem: str,
    message: str,
) -> None:
    manifest, rows, components, role_component_ids = _explicit_build_role_fixture(
        tmp_path,
        problem=problem,
    )

    with pytest.raises(ReleaseContractError, match=message):
        build_script._assign_explicit_build_roles(
            rows,
            manifest,
            components,
            role_component_ids,
        )


def _fixture_runtime_identity(root: Path, version: str = "3.10.17") -> RuntimeIdentity:
    artifact = _write(root / "runtime/python", b"authenticated runtime\n")
    identity_file = _write(root / "runtime-identity.json", b"identity\n")
    return RuntimeIdentity(
        implementation="CPython",
        version=version,
        target="linux-x86_64",
        target_triple="x86_64-manylinux_2_34",
        provider="fixture Conda runtime",
        source_provenance="fixture://conda-runtime",
        artifact=_reference("runtime artifact", artifact, "runtime/python"),
        identity_file=_reference(
            "runtime identity",
            identity_file,
            "runtime-identity.json",
        ),
    )


def _write_conda_record(
    base_prefix: Path,
    *,
    name: str,
    version: str,
    build: str,
    files: list[str],
    subdir: str = "linux-64",
    overrides: dict[str, object] | None = None,
) -> tuple[Path, dict[str, object]]:
    package_name = f"{name}-{version}-{build}"
    payload: dict[str, object] = {
        "build": build,
        "channel": "https://conda.example.invalid/conda-forge",
        "files": sorted(files),
        "license": "Fixture-License-1.0",
        "name": name,
        "sha256": build_script._raw_source_text_sha256(package_name),
        "subdir": subdir,
        "url": (
            "https://conda.example.invalid/conda-forge/"
            f"{subdir}/{package_name}.conda"
        ),
        "version": version,
    }
    if overrides:
        payload.update(overrides)
    path = base_prefix / "conda-meta" / f"{package_name}.json"
    _write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode())
    return path, payload


def _conda_build_dir(packet_root: Path) -> Path:
    build_dir = packet_root / "build"
    (build_dir / "evidence/components").mkdir(parents=True)
    return build_dir


def _existing_collected_row(final_path: str) -> dict[str, object]:
    return {
        "entry_type": "regular-file",
        "final_path": final_path,
        "package_path": "fixture.py",
        "raw_source_text_sha256": "a" * 64,
        "source_identity": {
            "entry_type": "regular-file",
            "sha256": "b" * 64,
            "size": 1,
        },
        "source_kind": "python-distribution-file",
        "source_locator": "python-distribution/fixture/fixture.py",
        "target_final_path": None,
        "toc_type": "DATA",
    }


def test_conda_component_evidence_assigns_only_exact_contributors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_prefix = tmp_path / "conda"
    python_files = [
        "lib/python3.10/LICENSE.txt",
        "lib/python3.10/os.py",
        "lib/python3.10/site.py",
    ]
    tk_files = ["lib/libtk.so", "share/licenses/tk/LICENSE"]
    unused_files = ["lib/libunused.so", "share/licenses/unused/LICENSE"]
    python_meta, python_payload = _write_conda_record(
        base_prefix,
        name="python",
        version="3.10.17",
        build="h0_cpython",
        files=python_files,
    )
    tk_meta, tk_payload = _write_conda_record(
        base_prefix,
        name="tk",
        version="8.6.13",
        build="h1",
        files=tk_files,
    )
    _write_conda_record(
        base_prefix,
        name="unused",
        version="1.0",
        build="h2",
        files=unused_files,
    )
    source_bytes = {
        "lib/python3.10/LICENSE.txt": b"Python fixture license\n",
        "lib/python3.10/os.py": b"OS = 'fixture'\n",
        "lib/python3.10/site.py": b"SITE = 'owned by Python metadata'\n",
        "lib/libtk.so": b"fixture tk library\n",
        "share/licenses/tk/LICENSE": b"Tk fixture license\n",
        "lib/libunused.so": b"unused\n",
        "share/licenses/unused/LICENSE": b"Unused fixture license\n",
    }
    for relative, data in source_bytes.items():
        _write(base_prefix / relative, data)

    os_source = base_prefix / "lib/python3.10/os.py"
    site_source = base_prefix / "lib/python3.10/site.py"
    tk_source = base_prefix / "lib/libtk.so"
    outside_source = _write(tmp_path / "system/libsystem.so", b"system\n")
    collect_rows = [
        _raw_collect_row(
            "_internal/libsystem.so",
            raw_source=str(outside_source),
            toc_type="BINARY",
        ),
        _raw_collect_row(
            "_internal/libtk.so",
            raw_source=str(tk_source),
            toc_type="BINARY",
        ),
        _raw_collect_row(
            "_internal/os.py",
            raw_source=str(os_source),
            toc_type="DATA",
        ),
        _raw_collect_row(
            "_internal/site.py",
            raw_source=str(site_source),
            toc_type="DATA",
        ),
    ]
    existing_id = "library:python-fixture@1.0"
    existing_components = {
        existing_id: _authenticated_component(
            existing_id,
            ecosystem="python",
            component_type="library",
            name="python-fixture",
            version="1.0",
            collected_files=[_existing_collected_row("_internal/site.py")],
        )
    }
    runtime = _fixture_runtime_identity(tmp_path / "runtime-input")
    monkeypatch.setattr(build_script.sys, "base_prefix", str(base_prefix))
    before_collect = canonical_json_bytes(collect_rows)
    before_components = canonical_json_bytes(existing_components)

    first_build = _conda_build_dir(tmp_path / "first-packet")
    second_build = _conda_build_dir(tmp_path / "second-packet")
    first = build_script.capture_conda_component_evidence(
        collect_rows,
        existing_components,
        first_build,
        runtime,
    )
    second = build_script.capture_conda_component_evidence(
        collect_rows,
        existing_components,
        second_build,
        runtime,
    )

    assert list(first) == ["library:python@3.10.17", "library:tk@8.6.13"]
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert str(tmp_path).encode() not in canonical_json_bytes(first)
    assert canonical_json_bytes(collect_rows) == before_collect
    assert canonical_json_bytes(existing_components) == before_components

    python = first["library:python@3.10.17"]
    assert python == {
        "collected_files": [
            {
                "entry_type": "regular-file",
                "final_path": "_internal/os.py",
                "package_path": "lib/python3.10/os.py",
                "raw_source_text_sha256": build_script._raw_source_text_sha256(
                    str(os_source)
                ),
                "source_identity": {
                    "entry_type": "regular-file",
                    "sha256": sha256_file(os_source),
                    "size": os_source.stat().st_size,
                },
                "source_kind": "conda-package-file",
                "source_locator": (
                    "conda-package/python-3.10.17-h0_cpython-linux-64/"
                    "lib/python3.10/os.py"
                ),
                "target_final_path": None,
                "toc_type": "DATA",
            }
        ],
        "component_id": "library:python@3.10.17",
        "ecosystem": "conda",
        "evidence_files": [
            {
                "kind": "conda-license",
                "retained_path": (
                    "build/evidence/components/conda/"
                    "python-3.10.17-h0_cpython-linux-64/licenses/"
                    "lib/python3.10/LICENSE.txt"
                ),
                "sha256": sha256_file(
                    base_prefix / "lib/python3.10/LICENSE.txt"
                ),
                "size": (base_prefix / "lib/python3.10/LICENSE.txt").stat().st_size,
                "source_locator": (
                    "conda-package/python-3.10.17-h0_cpython-linux-64/"
                    "lib/python3.10/LICENSE.txt"
                ),
            },
            {
                "kind": "conda-meta",
                "retained_path": (
                    "build/evidence/components/conda/"
                    "python-3.10.17-h0_cpython-linux-64/conda-meta.json"
                ),
                "sha256": sha256_file(python_meta),
                "size": python_meta.stat().st_size,
                "source_locator": (
                    "conda-meta/python-3.10.17-h0_cpython.json"
                ),
            },
        ],
        "license_candidates": [
            {"field": "license", "value": "Fixture-License-1.0"}
        ],
        "name": "python",
        "provider_candidates": [
            {
                "field": "channel",
                "value": "https://conda.example.invalid/conda-forge",
            },
            {"field": "url", "value": python_payload["url"]},
        ],
        "purl": "pkg:conda/python@3.10.17?build=h0_cpython&subdir=linux-64",
        "type": "library",
        "version": "3.10.17",
    }
    assert first["library:tk@8.6.13"]["provider_candidates"][1] == {
        "field": "url",
        "value": tk_payload["url"],
    }
    assert [
        row["final_path"]
        for component in first.values()
        for row in component["collected_files"]
    ] == ["_internal/os.py", "_internal/libtk.so"]
    assert not (
        first_build / "evidence/components/conda/unused-1.0-h2-linux-64"
    ).exists()
    first_packet = first_build.parent
    for component in first.values():
        for evidence in component["evidence_files"]:
            retained = first_packet / evidence["retained_path"]
            assert retained.is_file() and not retained.is_symlink()
            assert sha256_file(retained) == evidence["sha256"]


def test_conda_component_evidence_leaves_nonmembers_unassigned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_prefix = tmp_path / "conda"
    _write_conda_record(
        base_prefix,
        name="python",
        version="3.10.17",
        build="h0_cpython",
        files=["lib/python3.10/LICENSE.txt"],
    )
    _write(base_prefix / "lib/python3.10/LICENSE.txt", b"license\n")
    nonmember = _write(base_prefix / "lib/not-a-package-member.so", b"member?\n")
    build_dir = _conda_build_dir(tmp_path / "packet")
    runtime = _fixture_runtime_identity(tmp_path / "runtime-input")
    monkeypatch.setattr(build_script.sys, "base_prefix", str(base_prefix))

    result = build_script.capture_conda_component_evidence(
        [
            _raw_collect_row(
                "_internal/not-a-package-member.so",
                raw_source=str(nonmember),
                toc_type="BINARY",
            )
        ],
        {},
        build_dir,
        runtime,
    )

    assert result == {}
    assert not (build_dir / "evidence/components/conda").exists()


@pytest.mark.parametrize(
    "metadata_shape",
    ["unsorted-contributor-files", "license-less-empty-marker"],
)
def test_conda_component_evidence_accepts_real_installed_metadata_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata_shape: str,
) -> None:
    base_prefix = tmp_path / "conda"
    member = "lib/member.so"
    license_member = "share/licenses/python/LICENSE"
    files = [member]
    overrides = None
    if metadata_shape == "unsorted-contributor-files":
        files = [member, license_member]
        overrides = {"files": [license_member, member]}
    _write_conda_record(
        base_prefix,
        name="python",
        version="3.10.17",
        build="h0_cpython",
        files=files,
        overrides=overrides,
    )
    source = _write(base_prefix / member, b"member\n")
    if metadata_shape == "unsorted-contributor-files":
        _write(base_prefix / license_member, b"license\n")
    else:
        _write_conda_record(
            base_prefix,
            name="_libgcc_mutex",
            version="0.1",
            build="main",
            files=[],
            overrides={"license": None},
        )
    build_dir = _conda_build_dir(tmp_path / "packet")
    runtime = _fixture_runtime_identity(tmp_path / "runtime-input")
    monkeypatch.setattr(build_script.sys, "base_prefix", str(base_prefix))

    result = build_script.capture_conda_component_evidence(
        [
            _raw_collect_row(
                "_internal/member.so",
                raw_source=str(source),
                toc_type="BINARY",
            )
        ],
        {},
        build_dir,
        runtime,
    )

    assert list(result) == ["library:python@3.10.17"]
    assert result["library:python@3.10.17"]["collected_files"][0][
        "package_path"
    ] == member


@pytest.mark.parametrize("duplicate_kind", ["within-package", "across-packages"])
def test_conda_component_evidence_rejects_duplicate_file_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    duplicate_kind: str,
) -> None:
    base_prefix = tmp_path / "conda"
    shared = "lib/shared.so"
    python_files = [shared, shared] if duplicate_kind == "within-package" else [shared]
    _write_conda_record(
        base_prefix,
        name="python",
        version="3.10.17",
        build="h0_cpython",
        files=python_files,
    )
    if duplicate_kind == "across-packages":
        _write_conda_record(
            base_prefix,
            name="other",
            version="1.0",
            build="h1",
            files=[shared],
        )
    source = _write(base_prefix / shared, b"shared\n")
    build_dir = _conda_build_dir(tmp_path / "packet")
    runtime = _fixture_runtime_identity(tmp_path / "runtime-input")
    monkeypatch.setattr(build_script.sys, "base_prefix", str(base_prefix))

    with pytest.raises(ReleaseContractError, match="duplicate Conda file owner"):
        build_script.capture_conda_component_evidence(
            [
                _raw_collect_row(
                    "_internal/shared.so",
                    raw_source=str(source),
                    toc_type="BINARY",
                )
            ],
            {},
            build_dir,
            runtime,
        )
    assert not (build_dir / "evidence/components/conda").exists()


@pytest.mark.parametrize(
    "problem",
    [
        "filename",
        "package-sha256",
        "contributor-license",
        "runtime-version",
        "duplicate-json-key",
        "metadata-symlink",
        "metadata-parent-symlink",
        "source-symlink",
        "source-parent-symlink",
        "noncanonical-member",
        "nul-member",
    ],
)
def test_conda_component_evidence_rejects_invalid_identity_or_unsafe_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
) -> None:
    base_prefix = tmp_path / "conda"
    member = "lib/member.so"
    version = "3.10.16" if problem == "runtime-version" else "3.10.17"
    if problem == "noncanonical-member":
        files = ["lib/../member.so"]
    elif problem == "nul-member":
        files = ["lib/\x00member.so"]
    else:
        files = [member]
    overrides = None
    if problem == "package-sha256":
        overrides = {"sha256": "bad"}
    elif problem == "contributor-license":
        overrides = {"license": None}
    metadata, payload = _write_conda_record(
        base_prefix,
        name="python",
        version=version,
        build="h0_cpython",
        files=files,
        overrides=overrides,
    )
    if problem == "filename":
        metadata.rename(metadata.with_name("renamed-python.json"))
    elif problem == "duplicate-json-key":
        metadata.write_text(
            '{"name":"python","name":"forged"}\n',
            encoding="utf-8",
        )
    elif problem == "metadata-symlink":
        outside = _write(
            tmp_path / "outside-metadata.json",
            (json.dumps(payload, sort_keys=True) + "\n").encode(),
        )
        metadata.unlink()
        try:
            metadata.symlink_to(outside)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks are unavailable on this platform: {exc}")
    elif problem == "metadata-parent-symlink":
        outside_meta = tmp_path / "outside-conda-meta"
        outside_meta.mkdir()
        moved = outside_meta / metadata.name
        metadata.rename(moved)
        (base_prefix / "conda-meta").rmdir()
        try:
            (base_prefix / "conda-meta").symlink_to(
                outside_meta,
                target_is_directory=True,
            )
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks are unavailable on this platform: {exc}")

    if problem == "source-symlink":
        outside_source = _write(tmp_path / "outside-source.so", b"outside\n")
        source = base_prefix / member
        source.parent.mkdir(parents=True, exist_ok=True)
        try:
            source.symlink_to(outside_source)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks are unavailable on this platform: {exc}")
    elif problem == "source-parent-symlink":
        outside_parent = tmp_path / "outside-parent"
        _write(outside_parent / "member.so", b"outside parent\n")
        try:
            (base_prefix / "lib").symlink_to(
                outside_parent,
                target_is_directory=True,
            )
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks are unavailable on this platform: {exc}")
        source = base_prefix / member
    else:
        source = _write(base_prefix / member, b"member\n")

    build_dir = _conda_build_dir(tmp_path / "packet")
    runtime = _fixture_runtime_identity(tmp_path / "runtime-input")
    monkeypatch.setattr(build_script.sys, "base_prefix", str(base_prefix))

    with pytest.raises(ReleaseContractError):
        build_script.capture_conda_component_evidence(
            [
                _raw_collect_row(
                    "_internal/member.so",
                    raw_source=str(source),
                    toc_type="BINARY",
                )
            ],
            {},
            build_dir,
            runtime,
        )
    assert not (build_dir / "evidence/components/conda").exists()


@pytest.mark.parametrize("changed_source", ["metadata", "license"])
def test_conda_component_evidence_rejects_changed_metadata_or_license(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_source: str,
) -> None:
    base_prefix = tmp_path / "conda"
    member = "lib/member.so"
    license_member = "share/licenses/python/LICENSE"
    metadata, _payload = _write_conda_record(
        base_prefix,
        name="python",
        version="3.10.17",
        build="h0_cpython",
        files=[member, license_member],
    )
    source = _write(base_prefix / member, b"member\n")
    license_path = _write(base_prefix / license_member, b"license before\n")
    build_dir = _conda_build_dir(tmp_path / "packet")
    runtime = _fixture_runtime_identity(tmp_path / "runtime-input")
    monkeypatch.setattr(build_script.sys, "base_prefix", str(base_prefix))
    original_open = build_script.os.open
    target_name = metadata.name if changed_source == "metadata" else "LICENSE"
    opens = 0

    def mutate_before_reopen(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal opens
        if path == target_name and dir_fd is not None:
            opens += 1
            if opens == 2:
                target = metadata if changed_source == "metadata" else license_path
                target.write_bytes(target.read_bytes() + b"changed\n")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(build_script.os, "open", mutate_before_reopen)

    with pytest.raises(ReleaseContractError, match="changed"):
        build_script.capture_conda_component_evidence(
            [
                _raw_collect_row(
                    "_internal/member.so",
                    raw_source=str(source),
                    toc_type="BINARY",
                )
            ],
            {},
            build_dir,
            runtime,
        )


_DPKG_QUERY = "/usr/bin/dpkg-query"
_DPKG_ENV = {
    "LANG": "C.UTF-8",
    "LANGUAGE": "C",
    "LC_ALL": "C.UTF-8",
}
_DPKG_STATUS_FORMAT = (
    "${Package}\\t${binary:Package}\\t${db:Status-Abbrev}\\t${Version}\\t"
    "${Architecture}\\t${source:Package}\\t${source:Version}\\t${Maintainer}\\t"
    "${Original-Maintainer}\\n"
)
_DPKG_VERSION_BYTES = (
    b"Debian dpkg-query package management program query tool version "
    b"1.21.1 (amd64).\n"
    b"This is free software; see the GNU General Public License version 2 or\n"
    b"later for copying conditions. There is NO warranty.\n"
)


def _debian_system_root(tmp_path: Path) -> Path:
    root = tmp_path / "system-root"
    (root / "usr/lib").mkdir(parents=True)
    (root / "usr/share/doc").mkdir(parents=True)
    return root


def _debian_capture(
    monkeypatch: pytest.MonkeyPatch,
    system_root: Path,
):
    capture = build_script.capture_debian_component_evidence
    monkeypatch.setitem(capture.__globals__, "_SYSTEM_ROOT", system_root)
    return capture


def _assert_dpkg_invocation(options: dict[str, object]) -> None:
    assert options == {
        "check": False,
        "env": _DPKG_ENV,
        "shell": False,
        "stderr": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "timeout": 30,
    }


def _alpha_status(
    *,
    package: str = "alpha",
    binary_package: str = "alpha:amd64",
    status: str = "ii ",
) -> bytes:
    return (
        f"{package}\t{binary_package}\t{status}\t1:2.0-3ubuntu1\tamd64\t"
        "alpha-source\t1:2.0-3ubuntu1\t"
        "Ubuntu Maintainer <ubuntu@example.invalid>\t"
        "Debian Maintainer <debian@example.invalid>\n"
    ).encode()


def _alpha_system_source(root: Path) -> Path:
    library = _write(root / "usr/lib/libalpha.so.1", b"alpha library\n")
    soname = root / "usr/lib/libalpha.so.0"
    soname.symlink_to(library.name)
    (root / "lib").symlink_to("usr/lib", target_is_directory=True)
    _write(
        root / "usr/share/doc/alpha/copyright",
        b"Format: fixture\nLicense: GPL-2+\nLicense: MIT\n",
    )
    return root / "lib/libalpha.so.0"


def test_debian_component_evidence_assigns_exact_merged_usr_contributors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _debian_system_root(tmp_path)
    source = _alpha_system_source(root)
    excluded = _write(root / "etc/already-owned.conf", b"owned elsewhere\n")
    collect_rows = [
        _raw_collect_row(
            "_internal/already-owned.conf",
            raw_source=str(excluded),
            toc_type="DATA",
        ),
        _raw_collect_row(
            "_internal/libalpha.so.0",
            raw_source=str(source),
            toc_type="BINARY",
        ),
    ]
    existing_id = "library:higher-precedence@1.0"
    existing_components = {
        existing_id: _authenticated_component(
            existing_id,
            ecosystem="conda",
            component_type="library",
            name="higher-precedence",
            version="1.0",
            collected_files=[
                _existing_collected_row("_internal/already-owned.conf")
            ],
        )
    }
    owner_bytes = b"alpha:amd64: /usr/lib/libalpha.so.0\n"
    commands: list[list[str]] = []

    def fake_run(command: list[str], **options: object):
        _assert_dpkg_invocation(options)
        commands.append(command)
        if command == [_DPKG_QUERY, "--version"]:
            return subprocess.CompletedProcess(command, 0, _DPKG_VERSION_BYTES, b"")
        if command == [
            _DPKG_QUERY,
            "-S",
            "/lib/libalpha.so.0",
            "/usr/lib/libalpha.so.0",
        ]:
            return subprocess.CompletedProcess(
                command,
                1,
                owner_bytes,
                b"dpkg-query: no path found matching pattern /lib/libalpha.so.0\n",
            )
        if command == [
            _DPKG_QUERY,
            "-W",
            f"--showformat={_DPKG_STATUS_FORMAT}",
            "alpha:amd64",
        ]:
            return subprocess.CompletedProcess(command, 0, _alpha_status(), b"")
        raise AssertionError(f"unexpected dpkg command: {command!r}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    capture = _debian_capture(monkeypatch, root)
    before_collect = canonical_json_bytes(collect_rows)
    before_components = canonical_json_bytes(existing_components)
    first_build = _conda_build_dir(tmp_path / "first-packet")
    second_build = _conda_build_dir(tmp_path / "second-packet")

    first = capture(collect_rows, existing_components, first_build)
    second = capture(collect_rows, existing_components, second_build)

    assert commands == commands[:3] * 2
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_json_bytes(collect_rows) == before_collect
    assert canonical_json_bytes(existing_components) == before_components
    assert str(tmp_path).encode() not in canonical_json_bytes(first)
    assert list(first) == ["library:alpha@1:2.0-3ubuntu1"]
    component = first["library:alpha@1:2.0-3ubuntu1"]
    assert component["ecosystem"] == "debian"
    assert component["type"] == "library"
    assert component["name"] == "alpha"
    assert component["version"] == "1:2.0-3ubuntu1"
    assert component["purl"] == (
        "pkg:deb/ubuntu/alpha@1%3A2.0-3ubuntu1?arch=amd64"
    )
    assert component["license_candidates"] == [
        {"field": "copyright-License", "value": "GPL-2+"},
        {"field": "copyright-License", "value": "MIT"},
    ]
    assert component["provider_candidates"] == [
        {
            "field": "Maintainer",
            "value": "Ubuntu Maintainer <ubuntu@example.invalid>",
        },
        {
            "field": "Original-Maintainer",
            "value": "Debian Maintainer <debian@example.invalid>",
        },
        {"field": "Source", "value": "alpha-source (1:2.0-3ubuntu1)"},
    ]
    assert component["collected_files"] == [
        {
            "entry_type": "regular-file",
            "final_path": "_internal/libalpha.so.0",
            "package_path": "usr/lib/libalpha.so.0",
            "raw_source_text_sha256": build_script._raw_source_text_sha256(
                str(source)
            ),
            "source_identity": {
                "entry_type": "regular-file",
                "sha256": sha256_file(root / "usr/lib/libalpha.so.1"),
                "size": (root / "usr/lib/libalpha.so.1").stat().st_size,
            },
            "source_kind": "debian-package-file",
            "source_locator": (
                "debian-package/alpha-1:2.0-3ubuntu1-amd64/"
                "usr/lib/libalpha.so.0"
            ),
            "target_final_path": None,
            "toc_type": "BINARY",
        }
    ]
    assert [row["kind"] for row in component["evidence_files"]] == [
        "debian-copyright",
        "debian-installed-status",
        "debian-owner-query",
        "dpkg-query-version",
    ]
    expected_evidence = {
        "debian-copyright": (root / "usr/share/doc/alpha/copyright").read_bytes(),
        "debian-installed-status": _alpha_status(),
        "debian-owner-query": owner_bytes,
        "dpkg-query-version": _DPKG_VERSION_BYTES,
    }
    for row in component["evidence_files"]:
        retained = first_build.parent / row["retained_path"]
        assert retained.is_file() and not retained.is_symlink()
        assert retained.read_bytes() == expected_evidence[row["kind"]]
        assert sha256_file(retained) == row["sha256"]


def test_debian_component_evidence_sorts_multiple_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _debian_system_root(tmp_path)
    alpha_source = _write(root / "etc/alpha.conf", b"alpha\n")
    beta_source = _write(root / "etc/beta.conf", b"beta\n")
    _write(root / "usr/share/doc/alpha/copyright", b"License: MIT\n")
    _write(root / "usr/share/doc/beta/copyright", b"License: BSD-3-Clause\n")
    beta_status = (
        b"beta\tbeta:amd64\tii \t3.0-1\tamd64\tbeta-source\t3.0-1\t"
        b"Beta Maintainer <beta@example.invalid>\t\n"
    )

    def fake_run(command: list[str], **options: object):
        _assert_dpkg_invocation(options)
        if command == [_DPKG_QUERY, "--version"]:
            return subprocess.CompletedProcess(command, 0, _DPKG_VERSION_BYTES, b"")
        if command[1] == "-S":
            assert command[2:] == ["/etc/alpha.conf", "/etc/beta.conf"]
            return subprocess.CompletedProcess(
                command,
                0,
                (
                    b"beta:amd64: /etc/beta.conf\n"
                    b"alpha:amd64: /etc/alpha.conf\n"
                ),
                b"",
            )
        assert command[1:3] == [
            "-W",
            f"--showformat={_DPKG_STATUS_FORMAT}",
        ]
        assert command[3:] == ["alpha:amd64", "beta:amd64"]
        return subprocess.CompletedProcess(
            command,
            0,
            beta_status + _alpha_status(),
            b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = _debian_capture(monkeypatch, root)(
        [
            _raw_collect_row(
                "_internal/beta.conf",
                raw_source=str(beta_source),
                toc_type="DATA",
            ),
            _raw_collect_row(
                "_internal/alpha.conf",
                raw_source=str(alpha_source),
                toc_type="DATA",
            ),
        ],
        {},
        _conda_build_dir(tmp_path / "packet"),
    )

    assert list(result) == [
        "library:alpha@1:2.0-3ubuntu1",
        "library:beta@3.0-1",
    ]
    assert [
        result[component_id]["collected_files"][0]["final_path"]
        for component_id in result
    ] == ["_internal/alpha.conf", "_internal/beta.conf"]


def test_debian_component_evidence_leaves_non_system_sources_unassigned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _debian_system_root(tmp_path)
    source = _write(root / "opt/vendor/libvendor.so", b"vendor\n")
    build_dir = _conda_build_dir(tmp_path / "packet")
    capture = _debian_capture(monkeypatch, root)

    def unexpected_run(*_args: object, **_kwargs: object):
        raise AssertionError("non-system sources must not invoke dpkg-query")

    monkeypatch.setattr(subprocess, "run", unexpected_run)

    assert capture(
        [
            _raw_collect_row(
                "_internal/libvendor.so",
                raw_source=str(source),
                toc_type="BINARY",
            )
        ],
        {},
        build_dir,
    ) == {}
    assert not (build_dir / "evidence/components/debian").exists()


@pytest.mark.parametrize("owner_problem", ["zero", "multiple"])
def test_debian_component_evidence_rejects_zero_or_multiple_owners(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_problem: str,
) -> None:
    root = _debian_system_root(tmp_path)
    source = _write(root / "etc/alpha.conf", b"alpha\n")
    capture = _debian_capture(monkeypatch, root)

    def fake_run(command: list[str], **options: object):
        _assert_dpkg_invocation(options)
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, _DPKG_VERSION_BYTES, b"")
        assert command == [_DPKG_QUERY, "-S", "/etc/alpha.conf"]
        if owner_problem == "zero":
            return subprocess.CompletedProcess(command, 1, b"", b"no match\n")
        return subprocess.CompletedProcess(
            command,
            0,
            b"alpha:amd64: /etc/alpha.conf\nbeta:amd64: /etc/alpha.conf\n",
            b"",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ReleaseContractError, match="unique installed owner"):
        capture(
            [
                _raw_collect_row(
                    "_internal/alpha.conf",
                    raw_source=str(source),
                    toc_type="DATA",
                )
            ],
            {},
            _conda_build_dir(tmp_path / "packet"),
        )


def test_debian_component_evidence_rejects_merged_usr_inode_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _debian_system_root(tmp_path)
    source = _write(root / "lib/libalpha.so.0", b"unmerged source\n")
    _write(root / "usr/lib/libalpha.so.0", b"different usr source\n")
    capture = _debian_capture(monkeypatch, root)

    def fake_run(command: list[str], **options: object):
        _assert_dpkg_invocation(options)
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, _DPKG_VERSION_BYTES, b"")
        assert command[1] == "-S"
        return subprocess.CompletedProcess(
            command,
            1,
            b"alpha:amd64: /usr/lib/libalpha.so.0\n",
            b"no match for /lib/libalpha.so.0\n",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ReleaseContractError, match="same installed file"):
        capture(
            [
                _raw_collect_row(
                    "_internal/libalpha.so.0",
                    raw_source=str(source),
                    toc_type="BINARY",
                )
            ],
            {},
            _conda_build_dir(tmp_path / "packet"),
        )


@pytest.mark.parametrize("unsafe_suffix", ["alpha\x00.conf", "alpha*.conf"])
def test_debian_component_evidence_rejects_malformed_system_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_suffix: str,
) -> None:
    root = _debian_system_root(tmp_path)
    capture = _debian_capture(monkeypatch, root)

    def unexpected_run(*_args: object, **_kwargs: object):
        raise AssertionError("malformed sources must fail before dpkg-query")

    monkeypatch.setattr(subprocess, "run", unexpected_run)

    with pytest.raises(ReleaseContractError, match="malformed|query syntax"):
        capture(
            [
                _raw_collect_row(
                    "_internal/alpha.conf",
                    raw_source=f"{root.as_posix()}/etc/{unsafe_suffix}",
                    toc_type="DATA",
                )
            ],
            {},
            _conda_build_dir(tmp_path / "packet"),
        )


@pytest.mark.parametrize(
    "unsafe_link",
    ["source-cycle", "copyright-escape", "copyright-cycle"],
)
def test_debian_component_evidence_rejects_unsafe_source_or_copyright_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_link: str,
) -> None:
    root = _debian_system_root(tmp_path)
    source = root / "etc/alpha.conf"
    source.parent.mkdir(parents=True, exist_ok=True)
    if unsafe_link == "source-cycle":
        source.symlink_to("alpha.conf")
    else:
        _write(source, b"alpha\n")
        copyright_path = root / "usr/share/doc/alpha/copyright"
        copyright_path.parent.mkdir(parents=True, exist_ok=True)
        target = (
            "../../../../../outside-copyright"
            if unsafe_link == "copyright-escape"
            else "copyright"
        )
        copyright_path.symlink_to(target)
    capture = _debian_capture(monkeypatch, root)

    def fake_run(command: list[str], **options: object):
        _assert_dpkg_invocation(options)
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, _DPKG_VERSION_BYTES, b"")
        if command[1] == "-S":
            return subprocess.CompletedProcess(
                command, 0, b"alpha:amd64: /etc/alpha.conf\n", b""
            )
        return subprocess.CompletedProcess(command, 0, _alpha_status(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ReleaseContractError, match="symlink|cycle|escape"):
        capture(
            [
                _raw_collect_row(
                    "_internal/alpha.conf",
                    raw_source=str(source),
                    toc_type="DATA",
                )
            ],
            {},
            _conda_build_dir(tmp_path / "packet"),
        )


@pytest.mark.parametrize(
    "evidence_problem",
    [
        "fatal-owner-command",
        "malformed-owner",
        "oversize-owner",
        "oversize-query-path",
        "timeout",
        "unexpected-owner-path",
        "wrong-status",
        "status-owner-mismatch",
        "malformed-version",
    ],
)
def test_debian_component_evidence_rejects_invalid_dpkg_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evidence_problem: str,
) -> None:
    root = _debian_system_root(tmp_path)
    source = _write(root / "etc/alpha.conf", b"alpha\n")
    _write(root / "usr/share/doc/alpha/copyright", b"License: MIT\n")
    capture = _debian_capture(monkeypatch, root)
    if evidence_problem == "oversize-owner":
        monkeypatch.setitem(capture.__globals__, "_MAX_SUBPROCESS_BYTES", 16)
    elif evidence_problem == "oversize-query-path":
        monkeypatch.setitem(capture.__globals__, "_MAX_SYSTEM_PATH_BYTES", 8)

    def fake_run(command: list[str], **options: object):
        _assert_dpkg_invocation(options)
        if evidence_problem == "timeout":
            raise subprocess.TimeoutExpired(command, 30)
        if command[1] == "--version":
            stdout = (
                b"not a dpkg-query version\n"
                if evidence_problem == "malformed-version"
                else _DPKG_VERSION_BYTES
            )
            return subprocess.CompletedProcess(command, 0, stdout, b"")
        if command[1] == "-S":
            if evidence_problem == "fatal-owner-command":
                return subprocess.CompletedProcess(command, 2, b"", b"fatal\n")
            owner = b"alpha:amd64: /etc/alpha.conf\n"
            if evidence_problem == "malformed-owner":
                owner = b"not an owner record\n"
            elif evidence_problem == "oversize-owner":
                owner = b"x" * 17
            elif evidence_problem == "unexpected-owner-path":
                owner = b"alpha:amd64: /etc/not-queried.conf\n"
            return subprocess.CompletedProcess(command, 0, owner, b"")
        status = _alpha_status()
        if evidence_problem == "wrong-status":
            status = _alpha_status(status="rc ")
        elif evidence_problem == "status-owner-mismatch":
            status = _alpha_status(package="beta", binary_package="beta:amd64")
        return subprocess.CompletedProcess(command, 0, status, b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ReleaseContractError):
        capture(
            [
                _raw_collect_row(
                    "_internal/alpha.conf",
                    raw_source=str(source),
                    toc_type="DATA",
                )
            ],
            {},
            _conda_build_dir(tmp_path / "packet"),
        )


def test_debian_component_evidence_rejects_changed_copyright(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _debian_system_root(tmp_path)
    source = _write(root / "etc/alpha.conf", b"alpha\n")
    copyright_path = _write(
        root / "usr/share/doc/alpha/copyright",
        b"License: before\n",
    )
    capture = _debian_capture(monkeypatch, root)

    def fake_run(command: list[str], **options: object):
        _assert_dpkg_invocation(options)
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, _DPKG_VERSION_BYTES, b"")
        if command[1] == "-S":
            return subprocess.CompletedProcess(
                command, 0, b"alpha:amd64: /etc/alpha.conf\n", b""
            )
        return subprocess.CompletedProcess(command, 0, _alpha_status(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    original_open = build_script.os.open
    opens = 0

    def mutate_before_reopen(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal opens
        if path == "copyright" and dir_fd is not None:
            opens += 1
            if opens == 2:
                copyright_path.write_bytes(b"License: after\n")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(build_script.os, "open", mutate_before_reopen)

    with pytest.raises(ReleaseContractError, match="changed"):
        capture(
            [
                _raw_collect_row(
                    "_internal/alpha.conf",
                    raw_source=str(source),
                    toc_type="DATA",
                )
            ],
            {},
            _conda_build_dir(tmp_path / "packet"),
        )


def test_debian_component_evidence_preserves_empty_license_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _debian_system_root(tmp_path)
    source = _write(root / "etc/alpha.conf", b"alpha\n")
    _write(
        root / "usr/share/doc/alpha/copyright",
        b"Format: fixture\nCopyright: fixture\n",
    )
    capture = _debian_capture(monkeypatch, root)

    def fake_run(command: list[str], **options: object):
        _assert_dpkg_invocation(options)
        if command[1] == "--version":
            return subprocess.CompletedProcess(command, 0, _DPKG_VERSION_BYTES, b"")
        if command[1] == "-S":
            return subprocess.CompletedProcess(
                command, 0, b"alpha:amd64: /etc/alpha.conf\n", b""
            )
        return subprocess.CompletedProcess(command, 0, _alpha_status(), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = capture(
        [
            _raw_collect_row(
                "_internal/alpha.conf",
                raw_source=str(source),
                toc_type="DATA",
            )
        ],
        {},
        _conda_build_dir(tmp_path / "packet"),
    )

    assert result["library:alpha@1:2.0-3ubuntu1"]["license_candidates"] == []


def test_python_component_evidence_is_artifact_aligned_private_and_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "site"
    owned_source = _write(site / "demo.py", b"DEMO = 1\n")
    unowned_source = _write(tmp_path / "generated/EasyQC", b"generated\n")
    distribution = _path_distribution(
        site,
        name="demo",
        version="1.0",
        owned_module="demo.py",
    )
    monkeypatch.setattr(
        build_script.importlib_metadata,
        "distributions",
        lambda: [distribution],
    )

    first_packet = _python_capture_packet(
        tmp_path / "first/packet",
        owned_source=owned_source,
        unowned_source=unowned_source,
    )
    second_packet = _python_capture_packet(
        tmp_path / "second/packet",
        owned_source=owned_source,
        unowned_source=unowned_source,
    )
    first = build_script.capture_python_component_evidence(*first_packet)
    second = build_script.capture_python_component_evidence(*second_packet)

    assert first["schema"] == "easyqc-release-distribution-metadata-v2"
    assert first["collect_entry_count"] == 2
    assert first["component_count"] == 1
    assert first["assigned_collected_entry_count"] == 1
    assert first["unassigned_collected_entry_count"] == 1
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert str(tmp_path).encode() not in canonical_json_bytes(first)

    component = first["components"][0]
    assert component["component_id"] == "library:demo@1.0"
    assert component["provider_candidates"] == [
        {"field": "Author", "value": "Demo Provider"},
        {"field": "Author-email", "value": "qc@example.invalid"},
    ]
    assert component["license_candidates"] == {
        "classifiers": ["License :: OSI Approved :: MIT License"],
        "expression": "MIT",
        "field_first_line": None,
    }
    assert len(component["collected_files"]) == 1
    collected = component["collected_files"][0]
    assert collected["final_path"] == "_internal/demo.py"
    assert collected["distribution_path"] == "demo.py"
    assert collected["source_locator"] == "python-distribution/demo/demo.py"
    assert collected["source_identity"]["sha256"] == sha256_file(owned_source)

    license_reference = component["license_files"][0]
    retained_license = first_packet[3].parent / license_reference["retained_path"]
    assert retained_license.read_bytes() == b"Demo retained MIT license\n"
    assert license_reference["sha256"] == sha256_file(retained_license)
    assert first["unassigned_collected_entries"][0]["final_path"] == "EasyQC"


@pytest.mark.parametrize("owner_problem", ["duplicate", "ambiguous"])
def test_python_component_evidence_rejects_duplicate_or_ambiguous_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_problem: str,
) -> None:
    site = tmp_path / "site"
    owned_source = _write(site / "demo.py", b"DEMO = 1\n")
    unowned_source = _write(tmp_path / "generated/EasyQC", b"generated\n")
    first = _path_distribution(
        site,
        name="demo",
        version="1.0",
        owned_module="demo.py",
    )
    distributions = [first]
    if owner_problem == "duplicate":
        record_path = site / "demo-1.0.dist-info/RECORD"
        _write(record_path, record_path.read_bytes() + b"demo.py,,\n")
    else:
        distributions.append(
            _path_distribution(
                site,
                name="other-demo",
                version="2.0",
                owned_module="demo.py",
            )
        )
    monkeypatch.setattr(
        build_script.importlib_metadata,
        "distributions",
        lambda: distributions,
    )
    packet = _python_capture_packet(
        tmp_path / "packet",
        owned_source=owned_source,
        unowned_source=unowned_source,
    )

    with pytest.raises(
        ReleaseContractError,
        match=f"{owner_problem} Python distribution",
    ):
        build_script.capture_python_component_evidence(*packet)

    assert not (packet[3] / "distribution-metadata.json").exists()


def test_python_component_evidence_rejects_symlinked_license_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "site"
    owned_source = _write(site / "demo.py", b"DEMO = 1\n")
    unowned_source = _write(tmp_path / "generated/EasyQC", b"generated\n")
    distribution = _path_distribution(
        site,
        name="demo",
        version="1.0",
        owned_module="demo.py",
    )
    license_path = site / "demo-1.0.dist-info/LICENSE"
    outside_license = _write(tmp_path / "outside-license.txt", b"outside\n")
    license_path.unlink()
    try:
        license_path.symlink_to(outside_license)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")
    monkeypatch.setattr(
        build_script.importlib_metadata,
        "distributions",
        lambda: [distribution],
    )
    packet = _python_capture_packet(
        tmp_path / "packet",
        owned_source=owned_source,
        unowned_source=unowned_source,
    )

    with pytest.raises(ReleaseContractError, match="unsafe symlink"):
        build_script.capture_python_component_evidence(*packet)

    assert not (packet[3] / "distribution-metadata.json").exists()


def test_python_component_evidence_rejects_symlinked_license_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "site"
    owned_source = _write(site / "demo.py", b"DEMO = 1\n")
    unowned_source = _write(tmp_path / "generated/EasyQC", b"generated\n")
    distribution = _path_distribution(
        site,
        name="demo",
        version="1.0",
        owned_module="demo.py",
    )
    outside = tmp_path / "outside"
    _write(outside / "LICENSE", b"must not be retained\n")
    try:
        (site / "linked").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")
    record_path = site / "demo-1.0.dist-info/RECORD"
    _write(
        record_path,
        record_path.read_bytes().replace(
            b"demo-1.0.dist-info/LICENSE,,\n",
            b"linked/LICENSE,,\n",
        ),
    )
    monkeypatch.setattr(
        build_script.importlib_metadata,
        "distributions",
        lambda: [distribution],
    )
    packet = _python_capture_packet(
        tmp_path / "packet",
        owned_source=owned_source,
        unowned_source=unowned_source,
    )

    with pytest.raises(ReleaseContractError, match="unsafe symlink"):
        build_script.capture_python_component_evidence(*packet)

    assert not list((packet[3] / "evidence/python-distributions").rglob("LICENSE"))


def test_python_component_evidence_rejects_distribution_parent_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "site"
    owned_source = _write(site / "nested/demo.py", b"DEMO = 1\n")
    unowned_source = _write(tmp_path / "generated/EasyQC", b"generated\n")
    outside = tmp_path / "outside"
    _write(outside / "demo.py", b"DEMO = 1\n")
    distribution = _path_distribution(
        site,
        name="demo",
        version="1.0",
        owned_module="nested/demo.py",
    )
    monkeypatch.setattr(
        build_script.importlib_metadata,
        "distributions",
        lambda: [distribution],
    )
    packet = _python_capture_packet(
        tmp_path / "packet",
        owned_source=owned_source,
        unowned_source=unowned_source,
    )
    original_open = build_script.os.open
    swapped = False

    def swap_parent_before_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if path == "nested" and dir_fd is not None and not swapped:
            swapped = True
            (site / "nested").rename(site / "original-nested")
            (site / "nested").symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(build_script.os, "open", swap_parent_before_open)

    with pytest.raises(ReleaseContractError, match="unsafe symlink"):
        build_script.capture_python_component_evidence(*packet)


def test_python_component_evidence_rejects_unsupported_collect_type(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "site"
    owned_source = _write(site / "demo.py", b"DEMO = 1\n")
    unowned_source = _write(tmp_path / "generated/EasyQC", b"generated\n")
    distribution = _path_distribution(
        site,
        name="demo",
        version="1.0",
        owned_module="demo.py",
    )
    monkeypatch.setattr(
        build_script.importlib_metadata,
        "distributions",
        lambda: [distribution],
    )
    packet = _python_capture_packet(
        tmp_path / "packet",
        owned_source=owned_source,
        unowned_source=unowned_source,
        owned_toc_type="BOGUS",
    )

    with pytest.raises(ReleaseContractError, match="unsupported COLLECT TOC type"):
        build_script.capture_python_component_evidence(*packet)


def test_python_component_evidence_accepts_authenticated_empty_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "site"
    owned_source = _write(site / "demo.py", b"DEMO = 1\n")
    unowned_source = _write(tmp_path / "generated/EasyQC", b"generated\n")
    distribution = _path_distribution(
        site,
        name="demo",
        version="1.0",
        owned_module="demo.py",
    )
    monkeypatch.setattr(
        build_script.importlib_metadata,
        "distributions",
        lambda: [distribution],
    )
    packet = _python_capture_packet(
        tmp_path / "packet",
        owned_source=owned_source,
        unowned_source=unowned_source,
        warning_bytes=b"",
    )

    evidence = build_script.capture_python_component_evidence(*packet)

    assert evidence["schema"] == "easyqc-release-distribution-metadata-v2"


def _fixture_v3_collected_row(
    manifest: object,
    collect_row: dict[str, object],
    *,
    source_kind: str,
    package_path: str,
) -> dict[str, object]:
    final_path = str(collect_row["final_path"])
    entry = next(item for item in manifest.entries if item.path == final_path)
    assert entry.entry_type == "regular-file"
    return {
        "entry_type": "regular-file",
        "final_path": final_path,
        "package_path": package_path,
        "raw_source_text_sha256": collect_row["raw_source_text_sha256"],
        "source_identity": {
            "entry_type": "regular-file",
            "sha256": entry.sha256,
            "size": entry.size,
        },
        "source_kind": source_kind,
        "source_locator": f"{source_kind}/{package_path}",
        "target_final_path": None,
        "toc_type": collect_row["toc_type"],
    }


def _fixture_v3_component(
    packet_root: Path,
    manifest: object,
    collect_row: dict[str, object],
    *,
    component_id: str,
    ecosystem: str,
    name: str,
    version: str,
    source_kind: str,
    evidence_relative: str,
    provider_value: str = "authenticated fixture",
    retained_path_override: str | None = None,
) -> dict[str, object]:
    evidence_path = _write(
        packet_root / evidence_relative,
        f"evidence for {component_id}\n".encode(),
    )
    retained_path = retained_path_override or evidence_path.relative_to(
        packet_root
    ).as_posix()
    return {
        "collected_files": [
            _fixture_v3_collected_row(
                manifest,
                collect_row,
                source_kind=source_kind,
                package_path=f"packages/{name}",
            )
        ],
        "component_id": component_id,
        "ecosystem": ecosystem,
        "evidence_files": [
            {
                "kind": "fixture-metadata",
                "retained_path": retained_path,
                "sha256": sha256_file(packet_root / retained_path),
                "size": (packet_root / retained_path).stat().st_size,
                "source_locator": f"fixture-evidence/{name}",
            }
        ],
        "license_candidates": [{"field": "license", "value": "MIT"}],
        "name": name,
        "provider_candidates": [
            {"field": "provider", "value": provider_value}
        ],
        "purl": f"pkg:generic/{name}@{version}",
        "type": "library",
        "version": version,
    }


def _install_composite_v3_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    problem: str | None = None,
) -> dict[str, object]:
    packet_root = tmp_path / "packet"
    build_dir = packet_root / "build"
    (build_dir / "evidence/pyinstaller").mkdir(parents=True)
    evidence_index = _write(
        build_dir / "pyinstaller-evidence-index.json",
        b"fixture index; loader is isolated\n",
    )
    artifact = packet_root / "artifact/EasyQC-v1.0.0-linux-x86_64"
    paths = (
        "EasyQC",
        "_internal/base_library.zip",
        "_internal/demo.py",
        "_internal/libpython.so",
        "_internal/libsystem.so",
        "_internal/libxcb-cursor.so.0",
        "_internal/template/hcpall_template.scene",
    )
    for relative in paths:
        _write(artifact / relative, f"fixture {relative}\n".encode())
    alias = artifact / "_internal/demo-link.py"
    try:
        alias.symlink_to("demo.py")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")

    source_root = build_dir / "source"
    _write(source_root / "LICENSE", b"EasyQC fixture license\n")
    _write(source_root / "easyqc.spec", b"# authenticated fixture spec\n")
    cursor_library = _write(
        build_dir / "linux-cursor-sysroot/usr/lib/libxcb-cursor.so.0",
        b"verified cursor ELF\n",
    )
    reviewed_cursor_notice = _write(
        tmp_path / "reviewed-cursor-license.txt",
        b"verified cursor license\n",
    )
    cursor_notice = _write(
        build_dir / "linux-cursor-sysroot/usr/share/doc/copyright",
        reviewed_cursor_notice.read_bytes(),
    )
    _write(
        source_root / "template/hcpall_template.scene",
        (artifact / "_internal/template/hcpall_template.scene").read_bytes(),
    )
    _write(
        artifact / "_internal/libxcb-cursor.so.0",
        cursor_library.read_bytes(),
    )
    manifest = create_artifact_manifest(artifact)
    runtime_identity = _fixture_runtime_identity(tmp_path / "runtime")

    raw_sources = {
        "EasyQC": str(build_dir / "pyinstaller-work/EasyQC"),
        "_internal/base_library.zip": str(
            build_dir / "pyinstaller-work/base_library.zip"
        ),
        "_internal/demo-link.py": "demo.py",
        "_internal/demo.py": str(tmp_path / "site/demo.py"),
        "_internal/libpython.so": str(tmp_path / "conda/libpython.so"),
        "_internal/libsystem.so": "/usr/lib/libsystem.so",
        "_internal/libxcb-cursor.so.0": str(cursor_library),
        "_internal/template/hcpall_template.scene": str(
            source_root / "template/hcpall_template.scene"
        ),
    }
    collect_rows = [
        _raw_collect_row(
            final_path,
            raw_source=raw_sources[final_path],
            toc_type=(
                "SYMLINK"
                if final_path == "_internal/demo-link.py"
                else "EXECUTABLE"
                if final_path == "EasyQC"
                else "BINARY"
                if final_path.endswith(".so") or final_path.endswith(".so.0")
                else "DATA"
            ),
        )
        for final_path in sorted(raw_sources)
    ]
    collect_by_path = {str(row["final_path"]): row for row in collect_rows}
    monkeypatch.setattr(
        build_script,
        "_load_collect_rows_for_capture",
        lambda *_args, **_kwargs: collect_rows,
    )
    monkeypatch.setattr(
        build_script,
        "LINUX_CURSOR_LIBRARY_SHA256",
        sha256_file(cursor_library),
    )
    monkeypatch.setattr(
        build_script,
        "LINUX_CURSOR_NOTICE",
        reviewed_cursor_notice,
    )

    def fake_python_components(
        rows: list[dict[str, object]],
        output_build_dir: Path,
    ) -> dict[str, dict[str, object]]:
        assert rows == collect_rows
        assert Path(output_build_dir) == build_dir
        provider = str(tmp_path) if problem == "privacy" else "Python fixture"
        component = _fixture_v3_component(
            packet_root,
            manifest,
            collect_by_path["_internal/demo.py"],
            component_id="library:demo@1.0",
            ecosystem="python",
            name="demo",
            version="1.0",
            source_kind="python-distribution-file",
            evidence_relative="build/evidence/components/python/demo/METADATA",
            provider_value=provider,
        )
        return {"library:demo@1.0": component}

    def fake_conda_components(
        rows: list[dict[str, object]],
        existing: dict[str, dict[str, object]],
        output_build_dir: Path,
        authenticated_runtime: RuntimeIdentity,
    ) -> dict[str, dict[str, object]]:
        assert rows == collect_rows
        assert "library:demo@1.0" in existing
        assert Path(output_build_dir) == build_dir
        assert authenticated_runtime == runtime_identity
        component_id = (
            "library:demo@1.0"
            if problem == "duplicate-component"
            else "library:python@3.10.17"
        )
        evidence_override = (
            "build/evidence/components/python/demo/METADATA"
            if problem == "duplicate-evidence"
            else None
        )
        component = _fixture_v3_component(
            packet_root,
            manifest,
            collect_by_path["_internal/libpython.so"],
            component_id=component_id,
            ecosystem="conda",
            name="python" if problem != "duplicate-component" else "demo",
            version="3.10.17" if problem != "duplicate-component" else "1.0",
            source_kind="conda-package-file",
            evidence_relative="build/evidence/components/conda/python/metadata.json",
            retained_path_override=evidence_override,
        )
        return {component_id: component}

    def fake_debian_components(
        rows: list[dict[str, object]],
        existing: dict[str, dict[str, object]],
        output_build_dir: Path,
    ) -> dict[str, dict[str, object]]:
        assert rows == collect_rows
        assert Path(output_build_dir) == build_dir
        assert "library:python@3.10.17" in existing
        component_id = "library:system-fixture@1.0"
        return {
            component_id: _fixture_v3_component(
                packet_root,
                manifest,
                collect_by_path["_internal/libsystem.so"],
                component_id=component_id,
                ecosystem="debian",
                name="system-fixture",
                version="1.0",
                source_kind="debian-package-file",
                evidence_relative=(
                    "build/evidence/components/debian/system-fixture/copyright"
                ),
            )
        }

    monkeypatch.setattr(
        build_script,
        "_capture_python_components_v3",
        fake_python_components,
    )
    monkeypatch.setattr(
        build_script,
        "capture_conda_component_evidence",
        fake_conda_components,
    )
    monkeypatch.setattr(
        build_script,
        "capture_debian_component_evidence",
        fake_debian_components,
    )
    return {
        "artifact": artifact,
        "build_dir": build_dir,
        "cursor_runtime": build_script.VerifiedLinuxCursorRuntime(
            cursor_library,
            cursor_notice,
        ),
        "evidence_index": evidence_index,
        "manifest": manifest,
        "packet_root": packet_root,
        "runtime_identity": runtime_identity,
        "source_root": source_root,
    }


def test_python_v3_projection_is_distinct_from_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "site"
    owned_source = _write(site / "demo.py", b"DEMO = 1\n")
    unowned_source = _write(tmp_path / "generated/EasyQC", b"generated\n")
    distribution = _path_distribution(
        site,
        name="demo",
        version="1.0",
        owned_module="demo.py",
    )
    monkeypatch.setattr(
        build_script.importlib_metadata,
        "distributions",
        lambda: [distribution],
    )
    artifact, manifest, evidence_index, build_dir = _python_capture_packet(
        tmp_path / "packet",
        owned_source=owned_source,
        unowned_source=unowned_source,
    )
    collect_rows = build_script._load_collect_rows_for_capture(
        evidence_index,
        build_dir,
        manifest,
    )
    (build_dir / "evidence/components").mkdir()

    components = build_script._capture_python_components_v3(
        collect_rows,
        build_dir,
    )

    assert list(components) == ["library:demo@1.0"]
    component = components["library:demo@1.0"]
    assert component["ecosystem"] == "python"
    assert component["type"] == "library"
    assert component["collected_files"][0]["source_kind"] == (
        "python-distribution-file"
    )
    assert component["collected_files"][0]["package_path"] == "demo.py"
    assert [row["kind"] for row in component["evidence_files"]] == [
        "python-license",
        "python-metadata",
    ]
    assert all(
        str(row["retained_path"]).startswith("build/evidence/components/python/")
        for row in component["evidence_files"]
    )
    assert not (build_dir / "evidence/python-distributions").exists()
    assert artifact.is_dir()


def test_component_evidence_v3_composes_all_seven_source_kinds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _install_composite_v3_fixture(tmp_path, monkeypatch)

    metadata = build_script.capture_component_evidence_v3(
        fixture["artifact"],
        fixture["manifest"],
        fixture["evidence_index"],
        fixture["build_dir"],
        fixture["runtime_identity"],
        fixture["source_root"],
        fixture["cursor_runtime"],
        "1.0.0",
        "b" * 40,
    )

    assert metadata["schema"] == "easyqc-release-distribution-metadata-v3"
    assert metadata["component_count"] == len(metadata["components"])
    assert metadata["collect_entry_count"] == 8
    assert metadata["assigned_collected_entry_count"] == 8
    assert metadata["unassigned_collected_entry_count"] == 0
    assert metadata["unassigned_collected_entries"] == []
    assert [row["component_id"] for row in metadata["components"]] == sorted(
        row["component_id"] for row in metadata["components"]
    )
    source_kinds = {
        str(row["source_kind"])
        for component in metadata["components"]
        for row in component["collected_files"]
    }
    assert source_kinds == {
        "conda-package-file",
        "debian-package-file",
        "easyqc-source-file",
        "pyinstaller-generated",
        "python-distribution-file",
        "python-symlink-alias",
        "verified-cursor-deb",
    }
    component_inventory_module._load_composite_component_metadata(
        metadata,
        fixture["packet_root"],
    )
    assert str(tmp_path).encode() not in canonical_json_bytes(metadata)
    assert not (fixture["build_dir"] / "evidence/python-distributions").exists()


@pytest.mark.parametrize(
    ("problem", "expected_error"),
    [
        ("duplicate-component", "duplicate component"),
        (
            "duplicate-evidence",
            "duplicate.*evidence|evidence retained paths must be globally unique",
        ),
        ("privacy", "host-private|privacy"),
    ],
)
def test_component_evidence_v3_rejects_duplicate_component_evidence_and_privacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
    expected_error: str,
) -> None:
    fixture = _install_composite_v3_fixture(
        tmp_path,
        monkeypatch,
        problem=problem,
    )

    with pytest.raises(ReleaseContractError, match=expected_error):
        build_script.capture_component_evidence_v3(
            fixture["artifact"],
            fixture["manifest"],
            fixture["evidence_index"],
            fixture["build_dir"],
            fixture["runtime_identity"],
            fixture["source_root"],
            fixture["cursor_runtime"],
            "1.0.0",
            "b" * 40,
        )


@pytest.mark.parametrize(
    ("problem", "expected_error"),
    [
        ("candidate-mutation", "candidate changed"),
        ("source-mutation", "source template does not match"),
        ("unsafe-cursor-link", "escapes cursor sysroot|not a real regular file"),
        ("oversize-source", "exceeds (component evidence limit|16777216 bytes)"),
    ],
)
def test_component_evidence_v3_rejects_mutation_link_escape_and_oversize(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    problem: str,
    expected_error: str,
) -> None:
    fixture = _install_composite_v3_fixture(tmp_path, monkeypatch)
    if problem == "candidate-mutation":
        _write(Path(fixture["artifact"]) / "EasyQC", b"mutated candidate\n")
    elif problem == "source-mutation":
        _write(
            Path(fixture["source_root"]) / "template/hcpall_template.scene",
            b"mutated source template\n",
        )
    elif problem == "unsafe-cursor-link":
        copyright_path = Path(fixture["cursor_runtime"].copyright_path)
        copyright_path.unlink()
        try:
            copyright_path.symlink_to(tmp_path / "reviewed-cursor-license.txt")
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks are unavailable on this platform: {exc}")
    else:
        _write(
            Path(fixture["source_root"]) / "LICENSE",
            b"X" * (16 * 1024 * 1024 + 1),
        )

    with pytest.raises(ReleaseContractError, match=expected_error):
        build_script.capture_component_evidence_v3(
            fixture["artifact"],
            fixture["manifest"],
            fixture["evidence_index"],
            fixture["build_dir"],
            fixture["runtime_identity"],
            fixture["source_root"],
            fixture["cursor_runtime"],
            "1.0.0",
            "b" * 40,
        )


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
