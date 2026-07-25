from __future__ import annotations

import json
from threading import Event, get_ident

import pandas as pd
from shiboken6 import isValid
from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QAction, QFont
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QGroupBox,
    QLabel,
    QListWidget,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTabWidget,
    QTextEdit,
    QToolBar,
)

from core.app_services import build_app_services
from core.event_bus import EventType
from core.project_context_service import ProjectContextError
from core.qc_workflow_service import QcWorkflowService
from core.table_view_service import TableViewService
from gui_qt.application import build_product_window
from gui_qt.i18n import LanguageController, get_or_create_language_controller
from gui_qt.qc_results_page import QtQcResultsPage
from models.table_view_state import (
    ColumnViewState,
    FilterCondition,
    FilterExpression,
    FilterGroup,
    SortRule,
    filter_expression_to_json_object,
)



def _module_payload(*, name="AnatQC", label=None, rater="rater1"):
    return {
        "name": name,
        "label": label or f"{name} label",
        "rater": rater,
        "ezqcid": None,
        "watch_mode": False,
        "tags": {"1": {"label": "Motion", "value": False}},
        "scores": {
            "1": {
                "label": "Quality",
                "num": "Poor,Fair,Good",
                "num_": "Poor,Fair,Good",
                "value": None,
            }
        },
        "code": "freeview {image}",
        "interper": "shell",
        "control": True,
        "select_filter": None,
        "showing": True,
        "code_exe": None,
        "time": None,
        "notes": None,
        "button": {},
    }


def _site_filter(value: str, *, operator: str = "!=") -> FilterExpression:
    return FilterExpression(
        groups=(
            FilterGroup(
                group_id="site-filter",
                join="all",
                conditions=(
                    FilterCondition(
                        column="site",
                        operator=operator,
                        value=value,
                        condition_id="site-condition",
                    ),
                ),
            ),
        ),
    )


def _open_live_qc_filter(window, qtbot):
    qtbot.mouseClick(window.qc_workspace.filter_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: getattr(window, "qc_filter_dialog", None) is not None,
        timeout=3000,
    )
    return window.qc_filter_dialog


def _start_module_qc(window, qtbot, module_name="AnatQC"):
    previous = window.qc_controller
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons[module_name],
        Qt.LeftButton,
    )
    qtbot.waitUntil(
        lambda: window.qc_controller is not previous,
        timeout=3000,
    )
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )
    return window.qc_controller


def _add_project(
    services,
    tmp_path,
    name,
    *,
    prefix="",
    second_module=False,
    module_label=None,
    module_rater="rater1",
    identity_first=True,
):
    configuration = services.configuration_service
    configuration.create_project(name, tmp_path)
    columns = {
        "ezqcid": ["SUB001", "SUB002", "SUB003"],
        "site": ["A", "B", "C"],
        "image": [f"/{prefix}one.nii", f"/{prefix}two.nii", f"/{prefix}three.nii"],
    }
    if not identity_first:
        columns = {
            "site": columns["site"],
            "image": columns["image"],
            "ezqcid": columns["ezqcid"],
        }
    configuration.replace_subjects(pd.DataFrame(columns))
    configuration.save_module(
        _module_payload(label=module_label, rater=module_rater),
        original_name="example",
    )
    if second_module:
        configuration.add_module("FuncQC", "Functional QC")
        configuration.save_module(
            _module_payload(name="FuncQC", rater="rater2"),
            original_name="FuncQC",
        )


def _window(qtbot, tmp_path, *, second_module=False):
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE", second_module=second_module)
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    return window, services


def _primary_navigation_labels(window):
    return [
        (
            window.project_navigation_label.text()
            if index == window.project_page_index
            else window.navigation.item(index).text().splitlines()[0]
        )
        for index in range(window.navigation.count())
    ]


def _visible_page_texts(page) -> tuple[str, ...]:
    """Return human-visible control text for the currently selected page."""

    texts: list[str] = []
    for label in page.findChildren(QLabel):
        if label.isVisibleTo(page) and label.text().strip():
            texts.append(label.text().strip())
    for button in page.findChildren(QAbstractButton):
        if button.isVisibleTo(page) and button.text().strip():
            texts.append(button.text().strip())
    for group in page.findChildren(QGroupBox):
        if group.isVisibleTo(page) and group.title().strip():
            texts.append(group.title().strip())
    for tabs in page.findChildren(QTabWidget):
        if tabs.isVisibleTo(page):
            texts.extend(
                tabs.tabText(index).strip()
                for index in range(tabs.count())
                if tabs.tabText(index).strip()
            )
    for combo in page.findChildren(QComboBox):
        if combo.isVisibleTo(page):
            texts.extend(
                combo.itemText(index).strip()
                for index in range(combo.count())
                if combo.itemText(index).strip()
            )
    return tuple(texts)


def _presentation_texts(root) -> tuple[str, ...]:
    texts: list[str] = []
    objects = (root, *root.findChildren(QObject))
    for obj in objects:
        for getter_name in ("accessibleName", "toolTip", "windowTitle"):
            getter = getattr(obj, getter_name, None)
            if getter is not None:
                value = getter()
                if isinstance(value, str) and value.strip():
                    texts.append(value.strip())
        if isinstance(obj, QLabel):
            texts.append(obj.text().strip())
        elif isinstance(obj, QAbstractButton):
            texts.append(obj.text().strip())
        if isinstance(obj, QGroupBox):
            texts.append(obj.title().strip())
        if isinstance(obj, (QLineEdit, QPlainTextEdit, QTextEdit)):
            texts.append(obj.placeholderText().strip())
        if isinstance(obj, QComboBox):
            texts.extend(obj.itemText(index).strip() for index in range(obj.count()))
        if isinstance(obj, QListWidget):
            texts.extend(obj.item(index).text().strip() for index in range(obj.count()))
        if isinstance(obj, QTabWidget):
            texts.extend(obj.tabText(index).strip() for index in range(obj.count()))
        if isinstance(obj, QTableWidget):
            texts.extend(
                obj.horizontalHeaderItem(index).text().strip()
                for index in range(obj.columnCount())
                if obj.horizontalHeaderItem(index) is not None
            )
    texts.extend(
        action.text().strip()
        for action in root.findChildren(QAction)
        if action.text().strip()
    )
    return tuple(text for text in texts if text)


def test_empty_product_shell_keeps_configuration_available_without_writes(qtbot, tmp_path) -> None:
    registry = tmp_path / "projects.json"
    services = build_app_services(registry)
    window = build_product_window(services)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    assert window.current_context.project_name == ""
    assert window.workspace_stack.count() == 6
    assert window.navigation.currentRow() == window.project_page_index
    assert "尚未打开项目" in window.shell_empty_label.text()
    assert not registry.exists()


def test_qt_main_window_uses_six_direct_navigation_pages(qtbot, tmp_path) -> None:
    window, services = _window(qtbot, tmp_path)

    assert window.current_context.project_name == "SAMPLE"
    assert window.project_combo.currentText() == "SAMPLE"
    assert window.module_combo.currentData() == "AnatQC"
    assert window.table_workspace.result.matched_total == 3
    assert window.qc_workspace is None
    assert getattr(window, "qc_controller", None) is None
    assert window.config_workspace.configuration is services.configuration_service
    assert _primary_navigation_labels(window) == [
        "项目选择",
        "质控名单导入",
        "质控前名单",
        "常量设置",
        "质控模块",
        "质控结果",
    ]
    assert window.navigation.item(0).text() == ""
    assert (
        window.navigation.itemWidget(window.navigation.item(0))
        is window.project_navigation_content
    )
    assert window.project_navigation_label.text() == "项目选择"
    assert window.project_navigation_context.text() == "SAMPLE"
    assert window.project_navigation_context.isVisibleTo(window.navigation)
    assert window.findChild(QLabel, "activeProjectSummary") is None
    assert window.workspace_stack.count() == 6
    assert window.findChild(QListWidget, "primaryNavigation") is window.navigation
    assert window.findChild(QStackedWidget, "workspaceStack") is window.workspace_stack
    assert window.findChild(QTabWidget, "productWorkspaces") is None
    assert window.findChild(QListWidget, "primaryNavigation").isVisible()
    assert window.findChild(QStackedWidget, "workspaceStack").isVisible()
    assert window.findChild(QToolBar, "shellContextToolbar") is None
    host_font = QFont(window.font())
    assert window.navigation.spacing() == 4
    assert window.navigation.wordWrap()
    if host_font.pointSizeF() > 0:
        assert window.navigation.font().pointSizeF() == host_font.pointSizeF() + 1
    line_height = window.navigation.fontMetrics().lineSpacing()
    assert window.navigation.item(1).sizeHint().height() >= line_height + 16
    assert window.navigation.item(0).sizeHint().height() >= 2 * line_height + 16


def test_qt_main_window_navigation_switches_exact_page(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path)

    assert window.direct_pages == (
        window.project_page,
        window.qc_list_import_page,
        window.pre_qc_list_page,
        window.constants_page,
        window.modules_page,
        window.results_page,
    )
    for row, page in enumerate(window.direct_pages):
        window.navigation.setCurrentRow(row)
        assert window.workspace_stack.currentIndex() == row
        assert window.workspace_stack.currentWidget() is page


def test_runtime_language_switch_updates_six_pages_and_preserves_context(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    context = window.current_context
    table = window.table_workspace
    table.begin_filter_edit()
    table.set_filter_draft(
        (
            FilterCondition(
                column="site",
                operator="!=",
                value="C",
                condition_id="site-not-c",
            ),
        )
    )
    assert table.apply_filter_draft()
    assert table.apply_sort_rules((SortRule("site", ascending=False),))
    assert table.apply_column_state(
        ColumnViewState(
            order=("ezqcid", "site", "image"),
            hidden=("image",),
            pinned=("ezqcid",),
        )
    )
    assert table.set_page_size(1)
    assert table.next_page()
    assert table.select_source_position(0)
    applied_state = table.applied_state
    table_result = table.result
    table_window = table.row_window
    page_offset = table.page_offset
    selected_source_position = table.selected_source_position
    window.navigation.setCurrentRow(window.results_page_index)
    selected_page = window.workspace_stack.currentWidget()

    assert window.language_button.text() == "English"
    qtbot.mouseClick(window.language_button, Qt.LeftButton)

    assert isinstance(window.language, LanguageController)
    assert window.language.language == "en"
    assert _primary_navigation_labels(window) == [
        "Project selection",
        "QC list import",
        "Pre-QC list",
        "Constants",
        "QC modules",
        "QC results",
    ]
    assert window.navigation.item(0).text() == ""
    assert window.project_navigation_label.text() == "Project selection"
    assert window.project_navigation_context.text() == "SAMPLE"
    assert window.navigation.accessibleName() == "EasyQC feature navigation"
    assert window.language_button.text() == "中文"
    assert (
        window.language_button.accessibleName()
        == "Switch the interface to Chinese"
    )
    assert window.workspace_stack.accessibleName() == "Current EasyQC page"
    assert window.config_workspace.project_state_preview.text() == "Open now"
    assert [
        window.config_workspace.add_score_button.text(),
        window.config_workspace.remove_score_button.text(),
        window.config_workspace.add_tag_button.text(),
        window.config_workspace.remove_tag_button.text(),
    ] == [
        "Add rating item",
        "Delete rating item",
        "Add tag",
        "Delete tag",
    ]
    assert window.current_context is context
    assert window.workspace_stack.currentWidget() is selected_page
    assert window.navigation.currentRow() == window.results_page_index
    assert table.applied_state is applied_state
    assert table.result is table_result
    assert table.row_window is table_window
    assert table.page_offset == page_offset == 1
    assert table.selected_source_position == selected_source_position == 0
    assert table.applied_state.conditions == applied_state.conditions
    assert table.applied_state.sort_rules == applied_state.sort_rules
    assert table.applied_state.columns == applied_state.columns
    assert table.applied_state.page_size == 1
    filter_chip = next(
        action
        for action in table.applied_toolbar.actions()
        if action.data() == "site-not-c"
    )
    assert filter_chip.text() == "site Does not equal C  ×"
    assert filter_chip.toolTip() == "Remove filter site Does not equal C"
    assert (
        table.table_model.headerData(1, Qt.Horizontal, Qt.ToolTipRole)
        == "Sort priority 1 · Descending"
    )

    qtbot.mouseClick(window.language_button, Qt.LeftButton)
    assert _primary_navigation_labels(window)[0] == "项目选择"
    assert table.applied_state is applied_state
    assert table.result is table_result
    assert table.row_window is table_window
    assert table.page_offset == page_offset
    assert table.selected_source_position == selected_source_position
    filter_chip = next(
        action
        for action in table.applied_toolbar.actions()
        if action.data() == "site-not-c"
    )
    assert filter_chip.text() == "site 不等于 C  ×"
    assert filter_chip.toolTip() == "移除筛选 site 不等于 C"


def test_navigation_can_collapse_and_restore_current_workspace(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    window.navigation.setCurrentRow(window.results_page_index)
    selected_page = window.workspace_stack.currentWidget()

    assert not window.navigation_collapsed
    assert window.navigation_panel.minimumWidth() >= 202
    assert window.navigation.isVisible()
    qtbot.mouseClick(window.navigation_toggle_button, Qt.LeftButton)

    assert window.navigation_collapsed
    assert window.navigation_panel.maximumWidth() <= 52
    assert window.navigation_toggle_button.isVisible()
    assert not window.navigation.isVisible()
    assert not window.language_bar.isVisible()
    assert window.workspace_stack.currentWidget() is selected_page
    assert window.navigation.currentRow() == window.results_page_index

    window.resize(760, 560)
    qtbot.mouseClick(window.navigation_toggle_button, Qt.LeftButton)

    assert not window.navigation_collapsed
    assert window.navigation_panel.minimumWidth() >= 202
    assert window.navigation.isVisible()
    assert window.language_bar.isVisible()
    assert window.workspace_stack.currentWidget() is selected_page
    assert window.navigation.currentRow() == window.results_page_index


def test_single_language_button_toggles_without_losing_page_or_draft(
    qtbot,
    tmp_path,
) -> None:
    window, services = _window(qtbot, tmp_path)
    window.language.set_language("zh_CN")
    window.navigation.setCurrentRow(window.qc_list_import_page_index)
    selected_page = window.workspace_stack.currentWidget()
    draft = pd.DataFrame(
        {"ezqcid": ["DRAFT001", "DRAFT002"], "site": ["A", "B"]}
    )
    window.config_workspace.subjects_tab._install_draft(draft)

    assert window.findChild(QComboBox, "languageSelector") is None
    assert window.language_button.text() == "English"
    qtbot.mouseClick(window.language_button, Qt.LeftButton)

    assert window.language.language == "en"
    assert window.language_button.text() == "中文"
    assert window.workspace_stack.currentWidget() is selected_page
    pd.testing.assert_frame_equal(
        window.config_workspace.subjects_tab.draft,
        draft,
    )
    pd.testing.assert_frame_equal(
        services.configuration_service.subjects(),
        window.current_context.subjects,
    )

    qtbot.mouseClick(window.language_button, Qt.LeftButton)
    assert window.language.language == "zh_CN"
    assert window.language_button.text() == "English"
    assert window.workspace_stack.currentWidget() is selected_page
    pd.testing.assert_frame_equal(
        window.config_workspace.subjects_tab.draft,
        draft,
    )


def test_runtime_language_switch_preserves_module_and_rater_text(
    qtbot,
    tmp_path,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(
        services,
        tmp_path,
        "SAMPLE",
        module_label="常量设置",
        module_rater="项目选择",
    )
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    window.language.set_language("en")

    assert window.current_context.project_name == "SAMPLE"
    assert window.project_navigation_context.text() == "SAMPLE"
    assert window.project_combo.currentText() == "SAMPLE"
    module_item = window.config_workspace.module_list.item(0)
    module_row = window.config_workspace.module_list.itemWidget(module_item)
    assert module_row.findChild(QLabel, "moduleRowTitle").text() == "常量设置"
    assert module_row.findChild(QLabel, "moduleRowDetail").text() == (
        "AnatQC · 项目选择"
    )
    start_button = module_row.findChild(QPushButton, "moduleRowStart")
    assert start_button.text() == "Start QC"
    assert start_button.accessibleName() == "Start QC 常量设置"


def test_all_six_pages_have_no_untranslated_chinese_in_english_mode(
    qtbot,
    tmp_path,
) -> None:
    get_or_create_language_controller().set_language("en")
    window, _services = _window(qtbot, tmp_path)
    window.language.set_language("en")
    untranslated: dict[int, list[str]] = {}

    for index, page in enumerate(window.direct_pages):
        window.navigation.setCurrentRow(index)
        qtbot.wait(1)
        values = sorted(
            {
                text
                for text in _presentation_texts(page)
                if any("\u3400" <= char <= "\u9fff" for char in text)
                and text != "中文"
            }
        )
        if values:
            untranslated[index] = values

    window.language.set_language("zh_CN")
    assert untranslated == {}


def test_results_navigation_owns_direct_shared_results_page(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path)
    window.navigation.setCurrentRow(window.results_page_index)

    assert isinstance(window.results_page, QtQcResultsPage)
    assert window.results_workspace is window.results_page.table_workspace
    assert window.results_workspace is not window.table_workspace
    assert window.results_workspace.service is window.current_context.table_view_service
    assert window.workspace_stack.currentWidget() is window.results_page
    assert window.findChild(QLabel, "qcResultsTitle") is None


def test_non_first_ezqcid_project_loads_and_module_launch_click_responds(
    qtbot,
    tmp_path,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(
        services,
        tmp_path,
        "SAMPLE",
        identity_first=False,
    )
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    assert window.current_context.project_name == "SAMPLE"
    assert window.table_workspace.applied_state.columns.order[0] == "ezqcid"
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["AnatQC"],
        Qt.LeftButton,
    )
    qtbot.waitUntil(lambda: window.qc_controller is not None, timeout=3000)

    assert window.qc_controller.isVisible()
    assert (
        window.config_workspace.module_launch_status_label.text()
        == "已启动质控：AnatQC label"
    )


def test_pre_qc_derived_column_action_persists_and_refreshes_both_tables(
    qtbot,
    tmp_path,
) -> None:
    window, services = _window(qtbot, tmp_path)
    window.resize(1180, 760)
    window.navigation.setCurrentRow(window.pre_qc_list_page_index)
    qtbot.waitUntil(
        lambda: window.table_workspace.pinned_view.width()
        == min(
            window.table_workspace._pinned_content_width(),
            int(
                window.table_workspace.table_surface.contentsRect().width()
                * window.table_workspace.PINNED_SURFACE_FRACTION
            ),
        )
    )
    assert (
        window.table_workspace.pinned_view.geometry().right()
        < window.table_workspace.table_view.geometry().left()
    )

    assert window.table_workspace.derive_action.text() == "新增列"
    assert window.results_workspace.derive_action.text() == "新增列"
    assert window.table_workspace.columns_action.text().startswith("列显示")
    assert window.results_workspace.columns_action.text().startswith("列显示")
    qtbot.mouseClick(window.table_workspace.derive_button, Qt.LeftButton)
    dialog = window.table_workspace.derived_column_dialog
    assert dialog is not None and dialog.isVisible()
    dialog.name_edit.setText("site_copy")
    dialog.editor.set_source_column("site")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)

    qtbot.waitUntil(
        lambda: "site_copy" in window.current_context.subjects.columns,
        timeout=5000,
    )
    qtbot.waitUntil(
        lambda: not window.context_task_controller.busy,
        timeout=5000,
    )

    assert services.configuration_service.subjects()["site_copy"].tolist() == [
        "A",
        "B",
        "C",
    ]
    assert "site_copy" in {
        profile.name for profile in window.table_workspace.service.profiles
    }
    assert "site_copy" in {
        profile.name for profile in window.results_workspace.service.profiles
    }
    assert window.table_workspace.derive_status_label.text() == "已生成列：site_copy"


def test_results_page_derived_action_persists_from_subject_columns_only(
    qtbot,
    tmp_path,
) -> None:
    window, services = _window(qtbot, tmp_path)
    window.navigation.setCurrentRow(window.results_page_index)
    result_projection = window.current_context.subjects.copy(deep=True)
    result_projection["AnatQC.rater1.score1"] = ["Good", "Fair", "Good"]
    window.results_workspace.replace_service(
        TableViewService(result_projection),
        preserve_state=False,
    )

    assert window.results_workspace.derive_button is not None
    qtbot.mouseClick(window.results_workspace.derive_button, Qt.LeftButton)
    dialog = window.results_workspace.derived_column_dialog
    assert dialog is not None and dialog.isVisible()
    assert [
        dialog.editor.source_combo.itemData(index)
        for index in range(dialog.editor.source_combo.count())
    ] == list(window.current_context.subjects.columns)
    assert (
        dialog.editor.source_combo.findData("AnatQC.rater1.score1")
        == -1
    )

    dialog.name_edit.setText("site_from_results")
    dialog.editor.set_source_column("site")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: "site_from_results" in window.current_context.subjects.columns,
        timeout=5000,
    )
    qtbot.waitUntil(
        lambda: not window.context_task_controller.busy,
        timeout=5000,
    )

    assert services.configuration_service.subjects()["site_from_results"].tolist() == [
        "A",
        "B",
        "C",
    ]


def test_row_context_menus_cover_pre_qc_results_and_active_qc_queue(
    qtbot,
    tmp_path,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(
        services,
        tmp_path,
        "SAMPLE",
        second_module=True,
    )
    services.configuration_service.save_module_filter(
        "FuncQC",
        _site_filter("B", operator="=="),
    )
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    pre_index = window.table_workspace.table_model.index(0, 0)
    window.table_workspace._open_row_context_menu(
        window.table_workspace.pinned_view,
        window.table_workspace.pinned_view.visualRect(pre_index).center(),
    )
    pre_menu = window.table_workspace.active_row_context_menu
    assert pre_menu is not None
    assert not pre_menu.module_actions["FuncQC"].isEnabled()

    result_index = window.results_workspace.table_model.index(1, 1)
    window.results_workspace._open_row_context_menu(
        window.results_workspace.table_view,
        window.results_workspace.table_view.visualRect(result_index).center(),
    )
    results_menu = window.results_workspace.active_row_context_menu
    assert results_menu is not None
    assert results_menu.module_actions["FuncQC"].isEnabled()
    results_menu.module_actions["FuncQC"].trigger()
    qtbot.waitUntil(
        lambda: (
            not window.qc_filter_task_controller.busy
            and window.active_workflow is not None
            and window.active_workflow.current_module.name == "FuncQC"
        ),
        timeout=3000,
    )

    assert window.qc_controller is not None
    assert window.active_workflow.current_module.name == "FuncQC"
    assert window.active_workflow.subject_ids == ("SUB002",)
    queue_index = window.qc_workspace.queue_model.index(0, 1)
    window.qc_workspace._open_queue_context_menu(
        window.qc_workspace.queue_table.visualRect(queue_index).center()
    )
    assert window.qc_workspace.active_row_context_menu is not None
    assert (
        window.qc_workspace.active_row_context_menu.context.ezqcid
        == "SUB002"
    )


def test_row_record_action_starts_toggleable_read_only_and_dirty_draft_blocks_replacement(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE")
    project = services.configuration_service.current_project
    seed = QcWorkflowService(
        _module_payload(),
        services.configuration_service.subjects(),
        rating_dir=project.rating_dir / "AnatQC" / "rater1",
        code_executor=services.code_executor,
    )
    seed.set_score("1", "Good")
    seed.save()
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    first_index = window.table_workspace.table_model.index(0, 0)
    window.table_workspace._open_row_context_menu(
        window.table_workspace.pinned_view,
        window.table_workspace.pinned_view.visualRect(first_index).center(),
    )
    record_menu = window.table_workspace.active_row_context_menu
    record_menu.record_actions[("SUB001", "AnatQC", "rater1")].trigger()

    assert window.active_workflow.subject_ids == ("SUB001", "SUB002", "SUB003")
    assert window.active_workflow.initial_read_only
    assert not window.active_workflow.watch_mode
    assert window.active_workflow.current_module.scores["1"].value == "Good"
    assert window.qc_workspace.read_only_box.isChecked()
    assert window.qc_workspace.read_only_box.isEnabled()
    window.qc_workspace.read_only_box.click()
    assert window.qc_workspace.score_buttons["1"]["Fair"].isEnabled()

    window.table_workspace._open_row_context_menu(
        window.table_workspace.pinned_view,
        window.table_workspace.pinned_view.visualRect(first_index).center(),
    )
    window.table_workspace.active_row_context_menu.module_actions[
        "AnatQC"
    ].trigger()
    qtbot.waitUntil(
        lambda: (
            not window.qc_filter_task_controller.busy
            and window.active_workflow is not None
            and not window.active_workflow.watch_mode
        ),
        timeout=3000,
    )
    qtbot.mouseClick(window.qc_workspace.score_buttons["1"]["Fair"], Qt.LeftButton)
    assert window.active_workflow.dirty
    current_controller = window.qc_controller

    second_index = window.table_workspace.table_model.index(1, 0)
    window.table_workspace._open_row_context_menu(
        window.table_workspace.pinned_view,
        window.table_workspace.pinned_view.visualRect(second_index).center(),
    )
    window.table_workspace.active_row_context_menu.module_actions[
        "AnatQC"
    ].trigger()

    assert window.qc_controller is current_controller
    assert window.active_workflow.current_ezqcid == "SUB001"
    assert "请先保存或放弃" in window.shell_error_label.text()


def test_three_requested_pages_present_same_table_action_order(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path)

    def root(text: str) -> str:
        return text.split(" (", 1)[0]

    import_page = window.config_workspace.subjects_tab
    import_actions = [
        root(button.text())
        for button in (
            import_page.filter_button,
            import_page.sort_button,
            import_page.columns_button,
            import_page.derive_button,
        )
    ]
    pre_qc_actions = [
        root(action.text())
        for action in (
            window.table_workspace.filter_action,
            window.table_workspace.sort_action,
            window.table_workspace.columns_action,
            window.table_workspace.derive_action,
        )
    ]
    result_actions = [
        root(action.text())
        for action in (
            window.results_workspace.filter_action,
            window.results_workspace.sort_action,
            window.results_workspace.columns_action,
            window.results_workspace.derive_action,
        )
    ]

    expected = ["筛选", "排序", "列显示", "新增列"]
    assert import_actions == expected
    assert pre_qc_actions == expected
    assert result_actions == expected


def test_results_refresh_preserves_clean_qc_controller_and_view_state(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    controller = _start_module_qc(window, qtbot)
    assert window.results_workspace.apply_sort_rules(
        (SortRule("site", ascending=False),)
    )

    window.navigation.setCurrentRow(window.results_page_index)
    qtbot.mouseClick(window.results_page.refresh_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    assert window.qc_controller is controller
    assert window.results_workspace.applied_state.sort_rules == (
        SortRule("site", ascending=False),
    )
    assert not window.results_page.error_label.isVisible()


def test_results_refresh_error_is_visible_and_later_success_clears_it(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path)
    window.navigation.setCurrentRow(window.results_page_index)
    real_prepare = services.project_context_service.prepare_initial

    def fail_prepare():
        raise ProjectContextError("rating aggregation unavailable")

    monkeypatch.setattr(
        services.project_context_service,
        "prepare_initial",
        fail_prepare,
    )
    qtbot.mouseClick(window.results_page.refresh_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    assert "rating aggregation unavailable" in window.results_page.error_label.text()

    monkeypatch.setattr(
        services.project_context_service,
        "prepare_initial",
        real_prepare,
    )
    qtbot.mouseClick(window.results_page.refresh_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    assert not window.results_page.error_label.isVisible()


def test_results_refresh_rejects_dirty_qc_with_specific_visible_error(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    _start_module_qc(window, qtbot)
    qtbot.mouseClick(
        window.qc_workspace.score_buttons["1"]["Good"],
        Qt.LeftButton,
    )

    window.navigation.setCurrentRow(window.results_page_index)
    qtbot.mouseClick(window.results_page.refresh_button, Qt.LeftButton)

    assert not window.context_task_controller.busy
    assert window.qc_workspace.workflow.dirty
    assert window.results_page.error_label.text() == (
        "请先保存或放弃当前质控修改，再刷新结果"
    )
    assert window.qc_controller.close_discarding_draft()


def test_direct_configuration_pages_have_no_visible_nested_tabs(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path)

    assert window.config_workspace.tabs.isHidden()
    assert window.config_workspace.constants_tab.parentWidget() is window.constants_page
    assert isinstance(window.qc_list_import_scroll, QScrollArea)
    assert window.qc_list_import_scroll.widgetResizable()
    assert window.qc_list_import_scroll.widget() is window.config_workspace.subjects_tab
    assert window.config_workspace.subjects_tab.parentWidget() is (
        window.qc_list_import_scroll.viewport()
    )
    assert window.config_workspace.modules_tab.parentWidget() is window.modules_page
    for page_index, page, content in (
        (
            window.qc_list_import_page_index,
            window.qc_list_import_page,
            window.config_workspace.subjects_tab,
        ),
        (
            window.constants_page_index,
            window.constants_page,
            window.config_workspace.constants_tab,
        ),
        (
            window.modules_page_index,
            window.modules_page,
            window.config_workspace.modules_tab,
        ),
    ):
        window.navigation.setCurrentRow(page_index)
        qtbot.waitUntil(page.isVisible)
        assert content.isVisibleTo(page)
        assert content.size().width() > 0
        assert content.size().height() > 0


def test_direct_module_page_keeps_module_filter_transaction_reachable(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    workspace = window.config_workspace
    window.resize(720, 560)
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.waitUntil(window.modules_page.isVisible)
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy,
        timeout=3000,
    )
    workspace.module_editor_scroll.ensureWidgetVisible(
        workspace.clear_module_filter_button
    )

    assert workspace.module_filter_section.isVisibleTo(window.modules_page)
    assert workspace.module_filter_summary.text() == "全部名单"
    assert workspace.set_module_filter_button.isVisibleTo(window.modules_page)
    assert workspace.clear_module_filter_button.isVisibleTo(window.modules_page)


def test_shell_uses_neutral_list_language_for_visible_context(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path)

    visible_shell_text = " ".join(
        [
            *(
                window.navigation.item(index).text()
                for index in range(window.navigation.count())
            ),
            window.shell_status_label.text(),
            window.shell_empty_label.text(),
        ]
    ).casefold()
    assert not hasattr(window, "qc_placeholder")
    assert not hasattr(window, "qc_page")
    assert "受试者" not in visible_shell_text
    assert "被试" not in visible_shell_text
    assert "变量设置" not in visible_shell_text
    assert "subject" not in visible_shell_text


def test_all_six_pages_use_approved_runtime_vocabulary_without_repeated_titles(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    forbidden = (
        "受试者",
        "被试",
        "变量设置",
        "项目设置",
        "重新打开图像",
        "查看器已连接",
        "open qc",
        "subject",
    )

    assert window.windowTitle() == "EasyQC"
    assert window.shell_status_label.text() == "已加载项目：SAMPLE"
    for row, (navigation_label, page) in enumerate(
        zip(window.NAVIGATION_LABELS, window.direct_pages, strict=True)
    ):
        window.navigation.setCurrentRow(row)
        qtbot.waitUntil(page.isVisible)
        visible = _visible_page_texts(page)
        normalized = "\n".join(visible).casefold()
        assert not any(term.casefold() in normalized for term in forbidden), (
            navigation_label,
            visible,
        )
        assert navigation_label not in visible

    module_header = window.config_workspace.module_list_header.layout()
    project_header = window.config_workspace.project_list_header.layout()
    assert module_header.indexOf(window.config_workspace.module_list_title) < (
        module_header.indexOf(window.config_workspace.module_list_toolbar)
    )
    assert project_header.indexOf(window.config_workspace.project_list_title) < (
        project_header.indexOf(window.config_workspace.project_toolbar)
    )
    assert window.config_workspace.new_module_action.text() == "新建模块"
    assert window.config_workspace.import_module_action.text() == "导入模块"


def test_pre_qc_and_results_share_one_chinese_table_inspector_vocabulary(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    expected_outer = {
        "筛选",
        "排序",
        "列显示",
        "查找",
        "上一页",
        "下一页",
        "视图设置",
        "关闭",
        "重置",
        "取消",
        "应用",
    }
    expected_filter = {
        "组间关系",
        "添加组",
        "组内关系",
        "删除组",
        "添加条件",
        "启用",
        "列",
        "条件",
        "值",
        "删除",
    }
    forbidden_english_controls = {
        "Filter",
        "Sort",
        "Columns",
        "Find",
        "Export…",
        "Previous",
        "Next",
        "View options",
        "Close",
        "Reset",
        "Cancel",
        "Apply",
        "Across groups",
        "Add group",
        "Within this group",
        "Remove group",
        "Add condition",
        "Use",
        "Column",
        "Operator",
        "Value",
        "Remove",
    }

    for page_index, workspace in (
        (window.pre_qc_list_page_index, window.table_workspace),
        (window.results_page_index, window.results_workspace),
    ):
        window.navigation.setCurrentRow(page_index)
        workspace.open_filter_inspector()
        qtbot.waitUntil(workspace.view_inspector.isVisible)
        visible = set(_visible_page_texts(window.workspace_stack.currentWidget()))
        assert expected_outer <= visible
        assert expected_filter <= visible
        assert forbidden_english_controls.isdisjoint(visible)
        assert workspace.export_action.text() == "导出…"
        assert workspace.export_action in workspace.action_toolbar.actions()
        assert [workspace.inspector_tabs.tabText(index) for index in range(3)] == [
            "筛选",
            "排序",
            "列显示",
        ]
        assert workspace.inspector_filter_panel.top_join_combo.itemText(0) == "满足全部组"
        assert workspace.inspector_filter_panel.group_editors[0].join_combo.itemText(0) == (
            "满足全部"
        )
        assert workspace.inspector_filter_panel.condition_rows[0].operator_combo.itemText(0) == (
            "等于"
        )
        workspace.open_sort_inspector()
        assert workspace.inspector_sort_panel.add_button.text() == "添加排序"
        rule_row = workspace.inspector_sort_panel.add_rule()
        assert rule_row.direction_combo.itemText(0) == "升序"
        workspace.open_columns_inspector()
        assert workspace.inspector_columns_panel.search_label.text() == "搜索列"
        assert workspace.inspector_columns_panel.pin_button.text() == "固定"
        workspace.close_view_inspector()


def test_product_shell_reduced_viewport_keeps_navigation_and_content_reachable(
    qtbot,
    tmp_path,
) -> None:
    long_project = "PROJECT_WITH_A_VERY_LONG_CROSS_PLATFORM_NAME_" + "QC_" * 12
    long_module = "解剖结构质量控制模块_需要完整显示给人工复核人员_" + "模块" * 12
    services = build_app_services(tmp_path / "projects.json")
    _add_project(
        services,
        tmp_path,
        long_project,
        module_label=long_module,
    )
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    assert window.minimumWidth() <= 720
    window.resize(720, 560)
    qtbot.waitUntil(lambda: window.width() == 720)
    assert window.navigation.isVisible()
    assert window.workspace_stack.isVisible()
    assert window.navigation.width() >= window.navigation.minimumSizeHint().width()
    assert window.reload_action.shortcut().toString()
    assert window.project_combo.toolTip() == long_project
    assert window.module_combo.toolTip() == window.module_combo.currentText()
    assert long_module in window.module_combo.toolTip()


def test_pre_qc_list_has_no_qc_launch_action(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path)
    table = window.table_workspace
    assert table.apply_sort_rules((SortRule("site", ascending=False),))
    assert table.select_source_position(2)

    assert not table.open_qc_action.isVisible()
    assert table.open_qc_action not in table.action_toolbar.actions()
    assert window.qc_workspace is None


def test_module_rows_are_the_sole_visible_qc_launch_and_use_exact_module_name(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path, second_module=True)
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.waitUntil(window.modules_page.isVisible)
    qtbot.waitUntil(
        lambda: all(
            button.isVisible()
            for button in window.config_workspace.module_start_buttons.values()
        )
    )

    visible_launch_buttons = [
        button
        for button in window.findChildren(QPushButton)
        if button.isVisible() and button.text() == "启动质控"
    ]
    assert set(visible_launch_buttons) == set(
        window.config_workspace.module_start_buttons.values()
    )
    assert len(visible_launch_buttons) == 2
    assert all(
        any(
            row_widget is not None and row_widget.isAncestorOf(button)
            for row in range(window.config_workspace.module_list.count())
            for row_widget in [
                window.config_workspace.module_list.itemWidget(
                    window.config_workspace.module_list.item(row)
                )
            ]
        )
        for button in visible_launch_buttons
    )

    _start_module_qc(window, qtbot, "FuncQC")

    assert window.qc_workspace.workflow.current_module.name == "FuncQC"
    assert window.qc_controller is not None
    assert window.qc_controller.isWindow()
    assert window.qc_controller.parent() is None
    assert window.qc_controller.windowTitle() == "EasyQC · FuncQC · rater2"
    assert window.qc_controller.centralWidget() is window.qc_workspace
    assert window.qc_controller.isVisible()
    assert window.module_combo.currentData() == "FuncQC"
    assert window.config_workspace._selected_module_name == "FuncQC"


def test_module_launch_core_error_remains_visible_on_module_page(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    window.navigation.setCurrentRow(window.modules_page_index)

    def fail_launch(*_args, **_kwargs):
        raise ProjectContextError("查看器配置缺少 image 列")

    monkeypatch.setattr(
        window.context_service,
        "create_qc_workflow",
        fail_launch,
    )
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["AnatQC"],
        Qt.LeftButton,
    )
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_controller is None
    assert window.config_workspace.module_launch_status_label.isVisibleTo(
        window.modules_page
    )
    assert (
        window.config_workspace.module_launch_status_label.text()
        == "启动失败：查看器配置缺少 image 列"
    )


def test_module_launch_replaces_exactly_one_top_level_controller(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path, second_module=True)
    first_controller = _start_module_qc(window, qtbot)
    first_workflow = window.qc_workspace.workflow

    _start_module_qc(window, qtbot, "FuncQC")

    assert window.qc_controller is not first_controller
    assert window.qc_workspace.workflow is not first_workflow
    assert not isValid(first_controller) or not first_controller.isVisible()
    assert window.qc_controller.isVisible()
    assert window.findChildren(type(window.qc_controller)) == []


def test_stale_table_callback_is_rejected_after_same_id_project_switch(qtbot, tmp_path) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "ALPHA", prefix="alpha/")
    _add_project(services, tmp_path, "BETA", prefix="beta/")
    window = build_product_window(services)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    qtbot.waitUntil(
        lambda: not window.config_workspace.module_filter_task_controller.busy,
        timeout=3000,
    )
    assert window.load_project("ALPHA")
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    stale_callback = window.table_workspace.on_open_qc

    assert window.load_project("BETA")
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    current_workflow = window.qc_workspace
    stale_callback("SUB001")

    assert window.current_context.project_name == "BETA"
    assert window.qc_workspace is current_workflow
    assert "已失效" in window.shell_error_label.text()


def test_module_filter_save_blocks_project_switch_and_module_launch(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "ALPHA", prefix="alpha/")
    _add_project(services, tmp_path, "BETA", prefix="beta/")
    window = build_product_window(services)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    qtbot.waitUntil(
        lambda: not window.config_workspace.module_filter_task_controller.busy,
        timeout=3000,
    )
    assert window.load_project("ALPHA")
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.waitUntil(
        lambda: not window.config_workspace.module_filter_task_controller.busy,
        timeout=3000,
    )
    real_save = services.configuration_service.save_module_filter
    save_started = Event()
    save_release = Event()

    def delayed_save(module_name, expression, **kwargs):
        save_started.set()
        assert save_release.wait(2)
        return real_save(module_name, expression, **kwargs)

    monkeypatch.setattr(
        services.configuration_service,
        "save_module_filter",
        delayed_save,
    )
    window.config_workspace._submit_module_filter_save(
        "AnatQC",
        _site_filter("A", operator="=="),
    )
    qtbot.waitUntil(save_started.is_set, timeout=3000)

    switch_accepted = window.load_project("BETA")
    launch_accepted = window.start_qc_module("AnatQC")
    save_release.set()
    qtbot.waitUntil(
        lambda: not window.config_workspace.module_filter_task_controller.busy,
        timeout=3000,
    )
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    qtbot.waitUntil(lambda: not window.qc_filter_task_controller.busy, timeout=3000)

    assert (switch_accepted, launch_accepted) == (False, False)
    assert window.current_context.project_name == "ALPHA"
    assert services.configuration_service.current_project.name == "ALPHA"
    assert services.configuration_service.modules()[0].qc_filter == (
        filter_expression_to_json_object(_site_filter("A", operator="=="))
    )


def test_main_close_is_blocked_while_module_filter_save_is_running(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path)
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.waitUntil(
        lambda: not window.config_workspace.module_filter_task_controller.busy,
        timeout=3000,
    )
    real_save = services.configuration_service.save_module_filter
    save_started = Event()
    save_release = Event()
    save_finished = Event()

    def delayed_save(module_name, expression, **kwargs):
        save_started.set()
        assert save_release.wait(2)
        try:
            return real_save(module_name, expression, **kwargs)
        finally:
            save_finished.set()

    monkeypatch.setattr(
        services.configuration_service,
        "save_module_filter",
        delayed_save,
    )
    qtbot.mouseClick(
        window.config_workspace.set_module_filter_button,
        Qt.LeftButton,
    )
    dialog = window.config_workspace._module_filter_dialog
    assert dialog is not None
    expression = _site_filter("A", operator="==")
    dialog.editor.set_expression(expression)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(save_started.is_set, timeout=3000)
    assert window.config_workspace.module_filter_write_busy

    first_close = window.close()
    visible_after_first_close = window.isVisible()
    close_feedback = window.shell_error_label.text()
    module_close_feedback = (
        window.config_workspace.module_filter_error_label.text()
    )
    module_close_feedback_is_visible = (
        window.config_workspace.module_filter_error_label.isVisibleTo(window)
    )
    save_release.set()
    qtbot.waitUntil(save_finished.is_set, timeout=3000)
    qtbot.waitUntil(
        lambda: not window.config_workspace.module_filter_task_controller.busy,
        timeout=3000,
    )

    assert first_close is False
    assert visible_after_first_close
    assert close_feedback == "质控名单筛选事务正在完成，请稍候"
    assert module_close_feedback == "质控名单筛选事务正在完成，请稍候"
    assert module_close_feedback_is_visible
    assert services.configuration_service.modules()[0].qc_filter == (
        filter_expression_to_json_object(expression)
    )
    assert window.config_workspace.module_filter_summary.text() == "已筛选：1 条"
    assert window.close() is True


def test_product_restart_restores_last_opened_project_and_its_table(qtbot, tmp_path) -> None:
    registry = tmp_path / "projects.json"
    services = build_app_services(registry)
    _add_project(services, tmp_path, "ALPHA", prefix="alpha/")
    _add_project(services, tmp_path, "BETA", prefix="beta/")
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    assert window.current_context.project_name == "BETA"

    assert window.load_project("ALPHA")
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    assert window.current_context.project_name == "ALPHA"
    assert json.loads(registry.read_text(encoding="utf-8"))["last_project"] == "ALPHA"
    window.close()

    restarted_services = build_app_services(registry)
    restarted = build_product_window(restarted_services)
    qtbot.addWidget(restarted)
    restarted.show()
    qtbot.waitUntil(lambda: not restarted.context_task_controller.busy, timeout=3000)

    assert restarted.current_context.project_name == "ALPHA"
    assert restarted.current_context.subjects["image"].tolist() == [
        "/alpha/one.nii",
        "/alpha/two.nii",
        "/alpha/three.nii",
    ]
    assert restarted.table_workspace.result.matched_total == 3


def test_failed_qc_replacement_keeps_old_workflow_then_success_closes_it(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path, second_module=True)
    _start_module_qc(window, qtbot)
    previous = window.qc_workspace.workflow
    close_calls = []
    monkeypatch.setattr(
        services.code_executor,
        "close_current_processes",
        lambda *args, **kwargs: close_calls.append(True),
    )
    real_factory = services.project_context_service.create_qc_workflow

    def fail_func(*args, **kwargs):
        if kwargs.get("module_name") == "FuncQC":
            raise ProjectContextError("replacement failed")
        return real_factory(*args, **kwargs)

    monkeypatch.setattr(services.project_context_service, "create_qc_workflow", fail_func)
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["FuncQC"],
        Qt.LeftButton,
    )
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_workspace.workflow is previous
    assert close_calls == []
    assert "replacement failed" in window.shell_error_label.text()

    monkeypatch.setattr(services.project_context_service, "create_qc_workflow", real_factory)
    _start_module_qc(window, qtbot, "FuncQC")
    assert window.qc_workspace.workflow is not previous
    assert close_calls == [True]


def test_qc_filter_prepares_hidden_candidate_before_save_then_swaps_and_refreshes(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path)
    old_controller = _start_module_qc(window, qtbot)
    old_workflow = window.qc_workspace.workflow
    real_save = services.configuration_service.save_module_filter
    real_create = window.context_service.create_qc_workflow
    real_build = window._build_qc_controller
    save_started = Event()
    allow_save = Event()
    gui_thread = get_ident()
    candidate_workflow_threads = []
    candidate_controller_threads = []
    published_threads = []
    expected_identity_calls = []
    services.event_bus.subscribe(
        EventType.MODULES_CHANGED,
        lambda _event: published_threads.append(get_ident()),
    )

    def tracked_create(*args, **kwargs):
        candidate_workflow_threads.append(get_ident())
        return real_create(*args, **kwargs)

    def tracked_build(workflow):
        candidate_controller_threads.append(get_ident())
        return real_build(workflow)

    def delayed_save(
        module_name,
        expression,
        *,
        notify=True,
        expected_identities=None,
        expected_state=None,
    ):
        expected_identity_calls.append(expected_identities)
        save_started.set()
        assert allow_save.wait(2)
        return real_save(
            module_name,
            expression,
            notify=notify,
            expected_identities=expected_identities,
            expected_state=expected_state,
        )

    monkeypatch.setattr(
        services.configuration_service,
        "save_module_filter",
        delayed_save,
    )
    monkeypatch.setattr(
        window.context_service,
        "create_qc_workflow",
        tracked_create,
    )
    monkeypatch.setattr(window, "_build_qc_controller", tracked_build)
    qtbot.mouseClick(window.qc_workspace.filter_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: getattr(window, "qc_filter_dialog", None) is not None,
        timeout=3000,
    )
    dialog = window.qc_filter_dialog
    expression = _site_filter("A")
    dialog.editor.set_expression(expression)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(save_started.is_set, timeout=3000)

    transaction = window._pending_qc_filter
    assert window.qc_controller is old_controller
    assert window.qc_workspace.workflow is old_workflow
    assert transaction.candidate_controller is not None
    assert not transaction.candidate_controller.isVisible()
    assert services.configuration_service.modules()[0].qc_filter is None
    dialog.reject()
    qtbot.waitUntil(lambda: not isValid(dialog), timeout=3000)
    assert window._pending_qc_filter.dialog is None

    allow_save.set()
    qtbot.waitUntil(lambda: window.qc_controller is not old_controller, timeout=3000)
    replacement = window.qc_controller
    qtbot.waitUntil(
        lambda: not window.context_task_controller.busy,
        timeout=3000,
    )
    qtbot.waitUntil(
        lambda: not window.config_workspace.module_filter_task_controller.busy,
        timeout=3000,
    )

    assert replacement.isVisible()
    assert window.qc_controller is replacement
    assert window.qc_workspace.workflow.subject_ids == ("SUB002", "SUB003")
    assert window.qc_workspace.workflow.current_ezqcid == "SUB002"
    assert "2 条" in window.qc_workspace.filter_button.toolTip()
    assert services.configuration_service.modules()[0].qc_filter == (
        filter_expression_to_json_object(expression)
    )
    assert expected_identity_calls == [("SUB002", "SUB003")]
    assert candidate_workflow_threads
    assert all(thread != gui_thread for thread in candidate_workflow_threads)
    assert candidate_controller_threads == [gui_thread]
    assert published_threads == [get_ident()]
    assert window.config_workspace.module_filter_summary.text() == "已筛选：2 条"
    assert not isValid(old_controller) or not old_controller.isVisible()


def test_qc_filter_dialog_profiles_prepare_off_thread_and_cancel_writes_nothing(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path)
    _start_module_qc(window, qtbot)
    gui_thread = get_ident()
    prepare_threads = []
    prepared_columns = []
    real_init = TableViewService.__init__

    def tracked_init(service, source):
        prepare_threads.append(get_ident())
        prepared_columns.append(tuple(source.columns))
        real_init(service, source)

    monkeypatch.setattr(TableViewService, "__init__", tracked_init)
    dialog = _open_live_qc_filter(window, qtbot)

    assert prepare_threads
    assert all(thread != gui_thread for thread in prepare_threads)
    assert prepared_columns[-1] == tuple(window.current_context.subjects.columns)
    assert tuple(profile.name for profile in dialog.editor._profiles) == (
        "ezqcid",
        "site",
        "image",
    )

    dialog.reject()
    qtbot.waitUntil(lambda: window.qc_filter_dialog is None, timeout=3000)

    assert services.configuration_service.modules()[0].qc_filter is None
    assert window.qc_workspace.workflow.subject_ids == (
        "SUB001",
        "SUB002",
        "SUB003",
    )


def test_qc_filter_persistence_failure_preserves_then_success_retains_identity(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path)
    old_controller = _start_module_qc(window, qtbot)
    window.qc_workspace.workflow.navigate_to("SUB002")
    window.qc_workspace._refresh()
    close_calls = []
    monkeypatch.setattr(
        services.code_executor,
        "close_current_processes",
        lambda: close_calls.append(True),
    )
    real_save = services.configuration_service.save_module_filter

    def fail_save(*args, **kwargs):
        raise OSError("module filter disk unavailable")

    monkeypatch.setattr(
        services.configuration_service,
        "save_module_filter",
        fail_save,
    )
    dialog = _open_live_qc_filter(window, qtbot)
    dialog.editor.set_expression(_site_filter("A"))
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_controller is old_controller
    assert window.qc_workspace.workflow.current_ezqcid == "SUB002"
    assert services.configuration_service.modules()[0].qc_filter is None
    assert "disk unavailable" in window.qc_workspace.error_text
    assert dialog.isVisible()
    assert close_calls == []

    monkeypatch.setattr(
        services.configuration_service,
        "save_module_filter",
        real_save,
    )
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: window.qc_controller is not old_controller, timeout=3000)
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_workspace.workflow.subject_ids == ("SUB002", "SUB003")
    assert window.qc_workspace.workflow.current_ezqcid == "SUB002"
    assert close_calls == [True]


def test_qc_filter_expected_identity_mismatch_preserves_live_controller_and_settings(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path)
    old_controller = _start_module_qc(window, qtbot)
    old_workflow = window.qc_workspace.workflow
    changed_subjects = window.current_context.subjects.copy(deep=True)
    changed_subjects["site"] = ["A", "A", "A"]
    close_calls = []
    monkeypatch.setattr(
        services.configuration_service,
        "subjects",
        lambda: changed_subjects.copy(deep=True),
    )
    monkeypatch.setattr(
        services.code_executor,
        "close_current_processes",
        lambda: close_calls.append(True),
    )

    dialog = _open_live_qc_filter(window, qtbot)
    dialog.editor.set_expression(_site_filter("A"))
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_controller is old_controller
    assert window.qc_workspace.workflow is old_workflow
    assert window.qc_workspace.workflow.current_ezqcid == "SUB001"
    assert services.configuration_service.modules()[0].qc_filter is None
    assert "matches changed" in window.qc_workspace.error_text
    assert dialog.isVisible()
    assert close_calls == []


def test_qc_filter_zero_match_and_candidate_failure_write_nothing(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path)
    old_controller = _start_module_qc(window, qtbot)
    save_calls = []
    viewer_close_calls = []
    real_create = window.context_service.create_qc_workflow
    monkeypatch.setattr(
        services.code_executor,
        "close_current_processes",
        lambda: viewer_close_calls.append(True),
    )
    monkeypatch.setattr(
        services.configuration_service,
        "save_module_filter",
        lambda *args, **kwargs: save_calls.append((args, kwargs)),
    )

    dialog = _open_live_qc_filter(window, qtbot)
    dialog.editor.set_expression(_site_filter("Z", operator="=="))
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_controller is old_controller
    assert "matches no subjects" in window.qc_workspace.error_text
    assert save_calls == []
    assert services.configuration_service.modules()[0].qc_filter is None

    def fail_candidate(*args, **kwargs):
        raise ProjectContextError("candidate workflow unavailable")

    monkeypatch.setattr(
        window.context_service,
        "create_qc_workflow",
        fail_candidate,
    )
    dialog.editor.set_expression(_site_filter("A"))
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_controller is old_controller
    assert "candidate workflow unavailable" in window.qc_workspace.error_text
    assert save_calls == []
    assert viewer_close_calls == []
    assert services.configuration_service.modules()[0].qc_filter is None
    monkeypatch.setattr(
        window.context_service,
        "create_qc_workflow",
        real_create,
    )

    def fail_controller(_workflow):
        raise RuntimeError("candidate controller unavailable")

    monkeypatch.setattr(window, "_build_qc_controller", fail_controller)
    dialog.editor.set_expression(_site_filter("A"))
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_controller is old_controller
    assert "candidate controller unavailable" in window.qc_workspace.error_text
    assert save_calls == []
    assert viewer_close_calls == []
    assert services.configuration_service.modules()[0].qc_filter is None

    invalid = FilterExpression(
        groups=(
            FilterGroup(
                group_id="invalid-group",
                join="all",
                conditions=(
                    FilterCondition(
                        column="rating_only",
                        operator="==",
                        value="Good",
                        condition_id="invalid-condition",
                    ),
                ),
            ),
        ),
    )
    dialog.applyRequested.emit(invalid)
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_controller is old_controller
    assert "rating_only" in window.qc_workspace.error_text
    assert save_calls == []
    assert viewer_close_calls == []
    assert services.configuration_service.modules()[0].qc_filter is None


def test_saved_filter_launch_then_qc_clear_restores_complete_queue(qtbot, tmp_path) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE")
    expression = _site_filter("A")
    assert services.configuration_service.save_module_filter(
        "AnatQC",
        expression,
        notify=False,
    ) == ("SUB002", "SUB003")
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    window.navigation.setCurrentRow(window.modules_page_index)

    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["AnatQC"],
        Qt.LeftButton,
    )
    qtbot.waitUntil(lambda: window.qc_controller is not None, timeout=3000)
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_workspace.workflow.subject_ids == ("SUB002", "SUB003")
    assert window.qc_workspace.workflow.current_ezqcid == "SUB002"
    assert "2 条" in window.qc_workspace.filter_button.toolTip()

    filtered_controller = window.qc_controller
    dialog = _open_live_qc_filter(window, qtbot)
    dialog.editor.set_expression(FilterExpression())
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: window.qc_controller is not filtered_controller,
        timeout=3000,
    )
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert window.qc_workspace.workflow.subject_ids == (
        "SUB001",
        "SUB002",
        "SUB003",
    )
    assert window.qc_workspace.workflow.current_ezqcid == "SUB002"
    assert services.configuration_service.modules()[0].qc_filter == (
        filter_expression_to_json_object(FilterExpression())
    )
    assert window.qc_workspace.filter_button.toolTip() == "全部质控名单 · 3 条"


def test_unfiltered_module_launch_builds_workflow_off_thread_and_controller_on_gui(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    gui_thread = get_ident()
    workflow_threads = []
    controller_threads = []
    real_create = window.context_service.create_qc_workflow
    real_build = window._build_qc_controller

    def tracked_create(*args, **kwargs):
        workflow_threads.append(get_ident())
        return real_create(*args, **kwargs)

    def tracked_build(workflow):
        controller_threads.append(get_ident())
        return real_build(workflow)

    monkeypatch.setattr(
        window.context_service,
        "create_qc_workflow",
        tracked_create,
    )
    monkeypatch.setattr(window, "_build_qc_controller", tracked_build)

    _start_module_qc(window, qtbot)
    qtbot.waitUntil(
        lambda: not window.qc_filter_task_controller.busy,
        timeout=3000,
    )

    assert workflow_threads
    assert all(thread != gui_thread for thread in workflow_threads)
    assert controller_threads == [gui_thread]


def test_dirty_qc_disables_context_replacement_and_close_requires_confirmation(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    _start_module_qc(window, qtbot)
    qtbot.mouseClick(window.qc_workspace.score_buttons["1"]["Good"], Qt.LeftButton)

    assert window.qc_workspace.workflow.dirty
    assert not window.project_combo.isEnabled()
    assert not window.module_combo.isEnabled()
    assert not window.config_workspace.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No)
    assert not window.close()
    assert window.isVisible()

    qtbot.mouseClick(window.qc_workspace.save_button, Qt.LeftButton)
    assert not window.qc_workspace.workflow.dirty
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    assert window.project_combo.isEnabled()
    assert window.config_workspace.isEnabled()


def test_main_close_accepts_dirty_draft_once_and_closes_controller_resources(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path)
    controller = _start_module_qc(window, qtbot)
    close_calls = []
    monkeypatch.setattr(
        services.code_executor,
        "close_current_processes",
        lambda: close_calls.append(True),
    )
    qtbot.mouseClick(window.qc_workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    questions = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: questions.append((args, kwargs)) or QMessageBox.Yes,
    )

    assert window.close()

    assert len(questions) == 1
    assert close_calls == [True]
    assert not isValid(controller) or not controller.isVisible()


def test_rating_save_refreshes_table_and_preserves_view_state_and_qc_session(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    _start_module_qc(window, qtbot)
    table = window.table_workspace
    results = window.results_workspace
    assert table.apply_sort_rules((SortRule("site", ascending=False),))
    assert results.apply_sort_rules((SortRule("site", ascending=True),))
    assert table.find_identity_exact("SUB001")
    qc = window.qc_workspace
    qtbot.mouseClick(qc.score_buttons["1"]["Good"], Qt.LeftButton)
    qtbot.mouseClick(qc.save_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    assert window.qc_workspace is qc
    assert window.table_workspace.applied_state.sort_rules == (
        SortRule("site", ascending=False),
    )
    assert window.results_workspace.applied_state.sort_rules == (
        SortRule("site", ascending=True),
    )
    result_frame = window.table_workspace.service.get_window(
        window.table_workspace.result,
        0,
        max(1, window.table_workspace.result.matched_total),
    ).dataframe
    assert "AnatQC.rater1.score1" in result_frame.columns
    row = result_frame.set_index("ezqcid").loc["SUB001"]
    assert row["AnatQC.rater1.score1"] == "Good"
    assert window.table_workspace.find_identity_exact("SUB001")
    results_frame = window.results_workspace.service.get_window(
        window.results_workspace.result,
        0,
        max(1, window.results_workspace.result.matched_total),
    ).dataframe
    assert results_frame.set_index("ezqcid").loc["SUB001", "AnatQC.rater1.score1"] == "Good"


def test_initial_project_materialization_runs_without_blocking_qt(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE")
    started = Event()
    release = Event()
    real_prepare = services.project_context_service.prepare_initial

    def delayed_prepare():
        started.set()
        release.wait(2)
        return real_prepare()

    monkeypatch.setattr(
        services.project_context_service,
        "prepare_initial",
        delayed_prepare,
    )
    event_loop_progress = []
    window = build_product_window(services)
    qtbot.addWidget(window)
    QTimer.singleShot(0, lambda: event_loop_progress.append(True))
    qtbot.waitUntil(started.is_set, timeout=2000)
    qtbot.waitUntil(lambda: bool(event_loop_progress), timeout=2000)

    assert window.context_task_controller.busy
    assert "正在加载" in window.shell_status_label.text()
    release.set()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    assert window.current_context.project_name == "SAMPLE"
