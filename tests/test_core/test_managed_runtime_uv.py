from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from core.managed_runtime import (
    ManagedRuntimeError,
    MaterializationRequest,
    verify_artifact_set,
)
from core.managed_runtime_uv import (
    RealUvAdapter,
    SubprocessUvCommandRunner,
    UvCommandResult,
)
from models.managed_runtime import ReleaseManifestV1


MIB = 1024 * 1024
TARGET_ID = "ubuntu-22.04-x86_64"
PYTHON_KEY = "cpython-3.13.13-linux-x86_64-gnu"
PYTHON_ALIAS = "cpython-3.13-linux-x86_64-gnu"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tar_bytes(
    files: dict[str, bytes],
    *,
    symlinks: dict[str, str] | None = None,
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o755 if name.endswith(("python3.13", ".py")) else 0o644
            archive.addfile(member, io.BytesIO(data))
        for name, target in (symlinks or {}).items():
            member = tarfile.TarInfo(name)
            member.type = tarfile.SYMTYPE
            member.linkname = target
            member.mode = 0o777
            archive.addfile(member)
    return buffer.getvalue()


def _payloads(*, unsafe_python_member: str | None = None) -> dict[str, bytes]:
    python_files = {
        f"{PYTHON_KEY}/bin/python3.13": b"fake managed python",
        f"{PYTHON_KEY}/BUILD": b"20260720",
    }
    if unsafe_python_member is not None:
        python_files[unsafe_python_member] = b"escape"
    return {
        "uv": b"fake pinned uv executable",
        "python.tar.gz": _tar_bytes(
            python_files,
            symlinks={f"{PYTHON_KEY}/bin/python": "python3.13"},
        ),
        "easyqc-source.tar.gz": _tar_bytes(
            {"easyqc.py": b"print('easyqc')\n"}
        ),
        "requirements.lock": b"dependency==1 --hash=sha256:" + b"1" * 64,
        "dependency-1-py3-none-any.whl": b"wheel",
        "NOTICE.txt": b"notice",
    }


def _manifest(payloads: dict[str, bytes]) -> ReleaseManifestV1:
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
            "release_id": "easyqc-1.0.0-linux",
            "channel": "candidate",
            "target": {
                "os": "linux",
                "os_minimum": "22.04",
                "arch": "x86_64",
            },
            "easyqc": {
                "version": "1.0.0",
                "source_filename": "easyqc-source.tar.gz",
                "source_sha256": _sha256(payloads["easyqc-source.tar.gz"]),
                "entrypoint": "easyqc.py",
            },
            "uv": {
                "version": "0.11.29",
                "filename": "uv",
                "size_bytes": len(payloads["uv"]),
                "sha256": _sha256(payloads["uv"]),
            },
            "python": {
                "version": "3.13.13",
                "build": "20260720",
                "key": PYTHON_KEY,
                "filename": "python.tar.gz",
                "size_bytes": len(payloads["python.tar.gz"]),
                "sha256": _sha256(payloads["python.tar.gz"]),
            },
            "lock": {
                "filename": "requirements.lock",
                "sha256": _sha256(payloads["requirements.lock"]),
                "require_hashes": True,
            },
            "artifacts": [
                artifact("easyqc-source", "easyqc-source.tar.gz"),
                artifact("lock", "requirements.lock"),
                artifact("wheel", "dependency-1-py3-none-any.whl"),
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


def _request(tmp_path: Path, payloads: dict[str, bytes]):
    artifact_root = tmp_path / "payload"
    artifact_root.mkdir()
    for filename, data in payloads.items():
        (artifact_root / filename).write_bytes(data)
    manifest = _manifest(payloads)
    verified = verify_artifact_set(
        manifest,
        artifact_root,
        expected_target_id=TARGET_ID,
    )
    version_root = tmp_path / "versions" / manifest.release_id
    version_root.mkdir(parents=True)
    return (
        MaterializationRequest(version_root, manifest, verified),
        artifact_root,
    )


class FakeRunner:
    def __init__(
        self,
        *,
        uv_version: str = "uv 0.11.29 (x86_64-unknown-linux-gnu)",
        python_version: str = "Python 3.13.13",
        fail_step: str | None = None,
        escaping_alias: bool = False,
        escaping_environment_python: bool = False,
    ) -> None:
        self.uv_version = uv_version
        self.python_version = python_version
        self.fail_step = fail_step
        self.escaping_alias = escaping_alias
        self.escaping_environment_python = escaping_environment_python
        self.calls: list[
            tuple[tuple[str, ...], Path, dict[str, str], float]
        ] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> UvCommandResult:
        self.calls.append((argv, cwd, environment, timeout_seconds))
        if argv[1:] == ("--no-config", "--version"):
            step = "uv-version"
            stdout = self.uv_version
        elif "python" in argv and "install" in argv:
            step = "python-alias"
            runtime_root = Path(argv[argv.index("--install-dir") + 1])
            alias = runtime_root / PYTHON_ALIAS
            target = (
                cwd.parent / "outside"
                if self.escaping_alias
                else runtime_root / PYTHON_KEY
            )
            alias.symlink_to(target, target_is_directory=True)
            stdout = "Python 3.13.13 is already installed"
        elif argv[0].endswith("python") and argv[1:] == ("--version",):
            step = "python-version"
            stdout = self.python_version
        elif "venv" in argv:
            step = "venv"
            environment_root = Path(argv[argv.index("venv") + 1])
            python = environment_root / "bin" / "python"
            python.parent.mkdir(parents=True)
            managed_python = Path(argv[argv.index("--python") + 1])
            if self.escaping_environment_python:
                managed_python = cwd.parent / "outside-python"
                managed_python.write_bytes(b"outside")
            python.symlink_to(managed_python)
            stdout = ""
        elif "sync" in argv:
            step = "pip-sync"
            stdout = ""
        else:
            step = "pip-check"
            stdout = "all packages compatible"
        if step == self.fail_step:
            return UvCommandResult(argv, 7, stdout, f"{step} failed")
        return UvCommandResult(argv, 0, stdout, "")


def test_real_uv_adapter_materializes_final_path_with_fixed_offline_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request, artifact_root = _request(tmp_path, _payloads())
    runner = FakeRunner()
    monkeypatch.setenv("PYTHONPATH", "/untrusted/pythonpath")
    monkeypatch.setenv("UV_INDEX_URL", "https://secret.invalid/simple")
    monkeypatch.setenv("HTTP_PROXY", "http://user:password@proxy.invalid")

    result = RealUvAdapter(runner=runner, timeout_seconds=15.0).materialize(
        request
    )

    runtime_root = request.version_root / "runtime"
    exact_python = runtime_root / PYTHON_KEY / "bin" / "python"
    alias = runtime_root / PYTHON_ALIAS
    assert result.python_executable == request.version_root / "env/bin/python"
    assert result.python_executable.is_symlink()
    assert result.entrypoint == request.version_root / "app/easyqc.py"
    assert result.entrypoint.read_text(encoding="utf-8") == "print('easyqc')\n"
    assert (request.version_root / "notices/NOTICE.txt").read_bytes() == b"notice"
    assert alias.is_symlink()
    assert alias.resolve() == (runtime_root / PYTHON_KEY).resolve()

    commands = [call[0] for call in runner.calls]
    assert commands[0][1:] == ("--no-config", "--version")
    assert commands[1][1:5] == ("--no-config", "python", "install", "3.13.13")
    assert "--install-dir" in commands[1]
    assert "--no-bin" in commands[1]
    assert commands[2] == (str(exact_python), "--version")
    venv = next(command for command in commands if "venv" in command)
    sync = next(command for command in commands if "sync" in command)
    check = next(command for command in commands if "check" in command)
    assert venv[1:4] == ("--no-config", "venv", str(request.version_root / "env"))
    assert venv[venv.index("--python") + 1] == str(exact_python)
    assert sync[sync.index("--python") + 1] == str(result.python_executable)
    assert sync[sync.index("--find-links") + 1] == str(artifact_root)
    for flag in (
        "--managed-python",
        "--offline",
        "--no-python-downloads",
        "--no-index",
        "--require-hashes",
        "--only-binary",
        "--strict",
    ):
        assert flag in sync
    assert check[check.index("--python") + 1] == str(result.python_executable)

    for argv, cwd, environment, timeout in runner.calls:
        assert Path(argv[0]).is_absolute()
        assert cwd == request.version_root
        assert timeout == 15.0
        assert environment["UV_OFFLINE"] == "1"
        assert environment["UV_CACHE_DIR"].startswith(str(request.version_root))
        assert environment["UV_PYTHON_INSTALL_DIR"] == str(runtime_root)
        assert "PYTHONPATH" not in environment
        assert "UV_INDEX_URL" not in environment
        assert "HTTP_PROXY" not in environment
    assert not (request.version_root / ".materialize").exists()


def test_real_uv_adapter_rejects_renamable_staging_root_before_writes(
    tmp_path: Path,
) -> None:
    request, _artifact_root = _request(tmp_path, _payloads())
    staging = request.version_root.parent / ".staging-easyqc-1.0.0-linux-deadbeef"
    staging.mkdir()
    request = MaterializationRequest(
        staging,
        request.manifest,
        request.verified_artifacts,
    )
    runner = FakeRunner()

    with pytest.raises(ManagedRuntimeError, match="final release path"):
        RealUvAdapter(runner=runner).materialize(request)

    assert not runner.calls
    assert list(staging.iterdir()) == []


def test_real_uv_adapter_rejects_archive_traversal_before_commands(
    tmp_path: Path,
) -> None:
    request, _artifact_root = _request(
        tmp_path,
        _payloads(unsafe_python_member="../escape"),
    )
    runner = FakeRunner()

    with pytest.raises(ManagedRuntimeError, match="archive.*unsafe"):
        RealUvAdapter(runner=runner).materialize(request)

    assert not runner.calls
    assert not (tmp_path / "escape").exists()


def test_real_uv_adapter_rejects_archives_above_manifest_expanded_budget(
    tmp_path: Path,
) -> None:
    payloads = _payloads()
    payloads["easyqc-source.tar.gz"] = _tar_bytes(
        {"easyqc.py": b"x" * 5000}
    )
    request, _artifact_root = _request(tmp_path, payloads)
    runner = FakeRunner()

    with pytest.raises(ManagedRuntimeError, match="expanded.*budget"):
        RealUvAdapter(runner=runner).materialize(request)

    assert not runner.calls
    assert list(request.version_root.iterdir()) == []


def test_real_uv_adapter_rejects_alias_outside_final_runtime(
    tmp_path: Path,
) -> None:
    request, _artifact_root = _request(tmp_path, _payloads())
    runner = FakeRunner(escaping_alias=True)

    with pytest.raises(ManagedRuntimeError, match="Python alias.*contained"):
        RealUvAdapter(runner=runner).materialize(request)


def test_real_uv_adapter_rejects_environment_python_outside_allowed_roots(
    tmp_path: Path,
) -> None:
    request, _artifact_root = _request(tmp_path, _payloads())
    runner = FakeRunner(escaping_environment_python=True)

    with pytest.raises(ManagedRuntimeError, match="environment Python.*allowed"):
        RealUvAdapter(runner=runner).materialize(request)


@pytest.mark.parametrize(
    ("runner", "message"),
    (
        (FakeRunner(uv_version="uv 0.11.28"), "uv version"),
        (FakeRunner(uv_version=""), "uv version"),
        (FakeRunner(python_version="Python 3.13.12"), "Python version"),
        (FakeRunner(fail_step="pip-sync"), "pip-sync.*exit 7"),
    ),
)
def test_real_uv_adapter_fails_loudly_on_version_or_command_failure(
    runner: FakeRunner,
    message: str,
    tmp_path: Path,
) -> None:
    request, _artifact_root = _request(tmp_path, _payloads())

    with pytest.raises(ManagedRuntimeError, match=message):
        RealUvAdapter(runner=runner).materialize(request)

    assert not (request.version_root / "install-receipt.json").exists()
    assert not (request.version_root.parent.parent / "state/activation.txt").exists()


def test_subprocess_uv_runner_fails_loudly_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    executable = tmp_path / "uv"

    def time_out(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(str(executable), timeout=2.0)

    monkeypatch.setattr(subprocess, "run", time_out)

    with pytest.raises(ManagedRuntimeError, match="timed out after 2.0s"):
        SubprocessUvCommandRunner().run(
            (str(executable), "--version"),
            cwd=tmp_path,
            environment={},
            timeout_seconds=2.0,
        )


def test_real_uv_adapter_reverifies_artifacts_before_writing(
    tmp_path: Path,
) -> None:
    request, artifact_root = _request(tmp_path, _payloads())
    wheel = artifact_root / "dependency-1-py3-none-any.whl"
    wheel.write_bytes(b"x" * wheel.stat().st_size)
    runner = FakeRunner()

    with pytest.raises(ManagedRuntimeError, match="hash"):
        RealUvAdapter(runner=runner).materialize(request)

    assert not runner.calls
    assert list(request.version_root.iterdir()) == []
