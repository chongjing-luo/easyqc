from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys

import pytest

from core.managed_runtime import (
    ManagedRuntimeError,
    MaterializationRequest,
    MaterializationResult,
)
from core.managed_runtime_cli import (
    InstallerContext,
    load_trusted_manifest,
    main,
    parse_installer_arguments,
)
from core.managed_runtime_platform import (
    HostPreflightSnapshot,
    ManagedRuntimePaths,
    resolve_runtime_paths,
)
from core.managed_runtime_transaction import RuntimeSmokeCommandResult
from models.managed_runtime import ReleaseManifestV1, RuntimeTargetV1


MIB = 1024 * 1024


def _native_target() -> RuntimeTargetV1:
    if sys.platform == "win32":
        return RuntimeTargetV1("windows", "11", "x86_64")
    if sys.platform == "darwin":
        return RuntimeTargetV1("macos", "13", "arm64")
    if sys.platform.startswith("linux"):
        return RuntimeTargetV1("linux", "22.04", "x86_64")
    raise RuntimeError("managed-runtime CLI fixture requires a supported host")


TARGET = _native_target()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _payload(
    root: Path,
    version: str,
    *,
    channel: str = "stable",
) -> ReleaseManifestV1:
    root.mkdir(parents=True)
    uv_filename = "uv.exe" if TARGET.os == "windows" else "uv"
    files = {
        uv_filename: b"uv",
        "python.tar.gz": b"python",
        "easyqc-source.tar.gz": f"source-{version}".encode(),
        "requirements.lock": b"lock",
        "dependency.whl": b"wheel",
    }
    for filename, data in files.items():
        (root / filename).write_bytes(data)

    def artifact(kind: str, filename: str) -> dict[str, object]:
        data = files[filename]
        return {
            "kind": kind,
            "filename": filename,
            "size_bytes": len(data),
            "sha256": _sha256(data),
        }

    manifest = ReleaseManifestV1.from_json_object(
        {
            "schema_version": 1,
            "release_id": f"easyqc-{version}-stable-{TARGET.os}",
            "channel": channel,
            "target": TARGET.to_json_object(),
            "easyqc": {
                "version": version,
                "source_filename": "easyqc-source.tar.gz",
                "source_sha256": _sha256(files["easyqc-source.tar.gz"]),
                "entrypoint": "easyqc.py",
            },
            "uv": {
                "version": "0.11.29",
                "filename": uv_filename,
                "size_bytes": len(files[uv_filename]),
                "sha256": _sha256(files[uv_filename]),
            },
            "python": {
                "version": "3.13.13",
                "build": "20260720",
                "key": {
                    "linux": "cpython-3.13.13-linux-x86_64-gnu",
                    "windows": "cpython-3.13.13-windows-x86_64-none",
                    "macos": "cpython-3.13.13-darwin-aarch64-none",
                }[TARGET.os],
                "filename": "python.tar.gz",
                "size_bytes": len(files["python.tar.gz"]),
                "sha256": _sha256(files["python.tar.gz"]),
            },
            "lock": {
                "filename": "requirements.lock",
                "sha256": _sha256(files["requirements.lock"]),
                "require_hashes": True,
            },
            "artifacts": [
                artifact("easyqc-source", "easyqc-source.tar.gz"),
                artifact("lock", "requirements.lock"),
                artifact("wheel", "dependency.whl"),
            ],
            "sources": [],
            "notices": [],
            "expanded_version_bytes": 4096,
            "required_free_bytes": 256 * MIB + 4096,
            "smoke_contract_version": 1,
        }
    )
    (root / "release-manifest.json").write_bytes(manifest.canonical_bytes)
    return manifest


class FakeStableAdapter:
    def materialize(self, request: MaterializationRequest) -> MaterializationResult:
        if request.manifest.target.os == "windows":
            environment_python = (
                request.version_root / "env" / "Scripts" / "python.exe"
            )
            environment_python.parent.mkdir(parents=True)
            shutil.copy2(Path(sys.executable).resolve(), environment_python)
        else:
            runtime_python = request.version_root / "runtime/python"
            runtime_python.parent.mkdir()
            shutil.copy2(Path(sys.executable).resolve(), runtime_python)
            environment_python = request.version_root / "env/bin/python"
            environment_python.parent.mkdir(parents=True)
            environment_python.symlink_to(runtime_python)
        app_root = request.version_root / "app"
        app_root.mkdir()
        entrypoint = app_root / "easyqc.py"
        entrypoint.write_text(
            "import sys\n"
            f"VERSION = {request.manifest.easyqc.version!r}\n"
            "print(VERSION if '--version' in sys.argv else 'APP')\n",
            encoding="utf-8",
        )
        (app_root / "easyqc_install.py").write_text(
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        return MaterializationResult(environment_python, entrypoint)


class QueueSmoke:
    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = outcomes

    def run(
        self,
        python_executable: Path,
        entrypoint: Path,
        expected_version: str,
        timeout_seconds: float,
    ) -> tuple[RuntimeSmokeCommandResult, ...]:
        passed = self.outcomes.pop(0)
        code = 0 if passed else 31
        return (
            RuntimeSmokeCommandResult(
                "version",
                (str(python_executable), str(entrypoint), "--version"),
                code,
                f"{expected_version}\n" if passed else "",
                "" if passed else "injected smoke failure",
            ),
            RuntimeSmokeCommandResult(
                "qt-offscreen",
                (str(python_executable), "-c", "<fixed-qt-smoke>"),
                code,
                "QT_OFFSCREEN_OK 6.11.1\n" if passed else "",
                "" if passed else "injected smoke failure",
            ),
        )


class MemoryWindowsPathAdapter:
    def __init__(self) -> None:
        self.value = ""

    def read_path(self, scope: str) -> str:
        assert scope in {"user", "system"}
        return self.value

    def write_path(self, scope: str, value: str) -> None:
        assert scope in {"user", "system"}
        self.value = value


def _passing_snapshot() -> HostPreflightSnapshot:
    if TARGET.os == "windows":
        return HostPreflightSnapshot(
            os_name="windows",
            os_version="11",
            distribution_id=None,
            arch="x86_64",
            available_free_bytes=2 * 1024 * MIB,
            root_writable=True,
            privileged=False,
            root_is_safe=True,
            available_native_libraries=(),
        )
    if TARGET.os == "macos":
        return HostPreflightSnapshot(
            os_name="macos",
            os_version="13",
            distribution_id=None,
            arch="arm64",
            available_free_bytes=2 * 1024 * MIB,
            root_writable=True,
            privileged=False,
            root_is_safe=True,
            available_native_libraries=(),
        )
    return HostPreflightSnapshot(
        os_name="linux",
        os_version="22.04",
        distribution_id="ubuntu",
        arch="x86_64",
        available_free_bytes=2 * 1024 * MIB,
        root_writable=True,
        privileged=False,
        root_is_safe=True,
        available_native_libraries=("libxcb-cursor.so.0",),
    )


def _context(
    install_root: Path,
    exposure_root: Path,
    smoke: QueueSmoke,
    *,
    snapshot: HostPreflightSnapshot | None = None,
    scope: str = "user",
) -> InstallerContext:
    windows_path_adapter = (
        MemoryWindowsPathAdapter() if TARGET.os == "windows" else None
    )

    def paths(target: RuntimeTargetV1, requested_scope: str) -> ManagedRuntimePaths:
        assert target == TARGET
        assert requested_scope == scope
        if requested_scope == "user":
            return resolve_runtime_paths(
                target,
                requested_scope,
                user_data_root=str(install_root),
            )
        return ManagedRuntimePaths(
            target=target,
            scope=requested_scope,
            install_root=str(install_root),
            state_root=str(install_root / "state"),
            transaction_lock=str(install_root / "state/transaction.lock"),
        )

    return InstallerContext(
        target_detector=lambda: TARGET,
        path_resolver=paths,
        snapshot_provider=lambda _paths: snapshot or _passing_snapshot(),
        adapter=FakeStableAdapter(),
        smoke_runner=smoke,
        posix_exposure_root=exposure_root,
        environment_path=f"/usr/bin:{exposure_root}",
        windows_path_adapter=windows_path_adapter,
    )


def _run(
    argv: list[str],
    context: InstallerContext,
) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(argv, context=context, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _assert_success(
    argv: list[str],
    context: InstallerContext,
) -> tuple[int, str, str]:
    result = _run(argv, context)
    assert result[0] == 0, result[2]
    return result


def _mutation_args(
    command: str,
    payload: Path,
    manifest: ReleaseManifestV1,
) -> list[str]:
    return [
        command,
        "--manifest",
        str(payload / "release-manifest.json"),
        "--manifest-sha256",
        manifest.sha256,
        "--offline-payload",
        str(payload),
    ]


def test_trusted_stable_install_and_status_json_are_receipt_backed(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload-v1"
    manifest = _payload(payload, "1.0.0")
    install_root = tmp_path / "安装 root"
    exposure = tmp_path / "用户 bin"
    context = _context(install_root, exposure, QueueSmoke([True]))

    code, stdout, stderr = _run(
        _mutation_args("install", payload, manifest),
        context,
    )

    assert code == 0, stderr
    assert "install complete" in stdout
    assert stderr == ""
    code, stdout, stderr = _run(["status", "--json"], context)
    status = json.loads(stdout)
    assert code == 0
    assert stderr == ""
    assert status == {
        "schema_version": 1,
        "installation_state": "ready",
        "installed": True,
        "target_id": TARGET.target_id,
        "scope": "user",
        "install_root": str(install_root),
        "launcher_ready": True,
        "path_contains_exposure": True,
        "launcher_issues": [],
        "active_release_id": manifest.release_id,
        "previous_release_id": None,
        "generation": 1,
        "valid_release_ids": [manifest.release_id],
        "incomplete_release_ids": [],
        "retained_failure_names": [],
    }


def test_failed_update_preserves_pointer_then_repair_update_and_rollback_work(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "install"
    exposure = tmp_path / "bin"
    payload_v1 = tmp_path / "payload-v1"
    payload_v2 = tmp_path / "payload-v2"
    payload_v3 = tmp_path / "payload-v3"
    manifest_v1 = _payload(payload_v1, "1.0.0")
    manifest_v2 = _payload(payload_v2, "2.0.0")
    manifest_v3 = _payload(payload_v3, "3.0.0")
    context = _context(
        install_root,
        exposure,
        QueueSmoke([True, False, True, True]),
    )
    _assert_success(
        _mutation_args("install", payload_v1, manifest_v1),
        context,
    )
    pointer_path = install_root / "state/activation.txt"
    original_pointer = pointer_path.read_bytes()

    code, _stdout, stderr = _run(
        _mutation_args("update", payload_v2, manifest_v2),
        context,
    )

    assert code == 1
    assert "smoke" in stderr
    assert pointer_path.read_bytes() == original_pointer
    _assert_success(
        _mutation_args("repair", payload_v2, manifest_v2),
        context,
    )
    _assert_success(
        _mutation_args("update", payload_v3, manifest_v3),
        context,
    )
    _assert_success(["rollback"], context)
    status = json.loads(_run(["status", "--json"], context)[1])
    assert status["active_release_id"] == manifest_v2.release_id
    assert status["previous_release_id"] == manifest_v3.release_id
    assert status["generation"] == 4
    assert status["retained_failure_names"]


def test_bad_hash_and_candidate_are_rejected_before_install_or_exposure_writes(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "install"
    exposure = tmp_path / "bin"
    stable_payload = tmp_path / "stable"
    candidate_payload = tmp_path / "candidate"
    stable = _payload(stable_payload, "1.0.0")
    candidate = _payload(candidate_payload, "2.0.0", channel="candidate")
    context = _context(install_root, exposure, QueueSmoke([]))
    bad_hash_args = _mutation_args("install", stable_payload, stable)
    bad_hash_args[bad_hash_args.index(stable.sha256)] = "0" * 64

    code, _stdout, stderr = _run(bad_hash_args, context)
    assert code == 1
    assert "trusted manifest SHA-256" in stderr
    assert not install_root.exists()
    assert not exposure.exists()

    code, _stdout, stderr = _run(
        _mutation_args("install", candidate_payload, candidate),
        context,
    )
    assert code == 1
    assert "stable" in stderr
    assert not install_root.exists()
    assert not exposure.exists()


def test_system_permission_failure_never_falls_back_or_writes(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    manifest = _payload(payload, "1.0.0")
    install_root = tmp_path / "system-root"
    exposure = tmp_path / "system-bin"
    snapshot = replace(
        _passing_snapshot(),
        root_writable=False,
        privileged=False,
    )
    context = _context(
        install_root,
        exposure,
        QueueSmoke([]),
        snapshot=snapshot,
        scope="system",
    )
    args = [*_mutation_args("install", payload, manifest), "--scope", "system"]

    code, _stdout, stderr = _run(args, context)

    assert code == 1
    assert "SCOPE_PERMISSION" in stderr
    assert not install_root.exists()
    assert not exposure.exists()


def test_not_installed_status_is_read_only_and_parser_errors_exit_two(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "absent"
    exposure = tmp_path / "bin"
    context = _context(install_root, exposure, QueueSmoke([]))

    code, stdout, stderr = _run(["status", "--json"], context)

    assert code == 0
    assert stderr == ""
    assert json.loads(stdout)["installation_state"] == "not-installed"
    assert not install_root.exists()
    assert not exposure.exists()
    with pytest.raises(SystemExit) as no_command:
        parse_installer_arguments([])
    assert no_command.value.code == 2
    with pytest.raises(SystemExit) as missing_hash:
        parse_installer_arguments(["install", "--manifest", "manifest.json"])
    assert missing_hash.value.code == 2


def test_trusted_manifest_loader_rejects_symlink_and_noncanonical_bytes(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    manifest = _payload(payload, "1.0.0")
    link = tmp_path / "manifest-link.json"
    link.symlink_to(payload / "release-manifest.json")
    with pytest.raises(ManagedRuntimeError, match="regular non-symlink"):
        load_trusted_manifest(link, manifest.sha256)

    noncanonical = tmp_path / "noncanonical.json"
    noncanonical.write_text(
        json.dumps(manifest.to_json_object(), indent=2),
        encoding="utf-8",
    )
    digest = _sha256(noncanonical.read_bytes())
    with pytest.raises(ManagedRuntimeError, match="canonical"):
        load_trusted_manifest(noncanonical, digest)
