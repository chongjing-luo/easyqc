from __future__ import annotations

from pathlib import Path
import json
import os
import platform
import subprocess
import sys


def test_installer_script_exposes_only_the_approved_commands() -> None:
    project_root = Path(__file__).resolve().parents[2]

    process = subprocess.run(
        [sys.executable, str(project_root / "easyqc_install.py"), "--help"],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert process.returncode == 0
    assert process.stdout.startswith("usage: easyqc-install ")
    assert "{install,update,repair,rollback,status}" in process.stdout
    assert process.stderr == ""


def test_installer_status_process_obeys_native_support_boundary(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment.update(
        {
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
            "LOCALAPPDATA": str(tmp_path / "local-app-data"),
            "HOME": str(tmp_path / "home"),
        }
    )

    process = subprocess.run(
        [
            sys.executable,
            str(project_root / "easyqc_install.py"),
            "status",
            "--json",
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    if sys.platform == "win32" and platform.release() != "11":
        assert process.returncode == 1
        assert process.stdout == ""
        assert process.stderr.startswith(
            "easyqc-install: error: managed runtime supports "
            "Windows 11 x86_64 only; "
        )
        assert f"observed Windows {platform.release()} " in process.stderr
        assert process.stderr.count("\n") == 1
        assert list(tmp_path.iterdir()) == []
        return

    assert process.returncode == 0
    assert process.stdout.startswith("{")
    assert process.stdout.count("\n") == 1
    assert json.loads(process.stdout)["installation_state"] == "not-installed"
    assert process.stderr == ""
    assert list(tmp_path.iterdir()) == []
