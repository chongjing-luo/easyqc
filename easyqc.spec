# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for EasyQC — 跨平台打包 (Linux / macOS / Windows)

用法:
    pip install pyinstaller
    pyinstaller easyqc.spec

输出:
    dist/EasyQC/           # 目录模式 (推荐 — 启动更快，方便调试)
    dist/EasyQC/EasyQC     # Linux 可执行文件
    dist/EasyQC/EasyQC.app # macOS .app 包
    dist/EasyQC/EasyQC.exe # Windows 可执行文件
"""

import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

_PROJECT_ROOT = Path(SPECPATH)
_is_macos = sys.platform == "darwin"
_is_windows = sys.platform == "win32"
APP_NAME = "EasyQC"

# ============== hidden imports ==============
hiddenimports = [
    # --- tkinter 子模块 (PyInstaller 有时检测不到) ---
    "tkinter", "tkinter.ttk", "tkinter.scrolledtext",
    "tkinter.messagebox", "tkinter.filedialog", "tkinter.simpledialog",
    "_tkinter",
    # --- pandas ---
    "pandas._libs.tslibs",
]

# 自动收集子模块：pandas 有复杂的 C 扩展
for _pkg in ["pandas"]:
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception:
        pass

# ============== data files ==============
datas = []

# 项目模板文件
_template = _PROJECT_ROOT / "template" / "hcpall_template.scene"
if _template.exists():
    datas.append((str(_template), "template"))

# ============== excludes ==============
excludes = [
    "tests", "test", "unittest", "pytest", "doctest",
    "setuptools", "pip", "wheel", "pkg_resources",
    "tkinter.test", "tkinter.test.test_tkinter",
    "numpy.tests", "numpy.f2py",
    "pandas.tests",
    "lib2to3", "distutils", "ensurepip",
]

# ============== Analysis ==============
a = Analysis(
    [str(_PROJECT_ROOT / "easyqc.py")],
    pathex=[str(_PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

# ============== PYZ ==============
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# ============== EXE ==============
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,                     # GUI 应用，不显示终端
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # macOS 特定
    **({"argv_emulation": False} if _is_macos else {}),
)

# ============== COLLECT (目录模式) ==============
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)

# ============== macOS .app Bundle ==============
app = None
if _is_macos:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier="edu.bnu.cogneuro.easyqc",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": "EasyQC",
            "CFBundleExecutable": APP_NAME,
            "NSHumanReadableCopyright": "MIT License. (c) 2024-2026 Chongjing Luo.",
            "LSEnvironment": {
                # tkinter 在 macOS 上可能需要 framework 路径
                "TCL_LIBRARY": "/System/Library/Frameworks/Tcl.framework/Versions/Current",
                "TK_LIBRARY": "/System/Library/Frameworks/Tk.framework/Versions/Current",
            },
        },
    )
