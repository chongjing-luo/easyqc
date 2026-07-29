"""Cross-platform, project-scoped serialization for rating file writes."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import threading
from typing import Iterator


_LOCK_FILENAME = ".easyqc-rating-write.lock"
_CONTENTION_ERRNOS = {
    errno.EACCES,
    errno.EAGAIN,
    getattr(errno, "EDEADLK", errno.EACCES),
}
_MUTEX_REGISTRY_GUARD = threading.Lock()
_PROJECT_MUTEXES: dict[str, threading.RLock] = {}
_THREAD_DEPTHS = threading.local()


class RatingWriteLockError(RuntimeError):
    """Raised when a rating writer lock cannot be used safely."""


class RatingWriteBusyError(RatingWriteLockError):
    """Raised when another process currently owns the project writer lock."""


@contextmanager
def rating_write_lock(
    target_json_path: str | os.PathLike[str],
    *,
    create_lock_root: bool = True,
) -> Iterator[Path]:
    """Serialize writes for the project containing ``target_json_path``.

    The context yields the persistent lock-file path. Threads in this process
    wait for one another via a per-project reentrant lock. A different process
    is never waited on: OS-level contention raises :class:`RatingWriteBusyError`.
    Normal saves retain the default directory-creation behavior. Safety
    operations can pass ``create_lock_root=False`` so a missing active tree is
    rejected instead of accidentally reconstructed.
    """

    lock_path = _resolve_lock_path(
        target_json_path,
        create_lock_root=create_lock_root,
    )
    lock_key = os.path.normcase(
        os.path.realpath(os.path.abspath(lock_path))
    )
    mutex = _project_mutex(lock_key)

    with mutex:
        depths = _thread_depths()
        if depths.get(lock_key, 0):
            depths[lock_key] += 1
            try:
                yield lock_path
            finally:
                depths[lock_key] -= 1
            return

        descriptor = _open_lock_file(lock_path)
        try:
            _acquire_os_lock(descriptor)
        except Exception:
            os.close(descriptor)
            raise

        depths[lock_key] = 1
        try:
            yield lock_path
        finally:
            del depths[lock_key]
            release_error: Exception | None = None
            try:
                _release_os_lock(descriptor)
            except Exception as exc:
                release_error = exc
            try:
                os.close(descriptor)
            except OSError as exc:
                if release_error is None:
                    release_error = RatingWriteLockError(
                        f"cannot close rating writer lock {lock_path}: {exc}"
                    )
            if release_error is not None:
                raise release_error


def _resolve_lock_path(
    target_json_path: str | os.PathLike[str],
    *,
    create_lock_root: bool,
) -> Path:
    target = Path(target_json_path)
    lock_root = target.parent
    for ancestor in target.parents:
        if ancestor.name == "RatingFiles":
            lock_root = ancestor
            break

    if create_lock_root:
        try:
            lock_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RatingWriteLockError(
                f"cannot create rating writer lock directory {lock_root}: {exc}"
            ) from exc
        return lock_root / _LOCK_FILENAME

    try:
        metadata = lock_root.lstat()
    except FileNotFoundError as exc:
        raise RatingWriteLockError(
            f"rating writer lock directory must already exist: {lock_root}"
        ) from exc
    except OSError as exc:
        raise RatingWriteLockError(
            f"cannot inspect rating writer lock directory {lock_root}: {exc}"
        ) from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise RatingWriteLockError(
            f"rating writer lock directory must already exist as a real directory: "
            f"{lock_root}"
        )
    return lock_root / _LOCK_FILENAME


def _project_mutex(lock_key: str):
    with _MUTEX_REGISTRY_GUARD:
        mutex = _PROJECT_MUTEXES.get(lock_key)
        if mutex is None:
            mutex = threading.RLock()
            _PROJECT_MUTEXES[lock_key] = mutex
        return mutex


def _thread_depths() -> dict[str, int]:
    depths = getattr(_THREAD_DEPTHS, "values", None)
    if depths is None:
        depths = {}
        _THREAD_DEPTHS.values = depths
    return depths


def _open_lock_file(lock_path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise RatingWriteLockError(
            f"cannot open rating writer lock {lock_path}: {exc}"
        ) from exc

    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RatingWriteLockError(
                f"rating writer lock must be a regular file: {lock_path}"
            )
        if metadata.st_size == 0:
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _acquire_os_lock(descriptor: int) -> None:
    if os.name == "posix":
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - broken native runtime
            raise RatingWriteLockError(
                "POSIX rating writer locking is unavailable"
            ) from exc
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            _raise_acquire_error(exc)
        return

    if os.name == "nt":
        try:
            import msvcrt
        except ImportError as exc:  # pragma: no cover - broken native runtime
            raise RatingWriteLockError(
                "Windows rating writer locking is unavailable"
            ) from exc
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            _raise_acquire_error(exc)
        return

    raise RatingWriteLockError(
        f"rating writer locking is unsupported on os.name={os.name!r}"
    )


def _raise_acquire_error(exc: OSError) -> None:
    if exc.errno in _CONTENTION_ERRNOS:
        raise RatingWriteBusyError(
            "rating project is already being written by another process"
        ) from exc
    raise RatingWriteLockError(f"cannot acquire rating writer lock: {exc}") from exc


def _release_os_lock(descriptor: int) -> None:
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
            return
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            return
    except (ImportError, OSError) as exc:
        raise RatingWriteLockError(
            f"cannot release rating writer lock: {exc}"
        ) from exc
    raise RatingWriteLockError(
        f"rating writer locking is unsupported on os.name={os.name!r}"
    )


__all__ = [
    "RatingWriteBusyError",
    "RatingWriteLockError",
    "rating_write_lock",
]
