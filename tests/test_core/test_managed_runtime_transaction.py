from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import sys
import venv

import pytest

from core.managed_runtime import (
    CandidateInstallRequest,
    ManagedRuntimeError,
    MaterializationRequest,
    MaterializationResult,
    install_candidate,
    launch_active,
    load_install_receipt,
    read_activation_pointer,
    rollback,
    verify_artifact_set,
)
from models.managed_runtime import (
    ActivationPointerV1,
    ReleaseManifestV1,
    RuntimeTargetV1,
)


MIB = 1024 * 1024


def _native_target() -> RuntimeTargetV1:
    if sys.platform == "win32":
        return RuntimeTargetV1("windows", "11", "x86_64")
    if sys.platform == "darwin":
        return RuntimeTargetV1("macos", "13", "arm64")
    if sys.platform.startswith("linux"):
        return RuntimeTargetV1("linux", "22.04", "x86_64")
    raise RuntimeError(
        "managed-runtime transaction fixture requires a supported host"
    )


TARGET = _native_target()
TARGET_ID = TARGET.target_id


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_artifacts(root: Path, version: str) -> ReleaseManifestV1:
    root.mkdir(parents=True)
    uv_filename = "uv.exe" if TARGET.os == "windows" else "uv"
    payloads = {
        uv_filename: f"uv-{version}".encode(),
        "python.tar.zst": f"python-{version}".encode(),
        "easyqc-source.tar.gz": f"source-{version}".encode(),
        "requirements.lock": f"lock-{version}".encode(),
        "easyqc_dep-1-py3-none-any.whl": f"wheel-{version}".encode(),
        "NOTICE.txt": f"notice-{version}".encode(),
    }
    for filename, data in payloads.items():
        (root / filename).write_bytes(data)

    def artifact(kind: str, filename: str) -> dict[str, object]:
        data = payloads[filename]
        return {
            "kind": kind,
            "filename": filename,
            "size_bytes": len(data),
            "sha256": _sha256(data),
        }

    return ReleaseManifestV1.from_json_object(
        {
            "schema_version": 1,
            "release_id": f"easyqc-{version}-{TARGET.os}",
            "channel": "candidate",
            "target": TARGET.to_json_object(),
            "easyqc": {
                "version": version,
                "source_filename": "easyqc-source.tar.gz",
                "source_sha256": _sha256(payloads["easyqc-source.tar.gz"]),
                "entrypoint": "easyqc_version.py",
            },
            "uv": {
                "version": "0.11.29",
                "filename": uv_filename,
                "size_bytes": len(payloads[uv_filename]),
                "sha256": _sha256(payloads[uv_filename]),
            },
            "python": {
                "version": "3.13.13",
                "build": "20260720",
                "key": {
                    "linux": "cpython-3.13.13-linux-x86_64-gnu",
                    "windows": "cpython-3.13.13-windows-x86_64-none",
                    "macos": "cpython-3.13.13-darwin-aarch64-none",
                }[TARGET.os],
                "filename": "python.tar.zst",
                "size_bytes": len(payloads["python.tar.zst"]),
                "sha256": _sha256(payloads["python.tar.zst"]),
            },
            "lock": {
                "filename": "requirements.lock",
                "sha256": _sha256(payloads["requirements.lock"]),
                "require_hashes": True,
            },
            "artifacts": [
                artifact("easyqc-source", "easyqc-source.tar.gz"),
                artifact("lock", "requirements.lock"),
                artifact("wheel", "easyqc_dep-1-py3-none-any.whl"),
                artifact("notice", "NOTICE.txt"),
            ],
            "sources": [],
            "notices": [
                {
                    "filename": "NOTICE.txt",
                    "sha256": _sha256(payloads["NOTICE.txt"]),
                }
            ],
            "expanded_version_bytes": 4096,
            "required_free_bytes": 256 * MIB + 4096,
            "smoke_contract_version": 1,
        }
    )


class FakeUvAdapter:
    """scaffold-simple: stdlib venv plus a deterministic version stub.

    limit=no real uv/Python-3.13/native launcher; trigger=FT-R2 pass;
    follow-up=managed-runtime architecture section 14 items 3-7.
    """

    def __init__(self, *, fail_smoke: bool = False) -> None:
        self.fail_smoke = fail_smoke
        self.calls: list[MaterializationRequest] = []

    def materialize(self, request: MaterializationRequest) -> MaterializationResult:
        self.calls.append(request)
        environment = request.version_root / "env"
        if request.manifest.target.os == "macos":
            python = environment / "bin" / "python"
            python.parent.mkdir(parents=True)
            native_python = shlex.quote(str(Path(sys.executable).resolve()))
            python.write_text(
                f'#!/bin/sh\nexec {native_python} "$@"\n',
                encoding="utf-8",
            )
            python.chmod(0o755)
        else:
            venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
            python = (
                environment / "Scripts" / "python.exe"
                if request.manifest.target.os == "windows"
                else environment / "bin" / "python"
            )
        app = request.version_root / "app"
        app.mkdir()
        entrypoint = app / request.manifest.easyqc.entrypoint
        if self.fail_smoke:
            entrypoint.write_text("raise SystemExit(23)\n", encoding="utf-8")
        else:
            entrypoint.write_text(
                f"print({request.manifest.easyqc.version!r})\n", encoding="utf-8"
            )
        return MaterializationResult(
            python_executable=python,
            entrypoint=entrypoint,
        )


class EscapingAdapter:
    def __init__(self, outside: Path) -> None:
        self.outside = outside

    def materialize(self, request: MaterializationRequest) -> MaterializationResult:
        return MaterializationResult(
            python_executable=self.outside,
            entrypoint=self.outside,
        )


def _request(
    install_root: Path,
    manifest: ReleaseManifestV1,
    artifact_root: Path,
) -> CandidateInstallRequest:
    return CandidateInstallRequest(
        install_root=install_root,
        manifest=manifest,
        artifact_root=artifact_root,
        expected_target_id=TARGET_ID,
        smoke_timeout_seconds=20.0,
    )


@pytest.mark.parametrize("failure", ["missing", "size", "hash"])
def test_artifacts_fail_before_staging(failure: str, tmp_path: Path) -> None:
    artifact_root = tmp_path / "payload"
    manifest = _write_artifacts(artifact_root, "1.0.0")
    wheel = artifact_root / "easyqc_dep-1-py3-none-any.whl"
    if failure == "missing":
        wheel.unlink()
    elif failure == "size":
        wheel.write_bytes(b"different-size")
    else:
        original_size = wheel.stat().st_size
        wheel.write_bytes(b"x" * original_size)

    install_root = tmp_path / "安装 root with spaces"
    with pytest.raises(ManagedRuntimeError, match=failure):
        install_candidate(_request(install_root, manifest, artifact_root), FakeUvAdapter())

    assert not (install_root / "versions").exists()


def test_artifact_verification_rejects_symlink_and_wrong_target(tmp_path: Path) -> None:
    artifact_root = tmp_path / "payload"
    manifest = _write_artifacts(artifact_root, "1.0.0")
    wheel = artifact_root / "easyqc_dep-1-py3-none-any.whl"
    target = tmp_path / "elsewhere.whl"
    target.write_bytes(wheel.read_bytes())
    wheel.unlink()
    try:
        wheel.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ManagedRuntimeError, match="symlink"):
        verify_artifact_set(manifest, artifact_root, expected_target_id=TARGET_ID)

    with pytest.raises(ManagedRuntimeError, match="target"):
        verify_artifact_set(
            manifest,
            artifact_root,
            expected_target_id=(
                "ubuntu-24.04-x86_64"
                if TARGET.os == "linux"
                else "ubuntu-22.04-x86_64"
            ),
        )


def test_install_failure_update_launch_and_rollback_transaction(tmp_path: Path) -> None:
    install_root = tmp_path / "安装 root with spaces"
    artifacts_v1 = tmp_path / "payload one"
    manifest_v1 = _write_artifacts(artifacts_v1, "1.0.0")

    receipt_v1 = install_candidate(
        _request(install_root, manifest_v1, artifacts_v1), FakeUvAdapter()
    )
    pointer_v1 = read_activation_pointer(install_root)
    assert pointer_v1 == ActivationPointerV1(manifest_v1.release_id, None, 1)
    assert receipt_v1.smoke.passed is True
    assert (install_root / "versions" / manifest_v1.release_id / "smoke-report.json").is_file()
    assert (install_root / "versions" / manifest_v1.release_id / "install-receipt.json").is_file()
    assert launch_active(install_root, ("--version",)).stdout.strip() == "1.0.0"

    pointer_path = install_root / "state" / "activation.txt"
    original_pointer_bytes = pointer_path.read_bytes()
    artifacts_v2 = tmp_path / "payload two"
    manifest_v2 = _write_artifacts(artifacts_v2, "2.0.0")
    with pytest.raises(ManagedRuntimeError, match="smoke"):
        install_candidate(
            _request(install_root, manifest_v2, artifacts_v2),
            FakeUvAdapter(fail_smoke=True),
        )

    assert pointer_path.read_bytes() == original_pointer_bytes
    assert not (
        install_root / "versions" / manifest_v2.release_id / "install-receipt.json"
    ).exists()
    failed_stages = list(
        (install_root / "versions").glob(f".staging-{manifest_v2.release_id}-*")
    )
    assert len(failed_stages) == 1
    assert not (failed_stages[0] / "install-receipt.json").exists()

    receipt_v2 = install_candidate(
        _request(install_root, manifest_v2, artifacts_v2), FakeUvAdapter()
    )
    pointer_v2 = read_activation_pointer(install_root)
    assert pointer_v2 == ActivationPointerV1(
        manifest_v2.release_id, manifest_v1.release_id, 2
    )
    assert receipt_v2.release_id == manifest_v2.release_id
    assert launch_active(install_root, ("--version",)).stdout.strip() == "2.0.0"

    rolled_back = rollback(install_root)
    assert rolled_back == ActivationPointerV1(
        manifest_v1.release_id, manifest_v2.release_id, 3
    )
    assert launch_active(install_root, ("--version",)).stdout.strip() == "1.0.0"


def test_malformed_pointer_unknown_receipt_and_collision_fail_loud(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "安装 root with spaces"
    artifact_root = tmp_path / "payload"
    manifest = _write_artifacts(artifact_root, "1.0.0")
    install_candidate(_request(install_root, manifest, artifact_root), FakeUvAdapter())
    pointer_path = install_root / "state" / "activation.txt"

    pointer_path.write_bytes(b"../escape\n-\n1\n")
    with pytest.raises(ManagedRuntimeError, match="activation pointer"):
        launch_active(install_root, ())

    pointer_path.write_bytes(ActivationPointerV1("unknown", None, 2).to_bytes())
    with pytest.raises(ManagedRuntimeError, match="receipt"):
        launch_active(install_root, ())

    pointer_path.write_bytes(ActivationPointerV1(manifest.release_id, None, 1).to_bytes())
    original = pointer_path.read_bytes()
    with pytest.raises(ManagedRuntimeError, match="immutable version collision"):
        install_candidate(_request(install_root, manifest, artifact_root), FakeUvAdapter())
    assert pointer_path.read_bytes() == original


def test_adapter_paths_must_stay_inside_staging_root(tmp_path: Path) -> None:
    install_root = tmp_path / "安装 root with spaces"
    artifact_root = tmp_path / "payload"
    manifest = _write_artifacts(artifact_root, "1.0.0")
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    with pytest.raises(ManagedRuntimeError, match="adapter.*outside"):
        install_candidate(
            _request(install_root, manifest, artifact_root),
            EscapingAdapter(outside),
        )
    assert not (install_root / "state" / "activation.txt").exists()


def test_launch_revalidates_stored_manifest_and_receipt(tmp_path: Path) -> None:
    install_root = tmp_path / "安装 root with spaces"
    artifact_root = tmp_path / "payload"
    manifest = _write_artifacts(artifact_root, "1.0.0")
    install_candidate(_request(install_root, manifest, artifact_root), FakeUvAdapter())
    version_root = install_root / "versions" / manifest.release_id

    stored_manifest = version_root / "release-manifest.json"
    stored_manifest.write_bytes(stored_manifest.read_bytes() + b" ")
    with pytest.raises(ManagedRuntimeError, match="manifest"):
        launch_active(install_root, ())

    stored_manifest.write_bytes(manifest.canonical_bytes)
    receipt = load_install_receipt(install_root, manifest.release_id)
    tampered = receipt.to_json_object()
    tampered["source_sha256"] = "0" * 64
    (version_root / "install-receipt.json").write_text(
        json.dumps(
            tampered,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ManagedRuntimeError, match="receipt"):
        launch_active(install_root, ())
