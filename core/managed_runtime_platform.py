"""Platform roots and non-mutating preflight for managed-runtime installs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import ctypes.util
import os
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
import platform
import shutil
import stat

from platformdirs import PlatformDirs

from core.managed_runtime import ManagedRuntimeError
from models.managed_runtime import ReleaseManifestV1, RuntimeTargetV1


_SCOPES = {"user", "system"}
_LINUX_CURSOR_SONAME = "libxcb-cursor.so.0"


@dataclass(frozen=True)
class ManagedRuntimePaths:
    """Canonical target paths derived from one explicit scope."""

    target: RuntimeTargetV1
    scope: str
    install_root: str
    state_root: str
    transaction_lock: str


@dataclass(frozen=True)
class HostPreflightSnapshot:
    """Normalized read-only host facts consumed by one preflight."""

    os_name: str
    os_version: str
    distribution_id: str | None
    arch: str
    available_free_bytes: int
    root_writable: bool
    privileged: bool
    root_is_safe: bool
    available_native_libraries: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.os_name not in {"linux", "windows", "macos"}:
            raise ManagedRuntimeError("host snapshot has an unsupported OS")
        if not self.os_version:
            raise ManagedRuntimeError("host snapshot OS version is required")
        if not self.arch:
            raise ManagedRuntimeError("host snapshot architecture is required")
        if (
            isinstance(self.available_free_bytes, bool)
            or not isinstance(self.available_free_bytes, int)
            or self.available_free_bytes < 0
        ):
            raise ManagedRuntimeError(
                "host snapshot available free bytes must be a non-negative integer"
            )
        for value, label in (
            (self.root_writable, "root_writable"),
            (self.privileged, "privileged"),
            (self.root_is_safe, "root_is_safe"),
        ):
            if not isinstance(value, bool):
                raise ManagedRuntimeError(
                    f"host snapshot {label} must be a boolean"
                )
        if any(
            not isinstance(item, str) or not item
            for item in self.available_native_libraries
        ):
            raise ManagedRuntimeError(
                "host snapshot native libraries must be non-empty strings"
            )


@dataclass(frozen=True)
class PreflightCheck:
    """One machine-readable and actionable preflight finding."""

    code: str
    passed: bool
    message: str


@dataclass(frozen=True)
class PreflightReport:
    """Complete non-mutating decision for one target/scope/root."""

    target_id: str
    scope: str
    install_root: str
    required_free_bytes: int
    available_free_bytes: int
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(check.passed for check in self.checks)

    def require_passed(self) -> None:
        """Fail loudly with every failed check while preserving the report."""

        failed = [check for check in self.checks if not check.passed]
        if not failed:
            return
        detail = "; ".join(
            f"{check.code}: {check.message}" for check in failed
        )
        raise ManagedRuntimeError(f"preflight failed [{detail}]")


def resolve_runtime_paths(
    target: RuntimeTargetV1,
    scope: str,
    *,
    user_data_root: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> ManagedRuntimePaths:
    """Resolve the approved target/scope root without creating it.

    ``user_data_root`` is a normalized platform-context injection used by
    cross-target contract tests. Production callers omit it and use
    ``platformdirs`` on the matching host. System roots never fall back to a
    user root.
    """

    if not isinstance(target, RuntimeTargetV1):
        raise ManagedRuntimeError("runtime target must be RuntimeTargetV1")
    if scope not in _SCOPES:
        raise ManagedRuntimeError("install scope must be user or system")
    environment = os.environ if environment is None else environment

    if scope == "user":
        if user_data_root is None:
            if target.os != _current_os_name():
                raise ManagedRuntimeError(
                    "user root for a foreign target requires explicit platform context"
                )
            appname = "easyqc" if target.os == "linux" else "EasyQC"
            try:
                root_value = str(
                    PlatformDirs(appname, appauthor=False).user_data_path
                )
            except (OSError, RuntimeError) as exc:
                raise ManagedRuntimeError(
                    f"cannot resolve platform user data root: {exc}"
                ) from exc
        else:
            root_value = os.fspath(user_data_root)
    elif target.os == "linux":
        root_value = "/opt/easyqc"
    elif target.os == "macos":
        root_value = "/Library/Application Support/EasyQC"
    else:
        program_files = environment.get("ProgramFiles", "").strip()
        if not program_files:
            raise ManagedRuntimeError(
                "ProgramFiles is required for Windows system scope"
            )
        root_value = str(PureWindowsPath(program_files) / "EasyQC")

    pure_root = _canonical_target_path(root_value, target.os, "install root")
    state_root = pure_root / "state"
    transaction_lock = state_root / "transaction.lock"
    if target.os == _current_os_name():
        _reject_existing_unsafe_root(Path(str(pure_root)))
    return ManagedRuntimePaths(
        target=target,
        scope=scope,
        install_root=str(pure_root),
        state_root=str(state_root),
        transaction_lock=str(transaction_lock),
    )


def inspect_host(paths: ManagedRuntimePaths) -> HostPreflightSnapshot:
    """Read current host facts without creating or modifying install state."""

    if not isinstance(paths, ManagedRuntimePaths):
        raise ManagedRuntimeError("preflight paths must be ManagedRuntimePaths")
    os_name = _current_os_name()
    os_version, distribution_id = _current_os_version(os_name)
    arch = _normalized_arch(platform.machine())
    if paths.target.os != os_name:
        return HostPreflightSnapshot(
            os_name=os_name,
            os_version=os_version,
            distribution_id=distribution_id,
            arch=arch,
            available_free_bytes=0,
            root_writable=False,
            privileged=_is_privileged(os_name),
            root_is_safe=False,
            available_native_libraries=(),
        )

    root = Path(paths.install_root)
    root_is_safe = _local_authority_paths_are_safe(root)
    anchor = _nearest_existing_directory(root)
    try:
        available_free_bytes = int(shutil.disk_usage(anchor).free)
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect install filesystem: {exc}") from exc
    root_writable = os.access(anchor, os.W_OK | os.X_OK)
    libraries: tuple[str, ...] = ()
    if os_name == "linux" and ctypes.util.find_library("xcb-cursor"):
        libraries = (_LINUX_CURSOR_SONAME,)
    return HostPreflightSnapshot(
        os_name=os_name,
        os_version=os_version,
        distribution_id=distribution_id,
        arch=arch,
        available_free_bytes=available_free_bytes,
        root_writable=root_writable,
        privileged=_is_privileged(os_name),
        root_is_safe=root_is_safe,
        available_native_libraries=libraries,
    )


def run_preflight(
    paths: ManagedRuntimePaths,
    manifest: ReleaseManifestV1,
    *,
    snapshot: HostPreflightSnapshot | None = None,
) -> PreflightReport:
    """Return all target/scope/disk/native checks without writing state."""

    if not isinstance(paths, ManagedRuntimePaths):
        raise ManagedRuntimeError("preflight paths must be ManagedRuntimePaths")
    if not isinstance(manifest, ReleaseManifestV1):
        raise ManagedRuntimeError("preflight manifest must be ReleaseManifestV1")
    if manifest.target != paths.target:
        raise ManagedRuntimeError(
            "preflight manifest target does not match the resolved paths"
        )
    if snapshot is None:
        snapshot = inspect_host(paths)
    elif not isinstance(snapshot, HostPreflightSnapshot):
        raise ManagedRuntimeError(
            "preflight snapshot must be HostPreflightSnapshot"
        )

    target_passed, target_message = _target_check(paths.target, snapshot)
    safety_passed = snapshot.root_is_safe
    safety_message = (
        "Install authority paths are safe and contained."
        if safety_passed
        else "Install authority root is unsafe, symlinked, or not a directory."
    )
    if paths.scope == "system":
        permission_passed = snapshot.root_writable and snapshot.privileged
        permission_message = (
            "System scope has explicit privilege and a writable root boundary."
            if permission_passed
            else "System scope requires explicit privilege and a writable root; "
            "scope will not fall back to user."
        )
    else:
        permission_passed = snapshot.root_writable
        permission_message = (
            "User scope root boundary is writable."
            if permission_passed
            else "User scope root boundary is not writable; scope will not change."
        )
    disk_passed = snapshot.available_free_bytes >= manifest.required_free_bytes
    disk_message = (
        f"Available free bytes {snapshot.available_free_bytes} satisfy required "
        f"free bytes {manifest.required_free_bytes}."
        if disk_passed
        else f"Available free bytes {snapshot.available_free_bytes} are below "
        f"required free bytes {manifest.required_free_bytes}."
    )
    if paths.target.os == "linux":
        native_passed = (
            _LINUX_CURSOR_SONAME in snapshot.available_native_libraries
        )
        native_message = (
            f"Required native library {_LINUX_CURSOR_SONAME} is available."
            if native_passed
            else f"Missing {_LINUX_CURSOR_SONAME}; install the supported OS "
            "package libxcb-cursor0 before continuing."
        )
    else:
        native_passed = True
        native_message = (
            "No static native package requirement is asserted for this target; "
            "later native smoke evidence remains required."
        )

    return PreflightReport(
        target_id=paths.target.target_id,
        scope=paths.scope,
        install_root=paths.install_root,
        required_free_bytes=manifest.required_free_bytes,
        available_free_bytes=snapshot.available_free_bytes,
        checks=(
            PreflightCheck("HOST_TARGET", target_passed, target_message),
            PreflightCheck("ROOT_SAFETY", safety_passed, safety_message),
            PreflightCheck(
                "SCOPE_PERMISSION",
                permission_passed,
                permission_message,
            ),
            PreflightCheck("DISK_SPACE", disk_passed, disk_message),
            PreflightCheck(
                "NATIVE_PREREQUISITES",
                native_passed,
                native_message,
            ),
        ),
    )


def _canonical_target_path(value: str, target_os: str, label: str) -> PurePath:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ManagedRuntimeError(f"{label} must be a non-empty path string")
    path: PurePath
    if target_os == "windows":
        path = PureWindowsPath(value)
        canonical = str(path)
    else:
        path = PurePosixPath(value)
        canonical = path.as_posix()
    if not path.is_absolute() or ".." in path.parts or canonical != value:
        raise ManagedRuntimeError(
            f"{label} must be an absolute canonical {target_os} path"
        )
    if path.parent == path:
        raise ManagedRuntimeError(f"{label} must not be a filesystem root")
    return path


def _reject_existing_unsafe_root(root: Path) -> None:
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect install root: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ManagedRuntimeError("install root must not be a symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ManagedRuntimeError("install root must be a directory")


def _local_authority_paths_are_safe(root: Path) -> bool:
    for path, expected_directory in (
        (root, True),
        (root / "state", True),
        (root / "state" / "transaction.lock", False),
    ):
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if stat.S_ISLNK(metadata.st_mode):
            return False
        if expected_directory and not stat.S_ISDIR(metadata.st_mode):
            return False
        if not expected_directory and not stat.S_ISREG(metadata.st_mode):
            return False
    return True


def _nearest_existing_directory(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise ManagedRuntimeError(
                "no existing filesystem parent for the install root"
            )
        candidate = parent
    if candidate.is_symlink() or not candidate.is_dir():
        raise ManagedRuntimeError(
            "nearest existing install-root parent must be a real directory"
        )
    return candidate


def _target_check(
    target: RuntimeTargetV1,
    snapshot: HostPreflightSnapshot,
) -> tuple[bool, str]:
    arch_matches = _normalized_arch(snapshot.arch) == target.arch
    if target.os == "linux":
        version_matches = (
            snapshot.distribution_id == "ubuntu"
            and snapshot.os_version == target.os_minimum
        )
        passed = (
            snapshot.os_name == "linux" and arch_matches and version_matches
        )
        expected = f"Ubuntu {target.os_minimum} {target.arch}"
    elif target.os == "windows":
        passed = (
            snapshot.os_name == "windows"
            and arch_matches
            and snapshot.os_version == target.os_minimum
        )
        expected = f"Windows {target.os_minimum} {target.arch}"
    else:
        passed = (
            snapshot.os_name == "macos"
            and arch_matches
            and _major_version(snapshot.os_version) >= int(target.os_minimum)
        )
        expected = f"macOS {target.os_minimum}+ {target.arch}"
    actual_distribution = (
        f"{snapshot.distribution_id} " if snapshot.distribution_id else ""
    )
    actual = (
        f"{actual_distribution}{snapshot.os_name} "
        f"{snapshot.os_version} {_normalized_arch(snapshot.arch)}"
    )
    if passed:
        return True, f"Host target matches {expected}."
    return False, f"Host target mismatch: expected {expected}; observed {actual}."


def _major_version(value: str) -> int:
    try:
        return int(value.split(".", 1)[0])
    except (TypeError, ValueError) as exc:
        raise ManagedRuntimeError(
            f"host OS version has no numeric major component: {value!r}"
        ) from exc


def _normalized_arch(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "aarch64": "arm64",
    }
    return aliases.get(normalized, normalized)


def _current_os_name() -> str:
    if os.name == "nt":
        return "windows"
    if sys_platform := platform.system().lower():
        if sys_platform == "darwin":
            return "macos"
        if sys_platform == "linux":
            return "linux"
    raise ManagedRuntimeError("current operating system is unsupported")


def _current_os_version(os_name: str) -> tuple[str, str | None]:
    if os_name == "linux":
        try:
            release = platform.freedesktop_os_release()
        except OSError as exc:
            raise ManagedRuntimeError(f"cannot inspect Linux release: {exc}") from exc
        return release.get("VERSION_ID", "unknown"), release.get("ID")
    if os_name == "windows":
        return platform.release(), None
    version = platform.mac_ver()[0]
    return version or "unknown", None


def _is_privileged(os_name: str) -> bool:
    if os_name == "windows":
        try:
            return bool(
                ctypes.windll.shell32.IsUserAnAdmin()  # type: ignore[attr-defined]
            )
        except (AttributeError, OSError):
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


__all__ = [
    "HostPreflightSnapshot",
    "ManagedRuntimePaths",
    "PreflightCheck",
    "PreflightReport",
    "inspect_host",
    "resolve_runtime_paths",
    "run_preflight",
]
