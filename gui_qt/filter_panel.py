"""Typed visual filter editor for the Qt Table workspace."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from models.table_view_state import ColumnKind, ColumnProfile, FilterCondition


_OPERATORS_BY_KIND = {
    ColumnKind.TEXT: ("==", "!=", "contains", "startswith", "endswith", "in", "not_in", "isna", "notna"),
    ColumnKind.NUMBER: ("==", "!=", ">", ">=", "<", "<=", "between", "in", "not_in", "isna", "notna"),
    ColumnKind.BOOLEAN: ("==", "!=", "isna", "notna"),
    ColumnKind.DATETIME: ("==", "!=", ">", ">=", "<", "<=", "between", "in", "not_in", "isna", "notna"),
}

_OPERATOR_LABELS = {
    "==": "is",
    "!=": "is not",
    "contains": "contains",
    "startswith": "starts with",
    "endswith": "ends with",
    ">": "greater than",
    ">=": "at least",
    "<": "less than",
    "<=": "at most",
    "between": "between",
    "in": "is one of",
    "not_in": "is not one of",
    "isna": "is empty",
    "notna": "is not empty",
}


def operator_label(code: str) -> str:
    return _OPERATOR_LABELS.get(code, code)


class FilterConditionRow(QFrame):
    """One type-aware condition; it contains no query implementation."""

    removeRequested = Signal(object)

    def __init__(
        self,
        profiles: tuple[ColumnProfile, ...],
        condition_id: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not profiles:
            raise ValueError("FilterConditionRow requires at least one column profile")
        self.setObjectName("filterConditionRow")
        self._profiles = {profile.name: profile for profile in profiles}
        self.condition_id = condition_id
        self.operator_codes: tuple[str, ...] = ()
        self.value_control_kind = "literal"
        self._build_ui()
        self._populate_columns()
        self._refresh_operators()

    @property
    def profile(self) -> ColumnProfile:
        return self._profiles[self.column_combo.currentData()]

    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        self.column_combo = QComboBox(self)
        self.column_combo.setObjectName("filterColumn")
        self.operator_combo = QComboBox(self)
        self.operator_combo.setObjectName("filterOperator")
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setObjectName("removeFilterCondition")
        self.remove_button.setAccessibleName("Remove filter condition")

        self.value_stack = QStackedWidget(self)
        self.literal_edit = QLineEdit(self.value_stack)
        self.literal_edit.setObjectName("filterValue")
        self.choice_combo = QComboBox(self.value_stack)
        self.choice_combo.setObjectName("filterChoice")
        self.choice_combo.setEditable(False)
        self.membership_edit = QLineEdit(self.value_stack)
        self.membership_edit.setObjectName("filterMembership")
        self.membership_edit.setPlaceholderText("Comma-separated values")
        self.range_widget = QWidget(self.value_stack)
        range_layout = QHBoxLayout(self.range_widget)
        range_layout.setContentsMargins(0, 0, 0, 0)
        self.range_start = QLineEdit(self.range_widget)
        self.range_start.setObjectName("filterRangeStart")
        self.range_start.setPlaceholderText("From")
        self.range_end = QLineEdit(self.range_widget)
        self.range_end.setObjectName("filterRangeEnd")
        self.range_end.setPlaceholderText("To")
        range_layout.addWidget(self.range_start)
        range_layout.addWidget(QLabel("to", self.range_widget))
        range_layout.addWidget(self.range_end)
        self.no_value_label = QLabel("No value required", self.value_stack)

        for widget in (
            self.literal_edit,
            self.choice_combo,
            self.membership_edit,
            self.range_widget,
            self.no_value_label,
        ):
            self.value_stack.addWidget(widget)

        layout.addWidget(self.column_combo, 0, 0)
        layout.addWidget(self.operator_combo, 0, 1)
        layout.addWidget(self.remove_button, 0, 2)
        layout.addWidget(self.value_stack, 1, 0, 1, 3)
        layout.setColumnStretch(0, 2)
        layout.setColumnStretch(1, 2)

        self.column_combo.currentIndexChanged.connect(self._refresh_operators)
        self.operator_combo.currentIndexChanged.connect(self._refresh_value_control)
        self.remove_button.clicked.connect(lambda: self.removeRequested.emit(self))

    def _populate_columns(self) -> None:
        for profile in self._profiles.values():
            self.column_combo.addItem(profile.name, profile.name)

    def _refresh_operators(self) -> None:
        current = self.operator_combo.currentData()
        self.operator_codes = _OPERATORS_BY_KIND[self.profile.kind]
        self.operator_combo.blockSignals(True)
        self.operator_combo.clear()
        for code in self.operator_codes:
            self.operator_combo.addItem(operator_label(code), code)
        selected = self.operator_combo.findData(current)
        self.operator_combo.setCurrentIndex(selected if selected >= 0 else 0)
        self.operator_combo.blockSignals(False)
        self._refresh_value_control()

    def _refresh_value_control(self) -> None:
        operator = self.operator_combo.currentData()
        profile = self.profile
        if operator in {"isna", "notna"}:
            self.value_control_kind = "none"
            self.value_stack.setCurrentWidget(self.no_value_label)
        elif operator == "between":
            self.value_control_kind = "range"
            self.value_stack.setCurrentWidget(self.range_widget)
        elif operator in {"in", "not_in"}:
            self.value_control_kind = "multi"
            self.value_stack.setCurrentWidget(self.membership_edit)
        elif operator in {"==", "!="} and (profile.kind == ColumnKind.BOOLEAN or profile.values):
            self.value_control_kind = "choice"
            self.choice_combo.clear()
            values = (True, False) if profile.kind == ColumnKind.BOOLEAN else profile.values
            for value in values:
                self.choice_combo.addItem(str(value), value)
            self.value_stack.setCurrentWidget(self.choice_combo)
        else:
            self.value_control_kind = "literal"
            if profile.kind == ColumnKind.NUMBER:
                self.literal_edit.setPlaceholderText("Number")
            elif profile.kind == ColumnKind.DATETIME:
                self.literal_edit.setPlaceholderText("YYYY-MM-DD or date-time")
            else:
                self.literal_edit.setPlaceholderText("Value")
            self.value_stack.setCurrentWidget(self.literal_edit)

    def set_column(self, column: str) -> None:
        index = self.column_combo.findData(column)
        if index < 0:
            raise ValueError(f"Unknown filter column: {column}")
        self.column_combo.setCurrentIndex(index)

    def set_operator(self, operator: str) -> None:
        index = self.operator_combo.findData(operator)
        if index < 0:
            raise ValueError(f"Operator is not valid for the selected column: {operator}")
        self.operator_combo.setCurrentIndex(index)

    def set_value(self, value: Any) -> None:
        if self.value_control_kind == "none":
            return
        if self.value_control_kind == "range":
            values = tuple(value or ()) if not isinstance(value, str) else tuple(value.split(",", 1))
            if len(values) == 2:
                self.range_start.setText(str(values[0]))
                self.range_end.setText(str(values[1]))
            return
        if self.value_control_kind == "multi":
            values = value if isinstance(value, (tuple, list)) else (value,)
            self.membership_edit.setText(", ".join(str(item) for item in values if item is not None))
            return
        if self.value_control_kind == "choice":
            for index in range(self.choice_combo.count()):
                if self.choice_combo.itemData(index) == value or str(self.choice_combo.itemData(index)) == str(value):
                    self.choice_combo.setCurrentIndex(index)
                    return
            return
        self.literal_edit.setText("" if value is None else str(value))

    def set_condition(self, condition: FilterCondition) -> None:
        self.condition_id = condition.condition_id or self.condition_id
        self.set_column(condition.column)
        self.set_operator(condition.operator)
        self.set_value(condition.value)

    def is_blank(self) -> bool:
        if self.value_control_kind == "none" or self.value_control_kind == "choice":
            return False
        if self.value_control_kind == "range":
            return not self.range_start.text().strip() and not self.range_end.text().strip()
        if self.value_control_kind == "multi":
            return not self.membership_edit.text().strip()
        return not self.literal_edit.text().strip()

    def condition(self) -> FilterCondition:
        operator = str(self.operator_combo.currentData())
        value: Any = None
        if self.value_control_kind == "range":
            value = (self.range_start.text().strip(), self.range_end.text().strip())
        elif self.value_control_kind == "multi":
            value = tuple(part.strip() for part in self.membership_edit.text().split(",") if part.strip())
        elif self.value_control_kind == "choice":
            value = self.choice_combo.currentData()
        elif self.value_control_kind == "literal":
            value = self.literal_edit.text()
        return FilterCondition(
            column=str(self.column_combo.currentData()),
            operator=operator,
            value=value,
            condition_id=self.condition_id,
        )


class FilterPanel(QWidget):
    """Own condition widgets for one draft; applied state lives elsewhere."""

    applyRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, profiles: tuple[ColumnProfile, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not profiles:
            raise ValueError("FilterPanel requires at least one column profile")
        self.setObjectName("filterPanel")
        self._profiles = tuple(profiles)
        self._sequence = 0
        self.condition_rows: list[FilterConditionRow] = []
        self._build_ui()
        self.add_condition()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        heading = QLabel("Match all conditions", self)
        heading.setObjectName("panelHeading")
        layout.addWidget(heading)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        self.rows_host = QWidget(scroll)
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.addStretch(1)
        scroll.setWidget(self.rows_host)
        layout.addWidget(scroll, 1)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("filterError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        self.add_button = QPushButton("Add condition", self)
        self.clear_button = QPushButton("Clear", self)
        self.cancel_button = QPushButton("Cancel", self)
        self.apply_button = QPushButton("Apply", self)
        self.apply_button.setObjectName("primaryAction")
        actions.addWidget(self.add_button)
        actions.addWidget(self.clear_button)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.apply_button)
        layout.addLayout(actions)

        self.add_button.clicked.connect(self.add_condition)
        self.clear_button.clicked.connect(self.clear_conditions)
        self.cancel_button.clicked.connect(self.cancelRequested.emit)
        self.apply_button.clicked.connect(self.applyRequested.emit)

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def set_error(self, message: str) -> None:
        self.error_label.setText(message)

    def _next_id(self) -> str:
        self._sequence += 1
        return f"filter-{self._sequence}"

    def add_condition(self, condition: FilterCondition | None = None) -> FilterConditionRow:
        condition_id = condition.condition_id if condition and condition.condition_id else self._next_id()
        row = FilterConditionRow(self._profiles, condition_id, self.rows_host)
        row.removeRequested.connect(self.remove_condition)
        if condition is not None:
            row.set_condition(condition)
        self.condition_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        return row

    def remove_condition(self, row: FilterConditionRow) -> None:
        if row not in self.condition_rows:
            return
        self.condition_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        if not self.condition_rows:
            self.add_condition()

    def clear_conditions(self) -> None:
        self.set_conditions(())
        self.set_error("")

    def set_conditions(self, conditions: tuple[FilterCondition, ...]) -> None:
        for row in self.condition_rows:
            row.setParent(None)
            row.deleteLater()
        self.condition_rows.clear()
        for condition in conditions:
            self.add_condition(condition)
        if not self.condition_rows:
            self.add_condition()

    def conditions(self) -> tuple[FilterCondition, ...]:
        return tuple(row.condition() for row in self.condition_rows if not row.is_blank())


__all__ = ["FilterConditionRow", "FilterPanel", "operator_label"]
