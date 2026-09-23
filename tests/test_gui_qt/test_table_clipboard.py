"""RI-13a selected-cell serialization: visible order and no unselected data."""

import csv
import io

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableView

from gui_qt.table_clipboard import (
    copy_selected_cells,
    prepare_cell_context_selection,
    selected_cells_tsv,
)


def _views(qtbot):
    model = QStandardItemModel()
    for row in range(3):
        model.appendRow([QStandardItem(f"{row}:{column}") for column in range(4)])
    views = (QTableView(), QTableView())
    for view in views:
        qtbot.addWidget(view)
        view.setModel(model)
        view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        view.setSelectionBehavior(QAbstractItemView.SelectItems)
    views[1].setSelectionModel(views[0].selectionModel())
    return model, views


def _select(model, view, *cells):
    for row, column in cells:
        view.selectionModel().select(model.index(row, column), QItemSelectionModel.Select)


def test_copy_visual_order_deduplicates_frozen_columns_and_omits_hidden(qtbot):
    model, (frozen, main) = _views(qtbot)
    for column in (1, 2, 3):
        frozen.hideColumn(column)
    main.hideColumn(2)
    main.horizontalHeader().moveSection(3, 1)
    _select(model, main, (0, 0), (0, 1), (0, 2), (0, 3))
    assert selected_cells_tsv((frozen, main)) == "0:0\t0:3\t0:1"


def test_sparse_selection_keeps_holes_without_leaking_unselected_values(qtbot):
    model, views = _views(qtbot)
    _select(model, views[0], (0, 0), (2, 3))
    assert selected_cells_tsv(views) == "0:0\t\t\t\n\t\t\t\n\t\t\t2:3"
    for view in views:
        view.hideRow(1)
    assert selected_cells_tsv(views) == "0:0\t\t\t\n\t\t\t2:3"


def test_copy_quotes_embedded_tsv_delimiters_and_writes_only_when_selected(qtbot):
    model, views = _views(qtbot)
    model.setData(model.index(0, 0), 'line\nwith\ttab and "quote"')
    _select(model, views[0], (0, 0))
    result = selected_cells_tsv(views)
    assert list(csv.reader(io.StringIO(result), delimiter="\t")) == [
        ['line\nwith\ttab and "quote"']
    ]
    clipboard = QApplication.clipboard()
    previous = clipboard.text()
    try:
        assert copy_selected_cells(views)
        assert clipboard.text() == result
        views[0].clearSelection()
        assert not copy_selected_cells(views)
        assert clipboard.text() == result
    finally:
        clipboard.setText(previous)


def test_context_click_preserves_inside_selection_and_replaces_outside(qtbot):
    model, views = _views(qtbot)
    _select(model, views[0], (0, 0), (0, 1))
    prepare_cell_context_selection(views[0], model.index(0, 0))
    assert len(views[0].selectionModel().selectedIndexes()) == 2
    prepare_cell_context_selection(views[0], model.index(2, 3))
    assert views[0].selectionModel().selectedIndexes() == [model.index(2, 3)]


def test_different_table_models_are_rejected(qtbot):
    _model, views = _views(qtbot)
    other = QTableView()
    qtbot.addWidget(other)
    other.setModel(QStandardItemModel(1, 1, other))
    with pytest.raises(ValueError, match="same model"):
        selected_cells_tsv((views[0], other))


def test_copy_uses_visual_row_order_not_original_source_order(qtbot):
    model, views = _views(qtbot)
    views[0].verticalHeader().moveSection(2, 0)
    _select(model, views[0], (0, 0), (2, 0))
    assert selected_cells_tsv(views) == "2:0\n0:0"


def test_empty_and_hidden_only_selection_are_empty(qtbot):
    assert selected_cells_tsv(()) == ""
    model, views = _views(qtbot)
    assert selected_cells_tsv(views) == ""
    _select(model, views[0], (0, 1))
    for view in views:
        view.hideColumn(1)
    assert selected_cells_tsv(views) == ""


def test_carriage_returns_inside_cells_round_trip_as_one_tsv_cell(qtbot):
    model, views = _views(qtbot)
    model.setData(model.index(0, 0), "first\rsecond")
    _select(model, views[0], (0, 0))
    assert list(csv.reader(io.StringIO(selected_cells_tsv(views), newline=""), delimiter="\t")) == [
        ["first\rsecond"]
    ]
