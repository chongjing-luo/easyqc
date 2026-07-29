import tkinter as tk

import pandas as pd
import pytest

from gui.table_widgets import TableRowReference, WindowedTable
from models.table_view_state import ColumnViewState, RowWindow


@pytest.fixture
def tk_root():
    root = tk.Tk()
    root.geometry("520x260")
    root.update_idletasks()
    yield root
    root.destroy()


def _window() -> RowWindow:
    return RowWindow(
        dataframe=pd.DataFrame(
            {
                "site": ["A", "B"],
                "easyqcid": ["SUB010", "SUB011"],
                "hidden_note": ["ignore", "ignore"],
                "score": [3.5, None],
                "comment": [None, "review"],
            }
        ),
        source_positions=(8, 3),
        offset=10,
        limit=2,
        matched_total=40,
    )


def _columns() -> ColumnViewState:
    return ColumnViewState(
        order=("site", "easyqcid", "hidden_note", "score", "comment"),
        hidden=("hidden_note",),
        widths=(("site", 260), ("easyqcid", 140), ("score", 260), ("comment", 260)),
        pinned=("easyqcid",),
    )


def test_render_window_splits_pinned_and_scrolling_columns_and_formats_missing(
    tk_root,
) -> None:
    table = WindowedTable(tk_root)
    table.pack(fill=tk.BOTH, expand=True)

    table.render_window(_window(), _columns())
    tk_root.update()

    assert table.pinned_tree["columns"] == ("easyqcid",)
    assert table.main_tree["columns"] == ("site", "score", "comment")
    assert len(table.pinned_tree.get_children()) == 2
    assert len(table.main_tree.get_children()) == 2

    first, second = table.main_tree.get_children()
    assert table.pinned_tree.item(first, "values") == ("SUB010",)
    assert table.main_tree.item(first, "values") == ("A", "3.5", "—")
    assert table.main_tree.item(second, "values") == ("B", "—", "review")

    pinned_x_before = table.pinned_tree.xview()
    table.main_tree.xview_moveto(1.0)
    tk_root.update()

    assert table.main_tree.xview()[0] > 0
    assert table.pinned_tree.xview() == pinned_x_before


def test_render_without_pinned_columns_uses_one_visible_keyboard_table(tk_root) -> None:
    table = WindowedTable(tk_root)
    table.pack(fill=tk.BOTH, expand=True)
    table.render_window(
        _window(),
        ColumnViewState(
            order=("comment", "site", "hidden_note"),
            hidden=("hidden_note",),
        ),
    )
    tk_root.update()

    assert not table.pinned_frame.winfo_ismapped()
    assert table.pinned_tree.get_children() == ()
    assert table.main_tree["columns"] == ("comment", "site")
    assert len(table.main_tree.get_children()) == 2


def test_selection_from_either_tree_is_synchronized_and_preserves_positions(
    tk_root,
) -> None:
    selected = []
    table = WindowedTable(tk_root, on_selection=selected.append)
    table.pack(fill=tk.BOTH, expand=True)
    table.render_window(_window(), _columns())
    tk_root.update()
    first, second = table.main_tree.get_children()

    table.main_tree.selection_set(first)
    table.main_tree.focus(first)
    table.main_tree.event_generate("<<TreeviewSelect>>")
    tk_root.update()

    assert table.pinned_tree.selection() == (first,)
    assert table.selected_row == TableRowReference(result_position=10, source_position=8)

    table.pinned_tree.selection_set(second)
    table.pinned_tree.focus(second)
    table.pinned_tree.event_generate("<<TreeviewSelect>>")
    tk_root.update()

    assert table.main_tree.selection() == (second,)
    assert table.selected_row == TableRowReference(result_position=11, source_position=3)
    assert selected[-1] == TableRowReference(result_position=11, source_position=3)
    assert table.pinned_tree.cget("takefocus") in (False, "0", 0)


def test_vertical_scrolling_stays_synchronized_between_frozen_and_main_trees(
    tk_root,
) -> None:
    rows = 40
    window = RowWindow(
        dataframe=pd.DataFrame(
            {
                "easyqcid": [f"SUB{i:03d}" for i in range(rows)],
                "score": list(range(rows)),
            }
        ),
        source_positions=tuple(range(rows)),
        offset=0,
        limit=rows,
        matched_total=rows,
    )
    table = WindowedTable(tk_root)
    table.pack(fill=tk.BOTH, expand=True)
    table.render_window(
        window,
        ColumnViewState(order=("easyqcid", "score"), pinned=("easyqcid",)),
    )
    tk_root.update()

    table.main_tree.yview_moveto(0.75)
    tk_root.update()
    assert table.pinned_tree.yview() == pytest.approx(table.main_tree.yview())

    table.pinned_tree.yview_moveto(0.2)
    tk_root.update()
    assert table.main_tree.yview() == pytest.approx(table.pinned_tree.yview())


def test_return_activates_the_selected_row_with_stable_result_and_source_positions(
    tk_root,
) -> None:
    activated = []
    table = WindowedTable(tk_root, on_activate=activated.append)
    table.pack(fill=tk.BOTH, expand=True)
    table.render_window(_window(), _columns())
    tk_root.update()
    second = table.main_tree.get_children()[1]

    table.pinned_tree.selection_set(second)
    table.pinned_tree.focus(second)
    table.pinned_tree.event_generate("<<TreeviewSelect>>")
    tk_root.update()
    table.main_tree.focus_force()
    table.main_tree.event_generate("<Return>")
    tk_root.update()

    assert activated == [TableRowReference(result_position=11, source_position=3)]


def test_render_and_keyboard_input_do_not_mutate_window_or_source_values(tk_root) -> None:
    window = _window()
    original_frame = window.dataframe.copy(deep=True)
    original_positions = window.source_positions
    columns = _columns()
    table = WindowedTable(tk_root)
    table.pack(fill=tk.BOTH, expand=True)

    table.render_window(window, columns)
    tk_root.update()
    first = table.main_tree.get_children()[0]
    displayed_before = table.main_tree.item(first, "values")
    table.main_tree.selection_set(first)
    table.main_tree.focus(first)
    table.main_tree.focus_force()
    table.main_tree.event_generate("<KeyPress-a>")
    tk_root.update()

    assert table.main_tree.item(first, "values") == displayed_before
    pd.testing.assert_frame_equal(window.dataframe, original_frame)
    assert window.source_positions == original_positions
    assert columns == _columns()


def test_render_rejects_inconsistent_window_metadata(tk_root) -> None:
    table = WindowedTable(tk_root)
    invalid = RowWindow(
        dataframe=pd.DataFrame({"easyqcid": ["SUB001", "SUB002"]}),
        source_positions=(4,),
        offset=0,
        limit=2,
        matched_total=2,
    )

    with pytest.raises(ValueError, match="source_positions"):
        table.render_window(
            invalid,
            ColumnViewState(order=("easyqcid",), pinned=("easyqcid",)),
        )
