"""Qt-specific Table workspace interaction tests."""

from __future__ import annotations

from dataclasses import replace

import pandas as pd
from pandas.testing import assert_frame_equal
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QToolBar,
    QToolButton,
)

from core.table_view_service import TableViewError, TableViewService
from gui_qt.table_workspace import QtTableWorkspace
from models.table_view_state import (
    ColumnViewState,
    FilterCondition,
    FilterExpression,
    FilterGroup,
    SortRule,
)


def _source() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003", "SUB004", "SUB005"],
            "site": ["A", "B", "A", "B", "A"],
            "age": [29, 31, 27, 30, 35],
            "passed": [True, False, True, False, True],
        }
    )


def _full_result_frame(workspace: QtTableWorkspace) -> pd.DataFrame:
    return workspace.service.get_window(
        workspace.result,
        0,
        max(1, workspace.result.matched_total),
    ).dataframe


def test_table_uses_standard_overflow_toolbars_without_fixed_chip_geometry(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.resize(360, 560)
    workspace.show()
    qtbot.waitUntil(lambda: workspace.width() == 360)

    action_toolbar = workspace.findChild(QToolBar, "tableToolbar")
    applied_toolbar = workspace.findChild(QToolBar, "appliedFilterChips")
    extension = action_toolbar.findChild(QToolButton, "qt_toolbar_ext_button")

    assert action_toolbar is workspace.action_toolbar
    assert applied_toolbar is workspace.applied_toolbar
    assert extension is not None and extension.isVisible()
    assert action_toolbar.isMovable() is False
    assert applied_toolbar.isMovable() is False
    assert applied_toolbar.minimumHeight() < applied_toolbar.maximumHeight()
    assert workspace.findChild(QScrollArea, "appliedFilterChips") is None
    assert workspace.findChild(QSplitter, "tableWorkspaceSplitter") is None
    assert workspace.findChild(QTabWidget, "tableInspector") is None
    assert len(workspace.critical_shortcuts) == len(workspace.critical_actions)
    assert all(shortcut.key().toString() for shortcut in workspace.critical_shortcuts)


def test_applied_filter_actions_use_toolbar_overflow_and_remain_removable(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.resize(300, 560)
    workspace.show()
    workspace.begin_filter_edit()
    workspace.set_filter_draft(
        (
            FilterCondition("site", "==", "A", "site-a"),
            FilterCondition("age", ">", 25, "age-over-25"),
            FilterCondition("passed", "==", True, "passed-only"),
        )
    )
    assert workspace.apply_filter_draft()
    qtbot.waitUntil(lambda: workspace.applied_toolbar.isVisible())

    extension = workspace.applied_toolbar.findChild(
        QToolButton,
        "qt_toolbar_ext_button",
    )
    assert extension is not None and extension.isVisible()
    assert [action.data() for action in workspace.applied_toolbar.actions()] == [
        "site-a",
        "age-over-25",
        "passed-only",
    ]

    workspace.applied_toolbar.actions()[0].trigger()

    assert all(
        condition.condition_id != "site-a"
        for condition in workspace.applied_state.conditions
    )
    assert workspace.filter_count == 2


def test_multi_pinned_width_uses_all_sections_and_caps_at_45_percent(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.resize(640, 560)
    workspace.show()
    assert workspace.apply_column_state(
        ColumnViewState(
            order=("ezqcid", "age", "site", "passed"),
            widths=(("ezqcid", 260), ("age", 240), ("site", 132), ("passed", 132)),
            pinned=("ezqcid", "age"),
        )
    )
    qtbot.waitUntil(lambda: workspace.pinned_view.horizontalScrollBar().maximum() > 0)

    surface_width = workspace.table_surface.contentsRect().width()
    cap = int(surface_width * workspace.PINNED_SURFACE_FRACTION)
    expected_content = (
        workspace.pinned_view.horizontalHeader().sectionSize(0)
        + workspace.pinned_view.horizontalHeader().sectionSize(1)
        + workspace.pinned_view.verticalHeader().width()
        + 2 * workspace.pinned_view.frameWidth()
    )

    assert workspace._pinned_content_width() == expected_content
    assert workspace.pinned_view.width() == min(expected_content, cap)
    assert workspace.pinned_view.width() <= cap
    assert (
        workspace.pinned_view.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert workspace.pinned_view.horizontalScrollBar().maximum() > 0


def test_pinned_width_recomputes_after_resize_and_section_change(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.resize(1200, 560)
    workspace.show()
    assert workspace.apply_column_state(
        ColumnViewState(
            order=("ezqcid", "age", "site", "passed"),
            widths=(("ezqcid", 220), ("age", 200)),
            pinned=("ezqcid", "age"),
        )
    )
    qtbot.waitUntil(
        lambda: workspace.pinned_view.width() == workspace._pinned_content_width()
    )
    expanded_width = workspace.pinned_view.width()

    workspace.resize(520, 560)
    qtbot.waitUntil(lambda: workspace.pinned_view.width() < expanded_width)
    assert workspace.pinned_view.width() <= int(
        workspace.table_surface.contentsRect().width()
        * workspace.PINNED_SURFACE_FRACTION
    )

    before_section_resize = workspace._pinned_content_width()
    workspace.pinned_view.setColumnWidth(1, 300)
    qtbot.waitUntil(
        lambda: workspace._pinned_content_width() > before_section_resize
    )
    assert workspace.pinned_view.width() <= int(
        workspace.table_surface.contentsRect().width()
        * workspace.PINNED_SURFACE_FRACTION
    )


def test_font_style_and_screen_events_schedule_pinned_width_recompute(
    qtbot,
    monkeypatch,
):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.show()
    events = []
    monkeypatch.setattr(
        workspace,
        "_schedule_pinned_width_update",
        lambda: events.append("scheduled"),
    )

    for event_type in (
        QEvent.Type.FontChange,
        QEvent.Type.StyleChange,
        QEvent.Type.ScreenChangeInternal,
    ):
        QCoreApplication.sendEvent(workspace, QEvent(event_type))

    assert events == ["scheduled", "scheduled", "scheduled"]


def test_narrow_toolbar_actions_are_keyboard_reachable(qtbot):
    opened = []
    workspace = QtTableWorkspace(_source(), on_open_qc=opened.append)
    qtbot.addWidget(workspace)
    workspace.resize(360, 560)
    workspace.show()
    qtbot.waitUntil(lambda: workspace.width() == 360)
    workspace.activateWindow()
    workspace.table_view.setFocus()
    qtbot.waitUntil(workspace.table_view.hasFocus)

    qtbot.keyClick(
        workspace.table_view,
        Qt.Key.Key_F,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert "exact ezqcid" in workspace.error_text

    qtbot.keyClick(
        workspace.table_view,
        Qt.Key.Key_F,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert workspace.filter_dialog is not None and workspace.filter_dialog.isVisible()
    workspace.filter_dialog.reject()

    qtbot.keyClick(
        workspace.table_view,
        Qt.Key.Key_S,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert workspace.sort_dialog is not None and workspace.sort_dialog.isVisible()
    workspace.sort_dialog.reject()

    qtbot.keyClick(
        workspace.table_view,
        Qt.Key.Key_C,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert workspace.columns_dialog is not None and workspace.columns_dialog.isVisible()
    workspace.columns_dialog.reject()

    assert workspace.select_source_position(0)
    qtbot.keyClick(
        workspace.table_view,
        Qt.Key.Key_Return,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert opened == ["SUB001"]


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
    valid_ids = _full_result_frame(workspace)["ezqcid"].tolist()
    assert valid_ids == ["SUB001", "SUB003", "SUB005"]
    assert workspace.filter_count == 1
    assert workspace.applied_chip_texts == ("site is A",)

    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("age", ">", "not-a-number", "bad"),))
    assert not workspace.apply_filter_draft()
    assert workspace.applied_state.revision == valid_revision
    assert _full_result_frame(workspace)["ezqcid"].tolist() == valid_ids
    assert "数值" in workspace.error_text
    assert_frame_equal(source, original)


def test_filter_dialog_cancel_reset_apply_and_inline_error_are_transactional(qtbot):
    source = _source()
    original = source.copy(deep=True)
    workspace = QtTableWorkspace(source, page_size=2)
    qtbot.addWidget(workspace)
    initial_state = workspace.applied_state
    initial_result = workspace.result

    dialog = workspace.open_filter_dialog()
    assert workspace.draft_state is None
    reset_draft = FilterExpression(
        "all",
        (
            FilterGroup(
                "temporary",
                "all",
                (FilterCondition("site", "==", "B", "temporary-site"),),
            ),
        ),
    )
    dialog.editor.set_expression(reset_draft)
    assert workspace.draft_state is None
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Reset),
        Qt.MouseButton.LeftButton,
    )
    assert dialog.editor.expression() == FilterExpression()
    assert workspace.applied_state is initial_state
    assert workspace.result is initial_result
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Cancel),
        Qt.MouseButton.LeftButton,
    )
    assert workspace.applied_state is initial_state
    assert workspace.result is initial_result

    dialog = workspace.open_filter_dialog()
    dialog.editor.set_expression(reset_draft)
    dialog.close()
    assert workspace.filter_dialog is None
    assert workspace.applied_state is initial_state
    assert workspace.result is initial_result

    valid = FilterExpression(
        "any",
        (
            FilterGroup(
                "site-a",
                "all",
                (FilterCondition("site", "==", "A", "site-is-a"),),
            ),
            FilterGroup(
                "older",
                "all",
                (FilterCondition("age", ">", 32, "age-over-32"),),
            ),
        ),
    )
    dialog = workspace.open_filter_dialog()
    dialog.editor.set_expression(valid)
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )
    assert workspace.applied_state.revision == initial_state.revision + 1
    assert workspace.applied_state.filter == valid
    assert _full_result_frame(workspace)["ezqcid"].tolist() == [
        "SUB001",
        "SUB003",
        "SUB005",
    ]

    before_state = workspace.applied_state
    before_result = workspace.result
    invalid = FilterExpression(
        "all",
        (
            FilterGroup(
                "invalid-values",
                "all",
                (FilterCondition("age", ">", "not-a-number", "bad-age"),),
            ),
        ),
    )
    dialog = workspace.open_filter_dialog()
    dialog.editor.set_expression(invalid)
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )
    row = dialog.editor.group_editors[0].condition_rows[0]
    assert dialog.isVisible()
    assert "数值" in row.error_text
    assert workspace.applied_state is before_state
    assert workspace.result is before_result
    assert_frame_equal(source, original)


def test_source_replacement_rejects_an_open_filter_draft(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    dialog = workspace.open_filter_dialog()
    dialog.editor.set_expression(
        FilterExpression(
            "all",
            (
                FilterGroup(
                    "temporary",
                    "all",
                    (FilterCondition("site", "==", "B", "temporary-site"),),
                ),
            ),
        )
    )

    refreshed = _source().assign(extra=[1, 2, 3, 4, 5])
    workspace.replace_service(TableViewService(refreshed), preserve_state=True)

    assert workspace.filter_dialog is None
    assert dialog.result() == dialog.DialogCode.Rejected
    assert workspace.draft_state is None
    assert workspace.applied_state.conditions == ()
    assert workspace.result.matched_total == len(refreshed)


def test_source_replacement_preserves_compatible_grouped_filter_semantics(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    expression = FilterExpression(
        "any",
        (
            FilterGroup(
                "site-a",
                "all",
                (FilterCondition("site", "==", "A", "site-is-a"),),
            ),
            FilterGroup(
                "older",
                "all",
                (FilterCondition("age", ">", 32, "age-over-32"),),
            ),
        ),
    )
    dialog = workspace.open_filter_dialog()
    dialog.editor.set_expression(expression)
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )

    refreshed = _source().drop(columns="age")
    workspace.replace_service(TableViewService(refreshed), preserve_state=True)

    assert workspace.applied_state.filter == FilterExpression(
        "any",
        (
            FilterGroup(
                "site-a",
                "all",
                (FilterCondition("site", "==", "A", "site-is-a"),),
            ),
        ),
    )
    assert _full_result_frame(workspace)["ezqcid"].tolist() == [
        "SUB001",
        "SUB003",
        "SUB005",
    ]


def test_filter_dialog_background_failure_preserves_applied_view(qtbot, monkeypatch):
    workspace = QtTableWorkspace(_source(), background_row_threshold=1)
    qtbot.addWidget(workspace)
    before_state = workspace.applied_state
    before_result = workspace.result

    def fail_query(_state):
        raise TableViewError("synthetic filter background failure")

    monkeypatch.setattr(workspace.service, "apply_state", fail_query)
    dialog = workspace.open_filter_dialog()
    dialog.editor.set_expression(
        FilterExpression(
            "all",
            (
                FilterGroup(
                    "site-a",
                    "all",
                    (FilterCondition("site", "==", "A", "site-is-a"),),
                ),
            ),
        )
    )
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )
    qtbot.waitUntil(lambda: bool(workspace.error_text), timeout=2000)
    qtbot.waitUntil(lambda: not workspace.task_controller.busy, timeout=2000)

    assert workspace.filter_dialog is None
    assert workspace.applied_state is before_state
    assert workspace.result is before_result
    assert "synthetic filter background failure" in workspace.error_text


def test_filter_button_opens_nonblocking_dialog_and_group_chip_removal_commits_once(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.show()

    qtbot.mouseClick(workspace.filter_button, Qt.MouseButton.LeftButton)
    dialog = workspace.filter_dialog
    assert dialog is not None
    assert dialog.isVisible()
    assert workspace.findChild(QTabWidget, "tableInspector") is None

    expression = FilterExpression(
        "all",
        (
            FilterGroup(
                "site",
                "all",
                (FilterCondition("site", "==", "A", "site-a"),),
            ),
            FilterGroup(
                "age",
                "all",
                (FilterCondition("age", ">", 28, "age-28"),),
            ),
        ),
    )
    dialog.editor.set_expression(expression)
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )
    revision = workspace.applied_state.revision

    assert workspace.remove_applied_condition("site-a")
    assert workspace.applied_state.revision == revision + 1
    assert workspace.applied_state.filter == FilterExpression(
        "all",
        (
            FilterGroup(
                "age",
                "all",
                (FilterCondition("age", ">", 28.0, "age-28"),),
            ),
        ),
    )


def test_multi_sort_header_state_and_column_layout_are_applied(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    rules = (SortRule("site", True), SortRule("age", False))
    workspace.table_view.setColumnWidth(1, 211)

    assert workspace.apply_sort_rules(rules)
    assert _full_result_frame(workspace)["ezqcid"].tolist() == [
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
    assert workspace.applied_state.columns.width_for("site") == 211


def test_sort_dialog_cancel_duplicate_and_apply_are_transactional(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.show()
    initial_state = workspace.applied_state
    initial_result = workspace.result

    qtbot.mouseClick(workspace.sort_button, Qt.MouseButton.LeftButton)
    dialog = workspace.sort_dialog
    assert dialog is not None and dialog.isVisible()
    dialog.editor.set_rules((SortRule("site", True),))
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Reset),
        Qt.MouseButton.LeftButton,
    )
    assert dialog.editor.rules() == ()
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Cancel),
        Qt.MouseButton.LeftButton,
    )
    assert workspace.applied_state is initial_state
    assert workspace.result is initial_result

    dialog = workspace.open_sort_dialog()
    dialog.editor.set_rules((SortRule("site", True), SortRule("site", False)))
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )
    assert dialog.isVisible()
    assert workspace.applied_state is initial_state
    assert workspace.result is initial_result

    dialog.editor.set_rules((SortRule("site", True), SortRule("age", False)))
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )
    assert workspace.sort_dialog is None
    assert workspace.applied_state.revision == initial_state.revision + 1
    assert workspace.applied_state.sort_rules == (
        SortRule("site", True),
        SortRule("age", False),
    )
    assert _full_result_frame(workspace)["ezqcid"].tolist() == [
        "SUB005",
        "SUB001",
        "SUB003",
        "SUB002",
        "SUB004",
    ]


def test_columns_dialog_cancel_reset_and_apply_are_transactional(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.show()
    initial_state = workspace.applied_state
    initial_result = workspace.result

    dialog = workspace.open_columns_dialog()
    modified = ColumnViewState(
        order=("ezqcid", "age", "site", "passed"),
        hidden=("passed",),
        pinned=("ezqcid", "age"),
    )
    dialog.editor.set_state(modified)
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Reset),
        Qt.MouseButton.LeftButton,
    )
    assert dialog.editor.state().order == workspace.initial_state.columns.order
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Cancel),
        Qt.MouseButton.LeftButton,
    )
    assert workspace.applied_state is initial_state
    assert workspace.result is initial_result

    dialog = workspace.open_columns_dialog()
    dialog.editor.set_state(modified)
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )
    assert workspace.columns_dialog is None
    assert workspace.applied_state.revision == initial_state.revision + 1
    assert workspace.applied_state.columns.order == modified.order
    assert workspace.applied_state.columns.hidden == modified.hidden
    assert workspace.applied_state.columns.pinned == modified.pinned


def test_sort_and_columns_buttons_use_dialogs_without_permanent_inspector(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.show()

    assert workspace.findChild(QTabWidget, "tableInspector") is None
    qtbot.mouseClick(workspace.sort_button, Qt.MouseButton.LeftButton)
    assert workspace.sort_dialog is not None
    workspace.sort_dialog.reject()
    qtbot.mouseClick(workspace.columns_button, Qt.MouseButton.LeftButton)
    assert workspace.columns_dialog is not None


def test_source_replacement_rejects_open_sort_and_columns_drafts(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    sort_dialog = workspace.open_sort_dialog()
    sort_dialog.editor.set_rules((SortRule("age", False),))

    workspace.replace_service(TableViewService(_source()), preserve_state=True)
    assert workspace.sort_dialog is None
    assert sort_dialog.result() == sort_dialog.DialogCode.Rejected

    columns_dialog = workspace.open_columns_dialog()
    columns_dialog.editor.set_state(
        ColumnViewState(
            order=("ezqcid", "age", "site", "passed"),
            pinned=("ezqcid",),
        )
    )
    workspace.replace_service(TableViewService(_source()), preserve_state=True)
    assert workspace.columns_dialog is None
    assert columns_dialog.result() == columns_dialog.DialogCode.Rejected


def test_header_click_and_shift_click_keep_the_fast_sort_path(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.resize(900, 600)
    workspace.show()
    header = workspace.table_view.horizontalHeader()

    site_position = header.sectionViewportPosition(1) + 8
    age_position = header.sectionViewportPosition(2) + 8
    qtbot.mouseClick(
        header.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        header.viewport().rect().topLeft() + QPoint(site_position, 8),
    )
    qtbot.mouseClick(
        header.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        header.viewport().rect().topLeft() + QPoint(age_position, 8),
    )

    assert workspace.applied_state.sort_rules == (
        SortRule("site", True),
        SortRule("age", True),
    )


def test_sort_dialog_background_failure_preserves_applied_view(qtbot, monkeypatch):
    workspace = QtTableWorkspace(_source(), background_row_threshold=1)
    qtbot.addWidget(workspace)
    before_state = workspace.applied_state
    before_result = workspace.result

    def fail_query(_state):
        raise TableViewError("synthetic sort dialog background failure")

    monkeypatch.setattr(workspace.service, "apply_state", fail_query)
    dialog = workspace.open_sort_dialog()
    dialog.editor.set_rules((SortRule("age", False),))
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )
    qtbot.waitUntil(lambda: bool(workspace.error_text), timeout=2000)
    qtbot.waitUntil(lambda: not workspace.task_controller.busy, timeout=2000)

    assert workspace.sort_dialog is None
    assert workspace.applied_state is before_state
    assert workspace.result is before_result
    assert "synthetic sort dialog background failure" in workspace.error_text


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
    qtbot.waitUntil(lambda: not workspace.task_controller.busy, timeout=2000)

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
    assert workspace.apply_column_state(
        ColumnViewState(
            order=("ezqcid", "age", "site", "passed"),
            hidden=("passed",),
            pinned=("ezqcid", "age"),
        )
    )
    assert workspace.find_identity_exact("SUB003")

    refreshed = _source().copy()
    refreshed["AnatQC.rater1.score1"] = ["Good", None, "Fair", None, "Poor"]
    workspace.replace_service(TableViewService(refreshed), preserve_state=True)

    assert workspace.applied_state.conditions == (
        FilterCondition("site", "==", "A", "site-a"),
    )
    assert workspace.applied_state.sort_rules == (SortRule("age", False),)
    assert workspace.applied_state.columns.pinned == ("ezqcid", "age")
    assert workspace.applied_state.columns.hidden == ("passed",)
    assert workspace.applied_state.columns.order[:2] == ("ezqcid", "age")
    assert _full_result_frame(workspace)["ezqcid"].tolist() == [
        "SUB005",
        "SUB001",
        "SUB003",
    ]
    assert "AnatQC.rater1.score1" in workspace.applied_state.columns.order
    assert workspace.table_model.row_reference(2).ezqcid == "SUB003"
