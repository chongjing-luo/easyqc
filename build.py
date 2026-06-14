#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC 一键打包脚本 — 支持 Linux / macOS / Windows

用法:
    python build.py              # 打包当前平台
    python build.py --clean      # 清理后重新打包
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
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

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
        warn("PyInstaller 未安装，正在安装...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        return check_pyinstaller()

def check_deps():
    for pkg in ["pandas", "numpy"]:
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

def run_pyinstaller():
    spec = SCRIPT_DIR / SPEC_FILE
    if not spec.exists():
        error(f"未找到配置: {spec}")
        sys.exit(1)

    info("PyInstaller 打包中（可能需要几分钟）...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--log-level=WARN",
        str(spec),
    ]
    result = subprocess.run(cmd, cwd=str(SCRIPT_DIR))
    if result.returncode != 0:
        error("PyInstaller 打包失败")
        sys.exit(1)

def verify_output():
    """检查产物是否存在"""
    exe = DIST_DIR / APP_NAME / EXE_NAME
    internal = DIST_DIR / APP_NAME / "_internal"

    if not exe.exists():
        error(f"未找到可执行文件: {exe}")
        sys.exit(1)

    size_bytes = sum(f.stat().st_size for f in (DIST_DIR / APP_NAME).rglob("*") if f.is_file())
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

def smoke_test(binary: Path):
    """快速验证二进制可运行（仅检查 --help 文本输出，无需显示器）"""
    try:
        result = subprocess.run(
            [str(binary), "--help"],
            capture_output=True, text=True, timeout=15,
            env={**os.environ, "DISPLAY": ""},  # 无头环境友好
        )
        combined = (result.stdout + result.stderr).lower()
        if "easyqc" in combined:
            info("✓ 二进制验证通过 (--help)")
        else:
            warn("二进制 --help 输出异常，请手动验证")
    except subprocess.TimeoutExpired:
        warn("二进制启动超时，请手动验证")
    except Exception as e:
        warn(f"二进制验证跳过: {e}")

# ---------------------------------------------------------------------------
# 平台提示
# ---------------------------------------------------------------------------
def platform_notes():
    print()
    info(f"当前平台: {SYSTEM} ({ARCH}) — 打包产物仅适用于 {SYSTEM}")
    print()
    print(f"  {_c('bold', '要为其他平台打包，请在对应系统上运行本脚本:')}")
    print(f"    • Linux:   python build.py")
    print(f"    • macOS:   python build.py")
    print(f"    • Windows: python build.py")
    print()

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description=f"EasyQC 一键打包脚本 — 当前平台: {SYSTEM} ({ARCH})"
    )
    parser.add_argument("--clean", action="store_true", help="清理旧构建产物后重新打包")
    parser.add_argument("--version", default="1.0.0", help="版本号 (默认 1.0.0)")
    parser.add_argument("--skip-smoke", action="store_true", help="跳过二进制冒烟测试")
    args = parser.parse_args()

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

    # 3. 打包
    header("开始打包")
    run_pyinstaller()

    # 4. 验证
    header("验证产物")
    verify_output()

    # 5. 冒烟测试
    if not args.skip_smoke:
        exe = DIST_DIR / APP_NAME / EXE_NAME
        smoke_test(exe)

    # 6. 重命名
    out = rename_output(args.version)

    # 7. 清理构建临时文件
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    # 8. 完成
    header("打包完成 ✓")
    print(f"  平台:   {SYSTEM} ({ARCH})")
    print(f"  版本:   v{args.version}")
    print(f"  输出:   {out}")
    print(f"  启动:   {out / EXE_NAME}")
    print()
    platform_notes()

if __name__ == "__main__":
    main()
