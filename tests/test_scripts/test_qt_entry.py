from __future__ import annotations

import sys
from pathlib import Path
import subprocess

import pytest

import easyqc as entrypoint


def test_parser_has_one_qt_gui_and_no_ui_selector():
    parsed = entrypoint.parse_arguments([])

    assert parsed.args == []
    assert not hasattr(parsed, "ui")


def test_parser_preserves_existing_four_positional_cli_arguments():
    parsed = entrypoint.parse_arguments(["demo", "anat", "rater", "SUB001"])

    assert parsed.args == ["demo", "anat", "rater", "SUB001"]


def test_parser_rejects_removed_gui_selector() -> None:
    with pytest.raises(SystemExit):
        entrypoint.parse_arguments(["--ui", "tk"])


def test_entrypoint_routes_default_launch_to_qt(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "gui_qt.application.launch_qt",
        lambda argv, registry_path=None: calls.append((tuple(argv), registry_path)) or 0,
    )

    assert entrypoint.main([]) == 0
    assert len(calls) == 1
    assert calls[0][1] == entrypoint.project_root / "projects.json"


def test_entrypoint_routes_four_positionals_to_direct_qt_qc(monkeypatch):
    calls = []

    monkeypatch.setattr(
        "gui_qt.application.launch_qt_qc",
        lambda argv, **kwargs: calls.append((tuple(argv), kwargs)) or 0,
    )

    assert entrypoint.main(["DEMO", "AnatQC", "rater_2", "SUB001"]) == 0
    assert calls == [
        (
            (
                sys.argv[0],
                "DEMO",
                "AnatQC",
                "rater_2",
                "SUB001",
            ),
            {
                "project": "DEMO",
                "module": "AnatQC",
                "rater": "rater_2",
                "easyqcid": "SUB001",
                "registry_path": entrypoint.project_root / "projects.json",
            },
        )
    ]


def test_version_returns_without_importing_or_constructing_a_gui(
    monkeypatch,
    capsys,
):
    monkeypatch.setitem(sys.modules, "gui_qt.application", None)

    assert entrypoint.main(["--version"]) == 0
    assert capsys.readouterr().out == "1.0.0\n"


def test_missing_qt_dependency_reports_the_original_import_error(
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setitem(sys.modules, "gui_qt.application", None)

    assert entrypoint.main([]) == 1
    output = capsys.readouterr().out
    assert "错误：无法导入必要的模块" in output
    assert "gui_qt.application" in output


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
