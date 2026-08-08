#!/usr/bin/env python3
"""Build one host-native EasyQC installer around a frozen application.

This script does not freeze EasyQC and it does not cross-compile.  It accepts a
PyInstaller output produced on the current host and wraps it with the host's
native packaging tool:

* Ubuntu x86_64: ``dpkg-deb`` -> ``.deb``
* Windows x86_64: Inno Setup ``ISCC.exe`` -> ``Setup.exe``
* macOS arm64: ``hdiutil`` -> ``.dmg``

Every successful build emits a canonical artifact manifest and SHA-256 file.
The first release contract is deliberately unsigned; signing must be added as
an explicit, evidenced release step rather than inferred from a filename.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Literal


Target = Literal["linux-x86_64", "windows-x86_64", "macos-arm64"]

_VERSION = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9][A-Za-z0-9.-]*)?$"
)
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_TARGETS: tuple[Target, ...] = (
    "linux-x86_64",
    "windows-x86_64",
    "macos-arm64",
)
_INNO_APP_ID = "{{2A7D1208-014F-4F52-BAC9-CF2E96582E9E}"


class NativeInstallerError(RuntimeError):
    """Raised when a native package cannot be produced or verified."""


@dataclass(frozen=True)
class BuildRequest:
    """One validated-intent native package build."""

    version: str
    target: Target
    app_path: Path
    output_dir: Path
    source_revision: str


def normalize_version(value: str) -> str:
    """Return a conservative release version safe for all package formats."""

    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise NativeInstallerError(
            "version must use MAJOR.MINOR.PATCH with an optional safe suffix"
        )
    return value


def read_product_version(project_root: Path) -> str:
    """Read the one literal ``EASYQC_VERSION`` assignment without importing it."""

    entrypoint = Path(project_root) / "easyqc.py"
    if entrypoint.is_symlink() or not entrypoint.is_file():
        raise NativeInstallerError(f"EasyQC entrypoint is missing: {entrypoint}")
    try:
        tree = ast.parse(
            entrypoint.read_text(encoding="utf-8"),
            filename=str(entrypoint),
        )
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise NativeInstallerError("cannot parse EASYQC_VERSION from easyqc.py") from exc
    values: list[str] = []
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "EASYQC_VERSION"
            for target in statement.targets
        ):
            continue
        if not isinstance(statement.value, ast.Constant) or not isinstance(
            statement.value.value, str
        ):
            raise NativeInstallerError("EASYQC_VERSION must be one literal string")
        values.append(statement.value.value)
    if len(values) != 1:
        raise NativeInstallerError("easyqc.py must define EASYQC_VERSION exactly once")
    return normalize_version(values[0])


def detect_target() -> Target:
    """Map the current native host to the supported release target."""

    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux" and machine in {"x86_64", "amd64"}:
        return "linux-x86_64"
    if system == "Windows" and machine in {"amd64", "x86_64"}:
        return "windows-x86_64"
    if system == "Darwin" and machine in {"arm64", "aarch64"}:
        return "macos-arm64"
    raise NativeInstallerError(
        f"unsupported native installer host: {system} {platform.machine()}"
    )


def validate_request(request: BuildRequest, *, require_native: bool = True) -> None:
    """Validate all shared request fields before creating package state."""

    normalize_version(request.version)
    if request.target not in _TARGETS:
        raise NativeInstallerError(f"unsupported target: {request.target}")
    if require_native and request.target != detect_target():
        raise NativeInstallerError(
            f"cross-platform packaging is not supported: host={detect_target()} "
            f"request={request.target}"
        )
    if _REVISION.fullmatch(request.source_revision) is None:
        raise NativeInstallerError(
            "source revision must be one exact lowercase 40-character git SHA"
        )
    app_path = Path(request.app_path)
    if app_path.is_symlink() or not app_path.is_dir():
        raise NativeInstallerError(
            f"frozen application must be a real directory: {app_path}"
        )
    output_dir = Path(request.output_dir)
    if output_dir.exists() and (output_dir.is_symlink() or not output_dir.is_dir()):
        raise NativeInstallerError(
            f"output directory must be a real directory: {output_dir}"
        )


def _require_tool(name: str) -> str:
    tool = shutil.which(name)
    if tool is None:
        raise NativeInstallerError(f"required native packaging tool is missing: {name}")
    return tool


def _prepare_output(request: BuildRequest, filename: str) -> Path:
    output_dir = Path(request.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise NativeInstallerError(f"output directory is unsafe: {output_dir}")
    if any(output_dir.iterdir()):
        raise NativeInstallerError(
            f"output directory must be empty before packaging: {output_dir}"
        )
    output = output_dir / filename
    if output.exists() or output.is_symlink():
        raise NativeInstallerError(f"native package output already exists: {output}")
    return output


def _validate_copy_tree(root: Path) -> None:
    """Reject special files and symlinks that escape the frozen app root."""

    root = root.resolve(strict=True)
    for path in root.rglob("*"):
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            target = (path.parent / os.readlink(path)).resolve(strict=False)
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise NativeInstallerError(
                    f"frozen application symlink escapes its root: {path}"
                ) from exc
        elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise NativeInstallerError(
                f"frozen application contains a special filesystem entry: {path}"
            )


def _tree_size_kib(root: Path) -> int:
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
    return max(1, (total + 1023) // 1024)


def _write_text(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755 if executable else 0o644)


def build_linux_deb(request: BuildRequest) -> Path:
    """Create one Ubuntu/Debian package from a Linux PyInstaller onedir."""

    validate_request(request, require_native=True)
    if request.target != "linux-x86_64":
        raise NativeInstallerError("build_linux_deb requires linux-x86_64")
    app_executable = Path(request.app_path) / "EasyQC"
    if app_executable.is_symlink() or not app_executable.is_file():
        raise NativeInstallerError(f"Linux EasyQC executable is missing: {app_executable}")
    if not os.access(app_executable, os.X_OK):
        raise NativeInstallerError(
            f"Linux EasyQC executable is not executable: {app_executable}"
        )
    _validate_copy_tree(Path(request.app_path))
    dpkg_deb = _require_tool("dpkg-deb")
    output = _prepare_output(
        request, f"EasyQC-{request.version}-linux-x86_64.deb"
    )

    with tempfile.TemporaryDirectory(
        prefix="easyqc-deb-", dir=str(Path(request.output_dir))
    ) as temporary:
        stage = Path(temporary) / "root"
        application = stage / "opt" / "easyqc"
        shutil.copytree(request.app_path, application, symlinks=True)
        _write_text(
            stage / "usr" / "bin" / "easyqc",
            '#!/bin/sh\nexec /opt/easyqc/EasyQC "$@"\n',
            executable=True,
        )
        _write_text(
            stage / "usr" / "share" / "applications" / "easyqc.desktop",
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=EasyQC\n"
            "Comment=Manual quality control for neuroimaging data\n"
            "Exec=/usr/bin/easyqc\n"
            "Terminal=false\n"
            "Categories=Science;MedicalSoftware;\n",
        )
        license_path = Path(__file__).resolve().parents[1] / "LICENSE"
        if license_path.is_symlink() or not license_path.is_file():
            raise NativeInstallerError(f"EasyQC license is missing: {license_path}")
        copyright_text = (
            "Format: https://www.debian.org/doc/packaging-manuals/"
            "copyright-format/1.0/\n"
            "Upstream-Name: EasyQC\n"
            "Source: https://github.com/chongjing-luo/easyqc\n\n"
            "Files: *\n"
            "Copyright: 2024-2026 Chongjing Luo\n"
            "License: MIT\n"
            + "\n".join(
                f" {line}" if line else " ."
                for line in license_path.read_text(encoding="utf-8").splitlines()
            )
            + "\n"
        )
        _write_text(
            stage / "usr" / "share" / "doc" / "easyqc" / "copyright",
            copyright_text,
        )
        installed_size = _tree_size_kib(stage)
        control = (
            "Package: easyqc\n"
            f"Version: {request.version}\n"
            "Section: science\n"
            "Priority: optional\n"
            "Architecture: amd64\n"
            "Maintainer: Chongjing Luo <chongjing.luo@mail.bnu.edu.cn>\n"
            f"Installed-Size: {installed_size}\n"
            "Depends: libc6 (>= 2.35), libegl1, libgl1, libxkbcommon-x11-0, "
            "libxcb-icccm4, libxcb-keysyms1, libxcb-render-util0, "
            "libxcb-xinerama0\n"
            "Homepage: https://github.com/chongjing-luo/easyqc\n"
            "Description: configurable desktop workflow for manual image QC\n"
            " EasyQC manages QC lists, modules, external viewers, ratings and exports.\n"
        )
        _write_text(stage / "DEBIAN" / "control", control)
        _run(
            [
                dpkg_deb,
                "--root-owner-group",
                "-Zzstd",
                "-z9",
                "--build",
                str(stage),
                str(output),
            ],
            "dpkg-deb build",
        )

    _require_regular_output(output)
    _run([dpkg_deb, "--info", str(output)], "dpkg-deb info")
    _run([dpkg_deb, "--contents", str(output)], "dpkg-deb contents")
    return output


def _inno_escape(value: str) -> str:
    if "\n" in value or "\r" in value:
        raise NativeInstallerError("Inno Setup value contains a newline")
    return value.replace('"', '""')


def render_inno_script(request: BuildRequest) -> str:
    """Render the complete per-user Inno Setup input without executing it."""

    validate_request(request, require_native=False)
    if request.target != "windows-x86_64":
        raise NativeInstallerError("render_inno_script requires windows-x86_64")
    app_executable = Path(request.app_path) / "EasyQC.exe"
    if app_executable.is_symlink() or not app_executable.is_file():
        raise NativeInstallerError(
            f"Windows EasyQC executable is missing: {app_executable}"
        )
    source = _inno_escape(
        str((Path(request.app_path).resolve() / "*")).replace("/", "\\")
    )
    output = _inno_escape(str(Path(request.output_dir).resolve()).replace("/", "\\"))
    return (
        "[Setup]\n"
        f"AppId={_INNO_APP_ID}\n"
        "AppName=EasyQC\n"
        f"AppVersion={request.version}\n"
        "AppPublisher=Chongjing Luo\n"
        "AppPublisherURL=https://github.com/chongjing-luo/easyqc\n"
        r"DefaultDirName={localappdata}\Programs\EasyQC" "\n"
        r"DefaultGroupName=EasyQC" "\n"
        "PrivilegesRequired=lowest\n"
        "ArchitecturesAllowed=x64compatible\n"
        "ArchitecturesInstallIn64BitMode=x64compatible\n"
        r"UninstallDisplayIcon={app}\EasyQC.exe" "\n"
        f"OutputDir={output}\n"
        f"OutputBaseFilename=EasyQC-{request.version}-windows-x86_64-setup\n"
        "Compression=lzma2\n"
        "SolidCompression=yes\n"
        "WizardStyle=modern\n"
        "DisableProgramGroupPage=yes\n"
        "SetupLogging=yes\n\n"
        "[Files]\n"
        f'Source: "{source}"; DestDir: "{{app}}"; '
        "Flags: ignoreversion recursesubdirs createallsubdirs\n\n"
        "[Icons]\n"
        r'Name: "{group}\EasyQC"; Filename: "{app}\EasyQC.exe"' "\n"
        r'Name: "{userdesktop}\EasyQC"; Filename: "{app}\EasyQC.exe"; Tasks: desktopicon' "\n\n"
        "[Tasks]\n"
        r'Name: "desktopicon"; Description: "Create a desktop shortcut"; '
        r'GroupDescription: "Additional icons:"; Flags: unchecked' "\n\n"
        "[Run]\n"
        r'Filename: "{app}\EasyQC.exe"; Description: "Launch EasyQC"; '
        "Flags: nowait postinstall skipifsilent\n"
    )


def _find_iscc() -> Path:
    configured = os.environ.get("ISCC_EXE")
    candidates = [Path(configured)] if configured else []
    discovered = shutil.which("ISCC.exe") or shutil.which("iscc")
    if discovered:
        candidates.append(Path(discovered))
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "Inno Setup 6" / "ISCC.exe")
    for candidate in candidates:
        if not candidate.is_symlink() and candidate.is_file():
            return candidate
    raise NativeInstallerError(
        "Inno Setup 6 ISCC.exe is missing; set ISCC_EXE to its exact path"
    )


def build_windows_setup(request: BuildRequest) -> Path:
    """Compile one per-user Setup.exe with the native Inno Setup compiler."""

    validate_request(request, require_native=True)
    if request.target != "windows-x86_64":
        raise NativeInstallerError("build_windows_setup requires windows-x86_64")
    output = _prepare_output(
        request, f"EasyQC-{request.version}-windows-x86_64-setup.exe"
    )
    iscc = _find_iscc()
    with tempfile.TemporaryDirectory(prefix="easyqc-inno-") as temporary:
        script = Path(temporary) / "EasyQC.iss"
        script.write_text(
            render_inno_script(request),
            encoding="utf-8-sig",
            newline="\r\n",
        )
        _run([str(iscc), str(script)], "Inno Setup compile")
    _require_regular_output(output)
    return output


def macos_hdiutil_command(
    request: BuildRequest, staging: Path, output: Path
) -> list[str]:
    """Return the exact argument-vector contract for the macOS DMG tool."""

    normalize_version(request.version)
    if request.target != "macos-arm64":
        raise NativeInstallerError("macos_hdiutil_command requires macos-arm64")
    return [
        "hdiutil",
        "create",
        "-volname",
        f"EasyQC {request.version}",
        "-srcfolder",
        str(staging),
        "-ov",
        "-format",
        "UDZO",
        str(output),
    ]


def build_macos_dmg(request: BuildRequest) -> Path:
    """Create one compressed drag-install DMG from ``EasyQC.app``."""

    validate_request(request, require_native=True)
    if request.target != "macos-arm64":
        raise NativeInstallerError("build_macos_dmg requires macos-arm64")
    app = Path(request.app_path)
    executable = app / "Contents" / "MacOS" / "EasyQC"
    if (
        app.name != "EasyQC.app"
        or executable.is_symlink()
        or not executable.is_file()
    ):
        raise NativeInstallerError(f"macOS EasyQC.app bundle is invalid: {app}")
    if not os.access(executable, os.X_OK):
        raise NativeInstallerError(
            f"macOS app executable is not executable: {executable}"
        )
    hdiutil = _require_tool("hdiutil")
    output = _prepare_output(request, f"EasyQC-{request.version}-macos-arm64.dmg")
    with tempfile.TemporaryDirectory(
        prefix="easyqc-dmg-", dir=str(Path(request.output_dir))
    ) as temporary:
        staging = Path(temporary) / "EasyQC"
        staging.mkdir()
        shutil.copytree(app, staging / "EasyQC.app", symlinks=True)
        (staging / "Applications").symlink_to(
            "/Applications",
            target_is_directory=True,
        )
        command = macos_hdiutil_command(request, staging, output)
        command[0] = hdiutil
        _run(command, "hdiutil create")
    _require_regular_output(output)
    _run([hdiutil, "imageinfo", str(output)], "hdiutil imageinfo")
    return output


def _run(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or "").strip()
        raise NativeInstallerError(
            f"{label} failed" + (f": {detail}" if detail else "")
        ) from exc


def _require_regular_output(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        raise NativeInstallerError(f"native package output is missing or empty: {path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if (
        path.exists()
        or path.is_symlink()
        or temporary.exists()
        or temporary.is_symlink()
    ):
        raise NativeInstallerError(f"release evidence output already exists: {path}")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise NativeInstallerError(f"cannot write release evidence: {path}") from exc


def write_release_metadata(
    request: BuildRequest, package: Path
) -> tuple[Path, Path]:
    """Write canonical integrity metadata beside one immutable package."""

    normalize_version(request.version)
    if request.target not in _TARGETS:
        raise NativeInstallerError(f"unsupported target: {request.target}")
    if _REVISION.fullmatch(request.source_revision) is None:
        raise NativeInstallerError("source revision is invalid")
    package = Path(package)
    _require_regular_output(package)
    output_dir = Path(request.output_dir).resolve(strict=True)
    if package.parent.resolve(strict=True) != output_dir:
        raise NativeInstallerError("native package is outside the output directory")
    digest = _sha256(package)
    manifest = {
        "schema": "easyqc-native-artifact-v1",
        "product": "EasyQC",
        "version": request.version,
        "target": request.target,
        "source_revision": request.source_revision,
        "artifact": package.name,
        "size_bytes": package.stat().st_size,
        "sha256": digest,
        "signed": False,
    }
    manifest_path = output_dir / "artifact-manifest.json"
    checksums_path = output_dir / "SHA256SUMS"
    _atomic_write(
        checksums_path,
        f"{digest}  {package.name}\n".encode("utf-8"),
    )
    _atomic_write(
        manifest_path,
        (
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8"),
    )
    return manifest_path, checksums_path


def build_native_package(request: BuildRequest) -> Path:
    """Dispatch one request to exactly one host-native packaging implementation."""

    if request.target == "linux-x86_64":
        return build_linux_deb(request)
    if request.target == "windows-x86_64":
        return build_windows_setup(request)
    if request.target == "macos-arm64":
        return build_macos_dmg(request)
    raise NativeInstallerError(f"unsupported target: {request.target}")


def _git_revision(project_root: Path) -> str:
    result = _run(
        ["git", "rev-parse", "HEAD"],
        "git source revision",
    )
    revision = result.stdout.strip()
    if _REVISION.fullmatch(revision) is None:
        raise NativeInstallerError("git returned an invalid source revision")
    return revision


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build one native EasyQC installer from a frozen application"
    )
    parser.add_argument("--version", required=True, help="release version, e.g. 1.0.0")
    parser.add_argument(
        "--target",
        choices=_TARGETS,
        help="native target; defaults to the detected current host",
    )
    parser.add_argument(
        "--app-path",
        required=True,
        type=Path,
        help="PyInstaller onedir or EasyQC.app produced on this host",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="new/empty directory for package and integrity metadata",
    )
    parser.add_argument(
        "--source-revision",
        help="exact lowercase 40-character git SHA; defaults to current HEAD",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        target = args.target or detect_target()
        project_root = Path(__file__).resolve().parents[1]
        requested_version = normalize_version(args.version)
        product_version = read_product_version(project_root)
        if requested_version != product_version:
            raise NativeInstallerError(
                "requested package version does not match easyqc.py "
                f"EASYQC_VERSION: {requested_version} != {product_version}"
            )
        request = BuildRequest(
            version=requested_version,
            target=target,
            app_path=args.app_path,
            output_dir=args.output_dir,
            source_revision=args.source_revision or _git_revision(project_root),
        )
        package = build_native_package(request)
        manifest, checksums = write_release_metadata(request, package)
    except NativeInstallerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = {
        "package": str(package),
        "manifest": str(manifest),
        "checksums": str(checksums),
        "signed": False,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
