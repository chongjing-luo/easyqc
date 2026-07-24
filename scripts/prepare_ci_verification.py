#!/usr/bin/env python3
"""Prepare one exact CI-only verification request and check plan.

Input: one approved CI row, its repository lock, the clean checked-out source,
GitHub runner metadata and an optional paired release identity. Output: one new
directory containing canonical request, plan and release-input files. Side
effects are limited to bounded identity probes and the new output directory;
test execution and evidence normalization remain separate commands.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
from types import MappingProxyType
from typing import Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.platform_verification import (  # noqa: E402
    PlatformVerificationError,
    write_new_verification_authority,
)
from models.platform_verification import (  # noqa: E402
    AutomatedCheckPlanV1,
    PlatformVerificationContractError,
    VerificationRequestV1,
    canonical_json_bytes,
)


COMMAND_VERSION = "platform-v1"
PYTHON_VERSION = "3.10.17"
UV_VERSION = "0.11.29"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_UV_VERSION_OUTPUT_PATTERN = re.compile(
    r"^uv (?P<version>[0-9][A-Za-z0-9.+-]*)"
    r"(?: \([A-Za-z0-9_.+-]+\))?$"
)
_LOCK_PIN_PATTERN = re.compile(
    r"(?m)^(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)=="
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9_.!+-]*)"
)


class CiPreparationError(RuntimeError):
    """Raised when a CI request cannot be bound to exact safe inputs."""


@dataclass(frozen=True)
class CiRowSpec:
    runner_label: str
    target_id: str
    lock_path: str
    os: str
    version_prefix: str | None
    arch: str
    runner_image_prefix: str


CI_ROW_SPECS: Mapping[str, CiRowSpec] = MappingProxyType(
    {
        "ci-ubuntu-22.04-x86_64": CiRowSpec(
            runner_label="ubuntu-22.04",
            target_id="ubuntu-22.04-x86_64",
            lock_path="packaging/locks/python-3.10.17/linux-x86_64/test.txt",
            os="linux",
            version_prefix="22.04",
            arch="x86_64",
            runner_image_prefix="ubuntu22:",
        ),
        "ci-ubuntu-24.04-x86_64": CiRowSpec(
            runner_label="ubuntu-24.04",
            target_id="ubuntu-24.04-x86_64",
            lock_path="packaging/locks/python-3.10.17/linux-x86_64/test.txt",
            os="linux",
            version_prefix="24.04",
            arch="x86_64",
            runner_image_prefix="ubuntu24:",
        ),
        "ci-windows-2022-x86_64": CiRowSpec(
            runner_label="windows-2022",
            target_id="windows-11-x86_64",
            lock_path="packaging/locks/python-3.10.17/windows-x86_64/test.txt",
            os="windows",
            version_prefix=None,
            arch="x86_64",
            runner_image_prefix="win22:",
        ),
        "ci-macos-15-arm64": CiRowSpec(
            runner_label="macos-15",
            target_id="macos-13-arm64",
            lock_path="packaging/locks/python-3.10.17/macos-arm64/test.txt",
            os="macos",
            version_prefix="15",
            arch="arm64",
            runner_image_prefix="macos15:",
        ),
    }
)


@dataclass(frozen=True)
class CiPreparationInput:
    matrix_row_id: str
    run_id: str
    runner_label: str
    source_root: Path
    lock_path: Path
    release_id: str | None
    manifest_sha256: str | None


@dataclass(frozen=True)
class CiEnvironmentSnapshot:
    source_revision: str
    source_dirty: bool
    python_executable: str
    uv: str
    python: str
    qt: str
    pyside: str
    pandas: str
    numpy: str
    os: str
    version: str
    arch: str
    kernel: str
    runner_image: str
    cpu: str
    ram_bytes: int
    gpu_or_renderer: str


@dataclass(frozen=True)
class PreparedCiVerificationInputs:
    request_bytes: bytes
    plan_bytes: bytes
    release_input_bytes: bytes


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_lock_path(config: CiPreparationInput, spec: CiRowSpec) -> Path:
    source_root = config.source_root
    if not source_root.is_absolute():
        raise CiPreparationError("source root must be absolute")
    if source_root.is_symlink() or not source_root.is_dir():
        raise CiPreparationError("source root must be a real directory")
    expected = source_root.joinpath(*PurePosixPath(spec.lock_path).parts)
    if config.lock_path != expected:
        raise CiPreparationError(
            f"lock path does not match CI row: expected {spec.lock_path}"
        )
    cursor = source_root
    for part in PurePosixPath(spec.lock_path).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise CiPreparationError("lock path must not contain symlinks")
    if not expected.is_file():
        raise CiPreparationError("lock path must be a regular file")
    return expected


def _validate_snapshot(
    config: CiPreparationInput,
    snapshot: CiEnvironmentSnapshot,
    spec: CiRowSpec,
) -> None:
    if snapshot.source_dirty:
        raise CiPreparationError("CI source revision must be clean")
    if not _REVISION_PATTERN.fullmatch(snapshot.source_revision):
        raise CiPreparationError("CI source revision must be 40 lowercase hex")
    if config.runner_label != spec.runner_label:
        raise CiPreparationError("runner label does not match CI matrix row")
    if snapshot.python != PYTHON_VERSION:
        raise CiPreparationError(
            f"Python identity mismatch: expected {PYTHON_VERSION}"
        )
    if snapshot.uv != UV_VERSION:
        raise CiPreparationError(f"uv identity mismatch: expected {UV_VERSION}")
    if snapshot.os != spec.os:
        raise CiPreparationError("platform OS does not match CI matrix row")
    if snapshot.arch != spec.arch:
        raise CiPreparationError("platform architecture does not match CI matrix row")
    if spec.version_prefix is not None and not (
        snapshot.version == spec.version_prefix
        or snapshot.version.startswith(f"{spec.version_prefix}.")
    ):
        raise CiPreparationError("platform version does not match CI matrix row")
    if not snapshot.runner_image.startswith(spec.runner_image_prefix):
        raise CiPreparationError("runner image does not match CI matrix row")
    if not snapshot.python_executable:
        raise CiPreparationError("Python executable identity is missing")
    if not snapshot.cpu or not snapshot.gpu_or_renderer or snapshot.ram_bytes <= 0:
        raise CiPreparationError("hardware identity is incomplete")


def _validate_locked_runtime(
    lock_path: Path,
    snapshot: CiEnvironmentSnapshot,
) -> None:
    try:
        lock_text = lock_path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise CiPreparationError("target lock must be readable UTF-8") from exc
    pins: dict[str, str] = {}
    for match in _LOCK_PIN_PATTERN.finditer(lock_text):
        name = match.group("name").lower().replace("_", "-")
        version = match.group("version")
        if name in pins:
            raise CiPreparationError(f"duplicate package pin in target lock: {name}")
        pins[name] = version
    required = {
        "numpy": snapshot.numpy,
        "pandas": snapshot.pandas,
        "pyside6-essentials": snapshot.pyside,
    }
    for package, actual in required.items():
        expected = pins.get(package)
        label = "PySide" if package == "pyside6-essentials" else package
        if expected is None:
            raise CiPreparationError(f"{label} pin is missing from target lock")
        if actual != expected:
            raise CiPreparationError(
                f"{label} runtime version does not match target lock: "
                f"expected {expected}, got {actual}"
            )
    if snapshot.qt != pins["pyside6-essentials"]:
        raise CiPreparationError(
            "Qt runtime version does not match the PySide target lock"
        )


def _release_identity(
    config: CiPreparationInput,
    snapshot: CiEnvironmentSnapshot,
    spec: CiRowSpec,
    lock_sha256: str,
) -> tuple[str, str, bytes]:
    has_release_id = config.release_id is not None
    has_manifest = config.manifest_sha256 is not None
    if has_release_id != has_manifest:
        raise CiPreparationError(
            "both release_id and manifest_sha256 are required for release input"
        )
    if has_release_id:
        assert config.release_id is not None
        assert config.manifest_sha256 is not None
        if not _SHA256_PATTERN.fullmatch(config.manifest_sha256):
            raise CiPreparationError("release manifest_sha256 must be lowercase hex")
        release_input = canonical_json_bytes(
            {
                "schema_version": 1,
                "identity_class": "external-release-manifest-reference",
                "release_id": config.release_id,
                "manifest_sha256": config.manifest_sha256,
                "matrix_row_id": config.matrix_row_id,
                "source_revision": snapshot.source_revision,
                "target_id": spec.target_id,
                "lock_sha256": lock_sha256,
            }
        )
        return config.release_id, config.manifest_sha256, release_input

    release_id = (
        f"ci-candidate-{snapshot.source_revision[:12]}-"
        f"{config.matrix_row_id.removeprefix('ci-')}"
    )
    release_input = canonical_json_bytes(
        {
            "schema_version": 1,
            "identity_class": "ci-candidate-manifest",
            "release_id": release_id,
            "matrix_row_id": config.matrix_row_id,
            "source_revision": snapshot.source_revision,
            "target_id": spec.target_id,
            "lock_sha256": lock_sha256,
            "command_version": COMMAND_VERSION,
        }
    )
    return release_id, hashlib.sha256(release_input).hexdigest(), release_input


def _check_plan(python_executable: str) -> AutomatedCheckPlanV1:
    core_targets = (
        "tests/test_models",
        "tests/test_core",
        "tests/test_utils",
        "tests/test_characterization",
        "tests/test_integration",
        "tests/test_smoke.py",
        "tests/test_scripts/test_replacement_first_throughput.py",
    )
    runtime_targets = (
        "tests/test_models/test_managed_runtime.py",
        "tests/test_core/test_managed_runtime_acquisition.py",
        "tests/test_core/test_managed_runtime_cli.py",
        "tests/test_core/test_managed_runtime_launcher.py",
        "tests/test_core/test_managed_runtime_lock.py",
        "tests/test_core/test_managed_runtime_platform.py",
        "tests/test_core/test_managed_runtime_real_transaction.py",
        "tests/test_core/test_managed_runtime_transaction.py",
        "tests/test_core/test_managed_runtime_uv.py",
        "tests/test_scripts/test_managed_runtime_entry.py",
    )
    return AutomatedCheckPlanV1.from_json_object(
        {
            "schema_version": 1,
            "command_version": COMMAND_VERSION,
            "checks": [
                {
                    "name": "core-suite",
                    "argv": [
                        python_executable,
                        "-m",
                        "pytest",
                        *core_targets,
                        "-q",
                    ],
                    "timeout_seconds": 1800.0,
                    "report_path": "reports/core-suite.json",
                    "classification": "test-report",
                },
                {
                    "name": "qt-offscreen-suite",
                    "argv": [
                        python_executable,
                        "-m",
                        "pytest",
                        "tests/test_gui_qt",
                        "tests/test_scripts/test_qt_preview_entry.py",
                        "-q",
                    ],
                    "timeout_seconds": 1800.0,
                    "report_path": "reports/qt-offscreen-suite.json",
                    "classification": "test-report",
                },
                {
                    "name": "managed-runtime-suite",
                    "argv": [
                        python_executable,
                        "-m",
                        "pytest",
                        *runtime_targets,
                        "-q",
                    ],
                    "timeout_seconds": 1800.0,
                    "report_path": "reports/managed-runtime-suite.json",
                    "classification": "test-report",
                },
            ],
        }
    )


def build_ci_verification_inputs(
    config: CiPreparationInput,
    snapshot: CiEnvironmentSnapshot,
) -> PreparedCiVerificationInputs:
    """Build one typed CI request/plan bundle without writing files."""

    spec = CI_ROW_SPECS.get(config.matrix_row_id)
    if spec is None:
        raise CiPreparationError("matrix_row_id must be an approved CI matrix row")
    lock_path = _validated_lock_path(config, spec)
    _validate_snapshot(config, snapshot, spec)
    _validate_locked_runtime(lock_path, snapshot)
    lock_sha256 = _hash_file(lock_path)
    release_id, manifest_sha256, release_input = _release_identity(
        config,
        snapshot,
        spec,
        lock_sha256,
    )
    try:
        request = VerificationRequestV1.from_json_object(
            {
                "schema_version": 1,
                "run_id": config.run_id,
                "matrix_row_id": config.matrix_row_id,
                "source": {
                    "revision": snapshot.source_revision,
                    "dirty": snapshot.source_dirty,
                },
                "release": {
                    "release_id": release_id,
                    "manifest_sha256": manifest_sha256,
                    "target_id": spec.target_id,
                },
                "runtime": {
                    "uv": snapshot.uv,
                    "python": snapshot.python,
                    "qt": snapshot.qt,
                    "pyside": snapshot.pyside,
                    "pandas": snapshot.pandas,
                    "numpy": snapshot.numpy,
                    "lock_sha256": lock_sha256,
                },
                "platform": {
                    "os": snapshot.os,
                    "version": snapshot.version,
                    "arch": snapshot.arch,
                    "kernel": snapshot.kernel,
                    "runner_label": config.runner_label,
                    "runner_image": snapshot.runner_image,
                },
                "display": {
                    "session": "offscreen",
                    "scale_percent": 100,
                    "logical_viewport": "1280x720",
                    "color_scheme": "system",
                    "remote": False,
                },
                "hardware": {
                    "cpu": snapshot.cpu,
                    "ram_bytes": snapshot.ram_bytes,
                    "gpu_or_renderer": snapshot.gpu_or_renderer,
                },
                "command_or_checklist_version": COMMAND_VERSION,
            }
        )
        plan = _check_plan(snapshot.python_executable)
    except PlatformVerificationContractError as exc:
        raise CiPreparationError(f"invalid CI verification identity: {exc}") from exc
    if request.evidence_class != "ci":
        raise CiPreparationError("prepared request must remain CI-only")
    return PreparedCiVerificationInputs(
        request_bytes=request.canonical_bytes,
        plan_bytes=plan.canonical_bytes,
        release_input_bytes=release_input,
    )


def write_ci_verification_inputs(
    prepared: PreparedCiVerificationInputs,
    output_root: Path,
) -> Path:
    """Create one no-overwrite canonical CI input bundle."""

    if not output_root.is_absolute():
        raise CiPreparationError("CI input output root must be absolute")
    if output_root.exists() or output_root.is_symlink():
        raise CiPreparationError("CI input output root already exists")
    parent = output_root.parent
    if parent.is_symlink() or not parent.is_dir():
        raise CiPreparationError("CI input output parent must be a real directory")
    try:
        output_root.mkdir(mode=0o700)
        write_new_verification_authority(
            output_root / "release-input.json",
            prepared.release_input_bytes,
        )
        write_new_verification_authority(
            output_root / "verification-request.json",
            prepared.request_bytes,
        )
        write_new_verification_authority(
            output_root / "automated-check-plan.json",
            prepared.plan_bytes,
        )
    except (OSError, PlatformVerificationError) as exc:
        raise CiPreparationError(f"cannot write CI input bundle: {exc}") from exc
    return output_root


def _run_identity_command(
    argv: Sequence[str],
    label: str,
    *,
    allow_blank: bool = False,
) -> str:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CiPreparationError(f"{label} identity probe failed") from exc
    if completed.returncode != 0:
        raise CiPreparationError(f"{label} identity probe exited non-zero")
    value = completed.stdout.strip()
    if not value and not allow_blank:
        raise CiPreparationError(f"{label} identity probe returned blank output")
    return value


def _normalize_architecture(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"amd64", "x86_64"}:
        return "x86_64"
    if normalized in {"aarch64", "arm64"}:
        return "arm64"
    raise CiPreparationError(f"unsupported runner architecture: {value}")


def _parse_uv_version(output: str) -> str:
    match = _UV_VERSION_OUTPUT_PATTERN.fullmatch(output)
    if match is None:
        raise CiPreparationError("uv version output is invalid")
    return match.group("version")


def _platform_identity() -> tuple[str, str]:
    system = platform.system()
    if system == "Linux":
        try:
            version = platform.freedesktop_os_release()["VERSION_ID"]
        except (KeyError, OSError) as exc:
            raise CiPreparationError("Linux VERSION_ID is unavailable") from exc
        return "linux", version
    if system == "Windows":
        return "windows", platform.version()
    if system == "Darwin":
        version = platform.mac_ver()[0]
        if not version:
            raise CiPreparationError("macOS version is unavailable")
        return "macos", version
    raise CiPreparationError(f"unsupported runner operating system: {system}")


def _physical_memory_bytes() -> int:
    system = platform.system()
    if system == "Windows":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_physical", ctypes.c_ulonglong),
                ("available_physical", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("available_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("available_virtual", ctypes.c_ulonglong),
                ("available_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise CiPreparationError("Windows physical memory probe failed")
        return int(status.total_physical)
    if system == "Darwin":
        value = _run_identity_command(("sysctl", "-n", "hw.memsize"), "memory")
        try:
            return int(value)
        except ValueError as exc:
            raise CiPreparationError("macOS physical memory is invalid") from exc
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError) as exc:
        raise CiPreparationError("Linux physical memory probe failed") from exc


def inspect_ci_environment(environ: Mapping[str, str]) -> CiEnvironmentSnapshot:
    """Capture exact source/runtime/runner identity from one GitHub-hosted job."""

    expected_revision = environ.get("GITHUB_SHA", "")
    actual_revision = _run_identity_command(
        ("git", "rev-parse", "HEAD"),
        "source revision",
    )
    if actual_revision != expected_revision:
        raise CiPreparationError("checked-out source does not match GITHUB_SHA")
    dirty = bool(
        _run_identity_command(
            ("git", "status", "--porcelain", "--untracked-files=all"),
            "source status",
            allow_blank=True,
        )
    )
    uv_executable = shutil.which("uv")
    if uv_executable is None:
        raise CiPreparationError("uv executable is unavailable")
    uv_output = _run_identity_command((uv_executable, "--version"), "uv")
    uv_version = _parse_uv_version(uv_output)

    image_os = environ.get("ImageOS", "")
    image_version = environ.get("ImageVersion", "")
    if not image_os or not image_version:
        raise CiPreparationError("GitHub runner image identity is unavailable")
    platform_os, platform_version = _platform_identity()

    import numpy
    import pandas
    import PySide6
    from PySide6.QtCore import qVersion

    return CiEnvironmentSnapshot(
        source_revision=actual_revision,
        source_dirty=dirty,
        python_executable=sys.executable,
        uv=uv_version,
        python=platform.python_version(),
        qt=qVersion(),
        pyside=PySide6.__version__,
        pandas=pandas.__version__,
        numpy=numpy.__version__,
        os=platform_os,
        version=platform_version,
        arch=_normalize_architecture(platform.machine()),
        kernel=platform.release(),
        runner_image=f"{image_os}:{image_version}",
        cpu=platform.processor() or platform.machine(),
        ram_bytes=_physical_memory_bytes(),
        gpu_or_renderer=(
            f"QT_QPA_PLATFORM={environ.get('QT_QPA_PLATFORM', 'unset')}"
        ),
    )


def _optional_environment_value(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def _github_command_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _emit_github_annotation(
    environ: Mapping[str, str],
    level: str,
    title: str,
    message: str,
) -> None:
    if environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return
    print(
        f"::{level} title={_github_command_escape(title)}::"
        f"{_github_command_escape(message)}",
        file=sys.stderr,
    )


def _diagnostic_line(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")


def _write_preparation_failure_diagnostic(
    environ: Mapping[str, str],
    *,
    run_id: str,
    matrix_row_id: str,
    message: str,
) -> Path:
    configured_root = environ.get("EASYQC_LOG_DIR", "").strip()
    if not configured_root:
        raise CiPreparationError("EASYQC_LOG_DIR is not configured")
    diagnostic_root = Path(configured_root)
    if not diagnostic_root.is_absolute():
        raise CiPreparationError("EASYQC_LOG_DIR must be absolute")
    if diagnostic_root.is_symlink():
        raise CiPreparationError("EASYQC_LOG_DIR must not be a symlink")
    if diagnostic_root.exists():
        if not diagnostic_root.is_dir():
            raise CiPreparationError("EASYQC_LOG_DIR must be a directory")
    else:
        parent = diagnostic_root.parent
        if parent.is_symlink() or not parent.is_dir():
            raise CiPreparationError(
                "EASYQC_LOG_DIR parent must be a real directory"
            )
        diagnostic_root.mkdir(mode=0o700)

    diagnostic_path = diagnostic_root / "prepare-ci-error.txt"
    if diagnostic_path.exists() or diagnostic_path.is_symlink():
        raise CiPreparationError("preparation diagnostic already exists")
    content = (
        "EasyQC CI preparation failure\n"
        f"run_id={_diagnostic_line(run_id)}\n"
        f"matrix_row_id={_diagnostic_line(matrix_row_id)}\n"
        f"source_revision={_diagnostic_line(environ.get('GITHUB_SHA', ''))}\n"
        f"error={_diagnostic_line(message)}\n"
    )
    with diagnostic_path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    return diagnostic_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-row-id", required=True)
    parser.add_argument("--runner-label", required=True)
    parser.add_argument("--target-lock", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run_id = "unavailable"
    try:
        run_id = (
            f"ci-{os.environ['GITHUB_RUN_ID']}-"
            f"{os.environ['GITHUB_RUN_ATTEMPT']}-{args.matrix_row_id}"
        )
        config = CiPreparationInput(
            matrix_row_id=args.matrix_row_id,
            run_id=run_id,
            runner_label=args.runner_label,
            source_root=PROJECT_ROOT,
            lock_path=(PROJECT_ROOT / args.target_lock),
            release_id=_optional_environment_value(
                os.environ,
                "EASYQC_CI_RELEASE_ID",
            ),
            manifest_sha256=_optional_environment_value(
                os.environ,
                "EASYQC_CI_MANIFEST_SHA256",
            ),
        )
        snapshot = inspect_ci_environment(os.environ)
        prepared = build_ci_verification_inputs(config, snapshot)
        output_root = write_ci_verification_inputs(
            prepared,
            args.output_root,
        )
    except (
        KeyError,
        OSError,
        CiPreparationError,
        PlatformVerificationContractError,
    ) as exc:
        message = str(exc)
        print(f"ERROR CI verification preparation: {message}", file=sys.stderr)
        _emit_github_annotation(
            os.environ,
            "error",
            "EasyQC CI preparation failed",
            message,
        )
        if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
            try:
                _write_preparation_failure_diagnostic(
                    os.environ,
                    run_id=run_id,
                    matrix_row_id=args.matrix_row_id,
                    message=message,
                )
            except (OSError, ValueError, CiPreparationError) as diagnostic_exc:
                diagnostic_message = str(diagnostic_exc)
                print(
                    f"ERROR CI diagnostic write: {diagnostic_message}",
                    file=sys.stderr,
                )
                _emit_github_annotation(
                    os.environ,
                    "warning",
                    "EasyQC CI diagnostic write failed",
                    diagnostic_message,
                )
        return 2
    print(
        f"PREPARED row={args.matrix_row_id} output={output_root}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
