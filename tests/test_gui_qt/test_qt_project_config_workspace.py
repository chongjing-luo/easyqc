from __future__ import annotations

from threading import Event, get_ident

import pandas as pd
from shiboken6 import isValid
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QToolBar,
    QToolButton,
)

from core.configuration_service import ConfigurationService
from core.event_bus import EventType
from core.project_service import ProjectService
from core.table_service import TableService
from gui_qt import project_config_workspace as workspace_module
from gui_qt.project_config_workspace import QtProjectConfigWorkspace
from models.table_view_state import (
    FilterCondition,
    FilterExpression,
    FilterGroup,
)


def _expression(
    column: str = "site",
    operator: str = "==",
    value="A",
) -> FilterExpression:
    return FilterExpression(
        groups=(
            FilterGroup(
                "group-1",
                "all",
                (
                    FilterCondition(
                        column,
                        operator,
                        value,
                        "condition-1",
                    ),
                ),
            ),
        ),
    )


def _workspace(qtbot, tmp_path, *, module_launcher=None):
    config = ConfigurationService(
        ProjectService(tmp_path / "projects.json"),
        TableService(),
    )
    config.create_project("SAMPLE", tmp_path)
    config.replace_subjects(pd.DataFrame({"ezqcid": ["SUB001", "SUB002"], "site": ["A", "B"]}))
    workspace = (
        QtProjectConfigWorkspace(config)
        if module_launcher is None
        else QtProjectConfigWorkspace(config, module_launcher=module_launcher)
    )
    qtbot.addWidget(workspace)
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=2000)
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=2000,
    )
    return workspace, config


def _constant_row(workspace, name: str) -> int:
    for row in range(workspace.constants_table.rowCount()):
        item = workspace.constants_table.item(row, 0)
        if item is not None and item.text() == name:
            return row
    raise AssertionError(f"constant row is missing: {name}")


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
    assert "正在加载配置" in workspace.status_label.text()
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


def test_constants_table_stretches_values_and_right_aligns_compact_actions(
    qtbot,
    tmp_path,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    long_path = "/含 空格/中文项目路径/" + "深层目录/" * 20 + "subjects.csv"
    config.set_constant("DATA_ROOT", long_path)
    workspace._refresh_constants()
    workspace.tabs.setCurrentWidget(workspace.constants_tab)
    workspace.resize(480, 520)
    workspace.show()

    table = workspace.constants_table
    header = table.horizontalHeader()
    row = _constant_row(workspace, "DATA_ROOT")
    actions = table.cellWidget(row, 2)
    action_layout = actions.layout()
    edit_button = action_layout.itemAt(1).widget()
    delete_button = action_layout.itemAt(2).widget()

    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.ResizeToContents
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.ResizeToContents
    assert not header.stretchLastSection()
    assert action_layout.getContentsMargins() == (0, 0, 0, 0)
    assert action_layout.spacing() == 4
    assert action_layout.count() == 3
    assert action_layout.itemAt(0).spacerItem() is not None
    assert edit_button.text() == "编辑"
    assert delete_button.text() == "删除"
    assert edit_button.isVisibleTo(actions)
    assert delete_button.isVisibleTo(actions)
    assert table.item(row, 1).toolTip() == long_path
    assert table.columnWidth(1) > table.columnWidth(0)
    assert table.columnWidth(1) > table.columnWidth(2)
    assert (
        table.columnViewportPosition(2) + table.columnWidth(2)
        <= table.viewport().width()
    )


def test_constant_row_actions_survive_refresh_search_and_reduced_width(
    qtbot,
    tmp_path,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    config.set_constant("DATA_ROOT", "/data")
    config.set_constant("OUTPUT_ROOT", "/results")
    workspace._refresh_constants()
    workspace.tabs.setCurrentWidget(workspace.constants_tab)
    workspace.resize(480, 520)
    workspace.show()

    workspace.constant_search.setText("output")
    assert workspace.constants_table.isRowHidden(
        _constant_row(workspace, "DATA_ROOT")
    )
    assert not workspace.constants_table.isRowHidden(
        _constant_row(workspace, "OUTPUT_ROOT")
    )
    workspace.constant_search.clear()
    workspace._refresh_constants()

    data_row = _constant_row(workspace, "DATA_ROOT")
    data_actions = workspace.constants_table.cellWidget(data_row, 2)
    data_layout = data_actions.layout()
    edit_button = data_layout.itemAt(1).widget()
    delete_button = data_layout.itemAt(2).widget()
    assert edit_button.isVisibleTo(data_actions)
    assert delete_button.isVisibleTo(data_actions)

    qtbot.mouseClick(edit_button, Qt.LeftButton)
    assert workspace.constant_name.text() == "DATA_ROOT"
    assert workspace.constant_value.text() == "/data"
    qtbot.mouseClick(workspace.cancel_constant_button, Qt.LeftButton)

    output_row = _constant_row(workspace, "OUTPUT_ROOT")
    output_actions = workspace.constants_table.cellWidget(output_row, 2)
    output_delete = output_actions.layout().itemAt(2).widget()
    qtbot.mouseClick(output_delete, Qt.LeftButton)

    assert config.constants() == {"DATA_ROOT": "/data"}
    assert workspace.constants_table.rowCount() == 1
    assert _constant_row(workspace, "DATA_ROOT") == 0


def test_qt_module_form_adds_and_reorders_without_json_editor(qtbot, tmp_path) -> None:
    workspace, config = _workspace(qtbot, tmp_path)

    assert workspace.add_module("AnatQC", "Anatomical QC")
    assert workspace.add_module("FuncQC", "Functional QC")
    assert workspace.move_module("FuncQC", -1)

    names = [module.name for module in config.modules()]
    assert names == ["example", "FuncQC", "AnatQC"]
    assert not hasattr(workspace, "json_editor")


def test_module_filter_section_prepares_complete_list_profiles_off_gui_thread(
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
        pd.DataFrame(
            {
                "ezqcid": ["SUB001", "SUB002", "SUB003"],
                "site": ["A", "B", "A"],
            }
        ),
        notify=False,
    )
    config.save_module_filter("example", _expression(), notify=False)
    started = Event()
    release = Event()
    worker_threads = []
    real_table_view_service = workspace_module.TableViewService

    def delayed_table_view_service(subjects):
        worker_threads.append(get_ident())
        started.set()
        release.wait(2)
        return real_table_view_service(subjects)

    monkeypatch.setattr(
        workspace_module,
        "TableViewService",
        delayed_table_view_service,
    )
    workspace = QtProjectConfigWorkspace(config, auto_refresh=False)
    qtbot.addWidget(workspace)
    gui_thread = get_ident()
    event_loop_progress = []
    QTimer.singleShot(0, lambda: event_loop_progress.append(True))

    workspace.refresh(config.snapshot())
    qtbot.waitUntil(started.is_set, timeout=2000)
    qtbot.waitUntil(lambda: bool(event_loop_progress), timeout=2000)

    editor_layout = workspace.module_editor_scroll.widget().layout()
    assert editor_layout.indexOf(workspace.module_filter_section) < (
        editor_layout.indexOf(workspace.score_table)
    )
    assert workspace.module_filter_title.text() == "质控名单"
    assert workspace.set_module_filter_button.text() == "设置筛选"
    assert workspace.clear_module_filter_button.text() == "清除筛选"
    assert worker_threads == [worker_threads[0]]
    assert worker_threads[0] != gui_thread

    release.set()
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=2000,
    )
    assert workspace.module_filter_summary.text() == "已筛选：2 条"
    assert tuple(profile.name for profile in workspace._module_filter_profiles) == (
        "ezqcid",
        "site",
    )

    workspace.resize(480, 420)
    workspace.show()
    workspace.tabs.setCurrentWidget(workspace.modules_tab)
    workspace.module_editor_scroll.ensureWidgetVisible(
        workspace.clear_module_filter_button
    )
    assert workspace.clear_module_filter_button.isVisibleTo(
        workspace.module_editor_scroll
    )


def test_module_filter_dialog_cancel_apply_and_clear_are_focused_transactions(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    config.add_module("FuncQC", "Functional QC")
    workspace._refresh_modules("example")
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=2000,
    )
    before_second = next(
        module.to_legacy_dict()
        for module in config.modules()
        if module.name == "FuncQC"
    )
    save_calls = []
    publish_threads = []
    gui_thread = get_ident()
    save_started = Event()
    save_release = Event()
    real_save = config.save_module_filter
    real_publish = config.publish_modules_changed

    def save_filter(module_name, expression, *, notify=True, expected_state=None):
        save_calls.append((module_name, expression, notify, get_ident()))
        if len(save_calls) == 1:
            save_started.set()
            save_release.wait(2)
        return real_save(
            module_name,
            expression,
            notify=notify,
            expected_state=expected_state,
        )

    def publish_modules_changed():
        publish_threads.append(get_ident())
        real_publish()

    monkeypatch.setattr(config, "save_module_filter", save_filter)
    monkeypatch.setattr(config, "publish_modules_changed", publish_modules_changed)

    qtbot.mouseClick(workspace.set_module_filter_button, Qt.LeftButton)
    cancelled_dialog = workspace._module_filter_dialog
    assert cancelled_dialog is not None
    cancelled_dialog.reject()
    assert save_calls == []

    qtbot.mouseClick(workspace.set_module_filter_button, Qt.LeftButton)
    dialog = workspace._module_filter_dialog
    assert dialog is not None
    dialog.editor.set_expression(_expression())
    event_loop_progress = []
    QTimer.singleShot(0, lambda: event_loop_progress.append(True))
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(save_started.is_set, timeout=2000)
    qtbot.waitUntil(lambda: bool(event_loop_progress), timeout=2000)
    assert workspace.module_filter_task_controller.busy
    save_release.set()
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=2000,
    )

    assert save_calls[0][:3] == ("example", _expression(), False)
    assert save_calls[0][3] != gui_thread
    assert publish_threads == [gui_thread]
    assert not isValid(dialog) or not dialog.isVisible()
    assert workspace.module_filter_summary.text() == "已筛选：1 条"
    assert next(
        module.to_legacy_dict()
        for module in config.modules()
        if module.name == "FuncQC"
    ) == before_second

    qtbot.mouseClick(workspace.clear_module_filter_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=2000,
    )

    assert save_calls[1][:3] == ("example", FilterExpression(), False)
    assert workspace.module_filter_summary.text() == "全部名单"
    assert publish_threads == [gui_thread, gui_thread]


def test_rejected_filter_dialog_during_save_does_not_break_completion(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    save_started = Event()
    save_release = Event()
    publish_threads = []
    gui_thread = get_ident()
    real_save = config.save_module_filter
    real_publish = config.publish_modules_changed

    def delayed_save(module_name, expression, *, notify=True, expected_state=None):
        save_started.set()
        save_release.wait(2)
        return real_save(
            module_name,
            expression,
            notify=notify,
            expected_state=expected_state,
        )

    def publish_modules_changed():
        publish_threads.append(get_ident())
        real_publish()

    monkeypatch.setattr(config, "save_module_filter", delayed_save)
    monkeypatch.setattr(config, "publish_modules_changed", publish_modules_changed)
    qtbot.mouseClick(workspace.set_module_filter_button, Qt.LeftButton)
    dialog = workspace._module_filter_dialog
    assert dialog is not None
    dialog.editor.set_expression(_expression())
    completion_validities = []
    real_complete_apply = dialog.complete_apply

    def track_complete_apply():
        completion_validities.append(isValid(dialog))
        real_complete_apply()

    dialog.complete_apply = track_complete_apply

    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(save_started.is_set, timeout=2000)
    dialog.reject()
    qtbot.waitUntil(lambda: not isValid(dialog), timeout=2000)
    save_release.set()
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=2000,
    )

    assert config.modules()[0].qc_filter == {
        "schema_version": 1,
        "group_join": "all",
        "groups": [
            {
                "group_id": "group-1",
                "join": "all",
                "conditions": [
                    {
                        "column": "site",
                        "operator": "==",
                        "value": "A",
                        "condition_id": "condition-1",
                        "enabled": True,
                    }
                ],
            }
        ],
    }
    assert workspace.module_filter_summary.text() == "已筛选：1 条"
    assert publish_threads == [gui_thread]
    assert completion_validities == []


def test_module_filter_invalid_apply_stays_open_and_dirty_or_new_form_blocks_writes(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    workspace, config = _workspace(qtbot, tmp_path)
    save_calls = []

    def reject_filter(
        module_name,
        expression,
        *,
        notify=True,
        expected_state=None,
    ):
        save_calls.append((module_name, expression, notify))
        raise ValueError("筛选条件无效")

    monkeypatch.setattr(config, "save_module_filter", reject_filter)
    qtbot.mouseClick(workspace.set_module_filter_button, Qt.LeftButton)
    dialog = workspace._module_filter_dialog
    assert dialog is not None
    dialog.editor.set_expression(_expression())
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=2000,
    )

    assert dialog.isVisible()
    assert dialog.editor.error_text == "筛选条件无效"
    dialog.reject()

    workspace.module_label.setText("未保存标签")
    qtbot.mouseClick(workspace.set_module_filter_button, Qt.LeftButton)
    qtbot.mouseClick(workspace.clear_module_filter_button, Qt.LeftButton)
    assert "保存" in workspace.error_text
    assert "放弃" in workspace.error_text
    assert len(save_calls) == 1

    workspace._set_error("")
    workspace._prepare_new_module()
    qtbot.mouseClick(workspace.set_module_filter_button, Qt.LeftButton)
    assert "保存" in workspace.error_text
    assert "放弃" in workspace.error_text
    workspace._set_error("")
    qtbot.mouseClick(workspace.clear_module_filter_button, Qt.LeftButton)
    assert "保存" in workspace.error_text
    assert "放弃" in workspace.error_text
    assert len(save_calls) == 1


def test_unsupported_legacy_module_filter_is_visible_replaceable_and_clearable(
    qtbot,
    tmp_path,
) -> None:
    config = ConfigurationService(
        ProjectService(tmp_path / "projects.json"),
        TableService(),
    )
    config.create_project("SAMPLE", tmp_path)
    config.replace_subjects(
        pd.DataFrame({"ezqcid": ["SUB001", "SUB002"], "site": ["A", "B"]}),
        notify=False,
    )
    module = config.modules()[0]
    module.select_filter = "SELECT ezqcid FROM df"
    config.save_module(module, original_name="example")
    workspace = QtProjectConfigWorkspace(config)
    qtbot.addWidget(workspace)
    workspace.show()
    workspace.tabs.setCurrentWidget(workspace.modules_tab)
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=2000)
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=2000,
    )

    assert "兼容" in workspace.module_filter_summary.text()
    assert "select_filter" in workspace.module_filter_error_label.text()
    assert workspace.module_filter_error_label.isVisibleTo(workspace)
    assert workspace.clear_module_filter_button.isEnabled()

    qtbot.mouseClick(workspace.set_module_filter_button, Qt.LeftButton)
    dialog = workspace._module_filter_dialog
    assert dialog is not None
    assert dialog.editor.expression() == FilterExpression()
    dialog.reject()

    qtbot.mouseClick(workspace.clear_module_filter_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=2000,
    )
    assert workspace.module_filter_summary.text() == "全部名单"
    saved = config.modules()[0]
    assert saved.qc_filter == {
        "schema_version": 1,
        "group_join": "all",
        "groups": [],
    }
    assert saved.select_filter is None


def test_stale_module_filter_preview_cannot_replace_new_selection_summary(
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
        pd.DataFrame({"ezqcid": ["SUB001", "SUB002"], "site": ["A", "B"]}),
        notify=False,
    )
    config.save_module_filter("example", _expression(), notify=False)
    config.add_module("FuncQC", "Functional QC")
    started = Event()
    release = Event()
    real_table_view_service = workspace_module.TableViewService

    def delayed_table_view_service(subjects):
        started.set()
        release.wait(2)
        return real_table_view_service(subjects)

    monkeypatch.setattr(
        workspace_module,
        "TableViewService",
        delayed_table_view_service,
    )
    workspace = QtProjectConfigWorkspace(config, auto_refresh=False)
    qtbot.addWidget(workspace)
    workspace.refresh(config.snapshot())
    qtbot.waitUntil(started.is_set, timeout=2000)

    workspace.module_list.setCurrentRow(1)
    assert workspace._selected_module_name == "FuncQC"
    release.set()
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=3000,
    )

    assert workspace._selected_module_name == "FuncQC"
    assert workspace.module_filter_summary.text() == "全部名单"

    save_started = Event()
    save_release = Event()
    real_save = config.save_module_filter

    def delayed_save(module_name, expression, *, notify=True, expected_state=None):
        save_started.set()
        save_release.wait(2)
        return real_save(
            module_name,
            expression,
            notify=notify,
            expected_state=expected_state,
        )

    monkeypatch.setattr(config, "save_module_filter", delayed_save)
    workspace._submit_module_filter_save("FuncQC", FilterExpression())
    qtbot.waitUntil(save_started.is_set, timeout=2000)
    workspace.module_list.setCurrentRow(0)
    assert workspace._selected_module_name == "example"
    save_release.set()
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=3000,
    )

    assert workspace._selected_module_name == "example"
    assert workspace.module_filter_summary.text() == "已筛选：1 条"


def test_module_list_header_owns_right_side_new_import_and_each_row_launches_exact_name(
    qtbot,
    tmp_path,
) -> None:
    launched = []
    workspace, _config = _workspace(
        qtbot,
        tmp_path,
        module_launcher=lambda name: launched.append(name) or True,
    )
    assert workspace.add_module("AnatQC", "Anatomical QC")
    workspace.resize(640, 560)
    workspace.show()
    workspace.tabs.setCurrentWidget(workspace.modules_tab)

    header_layout = workspace.module_list_header.layout()
    assert header_layout.indexOf(workspace.module_list_title) >= 0
    assert header_layout.indexOf(workspace.module_list_toolbar) > header_layout.indexOf(
        workspace.module_list_title
    )
    assert workspace.module_list_title.text() == "模块列表"
    assert workspace.new_module_action.text() == "新建模块"
    assert workspace.import_module_action.text() == "导入模块"
    assert workspace.new_module_action in workspace.module_list_toolbar.actions()
    assert workspace.import_module_action in workspace.module_list_toolbar.actions()
    assert workspace.new_module_action not in workspace.module_actions_toolbar.actions()
    assert workspace.import_module_action not in workspace.module_actions_toolbar.actions()
    assert workspace.new_module_button.isVisible()
    assert workspace.import_module_button.isVisible()

    assert set(workspace.module_start_buttons) == {"example", "AnatQC"}
    button = workspace.module_start_buttons["AnatQC"]
    assert isinstance(button, QPushButton)
    assert button.text() == "启动质控"
    matching_items = [
        workspace.module_list.item(row)
        for row in range(workspace.module_list.count())
        if workspace.module_list.item(row).data(Qt.UserRole) == "AnatQC"
    ]
    assert len(matching_items) == 1
    row_widget = workspace.module_list.itemWidget(matching_items[0])
    assert row_widget is not None and row_widget.isAncestorOf(button)

    qtbot.mouseClick(button, Qt.LeftButton)

    assert launched == ["AnatQC"]
    assert workspace._selected_module_name == "AnatQC"
    assert workspace.module_list.currentItem().data(Qt.UserRole) == "AnatQC"
    assert workspace.module_launch_status_label.text() == "已启动质控：Anatomical QC"

    row_labels = [
        label.text()
        for label in row_widget.findChildren(QLabel)
        if label.text().strip()
    ]
    assert row_labels == ["Anatomical QC", "AnatQC · 只读"]


def test_module_launch_failure_is_visible_on_module_page(qtbot, tmp_path) -> None:
    workspace, _config = _workspace(
        qtbot,
        tmp_path,
        module_launcher=lambda _name: False,
    )
    workspace.resize(640, 560)
    workspace.show()
    workspace.tabs.setCurrentWidget(workspace.modules_tab)

    qtbot.mouseClick(
        workspace.module_start_buttons["example"],
        Qt.LeftButton,
    )

    assert workspace.module_launch_status_label.isVisibleTo(workspace.modules_tab)
    assert "启动失败" in workspace.module_launch_status_label.text()
    assert "未被接受" in workspace.module_launch_status_label.text()


def test_module_footer_order_and_final_viewer_editor_support_long_commands(
    qtbot,
    tmp_path,
) -> None:
    workspace, _config = _workspace(qtbot, tmp_path)
    workspace.resize(640, 560)
    workspace.show()
    workspace.tabs.setCurrentWidget(workspace.modules_tab)

    footer_actions = [
        action.text()
        for action in workspace.module_actions_toolbar.actions()
        if action.text()
    ]
    assert footer_actions == ["删除模块", "导出模块", "放弃更改", "保存模块"]
    assert workspace.module_actions_toolbar.widgetForAction(
        workspace.delete_module_action
    ).x() < workspace.module_actions_toolbar.widgetForAction(
        workspace.export_module_action
    ).x()
    assert workspace.module_actions_toolbar.widgetForAction(
        workspace.discard_module_action
    ).x() < workspace.module_actions_toolbar.widgetForAction(
        workspace.save_module_action
    ).x()

    assert isinstance(workspace.module_code, QPlainTextEdit)
    assert workspace.module_code.lineWrapMode() == QPlainTextEdit.LineWrapMode.NoWrap
    assert workspace.module_code.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    assert workspace.module_code.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    assert workspace.module_code.font().family() == QFontDatabase.systemFont(
        QFontDatabase.SystemFont.FixedFont
    ).family()
    assert "ezqcid" in workspace.module_code.placeholderText()
    editor_layout = workspace.module_editor_scroll.widget().layout()
    assert editor_layout.indexOf(workspace.module_viewer_section) > editor_layout.indexOf(
        workspace.module_row_actions_toolbar
    )
    assert editor_layout.indexOf(workspace.module_actions_toolbar) > editor_layout.indexOf(
        workspace.module_viewer_section
    )

    workspace.module_code.setPlainText(
        "viewer --input {image} --title {ezqcid} " + "--very-long-option value " * 30
    )
    qtbot.waitUntil(
        lambda: workspace.module_code.horizontalScrollBar().maximum() > 0,
        timeout=2000,
    )


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
    assert selected_item.text() == ""
    assert selected_item.data(workspace.MODULE_LABEL_ROLE) == long_label
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
    assert all(
        not button.autoRaise()
        for button in (
            workspace.new_project_button,
            workspace.import_project_button,
            workspace.load_project_button,
            workspace.new_module_button,
            workspace.import_module_button,
            workspace.save_module_button,
            workspace.export_module_button,
        )
    )
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
    extension = workspace.module_list_toolbar.findChild(
        QToolButton,
        "qt_toolbar_ext_button",
    )
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
    assert "正在加载项目" in workspace.status_label.text()
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
    assert "正在导出质控模块" in workspace.status_label.text()
    assert not output.exists()

    release.set()
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=2000)
    assert output.exists()
    assert "导出完成" in workspace.status_label.text()
