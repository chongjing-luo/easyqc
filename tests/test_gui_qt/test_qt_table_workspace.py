"""Qt-specific Table workspace interaction tests."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
from pandas.testing import assert_frame_equal
from PySide6.QtCore import Qt

from core.table_view_service import TableViewError, TableViewService
from gui_qt.table_workspace import QtTableWorkspace
from models.table_view_state import ColumnViewState, FilterCondition, SortRule


def _source() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003", "SUB004", "SUB005"],
            "site": ["A", "B", "A", "B", "A"],
            "age": [29, 31, 27, 30, 35],
            "passed": [True, False, True, False, True],
        }
    )


def test_filter_draft_cancel_apply_and_invalid_input_preserve_last_result(qtbot):
    source = _source()
    original = source.copy(deep=True)
    workspace = QtTableWorkspace(source, page_size=2)
    qtbot.addWidget(workspace)

    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("site", "==", "A", "site-a"),))
    workspace.cancel_filter_draft()
    assert workspace.applied_state.conditions == ()

    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("site", "==", "A", "site-a"),))
    assert workspace.apply_filter_draft()
    valid_revision = workspace.applied_state.revision
    valid_ids = workspace.result.dataframe["ezqcid"].tolist()
    assert valid_ids == ["SUB001", "SUB003", "SUB005"]
    assert workspace.filter_count == 1
    assert workspace.applied_chip_texts == ("site is A",)

    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("age", ">", "not-a-number", "bad"),))
    assert not workspace.apply_filter_draft()
    assert workspace.applied_state.revision == valid_revision
    assert workspace.result.dataframe["ezqcid"].tolist() == valid_ids
    assert "数值" in workspace.filter_panel.error_text
    assert_frame_equal(source, original)


def test_multi_sort_header_state_and_column_layout_are_applied(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    rules = (SortRule("site", True), SortRule("age", False))
    workspace.table_view.setColumnWidth(1, 211)

    assert workspace.apply_sort_rules(rules)
    assert workspace.result.dataframe["ezqcid"].tolist() == [
        "SUB005",
        "SUB001",
        "SUB003",
        "SUB002",
        "SUB004",
    ]
    assert "1 site ↑" in workspace.sort_status_label.text()
    assert "2 age ↓" in workspace.sort_status_label.text()
    assert workspace.table_view.horizontalHeader().sortIndicatorSection() == 1
    assert workspace.table_model.headerData(1, Qt.Horizontal, Qt.ToolTipRole) == "Sort priority 1 · ascending"
    assert workspace.applied_state.columns.width_for("site") == 211

    columns = ColumnViewState(
        order=("ezqcid", "age", "site", "passed"),
        hidden=("passed",),
        pinned=("ezqcid",),
    )
    assert workspace.apply_column_state(columns)
    assert tuple(workspace.table_model.snapshot().columns) == ("ezqcid", "age", "site")
    assert workspace.pinned_view is not None
    assert workspace.pinned_view.isColumnHidden(0) is False
    assert workspace.table_view.isColumnHidden(0) is True


def test_ezqcid_cannot_be_hidden_or_unpinned(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    before = workspace.applied_state

    hidden = replace(before.columns, hidden=("ezqcid",))
    assert not workspace.apply_column_state(hidden)
    assert workspace.applied_state == before
    assert "ezqcid" in workspace.error_text

    unpinned = replace(before.columns, pinned=())
    assert not workspace.apply_column_state(unpinned)
    assert workspace.applied_state == before


def test_paging_exact_find_and_selection_restore_use_full_result(qtbot):
    workspace = QtTableWorkspace(_source(), page_size=2)
    qtbot.addWidget(workspace)

    assert workspace.visible_range == (1, 2)
    assert workspace.next_page()
    assert workspace.visible_range == (3, 4)
    assert workspace.find_identity_exact("SUB005")
    assert workspace.page_offset == 4
    assert workspace.selected_source_position == 4
    assert workspace.table_view.selectionModel().selectedRows()

    assert workspace.apply_sort_rules((SortRule("age", False),))
    assert workspace.selected_source_position == 4
    assert workspace.table_model.row_reference(0).ezqcid == "SUB005"
    assert workspace.table_view.selectionModel().selectedRows()[0].row() == 0
    assert "5 / 5" in workspace.count_label.text()


def test_filtered_out_selection_never_moves_to_another_subject(qtbot):
    workspace = QtTableWorkspace(_source(), page_size=5)
    qtbot.addWidget(workspace)
    assert workspace.select_source_position(1)
    assert workspace.table_model.row_reference(1).ezqcid == "SUB002"

    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("site", "==", "A", "site-a"),))
    assert workspace.apply_filter_draft()

    assert workspace.selected_source_position == 1
    assert workspace.selection_outside_view
    assert workspace.table_view.selectionModel().selectedRows() == []
    assert not workspace.open_selected_qc()


def test_open_qc_accepts_only_current_unique_nonblank_identity(qtbot):
    opened: list[str] = []
    workspace = QtTableWorkspace(_source(), on_open_qc=opened.append)
    qtbot.addWidget(workspace)
    assert workspace.select_source_position(2)
    old_reference = workspace.table_model.row_reference(2)

    assert workspace.open_selected_qc()
    assert opened == ["SUB003"]

    assert workspace.apply_sort_rules((SortRule("age", False),))
    assert not workspace.open_qc_reference(old_reference)
    assert opened == ["SUB003"]
    assert "失效" in workspace.error_text


def test_open_qc_blocks_blank_and_duplicate_ezqcid(qtbot):
    for source, expected in (
        (pd.DataFrame({"ezqcid": ["  "], "value": [1]}), "为空"),
        (pd.DataFrame({"ezqcid": ["SUB001", "SUB001"], "value": [1, 2]}), "不唯一"),
    ):
        opened: list[str] = []
        workspace = QtTableWorkspace(source, on_open_qc=opened.append)
        qtbot.addWidget(workspace)
        assert workspace.select_source_position(0)

        assert not workspace.open_selected_qc()
        assert expected in workspace.error_text
        assert opened == []


def test_large_table_apply_runs_revision_safe_background_query(qtbot):
    workspace = QtTableWorkspace(
        _source(),
        page_size=2,
        background_row_threshold=1,
    )
    qtbot.addWidget(workspace)
    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("site", "==", "A", "site-a"),))

    assert workspace.apply_filter_draft()
    qtbot.waitUntil(lambda: workspace.result.matched_total == 3, timeout=2000)

    assert workspace.applied_state.conditions[0].value == "A"
    assert not workspace.task_controller.busy
    assert "3 / 5" in workspace.count_label.text()


def test_large_table_validation_error_stays_synchronous_and_preserves_result(qtbot):
    workspace = QtTableWorkspace(_source(), background_row_threshold=1)
    qtbot.addWidget(workspace)
    before_state = workspace.applied_state
    before_result = workspace.result
    invalid_columns = replace(before_state.columns, pinned=())

    assert not workspace.apply_column_state(invalid_columns)

    assert workspace.applied_state == before_state
    assert workspace.result is before_result
    assert not workspace.task_controller.busy
    assert "ezqcid" in workspace.error_text


def test_large_table_background_error_preserves_last_applied_result(qtbot, monkeypatch):
    workspace = QtTableWorkspace(_source(), background_row_threshold=1)
    qtbot.addWidget(workspace)
    before_state = workspace.applied_state
    before_result = workspace.result

    def fail_query(_state):
        raise TableViewError("synthetic background failure")

    monkeypatch.setattr(workspace.service, "apply_state", fail_query)

    assert workspace.apply_sort_rules((SortRule("age", False),))
    assert workspace.task_controller.busy
    assert "Applying" in workspace.count_label.text()
    qtbot.waitUntil(lambda: bool(workspace.error_text), timeout=2000)
    qtbot.waitUntil(lambda: not workspace.task_controller.busy, timeout=2000)

    assert workspace.applied_state == before_state
    assert workspace.result is before_result
    assert "synthetic background failure" in workspace.error_text


def test_small_table_query_error_preserves_last_applied_result(qtbot, monkeypatch):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    before_state = workspace.applied_state
    before_result = workspace.result

    def fail_query(_state):
        raise TableViewError("synthetic synchronous failure")

    monkeypatch.setattr(workspace.service, "apply_state", fail_query)

    assert not workspace.apply_sort_rules((SortRule("age", False),))
    assert workspace.applied_state == before_state
    assert workspace.result is before_result
    assert "synthetic synchronous failure" in workspace.error_text


def test_prepared_source_replacement_preserves_compatible_view_and_identity(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("site", "==", "A", "site-a"),))
    assert workspace.apply_filter_draft()
    assert workspace.apply_sort_rules((SortRule("age", False),))
    assert workspace.find_identity_exact("SUB003")

    refreshed = _source().copy()
    refreshed["AnatQC.rater1.score1"] = ["Good", None, "Fair", None, "Poor"]
    workspace.replace_service(TableViewService(refreshed), preserve_state=True)

    assert workspace.applied_state.conditions == (
        FilterCondition("site", "==", "A", "site-a"),
    )
    assert workspace.applied_state.sort_rules == (SortRule("age", False),)
    assert workspace.result.dataframe["ezqcid"].tolist() == [
        "SUB005",
        "SUB001",
        "SUB003",
    ]
    assert "AnatQC.rater1.score1" in workspace.applied_state.columns.order
    assert workspace.table_model.row_reference(2).ezqcid == "SUB003"
