from dataclasses import replace
import tkinter as tk

import pandas as pd
import pytest

from gui.table_workspace import TableWorkspace
from models.table_view_state import FilterCondition


@pytest.fixture
def tk_root():
    root = tk.Tk()
    root.geometry("640x480")
    root.update_idletasks()
    yield root
    for child in root.winfo_children():
        if child.winfo_exists():
            child.destroy()
    root.destroy()


@pytest.fixture
def source_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003", "SUB004", "SUB005"],
            "site": ["A", "B", "A", "B", "A"],
            "score": [3.0, 1.0, 2.0, 4.0, None],
            "approved": [True, False, True, False, True],
        }
    )


def _workspace(tk_root, source_frame, **kwargs) -> TableWorkspace:
    workspace = TableWorkspace(tk_root, source_frame, **kwargs)
    workspace.window.update()
    return workspace


def test_workspace_is_resizable_non_modal_and_keeps_table_counts_and_inspector_visible(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame, title="QC records")

    assert workspace.window.title() == "QC records"
    assert workspace.window.resizable() == (1, 1)
    assert workspace.window.grab_current() is None
    assert workspace.table.winfo_ismapped()
    assert workspace.inspector.winfo_ismapped()
    assert workspace.applied_state.revision == 0
    assert workspace.result.matched_total == 5
    assert "5" in workspace.row_count_var.get()
    assert "4" in workspace.column_count_var.get()
    assert workspace.open_qc_button.instate(["disabled"])


def test_filter_cancel_is_a_byte_for_byte_noop_for_applied_state_and_result(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)
    state_before = workspace.applied_state
    positions_before = workspace.result.source_positions
    rows_before = workspace.result.dataframe.copy(deep=True)
    counts_before = (workspace.result.matched_total, workspace.result.source_total)

    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("site", "==", "A"),))
    workspace.cancel_filter_draft()

    assert workspace.applied_state == state_before
    assert workspace.applied_state is state_before
    assert workspace.result.source_positions == positions_before
    pd.testing.assert_frame_equal(workspace.result.dataframe, rows_before)
    assert (workspace.result.matched_total, workspace.result.source_total) == counts_before
    assert workspace.draft_state is None


def test_valid_filter_apply_commits_once_updates_counts_and_applied_chips(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)
    workspace.begin_filter_edit()
    workspace.set_filter_draft(
        (FilterCondition("site", "==", "A", condition_id="site-a"),)
    )

    assert workspace.apply_filter_draft() is True

    assert workspace.applied_state.revision == 1
    assert workspace.result.matched_total == 3
    assert workspace.result.source_positions == (0, 2, 4)
    assert workspace.page_offset == 0
    assert workspace.draft_state is None
    assert len(workspace.chip_texts) == 1
    assert "site" in workspace.chip_texts[0]
    assert "A" in workspace.chip_texts[0]


def test_visible_filter_inspector_is_immediately_editable_and_reusable_without_toolbar_click(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)
    assert workspace.draft_state is not None
    assert len(workspace.filter_rows) == 1
    row = workspace.filter_rows[0]
    row.column_var.set("site")
    row.refresh_for_column()
    row.value_var.set("A")

    assert workspace.apply_filter_draft() is True
    assert workspace.result.dataframe["site"].tolist() == ["A", "A", "A"]
    assert workspace.draft_state is None

    row.value_var.set("B")
    assert workspace.apply_filter_draft() is True
    assert workspace.result.dataframe["site"].tolist() == ["B", "B"]


def test_filter_condition_value_control_fits_inside_inspector(tk_root, source_frame) -> None:
    workspace = _workspace(tk_root, source_frame)
    row = workspace.filter_rows[0]
    workspace.window.update_idletasks()

    assert row.value_host.winfo_ismapped()
    assert row.value_host.winfo_width() > 100
    assert row.value_host.winfo_x() + row.value_host.winfo_width() <= row.winfo_width()


def test_essential_toolbar_controls_remain_visible_at_minimum_window_width(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)
    workspace.window.geometry("820x520")
    workspace.window.update()

    for widget in (
        workspace.filter_button,
        workspace.sort_button,
        workspace.columns_button,
        workspace.undo_button,
        workspace.reset_button,
        workspace.find_entry,
        workspace.density_combo,
        workspace.open_qc_button,
    ):
        assert widget.winfo_ismapped()
        assert widget.winfo_x() + widget.winfo_width() <= workspace.toolbar.winfo_width()


def test_applied_chip_removes_exactly_one_condition_as_a_committed_action(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)
    workspace.begin_filter_edit()
    workspace.set_filter_draft(
        (
            FilterCondition("site", "==", "A", condition_id="site-a"),
            FilterCondition("score", ">=", "2", condition_id="score-2"),
        )
    )
    assert workspace.apply_filter_draft() is True
    revision = workspace.applied_state.revision

    assert workspace.remove_applied_condition("score-2") is True

    assert workspace.applied_state.revision == revision + 1
    assert tuple(item.condition_id for item in workspace.applied_state.conditions) == ("site-a",)
    assert workspace.result.source_positions == (0, 2, 4)


def test_invalid_filter_remains_draft_and_preserves_applied_result(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)
    applied_before = workspace.applied_state
    positions_before = workspace.result.source_positions
    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("score", ">", "not-a-number"),))

    assert workspace.apply_filter_draft() is False

    assert workspace.applied_state is applied_before
    assert workspace.applied_state.revision == 0
    assert workspace.result.source_positions == positions_before
    assert workspace.draft_state is not None
    assert "score" in workspace.filter_error_var.get()


def test_typed_filter_row_changes_operators_and_uses_choice_control_for_boolean(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)
    workspace.begin_filter_edit()
    row = workspace.filter_rows[0]

    row.column_var.set("score")
    row.refresh_for_column()
    assert ">" in row.operator_codes
    assert "between" in row.operator_codes
    assert "contains" not in row.operator_codes

    row.column_var.set("approved")
    row.refresh_for_column()
    assert row.operator_codes == ("==", "!=", "isna", "notna")
    assert row.value_control_kind == "choice"


def test_high_cardinality_membership_uses_literal_value_picker_not_query_syntax(
    tk_root,
) -> None:
    frame = pd.DataFrame(
        {
            "ezqcid": [f"SUB{i:03d}" for i in range(101)],
            "score": list(range(101)),
        }
    )
    workspace = TableWorkspace(tk_root, frame)
    workspace.window.update()
    workspace.begin_filter_edit()
    row = workspace.filter_rows[0]
    row.column_var.set("ezqcid")
    row.refresh_for_column()

    assert "in" in row.operator_codes
    row.select_operator("in")
    assert row.value_control_kind == "multi-entry"
    row.multi_entry_var.set("SUB090")
    assert row.add_multi_value() is True
    row.multi_entry_var.set("SUB100")
    assert row.add_multi_value() is True

    assert workspace.apply_filter_draft() is True
    assert workspace.result.source_positions == (90, 100)


def test_selected_source_position_is_retained_outside_view_without_auto_select(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame, on_open_qc=lambda *_: None)
    assert workspace.select_source_position(0) is True
    assert workspace.table.selected_row is not None

    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("site", "==", "B"),))
    assert workspace.apply_filter_draft() is True

    assert workspace.selected_source_position == 0
    assert workspace.selection_outside_view is True
    assert workspace.table.selected_row is None
    assert workspace.open_qc_button.instate(["disabled"])
    assert workspace.result.source_positions == (1, 3)


def test_duplicate_identity_blocks_callback_with_specific_inline_error(tk_root) -> None:
    calls = []
    frame = pd.DataFrame(
        {"ezqcid": ["DUP", "DUP", "OK"], "score": [1, 2, 3]}
    )
    workspace = TableWorkspace(tk_root, frame, on_open_qc=lambda *args: calls.append(args))
    workspace.window.update()
    assert workspace.select_source_position(0) is True

    assert workspace.open_selected_qc() is False

    assert calls == []
    assert "DUP" in workspace.action_error_var.get()
    assert ("unique" in workspace.action_error_var.get().lower()) or (
        "不唯一" in workspace.action_error_var.get()
    )


def test_paging_and_exact_find_jump_to_correct_row_window_and_selection(tk_root) -> None:
    frame = pd.DataFrame(
        {
            "ezqcid": [f"SUB{i:03d}" for i in range(25)],
            "score": list(range(25)),
        }
    )
    workspace = TableWorkspace(tk_root, frame)
    workspace.window.update()
    assert workspace.set_page_size(5) is True

    workspace.next_page()
    assert workspace.page_offset == 5
    workspace.previous_page()
    assert workspace.page_offset == 0

    workspace.find_var.set("SUB018")
    assert workspace.find_identity_exact() is True
    assert workspace.page_offset == 15
    assert workspace.selected_source_position == 18
    assert workspace.table.selected_row is not None
    assert "16" in workspace.visible_range_var.get()
    assert "20" in workspace.visible_range_var.get()


def test_header_sort_cycles_direction_and_shows_direction_in_header_and_status(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)

    workspace.on_header_sort("score")
    assert workspace.applied_state.sort_rules[0].column == "score"
    assert workspace.applied_state.sort_rules[0].ascending is True
    assert workspace.result.dataframe["score"].iloc[:4].tolist() == [1.0, 2.0, 3.0, 4.0]
    assert "↑" in workspace.table.main_tree.heading("score")["text"]
    assert "score" in workspace.sort_status_var.get()

    workspace.on_header_sort("score")
    assert workspace.applied_state.sort_rules[0].ascending is False
    assert workspace.result.dataframe["score"].iloc[:4].tolist() == [4.0, 3.0, 2.0, 1.0]
    assert "↓" in workspace.table.main_tree.heading("score")["text"]

    workspace.on_header_sort("score")
    assert workspace.applied_state.sort_rules == ()


def test_columns_hide_reorder_reset_and_identity_column_cannot_be_hidden(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)

    assert workspace.set_column_visibility("site", False) is True
    assert "site" in workspace.applied_state.columns.hidden
    assert workspace.set_column_visibility("ezqcid", False) is False
    assert "ezqcid" not in workspace.applied_state.columns.hidden
    assert "ezqcid" in workspace.action_error_var.get()

    original_order = workspace.initial_state.columns.order
    assert workspace.move_column("score", -1) is True
    assert workspace.applied_state.columns.order.index("score") == 1

    workspace.reset_columns()
    assert workspace.applied_state.columns == workspace.initial_state.columns
    assert workspace.applied_state.columns.order == original_order


def test_user_resized_column_width_survives_filter_rerender_and_one_step_undo(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)
    workspace.table.main_tree.column("score", width=287)
    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("site", "==", "A"),))

    assert workspace.apply_filter_draft() is True
    assert workspace.table.main_tree.column("score", "width") == 287
    assert workspace.undo_view_state() is True
    assert workspace.table.main_tree.column("score", "width") == 287


@pytest.mark.parametrize(
    ("frame", "expected_fragment"),
    [
        (pd.DataFrame({"ezqcid": [None, "OK"], "score": [1, 2]}), "empty"),
        (pd.DataFrame({"subject": ["A", "B"], "score": [1, 2]}), "ezqcid"),
    ],
)
def test_blank_or_missing_qc_identity_is_rejected_without_callback(
    tk_root,
    frame,
    expected_fragment,
) -> None:
    calls = []
    workspace = TableWorkspace(tk_root, frame, on_open_qc=lambda *args: calls.append(args))
    workspace.window.update()
    assert workspace.select_source_position(0) is True

    assert workspace.open_selected_qc() is False

    assert calls == []
    error = workspace.action_error_var.get().lower()
    assert expected_fragment in error or "为空" in error or "缺少" in error


def test_empty_result_retains_headers_and_exposes_clear_and_undo_recovery(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)
    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("site", "==", "nowhere"),))
    assert workspace.apply_filter_draft() is True
    workspace.window.update()

    assert workspace.result.matched_total == 0
    assert workspace.table.main_tree["columns"]
    assert workspace.empty_frame.winfo_ismapped()
    assert not workspace.undo_button.instate(["disabled"])

    assert workspace.undo_view_state() is True
    assert workspace.result.matched_total == 5
    assert workspace.applied_state.conditions == ()


def test_reset_view_restores_baseline_and_density_changes_only_view_state(
    tk_root,
    source_frame,
) -> None:
    workspace = _workspace(tk_root, source_frame)
    compact_height = int(workspace.table.main_tree.tk.call("ttk::style", "lookup", workspace._tree_style, "-rowheight"))
    assert workspace.set_density("comfortable") is True
    comfortable_height = int(workspace.table.main_tree.tk.call("ttk::style", "lookup", workspace._tree_style, "-rowheight"))
    assert comfortable_height > compact_height
    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("site", "==", "A"),))
    assert workspace.apply_filter_draft() is True
    assert workspace.set_column_visibility("approved", False) is True

    revision = workspace.applied_state.revision
    assert workspace.reset_view_state() is True

    assert workspace.applied_state.revision == revision + 1
    assert replace(workspace.applied_state, revision=0) == workspace.initial_state
    assert workspace.result.matched_total == len(source_frame)


def test_keyboard_find_escape_and_return_activation_are_available(
    tk_root,
    source_frame,
) -> None:
    calls = []
    workspace = _workspace(
        tk_root,
        source_frame,
        on_open_qc=lambda ezqcid, anchor: calls.append((ezqcid, anchor)),
    )
    workspace.window.focus_force()
    workspace.window.event_generate("<Control-f>")
    workspace.window.update()
    assert workspace.window.focus_get() == workspace.find_entry

    workspace.begin_filter_edit()
    workspace.window.event_generate("<Escape>")
    workspace.window.update()
    assert workspace.draft_state is None
    assert workspace.window.winfo_exists()

    assert workspace.select_source_position(2) is True
    workspace.table.main_tree.focus_force()
    workspace.table.main_tree.event_generate("<Return>")
    workspace.window.update()
    assert calls and calls[0][0] == "SUB003"


def test_right_click_selects_exact_row_and_uses_pointer_as_qc_menu_anchor(
    tk_root,
    source_frame,
) -> None:
    calls = []
    workspace = _workspace(
        tk_root,
        source_frame,
        on_open_qc=lambda ezqcid, anchor: calls.append((ezqcid, anchor)),
    )
    tree = workspace.table.main_tree
    first = tree.get_children()[0]
    x, y, width, height = tree.bbox(first)

    tree.event_generate("<Button-3>", x=x + min(width - 1, 5), y=y + max(1, height // 2))
    workspace.window.update()

    assert calls and calls[0][0] == "SUB001"
    assert hasattr(calls[0][1], "x_root")
    assert workspace.selected_source_position == 0


def test_view_operations_do_not_mutate_the_callers_dataframe(tk_root, source_frame) -> None:
    original = source_frame.copy(deep=True)
    workspace = _workspace(tk_root, source_frame)
    workspace.begin_filter_edit()
    workspace.set_filter_draft((FilterCondition("score", ">=", "2"),))
    assert workspace.apply_filter_draft() is True
    workspace.on_header_sort("site")
    workspace.set_column_visibility("approved", False)
    workspace.next_page()

    pd.testing.assert_frame_equal(source_frame, original)
