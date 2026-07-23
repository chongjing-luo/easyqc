from __future__ import annotations

from threading import Event, get_ident

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QLabel,
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
    assert workspace.project_list.currentItem().text() == "SAMPLE"
    assert workspace.project_name_preview.text() == "SAMPLE"
    assert workspace.subjects_tab.current_row_count == 2
    assert workspace.subjects_tab.preview_model.rowCount() == 0


def test_project_list_header_owns_right_side_create_import_actions_and_read_only_preview(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    workspace.resize(640, 520)
    workspace.show()

    header_layout = workspace.project_list_header.layout()
    assert header_layout.indexOf(workspace.project_list_title) >= 0
    assert header_layout.indexOf(workspace.project_toolbar) > header_layout.indexOf(
        workspace.project_list_title
    )
    assert workspace.project_list_title.text() == "项目列表"
    assert workspace.new_project_action.text() == "新建项目"
    assert workspace.import_project_action.text() == "导入项目"
    assert workspace.reload_projects_action.text() == "刷新"
    assert workspace.open_project_directory_action.text() == "打开目录"
    assert workspace.remove_project_action.text() == "取消登记"
    assert workspace.load_project_action.text() == "打开项目"
    assert workspace.new_project_button.isVisible()
    assert workspace.import_project_button.isVisible()
    assert workspace.load_project_button.isVisible()
    assert workspace.project_name_preview.isReadOnly()
    assert workspace.project_path_preview.isReadOnly()
    assert workspace.project_state_preview.isReadOnly()
    assert workspace.project_path_preview.text() == str(config.current_project.path)
    opened_directories = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened_directories.append(url.toLocalFile()) or True,
    )
    qtbot.mouseClick(workspace.open_project_directory_button, Qt.LeftButton)
    assert opened_directories == [str(config.current_project.path)]


def test_project_list_click_previews_but_only_open_project_activates(
    qtbot,
    tmp_path,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    second = config.create_project("SECOND", tmp_path)
    config.load_project("SAMPLE")
    workspace.refresh()
    workspace.show()
    second_items = workspace.project_list.findItems("SECOND", Qt.MatchExactly)
    assert len(second_items) == 1

    item = second_items[0]
    qtbot.mouseClick(
        workspace.project_list.viewport(),
        Qt.LeftButton,
        pos=workspace.project_list.visualItemRect(item).center(),
    )

    assert config.current_project.name == "SAMPLE"
    assert workspace.project_name_preview.text() == "SECOND"
    assert workspace.project_path_preview.text() == str(second.path)
    assert workspace.project_state_preview.text() == "已登记"

    qtbot.mouseClick(workspace.load_project_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=2000)
    assert config.current_project.name == "SECOND"
    assert workspace.project_state_preview.text() == "当前打开"


def test_project_and_constants_pages_remove_repeated_titles_counts_and_visible_combo(
    qtbot,
    tmp_path,
) -> None:
    workspace, _config = _workspace(qtbot, tmp_path)

    assert workspace.findChild(QLabel, "configTitle") is None
    assert workspace.findChild(QComboBox, "projectSelector") is None
    assert workspace.project_combo.isHidden()
    assert workspace.findChild(QLabel, "constantsTitle") is None
    assert workspace.findChild(QLabel, "constantsCount") is None
    first_row = workspace.constants_tab.layout().itemAt(0).layout()
    assert first_row is not None
    assert first_row.indexOf(workspace.constant_name) >= 0
    assert first_row.indexOf(workspace.constant_value) >= 0
    assert first_row.indexOf(workspace.save_constant_button) >= 0
    assert workspace.save_constant_button.text() == "添加常量"


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
    assert workspace.subjects_tab.current_row_count == 2
    assert workspace.subject_model.rowCount() == 0


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
    assert workspace.project_list.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    assert workspace.project_path_preview.toolTip() == str(config.current_project.path)

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
    workspace.subjects_tab.preview_table.setFocus()
    qtbot.waitUntil(workspace.subjects_tab.preview_table.hasFocus)
    qtbot.keyClick(
        workspace.subjects_tab.preview_table,
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
    workspace.project_list.setCurrentItem(
        workspace.project_list.findItems("SAMPLE", Qt.MatchExactly)[0]
    )
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
    assert workspace.project_list.currentItem().text() == "SAMPLE"
    assert workspace.project_combo.currentText() == "SAMPLE"
    assert workspace.subjects_tab.current_row_count == 2
    assert workspace.subject_model.rowCount() == 0
    assert project_event_threads == [gui_thread]


def test_qc_list_import_page_is_composed_without_old_immediate_write_buttons(
    qtbot,
    tmp_path,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)

    assert workspace.subjects_tab.configuration is config
    assert workspace.subjects_tab.preview_table.editTriggers() == (
        QAbstractItemView.EditTrigger.NoEditTriggers
    )
    assert not hasattr(workspace, "replace_subjects_button")
    assert not hasattr(workspace, "merge_rows_button")
    assert not hasattr(workspace, "merge_columns_button")


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
