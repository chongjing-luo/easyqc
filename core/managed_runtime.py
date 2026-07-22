"""Offline candidate transaction for the managed-runtime first throughput.

This module owns only explicit temporary-root candidate operations.  Network
acquisition, real uv calls, locking, PATH exposure, privileged installation and
retention policy remain outside FT-R2.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import subprocess
from typing import Protocol, Sequence

from models.managed_runtime import (
    ActivationPointerV1,
    InstallReceiptV1,
    ManagedRuntimeContractError,
    ReceiptPythonV1,
    ReceiptUvV1,
    ReleaseManifestV1,
    SmokeReceiptV1,
    canonical_json_bytes,
)
from utils.file_utils import FileUtils


_CONTROLLER_VERSION = "ft-r2"
_MAX_AUTHORITY_BYTES = 4 * 1024 * 1024


class ManagedRuntimeError(RuntimeError):
    """Raised when one managed-runtime transaction phase fails closed."""


@dataclass(frozen=True)
class VerifiedArtifact:
    """One local regular file whose exact size and SHA-256 were verified."""

    kind: str
    filename: str
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class VerifiedArtifactSet:
    """Complete verified physical input set for one manifest."""

    root: Path
    manifest_sha256: str
    artifact_manifest_sha256: str
    artifacts: tuple[VerifiedArtifact, ...]


@dataclass(frozen=True)
class MaterializationRequest:
    """Typed input given to exactly one injected materialization adapter."""

    version_root: Path
    manifest: ReleaseManifestV1
    verified_artifacts: VerifiedArtifactSet


@dataclass(frozen=True)
class MaterializationResult:
    """Contained executable paths produced by one materialization adapter."""

    python_executable: Path
    entrypoint: Path


class UvAdapter(Protocol):
    """One-purpose boundary for materializing a private version tree."""

    def materialize(self, request: MaterializationRequest) -> MaterializationResult:
        """Create one staged version and return its fixed launch paths."""


@dataclass(frozen=True)
class CandidateInstallRequest:
    """Explicit offline input for one isolated candidate installation."""

    install_root: Path
    manifest: ReleaseManifestV1
    artifact_root: Path
    expected_target_id: str
    smoke_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not isinstance(self.install_root, Path):
            raise ManagedRuntimeError("install root must be a pathlib.Path")
        if not isinstance(self.artifact_root, Path):
            raise ManagedRuntimeError("artifact root must be a pathlib.Path")
        if not isinstance(self.expected_target_id, str) or not self.expected_target_id:
            raise ManagedRuntimeError("expected target ID is required")
        if (
            isinstance(self.smoke_timeout_seconds, bool)
            or not isinstance(self.smoke_timeout_seconds, (int, float))
            or self.smoke_timeout_seconds <= 0
        ):
            raise ManagedRuntimeError("smoke timeout must be positive")


def verify_artifact_set(
    manifest: ReleaseManifestV1,
    artifact_root: Path,
    *,
    expected_target_id: str,
) -> VerifiedArtifactSet:
    """Verify all local manifest inputs before any staging write.

    Input: one validated manifest, one explicit artifact directory and one
    expected target ID.  Output: immutable verified identities.  Side effects:
    file reads only.  Errors identify target, missing, symlink, size or hash
    failures.  Acquisition is a separate future contract.
    """

    if manifest.target.target_id != expected_target_id:
        raise ManagedRuntimeError(
            "artifact verification target mismatch: "
            f"expected {expected_target_id}, got {manifest.target.target_id}"
        )
    root = _existing_real_directory(artifact_root, "artifact root")
    identities = [
        ("uv", manifest.uv.filename, manifest.uv.size_bytes, manifest.uv.sha256),
        (
            "python",
            manifest.python.filename,
            manifest.python.size_bytes,
            manifest.python.sha256,
        ),
        *(
            (item.kind, item.filename, item.size_bytes, item.sha256)
            for item in manifest.artifacts
        ),
    ]
    verified: list[VerifiedArtifact] = []
    for kind, filename, expected_size, expected_hash in identities:
        candidate = root / filename
        try:
            metadata = candidate.lstat()
        except FileNotFoundError as exc:
            raise ManagedRuntimeError(f"missing artifact: {filename}") from exc
        except OSError as exc:
            raise ManagedRuntimeError(f"cannot inspect artifact {filename}: {exc}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ManagedRuntimeError(f"artifact is a symlink: {filename}")
        if not stat.S_ISREG(metadata.st_mode):
            raise ManagedRuntimeError(f"artifact is not a regular file: {filename}")
        if metadata.st_size != expected_size:
            raise ManagedRuntimeError(
                f"artifact size mismatch for {filename}: "
                f"expected {expected_size}, got {metadata.st_size}"
            )
        actual_hash = _sha256_regular_file(candidate, metadata, filename)
        if actual_hash != expected_hash:
            raise ManagedRuntimeError(
                f"artifact hash mismatch for {filename}: "
                f"expected {expected_hash}, got {actual_hash}"
            )
        verified.append(
            VerifiedArtifact(
                kind=kind,
                filename=filename,
                path=candidate,
                size_bytes=expected_size,
                sha256=actual_hash,
            )
        )
    manifest_rows = [
        {
            "kind": item.kind,
            "filename": item.filename,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
        }
        for item in sorted(verified, key=lambda item: item.filename)
    ]
    artifact_manifest_sha256 = hashlib.sha256(
        canonical_json_bytes(manifest_rows)
    ).hexdigest()
    return VerifiedArtifactSet(
        root=root,
        manifest_sha256=manifest.sha256,
        artifact_manifest_sha256=artifact_manifest_sha256,
        artifacts=tuple(verified),
    )


def install_candidate(
    request: CandidateInstallRequest, adapter: UvAdapter
) -> InstallReceiptV1:
    """Install, smoke, receipt and atomically activate one candidate.

    Input: one explicit candidate request and one injected adapter.  Output: a
    validated immutable receipt.  Side effects are confined to ``versions``
    and ``state`` below the requested root.  Any pre-activation failure leaves
    the previous activation bytes unchanged and retains staging evidence.
    Stable/system/PATH/network behavior is a separate Stage 6 contract.
    """

    manifest = request.manifest
    if manifest.channel != "candidate":
        raise ManagedRuntimeError("FT-R2 accepts candidate manifests only")
    verified = verify_artifact_set(
        manifest,
        request.artifact_root,
        expected_target_id=request.expected_target_id,
    )
    install_root = _candidate_install_root(request.install_root)
    versions_root = install_root / "versions"
    state_root = install_root / "state"
    final_root = versions_root / manifest.release_id
    if final_root.exists() or final_root.is_symlink():
        raise ManagedRuntimeError(
            f"immutable version collision: {manifest.release_id}"
        )

    _ensure_real_directory(install_root, "install root")
    _ensure_real_directory(versions_root, "versions root")
    _ensure_real_directory(state_root, "state root")
    transaction_id = secrets.token_hex(8)
    staging_root = versions_root / (
        f".staging-{manifest.release_id}-{transaction_id}"
    )
    try:
        staging_root.mkdir()
    except OSError as exc:
        raise ManagedRuntimeError(f"staging creation failed: {exc}") from exc

    manifest_path = staging_root / "release-manifest.json"
    _atomic_write_durable(manifest_path, manifest.canonical_bytes, "manifest")
    materialization_request = MaterializationRequest(
        version_root=staging_root,
        manifest=manifest,
        verified_artifacts=verified,
    )
    try:
        materialized = adapter.materialize(materialization_request)
    except Exception as exc:
        raise ManagedRuntimeError(f"materialization phase failed: {exc}") from exc
    python_executable, entrypoint = validate_materialization_result(
        staging_root, manifest, materialized
    )

    try:
        process = subprocess.run(
            [str(python_executable), str(entrypoint), "--version"],
            cwd=str(staging_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=request.smoke_timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        report = _smoke_report(
            manifest,
            passed=False,
            completed_at=_utc_now(),
            reason_code="PROCESS_ERROR",
            returncode=None,
            stdout="",
            stderr=str(exc),
        )
        _atomic_write_durable(
            staging_root / "smoke-report.json",
            canonical_json_bytes(report),
            "smoke report",
        )
        raise ManagedRuntimeError(f"smoke phase failed: {exc}") from exc

    completed_at = _utc_now()
    smoke_passed = (
        process.returncode == 0
        and process.stdout.strip() == manifest.easyqc.version
    )
    reason_code = "PASS" if smoke_passed else "VERSION_OR_EXIT_MISMATCH"
    report = _smoke_report(
        manifest,
        passed=smoke_passed,
        completed_at=completed_at,
        reason_code=reason_code,
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
    )
    report_bytes = canonical_json_bytes(report)
    report_path = staging_root / "smoke-report.json"
    _atomic_write_durable(report_path, report_bytes, "smoke report")
    if not smoke_passed:
        raise ManagedRuntimeError(
            "smoke phase failed: expected exit 0 and exact version "
            f"{manifest.easyqc.version!r}; got exit {process.returncode}"
        )

    receipt = write_install_receipt(
        staging_root,
        install_root,
        manifest,
        verified,
        install_scope="user",
        smoke_report_bytes=report_bytes,
        smoke_completed_at_utc=completed_at,
        installed_at_utc=_utc_now(),
        controller_version=_CONTROLLER_VERSION,
        expected_release_id=manifest.release_id,
    )

    if final_root.exists() or final_root.is_symlink():
        raise ManagedRuntimeError(
            f"immutable version collision: {manifest.release_id}"
        )
    try:
        os.rename(staging_root, final_root)
        _fsync_parent(versions_root)
    except OSError as exc:
        raise ManagedRuntimeError(f"version finalization failed: {exc}") from exc

    activate_release(install_root, manifest.release_id)
    return receipt


def read_activation_pointer(install_root: Path) -> ActivationPointerV1:
    """Read and strictly validate the sole activation authority."""

    root = _candidate_install_root(install_root)
    pointer_path = root / "state" / "activation.txt"
    try:
        raw = _read_regular_bytes(pointer_path, "activation pointer")
        return ActivationPointerV1.from_bytes(raw)
    except (ManagedRuntimeContractError, ManagedRuntimeError) as exc:
        raise ManagedRuntimeError(f"invalid activation pointer: {exc}") from exc


def load_install_receipt(
    install_root: Path, release_id: str
) -> InstallReceiptV1:
    """Load one receipt only after revalidating its stored manifest identity."""

    root = _candidate_install_root(install_root)
    try:
        pointer_identity = ActivationPointerV1(release_id, None, 0)
    except ManagedRuntimeContractError as exc:
        raise ManagedRuntimeError(f"invalid receipt release ID: {exc}") from exc
    version_root = root / "versions" / pointer_identity.active_release_id
    _, receipt = _load_version_authority(version_root, root)
    return receipt


def rollback(install_root: Path) -> ActivationPointerV1:
    """Validate and atomically exchange active/previous release authority."""

    root = _candidate_install_root(install_root)
    pointer_path = root / "state" / "activation.txt"
    original_bytes = _read_regular_bytes(pointer_path, "activation pointer")
    try:
        current = ActivationPointerV1.from_bytes(original_bytes)
    except ManagedRuntimeContractError as exc:
        raise ManagedRuntimeError(f"invalid activation pointer: {exc}") from exc
    if current.previous_release_id is None:
        raise ManagedRuntimeError("rollback requires a previous receipt")
    _load_version_authority(root / "versions" / current.active_release_id, root)
    _load_version_authority(root / "versions" / current.previous_release_id, root)
    next_pointer = ActivationPointerV1(
        active_release_id=current.previous_release_id,
        previous_release_id=current.active_release_id,
        generation=current.generation + 1,
    )
    _atomic_write_durable(pointer_path, next_pointer.to_bytes(), "activation pointer")
    return next_pointer


def launch_active(
    install_root: Path,
    argv: Sequence[str],
    *,
    timeout_seconds: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Launch the validated active version in one fresh child process.

    Input: one install root and one argument vector.  Output: captured completed
    process.  Side effect: one child process, always with ``shell=False``.
    Invalid pointer/receipt/manifest/path or child failure is explicit.  Stable
    PATH wrappers are a separate future contract.
    """

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise ManagedRuntimeError("launch timeout must be positive")
    if isinstance(argv, (str, bytes)) or any(
        not isinstance(argument, str) or "\x00" in argument for argument in argv
    ):
        raise ManagedRuntimeError("launch argv must be a sequence of safe strings")
    root = _candidate_install_root(install_root)
    pointer = read_activation_pointer(root)
    version_root = root / "versions" / pointer.active_release_id
    manifest, _ = _load_version_authority(version_root, root)
    python_executable, entrypoint = _fixed_launch_paths(version_root, manifest)
    _require_contained_launch_file(
        python_executable,
        version_root,
        "active Python executable",
        allow_symlink=True,
    )
    _require_contained_launch_file(
        entrypoint,
        version_root,
        "active entrypoint",
        allow_symlink=False,
    )
    try:
        process = subprocess.run(
            [str(python_executable), str(entrypoint), *tuple(argv)],
            cwd=str(version_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ManagedRuntimeError(f"active launch failed: {exc}") from exc
    if process.returncode != 0:
        raise ManagedRuntimeError(
            f"active launch failed with exit code {process.returncode}"
        )
    return process


def activate_release(install_root: Path, release_id: str) -> ActivationPointerV1:
    """Validate one receipt-backed release and atomically make it active."""

    install_root = _candidate_install_root(install_root)
    try:
        identity = ActivationPointerV1(release_id, None, 0)
    except ManagedRuntimeContractError as exc:
        raise ManagedRuntimeError(f"invalid activation release ID: {exc}") from exc
    version_root = install_root / "versions" / identity.active_release_id
    _load_version_authority(version_root, install_root)
    pointer_path = install_root / "state" / "activation.txt"
    if pointer_path.exists() or pointer_path.is_symlink():
        original_bytes = _read_regular_bytes(pointer_path, "activation pointer")
        try:
            current = ActivationPointerV1.from_bytes(original_bytes)
        except ManagedRuntimeContractError as exc:
            raise ManagedRuntimeError(f"invalid activation pointer: {exc}") from exc
        _load_version_authority(
            install_root / "versions" / current.active_release_id, install_root
        )
        if current.active_release_id == identity.active_release_id:
            raise ManagedRuntimeError("release is already active")
        pointer = ActivationPointerV1(
            active_release_id=identity.active_release_id,
            previous_release_id=current.active_release_id,
            generation=current.generation + 1,
        )
    else:
        pointer = ActivationPointerV1(
            active_release_id=identity.active_release_id,
            previous_release_id=None,
            generation=1,
        )
    _atomic_write_durable(pointer_path, pointer.to_bytes(), "activation pointer")
    return pointer


def _load_version_authority(
    version_root: Path,
    install_root: Path,
    *,
    expected_release_id: str | None = None,
) -> tuple[ReleaseManifestV1, InstallReceiptV1]:
    if version_root.is_symlink() or not version_root.is_dir():
        raise ManagedRuntimeError(f"receipt-backed version is missing: {version_root.name}")
    try:
        version_root.resolve(strict=True).relative_to(install_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise ManagedRuntimeError("receipt-backed version escapes install root") from exc

    manifest_path = version_root / "release-manifest.json"
    receipt_path = version_root / "install-receipt.json"
    try:
        manifest = ReleaseManifestV1.from_canonical_bytes(
            _read_regular_bytes(manifest_path, "stored manifest")
        )
    except (ManagedRuntimeContractError, ManagedRuntimeError) as exc:
        raise ManagedRuntimeError(f"invalid stored manifest: {exc}") from exc
    path_release_id = expected_release_id or version_root.name
    if manifest.release_id != path_release_id:
        raise ManagedRuntimeError("stored manifest release ID does not match version path")
    try:
        receipt = InstallReceiptV1.from_canonical_bytes(
            _read_regular_bytes(receipt_path, "install receipt")
        )
        receipt.validate_for(manifest, str(install_root))
    except (ManagedRuntimeContractError, ManagedRuntimeError) as exc:
        raise ManagedRuntimeError(f"invalid install receipt: {exc}") from exc
    expected_artifact_manifest = _manifest_artifact_set_sha256(manifest)
    if receipt.artifact_manifest_sha256 != expected_artifact_manifest:
        raise ManagedRuntimeError(
            "invalid install receipt: artifact manifest SHA-256 mismatch"
        )
    smoke_path = version_root / "smoke-report.json"
    smoke_bytes = _read_regular_bytes(smoke_path, "smoke report")
    if hashlib.sha256(smoke_bytes).hexdigest() != receipt.smoke.report_sha256:
        raise ManagedRuntimeError("invalid install receipt: smoke report hash mismatch")
    return manifest, receipt


def validate_materialization_result(
    version_root: Path,
    manifest: ReleaseManifestV1,
    result: MaterializationResult,
) -> tuple[Path, Path]:
    """Validate the adapter's two fixed launch paths inside one version root.

    The environment Python may be a symlink because uv creates that form on
    supported POSIX targets.  Its fully resolved regular file must still live
    inside the same immutable version root.  The application entrypoint remains
    a regular non-symlink file.
    """

    if not isinstance(result, MaterializationResult):
        raise ManagedRuntimeError("adapter returned an invalid materialization result")
    expected_python, expected_entrypoint = _fixed_launch_paths(version_root, manifest)
    resolved_root = version_root.resolve(strict=True)
    returned: list[tuple[Path, Path, str, bool]] = [
        (result.python_executable, expected_python, "Python executable", True),
        (result.entrypoint, expected_entrypoint, "entrypoint", False),
    ]
    for candidate, expected, label, allow_symlink in returned:
        if not isinstance(candidate, Path):
            raise ManagedRuntimeError(f"adapter {label} path is not pathlib.Path")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise ManagedRuntimeError(
                f"adapter {label} path is outside version root"
            ) from exc
        if candidate != expected:
            raise ManagedRuntimeError(f"adapter {label} path is not the fixed launch path")
        _require_contained_launch_file(
            candidate,
            version_root,
            f"adapter {label}",
            allow_symlink=allow_symlink,
        )
    return expected_python, expected_entrypoint


def write_install_receipt(
    version_root: Path,
    install_root: Path,
    manifest: ReleaseManifestV1,
    verified_artifacts: VerifiedArtifactSet,
    *,
    install_scope: str,
    smoke_report_bytes: bytes,
    smoke_completed_at_utc: str,
    installed_at_utc: str,
    controller_version: str,
    expected_release_id: str | None = None,
) -> InstallReceiptV1:
    """Construct, atomically write and revalidate one install receipt."""

    root = _candidate_install_root(install_root)
    if not isinstance(version_root, Path) or not version_root.is_absolute():
        raise ManagedRuntimeError("version root must be an explicit absolute path")
    if version_root.is_symlink() or not version_root.is_dir():
        raise ManagedRuntimeError("version root must be an existing real directory")
    try:
        resolved_version = version_root.resolve(strict=True)
        resolved_versions_root = (root / "versions").resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ManagedRuntimeError("cannot resolve receipt version root") from exc
    if resolved_version.parent != resolved_versions_root:
        raise ManagedRuntimeError("version root must be directly below versions")
    path_release_id = expected_release_id or manifest.release_id
    if (
        expected_release_id is None
        and version_root.name != manifest.release_id
    ):
        raise ManagedRuntimeError("version path does not match manifest release ID")
    if verified_artifacts.manifest_sha256 != manifest.sha256:
        raise ManagedRuntimeError("verified manifest identity does not match receipt")
    if (
        verified_artifacts.artifact_manifest_sha256
        != _manifest_artifact_set_sha256(manifest)
    ):
        raise ManagedRuntimeError("verified artifact identity does not match receipt")
    if not isinstance(smoke_report_bytes, bytes):
        raise ManagedRuntimeError("smoke report must be bytes")
    stored_smoke = _read_regular_bytes(
        version_root / "smoke-report.json", "smoke report"
    )
    if stored_smoke != smoke_report_bytes:
        raise ManagedRuntimeError("stored smoke report bytes changed before receipt")
    receipt_path = version_root / "install-receipt.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        raise ManagedRuntimeError("install receipt already exists")
    receipt = InstallReceiptV1(
        schema_version=1,
        release_id=manifest.release_id,
        target=manifest.target,
        install_scope=install_scope,
        install_root=str(root),
        manifest_sha256=manifest.sha256,
        source_sha256=manifest.easyqc.source_sha256,
        uv=ReceiptUvV1(version=manifest.uv.version, sha256=manifest.uv.sha256),
        python=ReceiptPythonV1(
            version=manifest.python.version,
            build=manifest.python.build,
            key=manifest.python.key,
            payload_sha256=manifest.python.sha256,
        ),
        lock_sha256=manifest.lock.sha256,
        artifact_manifest_sha256=verified_artifacts.artifact_manifest_sha256,
        installed_file_set_sha256=_installed_file_set_sha256(version_root),
        smoke=SmokeReceiptV1(
            contract_version=manifest.smoke_contract_version,
            passed=True,
            completed_at_utc=smoke_completed_at_utc,
            report_sha256=hashlib.sha256(smoke_report_bytes).hexdigest(),
        ),
        installed_at_utc=installed_at_utc,
        controller_version=controller_version,
    )
    _atomic_write_durable(receipt_path, receipt.canonical_bytes, "install receipt")
    _, stored_receipt = _load_version_authority(
        version_root,
        root,
        expected_release_id=path_release_id,
    )
    return stored_receipt


def _fixed_launch_paths(
    version_root: Path, manifest: ReleaseManifestV1
) -> tuple[Path, Path]:
    if manifest.target.os == "windows":
        python_executable = version_root / "env" / "Scripts" / "python.exe"
    else:
        python_executable = version_root / "env" / "bin" / "python"
    entrypoint_parts = PurePosixPath(manifest.easyqc.entrypoint).parts
    entrypoint = version_root.joinpath("app", *entrypoint_parts)
    return python_executable, entrypoint


def _smoke_report(
    manifest: ReleaseManifestV1,
    *,
    passed: bool,
    completed_at: str,
    reason_code: str,
    returncode: int | None,
    stdout: str,
    stderr: str,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "release_id": manifest.release_id,
        "manifest_sha256": manifest.sha256,
        "contract_version": manifest.smoke_contract_version,
        "passed": passed,
        "completed_at_utc": completed_at,
        "reason_code": reason_code,
        "returncode": returncode,
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr.encode("utf-8")).hexdigest(),
    }


def _manifest_artifact_set_sha256(manifest: ReleaseManifestV1) -> str:
    rows = [
        {
            "kind": "uv",
            "filename": manifest.uv.filename,
            "size_bytes": manifest.uv.size_bytes,
            "sha256": manifest.uv.sha256,
        },
        {
            "kind": "python",
            "filename": manifest.python.filename,
            "size_bytes": manifest.python.size_bytes,
            "sha256": manifest.python.sha256,
        },
        *(
            {
                "kind": item.kind,
                "filename": item.filename,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
            }
            for item in manifest.artifacts
        ),
    ]
    rows.sort(key=lambda item: str(item["filename"]))
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def _installed_file_set_sha256(version_root: Path) -> str:
    rows: list[dict[str, object]] = []
    resolved_root = version_root.resolve(strict=True)
    for path in sorted(version_root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(version_root).as_posix()
        metadata = path.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path)
            try:
                path.resolve(strict=True).relative_to(resolved_root)
            except (OSError, ValueError) as exc:
                raise ManagedRuntimeError(
                    f"installed symlink escapes version root: {relative}"
                ) from exc
            rows.append({"path": relative, "type": "symlink", "target": target})
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise ManagedRuntimeError(f"unsupported installed file type: {relative}")
        digest = _sha256_regular_file(path, metadata, relative)
        rows.append(
            {
                "path": relative,
                "type": "file",
                "size_bytes": metadata.st_size,
                "sha256": digest,
            }
        )
    return hashlib.sha256(canonical_json_bytes(rows)).hexdigest()


def _sha256_regular_file(path: Path, before: os.stat_result, label: str) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    digest = hashlib.sha256()
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ManagedRuntimeError(f"not a regular file while hashing: {label}")
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ):
            raise ManagedRuntimeError(f"file changed before hashing: {label}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    except ManagedRuntimeError:
        raise
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot hash regular file {label}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"file disappeared after hashing: {label}") from exc
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ):
        raise ManagedRuntimeError(f"file changed while hashing: {label}")
    return digest.hexdigest()


def _read_regular_bytes(path: Path, label: str) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ManagedRuntimeError(f"{label} is missing") from exc
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect {label}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} is not a regular non-symlink file")
    if metadata.st_size > _MAX_AUTHORITY_BYTES:
        raise ManagedRuntimeError(f"{label} exceeds the authority size limit")
    descriptor: int | None = None
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ManagedRuntimeError(f"{label} is not a regular file while open")
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
        ):
            raise ManagedRuntimeError(f"{label} changed before it was read")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_AUTHORITY_BYTES:
                raise ManagedRuntimeError(f"{label} exceeds the authority size limit")
            chunks.append(chunk)
        data = b"".join(chunks)
    except ManagedRuntimeError:
        raise
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot read {label}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"{label} disappeared after it was read") from exc
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    ):
        raise ManagedRuntimeError(f"{label} changed while it was read")
    return data


def _atomic_write_durable(path: Path, data: bytes, label: str) -> None:
    try:
        text = data.decode("ascii" if label == "activation pointer" else "utf-8")
    except UnicodeDecodeError as exc:
        raise ManagedRuntimeError(f"{label} bytes have an invalid encoding") from exc
    try:
        FileUtils.atomic_write(
            path,
            text,
            encoding=("ascii" if label == "activation pointer" else "utf-8"),
        )
        _fsync_parent(path.parent)
    except OSError as exc:
        raise ManagedRuntimeError(f"atomic {label} write failed: {exc}") from exc


def _fsync_parent(parent: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor: int | None = None
    try:
        descriptor = os.open(parent, flags)
        os.fsync(descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _candidate_install_root(path: Path) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ManagedRuntimeError("candidate install root must be an explicit absolute path")
    if path.exists() and path.is_symlink():
        raise ManagedRuntimeError("candidate install root must not be a symlink")
    try:
        return path.resolve(strict=False)
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot resolve candidate install root: {exc}") from exc


def _existing_real_directory(path: Path, label: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ManagedRuntimeError(f"{label} must be an explicit absolute path")
    if path.is_symlink() or not path.is_dir():
        raise ManagedRuntimeError(f"{label} must be an existing real directory")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot resolve {label}: {exc}") from exc


def _ensure_real_directory(path: Path, label: str) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_dir():
            raise ManagedRuntimeError(f"{label} must be a real directory")
        return
    try:
        path.mkdir()
        _fsync_parent(path.parent)
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot create {label}: {exc}") from exc


def _require_regular_nonsymlink(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"{label} is missing: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must be a regular non-symlink file")


def _require_contained_launch_file(
    path: Path,
    version_root: Path,
    label: str,
    *,
    allow_symlink: bool,
) -> None:
    try:
        metadata = path.lstat()
        resolved_root = version_root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
        resolved_metadata = resolved.stat()
    except (OSError, ValueError) as exc:
        raise ManagedRuntimeError(
            f"{label} is missing or escapes the version root"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        if not allow_symlink:
            raise ManagedRuntimeError(f"{label} must not be a symlink")
    elif not stat.S_ISREG(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must be a regular file")
    if not stat.S_ISREG(resolved_metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must resolve to a regular file")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


__all__ = [
    "CandidateInstallRequest",
    "ManagedRuntimeError",
    "MaterializationRequest",
    "MaterializationResult",
    "UvAdapter",
    "VerifiedArtifact",
    "VerifiedArtifactSet",
    "activate_release",
    "install_candidate",
    "launch_active",
    "load_install_receipt",
    "read_activation_pointer",
    "rollback",
    "validate_materialization_result",
    "verify_artifact_set",
    "write_install_receipt",
]
