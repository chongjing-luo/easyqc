"""Focused widgets for the read-only table workspace."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk
from typing import Any

import pandas as pd

from models.table_view_state import ColumnViewState, RowWindow, SortRule


@dataclass(frozen=True)
class TableRowReference:
    """Stable positions for one rendered result row."""

    result_position: int
    source_position: int


class WindowedTable(ttk.Frame):
    """Render one read-only row window with optional frozen columns.

    ``render_window`` is deliberately presentation-only: it reads one
    ``RowWindow`` and one ``ColumnViewState`` and changes Tk widget state.  It
    never filters, sorts, persists data, or changes either input object.
    """

    def __init__(
        self,
        parent,
        *,
        on_selection: Callable[[TableRowReference], None] | None = None,
        on_activate: Callable[[TableRowReference], None] | None = None,
        on_context: Callable[[TableRowReference, Any], None] | None = None,
        on_header_sort: Callable[[str], None] | None = None,
        tree_style: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.on_selection = on_selection
        self.on_activate = on_activate
        self.on_context = on_context
        self.on_header_sort = on_header_sort

        self.pinned_frame = ttk.Frame(self)
        self.pinned_tree = ttk.Treeview(
            self.pinned_frame,
            show="headings",
            selectmode="browse",
            takefocus=False,
            style=tree_style or "Treeview",
        )
        self.main_tree = ttk.Treeview(
            self,
            show="headings",
            selectmode="browse",
            takefocus=True,
            style=tree_style or "Treeview",
        )
        # A compatibility-friendly name for callers that need the keyboard
        # owning Treeview rather than the frozen identity pane.
        self.tree = self.main_tree
        self.v_scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._yview)
        self.h_scrollbar = ttk.Scrollbar(
            self,
            orient=tk.HORIZONTAL,
            command=self.main_tree.xview,
        )

        self.main_tree.configure(
            yscrollcommand=self._main_yview_changed,
            xscrollcommand=self.h_scrollbar.set,
        )
        self.pinned_tree.configure(yscrollcommand=self._pinned_yview_changed)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)
        self.pinned_frame.grid_rowconfigure(0, weight=1)
        self.pinned_frame.grid_columnconfigure(0, weight=1)
        self.pinned_frame.grid_propagate(False)
        self.pinned_tree.grid(row=0, column=0, sticky="nsew")
        self.pinned_frame.grid(row=0, column=0, sticky="nsew")
        self.main_tree.grid(row=0, column=1, sticky="nsew")
        self.v_scrollbar.grid(row=0, column=2, sticky="ns")
        self.h_scrollbar.grid(row=1, column=1, sticky="ew")

        self._pinned_visible = False
        self._syncing_selection = False
        self._syncing_scroll = False
        self._row_references: dict[str, TableRowReference] = {}
        self._selected_iid: str | None = None
        self._last_emitted_selection: TableRowReference | None = None

        self.main_tree.bind("<<TreeviewSelect>>", self._selection_changed, add="+")
        self.pinned_tree.bind("<<TreeviewSelect>>", self._selection_changed, add="+")
        self.main_tree.bind("<Return>", self._activate_selected, add="+")
        for tree in (self.pinned_tree, self.main_tree):
            tree.bind("<Button-3>", self._context_selected, add="+")
            tree.bind("<Button-2>", self._context_selected, add="+")

    @property
    def selected_row(self) -> TableRowReference | None:
        """Return the selected row's result/source positions, if any."""

        if self._selected_iid is None:
            return None
        return self._row_references.get(self._selected_iid)

    def render_window(
        self,
        window: RowWindow,
        columns: ColumnViewState,
        sort_rule: SortRule | None = None,
    ) -> None:
        """Replace the visible rows with exactly ``window.dataframe``."""

        if len(window.source_positions) != len(window.dataframe):
            raise ValueError("RowWindow source_positions must match dataframe rows")

        visible_columns = columns.visible_columns
        if len(set(visible_columns)) != len(visible_columns):
            raise ValueError("ColumnViewState contains duplicate visible columns")
        missing_columns = tuple(
            column for column in visible_columns if column not in window.dataframe.columns
        )
        if missing_columns:
            names = ", ".join(str(column) for column in missing_columns)
            raise ValueError(f"Visible columns are missing from RowWindow: {names}")

        pinned_names = set(columns.pinned)
        pinned_columns = tuple(column for column in visible_columns if column in pinned_names)
        main_columns = tuple(column for column in visible_columns if column not in pinned_names)

        self._clear_rows()
        self._configure_columns(self.pinned_tree, pinned_columns, columns, sort_rule)
        self._configure_columns(self.main_tree, main_columns, columns, sort_rule)
        self._set_pinned_visibility(bool(pinned_columns), columns, pinned_columns)

        visible_frame = window.dataframe.loc[:, list(visible_columns)]
        rows = visible_frame.itertuples(index=False, name=None)
        for local_position, row_values in enumerate(rows):
            iid = f"row-{local_position}"
            values_by_column = dict(zip(visible_columns, row_values))
            self._row_references[iid] = TableRowReference(
                result_position=window.offset + local_position,
                source_position=window.source_positions[local_position],
            )
            if pinned_columns:
                self.pinned_tree.insert(
                    "",
                    "end",
                    iid=iid,
                    values=tuple(
                        self._display_value(values_by_column[name]) for name in pinned_columns
                    ),
                )
            self.main_tree.insert(
                "",
                "end",
                iid=iid,
                values=tuple(self._display_value(values_by_column[name]) for name in main_columns),
            )

        self.main_tree.yview_moveto(0.0)
        if pinned_columns:
            self.pinned_tree.yview_moveto(0.0)
        self.main_tree.xview_moveto(0.0)

    def _clear_rows(self) -> None:
        for tree in (self.pinned_tree, self.main_tree):
            children = tree.get_children()
            if children:
                tree.delete(*children)
        self._row_references.clear()
        self._selected_iid = None
        self._last_emitted_selection = None

    def _configure_columns(
        self,
        tree: ttk.Treeview,
        column_names: tuple[str, ...],
        state: ColumnViewState,
        sort_rule: SortRule | None,
    ) -> None:
        tree["columns"] = column_names
        for column in column_names:
            heading = str(column)
            if sort_rule is not None and sort_rule.column == column:
                heading = f"{heading} {'↑' if sort_rule.ascending else '↓'}"
            heading_options = {"text": heading, "anchor": tk.W}
            if self.on_header_sort is not None:
                heading_options["command"] = lambda name=column: self.on_header_sort(name)
            tree.heading(column, **heading_options)
            tree.column(
                column,
                width=state.width_for(column),
                minwidth=60,
                stretch=False,
                anchor=tk.W,
            )

    def select_source_position(self, source_position: int) -> bool:
        """Select one rendered row by stable source position, if visible."""

        for iid, reference in self._row_references.items():
            if reference.source_position != source_position:
                continue
            self.main_tree.selection_set(iid)
            self.main_tree.focus(iid)
            self.main_tree.event_generate("<<TreeviewSelect>>")
            return True
        self.clear_selection()
        return False

    def clear_selection(self) -> None:
        """Clear both synchronized Treeview selections."""

        for tree in (self.pinned_tree, self.main_tree):
            tree.selection_remove(*tree.selection())
        self._selected_iid = None
        self._last_emitted_selection = None

    def current_widths(self) -> tuple[tuple[str, int], ...]:
        """Return current user-resized widths in displayed column order."""

        widths: list[tuple[str, int]] = []
        for tree in (self.pinned_tree, self.main_tree):
            for column in tree["columns"]:
                widths.append((str(column), int(tree.column(column, "width"))))
        return tuple(widths)

    def _set_pinned_visibility(
        self,
        visible: bool,
        columns: ColumnViewState,
        pinned_columns: tuple[str, ...],
    ) -> None:
        self._pinned_visible = visible
        if not visible:
            self.pinned_frame.grid_remove()
            return

        pinned_width = sum(columns.width_for(column) for column in pinned_columns)
        self.pinned_frame.configure(width=max(1, pinned_width))
        self.pinned_frame.grid()

    @staticmethod
    def _display_value(value: Any) -> str:
        try:
            is_missing = bool(pd.isna(value))
        except (TypeError, ValueError):
            is_missing = False
        return "—" if is_missing else str(value)

    def _selection_changed(self, event) -> None:
        if self._syncing_selection:
            return

        source = event.widget
        selection = source.selection()
        if not selection:
            return
        iid = selection[0]
        if iid not in self._row_references:
            return

        self._syncing_selection = True
        try:
            for tree in (self.pinned_tree, self.main_tree):
                if tree is self.pinned_tree and not self._pinned_visible:
                    continue
                if tree.selection() != (iid,):
                    tree.selection_set(iid)
                tree.focus(iid)
                tree.see(iid)
        finally:
            self._syncing_selection = False

        self._selected_iid = iid
        reference = self._row_references[iid]
        if reference != self._last_emitted_selection:
            self._last_emitted_selection = reference
            if self.on_selection is not None:
                self.on_selection(reference)

        if source is self.pinned_tree:
            self.after_idle(self.main_tree.focus_set)

    def _activate_selected(self, _event=None) -> str:
        reference = self.selected_row
        if reference is not None and self.on_activate is not None:
            self.on_activate(reference)
        return "break"

    def _context_selected(self, event) -> str:
        iid = event.widget.identify_row(event.y)
        if not iid or iid not in self._row_references:
            return "break"
        event.widget.selection_set(iid)
        event.widget.focus(iid)
        self._selection_changed(event)
        reference = self._row_references[iid]
        if self.on_context is not None:
            self.on_context(reference, event)
        return "break"

    def _yview(self, *args) -> None:
        self._syncing_scroll = True
        try:
            self.main_tree.yview(*args)
            if self._pinned_visible:
                self.pinned_tree.yview(*args)
        finally:
            self._syncing_scroll = False
        first, last = self.main_tree.yview()
        self.v_scrollbar.set(first, last)

    def _main_yview_changed(self, first: str, last: str) -> None:
        self.v_scrollbar.set(first, last)
        if not self._pinned_visible or self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            self.pinned_tree.yview_moveto(first)
        finally:
            self._syncing_scroll = False

    def _pinned_yview_changed(self, first: str, last: str) -> None:
        self.v_scrollbar.set(first, last)
        if self._syncing_scroll:
            return
        self._syncing_scroll = True
        try:
            self.main_tree.yview_moveto(first)
        finally:
            self._syncing_scroll = False


__all__ = ["TableRowReference", "WindowedTable"]
