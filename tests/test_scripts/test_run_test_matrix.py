from __future__ import annotations

import ast
from pathlib import Path
import subprocess

from scripts import run_test_matrix


def test_lane_registry_is_disjoint_and_non_gui_ignores_every_gui_target() -> None:
    tk_targets = set(run_test_matrix.TK_TARGETS)
    qt_targets = set(run_test_matrix.QT_TARGETS)

    assert tk_targets
    assert qt_targets
    assert tk_targets.isdisjoint(qt_targets)
    assert set(run_test_matrix.NON_GUI_IGNORES) == tk_targets | qt_targets
    assert [lane.name for lane in run_test_matrix.LANES] == ["non_gui", "tk", "qt"]
    assert [lane.pytest_plugins for lane in run_test_matrix.LANES] == [
        (),
        (),
        ("pytestqt.plugin",),
    ]


def test_gui_runtime_imports_stay_inside_their_registered_lane(
    easyqc_root: Path,
) -> None:
    targets = {
        "tk": run_test_matrix.TK_TARGETS,
        "qt": run_test_matrix.QT_TARGETS,
    }

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
            module == "tkinter" or module.startswith(("tkinter.", "gui."))
            for module in modules
        ):
            families.add("tk")
        if "gui" in modules:
            families.add("tk")
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


def test_headless_linux_wraps_only_tk_and_sets_offscreen_only_for_qt(
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
        platform_name="Linux",
        which=lambda _name: "/usr/bin/xvfb-run",
        runner=fake_run,
    )

    assert result == 0
    assert len(calls) == 3
    non_gui, tk_lane, qt_lane = calls
    assert non_gui[0][:4] == ["/controlled/python", "-m", "pytest", "tests"]
    assert all(f"--ignore={target}" in non_gui[0] for target in run_test_matrix.NON_GUI_IGNORES)
    assert tk_lane[0][:3] == ["/usr/bin/xvfb-run", "-a", "/controlled/python"]
    assert qt_lane[0][:3] == ["/controlled/python", "-m", "pytest"]
    assert "pytestqt.plugin" not in non_gui[0]
    assert "pytestqt.plugin" not in tk_lane[0]
    assert ["-p", "pytestqt.plugin"] == qt_lane[0][-2:]
    assert "QT_QPA_PLATFORM" not in non_gui[1]["env"]
    assert "QT_QPA_PLATFORM" not in tk_lane[1]["env"]
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
    returncodes = iter((0, 1, 0))
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, next(returncodes))

    result = run_test_matrix.run_test_matrix(
        project_root=easyqc_root,
        python=Path("/controlled/python"),
        environ={"DISPLAY": ":99"},
        platform_name="Linux",
        which=lambda _name: None,
        runner=fake_run,
    )

    assert result == 1
    assert len(commands) == 3
    output = capsys.readouterr().out
    assert "PASS non_gui" in output
    assert "FAIL tk" in output
    assert "PASS qt" in output
    assert "FAIL test matrix" in output


def test_missing_target_fails_preflight_before_any_child(tmp_path: Path, capsys) -> None:
    calls = []

    result = run_test_matrix.run_test_matrix(
        project_root=tmp_path,
        python=Path("/controlled/python"),
        environ={"DISPLAY": ":99"},
        platform_name="Linux",
        which=lambda _name: None,
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result == 2
    assert calls == []
    assert "missing test target" in capsys.readouterr().err.lower()


def test_headless_linux_without_xvfb_fails_before_any_child(
    easyqc_root: Path,
    capsys,
) -> None:
    calls = []

    result = run_test_matrix.run_test_matrix(
        project_root=easyqc_root,
        python=Path("/controlled/python"),
        environ={},
        platform_name="Linux",
        which=lambda _name: None,
        runner=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert result == 2
    assert calls == []
    assert "xvfb-run" in capsys.readouterr().err
