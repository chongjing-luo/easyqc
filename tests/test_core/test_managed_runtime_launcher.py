from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

import core.managed_runtime_launcher as launcher_module
from core.managed_runtime import (
    ManagedRuntimeError,
    MaterializationRequest,
    MaterializationResult,
    verify_artifact_set,
)
from core.managed_runtime_launcher import (
    install_stable_launchers,
    inspect_stable_launchers,
    next_windows_path,
    render_launcher_artifacts,
)
from core.managed_runtime_platform import HostPreflightSnapshot, resolve_runtime_paths
from core.managed_runtime_transaction import (
    RuntimeSmokeCommandResult,
    RuntimeTransactionController,
    RuntimeTransactionRequest,
)
from models.managed_runtime import ReleaseManifestV1, RuntimeTargetV1


MIB = 1024 * 1024
SOURCE_REVISION = "a" * 40
POSIX_INTEGRATION = pytest.mark.skipif(
    sys.platform == "win32",
    reason="stable POSIX launcher integration requires POSIX scripts and symlinks",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _native_posix_target() -> RuntimeTargetV1:
    if sys.platform == "darwin":
        return RuntimeTargetV1("macos", "13", "arm64")
    if sys.platform.startswith("linux"):
        return RuntimeTargetV1("linux", "22.04", "x86_64")
    raise RuntimeError("POSIX launcher fixture requires Linux or macOS")


def _manifest(payload: Path, version: str = "1.0.0") -> ReleaseManifestV1:
    payload.mkdir(parents=True)
    target = _native_posix_target()
    files = {
        "uv": b"uv",
        "python.tar.gz": b"python",
        "easyqc-source.tar.gz": b"source",
        "requirements.lock": b"lock",
        "dependency.whl": b"wheel",
    }
    for name, data in files.items():
        (payload / name).write_bytes(data)

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
            "release_id": f"easyqc-{version}-stable-{target.os}",
            "channel": "stable",
            "target": target.to_json_object(),
            "easyqc": {
                "version": version,
                "source_filename": "easyqc-source.tar.gz",
                "source_sha256": _sha256(files["easyqc-source.tar.gz"]),
                "entrypoint": "easyqc.py",
            },
            "uv": {
                "version": "0.11.29",
                "filename": "uv",
                "size_bytes": len(files["uv"]),
                "sha256": _sha256(files["uv"]),
            },
            "python": {
                "version": "3.13.13",
                "build": "20260720",
                "key": (
                    "cpython-3.13.13-darwin-aarch64-none"
                    if target.os == "macos"
                    else "cpython-3.13.13-linux-x86_64-gnu"
                ),
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
    (payload / "release-manifest.json").write_bytes(manifest.canonical_bytes)
    return manifest


def _passing_snapshot(target: RuntimeTargetV1) -> HostPreflightSnapshot:
    if target.os == "macos":
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


class StableFakeAdapter:
    def materialize(self, request: MaterializationRequest) -> MaterializationResult:
        runtime_python = request.version_root / "runtime" / "python"
        runtime_python.parent.mkdir()
        if sys.platform == "darwin":
            native_python = shlex.quote(str(Path(sys.executable).resolve()))
            runtime_python.write_text(
                f'#!/bin/sh\nexec {native_python} "$@"\n',
                encoding="utf-8",
            )
            runtime_python.chmod(0o755)
        else:
            shutil.copy2(Path(sys.executable).resolve(), runtime_python)
        environment_python = request.version_root / "env" / "bin" / "python"
        environment_python.parent.mkdir(parents=True)
        environment_python.symlink_to(runtime_python)
        app = request.version_root / "app"
        app.mkdir()
        entrypoint = app / "easyqc.py"
        entrypoint.write_text(
            "import json, sys\n"
            f"VERSION = {request.manifest.easyqc.version!r}\n"
            "if '--exit-23' in sys.argv:\n"
            "    raise SystemExit(23)\n"
            "if '--version' in sys.argv:\n"
            "    print(VERSION)\n"
            "else:\n"
            "    print(json.dumps(sys.argv[1:], ensure_ascii=False))\n",
            encoding="utf-8",
        )
        (app / "easyqc_install.py").write_text(
            "import json, sys\n"
            "print('INSTALLER ' + json.dumps(sys.argv[1:], ensure_ascii=False))\n",
            encoding="utf-8",
        )
        return MaterializationResult(environment_python, entrypoint)


class PassingSmoke:
    def run(
        self,
        python_executable: Path,
        entrypoint: Path,
        expected_version: str,
        timeout_seconds: float,
    ) -> tuple[RuntimeSmokeCommandResult, ...]:
        return (
            RuntimeSmokeCommandResult(
                "version",
                (str(python_executable), str(entrypoint), "--version"),
                0,
                f"{expected_version}\n",
                "",
            ),
            RuntimeSmokeCommandResult(
                "qt-offscreen",
                (str(python_executable), "-c", "<fixed-qt-smoke>"),
                0,
                "QT_OFFSCREEN_OK 6.11.1\n",
                "",
            ),
        )


def _installed_runtime(tmp_path: Path):
    root = tmp_path / "测试 root's folder"
    exposure = tmp_path / "用户 bin"
    payload = tmp_path / "payload"
    manifest = _manifest(payload)
    paths = resolve_runtime_paths(
        manifest.target,
        "user",
        user_data_root=str(root),
    )
    launchers = install_stable_launchers(
        paths,
        posix_exposure_root=exposure,
        environment_path=f"/usr/bin:{exposure}",
    )
    verified = verify_artifact_set(
        manifest,
        payload,
        expected_target_id=manifest.target.target_id,
    )
    controller = RuntimeTransactionController(
        adapter=StableFakeAdapter(),
        smoke_runner=PassingSmoke(),
        transaction_id_factory=lambda: "1" * 32,
    )
    controller.install(
        RuntimeTransactionRequest(
            paths=paths,
            manifest=manifest,
            verified_artifacts=verified,
            source_revision=SOURCE_REVISION,
            preflight_snapshot=_passing_snapshot(manifest.target),
        )
    )
    return paths, launchers


@POSIX_INTEGRATION
def test_posix_stable_launchers_run_in_fresh_shell_and_preserve_argv(
    tmp_path: Path,
) -> None:
    paths, launchers = _installed_runtime(tmp_path)
    dangerous = "$(touch SHOULD_NOT_EXIST); value"
    environment = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }

    product = subprocess.run(
        [str(launchers.exposed_product), "中文 空格", dangerous],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    installer = subprocess.run(
        [str(launchers.exposed_installer), "status", "--json"],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert product.returncode == 0
    assert json.loads(product.stdout) == ["中文 空格", dangerous]
    assert installer.returncode == 0
    assert installer.stdout.startswith("INSTALLER ")
    assert json.loads(installer.stdout.removeprefix("INSTALLER ")) == [
        "status",
        "--json",
    ]
    child_failure = subprocess.run(
        [str(launchers.exposed_product), "--exit-23"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert child_failure.returncode == 23
    assert not (Path(paths.install_root) / "SHOULD_NOT_EXIST").exists()
    inspection = inspect_stable_launchers(
        paths,
        posix_exposure_root=launchers.exposure_root,
    )
    assert inspection.ready is True
    assert inspection.issues == ()
    scope_state = json.loads(
        (Path(paths.state_root) / "scope-state.json").read_text(encoding="utf-8")
    )
    assert scope_state["path_contains_exposure"] is True


@POSIX_INTEGRATION
def test_posix_launcher_rejects_python_escape_and_never_overwrites_collision(
    tmp_path: Path,
) -> None:
    paths, launchers = _installed_runtime(tmp_path)
    pointer = (Path(paths.state_root) / "activation.txt").read_text(
        encoding="ascii"
    )
    release_id = pointer.splitlines()[0]
    python_path = (
        Path(paths.install_root) / "versions" / release_id / "env/bin/python"
    )
    python_path.unlink()
    python_path.symlink_to(Path("/usr/bin/python3"))

    escaped = subprocess.run(
        [str(launchers.product)],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert escaped.returncode != 0
    assert "version root" in escaped.stderr

    other_root = tmp_path / "other"
    payload = tmp_path / "other-payload"
    manifest = _manifest(payload)
    other_paths = resolve_runtime_paths(
        manifest.target,
        "user",
        user_data_root=str(other_root),
    )
    collision_root = tmp_path / "collision-bin"
    collision_root.mkdir()
    owned_by_user = collision_root / "easyqc"
    owned_by_user.write_bytes(b"USER OWNED\n")
    with pytest.raises(ManagedRuntimeError, match="collision"):
        install_stable_launchers(
            other_paths,
            posix_exposure_root=collision_root,
            environment_path=str(collision_root),
        )
    assert owned_by_user.read_bytes() == b"USER OWNED\n"
    assert not (Path(other_paths.state_root) / "scope-state.json").exists()


@POSIX_INTEGRATION
def test_posix_launcher_rejects_malformed_pointer_and_symlinked_authority(
    tmp_path: Path,
) -> None:
    paths, launchers = _installed_runtime(tmp_path)
    root = Path(paths.install_root)
    pointer_path = Path(paths.state_root) / "activation.txt"
    pointer_bytes = pointer_path.read_bytes()
    release_id = pointer_bytes.splitlines()[0].decode("ascii")
    version_root = root / "versions" / release_id

    pointer_path.write_bytes(pointer_bytes + b"extra\n")
    malformed = subprocess.run(
        [str(launchers.product)],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert malformed.returncode != 0
    assert "pointer is malformed" in malformed.stderr
    pointer_path.write_bytes(pointer_bytes)

    receipt = version_root / "install-receipt.json"
    receipt_backup = version_root / "receipt-backup.json"
    receipt.rename(receipt_backup)
    receipt.symlink_to(receipt_backup.name)
    symlinked_receipt = subprocess.run(
        [str(launchers.product)],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert symlinked_receipt.returncode != 0
    assert "receipt" in symlinked_receipt.stderr
    receipt.unlink()
    receipt_backup.rename(receipt)

    entrypoint = version_root / "app/easyqc.py"
    entrypoint_backup = version_root / "app/easyqc-real.py"
    entrypoint.rename(entrypoint_backup)
    entrypoint.symlink_to(entrypoint_backup.name)
    symlinked_entrypoint = subprocess.run(
        [str(launchers.product)],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert symlinked_entrypoint.returncode != 0
    assert "entrypoint" in symlinked_entrypoint.stderr


def test_windows_launcher_contract_and_path_transition_are_deterministic() -> None:
    paths = resolve_runtime_paths(
        RuntimeTargetV1("windows", "11", "x86_64"),
        "system",
        environment={"ProgramFiles": r"C:\Program Files"},
    )

    artifacts = render_launcher_artifacts(paths)

    assert [item.relative_path.as_posix() for item in artifacts] == [
        "bin/easyqc.cmd",
        "bin/easyqc-install.cmd",
        "bootstrap/controller/easyqc-launch.ps1",
    ]
    expected_product = (
        "@echo off\r\n"
        "setlocal\r\n"
        'set "EASYQC_ROOT=C:\\Program Files\\EasyQC"\r\n'
        "powershell.exe -NoLogo -NoProfile -NonInteractive "
        '-ExecutionPolicy Bypass -File "%EASYQC_ROOT%\\bootstrap\\controller'
        '\\easyqc-launch.ps1" product %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    ).encode("utf-8")
    expected_installer = expected_product.replace(b" product %*", b" installer %*")
    assert artifacts[0].content == expected_product
    assert artifacts[1].content == expected_installer
    assert artifacts[2].content.startswith(b"param(\r\n")
    assert artifacts[2].content.endswith(b"exit $LASTEXITCODE\r\n")
    assert b"Resolve-Path -LiteralPath" in artifacts[2].content
    assert b"easyqc_install.py" in artifacts[2].content
    assert all(b"\n" not in item.content.replace(b"\r\n", b"") for item in artifacts)

    update = next_windows_path(
        r"C:\Tools;C:\Windows",
        r"C:\Program Files\EasyQC\bin",
    )
    assert update.previous == r"C:\Tools;C:\Windows"
    assert update.current == (
        r"C:\Tools;C:\Windows;C:\Program Files\EasyQC\bin"
    )
    assert update.changed is True
    repeated = next_windows_path(update.current, r"c:\program files\easyqc\BIN")
    assert repeated.current == update.current
    assert repeated.changed is False


def test_new_file_writer_preserves_bytes_when_windows_text_mode_is_simulated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary_flag = 1 << 29
    binary_descriptors: set[int] = set()
    original_open = launcher_module.os.open
    original_write = launcher_module.os.write

    def simulated_windows_open(
        path: str | bytes | Path,
        flags: int,
        mode: int = 0o777,
    ) -> int:
        binary = bool(flags & binary_flag)
        descriptor = original_open(path, flags & ~binary_flag, mode)
        if binary:
            binary_descriptors.add(descriptor)
        return descriptor

    def simulated_windows_write(descriptor: int, content: bytes) -> int:
        if descriptor not in binary_descriptors:
            content = content.replace(b"\n", b"\r\n")
        return original_write(descriptor, content)

    monkeypatch.setattr(
        launcher_module.os,
        "O_BINARY",
        binary_flag,
        raising=False,
    )
    monkeypatch.setattr(launcher_module.os, "open", simulated_windows_open)
    monkeypatch.setattr(launcher_module.os, "write", simulated_windows_write)
    destination = tmp_path / "scope-state.json"
    expected = b'{"line":"one\\ntwo"}\nliteral-crlf\r\n'

    launcher_module._write_new_bytes(
        destination,
        expected,
        mode=0o600,
        label="scope state",
    )

    assert destination.read_bytes() == expected
