"""Invariant launcher rendering, exposure ownership, and inspection.

The stable commands live outside release directories.  Their only authority
is the receipt-gated activation pointer; updates and rollbacks never rewrite
these bytes or PATH ownership state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import ctypes
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import secrets
import stat
from typing import Protocol

from core.managed_runtime import ManagedRuntimeError
from core.managed_runtime_platform import ManagedRuntimePaths
from models.managed_runtime import canonical_json_bytes


_SCOPE_STATE_FIELDS = {
    "schema_version",
    "target_id",
    "scope",
    "install_root",
    "canonical_product",
    "canonical_installer",
    "exposure_root",
    "exposed_product",
    "exposed_installer",
    "path_contains_exposure",
    "windows_path_previous",
    "windows_path_installed",
}
_MAX_SCOPE_STATE_BYTES = 64 * 1024


@dataclass(frozen=True)
class LauncherArtifact:
    """One deterministic launcher file relative to the installation root."""

    relative_path: PurePosixPath
    content: bytes
    executable: bool


@dataclass(frozen=True)
class StableLauncherPaths:
    """Resolved canonical and user-visible launcher paths."""

    product: Path
    installer: Path
    exposure_root: Path
    exposed_product: Path
    exposed_installer: Path
    path_contains_exposure: bool


@dataclass(frozen=True)
class LauncherInspection:
    """Read-only structural launcher and ownership result."""

    ready: bool
    issues: tuple[str, ...]
    paths: StableLauncherPaths


@dataclass(frozen=True)
class WindowsPathUpdate:
    """Exact idempotent transition for one Windows PATH value."""

    previous: str
    current: str
    changed: bool


class WindowsPathAdapter(Protocol):
    """Reads and atomically replaces the selected Windows PATH authority."""

    def read_path(self, scope: str) -> str:
        """Return the exact current user or system PATH value."""

    def write_path(self, scope: str, value: str) -> None:
        """Replace the exact user or system PATH value."""


class RegistryWindowsPathAdapter:
    """Native Windows registry implementation used by production installs."""

    def read_path(self, scope: str) -> str:
        winreg, hive, key_name = _windows_registry_location(scope)
        try:
            with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ) as key:
                try:
                    value, _kind = winreg.QueryValueEx(key, "Path")
                except FileNotFoundError:
                    return ""
        except OSError as exc:
            raise ManagedRuntimeError(
                f"cannot read Windows {scope} PATH"
            ) from exc
        if not isinstance(value, str) or "\x00" in value:
            raise ManagedRuntimeError("Windows PATH registry value is invalid")
        return value

    def write_path(self, scope: str, value: str) -> None:
        if not isinstance(value, str) or "\x00" in value:
            raise ManagedRuntimeError("Windows PATH replacement is invalid")
        previous = self.read_path(scope)
        winreg, hive, key_name = _windows_registry_location(scope)
        access = winreg.KEY_SET_VALUE
        try:
            with winreg.CreateKeyEx(hive, key_name, 0, access) as key:
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, value)
                winreg.FlushKey(key)
        except OSError as exc:
            raise ManagedRuntimeError(
                f"cannot write Windows {scope} PATH"
            ) from exc
        try:
            _broadcast_environment_change()
        except ManagedRuntimeError as exc:
            try:
                with winreg.CreateKeyEx(hive, key_name, 0, access) as key:
                    winreg.SetValueEx(
                        key,
                        "Path",
                        0,
                        winreg.REG_EXPAND_SZ,
                        previous,
                    )
                    winreg.FlushKey(key)
            except OSError as rollback_exc:
                raise ManagedRuntimeError(
                    "Windows PATH notification failed and registry rollback failed"
                ) from rollback_exc
            raise ManagedRuntimeError(
                "Windows PATH notification failed; registry value was restored"
            ) from exc


def render_launcher_artifacts(
    paths: ManagedRuntimePaths,
) -> tuple[LauncherArtifact, ...]:
    """Render exact canonical launcher bytes without touching the filesystem."""

    _require_paths(paths)
    if paths.target.os == "windows":
        root = _safe_windows_root(paths.install_root)
        product = _windows_cmd(root, "product")
        installer = _windows_cmd(root, "installer")
        return (
            LauncherArtifact(PurePosixPath("bin/easyqc.cmd"), product, False),
            LauncherArtifact(
                PurePosixPath("bin/easyqc-install.cmd"), installer, False
            ),
            LauncherArtifact(
                PurePosixPath("bootstrap/controller/easyqc-launch.ps1"),
                _windows_powershell_launcher(),
                False,
            ),
        )
    root = _safe_posix_root(paths.install_root)
    return (
        LauncherArtifact(
            PurePosixPath("bin/easyqc"),
            _posix_canonical_launcher(root, "product"),
            True,
        ),
        LauncherArtifact(
            PurePosixPath("bin/easyqc-install"),
            _posix_canonical_launcher(root, "installer"),
            True,
        ),
    )


def install_stable_launchers(
    paths: ManagedRuntimePaths,
    *,
    posix_exposure_root: Path | None = None,
    environment_path: str | None = None,
    windows_path_adapter: WindowsPathAdapter | None = None,
) -> StableLauncherPaths:
    """Create the invariant launchers once and atomically record ownership.

    Existing non-identical files are collisions and remain byte-for-byte
    untouched.  Exact files from an interrupted first attempt may be adopted;
    the ownership record is always written last.
    """

    _require_paths(paths)
    artifacts = render_launcher_artifacts(paths)
    launcher_paths = _launcher_paths(
        paths,
        posix_exposure_root=posix_exposure_root,
        environment_path=environment_path,
        windows_path_adapter=windows_path_adapter,
    )
    state_path = Path(paths.state_root) / "scope-state.json"
    if state_path.exists() or state_path.is_symlink():
        inspection = inspect_stable_launchers(
            paths,
            posix_exposure_root=posix_exposure_root,
            environment_path=environment_path,
            windows_path_adapter=windows_path_adapter,
        )
        if not inspection.ready:
            raise ManagedRuntimeError(
                "stable launcher ownership is incomplete: "
                + "; ".join(inspection.issues)
            )
        return inspection.paths

    root = Path(paths.install_root)
    canonical = tuple(
        (root.joinpath(*artifact.relative_path.parts), artifact)
        for artifact in artifacts
    )
    exposure_artifacts: tuple[tuple[Path, bytes, bool], ...] = ()
    if paths.target.os != "windows":
        exposure_artifacts = (
            (
                launcher_paths.exposed_product,
                _posix_exposure_wrapper(launcher_paths.product),
                True,
            ),
            (
                launcher_paths.exposed_installer,
                _posix_exposure_wrapper(launcher_paths.installer),
                True,
            ),
        )
    for destination, artifact in canonical:
        _reject_collision(destination, artifact.content, artifact.executable)
    for destination, content, executable in exposure_artifacts:
        _reject_collision(destination, content, executable)

    _ensure_real_directory(root, "install root", parents=True)
    for destination, artifact in canonical:
        _write_exact_once(
            destination,
            artifact.content,
            executable=artifact.executable,
            label="canonical launcher",
        )
    for destination, content, executable in exposure_artifacts:
        _write_exact_once(
            destination,
            content,
            executable=executable,
            label="launcher exposure",
        )

    path_transition: WindowsPathUpdate | None = None
    active_windows_adapter = windows_path_adapter
    if paths.target.os == "windows":
        active_windows_adapter = active_windows_adapter or RegistryWindowsPathAdapter()
        previous = active_windows_adapter.read_path(paths.scope)
        path_transition = next_windows_path(
            previous,
            str(launcher_paths.exposure_root),
        )
        if path_transition.changed:
            active_windows_adapter.write_path(paths.scope, path_transition.current)

    scope_record = _scope_record(paths, launcher_paths, path_transition)
    try:
        _ensure_real_directory(
            state_path.parent,
            "scope-state directory",
            parents=True,
        )
        _write_new_bytes(
            state_path,
            canonical_json_bytes(scope_record),
            mode=0o600,
            label="scope state",
        )
    except Exception as exc:
        if (
            path_transition is not None
            and path_transition.changed
            and active_windows_adapter is not None
        ):
            try:
                active_windows_adapter.write_path(paths.scope, path_transition.previous)
            except Exception as rollback_exc:
                raise ManagedRuntimeError(
                    "scope-state write failed and Windows PATH rollback failed"
                ) from rollback_exc
        if isinstance(exc, ManagedRuntimeError):
            raise
        raise ManagedRuntimeError("cannot write stable launcher scope state") from exc
    return launcher_paths


def inspect_stable_launchers(
    paths: ManagedRuntimePaths,
    *,
    posix_exposure_root: Path | None = None,
    environment_path: str | None = None,
    windows_path_adapter: WindowsPathAdapter | None = None,
) -> LauncherInspection:
    """Validate owned launcher bytes and state without repairing anything."""

    _require_paths(paths)
    state_path = Path(paths.state_root) / "scope-state.json"
    if not state_path.exists() and not state_path.is_symlink():
        fallback = _launcher_paths(
            paths,
            posix_exposure_root=posix_exposure_root,
            environment_path=environment_path,
            windows_path_adapter=windows_path_adapter,
            inspect_only=True,
        )
        return LauncherInspection(False, ("scope state is missing",), fallback)
    record = _load_scope_record(state_path)
    _validate_scope_identity(record, paths)
    recorded_exposure = Path(str(record["exposure_root"]))
    expected_exposure = (
        Path(paths.install_root) / "bin"
        if paths.target.os == "windows"
        else posix_exposure_root or _default_posix_exposure_root(paths.scope)
    )
    if recorded_exposure != expected_exposure:
        raise ManagedRuntimeError(
            "scope state exposure root does not match the requested root"
        )
    expected_exposed_product = expected_exposure / (
        "easyqc.cmd" if paths.target.os == "windows" else "easyqc"
    )
    expected_exposed_installer = expected_exposure / (
        "easyqc-install.cmd" if paths.target.os == "windows" else "easyqc-install"
    )
    if (
        record["exposed_product"] != str(expected_exposed_product)
        or record["exposed_installer"] != str(expected_exposed_installer)
    ):
        raise ManagedRuntimeError("scope state exposed launcher paths are invalid")
    launcher_paths = StableLauncherPaths(
        product=Path(str(record["canonical_product"])),
        installer=Path(str(record["canonical_installer"])),
        exposure_root=recorded_exposure,
        exposed_product=Path(str(record["exposed_product"])),
        exposed_installer=Path(str(record["exposed_installer"])),
        path_contains_exposure=(
            bool(record["path_contains_exposure"])
            if paths.target.os == "windows"
            else _posix_path_contains(recorded_exposure, environment_path)
        ),
    )
    artifacts = render_launcher_artifacts(paths)
    root = Path(paths.install_root)
    expected: list[tuple[Path, bytes, bool, str]] = [
        (
            root.joinpath(*artifact.relative_path.parts),
            artifact.content,
            artifact.executable,
            artifact.relative_path.as_posix(),
        )
        for artifact in artifacts
    ]
    if paths.target.os != "windows":
        expected.extend(
            (
                destination,
                _posix_exposure_wrapper(canonical),
                True,
                label,
            )
            for destination, canonical, label in (
                (
                    launcher_paths.exposed_product,
                    launcher_paths.product,
                    "exposed easyqc",
                ),
                (
                    launcher_paths.exposed_installer,
                    launcher_paths.installer,
                    "exposed easyqc-install",
                ),
            )
        )
    issues: list[str] = []
    for destination, content, executable, label in expected:
        issue = _file_mismatch(destination, content, executable)
        if issue:
            issues.append(f"{label}: {issue}")
    if paths.target.os == "windows":
        adapter = windows_path_adapter or RegistryWindowsPathAdapter()
        current = adapter.read_path(paths.scope)
        path_present = not next_windows_path(
            current, str(launcher_paths.exposure_root)
        ).changed
        launcher_paths = StableLauncherPaths(
            launcher_paths.product,
            launcher_paths.installer,
            launcher_paths.exposure_root,
            launcher_paths.exposed_product,
            launcher_paths.exposed_installer,
            path_present,
        )
        if not path_present:
            issues.append("Windows PATH no longer contains the owned exposure")
    return LauncherInspection(not issues, tuple(issues), launcher_paths)


def next_windows_path(previous: str, exposure: str) -> WindowsPathUpdate:
    """Append one Windows path segment once using case-insensitive identity."""

    if not isinstance(previous, str) or "\x00" in previous:
        raise ManagedRuntimeError("previous Windows PATH is invalid")
    if not isinstance(exposure, str) or not exposure or "\x00" in exposure:
        raise ManagedRuntimeError("Windows PATH exposure is invalid")
    identity = _windows_path_identity(exposure)
    for segment in previous.split(";"):
        if segment and _windows_path_identity(segment) == identity:
            return WindowsPathUpdate(previous, previous, False)
    separator = "" if not previous or previous.endswith(";") else ";"
    current = f"{previous}{separator}{exposure}"
    return WindowsPathUpdate(previous, current, True)


def _launcher_paths(
    paths: ManagedRuntimePaths,
    *,
    posix_exposure_root: Path | None,
    environment_path: str | None,
    windows_path_adapter: WindowsPathAdapter | None,
    inspect_only: bool = False,
) -> StableLauncherPaths:
    root = Path(paths.install_root)
    if paths.target.os == "windows":
        product = root / "bin/easyqc.cmd"
        installer = root / "bin/easyqc-install.cmd"
        exposure_root = root / "bin"
        path_contains = False
        if not inspect_only:
            adapter = windows_path_adapter or RegistryWindowsPathAdapter()
            current = adapter.read_path(paths.scope)
            path_contains = not next_windows_path(
                current, str(exposure_root)
            ).changed
        return StableLauncherPaths(
            product,
            installer,
            exposure_root,
            product,
            installer,
            path_contains,
        )
    exposure_root = posix_exposure_root or _default_posix_exposure_root(paths.scope)
    if not isinstance(exposure_root, Path) or not exposure_root.is_absolute():
        raise ManagedRuntimeError("POSIX exposure root must be an absolute Path")
    path_contains = _posix_path_contains(exposure_root, environment_path)
    return StableLauncherPaths(
        root / "bin/easyqc",
        root / "bin/easyqc-install",
        exposure_root,
        exposure_root / "easyqc",
        exposure_root / "easyqc-install",
        path_contains,
    )


def _scope_record(
    paths: ManagedRuntimePaths,
    launchers: StableLauncherPaths,
    transition: WindowsPathUpdate | None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "target_id": paths.target.target_id,
        "scope": paths.scope,
        "install_root": paths.install_root,
        "canonical_product": str(launchers.product),
        "canonical_installer": str(launchers.installer),
        "exposure_root": str(launchers.exposure_root),
        "exposed_product": str(launchers.exposed_product),
        "exposed_installer": str(launchers.exposed_installer),
        "path_contains_exposure": (
            True if transition is not None else launchers.path_contains_exposure
        ),
        "windows_path_previous": transition.previous if transition else None,
        "windows_path_installed": transition.current if transition else None,
    }


def _load_scope_record(path: Path) -> dict[str, object]:
    data = _read_regular_bytes(path, "scope state", _MAX_SCOPE_STATE_BYTES)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManagedRuntimeError("scope state is not valid canonical JSON") from exc
    if not isinstance(value, dict) or set(value) != _SCOPE_STATE_FIELDS:
        raise ManagedRuntimeError("scope state fields are invalid")
    if canonical_json_bytes(value) != data:
        raise ManagedRuntimeError("scope state is not canonical JSON")
    if value["schema_version"] != 1:
        raise ManagedRuntimeError("scope state schema version is unsupported")
    text_fields = (
        "target_id",
        "scope",
        "install_root",
        "canonical_product",
        "canonical_installer",
        "exposure_root",
        "exposed_product",
        "exposed_installer",
    )
    if any(not isinstance(value[field], str) or not value[field] for field in text_fields):
        raise ManagedRuntimeError("scope state text fields are invalid")
    if not isinstance(value["path_contains_exposure"], bool):
        raise ManagedRuntimeError("scope state PATH flag is invalid")
    for field in ("windows_path_previous", "windows_path_installed"):
        if value[field] is not None and not isinstance(value[field], str):
            raise ManagedRuntimeError("scope state Windows PATH fields are invalid")
    return value


def _validate_scope_identity(
    record: Mapping[str, object], paths: ManagedRuntimePaths
) -> None:
    if (
        record["target_id"] != paths.target.target_id
        or record["scope"] != paths.scope
        or record["install_root"] != paths.install_root
    ):
        raise ManagedRuntimeError("scope state identity does not match this install")
    root = Path(paths.install_root)
    suffix = ".cmd" if paths.target.os == "windows" else ""
    expected_product = root / f"bin/easyqc{suffix}"
    expected_installer = root / f"bin/easyqc-install{suffix}"
    if (
        record["canonical_product"] != str(expected_product)
        or record["canonical_installer"] != str(expected_installer)
    ):
        raise ManagedRuntimeError("scope state canonical launcher paths are invalid")


def _posix_canonical_launcher(root: str, mode: str) -> bytes:
    entrypoint = "easyqc.py" if mode == "product" else "easyqc_install.py"
    label = "easyqc" if mode == "product" else "easyqc-install"
    text = f"""#!/bin/sh
set -eu
EASYQC_ROOT={_sh_literal(root)}
fail() {{
    printf '%s\\n' "{label}: $*" >&2
    exit 70
}}
POINTER="$EASYQC_ROOT/state/activation.txt"
if [ ! -f "$POINTER" ] || [ -L "$POINTER" ]; then
    fail "activation pointer is missing or unsafe"
fi
{{
    IFS= read -r RELEASE_ID || fail "activation pointer is malformed"
    IFS= read -r PREVIOUS_ID || fail "activation pointer is malformed"
    IFS= read -r GENERATION || fail "activation pointer is malformed"
    if IFS= read -r EXTRA; then
        : "$EXTRA"
        fail "activation pointer is malformed"
    fi
}} < "$POINTER"
case "$RELEASE_ID" in ''|*[!A-Za-z0-9._-]*) fail "active release ID is invalid" ;; esac
case "$PREVIOUS_ID" in -) ;; ''|*[!A-Za-z0-9._-]*) fail "previous release ID is invalid" ;; esac
case "$GENERATION" in ''|*[!0-9]*) fail "activation generation is invalid" ;; esac
VERSION_ROOT="$EASYQC_ROOT/versions/$RELEASE_ID"
if [ ! -d "$VERSION_ROOT" ] || [ -L "$VERSION_ROOT" ]; then
    fail "active version root is missing or unsafe"
fi
VERSION_REAL=$(CDPATH='' cd -P -- "$VERSION_ROOT" && pwd -P) || fail "cannot resolve active version root"
[ "$VERSION_REAL" = "$VERSION_ROOT" ] || fail "active version root escapes install root"
resolve_file() {{
    candidate=$1
    links=0
    while [ -L "$candidate" ]; do
        target=$(readlink "$candidate") || return 1
        case "$target" in
            /*) candidate=$target ;;
            *) candidate=$(dirname -- "$candidate")/$target ;;
        esac
        links=$((links + 1))
        [ "$links" -le 40 ] || return 1
    done
    [ -f "$candidate" ] || return 1
    directory=$(CDPATH='' cd -P -- "$(dirname -- "$candidate")" && pwd -P) || return 1
    printf '%s/%s\\n' "$directory" "$(basename -- "$candidate")"
}}
RECEIPT="$VERSION_ROOT/install-receipt.json"
if [ ! -f "$RECEIPT" ] || [ -L "$RECEIPT" ]; then
    fail "install receipt is missing or unsafe"
fi
RECEIPT_REAL=$(resolve_file "$RECEIPT") || fail "cannot resolve install receipt"
[ "$RECEIPT_REAL" = "$RECEIPT" ] || fail "install receipt escapes version root"
PYTHON="$VERSION_ROOT/env/bin/python"
PYTHON_REAL=$(resolve_file "$PYTHON") || fail "active Python is missing or unsafe"
case "$PYTHON_REAL" in "$VERSION_REAL"/*) ;; *) fail "active Python escapes version root" ;; esac
ENTRYPOINT="$VERSION_ROOT/app/{entrypoint}"
[ ! -L "$ENTRYPOINT" ] || fail "active entrypoint is unsafe"
ENTRYPOINT_REAL=$(resolve_file "$ENTRYPOINT") || fail "active entrypoint is missing or unsafe"
[ "$ENTRYPOINT_REAL" = "$ENTRYPOINT" ] || fail "active entrypoint escapes version root"
exec "$PYTHON_REAL" "$ENTRYPOINT_REAL" "$@"
"""
    return text.encode("utf-8")


def _posix_exposure_wrapper(canonical: Path) -> bytes:
    value = str(canonical)
    _safe_posix_root(value)
    return (
        "#!/bin/sh\n"
        f"exec {_sh_literal(value)} \"$@\"\n"
    ).encode("utf-8")


def _windows_cmd(root: str, mode: str) -> bytes:
    safe_root = root.replace("%", "%%")
    text = (
        "@echo off\r\n"
        "setlocal\r\n"
        f'set "EASYQC_ROOT={safe_root}"\r\n'
        "powershell.exe -NoLogo -NoProfile -NonInteractive "
        '-ExecutionPolicy Bypass -File "%EASYQC_ROOT%\\bootstrap\\controller'
        f'\\easyqc-launch.ps1" {mode} %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    )
    return text.encode("utf-8")


def _windows_powershell_launcher() -> bytes:
    lines = (
        "param(",
        "    [Parameter(Position=0, Mandatory=$true)]",
        "    [ValidateSet('product', 'installer')]",
        "    [string]$Mode,",
        "    [Parameter(ValueFromRemainingArguments=$true)]",
        "    [string[]]$ForwardedArguments",
        ")",
        "$ErrorActionPreference = 'Stop'",
        "$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\\..'))",
        "$Pointer = Join-Path $Root 'state\\activation.txt'",
        "if ((Get-Item -LiteralPath $Pointer -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'activation pointer is unsafe' }",
        "$PointerText = [IO.File]::ReadAllText($Pointer, [Text.Encoding]::ASCII)",
        "if ($PointerText -notmatch '\\A([A-Za-z0-9._-]+)\\n(?:-|[A-Za-z0-9._-]+)\\n[0-9]+\\n\\z') { throw 'activation pointer is malformed' }",
        "$ReleaseId = $Matches[1]",
        "$VersionRoot = [IO.Path]::GetFullPath((Join-Path $Root ('versions\\' + $ReleaseId)))",
        "$ResolvedVersion = (Resolve-Path -LiteralPath $VersionRoot).Path",
        "if ($ResolvedVersion -ne $VersionRoot) { throw 'active version root escapes install root' }",
        "$Receipt = Join-Path $VersionRoot 'install-receipt.json'",
        "$ReceiptItem = Get-Item -LiteralPath $Receipt -Force",
        "if ($ReceiptItem.PSIsContainer -or ($ReceiptItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'install receipt is unsafe' }",
        "$null = Resolve-Path -LiteralPath $Receipt",
        "$PythonCandidate = Join-Path $VersionRoot 'env\\Scripts\\python.exe'",
        "$PythonItem = Get-Item -LiteralPath $PythonCandidate -Force",
        "if ($PythonItem.PSIsContainer -or ($PythonItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'active Python is unsafe' }",
        "$Python = (Resolve-Path -LiteralPath $PythonCandidate).Path",
        "$Prefix = $VersionRoot.TrimEnd('\\') + '\\'",
        "if (-not $Python.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'active Python escapes version root' }",
        "$EntryName = if ($Mode -eq 'product') { 'easyqc.py' } else { 'easyqc_install.py' }",
        "$Entry = Join-Path $VersionRoot ('app\\' + $EntryName)",
        "$EntryItem = Get-Item -LiteralPath $Entry -Force",
        "if ($EntryItem.PSIsContainer -or ($EntryItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'active entrypoint is unsafe' }",
        "$ResolvedEntry = (Resolve-Path -LiteralPath $Entry).Path",
        "if ($ResolvedEntry -ne $Entry) { throw 'active entrypoint escapes version root' }",
        "& $Python $ResolvedEntry @ForwardedArguments",
        "exit $LASTEXITCODE",
    )
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def _safe_posix_root(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ManagedRuntimeError("POSIX install root must be absolute")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ManagedRuntimeError("POSIX install root contains unsafe characters")
    if PurePosixPath(value).as_posix() != value or value == "/":
        raise ManagedRuntimeError("POSIX install root must be canonical and non-root")
    return value


def _safe_windows_root(value: str) -> str:
    if not isinstance(value, str) or "\x00" in value or "\r" in value or "\n" in value:
        raise ManagedRuntimeError("Windows install root contains unsafe characters")
    path = PureWindowsPath(value)
    if not path.is_absolute() or path.parent == path or '"' in value:
        raise ManagedRuntimeError("Windows install root must be canonical and absolute")
    if str(path) != value:
        raise ManagedRuntimeError("Windows install root must be canonical")
    return value


def _sh_literal(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _default_posix_exposure_root(scope: str) -> Path:
    if scope == "system":
        return Path("/usr/local/bin")
    return Path.home() / ".local/bin"


def _posix_path_contains(
    exposure_root: Path,
    environment_path: str | None,
) -> bool:
    path_value = os.environ.get("PATH", "") if environment_path is None else environment_path
    return any(
        segment == str(exposure_root) for segment in path_value.split(os.pathsep)
    )


def _reject_collision(path: Path, content: bytes, executable: bool) -> None:
    if not path.exists() and not path.is_symlink():
        return
    issue = _file_mismatch(path, content, executable)
    if issue:
        raise ManagedRuntimeError(f"stable launcher collision at {path}: {issue}")


def _file_mismatch(path: Path, content: bytes, executable: bool) -> str | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"cannot inspect ({exc})"
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return "not a regular non-symlink file"
    try:
        actual = path.read_bytes()
    except OSError as exc:
        return f"cannot read ({exc})"
    if actual != content:
        return "bytes differ"
    if executable and not metadata.st_mode & stat.S_IXUSR:
        return "not executable by owner"
    return None


def _write_exact_once(
    path: Path,
    content: bytes,
    *,
    executable: bool,
    label: str,
) -> None:
    if path.exists() or path.is_symlink():
        issue = _file_mismatch(path, content, executable)
        if issue:
            raise ManagedRuntimeError(f"{label} collision at {path}: {issue}")
        return
    _ensure_real_directory(path.parent, f"{label} directory", parents=True)
    _write_new_bytes(
        path,
        content,
        mode=0o755 if executable else 0o644,
        label=label,
    )


def _write_new_bytes(path: Path, content: bytes, *, mode: int, label: str) -> None:
    """Atomically create one new file without ever replacing a collision."""

    temporary = path.with_name(
        f".{path.name}.tmp.{os.getpid()}.{secrets.token_hex(8)}"
    )
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(
            temporary,
            flags,
            mode,
        )
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise OSError("short launcher write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.chmod(temporary, mode)
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ManagedRuntimeError(
                f"{label} collision appeared during atomic create"
            ) from exc
        _fsync_directory(path.parent)
    except ManagedRuntimeError:
        raise
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot atomically create {label}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            if path.exists():
                raise ManagedRuntimeError(
                    f"cannot remove committed {label} temporary file"
                ) from exc


def _ensure_real_directory(path: Path, label: str, *, parents: bool) -> None:
    try:
        if not path.exists() and not path.is_symlink():
            path.mkdir(parents=parents, mode=0o700)
        metadata = path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot create or inspect {label}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must be a real directory")


def _read_regular_bytes(path: Path, label: str, maximum: int) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must be a regular non-symlink file")
    if metadata.st_size > maximum:
        raise ManagedRuntimeError(f"{label} exceeds its size limit")
    try:
        data = path.read_bytes()
        after = path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot read {label}") from exc
    if (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ManagedRuntimeError(f"{label} changed while it was read")
    return data


def _windows_path_identity(value: str) -> str:
    stripped = value.strip().strip('"').replace("/", "\\")
    path = PureWindowsPath(stripped)
    return str(path).rstrip("\\").casefold()


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError as exc:
        raise ManagedRuntimeError("cannot flush launcher directory") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _windows_registry_location(scope: str):
    if os.name != "nt":
        raise ManagedRuntimeError("Windows PATH registry requires native Windows")
    if scope not in {"user", "system"}:
        raise ManagedRuntimeError("Windows PATH scope must be user or system")
    try:
        import winreg
    except ImportError as exc:
        raise ManagedRuntimeError("Windows registry module is unavailable") from exc
    if scope == "user":
        return winreg, winreg.HKEY_CURRENT_USER, "Environment"
    return (
        winreg,
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    )


def _broadcast_environment_change() -> None:
    try:
        result = ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
            0xFFFF,
            0x001A,
            0,
            "Environment",
            0x0002,
            5000,
            None,
        )
    except (AttributeError, OSError) as exc:
        raise ManagedRuntimeError(
            "Windows PATH changed but environment notification failed"
        ) from exc
    if not result:
        raise ManagedRuntimeError(
            "Windows PATH changed but environment notification timed out"
        )


def _require_paths(paths: ManagedRuntimePaths) -> None:
    if not isinstance(paths, ManagedRuntimePaths):
        raise ManagedRuntimeError("stable launcher paths must be ManagedRuntimePaths")


__all__ = [
    "LauncherArtifact",
    "LauncherInspection",
    "RegistryWindowsPathAdapter",
    "StableLauncherPaths",
    "WindowsPathAdapter",
    "WindowsPathUpdate",
    "install_stable_launchers",
    "inspect_stable_launchers",
    "next_windows_path",
    "render_launcher_artifacts",
]
