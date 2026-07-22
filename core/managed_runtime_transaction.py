"""Receipt-gated, final-path transactions for the managed EasyQC runtime.

This module coordinates only already verified local artifacts.  Acquisition,
stable launchers, PATH integration, privilege escalation and retention cleanup
are deliberately separate contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
from typing import Callable, Protocol

from core.managed_runtime import (
    ManagedRuntimeError,
    MaterializationRequest,
    UvAdapter,
    VerifiedArtifactSet,
    activate_release,
    load_install_receipt,
    read_activation_pointer,
    rollback as rollback_release,
    validate_materialization_result,
    write_install_receipt,
)
from core.managed_runtime_lock import acquire_transaction_lease
from core.managed_runtime_platform import (
    HostPreflightSnapshot,
    ManagedRuntimePaths,
    run_preflight,
)
from models.managed_runtime import (
    ActivationPointerV1,
    InstallReceiptV1,
    ReleaseManifestV1,
    canonical_json_bytes,
)
from utils.file_utils import FileUtils


_CONTROLLER_VERSION = "s6-rt-03"
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_TRANSACTION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_MAX_ERROR_CHARS = 512
_MAX_SMOKE_OUTPUT_CHARS = 64 * 1024
_QT_SMOKE = (
    "from PySide6.QtCore import qVersion\n"
    "from PySide6.QtWidgets import QApplication\n"
    "app = QApplication([])\n"
    "print('QT_OFFSCREEN_OK ' + qVersion())\n"
    "app.quit()\n"
)


@dataclass(frozen=True)
class RuntimeTransactionRequest:
    """Complete verified input for install, update or repair."""

    paths: ManagedRuntimePaths
    manifest: ReleaseManifestV1
    verified_artifacts: VerifiedArtifactSet
    source_revision: str
    preflight_snapshot: HostPreflightSnapshot
    smoke_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.paths, ManagedRuntimePaths):
            raise ManagedRuntimeError("transaction paths must be ManagedRuntimePaths")
        if not isinstance(self.manifest, ReleaseManifestV1):
            raise ManagedRuntimeError("transaction manifest must be ReleaseManifestV1")
        if self.paths.target != self.manifest.target:
            raise ManagedRuntimeError("transaction paths and manifest target differ")
        if not isinstance(self.verified_artifacts, VerifiedArtifactSet):
            raise ManagedRuntimeError("transaction artifacts must already be verified")
        if self.verified_artifacts.manifest_sha256 != self.manifest.sha256:
            raise ManagedRuntimeError("verified artifacts belong to another manifest")
        _require_source_revision(self.source_revision)
        if not isinstance(self.preflight_snapshot, HostPreflightSnapshot):
            raise ManagedRuntimeError(
                "transaction preflight snapshot must be HostPreflightSnapshot"
            )
        if (
            isinstance(self.smoke_timeout_seconds, bool)
            or not isinstance(self.smoke_timeout_seconds, (int, float))
            or self.smoke_timeout_seconds <= 0
        ):
            raise ManagedRuntimeError("transaction smoke timeout must be positive")


@dataclass(frozen=True)
class RuntimeRollbackRequest:
    """Explicit authority needed for a rollback-only transaction."""

    paths: ManagedRuntimePaths
    source_revision: str

    def __post_init__(self) -> None:
        if not isinstance(self.paths, ManagedRuntimePaths):
            raise ManagedRuntimeError("rollback paths must be ManagedRuntimePaths")
        _require_source_revision(self.source_revision)


@dataclass(frozen=True)
class RuntimeSmokeCommandResult:
    """Bounded observation from one fixed smoke command."""

    name: str
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def __post_init__(self) -> None:
        if self.name not in {"version", "qt-offscreen"}:
            raise ManagedRuntimeError("smoke command has an unknown name")
        if not self.argv or any(
            not isinstance(item, str) or "\x00" in item for item in self.argv
        ):
            raise ManagedRuntimeError("smoke command argv is invalid")
        if isinstance(self.returncode, bool) or not isinstance(self.returncode, int):
            raise ManagedRuntimeError("smoke return code must be an integer")
        if not isinstance(self.stdout, str) or not isinstance(self.stderr, str):
            raise ManagedRuntimeError("smoke output must be text")
        if (
            len(self.stdout) > _MAX_SMOKE_OUTPUT_CHARS
            or len(self.stderr) > _MAX_SMOKE_OUTPUT_CHARS
        ):
            raise ManagedRuntimeError("smoke output exceeds the diagnostic limit")


class RuntimeSmokeRunner(Protocol):
    """Runs the fixed version and Qt-offscreen smoke contract."""

    def run(
        self,
        python_executable: Path,
        entrypoint: Path,
        expected_version: str,
        timeout_seconds: float,
    ) -> tuple[RuntimeSmokeCommandResult, ...]:
        """Return both bounded command observations without interpreting them."""


class SubprocessRuntimeSmokeRunner:
    """Shell-free production implementation of the two-command smoke contract."""

    def run(
        self,
        python_executable: Path,
        entrypoint: Path,
        expected_version: str,
        timeout_seconds: float,
    ) -> tuple[RuntimeSmokeCommandResult, ...]:
        if not python_executable.is_absolute() or not entrypoint.is_absolute():
            raise ManagedRuntimeError("smoke launch paths must be absolute")
        if not isinstance(expected_version, str) or not expected_version:
            raise ManagedRuntimeError("smoke expected version is required")
        environment = _smoke_environment()
        try:
            app_root = next(
                parent for parent in entrypoint.parents if parent.name == "app"
            )
        except StopIteration as exc:
            raise ManagedRuntimeError(
                "smoke entrypoint is not below the fixed app root"
            ) from exc
        version_root = app_root.parent
        commands = (
            ("version", (str(python_executable), str(entrypoint), "--version")),
            ("qt-offscreen", (str(python_executable), "-c", _QT_SMOKE)),
        )
        results: list[RuntimeSmokeCommandResult] = []
        for name, argv in commands:
            try:
                process = subprocess.run(
                    list(argv),
                    cwd=str(version_root),
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
                    f"{name} smoke timed out after {timeout_seconds}s"
                ) from exc
            except OSError as exc:
                raise ManagedRuntimeError(f"cannot execute {name} smoke") from exc
            results.append(
                RuntimeSmokeCommandResult(
                    name=name,
                    argv=argv,
                    returncode=process.returncode,
                    stdout=_bounded_output(process.stdout),
                    stderr=_bounded_output(process.stderr),
                )
            )
        return tuple(results)


@dataclass(frozen=True)
class RuntimeTransactionResult:
    """Receipt-backed outcome of one committed runtime transaction."""

    operation: str
    transaction_id: str
    version_root: Path
    receipt: InstallReceiptV1
    pointer: ActivationPointerV1
    report_path: Path
    quarantined_root: Path | None


@dataclass
class _TransactionState:
    operation: str
    transaction_id: str
    report_path: Path
    started_at_utc: str
    source_revision: str
    target_id: str
    scope: str
    install_root: Path
    release_id: str | None
    manifest_sha256: str | None
    version_root: Path | None
    phase: str = "prepare"
    pointer_committed: bool = False
    quarantined_root: Path | None = None


class RuntimeTransactionController:
    """Coordinate final-path install, update, repair and rollback operations."""

    def __init__(
        self,
        *,
        adapter: UvAdapter,
        smoke_runner: RuntimeSmokeRunner | None = None,
        transaction_id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not hasattr(adapter, "materialize"):
            raise ManagedRuntimeError("transaction adapter must materialize releases")
        self._adapter = adapter
        self._smoke_runner = smoke_runner or SubprocessRuntimeSmokeRunner()
        self._transaction_id_factory = transaction_id_factory or (
            lambda: secrets.token_hex(16)
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def install(self, request: RuntimeTransactionRequest) -> RuntimeTransactionResult:
        """Install the first release and create generation one."""

        _require_runtime_request(request)
        with acquire_transaction_lease(request.paths):
            state = self._begin_request("install", request)
            try:
                self._preflight(state, request)
                state.phase = "prepare"
                pointer_path = state.install_root / "state" / "activation.txt"
                if pointer_path.exists() or pointer_path.is_symlink():
                    raise ManagedRuntimeError("install requires no active pointer")
                versions_root = state.install_root / "versions"
                _ensure_real_directory(versions_root, "versions root")
                final_root = versions_root / request.manifest.release_id
                _require_absent(final_root, "immutable version collision")
                receipt = self._materialize_release(state, request, final_root)
                state.phase = "activate"
                pointer = activate_release(
                    state.install_root, request.manifest.release_id
                )
                state.pointer_committed = True
                return self._complete(state, receipt, pointer)
            except Exception as exc:
                self._raise_failed(state, exc)

    def update(self, request: RuntimeTransactionRequest) -> RuntimeTransactionResult:
        """Build or reuse a valid inactive release, then atomically activate it."""

        _require_runtime_request(request)
        with acquire_transaction_lease(request.paths):
            state = self._begin_request("update", request)
            try:
                self._preflight(state, request)
                state.phase = "prepare"
                current = read_activation_pointer(state.install_root)
                load_install_receipt(state.install_root, current.active_release_id)
                if current.active_release_id == request.manifest.release_id:
                    raise ManagedRuntimeError("update release is already active")
                versions_root = state.install_root / "versions"
                _ensure_real_directory(versions_root, "versions root")
                final_root = versions_root / request.manifest.release_id
                state.version_root = final_root
                if final_root.exists() or final_root.is_symlink():
                    if final_root.is_symlink() or not final_root.is_dir():
                        raise ManagedRuntimeError(
                            "immutable version collision is unsafe"
                        )
                    try:
                        receipt = load_install_receipt(
                            state.install_root, request.manifest.release_id
                        )
                    except ManagedRuntimeError as exc:
                        raise ManagedRuntimeError(
                            "immutable version collision is incomplete; use repair"
                        ) from exc
                    if receipt.manifest_sha256 != request.manifest.sha256:
                        raise ManagedRuntimeError(
                            "existing receipt belongs to another manifest"
                        )
                else:
                    receipt = self._materialize_release(state, request, final_root)
                state.phase = "activate"
                pointer = activate_release(
                    state.install_root, request.manifest.release_id
                )
                state.pointer_committed = True
                return self._complete(state, receipt, pointer)
            except Exception as exc:
                self._raise_failed(state, exc)

    def repair(self, request: RuntimeTransactionRequest) -> RuntimeTransactionResult:
        """Retain one inactive failed release and rebuild its exact final path."""

        _require_runtime_request(request)
        with acquire_transaction_lease(request.paths):
            state = self._begin_request("repair", request)
            try:
                self._preflight(state, request)
                state.phase = "prepare"
                current = read_activation_pointer(state.install_root)
                load_install_receipt(state.install_root, current.active_release_id)
                if current.active_release_id == request.manifest.release_id:
                    raise ManagedRuntimeError("repair refuses the active release")
                versions_root = state.install_root / "versions"
                final_root = versions_root / request.manifest.release_id
                state.version_root = final_root
                if final_root.is_symlink() or not final_root.is_dir():
                    raise ManagedRuntimeError(
                        "repair requires an inactive real failed version directory"
                    )
                try:
                    load_install_receipt(
                        state.install_root, request.manifest.release_id
                    )
                except ManagedRuntimeError:
                    target_has_valid_receipt = False
                else:
                    target_has_valid_receipt = True
                if target_has_valid_receipt:
                    raise ManagedRuntimeError(
                        "repair target already has a valid receipt"
                    )
                quarantine = versions_root / (
                    f".staging-{request.manifest.release_id}-failed-"
                    f"{state.transaction_id}"
                )
                _require_absent(quarantine, "repair evidence collision")
                try:
                    os.rename(final_root, quarantine)
                    _fsync_directory(versions_root)
                except OSError as exc:
                    raise ManagedRuntimeError(
                        "cannot retain failed version evidence"
                    ) from exc
                state.quarantined_root = quarantine
                receipt = self._materialize_release(state, request, final_root)
                state.phase = "activate"
                pointer = activate_release(
                    state.install_root, request.manifest.release_id
                )
                state.pointer_committed = True
                return self._complete(state, receipt, pointer)
            except Exception as exc:
                self._raise_failed(state, exc)

    def rollback(self, request: RuntimeRollbackRequest) -> RuntimeTransactionResult:
        """Exchange active and previous receipt authorities under the same lease."""

        if not isinstance(request, RuntimeRollbackRequest):
            raise ManagedRuntimeError(
                "rollback request must be RuntimeRollbackRequest"
            )
        with acquire_transaction_lease(request.paths):
            state = self._begin_rollback(request)
            try:
                state.phase = "rollback"
                pointer = rollback_release(state.install_root)
                state.pointer_committed = True
                state.release_id = pointer.active_release_id
                state.version_root = (
                    state.install_root / "versions" / pointer.active_release_id
                )
                receipt = load_install_receipt(
                    state.install_root, pointer.active_release_id
                )
                return self._complete(state, receipt, pointer)
            except Exception as exc:
                self._raise_failed(state, exc)

    def _preflight(
        self,
        state: _TransactionState,
        request: RuntimeTransactionRequest,
    ) -> None:
        state.phase = "preflight"
        run_preflight(
            request.paths,
            request.manifest,
            snapshot=request.preflight_snapshot,
        ).require_passed()

    def _materialize_release(
        self,
        state: _TransactionState,
        request: RuntimeTransactionRequest,
        final_root: Path,
    ) -> InstallReceiptV1:
        state.version_root = final_root
        state.phase = "materialize"
        _require_absent(final_root, "immutable version collision")
        try:
            final_root.mkdir(mode=0o700)
            _fsync_directory(final_root.parent)
        except OSError as exc:
            raise ManagedRuntimeError("cannot create final version root") from exc
        materialized = self._adapter.materialize(
            MaterializationRequest(
                version_root=final_root,
                manifest=request.manifest,
                verified_artifacts=request.verified_artifacts,
            )
        )
        _atomic_bytes(
            final_root / "release-manifest.json",
            request.manifest.canonical_bytes,
            "release manifest",
        )
        python_executable, entrypoint = validate_materialization_result(
            final_root,
            request.manifest,
            materialized,
        )
        if request.manifest.channel == "stable":
            _require_stable_installer_entry(final_root)

        state.phase = "smoke"
        try:
            commands = _validated_smoke_results(
                self._smoke_runner.run(
                    python_executable,
                    entrypoint,
                    request.manifest.easyqc.version,
                    float(request.smoke_timeout_seconds),
                )
            )
        except Exception:
            completed_at = self._now()
            smoke_bytes = _smoke_report_bytes(
                request.manifest,
                (),
                completed_at,
                passed=False,
                reason_code="PROCESS_ERROR",
            )
            _atomic_bytes(
                final_root / "smoke-report.json",
                smoke_bytes,
                "smoke report",
            )
            raise
        completed_at = self._now()
        passed = _smoke_passed(
            commands, request.manifest.easyqc.version
        )
        smoke_bytes = _smoke_report_bytes(
            request.manifest,
            commands,
            completed_at,
            passed=passed,
            reason_code="PASS" if passed else "COMMAND_CONTRACT_MISMATCH",
        )
        _atomic_bytes(
            final_root / "smoke-report.json",
            smoke_bytes,
            "smoke report",
        )
        if not passed:
            raise ManagedRuntimeError("smoke command contract did not pass")

        state.phase = "receipt"
        return write_install_receipt(
            final_root,
            state.install_root,
            request.manifest,
            request.verified_artifacts,
            install_scope=request.paths.scope,
            smoke_report_bytes=smoke_bytes,
            smoke_completed_at_utc=completed_at,
            installed_at_utc=self._now(),
            controller_version=_CONTROLLER_VERSION,
        )

    def _begin_request(
        self,
        operation: str,
        request: RuntimeTransactionRequest,
    ) -> _TransactionState:
        return self._begin(
            operation=operation,
            paths=request.paths,
            source_revision=request.source_revision,
            release_id=request.manifest.release_id,
            manifest_sha256=request.manifest.sha256,
        )

    def _begin_rollback(
        self, request: RuntimeRollbackRequest
    ) -> _TransactionState:
        return self._begin(
            operation="rollback",
            paths=request.paths,
            source_revision=request.source_revision,
            release_id=None,
            manifest_sha256=None,
        )

    def _begin(
        self,
        *,
        operation: str,
        paths: ManagedRuntimePaths,
        source_revision: str,
        release_id: str | None,
        manifest_sha256: str | None,
    ) -> _TransactionState:
        try:
            transaction_id = self._transaction_id_factory()
        except Exception as exc:
            raise ManagedRuntimeError("cannot allocate transaction ID") from exc
        if not isinstance(transaction_id, str) or not _TRANSACTION_ID_PATTERN.fullmatch(
            transaction_id
        ):
            raise ManagedRuntimeError(
                "transaction ID must be 32 lowercase hexadecimal characters"
            )
        install_root = Path(paths.install_root)
        logs_root = install_root / "state" / "logs"
        _ensure_real_directory(logs_root, "transaction logs root")
        state = _TransactionState(
            operation=operation,
            transaction_id=transaction_id,
            report_path=logs_root / f"transaction-{transaction_id}.json",
            started_at_utc=self._now(),
            source_revision=source_revision,
            target_id=paths.target.target_id,
            scope=paths.scope,
            install_root=install_root,
            release_id=release_id,
            manifest_sha256=manifest_sha256,
            version_root=(
                install_root / "versions" / release_id if release_id else None
            ),
        )
        _require_absent(state.report_path, "transaction report collision")
        try:
            self._write_state(state, result="running")
        except Exception as exc:
            raise ManagedRuntimeError(
                "cannot write initial transaction report"
            ) from exc
        return state

    def _complete(
        self,
        state: _TransactionState,
        receipt: InstallReceiptV1,
        pointer: ActivationPointerV1,
    ) -> RuntimeTransactionResult:
        state.phase = "complete"
        self._write_state(state, result="passed", completed_at=self._now())
        if state.version_root is None:
            raise ManagedRuntimeError("transaction result has no version root")
        return RuntimeTransactionResult(
            operation=state.operation,
            transaction_id=state.transaction_id,
            version_root=state.version_root,
            receipt=receipt,
            pointer=pointer,
            report_path=state.report_path,
            quarantined_root=state.quarantined_root,
        )

    def _raise_failed(self, state: _TransactionState, exc: Exception) -> None:
        report_error: Exception | None = None
        try:
            self._write_state(
                state,
                result="failed",
                completed_at=self._now(),
                error=exc,
            )
        except Exception as write_exc:
            report_error = write_exc
        detail = _bounded_error(exc)
        suffix = (
            f"; transaction report write failed: {_bounded_error(report_error)}"
            if report_error is not None
            else f"; log={state.report_path}"
        )
        raise ManagedRuntimeError(
            f"{state.operation} {state.phase} phase failed: {detail}{suffix}"
        ) from exc

    def _write_state(
        self,
        state: _TransactionState,
        *,
        result: str,
        completed_at: str | None = None,
        error: Exception | None = None,
    ) -> None:
        record: dict[str, object] = {
            "schema_version": 1,
            "transaction_id": state.transaction_id,
            "operation": state.operation,
            "phase": state.phase,
            "result": result,
            "pointer_committed": state.pointer_committed,
            "release_id": state.release_id,
            "manifest_sha256": state.manifest_sha256,
            "source_revision": state.source_revision,
            "target_id": state.target_id,
            "scope": state.scope,
            "install_root": str(state.install_root),
            "version_root": (
                str(state.version_root) if state.version_root is not None else None
            ),
            "quarantined_root": (
                str(state.quarantined_root)
                if state.quarantined_root is not None
                else None
            ),
            "started_at_utc": state.started_at_utc,
            "completed_at_utc": completed_at,
            "error_type": type(error).__name__ if error is not None else None,
            "error_message_sha256": (
                hashlib.sha256(str(error).encode("utf-8")).hexdigest()
                if error is not None
                else None
            ),
        }
        _atomic_bytes(
            state.report_path,
            canonical_json_bytes(record),
            "transaction report",
        )

    def _now(self) -> str:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ManagedRuntimeError(
                "transaction clock must return a timezone-aware datetime"
            )
        return value.astimezone(timezone.utc).isoformat(
            timespec="microseconds"
        ).replace("+00:00", "Z")


def _require_runtime_request(request: RuntimeTransactionRequest) -> None:
    if not isinstance(request, RuntimeTransactionRequest):
        raise ManagedRuntimeError(
            "transaction request must be RuntimeTransactionRequest"
        )


def _require_source_revision(value: str) -> None:
    if not isinstance(value, str) or not _REVISION_PATTERN.fullmatch(value):
        raise ManagedRuntimeError(
            "source revision must be 40 lowercase hexadecimal characters"
        )


def _ensure_real_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        try:
            path.mkdir(mode=0o700)
            _fsync_directory(path.parent)
            metadata = path.lstat()
        except OSError as exc:
            raise ManagedRuntimeError(f"cannot create {label}") from exc
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must be a real directory")


def _require_absent(path: Path, label: str) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect {label}") from exc
    raise ManagedRuntimeError(f"{label}: {path.name}")


def _require_stable_installer_entry(version_root: Path) -> None:
    """Require the fixed installer entry before a stable receipt can exist."""

    installer = version_root / "app" / "easyqc_install.py"
    try:
        metadata = installer.lstat()
        resolved = installer.resolve(strict=True)
        resolved.relative_to(version_root.resolve(strict=True))
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise ManagedRuntimeError(
            "stable source is missing the contained easyqc_install.py entry"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ManagedRuntimeError(
            "stable easyqc_install.py must be a regular non-symlink file"
        )


def _smoke_passed(
    commands: tuple[RuntimeSmokeCommandResult, ...],
    expected_version: str,
) -> bool:
    if len(commands) != 2 or tuple(item.name for item in commands) != (
        "version",
        "qt-offscreen",
    ):
        return False
    version, qt = commands
    return (
        version.returncode == 0
        and version.stdout.strip() == expected_version
        and qt.returncode == 0
        and qt.stdout.strip().startswith("QT_OFFSCREEN_OK ")
    )


def _validated_smoke_results(
    value: object,
) -> tuple[RuntimeSmokeCommandResult, ...]:
    if not isinstance(value, tuple) or any(
        not isinstance(item, RuntimeSmokeCommandResult) for item in value
    ):
        raise ManagedRuntimeError(
            "smoke runner returned invalid command observations"
        )
    return value


def _smoke_report_bytes(
    manifest: ReleaseManifestV1,
    commands: tuple[RuntimeSmokeCommandResult, ...],
    completed_at: str,
    *,
    passed: bool,
    reason_code: str,
) -> bytes:
    observations = [
        {
            "name": item.name,
            "returncode": item.returncode,
            "stdout_sha256": hashlib.sha256(item.stdout.encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(item.stderr.encode("utf-8")).hexdigest(),
        }
        for item in commands
    ]
    return canonical_json_bytes(
        {
            "schema_version": 1,
            "release_id": manifest.release_id,
            "manifest_sha256": manifest.sha256,
            "contract_version": manifest.smoke_contract_version,
            "passed": passed,
            "completed_at_utc": completed_at,
            "reason_code": reason_code,
            "commands": observations,
        }
    )


def _atomic_bytes(path: Path, data: bytes, label: str) -> None:
    try:
        content = data.decode("utf-8")
        FileUtils.atomic_write(path, content, encoding="utf-8")
        _fsync_directory(path.parent)
    except (OSError, UnicodeDecodeError) as exc:
        raise ManagedRuntimeError(f"cannot write {label}") from exc


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
        raise ManagedRuntimeError("cannot durably synchronize directory") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _bounded_output(value: str) -> str:
    if len(value) <= _MAX_SMOKE_OUTPUT_CHARS:
        return value
    return value[:_MAX_SMOKE_OUTPUT_CHARS]


def _smoke_environment() -> dict[str, str]:
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
    )
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment.update(
        {
            "QT_QPA_PLATFORM": "offscreen",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment


def _bounded_error(exc: Exception | None) -> str:
    if exc is None:
        return "unknown error"
    value = " ".join(str(exc).split()) or type(exc).__name__
    return value[:_MAX_ERROR_CHARS]


__all__ = [
    "RuntimeRollbackRequest",
    "RuntimeSmokeCommandResult",
    "RuntimeTransactionController",
    "RuntimeTransactionRequest",
    "RuntimeTransactionResult",
    "SubprocessRuntimeSmokeRunner",
]
