from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import core.managed_runtime_lock as managed_runtime_lock
from core.managed_runtime import ManagedRuntimeError
from core.managed_runtime_lock import (
    TransactionLeaseContention,
    WindowsFileLockAdapter,
    acquire_transaction_lease,
)
from core.managed_runtime_platform import resolve_runtime_paths
from models.managed_runtime import RuntimeTargetV1


TARGET = RuntimeTargetV1("linux", "22.04", "x86_64")


def _paths(root: Path):
    return resolve_runtime_paths(
        TARGET,
        "user",
        user_data_root=str(root),
    )


def test_transaction_lease_rejects_a_second_owner_and_reacquires(
    tmp_path: Path,
) -> None:
    root = tmp_path / "安装 root with spaces"
    root.parent.mkdir(exist_ok=True)
    paths = _paths(root)

    first = acquire_transaction_lease(paths)
    with pytest.raises(TransactionLeaseContention, match="already held"):
        acquire_transaction_lease(paths)
    first.release()

    with acquire_transaction_lease(paths) as second:
        assert second.acquired
        lock_path = Path(paths.transaction_lock)
        metadata = lock_path.lstat()
        assert stat.S_ISREG(metadata.st_mode)
        assert not lock_path.is_symlink()
        assert metadata.st_size >= 1
    assert not second.acquired
    assert Path(paths.transaction_lock).is_file()


def test_real_subprocess_owner_excludes_contender_then_releases(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    root = tmp_path / "并发 root with spaces"
    code = """
import sys
from pathlib import Path
from core.managed_runtime_lock import acquire_transaction_lease
from core.managed_runtime_platform import resolve_runtime_paths
from models.managed_runtime import RuntimeTargetV1

paths = resolve_runtime_paths(
    RuntimeTargetV1("linux", "22.04", "x86_64"),
    "user",
    user_data_root=sys.argv[1],
)
with acquire_transaction_lease(paths):
    print("READY", flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(root)],
        cwd=easyqc_root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        startup_lines: list[str] = []
        for _ in range(20):
            line = child.stdout.readline()
            if not line:
                break
            startup_lines.append(line.strip())
            if line.strip() == "READY":
                break
        assert "READY" in startup_lines, startup_lines
        with pytest.raises(TransactionLeaseContention):
            acquire_transaction_lease(_paths(root))
    finally:
        if child.stdin is not None:
            child.stdin.write("release\n")
            child.stdin.flush()
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, f"stdout={stdout!r} stderr={stderr!r}"

    with acquire_transaction_lease(_paths(root)):
        pass


@pytest.mark.parametrize("unsafe_part", ("root", "state", "lock"))
def test_transaction_lease_rejects_symlink_authority_paths(
    unsafe_part: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "install"
    outside = tmp_path / "outside"
    outside.mkdir()
    state_root = root / "state"
    try:
        if unsafe_part == "root":
            root.symlink_to(outside, target_is_directory=True)
        else:
            root.mkdir()
        if unsafe_part == "state":
            state_root.symlink_to(outside, target_is_directory=True)
        elif unsafe_part == "lock":
            state_root.mkdir()
            (state_root / "transaction.lock").symlink_to(outside / "lock")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ManagedRuntimeError, match="symlink"):
        acquire_transaction_lease(_paths(root))


def test_transaction_lease_permission_error_has_no_lock_free_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path / "install")
    Path(paths.state_root).mkdir(parents=True)

    def deny_open(*_args, **_kwargs):
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr("core.managed_runtime_lock.os.open", deny_open)

    with pytest.raises(ManagedRuntimeError, match="permission"):
        acquire_transaction_lease(paths)


def test_transaction_lease_rejects_non_regular_lock_file(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path / "install")
    lock_path = Path(paths.transaction_lock)
    lock_path.mkdir(parents=True)

    with pytest.raises(ManagedRuntimeError, match="regular file"):
        acquire_transaction_lease(paths)


def test_unsupported_lock_backend_fails_before_creating_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "install"
    paths = _paths(root)

    def unavailable() -> None:
        raise ManagedRuntimeError("locking backend is unsupported")

    monkeypatch.setattr(managed_runtime_lock, "_native_adapter", unavailable)

    with pytest.raises(ManagedRuntimeError, match="unsupported"):
        acquire_transaction_lease(paths)
    assert not root.exists()


class _FakeMsvcrt:
    LK_NBLCK = 2
    LK_UNLCK = 0

    def __init__(self, *, contend: bool = False) -> None:
        self.contend = contend
        self.calls: list[tuple[int, int, int]] = []

    def locking(self, descriptor: int, mode: int, count: int) -> None:
        self.calls.append((descriptor, mode, count))
        if self.contend and mode == self.LK_NBLCK:
            raise OSError(errno.EACCES, "locked")


def test_windows_adapter_matches_acquire_contention_release_contract(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / "transaction.lock"
    lock_path.write_bytes(b"\0")
    module = _FakeMsvcrt()
    adapter = WindowsFileLockAdapter(module)

    descriptor = os.open(lock_path, os.O_RDWR)
    try:
        adapter.acquire(descriptor)
        adapter.release(descriptor)
    finally:
        os.close(descriptor)

    assert [mode for _, mode, count in module.calls if count == 1] == [
        module.LK_NBLCK,
        module.LK_UNLCK,
    ]

    contender = WindowsFileLockAdapter(_FakeMsvcrt(contend=True))
    descriptor = os.open(lock_path, os.O_RDWR)
    try:
        with pytest.raises(TransactionLeaseContention, match="already held"):
            contender.acquire(descriptor)
    finally:
        os.close(descriptor)
