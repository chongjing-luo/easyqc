from __future__ import annotations

from threading import Event

import pandas as pd
from shiboken6 import isValid
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QToolBar,
)

from core.app_services import build_app_services
from core.project_context_service import ProjectContextError
from gui_qt.application import build_product_window
from gui_qt.qc_results_page import QtQcResultsPage
from models.table_view_state import SortRule



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


def _add_project(
    services,
    tmp_path,
    name,
    *,
    prefix="",
    second_module=False,
    module_label=None,
):
    configuration = services.configuration_service
    configuration.create_project(name, tmp_path)
    configuration.replace_subjects(
        pd.DataFrame(
            {
                "ezqcid": ["SUB001", "SUB002", "SUB003"],
                "site": ["A", "B", "C"],
                "image": [f"/{prefix}one.nii", f"/{prefix}two.nii", f"/{prefix}three.nii"],
            }
        )
    )
    configuration.save_module(
        _module_payload(label=module_label),
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
        window.navigation.item(index).text().splitlines()[0]
        for index in range(window.navigation.count())
    ]


def test_empty_product_shell_keeps_configuration_available_without_writes(qtbot, tmp_path) -> None:
    registry = tmp_path / "projects.json"
    services = build_app_services(registry)
    window = build_product_window(services)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    assert window.current_context.project_name == ""
    assert window.workspace_stack.count() == 6
    assert window.navigation.currentRow() == window.project_page_index
    assert "No project" in window.shell_empty_label.text()
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
    assert window.navigation.item(0).text().splitlines() == ["项目选择", "SAMPLE"]
    assert window.findChild(QLabel, "activeProjectSummary") is None
    assert window.workspace_stack.count() == 6
    assert window.findChild(QListWidget, "primaryNavigation") is window.navigation
    assert window.findChild(QStackedWidget, "workspaceStack") is window.workspace_stack
    assert window.findChild(QTabWidget, "productWorkspaces") is None
    assert window.findChild(QListWidget, "primaryNavigation").isVisible()
    assert window.findChild(QStackedWidget, "workspaceStack").isVisible()
    assert window.findChild(QToolBar, "shellContextToolbar") is None


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


def test_results_navigation_owns_direct_shared_results_page(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path)
    window.navigation.setCurrentRow(window.results_page_index)

    assert isinstance(window.results_page, QtQcResultsPage)
    assert window.results_workspace is window.results_page.table_workspace
    assert window.results_workspace is not window.table_workspace
    assert window.results_workspace.service is window.current_context.table_view_service
    assert window.workspace_stack.currentWidget() is window.results_page
    assert window.findChild(QLabel, "qcResultsTitle") is None


def test_results_refresh_preserves_clean_qc_controller_and_view_state(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["AnatQC"],
        Qt.LeftButton,
    )
    controller = window.qc_controller
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
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["AnatQC"],
        Qt.LeftButton,
    )
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
    assert window.config_workspace.subjects_tab.parentWidget() is window.qc_list_import_page
    assert window.config_workspace.modules_tab.parentWidget() is window.modules_page


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

    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["FuncQC"],
        Qt.LeftButton,
    )

    assert window.qc_workspace.workflow.current_module.name == "FuncQC"
    assert window.qc_controller is not None
    assert window.qc_controller.isWindow()
    assert window.qc_controller.parent() is None
    assert window.qc_controller.windowTitle() == "EasyQC"
    assert window.qc_controller.centralWidget() is window.qc_workspace
    assert window.qc_controller.isVisible()
    assert window.module_combo.currentData() == "FuncQC"
    assert window.config_workspace._selected_module_name == "FuncQC"


def test_module_launch_replaces_exactly_one_top_level_controller(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path, second_module=True)
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["AnatQC"],
        Qt.LeftButton,
    )
    first_controller = window.qc_controller
    first_workflow = window.qc_workspace.workflow

    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["FuncQC"],
        Qt.LeftButton,
    )

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
    assert window.load_project("ALPHA")
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    stale_callback = window.table_workspace.on_open_qc

    assert window.load_project("BETA")
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    current_workflow = window.qc_workspace
    stale_callback("SUB001")

    assert window.current_context.project_name == "BETA"
    assert window.qc_workspace is current_workflow
    assert "stale" in window.shell_error_label.text().lower()


def test_failed_qc_replacement_keeps_old_workflow_then_success_closes_it(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path, second_module=True)
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["AnatQC"],
        Qt.LeftButton,
    )
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

    assert window.qc_workspace.workflow is previous
    assert close_calls == []
    assert "replacement failed" in window.shell_error_label.text()

    monkeypatch.setattr(services.project_context_service, "create_qc_workflow", real_factory)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["FuncQC"],
        Qt.LeftButton,
    )
    assert window.qc_workspace.workflow is not previous
    assert close_calls == [True]


def test_dirty_qc_disables_context_replacement_and_close_requires_confirmation(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["AnatQC"],
        Qt.LeftButton,
    )
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
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["AnatQC"],
        Qt.LeftButton,
    )
    controller = window.qc_controller
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
    window.navigation.setCurrentRow(window.modules_page_index)
    qtbot.mouseClick(
        window.config_workspace.module_start_buttons["AnatQC"],
        Qt.LeftButton,
    )
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
    assert "Loading" in window.shell_status_label.text()
    release.set()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    assert window.current_context.project_name == "SAMPLE"
