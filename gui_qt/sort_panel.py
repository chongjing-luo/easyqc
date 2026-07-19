"""Ordered multi-sort editor for the Qt Table workspace."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from models.table_view_state import SortRule


class SortRuleRow(QFrame):
    removeRequested = Signal(object)

    def __init__(self, columns: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sortRuleRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        self.column_combo = QComboBox(self)
        for column in columns:
            self.column_combo.addItem(column, column)
        self.direction_combo = QComboBox(self)
        self.direction_combo.addItem("Ascending", True)
        self.direction_combo.addItem("Descending", False)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setAccessibleName("Remove sort rule")
        layout.addWidget(self.column_combo, 2)
        layout.addWidget(self.direction_combo, 1)
        layout.addWidget(self.remove_button)
        self.remove_button.clicked.connect(lambda: self.removeRequested.emit(self))

    def set_rule(self, rule: SortRule) -> None:
        column_index = self.column_combo.findData(rule.column)
        if column_index < 0:
            raise ValueError(f"Unknown sort column: {rule.column}")
        self.column_combo.setCurrentIndex(column_index)
        self.direction_combo.setCurrentIndex(0 if rule.ascending else 1)

    def rule(self) -> SortRule:
        return SortRule(
            column=str(self.column_combo.currentData()),
            ascending=bool(self.direction_combo.currentData()),
        )


class SortPanel(QWidget):
    applyRequested = Signal(object)

    def __init__(self, columns: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not columns:
            raise ValueError("SortPanel requires at least one column")
        self.setObjectName("sortPanel")
        self._columns = tuple(columns)
        self.rule_rows: list[SortRuleRow] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        heading = QLabel("Sort priority", self)
        heading.setObjectName("panelHeading")
        layout.addWidget(heading)
        explanation = QLabel("Rules are applied from top to bottom.", self)
        explanation.setObjectName("panelHint")
        layout.addWidget(explanation)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.rows_host = QWidget(scroll)
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.addStretch(1)
        scroll.setWidget(self.rows_host)
        layout.addWidget(scroll, 1)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("sortError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        self.add_button = QPushButton("Add sort", self)
        self.clear_button = QPushButton("Clear", self)
        self.apply_button = QPushButton("Apply", self)
        self.apply_button.setObjectName("primaryAction")
        actions.addWidget(self.add_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)
        self.add_button.clicked.connect(lambda: self.add_rule())
        self.clear_button.clicked.connect(lambda: self.set_rules(()))
        self.apply_button.clicked.connect(lambda: self.applyRequested.emit(self.rules()))

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def set_error(self, message: str) -> None:
        self.error_label.setText(message)

    def add_rule(self, rule: SortRule | None = None) -> SortRuleRow:
        row = SortRuleRow(self._columns, self.rows_host)
        row.removeRequested.connect(self.remove_rule)
        if rule is not None:
            row.set_rule(rule)
        self.rule_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        return row

    def remove_rule(self, row: SortRuleRow) -> None:
        if row not in self.rule_rows:
            return
        self.rule_rows.remove(row)
        row.setParent(None)
        row.deleteLater()

    def set_rules(self, rules: tuple[SortRule, ...]) -> None:
        for row in self.rule_rows:
            row.setParent(None)
            row.deleteLater()
        self.rule_rows.clear()
        for rule in rules:
            self.add_rule(rule)

    def rules(self) -> tuple[SortRule, ...]:
        return tuple(row.rule() for row in self.rule_rows)


__all__ = ["SortPanel", "SortRuleRow"]
