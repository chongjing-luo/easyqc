from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from scripts.build_native_installer import (
    BuildRequest,
    NativeInstallerError,
    build_linux_deb,
    macos_hdiutil_command,
    normalize_version,
    read_product_version,
    render_inno_script,
    write_release_metadata,
)


def _fake_linux_app(tmp_path: Path) -> Path:
    app = tmp_path / "EasyQC application"
    app.mkdir()
    executable = app / "EasyQC"
    executable.write_text("#!/bin/sh\nprintf 'EasyQC test app\\n'\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    internal = app / "_internal"
    internal.mkdir()
    (internal / "payload.txt").write_text("payload", encoding="utf-8")
    return app


@pytest.mark.parametrize("version", ["1.0.0", "2.3.4-rc1", "10.0.1+build7"])
def test_normalize_version_accepts_release_versions(version: str) -> None:
    assert normalize_version(version) == version


@pytest.mark.parametrize(
    "version",
    ["", "v1.0.0", "1", "1.0", "1.0.0/../../tmp", "1.0.0\nName: bad"],
)
def test_normalize_version_rejects_ambiguous_or_injectable_values(
    version: str,
) -> None:
    with pytest.raises(NativeInstallerError, match="version"):
        normalize_version(version)


def test_product_version_is_read_as_data_without_executing_entrypoint(
    tmp_path: Path,
) -> None:
    (tmp_path / "easyqc.py").write_text(
        'raise RuntimeError("must not execute")\nEASYQC_VERSION = "3.4.5"\n',
        encoding="utf-8",
    )

    assert read_product_version(tmp_path) == "3.4.5"


@pytest.mark.parametrize(
    "source",
    [
        "EASYQC_VERSION = build_version()\n",
        'EASYQC_VERSION = "1.0.0"\nEASYQC_VERSION = "2.0.0"\n',
        'VERSION = "1.0.0"\n',
    ],
)
def test_product_version_contract_rejects_dynamic_duplicate_or_missing_values(
    tmp_path: Path,
    source: str,
) -> None:
    (tmp_path / "easyqc.py").write_text(source, encoding="utf-8")

    with pytest.raises(NativeInstallerError, match="EASYQC_VERSION"):
        read_product_version(tmp_path)


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb unavailable")
def test_linux_builder_creates_installable_layout_and_control_metadata(
    tmp_path: Path,
) -> None:
    request = BuildRequest(
        version="1.2.3",
        target="linux-x86_64",
        app_path=_fake_linux_app(tmp_path),
        output_dir=tmp_path / "release output",
        source_revision="a" * 40,
    )

    package = build_linux_deb(request)

    assert package.name == "EasyQC-1.2.3-linux-x86_64.deb"
    assert package.is_file()
    fields = subprocess.run(
        ["dpkg-deb", "--field", str(package), "Package", "Version", "Architecture"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert fields == ["Package: easyqc", "Version: 1.2.3", "Architecture: amd64"]
    contents = subprocess.run(
        ["dpkg-deb", "--contents", str(package)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "./opt/easyqc/EasyQC" in contents
    assert "./usr/bin/easyqc" in contents
    assert "./usr/share/applications/easyqc.desktop" in contents


@pytest.mark.skipif(shutil.which("dpkg-deb") is None, reason="dpkg-deb unavailable")
def test_linux_builder_rejects_a_nonempty_release_directory(tmp_path: Path) -> None:
    output = tmp_path / "release output"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("user-owned", encoding="utf-8")
    request = BuildRequest(
        version="1.2.3",
        target="linux-x86_64",
        app_path=_fake_linux_app(tmp_path),
        output_dir=output,
        source_revision="a" * 40,
    )

    with pytest.raises(NativeInstallerError, match="empty"):
        build_linux_deb(request)

    assert marker.read_text(encoding="utf-8") == "user-owned"
    assert list(output.iterdir()) == [marker]


def test_inno_renderer_is_per_user_and_escapes_paths(tmp_path: Path) -> None:
    app = tmp_path / 'EasyQC "application"'
    app.mkdir()
    (app / "EasyQC.exe").write_bytes(b"MZ-fake")
    request = BuildRequest(
        version="1.2.3",
        target="windows-x86_64",
        app_path=app,
        output_dir=tmp_path / "release output",
        source_revision="b" * 40,
    )

    rendered = render_inno_script(request)

    assert "PrivilegesRequired=lowest" in rendered
    assert r"DefaultDirName={localappdata}\Programs\EasyQC" in rendered
    assert "ArchitecturesAllowed=x64compatible" in rendered
    assert 'OutputBaseFilename=EasyQC-1.2.3-windows-x86_64-setup' in rendered
    assert 'Source: "' in rendered
    assert '""application""' in rendered
    assert r'Filename: "{app}\EasyQC.exe"' in rendered
    assert "UninstallDisplayIcon" in rendered


def test_macos_command_uses_argument_vector_and_expected_output(tmp_path: Path) -> None:
    app = tmp_path / "EasyQC.app"
    app.mkdir()
    request = BuildRequest(
        version="1.2.3",
        target="macos-arm64",
        app_path=app,
        output_dir=tmp_path / "release output",
        source_revision="c" * 40,
    )
    staging = tmp_path / "DMG staging"
    output = tmp_path / "release output" / "EasyQC-1.2.3-macos-arm64.dmg"

    command = macos_hdiutil_command(request, staging, output)

    assert command == [
        "hdiutil",
        "create",
        "-volname",
        "EasyQC 1.2.3",
        "-srcfolder",
        str(staging),
        "-ov",
        "-format",
        "UDZO",
        str(output),
    ]


def test_release_metadata_binds_package_target_revision_and_unsigned_state(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    package = output / "EasyQC-1.2.3-linux-x86_64.deb"
    package.write_bytes(b"package-bytes")
    request = BuildRequest(
        version="1.2.3",
        target="linux-x86_64",
        app_path=tmp_path,
        output_dir=output,
        source_revision="d" * 40,
    )

    manifest_path, checksums_path = write_release_metadata(request, package)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_hash = hashlib.sha256(b"package-bytes").hexdigest()
    assert manifest == {
        "schema": "easyqc-native-artifact-v1",
        "product": "EasyQC",
        "version": "1.2.3",
        "target": "linux-x86_64",
        "source_revision": "d" * 40,
        "artifact": package.name,
        "size_bytes": len(b"package-bytes"),
        "sha256": expected_hash,
        "signed": False,
    }
    assert checksums_path.read_text(encoding="utf-8") == (
        f"{expected_hash}  {package.name}\n"
    )


def test_checksum_collision_fails_before_manifest_becomes_authoritative(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    package = output / "EasyQC-1.2.3-linux-x86_64.deb"
    package.write_bytes(b"package-bytes")
    checksum = output / "SHA256SUMS"
    checksum.write_text("existing\n", encoding="utf-8")
    request = BuildRequest(
        version="1.2.3",
        target="linux-x86_64",
        app_path=tmp_path,
        output_dir=output,
        source_revision="d" * 40,
    )

    with pytest.raises(NativeInstallerError, match="already exists"):
        write_release_metadata(request, package)

    assert checksum.read_text(encoding="utf-8") == "existing\n"
    assert not (output / "artifact-manifest.json").exists()
