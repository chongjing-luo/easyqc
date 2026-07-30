from __future__ import annotations

import ast
from pathlib import Path
import subprocess

from scripts import run_test_matrix


def test_lane_registry_is_disjoint_and_non_gui_ignores_every_gui_target() -> None:
    qt_targets = set(run_test_matrix.QT_TARGETS)

    assert qt_targets
    assert set(run_test_matrix.NON_GUI_IGNORES) == qt_targets
    assert [lane.name for lane in run_test_matrix.LANES] == ["non_gui", "qt"]
    assert [lane.pytest_plugins for lane in run_test_matrix.LANES] == [
        (),
        ("pytestqt.plugin",),
    ]


def test_gui_runtime_imports_stay_inside_their_registered_lane(
    easyqc_root: Path,
) -> None:
    targets = {"qt": run_test_matrix.QT_TARGETS}

    def is_in_target(relative_path: str, target: str) -> bool:
        return relative_path == target or relative_path.startswith(f"{target}/")

    misplaced: list[str] = []
    mixed: list[str] = []
    for path in sorted((easyqc_root / "tests").rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        modules.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        families = set()
        if any(
            module == "PySide6" or module.startswith(("PySide6.", "gui_qt."))
            for module in modules
        ):
            families.add("qt")
        if "gui_qt" in modules:
            families.add("qt")

        relative_path = path.relative_to(easyqc_root).as_posix()
        if len(families) > 1:
            mixed.append(relative_path)
        for family in families:
            if not any(
                is_in_target(relative_path, target) for target in targets[family]
            ):
                misplaced.append(f"{family}:{relative_path}")

    assert mixed == []
    assert misplaced == []


def test_qt_lane_is_offscreen_and_toolkit_free_lane_has_no_qt_plugin(
    easyqc_root: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((list(command), kwargs))
        return subprocess.CompletedProcess(command, 0)

    result = run_test_matrix.run_test_matrix(
        project_root=easyqc_root,
        python=Path("/controlled/python"),
        environ={
            "PYTEST_ADDOPTS": "-p pytestqt.plugin",
            "PYTEST_PLUGINS": "pytestqt.plugin",
        },
        runner=fake_run,
    )

    assert result == 0
    assert len(calls) == 2
    non_gui, qt_lane = calls
    assert non_gui[0][:4] == ["/controlled/python", "-m", "pytest", "tests"]
    assert all(f"--ignore={target}" in non_gui[0] for target in run_test_matrix.NON_GUI_IGNORES)
    assert qt_lane[0][:3] == ["/controlled/python", "-m", "pytest"]
    assert "pytestqt.plugin" not in non_gui[0]
    assert ["-p", "pytestqt.plugin"] == qt_lane[0][-2:]
    assert "QT_QPA_PLATFORM" not in non_gui[1]["env"]
    assert qt_lane[1]["env"]["QT_QPA_PLATFORM"] == "offscreen"
    for _command, kwargs in calls:
        assert kwargs["env"]["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
        assert "PYTEST_ADDOPTS" not in kwargs["env"]
        assert "PYTEST_PLUGINS" not in kwargs["env"]
    assert all(call[1]["cwd"] == easyqc_root for call in calls)
    assert all(call[1]["check"] is False for call in calls)
    assert all("shell" not in call[1] for call in calls)


def test_all_lanes_run_and_aggregate_failure_is_nonzero(
    easyqc_root: Path,
    capsys,
) -> None:
    returncodes = iter((0, 1))
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, next(returncodes))

    result = run_test_matrix.run_test_matrix(
        project_root=easyqc_root,
        python=Path("/controlled/python"),
        environ={"DISPLAY": ":99"},
        runner=fake_run,
    )

    assert result == 1
    assert len(commands) == 2
    output = capsys.readouterr().out
    assert "PASS non_gui" in output
    assert "FAIL qt" in output
    assert "FAIL test matrix" in output


def test_missing_target_fails_preflight_before_any_child(tmp_path: Path, capsys) -> None:
    calls = []

    result = run_test_matrix.run_test_matrix(
        project_root=tmp_path,
        python=Path("/controlled/python"),
        environ={"DISPLAY": ":99"},
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result == 2
    assert calls == []
    assert "missing test target" in capsys.readouterr().err.lower()
