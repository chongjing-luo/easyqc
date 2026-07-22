"""Final-path, offline-only uv materialization for verified runtime payloads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
from typing import Protocol

from core.managed_runtime import (
    ManagedRuntimeError,
    MaterializationRequest,
    MaterializationResult,
    VerifiedArtifact,
    verify_artifact_set,
)


_UV_VERSION_PATTERN = re.compile(r"^uv 0\.11\.29(?:\s|$)")
_PYTHON_VERSION = "Python 3.13.13"
_MAX_DIAGNOSTIC_CHARS = 4096


@dataclass(frozen=True)
class UvCommandResult:
    """Normalized subprocess observation for one fixed materialization step."""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class UvCommandRunner(Protocol):
    """One-purpose shell-free command boundary for uv and Python checks."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> UvCommandResult:
        """Run one absolute argv and return normalized captured output."""


class SubprocessUvCommandRunner:
    """Production runner using captured text output and ``shell=False``."""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> UvCommandResult:
        if not argv or not Path(argv[0]).is_absolute():
            raise ManagedRuntimeError("materialization command must be absolute")
        try:
            process = subprocess.run(
                list(argv),
                cwd=str(cwd),
                env=environment,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ManagedRuntimeError(
                f"materialization command timed out after {timeout_seconds}s"
            ) from exc
        except OSError as exc:
            raise ManagedRuntimeError(
                f"cannot execute materialization command: {exc}"
            ) from exc
        return UvCommandResult(
            argv=argv,
            returncode=process.returncode,
            stdout=process.stdout,
            stderr=process.stderr,
        )


class RealUvAdapter:
    """Materialize a verified release with pinned uv at its final path.

    The adapter intentionally refuses a renamable staging-root name because uv
    environments and managed-Python aliases contain absolute final-path state.
    Receipt, activation, rollback and retention remain caller responsibilities.
    """

    def __init__(
        self,
        *,
        runner: UvCommandRunner | None = None,
        timeout_seconds: float = 300.0,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or timeout_seconds <= 0
        ):
            raise ManagedRuntimeError("uv command timeout must be positive")
        self._runner = runner or SubprocessUvCommandRunner()
        self._timeout_seconds = float(timeout_seconds)

    def materialize(self, request: MaterializationRequest) -> MaterializationResult:
        """Create runtime/app/env output below one final release root."""

        if not isinstance(request, MaterializationRequest):
            raise ManagedRuntimeError(
                "uv materialization request must be MaterializationRequest"
            )
        version_root = _final_version_root(request)
        _require_empty_directory(version_root, "final version root")
        verified = verify_artifact_set(
            request.manifest,
            request.verified_artifacts.root,
            expected_target_id=request.manifest.target.target_id,
        )
        if (
            verified.manifest_sha256
            != request.verified_artifacts.manifest_sha256
            or verified.artifact_manifest_sha256
            != request.verified_artifacts.artifact_manifest_sha256
        ):
            raise ManagedRuntimeError(
                "verified artifact identity changed before materialization"
            )

        python_artifact = _artifact_by_filename(
            verified.artifacts,
            request.manifest.python.filename,
        )
        source_artifact = _artifact_by_filename(
            verified.artifacts,
            request.manifest.easyqc.source_filename,
        )
        uv_artifact = _artifact_by_filename(
            verified.artifacts,
            request.manifest.uv.filename,
        )
        notice_artifacts = tuple(
            _artifact_by_filename(verified.artifacts, notice.filename)
            for notice in request.manifest.notices
        )

        python_members = _validated_tar_members(
            python_artifact.path,
            "Python payload archive",
            required_prefix=request.manifest.python.key,
        )
        source_members = _validated_tar_members(
            source_artifact.path,
            "EasyQC source archive",
        )
        expanded_archive_bytes = sum(
            member.size
            for member in (*python_members, *source_members)
            if member.isfile()
        )
        if expanded_archive_bytes > request.manifest.expanded_version_bytes:
            raise ManagedRuntimeError(
                "archive expanded bytes exceed the manifest budget"
            )

        runtime_root = version_root / "runtime"
        app_root = version_root / "app"
        environment_root = version_root / "env"
        notices_root = version_root / "notices"
        work_root = version_root / ".materialize"
        for path, label in (
            (runtime_root, "runtime root"),
            (app_root, "app root"),
            (notices_root, "notices root"),
            (work_root, "materialization work root"),
        ):
            _create_new_directory(path, label)

        uv_executable = work_root / (
            "uv.exe" if request.manifest.target.os == "windows" else "uv"
        )
        _copy_executable(uv_artifact, uv_executable)
        command_environment = _sanitized_environment(
            version_root,
            runtime_root,
            work_root / "cache",
        )
        _run_checked(
            self._runner,
            "uv-version",
            (str(uv_executable), "--no-config", "--version"),
            version_root,
            command_environment,
            self._timeout_seconds,
            expected_pattern=_UV_VERSION_PATTERN,
        )

        _extract_members(
            python_artifact.path,
            runtime_root,
            python_members,
            "Python payload archive",
        )
        _extract_members(
            source_artifact.path,
            app_root,
            source_members,
            "EasyQC source archive",
        )
        _validate_tree(runtime_root, "runtime root")
        _validate_tree(app_root, "app root")

        exact_python_root = runtime_root / request.manifest.python.key
        if not exact_python_root.is_dir() or exact_python_root.is_symlink():
            raise ManagedRuntimeError(
                "Python payload archive did not produce the exact patch root"
            )
        python_executable = _python_executable(
            exact_python_root,
            request.manifest.target.os,
        )
        _require_contained_regular(
            python_executable,
            exact_python_root,
            "managed Python executable",
        )

        _run_checked(
            self._runner,
            "python-alias",
            (
                str(uv_executable),
                "--no-config",
                "python",
                "install",
                request.manifest.python.version,
                "--managed-python",
                "--install-dir",
                str(runtime_root),
                "--no-bin",
                "--offline",
                "--no-python-downloads",
            ),
            version_root,
            command_environment,
            self._timeout_seconds,
        )
        _require_destination_alias(
            runtime_root,
            request.manifest.python.key,
            request.manifest.python.version,
        )
        _run_checked(
            self._runner,
            "Python version",
            (str(python_executable), "--version"),
            version_root,
            command_environment,
            self._timeout_seconds,
            expected_text=_PYTHON_VERSION,
        )

        _run_checked(
            self._runner,
            "venv",
            (
                str(uv_executable),
                "--no-config",
                "venv",
                str(environment_root),
                "--python",
                str(python_executable),
                "--managed-python",
                "--no-project",
                "--offline",
                "--no-python-downloads",
            ),
            version_root,
            command_environment,
            self._timeout_seconds,
        )
        environment_python = _python_executable(
            environment_root,
            request.manifest.target.os,
        )
        _require_environment_python(
            environment_python,
            environment_root,
            python_executable,
        )

        lock_path = verified.root / request.manifest.lock.filename
        _run_checked(
            self._runner,
            "pip-sync",
            (
                str(uv_executable),
                "--no-config",
                "pip",
                "sync",
                str(lock_path),
                "--python",
                str(environment_python),
                "--require-hashes",
                "--managed-python",
                "--offline",
                "--no-python-downloads",
                "--no-index",
                "--find-links",
                str(verified.root),
                "--only-binary",
                ":all:",
                "--strict",
            ),
            version_root,
            command_environment,
            self._timeout_seconds,
        )
        _run_checked(
            self._runner,
            "pip-check",
            (
                str(uv_executable),
                "--no-config",
                "pip",
                "check",
                "--python",
                str(environment_python),
                "--managed-python",
                "--offline",
                "--no-python-downloads",
            ),
            version_root,
            command_environment,
            self._timeout_seconds,
        )

        for artifact in notice_artifacts:
            _copy_regular_file(
                artifact.path,
                notices_root / artifact.filename,
                f"notice {artifact.filename}",
            )
        entrypoint = app_root.joinpath(
            *PurePosixPath(request.manifest.easyqc.entrypoint).parts
        )
        _require_contained_regular(entrypoint, app_root, "EasyQC entrypoint")
        _remove_success_work_root(work_root)
        return MaterializationResult(
            python_executable=environment_python,
            entrypoint=entrypoint,
        )


def _final_version_root(request: MaterializationRequest) -> Path:
    root = request.version_root
    if not isinstance(root, Path) or not root.is_absolute():
        raise ManagedRuntimeError("final version root must be an absolute Path")
    if ".." in root.parts or str(root) != os.path.normpath(str(root)):
        raise ManagedRuntimeError("final version root must be canonical")
    if root.name != request.manifest.release_id:
        raise ManagedRuntimeError(
            "uv materialization requires the final release path"
        )
    try:
        metadata = root.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(
            "final version root must be an existing real directory"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ManagedRuntimeError(
            "final version root must be an existing real directory"
        )
    return root


def _require_empty_directory(path: Path, label: str) -> None:
    try:
        if any(path.iterdir()):
            raise ManagedRuntimeError(f"{label} must be empty")
    except ManagedRuntimeError:
        raise
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect {label}: {exc}") from exc


def _artifact_by_filename(
    artifacts: tuple[VerifiedArtifact, ...],
    filename: str,
) -> VerifiedArtifact:
    matches = [item for item in artifacts if item.filename == filename]
    if len(matches) != 1:
        raise ManagedRuntimeError(
            f"verified artifact identity is missing or duplicate: {filename}"
        )
    return matches[0]


def _validated_tar_members(
    archive_path: Path,
    label: str,
    *,
    required_prefix: str | None = None,
) -> tuple[tarfile.TarInfo, ...]:
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            all_members = archive.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise ManagedRuntimeError(f"cannot read {label}: {exc}") from exc
    selected: list[tarfile.TarInfo] = []
    for member in all_members:
        parts = _safe_archive_parts(member.name, label)
        _require_safe_tar_type(member, label)
        _require_safe_link(member, parts, label)
        if required_prefix is None or parts[0] == required_prefix:
            selected.append(member)
    if not selected:
        suffix = f" for {required_prefix}" if required_prefix else ""
        raise ManagedRuntimeError(f"{label} has no usable members{suffix}")
    return tuple(selected)


def _safe_archive_parts(name: str, label: str) -> tuple[str, ...]:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or path.as_posix() != name
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ManagedRuntimeError(f"{label} member is unsafe: {name!r}")
    return path.parts


def _require_safe_tar_type(member: tarfile.TarInfo, label: str) -> None:
    if not (
        member.isfile()
        or member.isdir()
        or member.issym()
        or member.islnk()
    ):
        raise ManagedRuntimeError(
            f"{label} contains an unsafe special member: {member.name}"
        )


def _require_safe_link(
    member: tarfile.TarInfo,
    member_parts: tuple[str, ...],
    label: str,
) -> None:
    if not (member.issym() or member.islnk()):
        return
    target = PurePosixPath(member.linkname)
    if (
        not member.linkname
        or "\\" in member.linkname
        or target.is_absolute()
    ):
        raise ManagedRuntimeError(
            f"{label} link target is unsafe: {member.name}"
        )
    base = member_parts[:-1] if member.issym() else ()
    depth = 0
    for part in (*base, *target.parts):
        if part in {"", "."}:
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                raise ManagedRuntimeError(
                    f"{label} link target escapes: {member.name}"
                )
        else:
            depth += 1


def _extract_members(
    archive_path: Path,
    destination: Path,
    members: tuple[tarfile.TarInfo, ...],
    label: str,
) -> None:
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            archive.extractall(
                path=destination,
                members=members,
                filter="data",
            )
    except (OSError, tarfile.TarError) as exc:
        raise ManagedRuntimeError(f"cannot extract {label}: {exc}") from exc


def _validate_tree(root: Path, label: str) -> None:
    authority = root.resolve(strict=True)
    try:
        paths = tuple(root.rglob("*"))
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect extracted {label}: {exc}") from exc
    for path in paths:
        try:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                resolved = path.resolve(strict=True)
                resolved.relative_to(authority)
            elif not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)):
                raise ManagedRuntimeError(
                    f"extracted {label} contains a special file"
                )
        except (OSError, ValueError) as exc:
            raise ManagedRuntimeError(
                f"extracted {label} has an unsafe or broken link: {path.name}"
            ) from exc


def _create_new_directory(path: Path, label: str) -> None:
    try:
        path.mkdir(mode=0o700)
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot create {label}: {exc}") from exc


def _copy_executable(artifact: VerifiedArtifact, destination: Path) -> None:
    _copy_regular_file(artifact.path, destination, "uv executable")
    try:
        destination.chmod(0o700)
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot mark uv executable: {exc}") from exc
    if _sha256_file(destination) != artifact.sha256:
        raise ManagedRuntimeError("copied uv executable hash mismatch")


def _copy_regular_file(source: Path, destination: Path, label: str) -> None:
    try:
        metadata = source.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect {label}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must be a regular non-symlink file")
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)
            writer.flush()
            os.fsync(writer.fileno())
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot copy {label}: {exc}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot hash copied file: {exc}") from exc
    return digest.hexdigest()


def _sanitized_environment(
    version_root: Path,
    runtime_root: Path,
    cache_root: Path,
) -> dict[str, str]:
    allowed = (
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    )
    environment = {
        key: os.environ[key] for key in allowed if key in os.environ
    }
    environment.update(
        {
            "UV_CACHE_DIR": str(cache_root),
            "UV_PYTHON_INSTALL_DIR": str(runtime_root),
            "UV_OFFLINE": "1",
            "UV_MANAGED_PYTHON": "1",
            "UV_NO_CONFIG": "1",
            "UV_NO_PROGRESS": "1",
            "NO_COLOR": "1",
            "EASYQC_MATERIALIZATION_ROOT": str(version_root),
        }
    )
    return environment


def _run_checked(
    runner: UvCommandRunner,
    step: str,
    argv: tuple[str, ...],
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: float,
    *,
    expected_pattern: re.Pattern[str] | None = None,
    expected_text: str | None = None,
) -> UvCommandResult:
    try:
        result = runner.run(
            argv,
            cwd=cwd,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
    except ManagedRuntimeError as exc:
        raise ManagedRuntimeError(f"{step} failed: {exc}") from exc
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise ManagedRuntimeError(f"{step} failed: {exc}") from exc
    if not isinstance(result, UvCommandResult):
        raise ManagedRuntimeError(f"{step} returned an invalid command result")
    if result.returncode != 0:
        detail = _bounded_diagnostic(result.stderr or result.stdout)
        raise ManagedRuntimeError(
            f"{step} command exit {result.returncode}: {detail}"
        )
    observed = (result.stdout or result.stderr).strip()
    if expected_pattern is not None and not expected_pattern.match(observed):
        raise ManagedRuntimeError(
            f"uv version mismatch: expected 0.11.29, observed "
            f"{_bounded_diagnostic(observed)}"
        )
    if expected_text is not None and observed != expected_text:
        raise ManagedRuntimeError(
            f"Python version mismatch: expected {expected_text}, observed "
            f"{_bounded_diagnostic(observed)}"
        )
    return result


def _bounded_diagnostic(value: str) -> str:
    normalized = value.replace("\x00", "?").strip()
    if not normalized:
        return "no diagnostic output"
    if len(normalized) > _MAX_DIAGNOSTIC_CHARS:
        return f"{normalized[:_MAX_DIAGNOSTIC_CHARS]}...<truncated>"
    return normalized


def _python_executable(root: Path, target_os: str) -> Path:
    if target_os == "windows":
        if root.name == "env":
            return root / "Scripts" / "python.exe"
        return root / "python.exe"
    return root / "bin" / "python"


def _require_contained_regular(path: Path, root: Path, label: str) -> None:
    try:
        authority = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(authority)
        metadata = resolved.stat()
    except (OSError, ValueError) as exc:
        raise ManagedRuntimeError(f"{label} must be contained and present") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must be a regular file")


def _require_environment_python(
    path: Path,
    environment_root: Path,
    managed_python: Path,
) -> None:
    label = "environment Python executable"
    try:
        environment_authority = environment_root.resolve(strict=True)
        path.parent.resolve(strict=True).relative_to(environment_authority)
        path_metadata = path.lstat()
        if not (
            stat.S_ISREG(path_metadata.st_mode)
            or stat.S_ISLNK(path_metadata.st_mode)
        ):
            raise ManagedRuntimeError(
                f"{label} must be a regular file or symlink"
            )
        resolved = path.resolve(strict=True)
        if not stat.S_ISREG(resolved.stat().st_mode):
            raise ManagedRuntimeError(f"{label} must resolve to a regular file")
        managed_authority = managed_python.resolve(strict=True)
        if resolved != managed_authority:
            resolved.relative_to(environment_authority)
    except ManagedRuntimeError:
        raise
    except (OSError, ValueError) as exc:
        raise ManagedRuntimeError(
            f"{label} must stay within the allowed env or managed Python roots"
        ) from exc


def _require_destination_alias(
    runtime_root: Path,
    exact_key: str,
    version: str,
) -> None:
    major_minor = version.rsplit(".", 1)[0]
    alias_key = exact_key.replace(version, major_minor, 1)
    alias = runtime_root / alias_key
    exact = runtime_root / exact_key
    try:
        alias_target = alias.resolve(strict=False)
        exact_target = exact.resolve(strict=True)
        alias_target.relative_to(runtime_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ManagedRuntimeError(
            "Python alias must resolve to the contained exact patch root"
        ) from exc
    if alias_target != exact_target or not alias.exists():
        raise ManagedRuntimeError(
            "Python alias must resolve to the contained exact patch root"
        )


def _remove_success_work_root(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise ManagedRuntimeError(
            f"cannot remove successful materialization work root: {exc}"
        ) from exc


__all__ = [
    "RealUvAdapter",
    "SubprocessUvCommandRunner",
    "UvCommandResult",
    "UvCommandRunner",
]
