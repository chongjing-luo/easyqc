"""Trusted-manifest CLI orchestration for the EasyQC managed runtime."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
from typing import TextIO

from core.managed_runtime import (
    ManagedRuntimeError,
    UvAdapter,
    VerifiedArtifactSet,
    load_install_receipt,
    read_activation_pointer,
)
from core.managed_runtime_acquisition import (
    HttpsTransport,
    OfflinePayloadRequest,
    OnlineAcquisitionRequest,
    acquire_online,
    inspect_offline_payload,
)
from core.managed_runtime_launcher import (
    WindowsPathAdapter,
    install_stable_launchers,
    inspect_stable_launchers,
)
from core.managed_runtime_platform import (
    HostPreflightSnapshot,
    ManagedRuntimePaths,
    detect_native_runtime_target,
    inspect_host,
    resolve_runtime_paths,
    run_preflight,
)
from core.managed_runtime_transaction import (
    RuntimeRollbackRequest,
    RuntimeSmokeRunner,
    RuntimeTransactionController,
    RuntimeTransactionRequest,
    RuntimeTransactionResult,
)
from core.managed_runtime_uv import RealUvAdapter
from models.managed_runtime import (
    ManagedRuntimeContractError,
    ReleaseManifestV1,
    RuntimeTargetV1,
    canonical_json_bytes,
)


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024


@dataclass
class InstallerContext:
    """Narrow platform/process seams used by production and contract tests."""

    target_detector: Callable[[], RuntimeTargetV1] = detect_native_runtime_target
    path_resolver: Callable[[RuntimeTargetV1, str], ManagedRuntimePaths] | None = None
    snapshot_provider: Callable[[ManagedRuntimePaths], HostPreflightSnapshot] = inspect_host
    adapter: UvAdapter | None = None
    smoke_runner: RuntimeSmokeRunner | None = None
    transaction_id_factory: Callable[[], str] | None = None
    clock: Callable[[], datetime] | None = None
    posix_exposure_root: Path | None = None
    environment_path: str | None = None
    environment: Mapping[str, str] | None = None
    user_data_root: str | os.PathLike[str] | None = None
    windows_path_adapter: WindowsPathAdapter | None = None
    transport: HttpsTransport | None = None
    source_revision: str | None = None

    def __post_init__(self) -> None:
        if self.source_revision is not None and not _SOURCE_REVISION_PATTERN.fullmatch(
            self.source_revision
        ):
            raise ManagedRuntimeError(
                "installer source revision must be 40 lowercase hexadecimal characters"
            )


@dataclass(frozen=True)
class InstallerStatusV1:
    """Deterministic read-only projection of installer authority state."""

    installation_state: str
    installed: bool
    target_id: str
    scope: str
    install_root: str
    launcher_ready: bool
    path_contains_exposure: bool
    launcher_issues: tuple[str, ...]
    active_release_id: str | None
    previous_release_id: str | None
    generation: int | None
    valid_release_ids: tuple[str, ...]
    incomplete_release_ids: tuple[str, ...]
    retained_failure_names: tuple[str, ...]

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "installation_state": self.installation_state,
            "installed": self.installed,
            "target_id": self.target_id,
            "scope": self.scope,
            "install_root": self.install_root,
            "launcher_ready": self.launcher_ready,
            "path_contains_exposure": self.path_contains_exposure,
            "launcher_issues": list(self.launcher_issues),
            "active_release_id": self.active_release_id,
            "previous_release_id": self.previous_release_id,
            "generation": self.generation,
            "valid_release_ids": list(self.valid_release_ids),
            "incomplete_release_ids": list(self.incomplete_release_ids),
            "retained_failure_names": list(self.retained_failure_names),
        }


def build_installer_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="easyqc-install",
        description="Install and maintain the EasyQC managed runtime.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("install", "update", "repair"):
        mutation = commands.add_parser(command)
        mutation.add_argument("--manifest", required=True, type=Path)
        mutation.add_argument("--manifest-sha256", required=True)
        mutation.add_argument("--offline-payload", type=Path)
        mutation.add_argument(
            "--scope", choices=("user", "system"), default="user"
        )
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--scope", choices=("user", "system"), default="user")
    status = commands.add_parser("status")
    status.add_argument("--scope", choices=("user", "system"), default="user")
    status.add_argument("--json", action="store_true", dest="as_json")
    return parser


def parse_installer_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    return build_installer_parser().parse_args(argv)


def load_trusted_manifest(path: Path, trusted_sha256: str) -> ReleaseManifestV1:
    """Authenticate exact regular canonical bytes before trusting their schema."""

    if not isinstance(path, Path):
        raise ManagedRuntimeError("manifest path must be pathlib.Path")
    if not isinstance(trusted_sha256, str) or not _SHA256_PATTERN.fullmatch(
        trusted_sha256
    ):
        raise ManagedRuntimeError(
            "trusted manifest SHA-256 must be 64 lowercase hexadecimal characters"
        )
    data = _read_regular_bytes(path, "release manifest", _MAX_MANIFEST_BYTES)
    actual = hashlib.sha256(data).hexdigest()
    if actual != trusted_sha256:
        raise ManagedRuntimeError(
            "trusted manifest SHA-256 mismatch: "
            f"expected {trusted_sha256}, got {actual}"
        )
    try:
        manifest = ReleaseManifestV1.from_canonical_bytes(data)
    except ManagedRuntimeContractError as exc:
        raise ManagedRuntimeError(f"invalid canonical release manifest: {exc}") from exc
    if manifest.channel != "stable":
        raise ManagedRuntimeError(
            "production installer accepts stable release manifests only"
        )
    if manifest.easyqc.entrypoint != "easyqc.py":
        raise ManagedRuntimeError(
            "stable release manifest must use the fixed easyqc.py entrypoint"
        )
    return manifest


def inspect_installer_status(
    paths: ManagedRuntimePaths,
    *,
    context: InstallerContext | None = None,
) -> InstallerStatusV1:
    """Return strict status while treating absent first-install state as normal."""

    active_context = context or InstallerContext()
    root = Path(paths.install_root)
    if not root.exists() and not root.is_symlink():
        return InstallerStatusV1(
            "not-installed",
            False,
            paths.target.target_id,
            paths.scope,
            paths.install_root,
            False,
            False,
            ("scope state is missing",),
            None,
            None,
            None,
            (),
            (),
            (),
        )
    _require_real_directory(root, "install root")
    launcher = inspect_stable_launchers(
        paths,
        posix_exposure_root=active_context.posix_exposure_root,
        environment_path=active_context.environment_path,
        windows_path_adapter=active_context.windows_path_adapter,
    )
    pointer_path = Path(paths.state_root) / "activation.txt"
    pointer = None
    if pointer_path.exists() or pointer_path.is_symlink():
        pointer = read_activation_pointer(root)

    valid: list[str] = []
    incomplete: list[str] = []
    retained: list[str] = []
    versions_root = root / "versions"
    if versions_root.exists() or versions_root.is_symlink():
        _require_real_directory(versions_root, "versions root")
        try:
            children = sorted(versions_root.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise ManagedRuntimeError("cannot enumerate installed versions") from exc
        for child in children:
            if child.is_symlink():
                raise ManagedRuntimeError(
                    f"version entry must not be a symlink: {child.name}"
                )
            if child.name.startswith(".staging-"):
                if not child.is_dir():
                    raise ManagedRuntimeError(
                        f"retained failure is not a directory: {child.name}"
                    )
                retained.append(child.name)
                continue
            if not child.is_dir():
                raise ManagedRuntimeError(
                    f"version entry is not a directory: {child.name}"
                )
            receipt_path = child / "install-receipt.json"
            if not receipt_path.exists() and not receipt_path.is_symlink():
                incomplete.append(child.name)
                continue
            load_install_receipt(root, child.name)
            valid.append(child.name)

    if pointer is not None:
        load_install_receipt(root, pointer.active_release_id)
        if pointer.previous_release_id is not None:
            load_install_receipt(root, pointer.previous_release_id)
    installed = pointer is not None
    state = "ready" if installed and launcher.ready else "incomplete"
    return InstallerStatusV1(
        state,
        installed,
        paths.target.target_id,
        paths.scope,
        paths.install_root,
        launcher.ready,
        launcher.paths.path_contains_exposure,
        launcher.issues,
        pointer.active_release_id if pointer else None,
        pointer.previous_release_id if pointer else None,
        pointer.generation if pointer else None,
        tuple(valid),
        tuple(incomplete),
        tuple(retained),
    )


def main(
    argv: list[str] | None = None,
    *,
    context: InstallerContext | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Map installer argv to deterministic stdout/stderr and an exit code."""

    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    args = parse_installer_arguments(argv)
    active_context = context or InstallerContext()
    try:
        target = active_context.target_detector()
        if not isinstance(target, RuntimeTargetV1):
            raise ManagedRuntimeError(
                "native target detector did not return RuntimeTargetV1"
            )
        paths = _resolve_paths(active_context, target, args.scope)
        if args.command == "status":
            status = inspect_installer_status(paths, context=active_context)
            if args.as_json:
                output.write(canonical_json_bytes(status.to_json_object()).decode("utf-8"))
            else:
                output.write(_human_status(status))
            return 0
        if args.command == "rollback":
            inspection = inspect_stable_launchers(
                paths,
                posix_exposure_root=active_context.posix_exposure_root,
                environment_path=active_context.environment_path,
                windows_path_adapter=active_context.windows_path_adapter,
            )
            _require_launcher_ready(inspection.ready, inspection.issues)
            controller = _controller(active_context)
            source_revision = _rollback_source_revision(paths, active_context)
            result = controller.rollback(
                RuntimeRollbackRequest(paths, source_revision)
            )
            output.write(_transaction_message(result))
            return 0

        manifest = load_trusted_manifest(args.manifest, args.manifest_sha256)
        if manifest.target != target:
            raise ManagedRuntimeError(
                "release manifest target does not match the detected native target: "
                f"expected {target.target_id}, got {manifest.target.target_id}"
            )
        if paths.target != manifest.target:
            raise ManagedRuntimeError(
                "resolved install paths do not match the trusted manifest target"
            )
        snapshot = active_context.snapshot_provider(paths)
        run_preflight(paths, manifest, snapshot=snapshot).require_passed()

        if args.command in {"update", "repair"}:
            inspection = inspect_stable_launchers(
                paths,
                posix_exposure_root=active_context.posix_exposure_root,
                environment_path=active_context.environment_path,
                windows_path_adapter=active_context.windows_path_adapter,
            )
            _require_launcher_ready(inspection.ready, inspection.issues)
        verified = _acquire_verified_artifacts(
            paths,
            manifest,
            args.offline_payload,
            active_context,
        )
        if args.command == "install":
            launchers = install_stable_launchers(
                paths,
                posix_exposure_root=active_context.posix_exposure_root,
                environment_path=active_context.environment_path,
                windows_path_adapter=active_context.windows_path_adapter,
            )
            if not launchers.path_contains_exposure:
                output.write(
                    "warning: launcher exposure directory is not on PATH; "
                    f"add {launchers.exposure_root} to a new shell\n"
                )

        request = RuntimeTransactionRequest(
            paths=paths,
            manifest=manifest,
            verified_artifacts=verified,
            source_revision=_manifest_source_revision(manifest, active_context),
            preflight_snapshot=snapshot,
        )
        controller = _controller(active_context)
        operation = getattr(controller, args.command)
        result = operation(request)
        output.write(_transaction_message(result))
        return 0
    except ManagedRuntimeError as exc:
        errors.write(f"easyqc-install: error: {exc}\n")
        return 1


def _resolve_paths(
    context: InstallerContext,
    target: RuntimeTargetV1,
    scope: str,
) -> ManagedRuntimePaths:
    if context.path_resolver is not None:
        paths = context.path_resolver(target, scope)
    else:
        paths = resolve_runtime_paths(
            target,
            scope,
            user_data_root=context.user_data_root,
            environment=context.environment,
        )
    if not isinstance(paths, ManagedRuntimePaths):
        raise ManagedRuntimeError("installer path resolver returned invalid paths")
    if paths.target != target or paths.scope != scope:
        raise ManagedRuntimeError("installer path resolver changed target or scope")
    return paths


def _acquire_verified_artifacts(
    paths: ManagedRuntimePaths,
    manifest: ReleaseManifestV1,
    offline_payload: Path | None,
    context: InstallerContext,
) -> VerifiedArtifactSet:
    if offline_payload is not None:
        return inspect_offline_payload(
            OfflinePayloadRequest(
                manifest,
                offline_payload,
                manifest.target.target_id,
            )
        )
    destination = Path(paths.install_root) / "downloads" / manifest.release_id
    return acquire_online(
        OnlineAcquisitionRequest(
            manifest,
            destination,
            manifest.target.target_id,
        ),
        transport=context.transport,
    )


def _controller(context: InstallerContext) -> RuntimeTransactionController:
    return RuntimeTransactionController(
        adapter=context.adapter or RealUvAdapter(),
        smoke_runner=context.smoke_runner,
        transaction_id_factory=context.transaction_id_factory,
        clock=context.clock,
    )


def _manifest_source_revision(
    manifest: ReleaseManifestV1,
    context: InstallerContext,
) -> str:
    """Fill the controller's 40-hex audit slot deterministically.

    Manifest v1 has no SCM-revision field; its full source SHA-256 remains the
    receipt authority. The prefix is diagnostic metadata only, never a trust
    or equality decision.
    """

    return context.source_revision or manifest.easyqc.source_sha256[:40]


def _rollback_source_revision(
    paths: ManagedRuntimePaths,
    context: InstallerContext,
) -> str:
    if context.source_revision is not None:
        return context.source_revision
    root = Path(paths.install_root)
    pointer = read_activation_pointer(root)
    receipt = load_install_receipt(root, pointer.active_release_id)
    return receipt.source_sha256[:40]


def _require_launcher_ready(ready: bool, issues: tuple[str, ...]) -> None:
    if not ready:
        raise ManagedRuntimeError(
            "stable launcher ownership is not ready: " + "; ".join(issues)
        )


def _transaction_message(result: RuntimeTransactionResult) -> str:
    pointer = result.pointer
    report_path = result.report_path
    return (
        f"{result.operation} complete: release={pointer.active_release_id} "
        f"generation={pointer.generation} log={report_path}\n"
    )


def _human_status(status: InstallerStatusV1) -> str:
    if not status.installed:
        return (
            f"EasyQC is {status.installation_state} "
            f"({status.scope}, {status.install_root}).\n"
        )
    return (
        f"EasyQC {status.installation_state}: active={status.active_release_id} "
        f"previous={status.previous_release_id or '-'} "
        f"generation={status.generation} launcher_ready="
        f"{'yes' if status.launcher_ready else 'no'} path_ready="
        f"{'yes' if status.path_contains_exposure else 'no'}\n"
    )


def _require_real_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must be a real directory")


def _read_regular_bytes(path: Path, label: str, maximum: int) -> bytes:
    try:
        before = path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect {label}") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ManagedRuntimeError(f"{label} must be a regular non-symlink file")
    if before.st_size > maximum:
        raise ManagedRuntimeError(f"{label} exceeds the authority size limit")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ManagedRuntimeError(f"{label} changed before open")
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            before.st_dev,
            before.st_ino,
            before.st_size,
        ):
            raise ManagedRuntimeError(f"{label} changed before it was read")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise ManagedRuntimeError(
                    f"{label} exceeds the authority size limit"
                )
            chunks.append(chunk)
        data = b"".join(chunks)
    except ManagedRuntimeError:
        raise
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot read {label}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"{label} disappeared after read") from exc
    if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ):
        raise ManagedRuntimeError(f"{label} changed while it was read")
    return data


__all__ = [
    "InstallerContext",
    "InstallerStatusV1",
    "build_installer_parser",
    "inspect_installer_status",
    "load_trusted_manifest",
    "main",
    "parse_installer_arguments",
]
