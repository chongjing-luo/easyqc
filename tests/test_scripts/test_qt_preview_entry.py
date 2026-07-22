from __future__ import annotations

import sys
from pathlib import Path
import subprocess

import pytest

import easyqc as entrypoint


def test_parser_keeps_tk_default_and_accepts_explicit_qt_preview():
    assert entrypoint.parse_arguments([]).ui == "tk"
    assert entrypoint.parse_arguments(["--ui", "qt-preview"]).ui == "qt-preview"


def test_parser_preserves_existing_four_positional_cli_arguments():
    parsed = entrypoint.parse_arguments(["demo", "anat", "rater", "SUB001"])

    assert parsed.ui == "tk"
    assert parsed.args == ["demo", "anat", "rater", "SUB001"]


def test_parser_rejects_unknown_ui():
    with pytest.raises(SystemExit):
        entrypoint.parse_arguments(["--ui", "web"])


def test_entrypoint_keeps_tk_default_and_routes_only_explicit_preview(monkeypatch):
    calls = []

    monkeypatch.setattr(entrypoint, "parse_arguments", lambda _argv=None: type(
        "Args", (), {"ui": "qt-preview", "args": []}
    )())
    monkeypatch.setattr(
        "gui_qt.application.launch_qt_preview",
        lambda argv, registry_path=None: calls.append((tuple(argv), registry_path)) or 0,
    )

    assert entrypoint.main([]) == 0
    assert len(calls) == 1


def test_version_returns_without_importing_or_constructing_a_gui(
    monkeypatch,
    capsys,
):
    monkeypatch.setitem(sys.modules, "gui.app", None)

    assert entrypoint.main(["--version"]) == 0
    assert capsys.readouterr().out == "1.0.0\n"


def test_direct_version_process_has_exact_machine_output() -> None:
    project_root = Path(__file__).resolve().parents[2]

    process = subprocess.run(
        [sys.executable, str(project_root / "easyqc.py"), "--version"],
        cwd=project_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert process.returncode == 0
    assert process.stdout == "1.0.0\n"
    assert process.stderr == ""
