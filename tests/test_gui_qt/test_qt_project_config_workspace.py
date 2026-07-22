from __future__ import annotations

from threading import Event, get_ident

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QHeaderView,
    QScrollArea,
    QSplitter,
    QToolBar,
    QToolButton,
)

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


def test_qt_constant_row_double_click_loads_transactional_edit_form(
    qtbot,
    tmp_path,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    config.set_constant("DATA_ROOT", "/data/original")
    workspace._refresh_constants()
    workspace.tabs.setCurrentWidget(workspace.constants_tab)
    workspace.show()

    workspace.constants_table.cellDoubleClicked.emit(0, 0)

    assert workspace.constant_name.text() == "DATA_ROOT"
    assert workspace.constant_name.isReadOnly()
    assert workspace.constant_value.text() == "/data/original"
    assert workspace.save_constant_button.text() == "保存"
    workspace.constant_value.setText("/data/updated")
    qtbot.mouseClick(workspace.save_constant_button, Qt.LeftButton)
    assert config.constants()["DATA_ROOT"] == "/data/updated"
    assert not workspace.constant_name.isReadOnly()
    assert workspace.constant_name.text() == ""


def test_qt_constant_search_filters_visible_rows_without_mutating_values(
    qtbot,
    tmp_path,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    config.set_constant("DATA_ROOT", "/data")
    config.set_constant("OUTPUT_ROOT", "/results")
    workspace._refresh_constants()

    workspace.constant_search.setText("output")

    visible_names = {
        workspace.constants_table.item(row, 0).text()
        for row in range(workspace.constants_table.rowCount())
        if not workspace.constants_table.isRowHidden(row)
    }
    assert visible_names == {"OUTPUT_ROOT"}
    assert config.constants() == {"DATA_ROOT": "/data", "OUTPUT_ROOT": "/results"}

    workspace.constant_search.clear()
    assert all(
        not workspace.constants_table.isRowHidden(row)
        for row in range(workspace.constants_table.rowCount())
    )


def test_qt_module_form_adds_and_reorders_without_json_editor(qtbot, tmp_path) -> None:
    workspace, config = _workspace(qtbot, tmp_path)

    assert workspace.add_module("AnatQC", "Anatomical QC")
    assert workspace.add_module("FuncQC", "Functional QC")
    assert workspace.move_module("FuncQC", -1)

    names = [module.name for module in config.modules()]
    assert names == ["example", "FuncQC", "AnatQC"]
    assert not hasattr(workspace, "json_editor")


def test_qt_configuration_uses_responsive_toolbars_splitter_and_long_tooltips(
    qtbot,
    tmp_path,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    long_path = "/含 空格/中文项目路径/" + "深层目录/" * 20 + "subjects.csv"
    config.set_constant("DATA_ROOT", long_path)
    workspace._refresh_constants()
    long_label = "功能质量控制模块_长中文标签_" + "模块" * 24
    assert workspace.add_module("LongQC", long_label)
    workspace.resize(640, 520)
    workspace.show()
    workspace.activateWindow()
    workspace.tabs.setCurrentWidget(workspace.modules_tab)

    project_toolbar = workspace.findChild(QToolBar, "configProjectToolbar")
    module_splitter = workspace.findChild(QSplitter, "configModuleSplitter")
    editor_scroll = workspace.findChild(QScrollArea, "configModuleEditorScroll")
    module_toolbar = workspace.findChild(QToolBar, "configModuleActions")
    assert project_toolbar is workspace.project_toolbar
    assert not project_toolbar.isMovable()
    assert not project_toolbar.isFloatable()
    assert module_splitter is workspace.module_splitter
    assert not module_splitter.isCollapsible(0)
    assert not module_splitter.isCollapsible(1)
    assert editor_scroll is workspace.module_editor_scroll
    assert editor_scroll.widgetResizable()
    assert module_toolbar is workspace.module_actions_toolbar
    assert workspace.score_table.horizontalHeader().sectionResizeMode(0) == (
        QHeaderView.ResizeMode.Interactive
    )
    assert workspace.score_table.columnWidth(0) == (
        workspace.score_table.horizontalHeader().defaultSectionSize()
    )
    assert workspace.tag_table.horizontalHeader().stretchLastSection()
    assert workspace.constants_table.item(0, 1).toolTip() == long_path
    selected_item = workspace.module_list.currentItem()
    assert selected_item.toolTip() == selected_item.text()
    assert long_label in selected_item.toolTip()
    assert workspace.project_combo.width() >= workspace.project_combo.minimumSizeHint().width()
    assert workspace.project_combo.toolTip() == workspace.project_combo.currentText()

    actions = (
        workspace.load_project_action,
        workspace.new_project_action,
        workspace.import_project_action,
        workspace.save_module_action,
        workspace.import_module_action,
        workspace.export_module_action,
    )
    assert all(action.shortcut().toString() for action in actions)
    workspace.module_label.setText("键盘保存后的模块标签")
    workspace.module_label.setFocus()
    qtbot.waitUntil(workspace.module_label.hasFocus)
    qtbot.keyClick(
        workspace.module_label,
        Qt.Key.Key_S,
        Qt.KeyboardModifier.ControlModifier,
    )
    saved = next(module for module in config.modules() if module.name == "LongQC")
    assert saved.label == "键盘保存后的模块标签"

    workspace.module_label.setText("隐藏模块页不应响应保存快捷键")
    workspace.tabs.setCurrentWidget(workspace.subjects_tab)
    workspace.subject_table.setFocus()
    qtbot.waitUntil(workspace.subject_table.hasFocus)
    qtbot.keyClick(
        workspace.subject_table,
        Qt.Key.Key_S,
        Qt.KeyboardModifier.ControlModifier,
    )
    saved = next(module for module in config.modules() if module.name == "LongQC")
    assert saved.label == "键盘保存后的模块标签"

    workspace.tabs.setCurrentWidget(workspace.modules_tab)
    workspace.resize(480, 520)
    qtbot.waitUntil(lambda: workspace.width() == 480)
    extension = module_toolbar.findChild(QToolButton, "qt_toolbar_ext_button")
    assert extension is not None and extension.isVisible()


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
