"""Direct read-only QC results page tests."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QAbstractItemView, QLabel, QPushButton, QToolBar

from core.table_view_service import TableViewService
from gui_qt.i18n import LanguageController
from gui_qt.qc_results_page import QtQcResultsPage
from models.table_view_state import FilterCondition, SortRule


def _source() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB002", "SUB003"],
            "site": ["A", "B", "A"],
            "AnatQC.rater1.score1": ["Good", "Fair", None],
        }
    )


def _long_source() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB001", "SUB002"],
            "module_name": ["AnatQC", "FuncQC", "AnatQC"],
            "rater": ["rater1", "rater2", "rater1"],
            "score1": ["Good", "Fair", "Poor"],
            "tag1": [True, False, True],
            "notes": ["", "review", ""],
            "time": ["2026-08-01", "2026-08-02", "2026-08-03"],
        }
    )


def _visible_toolbar_text(page: QtQcResultsPage) -> list[str]:
    return [
        action.text()
        for action in page.table_workspace.action_toolbar.actions()
        if action.isVisible() and not action.isSeparator() and action.text()
    ]


def test_results_page_is_direct_read_only_shared_table_without_repeated_title(
    qtbot,
) -> None:
    page = QtQcResultsPage(_source(), refresh_callback=lambda: True)
    qtbot.addWidget(page)
    page.resize(1000, 640)
    page.show()
    table = page.table_workspace
    qtbot.waitUntil(lambda: not table._pinned_width_update_pending)

    assert page.objectName() == "qcResultsPage"
    assert table.service.source_total == 3
    assert table.table_view.editTriggers() == QAbstractItemView.NoEditTriggers
    assert table.pinned_view.editTriggers() == QAbstractItemView.NoEditTriggers
    assert table.on_open_qc is None
    assert table.open_qc_button is None
    assert table.empty_state_label.objectName() == "qcResultsEmptyState"
    assert page.findChild(QToolBar, "qcResultsToolbar") is table.action_toolbar
    assert table.table_view.objectName() == "qcResultsTable"
    assert not (table.table_model.flags(table.table_model.index(0, 0)) & Qt.ItemIsEditable)
    assert _visible_toolbar_text(page) == [
        "刷新结果",
        "切换为长表",
        "筛选 (0)",
        "排序 (0)",
        "列显示 (3/3)",
        "查找",
        "导出…",
    ]
    assert table.find_edit.placeholderText() == "搜索 easyqcid"
    assert all(
        label.text().strip() != "质控结果" for label in page.findChildren(QLabel)
    )
    assert not {
        "保存",
        "保存并下一个",
        "启动质控",
        "删除",
    } & {
        button.text().strip()
        for button in page.findChildren(QPushButton)
        if button.isVisible()
    }

    qtbot.mouseClick(table.filter_button, Qt.LeftButton)
    assert table.view_inspector.isVisible()
    assert [
        table.inspector_tabs.tabText(index)
        for index in range(table.inspector_tabs.count())
    ] == ["筛选", "排序", "列显示"]
    assert table.find_identity_exact("SUB003")
    qtbot.waitUntil(lambda: not table._pinned_width_update_pending)


def test_results_page_switches_prepared_services_with_independent_mode_state(
    qtbot,
) -> None:
    page = QtQcResultsPage(_source(), refresh_callback=lambda: True)
    qtbot.addWidget(page)
    wide = TableViewService(_source())
    long = TableViewService(_long_source())

    page.replace_services(wide, long, preserve_state=False)

    assert page.current_mode == "wide"
    assert page.table_workspace.service is wide
    assert page.table_workspace.row_key_columns == ("easyqcid",)
    assert page.mode_action.text() == "切换为长表"
    assert page.mode_action.isEnabled()
    assert page.mode_action.shortcut() != QKeySequence()
    assert page.table_workspace.apply_sort_rules((SortRule("site", False),))
    assert page.table_workspace.apply_column_state(
        replace(
            page.table_workspace.applied_state.columns,
            hidden=("AnatQC.rater1.score1",),
        )
    )
    assert page.table_workspace.set_page_size(50)

    assert page.toggle_result_mode() == "long"

    assert page.current_mode == "long"
    assert page.table_workspace.service is long
    assert page.table_workspace.row_key_columns == (
        "easyqcid",
        "module_name",
        "rater",
    )
    assert page.mode_action.text() == "切换为宽表"
    assert page.table_workspace.applied_state.sort_rules == ()
    assert page.table_workspace.apply_sort_rules((SortRule("module_name", True),))
    assert page.table_workspace.apply_column_state(
        replace(
            page.table_workspace.applied_state.columns,
            hidden=("notes",),
        )
    )
    assert page.table_workspace.set_page_size(100)

    assert page.toggle_result_mode() == "wide"
    assert page.table_workspace.applied_state.sort_rules == (SortRule("site", False),)
    assert page.table_workspace.applied_state.columns.hidden == (
        "AnatQC.rater1.score1",
    )
    assert page.table_workspace.applied_state.page_size == 50

    assert page.toggle_result_mode() == "long"
    assert page.table_workspace.applied_state.sort_rules == (
        SortRule("module_name", True),
    )
    assert page.table_workspace.applied_state.columns.hidden == ("notes",)
    assert page.table_workspace.applied_state.page_size == 100

    refreshed_wide = TableViewService(_source().assign(scanner=["A", "B", "C"]))
    refreshed_long = TableViewService(_long_source().assign(score2=[1, 2, 3]))
    page.replace_services(refreshed_wide, refreshed_long, preserve_state=True)

    assert page.current_mode == "long"
    assert page.table_workspace.service is refreshed_long
    assert page.table_workspace.applied_state.sort_rules == (
        SortRule("module_name", True),
    )
    assert page.table_workspace.applied_state.columns.hidden == ("notes",)
    assert page.toggle_result_mode() == "wide"
    assert page.table_workspace.service is refreshed_wide
    assert page.table_workspace.applied_state.sort_rules == (
        SortRule("site", False),
    )
    assert page.table_workspace.applied_state.columns.hidden == (
        "AnatQC.rater1.score1",
    )
    assert "scanner" in page.table_workspace.applied_state.columns.order

    page.replace_services(wide, long, preserve_state=False)

    assert page.current_mode == "wide"
    assert page.table_workspace.service is wide
    assert page.table_workspace.applied_state.sort_rules == ()
    assert page.table_workspace.applied_state.columns.hidden == ()
    assert page.table_workspace.applied_state.page_size == 25


def test_results_page_export_uses_only_the_visible_long_mode(qtbot, tmp_path) -> None:
    page = QtQcResultsPage(_source(), refresh_callback=lambda: True)
    qtbot.addWidget(page)
    wide = TableViewService(_source())
    long = TableViewService(_long_source())
    page.replace_services(wide, long, preserve_state=False)
    assert page.toggle_result_mode() == "long"
    assert page.table_workspace.apply_sort_rules((SortRule("module_name", False),))
    destination = tmp_path / "long-results.csv"

    assert page.table_workspace.start_export(destination, chunk_size=1)
    qtbot.waitUntil(
        lambda: not page.table_workspace.export_task_controller.busy,
        timeout=3000,
    )

    exported = pd.read_csv(destination)
    assert exported.columns.tolist() == _long_source().columns.tolist()
    assert exported["module_name"].tolist() == ["FuncQC", "AnatQC", "AnatQC"]
    assert len(exported) == 3


def test_legacy_single_service_replacement_always_targets_the_wide_projection(
    qtbot,
) -> None:
    page = QtQcResultsPage(_source(), refresh_callback=lambda: True)
    qtbot.addWidget(page)
    initial_wide = TableViewService(_source())
    long = TableViewService(_long_source())
    page.replace_services(initial_wide, long, preserve_state=False)
    assert page.toggle_result_mode() == "long"
    replacement = TableViewService(_source().assign(scanner=["A", "B", "C"]))

    page.replace_service(replacement, preserve_state=False)

    assert page.current_mode == "wide"
    assert page.wide_service is replacement
    assert page.long_service is long
    assert page.table_workspace.service is replacement

    assert page.toggle_result_mode() == "long"
    newer_wide = TableViewService(_source().assign(session=[1, 2, 3]))
    page.replace_service(newer_wide, preserve_state=True)

    assert page.current_mode == "long"
    assert page.wide_service is newer_wide
    assert page.long_service is long
    assert page.table_workspace.service is long
    assert page.toggle_result_mode() == "wide"
    assert page.table_workspace.service is newer_wide


def test_results_statuses_stay_english_when_state_changes_after_language_switch(
    qtbot,
    tmp_path,
) -> None:
    controller = LanguageController(
        settings=QSettings(str(tmp_path / "language.ini"), QSettings.IniFormat)
    )
    page = QtQcResultsPage(
        _source(),
        refresh_callback=lambda: True,
        language=controller,
    )
    qtbot.addWidget(page)
    controller.register_root(page)
    page.replace_services(
        TableViewService(_source()),
        TableViewService(_long_source()),
        preserve_state=False,
    )
    controller.set_language("en")
    table = page.table_workspace

    table.begin_filter_edit()
    table.set_filter_draft(
        (FilterCondition("site", "==", "A", "site-a"),)
    )
    assert table.apply_filter_draft()
    assert table.apply_sort_rules((SortRule("easyqcid", False),))
    assert table.select_source_position(0)

    assert table.filter_action.text() == "Filter (1)"
    assert table.sort_action.text() == "Sort (1)"
    assert table.columns_action.text() == "Columns (3/3)"
    assert table.count_label.text() == "2 / 3 rows"
    assert table.range_label.text() == "Rows 1–2"
    assert table.columns_status_label.text() == "Columns 3/3"
    assert table.selection_status_label.text() == "Selected source row 1"
    assert page.mode_action.text() == "Switch to long view"
    assert page.toggle_result_mode() == "long"
    assert page.mode_action.text() == "Switch to wide view"


def test_results_page_refresh_empty_and_error_states_are_explicit(qtbot) -> None:
    refresh_calls = []
    page = QtQcResultsPage(
        pd.DataFrame(columns=["easyqcid"]),
        refresh_callback=lambda: refresh_calls.append(True) or True,
    )
    qtbot.addWidget(page)
    page.show()

    assert page.table_workspace.empty_state_label.isVisible()
    assert "没有可显示" in page.table_workspace.empty_state_label.text()
    qtbot.mouseClick(page.refresh_button, Qt.LeftButton)
    assert refresh_calls == [True]

    page.show_error("rating aggregation unavailable")
    assert page.error_label.isVisible()
    assert page.error_label.text() == "rating aggregation unavailable"
    page.set_refresh_busy(True)
    assert not page.refresh_action.isEnabled()
    assert page.refresh_action.text() == "刷新中…"
    page.replace_service(TableViewService(_source()), preserve_state=False)

    assert not page.error_label.isVisible()
    assert not page.table_workspace.empty_state_label.isVisible()
    assert page.refresh_action.isEnabled()
    assert page.refresh_action.text() == "刷新结果"
    qtbot.waitUntil(
        lambda: not page.table_workspace._pinned_width_update_pending
    )


def test_results_page_export_delegates_to_shared_core_export(qtbot, tmp_path) -> None:
    page = QtQcResultsPage(_source(), refresh_callback=lambda: True)
    qtbot.addWidget(page)
    destination = tmp_path / "qc-results.csv"

    assert page.table_workspace.start_export(destination, chunk_size=2)
    qtbot.waitUntil(
        lambda: not page.table_workspace.export_task_controller.busy,
        timeout=3000,
    )

    receipt = page.table_workspace.last_export_receipt
    assert receipt is not None
    assert receipt.destination == destination
    assert receipt.rows == 3
    exported = pd.read_csv(destination)
    assert exported["easyqcid"].tolist() == ["SUB001", "SUB002", "SUB003"]


def test_results_page_close_owns_shared_workspace_lifecycle(
    qtbot,
    monkeypatch,
) -> None:
    page = QtQcResultsPage(_source(), refresh_callback=lambda: True)
    qtbot.addWidget(page)
    page.show()
    qtbot.waitUntil(
        lambda: not page.table_workspace._pinned_width_update_pending
    )
    cancel_calls = []
    monkeypatch.setattr(
        page.table_workspace.task_controller,
        "cancel",
        lambda: cancel_calls.append("view"),
    )
    monkeypatch.setattr(
        page.table_workspace.export_task_controller,
        "cancel",
        lambda: cancel_calls.append("export"),
    )

    assert page.close()
    assert cancel_calls == ["view", "export"]
