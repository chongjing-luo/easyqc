"""Fail-closed process lease for managed-runtime transactions."""

from __future__ import annotations

from dataclasses import dataclass, field
import errno
import os
from pathlib import Path
import stat
import sys
from types import ModuleType
from typing import Protocol

from core.managed_runtime import ManagedRuntimeError
from core.managed_runtime_platform import ManagedRuntimePaths


_CONTENTION_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    getattr(errno, "EDEADLK", errno.EACCES),
}


class TransactionLeaseContention(ManagedRuntimeError):
    """Raised when another process already owns the transaction lease."""


class _FileLockAdapter(Protocol):
    def acquire(self, descriptor: int) -> None:
        """Acquire one non-blocking exclusive lock or fail loudly."""

    def release(self, descriptor: int) -> None:
        """Release a previously acquired lock or fail loudly."""


class PosixFileLockAdapter:
    """Non-blocking whole-file lease implemented by ``fcntl.flock``."""

    def __init__(self, module: ModuleType) -> None:
        self._module = module

    def acquire(self, descriptor: int) -> None:
        try:
            self._module.flock(
                descriptor,
                self._module.LOCK_EX | self._module.LOCK_NB,
            )
        except OSError as exc:
            if exc.errno in _CONTENTION_ERRNOS:
                raise TransactionLeaseContention(
                    "managed-runtime transaction lease is already held"
                ) from exc
            raise ManagedRuntimeError(
                f"cannot acquire POSIX transaction lease: {exc}"
            ) from exc

    def release(self, descriptor: int) -> None:
        try:
            self._module.flock(descriptor, self._module.LOCK_UN)
        except OSError as exc:
            raise ManagedRuntimeError(
                f"cannot release POSIX transaction lease: {exc}"
            ) from exc


class WindowsFileLockAdapter:
    """Non-blocking one-byte lease implemented by ``msvcrt.locking``."""

    def __init__(self, module: ModuleType | object) -> None:
        self._module = module

    def acquire(self, descriptor: int) -> None:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            self._module.locking(  # type: ignore[attr-defined]
                descriptor,
                self._module.LK_NBLCK,  # type: ignore[attr-defined]
                1,
            )
        except OSError as exc:
            if exc.errno in _CONTENTION_ERRNOS:
                raise TransactionLeaseContention(
                    "managed-runtime transaction lease is already held"
                ) from exc
            raise ManagedRuntimeError(
                f"cannot acquire Windows transaction lease: {exc}"
            ) from exc

    def release(self, descriptor: int) -> None:
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            self._module.locking(  # type: ignore[attr-defined]
                descriptor,
                self._module.LK_UNLCK,  # type: ignore[attr-defined]
                1,
            )
        except OSError as exc:
            raise ManagedRuntimeError(
                f"cannot release Windows transaction lease: {exc}"
            ) from exc


@dataclass
class TransactionLease:
    """One acquired OS lease whose ordinary lock file remains persistent."""

    path: Path
    _descriptor: int
    _adapter: _FileLockAdapter
    acquired: bool = field(default=True, init=False)

    def release(self) -> None:
        """Release and close deterministically; repeated release is harmless."""

        if not self.acquired:
            return
        release_error: Exception | None = None
        try:
            self._adapter.release(self._descriptor)
        except Exception as exc:
            release_error = exc
        try:
            os.close(self._descriptor)
        except OSError as exc:
            if release_error is None:
                release_error = ManagedRuntimeError(
                    f"cannot close transaction lease: {exc}"
                )
        finally:
            self._descriptor = -1
            self.acquired = False
        if release_error is not None:
            raise release_error

    def __enter__(self) -> "TransactionLease":
        if not self.acquired:
            raise ManagedRuntimeError("transaction lease has already been released")
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.release()


def acquire_transaction_lease(paths: ManagedRuntimePaths) -> TransactionLease:
    """Create the contained lock authority and acquire it without waiting.

    Side effects are limited to the resolved install root, its ``state``
    directory and a persistent regular ``transaction.lock`` file. Unsupported
    locking, contention and unsafe paths all fail closed.
    """

    if not isinstance(paths, ManagedRuntimePaths):
        raise ManagedRuntimeError("transaction paths must be ManagedRuntimePaths")
    _require_native_target(paths)
    root, state_root, lock_path = _validated_authority_paths(paths)
    adapter = _native_adapter()

    _ensure_real_directory(root, "install root")
    _ensure_real_directory(state_root, "state root")
    descriptor = _open_regular_lock(lock_path)
    try:
        adapter.acquire(descriptor)
        _require_unchanged_regular_file(lock_path, descriptor)
    except Exception:
        os.close(descriptor)
        raise
    return TransactionLease(lock_path, descriptor, adapter)


def _native_adapter() -> _FileLockAdapter:
    if os.name == "nt":
        try:
            import msvcrt
        except ImportError as exc:  # pragma: no cover - broken native runtime
            raise ManagedRuntimeError(
                "Windows transaction locking is unsupported by this runtime"
            ) from exc
        return WindowsFileLockAdapter(msvcrt)
    if os.name == "posix":
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - broken native runtime
            raise ManagedRuntimeError(
                "POSIX transaction locking is unsupported by this runtime"
            ) from exc
        return PosixFileLockAdapter(fcntl)
    raise ManagedRuntimeError(
        f"transaction locking is unsupported on os.name={os.name!r}"
    )


def _require_native_target(paths: ManagedRuntimePaths) -> None:
    if sys.platform == "win32":
        native_os = "windows"
    elif sys.platform == "darwin":
        native_os = "macos"
    elif sys.platform.startswith("linux"):
        native_os = "linux"
    else:
        raise ManagedRuntimeError(
            f"transaction locking is unsupported on platform {sys.platform!r}"
        )
    if paths.target.os != native_os:
        raise ManagedRuntimeError(
            "transaction target does not match the current operating system"
        )


def _validated_authority_paths(
    paths: ManagedRuntimePaths,
) -> tuple[Path, Path, Path]:
    root = Path(paths.install_root)
    state_root = Path(paths.state_root)
    lock_path = Path(paths.transaction_lock)
    if not root.is_absolute():
        raise ManagedRuntimeError("transaction install root must be absolute")
    if ".." in root.parts or str(root) != paths.install_root:
        raise ManagedRuntimeError("transaction install root must be canonical")
    if root.parent == root:
        raise ManagedRuntimeError(
            "transaction install root is an unsafe filesystem root"
        )
    if state_root != root / "state":
        raise ManagedRuntimeError("transaction state root is not contained")
    if lock_path != state_root / "transaction.lock":
        raise ManagedRuntimeError("transaction lock path is not contained")
    return root, state_root, lock_path


def _ensure_real_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except PermissionError as exc:
            raise ManagedRuntimeError(
                f"transaction lease permission denied creating {label}: {exc}"
            ) from exc
        except OSError as exc:
            raise ManagedRuntimeError(
                f"cannot create transaction {label}: {exc}"
            ) from exc
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ManagedRuntimeError(
                f"cannot inspect transaction {label}: {exc}"
            ) from exc
    except PermissionError as exc:
        raise ManagedRuntimeError(
            f"transaction lease permission denied inspecting {label}: {exc}"
        ) from exc
    except OSError as exc:
        raise ManagedRuntimeError(
            f"cannot inspect transaction {label}: {exc}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ManagedRuntimeError(f"transaction {label} must not be a symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ManagedRuntimeError(f"transaction {label} must be a directory")


def _open_regular_lock(lock_path: Path) -> int:
    try:
        metadata = lock_path.lstat()
    except FileNotFoundError:
        metadata = None
    except PermissionError as exc:
        raise ManagedRuntimeError(
            f"transaction lease permission denied inspecting lock: {exc}"
        ) from exc
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect transaction lock: {exc}") from exc
    if metadata is not None:
        if stat.S_ISLNK(metadata.st_mode):
            raise ManagedRuntimeError("transaction lock must not be a symlink")
        if not stat.S_ISREG(metadata.st_mode):
            raise ManagedRuntimeError("transaction lock must be a regular file")

    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except PermissionError as exc:
        raise ManagedRuntimeError(
            f"transaction lease permission denied opening lock: {exc}"
        ) from exc
    except OSError as exc:
        if exc.errno == getattr(errno, "ELOOP", -1):
            raise ManagedRuntimeError(
                "transaction lock must not be a symlink"
            ) from exc
        raise ManagedRuntimeError(f"cannot open transaction lock: {exc}") from exc
    try:
        _require_unchanged_regular_file(lock_path, descriptor)
        metadata = os.fstat(descriptor)
        if metadata.st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _require_unchanged_regular_file(lock_path: Path, descriptor: int) -> None:
    try:
        opened = os.fstat(descriptor)
        authority = lock_path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(
            f"cannot validate open transaction lock: {exc}"
        ) from exc
    if stat.S_ISLNK(authority.st_mode):
        raise ManagedRuntimeError("transaction lock must not be a symlink")
    if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(authority.st_mode):
        raise ManagedRuntimeError("transaction lock must be a regular file")
    if (opened.st_dev, opened.st_ino) != (authority.st_dev, authority.st_ino):
        raise ManagedRuntimeError("transaction lock authority changed while opening")


__all__ = [
    "PosixFileLockAdapter",
    "TransactionLease",
    "TransactionLeaseContention",
    "WindowsFileLockAdapter",
    "acquire_transaction_lease",
]
