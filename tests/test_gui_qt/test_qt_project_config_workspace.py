from __future__ import annotations

from threading import Event, get_ident

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFileDialog

from core.configuration_service import ConfigurationService
from core.event_bus import EventType
from core.project_service import ProjectService
from core.table_service import TableService
from gui_qt.project_config_workspace import QtProjectConfigWorkspace


def _workspace(qtbot, tmp_path):
    config = ConfigurationService(
        ProjectService(tmp_path / "projects.json"),
        TableService(),
    )
    config.create_project("SAMPLE", tmp_path)
    config.replace_subjects(pd.DataFrame({"ezqcid": ["SUB001", "SUB002"], "site": ["A", "B"]}))
    workspace = QtProjectConfigWorkspace(config)
    qtbot.addWidget(workspace)
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=2000)
    return workspace, config


def test_qt_project_config_renders_current_project_and_subject_summary(qtbot, tmp_path) -> None:
    workspace, _ = _workspace(qtbot, tmp_path)

    assert workspace.project_combo.currentText() == "SAMPLE"
    assert "2 subjects" in workspace.subject_summary.text()
    assert workspace.subject_model.rowCount() == 2


def test_initial_configuration_snapshot_loads_without_blocking_qt(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    config = ConfigurationService(
        ProjectService(tmp_path / "projects.json"),
        TableService(),
    )
    config.create_project("SAMPLE", tmp_path)
    config.replace_subjects(
        pd.DataFrame({"ezqcid": ["SUB001", "SUB002"], "site": ["A", "B"]})
    )
    real_snapshot = config.snapshot
    started = Event()
    release = Event()

    def delayed_snapshot():
        started.set()
        release.wait(2)
        return real_snapshot()

    monkeypatch.setattr(config, "snapshot", delayed_snapshot)

    workspace = QtProjectConfigWorkspace(config)
    qtbot.addWidget(workspace)
    qtbot.waitUntil(started.is_set, timeout=2000)

    assert workspace.io_task_controller.busy
    assert "Loading configuration" in workspace.status_label.text()
    assert workspace.subject_model.rowCount() == 0

    release.set()
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=2000)
    assert workspace.subject_model.rowCount() == 2


def test_qt_project_page_creates_and_unregisters_without_deleting_files(qtbot, tmp_path) -> None:
    workspace, config = _workspace(qtbot, tmp_path)

    assert workspace.create_project("SECOND", str(tmp_path))
    second_path = config.current_project.path
    assert workspace.project_combo.currentText() == "SECOND"
    assert workspace.remove_project("SECOND")
    assert second_path.exists()
    assert "SECOND" not in config.projects()


def test_qt_constant_form_commits_and_surfaces_column_collision(qtbot, tmp_path) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    workspace.constant_name.setText("DATA_ROOT")
    workspace.constant_value.setText("/data")
    qtbot.mouseClick(workspace.save_constant_button, Qt.LeftButton)

    assert config.constants()["DATA_ROOT"] == "/data"
    assert workspace.constants_table.rowCount() == 1

    workspace.constant_name.setText("site")
    workspace.constant_value.setText("bad")
    qtbot.mouseClick(workspace.save_constant_button, Qt.LeftButton)
    assert "column" in workspace.error_text.lower()
    assert "site" not in config.constants()


def test_qt_module_form_adds_and_reorders_without_json_editor(qtbot, tmp_path) -> None:
    workspace, config = _workspace(qtbot, tmp_path)

    assert workspace.add_module("AnatQC", "Anatomical QC")
    assert workspace.add_module("FuncQC", "Functional QC")
    assert workspace.move_module("FuncQC", -1)

    names = [module.name for module in config.modules()]
    assert names == ["example", "FuncQC", "AnatQC"]
    assert not hasattr(workspace, "json_editor")


def test_project_load_runs_in_background_and_keeps_qt_responsive(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    config.create_project("SECOND", tmp_path)
    workspace.refresh()
    workspace.project_combo.setCurrentText("SAMPLE")
    real_load = config.load_project
    started = Event()
    release = Event()

    def delayed_load(name, *, notify=True):
        started.set()
        release.wait(2)
        return real_load(name, notify=notify)

    monkeypatch.setattr(config, "load_project", delayed_load)
    event_loop_progress = []
    project_event_threads = []
    config.project_service.event_bus.subscribe(
        EventType.PROJECT_CHANGED,
        lambda _event: project_event_threads.append(get_ident()),
    )
    gui_thread = get_ident()
    QTimer.singleShot(0, lambda: event_loop_progress.append(True))

    qtbot.mouseClick(workspace.load_project_button, Qt.LeftButton)
    qtbot.waitUntil(started.is_set, timeout=2000)
    qtbot.waitUntil(lambda: bool(event_loop_progress), timeout=2000)

    assert workspace.io_task_controller.busy
    assert "Loading project" in workspace.status_label.text()
    assert not workspace.tabs.isEnabled()

    release.set()
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=2000)
    assert config.current_project.name == "SAMPLE"
    assert workspace.project_combo.currentText() == "SAMPLE"
    assert workspace.subject_model.rowCount() == 2
    assert project_event_threads == [gui_thread]


def test_subject_csv_import_runs_in_background_and_refreshes_snapshot(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    source = tmp_path / "subjects.csv"
    pd.DataFrame(
        {"ezqcid": ["SUB010", "SUB011", "SUB012"], "site": ["A", "B", "C"]}
    ).to_csv(source, index=False)
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: (str(source), "CSV files (*.csv)"),
    )
    real_import = config.import_subject_csv
    started = Event()
    release = Event()

    def delayed_import(path, *, mode, notify=True):
        started.set()
        release.wait(2)
        return real_import(path, mode=mode, notify=notify)

    monkeypatch.setattr(config, "import_subject_csv", delayed_import)
    subject_event_threads = []
    config.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        lambda _event: subject_event_threads.append(get_ident()),
    )
    gui_thread = get_ident()

    qtbot.mouseClick(workspace.replace_subjects_button, Qt.LeftButton)
    qtbot.waitUntil(started.is_set, timeout=2000)
    assert workspace.io_task_controller.busy
    assert "Importing subjects" in workspace.status_label.text()

    release.set()
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=2000)
    assert workspace.subject_model.rowCount() == 3
    assert "3 subjects" in workspace.subject_summary.text()
    assert subject_event_threads == [gui_thread]


def test_module_export_runs_in_background_and_reports_completion(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    output = tmp_path / "exported-module.json"
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(output), "JSON files (*.json)"),
    )
    real_export = config.export_module
    started = Event()
    release = Event()

    def delayed_export(name, path):
        started.set()
        release.wait(2)
        return real_export(name, path)

    monkeypatch.setattr(config, "export_module", delayed_export)

    qtbot.mouseClick(workspace.export_module_button, Qt.LeftButton)
    qtbot.waitUntil(started.is_set, timeout=2000)
    assert workspace.io_task_controller.busy
    assert "Exporting QC module" in workspace.status_label.text()
    assert not output.exists()

    release.set()
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=2000)
    assert output.exists()
    assert "Export complete" in workspace.status_label.text()
