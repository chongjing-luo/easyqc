from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from gui_qt.module_score_table import (
    move_score_row,
    sync_score_table_height,
    update_score_move_actions,
)


def _score_table(qtbot, rows: tuple[tuple[str, str], ...]) -> QTableWidget:
    table = QTableWidget(len(rows), 2)
    qtbot.addWidget(table)
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(value))
    table.show()
    return table


def test_score_table_height_uses_header_rows_and_frame_and_tracks_row_count(
    qtbot,
) -> None:
    table = _score_table(qtbot, (("Quality", "Poor,Fair,Good"),))

    one_row_height = sync_score_table_height(table)
    expected = (
        table.horizontalHeader().height()
        + sum(table.rowHeight(row) for row in range(table.rowCount()))
        + table.frameWidth() * 2
    )
    assert one_row_height == expected
    assert table.minimumHeight() == one_row_height
    assert table.maximumHeight() == one_row_height
    assert table.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff

    table.insertRow(1)
    table.setItem(1, 0, QTableWidgetItem("Artifact"))
    table.setItem(1, 1, QTableWidgetItem("None,Mild,Severe"))
    two_row_height = sync_score_table_height(table)
    assert two_row_height > one_row_height

    table.removeRow(1)
    assert sync_score_table_height(table) == one_row_height


def test_move_score_row_transfers_complete_items_metadata_and_selection(qtbot) -> None:
    table = _score_table(
        qtbot,
        (
            ("First", "1,2"),
            ("Second", "3,4"),
            ("Third", "5,6"),
        ),
    )
    source_items = [table.item(1, column) for column in range(2)]
    displaced_items = [table.item(0, column) for column in range(2)]
    for column, item in enumerate(source_items):
        item.setToolTip(f"source tooltip {column}")
        item.setData(Qt.UserRole, {"source": column})
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    table.selectRow(1)
    table.setCurrentCell(1, 1)

    assert move_score_row(table, -1) is True

    assert [table.item(0, column) for column in range(2)] == source_items
    assert [table.item(1, column) for column in range(2)] == displaced_items
    for column, item in enumerate(source_items):
        assert item.toolTip() == f"source tooltip {column}"
        assert item.data(Qt.UserRole) == {"source": column}
        assert not bool(item.flags() & Qt.ItemIsEditable)
    assert table.currentRow() == 0
    assert table.currentColumn() == 1
    assert {index.row() for index in table.selectedIndexes()} == {0}
    assert table.height() == sync_score_table_height(table)


def test_move_score_row_boundaries_and_invalid_calls_do_not_mutate(qtbot) -> None:
    table = _score_table(qtbot, (("First", "1"), ("Second", "2")))
    original_items = [
        [table.item(row, column) for column in range(2)]
        for row in range(table.rowCount())
    ]

    table.setCurrentCell(0, 0)
    assert move_score_row(table, -1) is False
    table.setCurrentCell(1, 0)
    assert move_score_row(table, 1) is False
    table.setCurrentItem(None)
    assert move_score_row(table, -1) is False

    for delta in (0, 2, True, "-1"):
        with pytest.raises(ValueError, match="delta"):
            move_score_row(table, delta)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="QTableWidget"):
        move_score_row(QWidget(), -1)  # type: ignore[arg-type]

    assert [
        [table.item(row, column) for column in range(2)]
        for row in range(table.rowCount())
    ] == original_items


def test_update_score_move_actions_tracks_selection_and_bounds(qtbot) -> None:
    table = _score_table(
        qtbot,
        (("First", "1"), ("Second", "2"), ("Third", "3")),
    )
    up = QPushButton()
    down = QPushButton()
    qtbot.addWidget(up)
    qtbot.addWidget(down)

    table.setCurrentItem(None)
    update_score_move_actions(table, up, down)
    assert not up.isEnabled()
    assert not down.isEnabled()

    table.setCurrentCell(0, 0)
    update_score_move_actions(table, up, down)
    assert not up.isEnabled()
    assert down.isEnabled()

    table.setCurrentCell(1, 1)
    update_score_move_actions(table, up, down)
    assert up.isEnabled()
    assert down.isEnabled()

    table.setCurrentCell(2, 0)
    update_score_move_actions(table, up, down)
    assert up.isEnabled()
    assert not down.isEnabled()

    table.setRowCount(1)
    table.setCurrentCell(0, 0)
    update_score_move_actions(table, up, down)
    assert not up.isEnabled()
    assert not down.isEnabled()
