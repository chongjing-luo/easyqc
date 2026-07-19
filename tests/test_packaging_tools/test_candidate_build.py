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
    def fake_python_component_evidence(
        artifact,
        manifest,
        evidence_index_path,
        build_dir,
    ):
        events.append("python-component-evidence")
        assert Path(artifact).is_dir()
        assert manifest.candidate_id == create_artifact_manifest(artifact).candidate_id
        assert Path(evidence_index_path).is_file()
        assert Path(build_dir).name == "build"
        return {
            "schema": "easyqc-release-distribution-metadata-v2",
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
        "capture_python_component_evidence",
        fake_python_component_evidence,
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
    assert events == ["pyinstaller", "manifest", "python-component-evidence"]

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
        "easyqc-release-distribution-metadata-v2"
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
