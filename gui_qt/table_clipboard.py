"""Cell selection and TSV copy shared by read-only Qt tables.

Views are supplied in left-to-right order (frozen first), over one model.
Serialization has no side effects; only copy_selected_cells writes the clipboard.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Sequence

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMenu, QTableView

from gui_qt.i18n import LanguageController, get_or_create_language_controller


def _visible_selection(views: Sequence[QTableView]):
    """Return visible axes and selected cells; reject unrelated table models."""
    if not views:
        return None, [], [], set()
    model = views[0].model()
    if any(view.model() is not model for view in views):
        raise ValueError("Clipboard views must share the same model")
    if model is None:
        return None, [], [], set()
    columns = []
    for view in views:
        header = view.horizontalHeader()
        for visual in range(header.count()):
            column = header.logicalIndex(visual)
            if not view.isColumnHidden(column) and column not in columns:
                columns.append(column)
    header = views[0].verticalHeader()
    rows = [header.logicalIndex(visual) for visual in range(header.count())]
    rows = [row for row in rows if any(not view.isRowHidden(row) for view in views)]
    selected = {
        (index.row(), index.column())
        for view in views
        if view.selectionModel() is not None
        for index in view.selectionModel().selectedIndexes()
        if not view.isRowHidden(index.row()) and not view.isColumnHidden(index.column())
    }
    return model, rows, columns, selected


def selected_cell_count(views: Sequence[QTableView]) -> int:
    """Count visible selected model cells without double-counting frozen views."""
    return len(_visible_selection(views)[3])


def selected_cells_tsv(views: Sequence[QTableView]) -> str:
    """Serialize the visible selection bounding rectangle, leaving holes blank.

    Embedded tabs/newlines are quoted as TSV. Hidden cells and unselected values
    are never read; invalid cross-model view groups fail with ValueError.
    """
    model, rows, columns, selected = _visible_selection(views)
    if not selected:
        return ""
    selected_rows = {row for row, _column in selected}
    selected_columns = {column for _row, column in selected}
    row_positions = [position for position, row in enumerate(rows) if row in selected_rows]
    column_positions = [position for position, column in enumerate(columns) if column in selected_columns]
    rows = rows[min(row_positions):max(row_positions) + 1]
    columns = columns[min(column_positions):max(column_positions) + 1]
    output = io.StringIO(newline="")
    # CRLF makes csv quote both kinds of embedded line break. Join the encoded
    # records ourselves so the clipboard still has predictable LF row endings.
    writer = csv.writer(output, delimiter="\t", lineterminator="\r\n")
    encoded_rows = []
    for row in rows:
        values = []
        for column in columns:
            value = model.data(model.index(row, column), Qt.DisplayRole) if (row, column) in selected else ""
            values.append("" if value is None else str(value))
        writer.writerow(values)
        encoded_rows.append(output.getvalue()[:-2])
        output.seek(0)
        output.truncate(0)
    return "\n".join(encoded_rows)


def copy_selected_cells(views: Sequence[QTableView]) -> bool:
    """Write selected cells to the application clipboard; no selection is a no-op."""
    if not selected_cell_count(views):
        return False
    QApplication.clipboard().setText(selected_cells_tsv(views))
    return True


def install_table_copy_shortcut(view: QTableView, views: Sequence[QTableView]) -> QShortcut:
    """Install the platform Copy shortcut, active only while this table has focus."""
    shortcut = QShortcut(QKeySequence.StandardKey.Copy, view)
    shortcut.setContext(Qt.WidgetWithChildrenShortcut)
    shortcut.activated.connect(lambda: copy_selected_cells(views))
    return shortcut


def prepare_cell_context_selection(view: QTableView, index: QModelIndex) -> None:
    """Preserve a selected context cell; otherwise replace selection with that cell."""
    if not index.isValid():
        return
    selection = view.selectionModel()
    flags = QItemSelectionModel.NoUpdate if selection.isSelected(index) else QItemSelectionModel.ClearAndSelect
    selection.setCurrentIndex(index, flags)


def create_copy_menu(
    views: Sequence[QTableView],
    parent: QTableView,
    *,
    language: LanguageController | None = None,
) -> QMenu:
    """Build a copy-only menu without resolving any row or QC identity."""
    menu = QMenu(parent)
    action = menu.addAction("复制")
    action.setEnabled(bool(selected_cell_count(views)))
    action.triggered.connect(lambda: copy_selected_cells(views))
    (language or get_or_create_language_controller()).register_root(menu)
    return menu
