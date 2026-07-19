"""Visible-column and order editor for the Qt Table workspace."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.table_view_state import ColumnViewState


class ColumnsPanel(QWidget):
    applyRequested = Signal(object)
    resetRequested = Signal()

    def __init__(self, initial: ColumnViewState, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("columnsPanel")
        self._initial = initial
        self._widths = initial.widths
        self._pinned = initial.pinned
        self._build_ui()
        self.set_state(initial)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        heading = QLabel("Columns", self)
        heading.setObjectName("panelHeading")
        layout.addWidget(heading)
        hint = QLabel("Checked columns are visible. ezqcid stays pinned.", self)
        hint.setObjectName("panelHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("columnList")
        self.list_widget.setAlternatingRowColors(True)
        layout.addWidget(self.list_widget, 1)

        move_actions = QHBoxLayout()
        self.up_button = QPushButton("Move up", self)
        self.down_button = QPushButton("Move down", self)
        move_actions.addWidget(self.up_button)
        move_actions.addWidget(self.down_button)
        layout.addLayout(move_actions)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("columnsError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        self.reset_button = QPushButton("Reset", self)
        self.apply_button = QPushButton("Apply", self)
        self.apply_button.setObjectName("primaryAction")
        actions.addWidget(self.reset_button)
        actions.addStretch(1)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)

        self.up_button.clicked.connect(lambda: self.move_selected(-1))
        self.down_button.clicked.connect(lambda: self.move_selected(1))
        self.reset_button.clicked.connect(self.resetRequested.emit)
        self.apply_button.clicked.connect(lambda: self.applyRequested.emit(self.state()))

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def set_error(self, message: str) -> None:
        self.error_label.setText(message)

    def set_state(self, state: ColumnViewState) -> None:
        self._widths = state.widths
        self._pinned = state.pinned
        self.list_widget.clear()
        hidden = set(state.hidden)
        pinned = set(state.pinned)
        for column in state.order:
            item = QListWidgetItem(column, self.list_widget)
            item.setData(Qt.UserRole, column)
            flags = item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled
            if column in pinned:
                flags &= ~Qt.ItemIsUserCheckable
                item.setText(f"{column}   · pinned")
            item.setFlags(flags)
            item.setCheckState(Qt.Unchecked if column in hidden else Qt.Checked)

    def state(self) -> ColumnViewState:
        order: list[str] = []
        hidden: list[str] = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            column = str(item.data(Qt.UserRole))
            order.append(column)
            if item.checkState() == Qt.Unchecked:
                hidden.append(column)
        return ColumnViewState(
            order=tuple(order),
            hidden=tuple(hidden),
            widths=self._widths,
            pinned=self._pinned,
        )

    def move_selected(self, delta: int) -> bool:
        row = self.list_widget.currentRow()
        if row < 0:
            return False
        item = self.list_widget.item(row)
        if str(item.data(Qt.UserRole)) in self._pinned:
            self.set_error("Pinned columns cannot be moved")
            return False
        minimum = len(self._pinned)
        target = max(minimum, min(self.list_widget.count() - 1, row + delta))
        if target == row:
            return False
        item = self.list_widget.takeItem(row)
        self.list_widget.insertItem(target, item)
        self.list_widget.setCurrentRow(target)
        self.set_error("")
        return True


__all__ = ["ColumnsPanel"]
