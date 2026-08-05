"""Shared draft-only behavior for QC-module score tables."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QSizePolicy,
    QTableWidget,
)


def sync_score_table_height(table: QTableWidget) -> int:
    """Apply native header/row/frame height to one score table and return it."""

    if not isinstance(table, QTableWidget):
        raise TypeError("score table height requires QTableWidget")
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.resizeRowsToContents()
    header_height = table.horizontalHeader().height()
    if header_height <= 0:
        header_height = table.horizontalHeader().sizeHint().height()
    height = (
        header_height
        + sum(table.rowHeight(row) for row in range(table.rowCount()))
        + table.frameWidth() * 2
    )
    table.setFixedHeight(height)
    policy = table.sizePolicy()
    policy.setVerticalPolicy(QSizePolicy.Fixed)
    table.setSizePolicy(policy)
    return height


def move_score_row(table: QTableWidget, delta: int) -> bool:
    """Move the selected complete score row by one position in the draft."""

    if not isinstance(table, QTableWidget):
        raise TypeError("score row movement requires QTableWidget")
    if (
        isinstance(delta, bool)
        or not isinstance(delta, int)
        or delta not in (-1, 1)
    ):
        raise ValueError("score row delta must be -1 or 1")

    selection_model = table.selectionModel()
    source_row = table.currentRow()
    if (
        source_row < 0
        or selection_model is None
        or not selection_model.hasSelection()
    ):
        return False
    target_row = source_row + delta
    if target_row < 0 or target_row >= table.rowCount():
        return False

    current_column = table.currentColumn()
    source_items = [
        table.takeItem(source_row, column)
        for column in range(table.columnCount())
    ]
    target_items = [
        table.takeItem(target_row, column)
        for column in range(table.columnCount())
    ]
    for column, item in enumerate(source_items):
        if item is not None:
            table.setItem(target_row, column, item)
    for column, item in enumerate(target_items):
        if item is not None:
            table.setItem(source_row, column, item)

    table.clearSelection()
    table.selectRow(target_row)
    if table.columnCount():
        table.setCurrentCell(
            target_row,
            min(max(current_column, 0), table.columnCount() - 1),
        )
    sync_score_table_height(table)
    return True


def update_score_move_actions(
    table: QTableWidget,
    up: QAction | QAbstractButton,
    down: QAction | QAbstractButton,
) -> None:
    """Enable score move controls only when the selected row can move."""

    if not isinstance(table, QTableWidget):
        raise TypeError("score move actions require QTableWidget")
    if not isinstance(up, (QAction, QAbstractButton)) or not isinstance(
        down,
        (QAction, QAbstractButton),
    ):
        raise TypeError("score move controls require QAction or QAbstractButton")

    selection_model = table.selectionModel()
    row = table.currentRow()
    selected = (
        row >= 0
        and selection_model is not None
        and selection_model.hasSelection()
    )
    up.setEnabled(selected and row > 0)
    down.setEnabled(selected and row < table.rowCount() - 1)


__all__ = [
    "move_score_row",
    "sync_score_table_height",
    "update_score_move_actions",
]
