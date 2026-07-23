"""Draft-only ordered multi-sort editor for the Qt Table workspace."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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
    """Edit one sort key and request only local list operations."""

    removeRequested = Signal(object)
    moveUpRequested = Signal(object)
    moveDownRequested = Signal(object)

    def __init__(self, columns: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sortRuleRow")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        self.priority_label = QLabel("", self)
        self.priority_label.setAccessibleName("排序优先级")
        self.column_combo = QComboBox(self)
        self.column_combo.setAccessibleName("排序列")
        for column in columns:
            self.column_combo.addItem(column, column)
        self.direction_combo = QComboBox(self)
        self.direction_combo.setAccessibleName("排序方向")
        self.direction_combo.addItem("升序", True)
        self.direction_combo.addItem("降序", False)
        self.move_up_button = QPushButton("上移", self)
        self.move_down_button = QPushButton("下移", self)
        self.remove_button = QPushButton("删除", self)
        layout.addWidget(self.priority_label)
        layout.addWidget(self.column_combo, 2)
        layout.addWidget(self.direction_combo, 1)
        layout.addWidget(self.move_up_button)
        layout.addWidget(self.move_down_button)
        layout.addWidget(self.remove_button)
        self.remove_button.clicked.connect(
            lambda _checked=False: self.removeRequested.emit(self)
        )
        self.move_up_button.clicked.connect(
            lambda _checked=False: self.moveUpRequested.emit(self)
        )
        self.move_down_button.clicked.connect(
            lambda _checked=False: self.moveDownRequested.emit(self)
        )

    def set_priority(self, priority: int) -> None:
        self.priority_label.setText(str(priority))
        self.column_combo.setAccessibleName(f"排序优先级 {priority} 的列")
        self.direction_combo.setAccessibleName(f"排序优先级 {priority} 的方向")
        self.move_up_button.setAccessibleName(f"上移排序优先级 {priority}")
        self.move_down_button.setAccessibleName(f"下移排序优先级 {priority}")
        self.remove_button.setAccessibleName(f"删除排序优先级 {priority}")

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
    """Own a bounded sort draft without applying it to a table."""

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
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = QLabel("排序优先级", self)
        layout.addWidget(heading)
        hint = QLabel("从上到下依次应用排序规则。", self)
        hint.setWordWrap(True)
        layout.addWidget(hint)

        field_labels = QHBoxLayout()
        field_labels.setContentsMargins(8, 0, 8, 0)
        field_labels.addWidget(QLabel("优先级", self))
        field_labels.addWidget(QLabel("列", self), 2)
        field_labels.addWidget(QLabel("方向", self), 1)
        field_labels.addStretch(2)
        layout.addLayout(field_labels)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_host = QWidget(scroll)
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.addStretch(1)
        scroll.setWidget(self.rows_host)
        layout.addWidget(scroll, 1)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("sortError")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("排序草稿错误")
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        self.add_button = QPushButton("添加排序", self)
        self.clear_button = QPushButton("清空", self)
        self.add_button.setAccessibleName("添加排序规则")
        self.clear_button.setAccessibleName("清空排序规则")
        actions.addWidget(self.add_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.add_button.clicked.connect(lambda _checked=False: self.add_rule())
        self.clear_button.clicked.connect(
            lambda _checked=False: self.set_rules(())
        )

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def set_error(self, message: str) -> None:
        self.error_label.setText(str(message))
        self.error_label.setVisible(bool(message))

    def add_rule(self, rule: SortRule | None = None) -> SortRuleRow:
        if len(self.rule_rows) >= len(self._columns):
            raise ValueError("排序规则数不能超过列数")
        if rule is None:
            used = {existing.rule().column for existing in self.rule_rows}
            column = next(column for column in self._columns if column not in used)
            rule = SortRule(column)
        elif not isinstance(rule, SortRule):
            raise TypeError("SortPanel rows require SortRule values")
        row = SortRuleRow(self._columns, self.rows_host)
        row.removeRequested.connect(self.remove_rule)
        row.moveUpRequested.connect(lambda current: self.move_rule(current, -1))
        row.moveDownRequested.connect(lambda current: self.move_rule(current, 1))
        row.set_rule(rule)
        self.rule_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self.set_error("")
        self._refresh_rows()
        return row

    def remove_rule(self, row: SortRuleRow) -> None:
        if row not in self.rule_rows:
            return
        self.rule_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self.set_error("")
        self._refresh_rows()

    def move_rule(self, row: SortRuleRow, delta: int) -> bool:
        if row not in self.rule_rows or delta not in {-1, 1}:
            return False
        current = self.rule_rows.index(row)
        target = current + delta
        if target < 0 or target >= len(self.rule_rows):
            return False
        self.rule_rows[current], self.rule_rows[target] = (
            self.rule_rows[target],
            self.rule_rows[current],
        )
        for index, current_row in enumerate(self.rule_rows):
            self.rows_layout.insertWidget(index, current_row)
        self.set_error("")
        self._refresh_rows()
        return True

    def set_rules(self, rules: tuple[SortRule, ...]) -> None:
        rules = tuple(rules)
        if len(rules) > len(self._columns):
            raise ValueError("排序规则数不能超过列数")
        if not all(isinstance(rule, SortRule) for rule in rules):
            raise TypeError("SortPanel rows require SortRule values")
        unknown = next(
            (rule.column for rule in rules if rule.column not in self._columns),
            None,
        )
        if unknown is not None:
            raise ValueError(f"Unknown sort column: {unknown}")
        for row in self.rule_rows:
            row.setParent(None)
            row.deleteLater()
        self.rule_rows.clear()
        for rule in rules:
            self.add_rule(rule)
        self.set_error("")
        self._refresh_rows()

    def rules(self) -> tuple[SortRule, ...]:
        return tuple(row.rule() for row in self.rule_rows)

    def _refresh_rows(self) -> None:
        for index, row in enumerate(self.rule_rows):
            row.set_priority(index + 1)
            row.move_up_button.setEnabled(index > 0)
            row.move_down_button.setEnabled(index < len(self.rule_rows) - 1)
        self.add_button.setEnabled(len(self.rule_rows) < len(self._columns))
        self.clear_button.setEnabled(bool(self.rule_rows))


__all__ = ["SortPanel", "SortRuleRow"]
