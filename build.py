#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC 一键打包脚本 — 支持 Linux / macOS / Windows

用法:
    python build.py --linux-cursor-deb PATH  # Linux x86_64
    python build.py              # macOS / Windows 当前平台
    python build.py --clean ...  # 清理后重新打包
    python build.py --version 1.0.0  # 指定版本号

前提:
    pip install pyinstaller
    pip install -r requirements.txt

输出:
    dist/EasyQC-v<version>-<os>-<arch>/
      ├── EasyQC[.exe]          # 可执行文件
      └── _internal/            # 依赖文件

说明:
    PyInstaller 只能在当前平台为当前平台打包（不支持交叉编译）。
    要为 Linux/macOS/Windows 分别打包，请在各自平台上运行本脚本。
"""

import argparse
import ast
import hashlib
from importlib import metadata as importlib_metadata
import json
import os
import platform
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
from dataclasses import dataclass

from packaging_tools.artifact_manifest import (
    ArtifactManifest,
    ArtifactManifestError,
    create_artifact_manifest,
    write_artifact_manifest,
)
from packaging_tools.contracts import (
    BuildReceipt,
    ReleaseContractError,
    ValidatedReleaseInputs,
    canonical_json_bytes,
    load_release_input,
    sha256_file,
    validate_release_inputs,
    write_canonical_json,
)

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
APP_NAME = "EasyQC"
SPEC_FILE = "easyqc.spec"
PYINSTALLER_MIN_VERSION = (5, 0)

SCRIPT_DIR = Path(__file__).resolve().parent
DIST_DIR = SCRIPT_DIR / "dist"
BUILD_DIR = SCRIPT_DIR / "build"

# ---------------------------------------------------------------------------
# 平台信息
# ---------------------------------------------------------------------------
SYSTEM = platform.system()           # "Linux" | "Darwin" | "Windows"
ARCH = platform.machine()            # "x86_64" | "arm64" | "AMD64"

OS_MAP = {"Linux": "linux", "Darwin": "macos", "Windows": "windows"}
OS_LABEL = OS_MAP.get(SYSTEM, SYSTEM.lower())

EXE_NAME = APP_NAME if SYSTEM != "Windows" else f"{APP_NAME}.exe"
LINUX_QT_LIBRARIES = (
    "libxcb-cursor.so.0",
    "libxkbcommon-x11.so.0",
    "libxcb-xinerama.so.0",
    "libxcb-icccm.so.4",
    "libxcb-keysyms.so.1",
    "libxcb-render-util.so.0",
)
LINUX_CURSOR_DEB_SIZE = 10_538
LINUX_CURSOR_DEB_SHA256 = (
    "c9b5d1ad4af57397b1bd77e0a92750e34419def134c0282a0836ae9efc07cf64"
)
LINUX_CURSOR_LIBRARY_SHA256 = (
    "729297e66519bdb0df3ac8d5a2950e2e09b1e8d0b9eeea33250fce57a27aafa3"
)
LINUX_CURSOR_PACKAGE = "libxcb-cursor0"
LINUX_CURSOR_VERSION = "0.1.1-4ubuntu1"
LINUX_CURSOR_ARCHITECTURE = "amd64"
LINUX_CURSOR_SONAME = "libxcb-cursor.so.0"
LINUX_CURSOR_NOTICE = SCRIPT_DIR / "packaging" / "licenses" / "xcb-util-cursor.txt"
LINUX_CURSOR_ENV = "EASYQC_LINUX_CURSOR_LIBRARY"
PLATFORMDIRS_VERSION = "4.10.1"
PLATFORMDIRS_NOTICE = SCRIPT_DIR / "packaging" / "licenses" / "platformdirs.txt"
PYINSTALLER_EVIDENCE_SCHEMA = "easyqc-release-pyinstaller-evidence-index-v1"
DISTRIBUTION_METADATA_SCHEMA = "easyqc-release-distribution-metadata-v1"
DISTRIBUTION_METADATA_V2_SCHEMA = "easyqc-release-distribution-metadata-v2"
COMPONENT_POLICY_SCHEMA = "easyqc-component-policy-v1"
_REQUIRED_PYINSTALLER_TOC_KINDS = (
    "Analysis",
    "PYZ",
    "PKG",
    "EXE",
    "COLLECT",
)
_PYINSTALLER_COLLECT_TYPES = frozenset(
    {"BINARY", "DATA", "EXECUTABLE", "EXTENSION", "SYMLINK"}
)
_PYINSTALLER_WARNING_DISPOSITIONS = frozenset(
    {"RETAINED_NO_WARNINGS", "RETAINED_REVIEW_REQUIRED"}
)
_SUPPORTS_OPENAT = os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY")
_LICENSE_FILE_PATTERN = re.compile(
    r"(^|/)(licen[cs]e|copying|notice|authors?|copyright)([._-]|$)",
    re.IGNORECASE,
)
_DISTRIBUTION_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")
_MAX_RETAINED_TOC_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class VerifiedLinuxCursorRuntime:
    """Paths normalized from the exact approved Ubuntu cursor package."""

    library_path: Path
    copyright_path: Path

# ---------------------------------------------------------------------------
# 颜色（Windows cmd 不支持 ANSI，简单处理）
# ---------------------------------------------------------------------------
_ENABLE_COLOR = SYSTEM != "Windows" or os.environ.get("TERM") == "xterm"

def _c(code: str, text: str) -> str:
    if not _ENABLE_COLOR:
        return text
    colors = {"red": 31, "green": 32, "yellow": 33, "blue": 34, "bold": 1}
    c = colors.get(code, 0)
    return f"\033[{c}m{text}\033[0m"

def info(msg):    print(f"{_c('green', '[INFO]')}  {msg}")
def warn(msg):    print(f"{_c('yellow', '[WARN]')}  {msg}")
def error(msg):   print(f"{_c('red', '[ERROR]')} {msg}")
def header(msg):  print(f"\n{_c('bold', '='*60)}\n{_c('bold', msg)}\n{_c('bold', '='*60)}")

# ---------------------------------------------------------------------------
# 环境检查
# ---------------------------------------------------------------------------
def check_python() -> str:
    v = sys.version_info
    ver_str = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 10):
        error(f"需要 Python >= 3.10，当前: {ver_str}")
        sys.exit(1)
    info(f"Python {ver_str}  ({sys.executable})")
    return ver_str

def check_pyinstaller() -> str:
    try:
        import PyInstaller
        ver = tuple(map(int, PyInstaller.__version__.split(".")[:2]))
        if ver < PYINSTALLER_MIN_VERSION:
            warn(f"PyInstaller 版本较旧 ({PyInstaller.__version__})，建议升级")
        info(f"PyInstaller {PyInstaller.__version__}")
        return PyInstaller.__version__
    except ImportError:
        error("PyInstaller 未安装。请先运行: pip install pyinstaller")
        sys.exit(1)

def check_deps():
    for pkg in ["pandas", "numpy", "platformdirs", "PySide6"]:
        try:
            __import__(pkg)
            info(f"✓ {pkg}")
        except ImportError:
            error(f"缺少依赖: {pkg}。请先运行: pip install -r requirements.txt")
            sys.exit(1)

def check_tkinter():
    """验证 tkinter 模块可导入（不要求真实显示器——打包可在无头环境执行）"""
    try:
        import tkinter
        info(f"✓ tkinter (Tk {tkinter.TkVersion})")
    except ImportError:
        if SYSTEM == "Linux":
            error("tkinter 未安装。Ubuntu/Debian: sudo apt install python3-tk")
        elif SYSTEM == "Darwin":
            error("tkinter 未安装。请使用官方 Python（包含 tkinter），而非 Homebrew 版本。")
        else:
            error("tkinter 未安装。请确保 Python 包含 tkinter。")
        sys.exit(1)

def check_qt_platform_dependencies(
    cursor_runtime: VerifiedLinuxCursorRuntime | None = None,
) -> None:
    """Fail before packaging when the native Linux Qt xcb stack is incomplete."""
    if SYSTEM != "Linux":
        return
    missing = set(_missing_linux_qt_libraries())
    if cursor_runtime is not None:
        missing.discard(LINUX_CURSOR_SONAME)
    if missing:
        error(f"缺少 Qt xcb 系统运行库: {', '.join(sorted(missing))}")
        sys.exit(1)
    info("✓ Linux Qt xcb 构建依赖（cursor 使用已验证输入）")

def _missing_linux_qt_libraries() -> tuple[str, ...]:
    """Inspect the actual xcb platform plugin dependency closure with ldd."""
    from PySide6.QtCore import QLibraryInfo

    plugin = (
        Path(QLibraryInfo.path(QLibraryInfo.PluginsPath))
        / "platforms"
        / "libqxcb.so"
    )
    if not plugin.is_file():
        return ("libqxcb.so",)
    try:
        result = subprocess.run(
            ["ldd", str(plugin)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ("ldd dependency inspection",)
    if result.returncode != 0:
        return ("libqxcb.so dependency inspection",)
    missing = {
        line.strip().split()[0]
        for line in result.stdout.splitlines()
        if "=> not found" in line
    }
    return tuple(sorted(missing))


def _sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 digest of one file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _abort(message: str) -> None:
    error(message)
    raise SystemExit(1)


def _verify_cursor_runtime_files(runtime: VerifiedLinuxCursorRuntime) -> None:
    """Revalidate one normalized cursor ELF and its approved notice."""
    if (
        runtime.library_path.name != LINUX_CURSOR_SONAME
        or not runtime.library_path.is_file()
    ):
        _abort("已验证 Linux cursor runtime 缺少预期 soname")
    if _sha256_file(runtime.library_path) != LINUX_CURSOR_LIBRARY_SHA256:
        _abort("已验证 Linux cursor runtime 的 ELF SHA-256 不匹配")
    if not runtime.copyright_path.is_file() or not LINUX_CURSOR_NOTICE.is_file():
        _abort("已验证 Linux cursor runtime 缺少 copyright/notice")
    if runtime.copyright_path.read_bytes() != LINUX_CURSOR_NOTICE.read_bytes():
        _abort("已验证 Linux cursor runtime 的 copyright/notice 不匹配")


def _run_dpkg(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    """Run one bounded dpkg-deb inspection/extraction command."""
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _abort(f"{label}失败: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        _abort(f"{label}失败" + (f": {detail}" if detail else ""))
    return result


def verify_linux_cursor_deb(
    deb_path: Path,
    *,
    build_root: Path | None = None,
) -> VerifiedLinuxCursorRuntime:
    """Validate and normalize the approved Jammy cursor deb into the build sysroot."""
    if SYSTEM != "Linux" or ARCH != "x86_64":
        _abort("Linux cursor 输入只适用于 Ubuntu 22.04 x86_64 构建基线")

    deb_path = Path(deb_path)
    if not deb_path.is_file() or deb_path.is_symlink():
        _abort(f"Linux cursor deb 必须是常规文件: {deb_path}")
    if deb_path.stat().st_size != LINUX_CURSOR_DEB_SIZE:
        _abort(
            "Linux cursor deb 大小不匹配: "
            f"预期 {LINUX_CURSOR_DEB_SIZE} bytes"
        )
    if _sha256_file(deb_path) != LINUX_CURSOR_DEB_SHA256:
        _abort(f"Linux cursor deb SHA-256 不匹配: {LINUX_CURSOR_DEB_SHA256}")

    control = _run_dpkg(
        [
            "dpkg-deb",
            "-f",
            str(deb_path),
            "Package",
            "Version",
            "Architecture",
        ],
        "Linux cursor deb 元数据验证",
    )
    metadata: dict[str, str] = {}
    for line in control.stdout.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            metadata[key.strip()] = value.strip()
    expected_metadata = {
        "Package": LINUX_CURSOR_PACKAGE,
        "Version": LINUX_CURSOR_VERSION,
        "Architecture": LINUX_CURSOR_ARCHITECTURE,
    }
    if metadata != expected_metadata:
        _abort(
            "Linux cursor deb 元数据不匹配: "
            f"预期 {expected_metadata}，实际 {metadata}"
        )

    if not LINUX_CURSOR_NOTICE.is_file():
        _abort(f"缺少已审核的 Linux cursor 许可证: {LINUX_CURSOR_NOTICE}")

    build_root = BUILD_DIR if build_root is None else Path(build_root)
    if build_root.is_symlink() or (build_root.exists() and not build_root.is_dir()):
        _abort(f"Linux cursor build root 不是常规目录: {build_root}")
    build_root.mkdir(parents=True, exist_ok=True)
    sysroot = build_root / "linux-cursor-sysroot"
    if sysroot.is_symlink() or (sysroot.exists() and not sysroot.is_dir()):
        _abort(f"受控 Linux cursor sysroot 不是常规目录: {sysroot}")
    if sysroot.exists():
        shutil.rmtree(sysroot)
    sysroot.mkdir(parents=True)
    _run_dpkg(
        ["dpkg-deb", "-x", str(deb_path), str(sysroot)],
        "Linux cursor deb 解包",
    )

    library_path = (
        sysroot
        / "usr"
        / "lib"
        / "x86_64-linux-gnu"
        / LINUX_CURSOR_SONAME
    )
    copyright_path = sysroot / "usr/share/doc/libxcb-cursor0/copyright"
    sysroot_resolved = sysroot.resolve()
    try:
        library_resolved = library_path.resolve(strict=True)
        copyright_resolved = copyright_path.resolve(strict=True)
    except OSError as exc:
        _abort(f"Linux cursor deb 缺少预期文件: {exc}")
    if not library_path.is_symlink():
        _abort(f"Linux cursor soname 不是预期符号链接: {library_path}")
    if not library_resolved.is_relative_to(sysroot_resolved):
        _abort("Linux cursor soname 解析到了受控 sysroot 之外")
    if not copyright_resolved.is_relative_to(sysroot_resolved):
        _abort("Linux cursor copyright 解析到了受控 sysroot 之外")
    if _sha256_file(library_path) != LINUX_CURSOR_LIBRARY_SHA256:
        _abort(
            "Linux cursor ELF SHA-256 不匹配: "
            f"{LINUX_CURSOR_LIBRARY_SHA256}"
        )
    if copyright_path.read_bytes() != LINUX_CURSOR_NOTICE.read_bytes():
        _abort("Linux cursor copyright 与已审核 MIT/X notice 不一致")

    info("✓ Linux cursor deb、ELF 与 MIT/X notice 已验证")
    return VerifiedLinuxCursorRuntime(
        library_path=library_path,
        copyright_path=copyright_path,
    )

# ---------------------------------------------------------------------------
# 构建
# ---------------------------------------------------------------------------
def clean():
    for d in [BUILD_DIR, DIST_DIR]:
        if d.exists():
            shutil.rmtree(d)
            info(f"已清理: {d}")
    for pycache in SCRIPT_DIR.rglob("__pycache__"):
        shutil.rmtree(pycache, ignore_errors=True)

def run_pyinstaller(
    cursor_runtime: VerifiedLinuxCursorRuntime | None = None,
    *,
    python_executable: Path | None = None,
    source_root: Path | None = None,
    work_path: Path | None = None,
    dist_path: Path | None = None,
) -> None:
    source_root = SCRIPT_DIR if source_root is None else Path(source_root)
    python_executable = (
        Path(sys.executable) if python_executable is None else Path(python_executable)
    )
    spec = source_root / SPEC_FILE
    if not spec.exists():
        error(f"未找到配置: {spec}")
        sys.exit(1)

    build_environment = os.environ.copy()
    for inherited_name in (
        LINUX_CURSOR_ENV,
        "LD_PRELOAD",
        "LD_AUDIT",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "PYTHONINSPECT",
        "PYTHONUSERBASE",
    ):
        build_environment.pop(inherited_name, None)
    build_environment["PYTHONNOUSERSITE"] = "1"
    if SYSTEM == "Linux":
        if cursor_runtime is None:
            _abort("Linux PyInstaller 构建缺少已验证的 cursor library")
        _verify_cursor_runtime_files(cursor_runtime)
        build_root = BUILD_DIR if work_path is None else Path(work_path).parent
        expected_sysroot = (build_root / "linux-cursor-sysroot").resolve()
        if not cursor_runtime.library_path.resolve().is_relative_to(expected_sysroot):
            _abort("Linux PyInstaller cursor library 不在受控 build sysroot 内")
        build_environment[LINUX_CURSOR_ENV] = str(
            cursor_runtime.library_path.absolute()
        )
    elif cursor_runtime is not None:
        _abort("非 Linux 构建拒绝 Linux cursor 输入")

    info("PyInstaller 打包中（可能需要几分钟）...")
    cmd = [
        str(python_executable), "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--log-level=WARN",
    ]
    if work_path is not None:
        cmd.extend(["--workpath", str(Path(work_path))])
    if dist_path is not None:
        cmd.extend(["--distpath", str(Path(dist_path))])
    cmd.append(str(spec))
    try:
        result = subprocess.run(
            cmd,
            cwd=str(source_root),
            env=build_environment,
            timeout=1800,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _abort(f"PyInstaller 打包失败: {exc}")
    if result.returncode != 0:
        error("PyInstaller 打包失败")
        sys.exit(1)


def _linux_cursor_provenance() -> str:
    return (
        "Component: xcb-util-cursor\n"
        "Distribution baseline: Ubuntu 22.04 x86_64\n"
        f"Source package: {LINUX_CURSOR_PACKAGE}\n"
        f"Version: {LINUX_CURSOR_VERSION}\n"
        f"Architecture: {LINUX_CURSOR_ARCHITECTURE}\n"
        f"Source package SHA-256: {LINUX_CURSOR_DEB_SHA256}\n"
        f"Bundled ELF: _internal/{LINUX_CURSOR_SONAME}\n"
        f"Bundled ELF SHA-256: {LINUX_CURSOR_LIBRARY_SHA256}\n"
        "License: MIT/X Consortium License\n"
        "Acquisition: caller-supplied official Ubuntu Jammy archive package\n"
    )


def write_common_license_bundle(artifact: Path) -> Path:
    """Write the exact cross-platform platformdirs notice to one onedir."""

    artifact = Path(artifact)
    if not artifact.is_dir() or artifact.is_symlink():
        _abort(f"产物目录不存在或无效: {artifact}")
    if not PLATFORMDIRS_NOTICE.is_file() or PLATFORMDIRS_NOTICE.is_symlink():
        _abort("platformdirs 许可证源文件不存在或无效")

    license_dir = artifact / "THIRD_PARTY_LICENSES"
    if license_dir.is_symlink():
        _abort("bundle THIRD_PARTY_LICENSES 不能是符号链接")
    license_dir.mkdir(parents=True, exist_ok=True)
    notice_path = license_dir / "platformdirs.txt"
    if notice_path.is_symlink():
        _abort("bundle platformdirs 许可证输出不能是符号链接")
    notice_path.write_bytes(PLATFORMDIRS_NOTICE.read_bytes())
    info("✓ platformdirs MIT notice 已写入产物")
    return notice_path


def verify_common_bundle(artifact: Path) -> None:
    """Verify platformdirs identity/notice and reject bundle-relative logs."""

    artifact = Path(artifact)
    if not artifact.is_dir() or artifact.is_symlink():
        _abort(f"产物目录不存在或无效: {artifact}")
    artifact_resolved = artifact.resolve()
    internal = artifact / "_internal"
    runtime_logs = internal / "logs"
    if runtime_logs.is_symlink() or runtime_logs.exists():
        _abort("bundle 不得包含 _internal/logs 运行时日志")

    notice_path = artifact / "THIRD_PARTY_LICENSES/platformdirs.txt"
    metadata_path = (
        internal
        / f"platformdirs-{PLATFORMDIRS_VERSION}.dist-info"
        / "METADATA"
    )
    required_files = (notice_path, metadata_path)
    missing = [str(path) for path in required_files if not path.is_file()]
    if missing:
        _abort(f"bundle 缺少 platformdirs 文件: {', '.join(missing)}")
    for path in required_files:
        if path.is_symlink() or not path.resolve().is_relative_to(artifact_resolved):
            _abort(f"bundle platformdirs 文件无效或逃逸产物目录: {path}")
    if notice_path.read_bytes() != PLATFORMDIRS_NOTICE.read_bytes():
        _abort("bundle platformdirs MIT notice 不完整或不匹配")

    metadata = {}
    for line in metadata_path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition(":")
        if separator and key in {"Name", "Version"}:
            metadata[key] = value.strip()
    expected = {"Name": "platformdirs", "Version": PLATFORMDIRS_VERSION}
    if metadata != expected:
        _abort(f"bundle platformdirs metadata 不匹配: {metadata}")
    info("✓ platformdirs metadata、notice 与运行时日志边界已验证")


def write_linux_license_bundle(
    artifact: Path,
    cursor_runtime: VerifiedLinuxCursorRuntime,
) -> tuple[Path, Path]:
    """Write the approved cursor notice and deterministic provenance to an onedir."""
    if SYSTEM != "Linux":
        _abort("Linux cursor 许可证只能写入 Linux 产物")
    artifact = Path(artifact)
    if not artifact.is_dir() or artifact.is_symlink():
        _abort(f"Linux 产物目录不存在: {artifact}")
    _verify_cursor_runtime_files(cursor_runtime)
    notice_bytes = LINUX_CURSOR_NOTICE.read_bytes()

    license_dir = artifact / "THIRD_PARTY_LICENSES"
    if license_dir.is_symlink():
        _abort("Linux bundle THIRD_PARTY_LICENSES 不能是符号链接")
    license_dir.mkdir(parents=True, exist_ok=True)
    notice_path = license_dir / "xcb-util-cursor.txt"
    provenance_path = license_dir / "libxcb-cursor0-PROVENANCE.txt"
    if notice_path.is_symlink() or provenance_path.is_symlink():
        _abort("Linux bundle 许可证输出不能是符号链接")
    notice_path.write_bytes(notice_bytes)
    provenance_path.write_text(_linux_cursor_provenance(), encoding="utf-8")
    info("✓ Linux cursor MIT/X notice 与 provenance 已写入产物")
    return notice_path, provenance_path


def _current_release_target() -> str:
    normalized_arch = ARCH.lower()
    if SYSTEM == "Linux" and normalized_arch in {"x86_64", "amd64"}:
        return "linux-x86_64"
    if SYSTEM == "Windows" and normalized_arch in {"x86_64", "amd64"}:
        return "windows-x86_64"
    if SYSTEM == "Darwin" and normalized_arch in {"arm64", "aarch64"}:
        return "macos-arm64"
    raise ReleaseContractError(
        f"unsupported native release target: {SYSTEM} ({ARCH})"
    )


def _revalidate_build_inputs(validated: ValidatedReleaseInputs) -> None:
    """Recheck every authenticated input immediately before packet creation."""

    if not isinstance(validated, ValidatedReleaseInputs):
        raise ReleaseContractError("build_candidate requires ValidatedReleaseInputs")
    bundle = validated.bundle
    if bundle.target != _current_release_target():
        raise ReleaseContractError(
            f"release target {bundle.target} does not match this native runner"
        )

    references = [
        (validated.source_archive, bundle.source_archive, "source archive"),
        (validated.component_policy, bundle.component_policy, "component policy"),
        (
            validated.runtime_identity.identity_file.path,
            bundle.runtime_identity_file,
            "runtime identity",
        ),
        (
            validated.runtime_identity.artifact.path,
            validated.runtime_identity.artifact,
            "runtime artifact",
        ),
    ]
    for scope in ("runtime", "build", "test"):
        references.append(
            (validated.lock_files[scope], bundle.lock_files[scope], f"{scope} lock")
        )
    if bundle.target == "linux-x86_64":
        if validated.linux_cursor_deb is None:
            raise ReleaseContractError("Linux release is missing its cursor deb")
        references.append(
            (
                validated.linux_cursor_deb,
                bundle.target_extension["linux_cursor_deb"],
                "Linux cursor deb",
            )
        )
    elif validated.linux_cursor_deb is not None:
        raise ReleaseContractError("non-Linux release rejects a Linux cursor deb")

    for actual_path, reference, label in references:
        actual = Path(actual_path)
        try:
            if actual.resolve(strict=True) != Path(reference.path).resolve(strict=True):
                raise ReleaseContractError(f"{label} path changed after validation")
        except OSError as exc:
            raise ReleaseContractError(f"cannot resolve {label}: {exc}") from exc
        actual_digest = sha256_file(actual)
        if actual_digest != reference.sha256:
            raise ReleaseContractError(
                f"{label} SHA-256 changed after validation: "
                f"expected {reference.sha256}, got {actual_digest}"
            )

    runtime = validated.runtime_identity
    current_python = Path(sys.executable).resolve(strict=True)
    if current_python != runtime.artifact.path.resolve(strict=True):
        raise ReleaseContractError(
            "release build must run under the exact RuntimeIdentity executable"
        )
    current_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if current_version != runtime.version:
        raise ReleaseContractError(
            f"release runtime version mismatch: expected {runtime.version}, "
            f"got {current_version}"
        )


def _release_tool_versions(runtime: Path) -> dict[str, str]:
    """Return exact in-process tool versions after runtime identity validation."""

    if Path(runtime).resolve(strict=True) != Path(sys.executable).resolve(strict=True):
        raise ReleaseContractError("tool inspection requires the exact release runtime")
    try:
        import PyInstaller
    except ImportError as exc:
        raise ReleaseContractError("PyInstaller is unavailable in the release runtime") from exc
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "pyinstaller": PyInstaller.__version__,
    }


def _create_release_packet(packet_root: Path) -> tuple[Path, Path]:
    packet_root = Path(packet_root)
    parent = packet_root.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ReleaseContractError(
            f"packet root parent must be a real directory: {parent}"
        )
    if packet_root.exists() or packet_root.is_symlink():
        raise ReleaseContractError(f"packet root already exists: {packet_root}")
    try:
        packet_root.mkdir()
        build_dir = packet_root / "build"
        artifact_dir = packet_root / "artifact"
        build_dir.mkdir()
        artifact_dir.mkdir()
    except OSError as exc:
        raise ReleaseContractError(f"cannot create release packet root: {exc}") from exc
    return build_dir, artifact_dir


def _canonical_archive_member(name: str) -> PurePosixPath:
    if "\\" in name:
        raise ReleaseContractError(f"source archive path uses backslashes: {name!r}")
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or path.as_posix() != name
        or name in {"", "."}
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ReleaseContractError(f"source archive path is not canonical: {name!r}")
    return path


def _extract_source_archive(archive_path: Path, destination: Path) -> Path:
    """Extract a regular-file/directory-only tar without following archive links."""

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ReleaseContractError(f"source extraction path already exists: {destination}")
    destination.mkdir()
    names: set[str] = set()
    casefold_names: dict[str, str] = {}
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            for member in archive.getmembers():
                relative = _canonical_archive_member(member.name)
                canonical = relative.as_posix()
                if canonical in names:
                    raise ReleaseContractError(
                        f"duplicate source archive path: {canonical}"
                    )
                collision = casefold_names.get(canonical.casefold())
                if collision is not None and collision != canonical:
                    raise ReleaseContractError(
                        f"case-colliding source archive paths: {collision}, {canonical}"
                    )
                names.add(canonical)
                casefold_names[canonical.casefold()] = canonical
                output = destination.joinpath(*relative.parts)
                if member.isdir():
                    output.mkdir(parents=True, exist_ok=True)
                    output.chmod(member.mode & 0o777)
                    continue
                if not member.isfile():
                    raise ReleaseContractError(
                        f"unsupported source archive entry: {canonical}"
                    )
                output.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise ReleaseContractError(
                        f"cannot read source archive entry: {canonical}"
                    )
                with stream, output.open("xb") as target:
                    shutil.copyfileobj(stream, target, length=1024 * 1024)
                output.chmod(member.mode & 0o777)
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseContractError(f"cannot extract exact source archive: {exc}") from exc

    spec = destination / SPEC_FILE
    if spec.is_symlink() or not spec.is_file():
        raise ReleaseContractError(
            f"exact source archive has no regular root {SPEC_FILE}"
        )
    return destination


def _verify_release_orchestrator_source(source_root: Path) -> None:
    """Bind the running release orchestrator to the authenticated source."""

    for relative in (
        "build.py",
        "packaging_tools/contracts.py",
        "packaging_tools/artifact_manifest.py",
    ):
        archived = _contained_regular_file(
            source_root,
            relative,
            f"source orchestrator {relative}",
        )
        running = SCRIPT_DIR.joinpath(*PurePosixPath(relative).parts)
        if running.is_symlink() or not running.is_file():
            raise ReleaseContractError(
                f"running release orchestrator is invalid: {relative}"
            )
        if sha256_file(archived) != sha256_file(running):
            raise ReleaseContractError(
                f"release orchestrator differs from source archive: {relative}"
            )


def _canonical_relative_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ReleaseContractError(f"{label} must be a canonical relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ReleaseContractError(f"{label} must be a canonical relative path")
    return path


def _contained_regular_file(root: Path, value: object, label: str) -> Path:
    relative = _canonical_relative_path(value, label)
    current = Path(root)
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ReleaseContractError(f"{label} contains a symlink")
    if not current.is_file():
        raise ReleaseContractError(f"{label} is not a regular file: {relative}")
    try:
        if not current.resolve(strict=True).is_relative_to(Path(root).resolve(strict=True)):
            raise ReleaseContractError(f"{label} escapes its source root")
    except OSError as exc:
        raise ReleaseContractError(f"cannot resolve {label}: {exc}") from exc
    return current


def _read_canonical_json_object(path: Path, label: str) -> dict[str, object]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise ReleaseContractError(f"{label} must be a canonical JSON object")
    return value


def _write_exclusive_file(path: Path, data: bytes, label: str) -> None:
    destination = Path(path)
    if destination.exists() or destination.is_symlink():
        raise ReleaseContractError(f"{label} already exists: {destination}")
    try:
        with destination.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ReleaseContractError(f"cannot write {label}: {exc}") from exc


def _candidate_output_path(
    artifact: Path,
    relative_path: str,
    label: str,
) -> Path:
    """Create real candidate parents one component at a time without links."""

    relative = _canonical_relative_path(relative_path, label)
    current = Path(artifact)
    for part in relative.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ReleaseContractError(f"{label} parent contains a symlink")
        if current.exists():
            if not current.is_dir():
                raise ReleaseContractError(f"{label} parent is not a directory")
        else:
            try:
                current.mkdir()
            except OSError as exc:
                raise ReleaseContractError(
                    f"cannot create {label} parent: {exc}"
                ) from exc
    return Path(artifact).joinpath(*relative.parts)


def finalize_candidate_notices(
    artifact: Path,
    policy_path: Path,
    source_root: Path,
    target: str,
    cursor_runtime: VerifiedLinuxCursorRuntime | None,
) -> tuple[Path, ...]:
    """Copy every exact policy notice and Linux provenance before identity."""

    artifact = Path(artifact)
    if artifact.is_symlink() or not artifact.is_dir():
        raise ReleaseContractError(f"candidate artifact is not a real directory: {artifact}")
    archived_policy = source_root / "packaging/component_policy.json"
    if archived_policy.is_symlink() or not archived_policy.is_file():
        raise ReleaseContractError("exact source archive has no component policy")
    if archived_policy.read_bytes() != Path(policy_path).read_bytes():
        raise ReleaseContractError(
            "source-archive component policy differs from validated release input"
        )
    policy = _read_canonical_json_object(archived_policy, "component policy")
    required_fields = {"schema", "version", "targets", "rules", "non_component_rules"}
    if set(policy) != required_fields:
        raise ReleaseContractError("component policy fields do not match release v1")
    if policy["schema"] != COMPONENT_POLICY_SCHEMA or policy["version"] != 1:
        raise ReleaseContractError("component policy schema/version is invalid")
    targets = policy["targets"]
    if not isinstance(targets, list) or target not in targets:
        raise ReleaseContractError(f"component policy does not include target {target}")
    rules = policy["rules"]
    if not isinstance(rules, list):
        raise ReleaseContractError("component policy rules must be a list")

    notices: list[tuple[str, Path, str]] = []
    destinations: set[str] = set()
    for index, rule in enumerate(rules):
        if not isinstance(rule, dict):
            raise ReleaseContractError(f"component policy rule {index} is not an object")
        rows = rule.get("notice_sources")
        if not isinstance(rows, list) or not rows:
            raise ReleaseContractError(
                f"component policy rule {index} has no exact notice_sources"
            )
        for notice_index, row in enumerate(rows):
            label = f"component policy rule {index} notice {notice_index}"
            if not isinstance(row, dict) or set(row) != {
                "source_path",
                "destination_path",
                "sha256",
            }:
                raise ReleaseContractError(f"{label} fields are invalid")
            source = _contained_regular_file(
                source_root,
                row["source_path"],
                f"{label}.source_path",
            )
            destination_path = _canonical_relative_path(
                row["destination_path"],
                f"{label}.destination_path",
            ).as_posix()
            if not destination_path.startswith("THIRD_PARTY_LICENSES/"):
                raise ReleaseContractError(
                    f"{label}.destination_path must be below THIRD_PARTY_LICENSES/"
                )
            expected_sha256 = row["sha256"]
            if not isinstance(expected_sha256, str) or not re.fullmatch(
                r"[0-9a-f]{64}", expected_sha256
            ):
                raise ReleaseContractError(f"{label}.sha256 is invalid")
            if sha256_file(source) != expected_sha256:
                raise ReleaseContractError(f"{label} source SHA-256 mismatch")
            if destination_path in destinations:
                raise ReleaseContractError(
                    f"duplicate component notice destination: {destination_path}"
                )
            destinations.add(destination_path)
            notices.append((destination_path, source, expected_sha256))

    written: list[Path] = []
    for destination_path, source, _digest in sorted(notices):
        destination = _candidate_output_path(
            artifact,
            destination_path,
            "component notice",
        )
        _write_exclusive_file(destination, source.read_bytes(), "component notice")
        written.append(destination)

    if target == "linux-x86_64":
        if cursor_runtime is None:
            raise ReleaseContractError("Linux notice finalization requires cursor runtime")
        provenance = _candidate_output_path(
            artifact,
            "THIRD_PARTY_LICENSES/libxcb-cursor0-PROVENANCE.txt",
            "Linux cursor provenance",
        )
        _write_exclusive_file(
            provenance,
            _linux_cursor_provenance().encode("utf-8"),
            "Linux cursor provenance",
        )
        written.append(provenance)
    elif cursor_runtime is not None:
        raise ReleaseContractError("non-Linux notice finalization rejects cursor runtime")
    return tuple(written)


def _copy_evidence_file(source: Path, destination: Path, label: str) -> str:
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ReleaseContractError(f"{label} is not a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise ReleaseContractError(f"retained evidence already exists: {destination}")
    try:
        with source.open("rb") as reader, destination.open("xb") as writer:
            for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    except OSError as exc:
        raise ReleaseContractError(f"cannot retain {label}: {exc}") from exc
    source_digest = sha256_file(source)
    destination_digest = sha256_file(destination)
    if source_digest != destination_digest:
        raise ReleaseContractError(f"retained {label} digest mismatch")
    return destination_digest


def retain_pyinstaller_evidence(work_path: Path, build_dir: Path) -> Path:
    """Retain and hash the exact PyInstaller TOCs and warning disposition."""

    work_root = Path(work_path) / Path(SPEC_FILE).stem
    if work_root.is_symlink() or not work_root.is_dir():
        raise ReleaseContractError(f"PyInstaller work directory is missing: {work_root}")
    build_dir = Path(build_dir)
    evidence_dir = build_dir / "evidence/pyinstaller"
    if evidence_dir.exists() or evidence_dir.is_symlink():
        raise ReleaseContractError("PyInstaller evidence directory already exists")
    evidence_dir.mkdir(parents=True)
    packet_root = build_dir.parent

    toc_rows: list[dict[str, str]] = []
    for kind in _REQUIRED_PYINSTALLER_TOC_KINDS:
        matches = sorted(work_root.glob(f"{kind}-*.toc"))
        if len(matches) != 1:
            raise ReleaseContractError(
                f"expected exactly one retained {kind} TOC, found {len(matches)}"
            )
        source = matches[0]
        destination = evidence_dir / source.name
        digest = _copy_evidence_file(source, destination, f"{kind} TOC")
        toc_rows.append(
            {
                "kind": kind,
                "path": destination.relative_to(packet_root).as_posix(),
                "sha256": digest,
            }
        )

    warning_matches = sorted(work_root.glob("warn-*.txt"))
    if len(warning_matches) != 1:
        raise ReleaseContractError(
            "expected exactly one PyInstaller warning file, "
            f"found {len(warning_matches)}"
        )
    warning_source = warning_matches[0]
    warning_destination = evidence_dir / warning_source.name
    warning_digest = _copy_evidence_file(
        warning_source,
        warning_destination,
        "PyInstaller warning file",
    )
    disposition = (
        "RETAINED_REVIEW_REQUIRED"
        if warning_destination.stat().st_size
        else "RETAINED_NO_WARNINGS"
    )
    index_path = build_dir / "pyinstaller-evidence-index.json"
    write_canonical_json(
        index_path,
        {
            "schema": PYINSTALLER_EVIDENCE_SCHEMA,
            "tocs": toc_rows,
            "warning": {
                "path": warning_destination.relative_to(packet_root).as_posix(),
                "sha256": warning_digest,
                "disposition": disposition,
            },
        },
    )
    return index_path


def _normalized_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _distribution_top_level_imports(
    distribution: importlib_metadata.Distribution,
) -> list[str]:
    roots: set[str] = set()
    for item in distribution.files or ():
        parts = item.parts
        if not parts or parts[0].startswith(("..", ".")):
            continue
        first = parts[0]
        if first.endswith((".dist-info", ".egg-info", ".data")):
            continue
        if first.endswith(".py"):
            roots.add(first.removesuffix(".py"))
        elif len(parts) > 1 and first.isidentifier():
            roots.add(first)
    return sorted(roots)


def _distribution_license_files(
    distribution: importlib_metadata.Distribution,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for item in distribution.files or ():
        relative = item.as_posix()
        if not _LICENSE_FILE_PATTERN.search(relative):
            continue
        located = Path(distribution.locate_file(item))
        if located.is_symlink() or not located.is_file():
            continue
        records.append(
            {
                "distribution_path": relative,
                "sha256": sha256_file(located),
                "size": located.stat().st_size,
            }
        )
    return sorted(records, key=lambda value: str(value["distribution_path"]))


def _capture_distribution_metadata() -> dict[str, object]:
    """Capture deterministic build-environment metadata without ownership claims."""

    records: list[dict[str, object]] = []
    for distribution in importlib_metadata.distributions():
        name = distribution.metadata.get("Name")
        if not name:
            raise ReleaseContractError("installed distribution has no Name metadata")
        canonical_name = _normalized_distribution_name(name)
        license_field = distribution.metadata.get("License") or ""
        records.append(
            {
                "canonical_name": canonical_name,
                "license_classifiers": sorted(
                    value
                    for value in distribution.metadata.get_all("Classifier", [])
                    if value.startswith("License ::")
                ),
                "license_expression": distribution.metadata.get("License-Expression"),
                "license_field_first_line": license_field.splitlines()[:1],
                "license_files": _distribution_license_files(distribution),
                "name": name,
                "purl": f"pkg:pypi/{canonical_name}@{distribution.version}",
                "requires_python": distribution.metadata.get("Requires-Python"),
                "top_level_imports": _distribution_top_level_imports(distribution),
                "version": distribution.version,
            }
        )
    records.sort(key=lambda value: (str(value["canonical_name"]), str(value["version"])))
    return {
        "schema": DISTRIBUTION_METADATA_SCHEMA,
        "distribution_count": len(records),
        "distributions": records,
    }


def _raw_source_text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_identity(path: Path, label: str) -> dict[str, object]:
    """Return a no-follow identity for one regular source file."""

    try:
        before = Path(path).lstat()
    except OSError as exc:
        raise ReleaseContractError(f"cannot inspect {label}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode):
        raise ReleaseContractError(f"{label} is an unsafe symlink")
    if not stat.S_ISREG(before.st_mode):
        raise ReleaseContractError(f"{label} is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseContractError(f"cannot open {label} without following links: {exc}") from exc
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    try:
        stream = os.fdopen(descriptor, "rb")
        descriptor = -1
        with stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or any(
                getattr(before, field) != getattr(opened, field)
                for field in identity_fields
            ):
                raise ReleaseContractError(f"{label} changed before it was opened")
            digest_builder = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest_builder.update(chunk)
            after_read = os.fstat(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        after_path = Path(path).lstat()
    except OSError as exc:
        raise ReleaseContractError(f"cannot re-inspect {label}: {exc}") from exc
    if any(
        getattr(opened, field) != getattr(after_read, field)
        or getattr(opened, field) != getattr(after_path, field)
        for field in identity_fields
    ):
        raise ReleaseContractError(f"{label} changed while evidence was captured")
    return {
        "entry_type": "regular-file",
        "sha256": digest_builder.hexdigest(),
        "size": opened.st_size,
    }


def _open_distribution_regular_file(
    distribution: importlib_metadata.Distribution,
    distribution_path: str,
    located_path: Path,
    label: str,
) -> int:
    """Open one distribution-relative file without following any path link."""

    relative = _canonical_relative_path(distribution_path, label)
    root = Path(os.path.abspath(os.fspath(distribution.locate_file(""))))
    try:
        root_metadata = root.lstat()
    except OSError as exc:
        raise ReleaseContractError(f"cannot inspect {label} root: {exc}") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise ReleaseContractError(f"{label} root is not a real directory")
    expected_path = root.joinpath(*relative.parts)
    if _host_source_key(expected_path) != _host_source_key(located_path):
        raise ReleaseContractError(f"{label} locate_file result is inconsistent")

    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    if _SUPPORTS_OPENAT:
        directory_flags = file_flags | os.O_DIRECTORY
        try:
            current_descriptor = os.open(root, directory_flags)
        except OSError as exc:
            raise ReleaseContractError(f"cannot open {label} root: {exc}") from exc
        try:
            opened_root = os.fstat(current_descriptor)
            if (
                not stat.S_ISDIR(opened_root.st_mode)
                or (opened_root.st_dev, opened_root.st_ino)
                != (root_metadata.st_dev, root_metadata.st_ino)
            ):
                raise ReleaseContractError(f"{label} root changed while opening")
            for part in relative.parts[:-1]:
                try:
                    next_descriptor = os.open(
                        part,
                        directory_flags,
                        dir_fd=current_descriptor,
                    )
                except OSError as exc:
                    raise ReleaseContractError(
                        f"{label} parent is missing, changed, or an unsafe symlink: {part}"
                    ) from exc
                next_metadata = os.fstat(next_descriptor)
                if not stat.S_ISDIR(next_metadata.st_mode):
                    os.close(next_descriptor)
                    raise ReleaseContractError(f"{label} parent is not a directory")
                os.close(current_descriptor)
                current_descriptor = next_descriptor
            try:
                descriptor = os.open(
                    relative.parts[-1],
                    file_flags,
                    dir_fd=current_descriptor,
                )
            except OSError as exc:
                raise ReleaseContractError(
                    f"{label} is missing, changed, or an unsafe symlink"
                ) from exc
        finally:
            os.close(current_descriptor)
    else:
        current = root
        final_metadata: os.stat_result | None = None
        for index, part in enumerate(relative.parts):
            current = current / part
            try:
                metadata = current.lstat()
            except OSError as exc:
                raise ReleaseContractError(f"cannot inspect {label}: {exc}") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise ReleaseContractError(f"{label} contains an unsafe symlink")
            if index < len(relative.parts) - 1 and not stat.S_ISDIR(
                metadata.st_mode
            ):
                raise ReleaseContractError(f"{label} parent is not a directory")
            final_metadata = metadata
        try:
            descriptor = os.open(current, file_flags)
        except OSError as exc:
            raise ReleaseContractError(f"cannot open {label}: {exc}") from exc
        opened = os.fstat(descriptor)
        if final_metadata is None or (
            opened.st_dev,
            opened.st_ino,
            opened.st_mode,
        ) != (
            final_metadata.st_dev,
            final_metadata.st_ino,
            final_metadata.st_mode,
        ):
            os.close(descriptor)
            raise ReleaseContractError(f"{label} changed while opening")

    opened_file = os.fstat(descriptor)
    if not stat.S_ISREG(opened_file.st_mode):
        os.close(descriptor)
        raise ReleaseContractError(f"{label} is not a regular file")
    return descriptor


def _descriptor_identity(descriptor: int, label: str) -> dict[str, object]:
    before = os.fstat(descriptor)
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise ReleaseContractError(f"cannot hash {label}: {exc}") from exc
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in identity_fields):
        raise ReleaseContractError(f"{label} changed while evidence was captured")
    return {
        "entry_type": "regular-file",
        "sha256": digest.hexdigest(),
        "size": before.st_size,
    }


def _revalidate_distribution_descriptor(
    distribution: importlib_metadata.Distribution,
    distribution_path: str,
    located_path: Path,
    expected_descriptor: os.stat_result,
    label: str,
) -> None:
    descriptor = _open_distribution_regular_file(
        distribution,
        distribution_path,
        located_path,
        label,
    )
    try:
        observed = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (observed.st_dev, observed.st_ino) != (
        expected_descriptor.st_dev,
        expected_descriptor.st_ino,
    ):
        raise ReleaseContractError(f"{label} changed after evidence capture")


def _distribution_source_identity(
    distribution: importlib_metadata.Distribution,
    distribution_path: str,
    located_path: Path,
    label: str,
) -> dict[str, object]:
    descriptor = _open_distribution_regular_file(
        distribution,
        distribution_path,
        located_path,
        label,
    )
    try:
        opened = os.fstat(descriptor)
        identity = _descriptor_identity(descriptor, label)
    finally:
        os.close(descriptor)
    _revalidate_distribution_descriptor(
        distribution,
        distribution_path,
        located_path,
        opened,
        label,
    )
    return identity


def _copy_distribution_evidence_file(
    distribution: importlib_metadata.Distribution,
    distribution_path: str,
    located_path: Path,
    destination: Path,
    label: str,
) -> dict[str, object]:
    descriptor = _open_distribution_regular_file(
        distribution,
        distribution_path,
        located_path,
        label,
    )
    try:
        opened = os.fstat(descriptor)
        if destination.exists() or destination.is_symlink():
            raise ReleaseContractError(f"{label} already exists: {destination}")
        digest = hashlib.sha256()
        try:
            os.lseek(descriptor, 0, os.SEEK_SET)
            with destination.open("xb") as writer:
                for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            after = os.fstat(descriptor)
        except OSError as exc:
            raise ReleaseContractError(f"cannot retain {label}: {exc}") from exc
    finally:
        os.close(descriptor)
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")
    if any(getattr(opened, field) != getattr(after, field) for field in identity_fields):
        raise ReleaseContractError(f"{label} changed while it was retained")
    if sha256_file(destination) != digest.hexdigest():
        raise ReleaseContractError(f"retained {label} digest mismatch")
    _revalidate_distribution_descriptor(
        distribution,
        distribution_path,
        located_path,
        opened,
        label,
    )
    return {
        "entry_type": "regular-file",
        "sha256": digest.hexdigest(),
        "size": opened.st_size,
    }


def _optional_source_identity(raw_source: str) -> dict[str, object]:
    if not os.path.isabs(raw_source):
        return {"entry_type": "unavailable"}
    path = Path(raw_source)
    try:
        metadata = path.lstat()
    except OSError:
        return {"entry_type": "unavailable"}
    if stat.S_ISREG(metadata.st_mode):
        return _source_identity(path, "unassigned COLLECT source")
    if stat.S_ISLNK(metadata.st_mode):
        try:
            target = os.readlink(path)
        except OSError as exc:
            raise ReleaseContractError(
                f"cannot read unassigned COLLECT symlink: {exc}"
            ) from exc
        return {
            "entry_type": "symlink",
            "size": metadata.st_size,
            "target_text_sha256": _raw_source_text_sha256(target),
        }
    return {"entry_type": "unavailable"}


def _retained_evidence_reference(
    packet_root: Path,
    row: object,
    label: str,
) -> Path:
    if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
        raise ReleaseContractError(f"{label} reference fields are invalid")
    expected = row["sha256"]
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ReleaseContractError(f"{label} SHA-256 is invalid")
    path = _contained_regular_file(packet_root, row["path"], label)
    actual = sha256_file(path)
    if actual != expected:
        raise ReleaseContractError(
            f"{label} SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return path


def _load_collect_rows_for_capture(
    evidence_index_path: Path,
    build_dir: Path,
    manifest: ArtifactManifest,
) -> list[dict[str, object]]:
    build_dir = Path(build_dir)
    packet_root = build_dir.parent.resolve(strict=True)
    index_path = Path(evidence_index_path)
    if index_path.is_symlink() or not index_path.is_file():
        raise ReleaseContractError("PyInstaller evidence index is not a regular file")
    try:
        if (
            index_path.resolve(strict=True) != index_path
            or index_path.parent.resolve(strict=True) != build_dir.resolve(strict=True)
        ):
            raise ReleaseContractError(
                "PyInstaller evidence index must be a direct build child"
            )
    except OSError as exc:
        raise ReleaseContractError(
            f"cannot resolve PyInstaller evidence index: {exc}"
        ) from exc
    index = _read_canonical_json_object(index_path, "PyInstaller evidence index")
    if set(index) != {"schema", "tocs", "warning"}:
        raise ReleaseContractError("PyInstaller evidence index fields are invalid")
    if index["schema"] != PYINSTALLER_EVIDENCE_SCHEMA:
        raise ReleaseContractError("PyInstaller evidence index schema is invalid")
    toc_rows = index["tocs"]
    if not isinstance(toc_rows, list) or len(toc_rows) != len(
        _REQUIRED_PYINSTALLER_TOC_KINDS
    ):
        raise ReleaseContractError("PyInstaller evidence must contain five TOCs")
    retained: dict[str, Path] = {}
    observed_kinds: list[str] = []
    for row in toc_rows:
        if not isinstance(row, dict) or set(row) != {"kind", "path", "sha256"}:
            raise ReleaseContractError("PyInstaller TOC evidence fields are invalid")
        kind = row["kind"]
        if not isinstance(kind, str):
            raise ReleaseContractError("PyInstaller TOC kind is invalid")
        observed_kinds.append(kind)
        retained_path = _retained_evidence_reference(
            packet_root,
            {"path": row["path"], "sha256": row["sha256"]},
            f"{kind} TOC",
        )
        if (
            retained_path.parent
            != build_dir.resolve(strict=True) / "evidence/pyinstaller"
            or retained_path.suffix != ".toc"
        ):
            raise ReleaseContractError(
                f"{kind} TOC must be retained below build/evidence/pyinstaller"
            )
        retained[kind] = retained_path
    if observed_kinds != list(_REQUIRED_PYINSTALLER_TOC_KINDS) or len(
        retained
    ) != len(observed_kinds):
        raise ReleaseContractError("PyInstaller TOC kinds/order are invalid")
    warning = index["warning"]
    if not isinstance(warning, dict) or set(warning) != {
        "disposition",
        "path",
        "sha256",
    }:
        raise ReleaseContractError("PyInstaller warning evidence fields are invalid")
    if warning["disposition"] not in _PYINSTALLER_WARNING_DISPOSITIONS:
        raise ReleaseContractError("PyInstaller warning disposition is invalid")
    warning_path = _retained_evidence_reference(
        packet_root,
        {"path": warning["path"], "sha256": warning["sha256"]},
        "PyInstaller warning evidence",
    )
    if warning_path.parent != build_dir.resolve(strict=True) / "evidence/pyinstaller":
        raise ReleaseContractError(
            "PyInstaller warning must be retained below build/evidence/pyinstaller"
        )
    warning_has_bytes = warning_path.stat().st_size > 0
    if warning_has_bytes != (
        warning["disposition"] == "RETAINED_REVIEW_REQUIRED"
    ):
        raise ReleaseContractError(
            "PyInstaller warning disposition disagrees with retained bytes"
        )

    collect_path = retained["COLLECT"]
    try:
        if collect_path.stat().st_size > _MAX_RETAINED_TOC_BYTES:
            raise ReleaseContractError(
                f"COLLECT TOC exceeds {_MAX_RETAINED_TOC_BYTES} bytes"
            )
        parsed = ast.literal_eval(collect_path.read_text(encoding="utf-8"))
    except ReleaseContractError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseContractError(f"cannot read COLLECT TOC: {exc}") from exc
    except (MemoryError, RecursionError, SyntaxError, ValueError) as exc:
        raise ReleaseContractError("COLLECT TOC is not a safe Python literal") from exc
    if (
        not isinstance(parsed, tuple)
        or len(parsed) != 1
        or not isinstance(parsed[0], list)
    ):
        raise ReleaseContractError("COLLECT TOC root shape is invalid")

    manifest_entries = {
        entry.path: entry
        for entry in manifest.entries
        if entry.entry_type != "directory"
    }
    records: list[dict[str, object]] = []
    final_paths: set[str] = set()
    for raw_row in parsed[0]:
        if (
            not isinstance(raw_row, (tuple, list))
            or len(raw_row) != 3
            or any(not isinstance(item, str) or not item for item in raw_row)
        ):
            raise ReleaseContractError("COLLECT TOC entry is invalid")
        destination, raw_source, toc_type = raw_row
        if toc_type not in _PYINSTALLER_COLLECT_TYPES:
            raise ReleaseContractError(
                f"unsupported COLLECT TOC type: {toc_type}"
            )
        destination_path = _canonical_relative_path(
            destination,
            "COLLECT destination",
        ).as_posix()
        candidates = [
            path
            for path in (destination_path, f"_internal/{destination_path}")
            if path in manifest_entries
        ]
        if len(candidates) != 1:
            raise ReleaseContractError(
                f"COLLECT destination has no unique candidate path: {destination_path}"
            )
        final_path = candidates[0]
        if final_path in final_paths:
            raise ReleaseContractError(f"duplicate COLLECT final path: {final_path}")
        final_paths.add(final_path)
        entry_type = "symlink" if toc_type == "SYMLINK" else "regular-file"
        if manifest_entries[final_path].entry_type != entry_type:
            raise ReleaseContractError(
                f"COLLECT entry type disagrees with candidate: {final_path}"
            )
        records.append(
            {
                "entry_type": entry_type,
                "final_path": final_path,
                "raw_source": raw_source,
                "raw_source_text_sha256": _raw_source_text_sha256(raw_source),
                "toc_type": toc_type,
            }
        )
    return sorted(records, key=lambda row: str(row["final_path"]))


def _host_source_key(value: object) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value)))


def _provider_candidates(
    distribution: importlib_metadata.Distribution,
) -> list[dict[str, str]]:
    candidates: set[tuple[str, str]] = set()
    for field in ("Author", "Author-email", "Maintainer", "Maintainer-email"):
        value = distribution.metadata.get(field)
        if isinstance(value, str) and value:
            candidates.add((field, value))
    return [
        {"field": field, "value": value}
        for field, value in sorted(candidates)
    ]


def _capture_contributor_license_files(
    distribution: importlib_metadata.Distribution,
    canonical_name: str,
    version: str,
    evidence_root: Path,
    packet_root: Path,
) -> list[dict[str, object]]:
    component_directory = f"{canonical_name}-{version}"
    _canonical_relative_path(component_directory, "distribution evidence directory")
    records: list[dict[str, object]] = []
    observed_paths: set[str] = set()
    for item in distribution.files or ():
        distribution_path = item.as_posix()
        if not _LICENSE_FILE_PATTERN.search(distribution_path):
            continue
        canonical_path = _canonical_relative_path(
            distribution_path,
            f"{canonical_name} license distribution_path",
        ).as_posix()
        if canonical_path in observed_paths:
            raise ReleaseContractError(
                f"duplicate {canonical_name} license distribution path: {canonical_path}"
            )
        observed_paths.add(canonical_path)
        source = Path(distribution.locate_file(item))
        relative_destination = PurePosixPath(component_directory) / PurePosixPath(
            canonical_path
        )
        destination = _candidate_output_path(
            evidence_root,
            relative_destination.as_posix(),
            f"{canonical_name} retained license",
        )
        source_identity = _copy_distribution_evidence_file(
            distribution,
            canonical_path,
            source,
            destination,
            f"{canonical_name} retained license",
        )
        records.append(
            {
                "distribution_path": canonical_path,
                "retained_path": destination.relative_to(packet_root).as_posix(),
                "sha256": source_identity["sha256"],
                "size": source_identity["size"],
            }
        )
    return sorted(records, key=lambda row: str(row["distribution_path"]))


def capture_python_component_evidence(
    artifact: Path,
    manifest: ArtifactManifest,
    evidence_index_path: Path,
    build_dir: Path,
) -> dict[str, object]:
    """Capture receipt-ready Python contributor evidence for one candidate.

    Input: one finalized artifact/manifest and its retained PyInstaller index.
    Output: one canonicalizable v2 metadata object containing only Python
    distributions that own an observed COLLECT source.
    Side effects: exclusively copies those distributions' regular license files
    below ``build/evidence/python-distributions``.
    Errors: malformed evidence, unsafe/changing sources, duplicate or ambiguous
    ownership, and path/hash/count mismatches fail loud.
    Split trigger: Conda, Debian, application, cursor, and source-template
    ownership remain separate evidence collectors.
    """

    artifact = Path(artifact)
    build_dir = Path(build_dir)
    if artifact.is_symlink() or not artifact.is_dir():
        raise ReleaseContractError("candidate artifact is not a real directory")
    try:
        if artifact.resolve(strict=True) != manifest.artifact_root.resolve(strict=True):
            raise ReleaseContractError("candidate artifact and manifest roots disagree")
    except OSError as exc:
        raise ReleaseContractError(f"cannot resolve candidate artifact: {exc}") from exc
    current_manifest = create_artifact_manifest(artifact)
    if current_manifest.canonical_bytes != manifest.canonical_bytes:
        raise ReleaseContractError("candidate artifact changed before metadata capture")
    collect_rows = _load_collect_rows_for_capture(
        evidence_index_path,
        build_dir,
        manifest,
    )

    distributions: dict[str, dict[str, object]] = {}
    source_owners: dict[str, list[tuple[str, str, Path]]] = {}
    for distribution in importlib_metadata.distributions():
        name = distribution.metadata.get("Name")
        version = distribution.version
        if not isinstance(name, str) or not name:
            raise ReleaseContractError("installed distribution has no Name metadata")
        if not isinstance(version, str) or not version:
            raise ReleaseContractError(f"installed distribution {name} has no version")
        if not _DISTRIBUTION_VERSION_PATTERN.fullmatch(version):
            raise ReleaseContractError(
                f"installed distribution {name} has an unsafe version: {version}"
            )
        canonical_name = _normalized_distribution_name(name)
        if not canonical_name or not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*",
            canonical_name,
        ):
            raise ReleaseContractError(
                f"installed distribution name cannot be normalized: {name}"
            )
        component_id = f"library:{canonical_name}@{version}"
        if component_id in distributions:
            raise ReleaseContractError(
                f"duplicate installed Python distribution: {component_id}"
            )
        distributions[component_id] = {
            "canonical_name": canonical_name,
            "component_id": component_id,
            "distribution": distribution,
            "name": name,
            "version": version,
        }
        for item in distribution.files or ():
            located = Path(distribution.locate_file(item))
            source_owners.setdefault(_host_source_key(located), []).append(
                (component_id, item.as_posix(), located)
            )

    assigned: dict[str, list[dict[str, object]]] = {}
    unassigned: list[dict[str, object]] = []
    for row in collect_rows:
        raw_source = str(row["raw_source"])
        owners = source_owners.get(_host_source_key(raw_source), []) if os.path.isabs(
            raw_source
        ) else []
        unique_owners = {
            (component_id, distribution_path, _host_source_key(located)):
            (component_id, distribution_path, located)
            for component_id, distribution_path, located in owners
        }
        if len(owners) != len(unique_owners):
            raise ReleaseContractError(
                "duplicate Python distribution owner for COLLECT path "
                f"{row['final_path']}"
            )
        if len(unique_owners) > 1:
            raise ReleaseContractError(
                "ambiguous Python distribution owner for COLLECT path "
                f"{row['final_path']}"
            )
        common = {
            "entry_type": row["entry_type"],
            "final_path": row["final_path"],
            "raw_source_text_sha256": row["raw_source_text_sha256"],
            "toc_type": row["toc_type"],
        }
        if unique_owners:
            component_id, distribution_path, located = next(
                iter(unique_owners.values())
            )
            canonical_distribution_path = _canonical_relative_path(
                distribution_path,
                f"{component_id} distribution_path",
            ).as_posix()
            distribution = distributions[component_id]["distribution"]
            source_identity = _distribution_source_identity(
                distribution,
                canonical_distribution_path,
                located,
                f"{component_id} COLLECT source",
            )
            canonical_name = str(distributions[component_id]["canonical_name"])
            assigned.setdefault(component_id, []).append(
                {
                    **common,
                    "distribution_path": canonical_distribution_path,
                    "source_identity": source_identity,
                    "source_locator": (
                        f"python-distribution/{canonical_name}/"
                        f"{canonical_distribution_path}"
                    ),
                }
            )
        else:
            digest = str(row["raw_source_text_sha256"])
            unassigned.append(
                {
                    **common,
                    "source_identity": _optional_source_identity(raw_source),
                    "source_locator": f"unassigned-source/{digest}",
                }
            )

    evidence_parent = build_dir / "evidence"
    if evidence_parent.is_symlink() or not evidence_parent.is_dir():
        raise ReleaseContractError("build evidence directory is not a real directory")
    evidence_root = evidence_parent / "python-distributions"
    if evidence_root.exists() or evidence_root.is_symlink():
        raise ReleaseContractError("Python distribution evidence already exists")
    evidence_root.mkdir()
    packet_root = build_dir.parent.resolve(strict=True)

    components: list[dict[str, object]] = []
    for component_id in sorted(assigned):
        descriptor = distributions[component_id]
        distribution = descriptor["distribution"]
        canonical_name = str(descriptor["canonical_name"])
        version = str(descriptor["version"])
        license_field = distribution.metadata.get("License")
        first_line = None
        if isinstance(license_field, str) and license_field.splitlines():
            first_line = license_field.splitlines()[0] or None
        expression = distribution.metadata.get("License-Expression")
        if not isinstance(expression, str) or not expression:
            expression = None
        classifiers = sorted(
            set(
                value
                for value in distribution.metadata.get_all("Classifier", [])
                if isinstance(value, str) and value.startswith("License ::")
            )
        )
        requires_python = distribution.metadata.get("Requires-Python")
        if not isinstance(requires_python, str) or not requires_python:
            requires_python = None
        collected_files = sorted(
            assigned[component_id],
            key=lambda value: str(value["final_path"]),
        )
        components.append(
            {
                "canonical_name": canonical_name,
                "collected_files": collected_files,
                "component_id": component_id,
                "license_candidates": {
                    "classifiers": classifiers,
                    "expression": expression,
                    "field_first_line": first_line,
                },
                "license_files": _capture_contributor_license_files(
                    distribution,
                    canonical_name,
                    version,
                    evidence_root,
                    packet_root,
                ),
                "name": descriptor["name"],
                "provider_candidates": _provider_candidates(distribution),
                "purl": f"pkg:pypi/{canonical_name}@{version}",
                "requires_python": requires_python,
                "version": version,
            }
        )

    assigned_count = sum(
        len(component["collected_files"]) for component in components
    )
    return {
        "schema": DISTRIBUTION_METADATA_V2_SCHEMA,
        "collect_entry_count": len(collect_rows),
        "component_count": len(components),
        "assigned_collected_entry_count": assigned_count,
        "unassigned_collected_entry_count": len(unassigned),
        "components": components,
        "unassigned_collected_entries": sorted(
            unassigned,
            key=lambda value: str(value["final_path"]),
        ),
    }


def _stage_candidate(
    generated_dist: Path,
    artifact_parent: Path,
    validated: ValidatedReleaseInputs,
) -> Path:
    source_name = "EasyQC.app" if validated.bundle.target == "macos-arm64" else APP_NAME
    source = Path(generated_dist) / source_name
    if source.is_symlink() or not source.is_dir():
        raise ReleaseContractError(f"PyInstaller candidate is missing: {source}")
    suffix = ".app" if validated.bundle.target == "macos-arm64" else ""
    destination = artifact_parent / (
        f"{APP_NAME}-v{validated.bundle.version}-{validated.bundle.target}{suffix}"
    )
    if destination.exists() or destination.is_symlink():
        raise ReleaseContractError(f"candidate destination already exists: {destination}")
    try:
        shutil.move(str(source), str(destination))
    except OSError as exc:
        raise ReleaseContractError(f"cannot finalize candidate directory: {exc}") from exc
    return destination


def _cleanup_release_intermediates(paths: tuple[Path, ...]) -> None:
    for path in paths:
        candidate = Path(path)
        if candidate.is_symlink():
            raise ReleaseContractError(f"release intermediate is a symlink: {candidate}")
        if not candidate.exists():
            continue
        if not candidate.is_dir():
            raise ReleaseContractError(
                f"release intermediate is not a directory: {candidate}"
            )
        try:
            shutil.rmtree(candidate)
        except OSError as exc:
            raise ReleaseContractError(
                f"cannot remove release intermediate {candidate}: {exc}"
            ) from exc


def build_candidate(
    validated_inputs: ValidatedReleaseInputs,
    packet_root: Path,
) -> BuildReceipt:
    """Build/finalize one candidate and publish its PASS BuildReceipt last.

    This release path performs no dependency resolution, inventory, smoke,
    native support check, signing, or publication.  A failed/partial build can
    leave diagnostic packet files but never a PASS receipt.
    """

    _revalidate_build_inputs(validated_inputs)
    tools = _release_tool_versions(validated_inputs.runtime_identity.artifact.path)
    build_dir, artifact_parent = _create_release_packet(packet_root)
    source_root = build_dir / "source"
    work_path = build_dir / "pyinstaller-work"
    generated_dist = build_dir / "pyinstaller-dist"
    cursor_runtime: VerifiedLinuxCursorRuntime | None = None

    try:
        _extract_source_archive(validated_inputs.source_archive, source_root)
        _verify_release_orchestrator_source(source_root)
        copied_policy = build_dir / "component-policy.json"
        _write_exclusive_file(
            copied_policy,
            validated_inputs.component_policy.read_bytes(),
            "retained component policy",
        )
        if validated_inputs.bundle.target == "linux-x86_64":
            if validated_inputs.linux_cursor_deb is None:
                raise ReleaseContractError("Linux release has no cursor deb")
            cursor_runtime = verify_linux_cursor_deb(
                validated_inputs.linux_cursor_deb,
                build_root=build_dir,
            )
        run_pyinstaller(
            cursor_runtime,
            python_executable=validated_inputs.runtime_identity.artifact.path,
            source_root=source_root,
            work_path=work_path,
            dist_path=generated_dist,
        )
        artifact = _stage_candidate(
            generated_dist,
            artifact_parent,
            validated_inputs,
        )
        finalize_candidate_notices(
            artifact,
            validated_inputs.component_policy,
            source_root,
            validated_inputs.bundle.target,
            cursor_runtime,
        )
        evidence_index_path = retain_pyinstaller_evidence(work_path, build_dir)
        manifest = create_artifact_manifest(artifact)
        manifest_path = build_dir / "artifact-manifest.json"
        write_artifact_manifest(manifest_path, manifest)
        metadata_path = build_dir / "distribution-metadata.json"
        write_canonical_json(
            metadata_path,
            capture_python_component_evidence(
                artifact,
                manifest,
                evidence_index_path,
                build_dir,
            ),
        )

        _cleanup_release_intermediates(
            (
                source_root,
                work_path,
                generated_dist,
                build_dir / "linux-cursor-sysroot",
            )
        )
        _revalidate_build_inputs(validated_inputs)

        packet_root = build_dir.parent
        receipt = BuildReceipt(
            status="PASS",
            evidence_class="release",
            target=validated_inputs.bundle.target,
            candidate_id=manifest.candidate_id,
            artifact_path=artifact.relative_to(packet_root).as_posix(),
            artifact_manifest_path=manifest_path.relative_to(packet_root).as_posix(),
            artifact_manifest_sha256=sha256_file(manifest_path),
            component_policy_path=copied_policy.relative_to(packet_root).as_posix(),
            component_policy_sha256=sha256_file(copied_policy),
            toc_index_path=evidence_index_path.relative_to(packet_root).as_posix(),
            toc_index_sha256=sha256_file(evidence_index_path),
            distribution_metadata_path=metadata_path.relative_to(packet_root).as_posix(),
            distribution_metadata_sha256=sha256_file(metadata_path),
            source_revision=validated_inputs.bundle.source_revision,
            source_archive_sha256=validated_inputs.bundle.source_archive.sha256,
            runtime_identity_sha256=(
                validated_inputs.bundle.runtime_identity_file.sha256
            ),
            locks={
                scope: validated_inputs.bundle.lock_files[scope].sha256
                for scope in ("runtime", "build", "test")
            },
            tools=tools,
        )
        write_canonical_json(build_dir / "build-receipt.json", receipt.as_json_object())
        return receipt
    except SystemExit as exc:
        raise ReleaseContractError(
            f"release build primitive failed with exit code {exc.code}"
        ) from exc


def verify_linux_bundle(
    artifact: Path,
    cursor_runtime: VerifiedLinuxCursorRuntime,
) -> None:
    """Verify cursor identity, notices and artifact-only xcb dependency closure."""
    if SYSTEM != "Linux":
        _abort("Linux bundle 验证只能用于 Linux 产物")
    artifact = Path(artifact)
    internal = artifact / "_internal"
    packaged_cursor = internal / LINUX_CURSOR_SONAME
    plugin = internal / "PySide6/Qt/plugins/platforms/libqxcb.so"
    license_dir = artifact / "THIRD_PARTY_LICENSES"
    notice_path = license_dir / "xcb-util-cursor.txt"
    provenance_path = license_dir / "libxcb-cursor0-PROVENANCE.txt"

    required_files = (packaged_cursor, plugin, notice_path, provenance_path)
    missing = [str(path) for path in required_files if not path.is_file()]
    if missing:
        _abort(f"Linux bundle 缺少必需文件: {', '.join(missing)}")
    artifact_resolved = artifact.resolve()
    for path in required_files:
        if not path.resolve().is_relative_to(artifact_resolved):
            _abort(f"Linux bundle 文件解析到了产物目录之外: {path}")
    _verify_cursor_runtime_files(cursor_runtime)
    if _sha256_file(packaged_cursor) != LINUX_CURSOR_LIBRARY_SHA256:
        _abort("Linux bundle cursor ELF SHA-256 不匹配")

    expected_notice = LINUX_CURSOR_NOTICE.read_bytes()
    if cursor_runtime.copyright_path.read_bytes() != expected_notice:
        _abort("Linux cursor runtime copyright 在打包前后发生变化")
    if notice_path.read_bytes() != expected_notice:
        _abort("Linux bundle MIT/X notice 不完整或不匹配")
    if provenance_path.read_text(encoding="utf-8") != _linux_cursor_provenance():
        _abort("Linux bundle provenance 不匹配")

    ldd_environment = os.environ.copy()
    ldd_environment["LD_LIBRARY_PATH"] = str(internal.resolve())
    ldd_environment.pop("LD_PRELOAD", None)
    ldd_environment.pop("LD_AUDIT", None)
    try:
        result = subprocess.run(
            ["ldd", str(plugin)],
            capture_output=True,
            text=True,
            env=ldd_environment,
            timeout=30,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _abort(f"Linux bundle ldd 验证失败: {exc}")
    if result.returncode != 0 or "=> not found" in result.stdout:
        detail = (result.stderr or result.stdout).strip()
        _abort("Linux bundle ldd 依赖不完整" + (f": {detail}" if detail else ""))

    cursor_line = next(
        (
            line
            for line in result.stdout.splitlines()
            if line.strip().startswith(f"{LINUX_CURSOR_SONAME} =>")
        ),
        "",
    )
    cursor_target = cursor_line.partition("=>")[2].strip().split(maxsplit=1)[0]
    if not cursor_target:
        _abort("Linux bundle ldd 未解析到 cursor ELF")
    try:
        cursor_target_path = Path(cursor_target).resolve(strict=True)
    except OSError as exc:
        _abort(f"Linux bundle ldd cursor 路径无效: {exc}")
    if cursor_target_path != packaged_cursor.resolve():
        _abort("Linux bundle ldd 使用了产物目录之外的 cursor ELF")
    info("✓ Linux bundle cursor、notice、provenance 与 ldd 闭包已验证")


def _artifact_manifest(
    artifact: Path,
) -> ArtifactManifest:
    """Return the shared canonical no-follow artifact identity."""
    try:
        return create_artifact_manifest(artifact)
    except ArtifactManifestError as exc:
        _abort(f"无法生成 artifact manifest: {exc}")


def _verify_artifact_unchanged_after_smoke(
    artifact: Path,
    before: ArtifactManifest,
) -> None:
    """Reject every artifact mutation produced by packaging smoke tests."""
    artifact = Path(artifact)
    after = _artifact_manifest(artifact)
    if before.candidate_id == after.candidate_id:
        info("✓ packaging smoke 未改变候选产物")
        return

    before_entries = {entry.path: entry for entry in before.entries}
    after_entries = {entry.path: entry for entry in after.entries}
    before_paths = set(before_entries)
    after_paths = set(after_entries)
    added = after_paths - before_paths
    removed = before_paths - after_paths
    changed = {
        path
        for path in before_paths & after_paths
        if before_entries[path] != after_entries[path]
    }
    unexpected = sorted(added | removed | changed)
    _abort(
        "packaging smoke 改变了候选产物: "
        + ", ".join(unexpected[:5])
    )


def _apparent_size_bytes(root: Path) -> int:
    """Sum each entry's own metadata size without following symlinks."""
    total = root.lstat().st_size
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                total += _apparent_size_bytes(Path(entry.path))
            else:
                total += entry.stat(follow_symlinks=False).st_size
    return total

def verify_output():
    """检查产物是否存在"""
    artifact = DIST_DIR / APP_NAME
    exe = artifact / EXE_NAME
    internal = artifact / "_internal"

    if not exe.exists():
        error(f"未找到可执行文件: {exe}")
        sys.exit(1)

    size_bytes = _apparent_size_bytes(artifact)
    size_mb = size_bytes / (1024 * 1024)

    info(f"✓ 可执行文件: {exe}")
    info(f"✓ 内部目录: {internal}")
    info(f"  总大小:   {size_mb:.0f} MB")

    return size_mb

def rename_output(version: str):
    """重命名 dist/EasyQC -> dist/EasyQC-v1.0.0-linux-x86_64"""
    src = DIST_DIR / APP_NAME
    dst = DIST_DIR / f"{APP_NAME}-v{version}-{OS_LABEL}-{ARCH}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.move(str(src), str(dst))
    info(f"输出目录: {dst}")
    return dst

def _terminate_process(process: subprocess.Popen[str]) -> None:
    """Terminate one smoke process group and leave no child behind."""
    if process.poll() is not None:
        process.communicate()
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    else:
        process.terminate()
    try:
        process.communicate(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        process.kill()
    process.communicate()


def _expect_process_alive(command: list[str], env: dict[str, str], label: str) -> None:
    """Require one packaged GUI command to remain alive for the smoke interval."""
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            shell=False,
            start_new_session=os.name == "posix",
        )
    except OSError as exc:
        _abort(f"{label} 启动失败: {exc}")
    try:
        stdout, stderr = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        _terminate_process(process)
        info(f"✓ {label} 事件循环验证通过")
        return
    combined = (stdout or "") + (stderr or "")
    _abort(
        f"{label} 未保持运行，打包验证失败"
        + (f": {combined.strip()}" if combined.strip() else "")
    )


def smoke_test(binary: Path) -> None:
    """Validate help, offscreen Qt and the Linux native xcb event loop."""
    help_environment = os.environ.copy()
    help_environment.pop("LD_LIBRARY_PATH", None)
    help_environment.pop("LD_PRELOAD", None)
    help_environment.pop("LD_AUDIT", None)
    try:
        result = subprocess.run(
            [str(binary), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
            env=help_environment,
            shell=False,
        )
        combined = (result.stdout + result.stderr).lower()
        if result.returncode != 0 or "easyqc" not in combined:
            error("二进制 --help 验证失败")
            sys.exit(1)
        info("✓ 二进制验证通过 (--help)")
    except subprocess.TimeoutExpired:
        error("二进制 --help 启动超时")
        sys.exit(1)
    except Exception as e:
        error(f"二进制 --help 验证失败: {e}")
        sys.exit(1)

    preview_environment = {
        **os.environ,
        "DISPLAY": "",
        "QT_QPA_PLATFORM": "offscreen",
    }
    preview_environment.pop("LD_LIBRARY_PATH", None)
    preview_environment.pop("LD_PRELOAD", None)
    preview_environment.pop("LD_AUDIT", None)
    _expect_process_alive(
        [str(binary), "--ui", "qt-preview"],
        preview_environment,
        "Qt Preview offscreen",
    )

    if SYSTEM != "Linux":
        return

    xvfb_run = shutil.which("xvfb-run")
    if xvfb_run is None:
        _abort("Linux native xcb smoke 需要 xvfb-run")
    native_environment = {
        **os.environ,
        "QT_QPA_PLATFORM": "xcb",
    }
    native_environment.pop("LD_LIBRARY_PATH", None)
    native_environment.pop("LD_PRELOAD", None)
    native_environment.pop("LD_AUDIT", None)
    _expect_process_alive(
        [xvfb_run, "-a", str(binary), "--ui", "qt-preview"],
        native_environment,
        "Qt Preview native xcb",
    )

# ---------------------------------------------------------------------------
# 平台提示
# ---------------------------------------------------------------------------
def platform_notes():
    print()
    info(f"当前平台: {SYSTEM} ({ARCH}) — 打包产物仅适用于 {SYSTEM}")
    print()
    print(f"  {_c('bold', '要为其他平台打包，请在对应系统上运行本脚本:')}")
    print(f"    • Linux:   python build.py --linux-cursor-deb <approved-deb-path>")
    print(f"    • macOS:   python build.py")
    print(f"    • Windows: python build.py")
    print()

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        description=f"EasyQC 一键打包脚本 — 当前平台: {SYSTEM} ({ARCH})"
    )
    parser.add_argument("--clean", action="store_true", help="清理旧构建产物后重新打包")
    parser.add_argument("--version", default="1.0.0", help="版本号 (默认 1.0.0)")
    parser.add_argument("--skip-smoke", action="store_true", help="跳过二进制冒烟测试")
    parser.add_argument(
        "--linux-cursor-deb",
        type=Path,
        help="Linux 必需：官方 libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb",
    )
    parser.add_argument(
        "--release-input",
        type=Path,
        help="release：canonical easyqc-release-input-v1 路径",
    )
    parser.add_argument(
        "--release-input-sha256",
        help="release：release input 的小写 SHA-256",
    )
    parser.add_argument(
        "--packet-root",
        type=Path,
        help="release：必须尚不存在的输出 packet 根目录",
    )
    args = parser.parse_args(argv)

    release_values = (
        args.release_input,
        args.release_input_sha256,
        args.packet_root,
    )
    if any(value is not None for value in release_values):
        if not all(value is not None for value in release_values):
            _abort(
                "release 构建必须同时提供 --release-input、"
                "--release-input-sha256 和 --packet-root"
            )
        if args.skip_smoke or args.clean or args.linux_cursor_deb is not None:
            _abort(
                "release 构建拒绝 legacy --skip-smoke/--clean/--linux-cursor-deb 参数"
            )
        header(f"EasyQC exact release build — {SYSTEM} ({ARCH})")
        try:
            bundle = load_release_input(
                args.release_input,
                args.release_input_sha256,
            )
            validated = validate_release_inputs(bundle)
            receipt = build_candidate(validated, args.packet_root)
        except ReleaseContractError as exc:
            _abort(f"release 构建失败: {exc}")
        info(f"✓ Candidate ID: {receipt.candidate_id}")
        info(f"✓ BuildReceipt: {args.packet_root / 'build/build-receipt.json'}")
        return

    header(f"EasyQC PyInstaller 打包 — {SYSTEM} ({ARCH})")

    # 1. 环境检查
    info("检查环境...")
    check_python()
    check_pyinstaller()
    check_deps()
    check_tkinter()

    # 2. 清理
    if args.clean:
        clean()

    # 3. 归一化目标平台输入
    cursor_runtime = None
    if SYSTEM == "Linux":
        if args.linux_cursor_deb is None:
            _abort("Linux 构建必须提供 --linux-cursor-deb PATH")
        cursor_runtime = verify_linux_cursor_deb(args.linux_cursor_deb)
    elif args.linux_cursor_deb is not None:
        _abort("--linux-cursor-deb 只允许用于 Linux x86_64 构建")
    check_qt_platform_dependencies(cursor_runtime)

    # 4. 打包
    header("开始打包")
    run_pyinstaller(cursor_runtime)

    # 5. 验证
    header("验证产物")
    artifact = DIST_DIR / APP_NAME
    write_common_license_bundle(artifact)
    verify_common_bundle(artifact)
    if cursor_runtime is not None:
        write_linux_license_bundle(artifact, cursor_runtime)
        verify_linux_bundle(artifact, cursor_runtime)
    artifact_before_smoke = _artifact_manifest(artifact)

    # 6. 冒烟测试
    if not args.skip_smoke:
        exe = DIST_DIR / APP_NAME / EXE_NAME
        smoke_test(exe)
        _verify_artifact_unchanged_after_smoke(artifact, artifact_before_smoke)
        verify_common_bundle(artifact)
    verify_output()

    # 7. 重命名
    out = rename_output(args.version)

    # 8. 清理构建临时文件
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    # 9. 完成
    header("打包完成 ✓")
    print(f"  平台:   {SYSTEM} ({ARCH})")
    print(f"  版本:   v{args.version}")
    print(f"  输出:   {out}")
    print(f"  启动:   {out / EXE_NAME}")
    print()
    platform_notes()

if __name__ == "__main__":
    main()
