"""One type-aware condition row for the grouped Qt filter editor."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QDateTime, QLocale, QSize, Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateTimeEdit,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from models.table_view_state import ColumnKind, ColumnProfile, FilterCondition


_OPERATORS_BY_KIND = {
    ColumnKind.TEXT: (
        "==",
        "!=",
        "contains",
        "startswith",
        "endswith",
        "in",
        "not_in",
        "isna",
        "notna",
    ),
    ColumnKind.NUMBER: (
        "==",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "between",
        "in",
        "not_in",
        "isna",
        "notna",
    ),
    ColumnKind.BOOLEAN: ("==", "!=", "isna", "notna"),
    ColumnKind.DATETIME: (
        "==",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "between",
        "in",
        "not_in",
        "isna",
        "notna",
    ),
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


def _number_validator(parent: QWidget) -> QDoubleValidator:
    """Match the dot-decimal format consumed by Core's numeric parser."""

    validator = QDoubleValidator(parent)
    locale = QLocale.c()
    locale.setNumberOptions(
        locale.numberOptions() | QLocale.NumberOption.RejectGroupSeparator
    )
    validator.setLocale(locale)
    return validator


class _CurrentEditorStack(QStackedWidget):
    """Size the stack for its visible editor, not its largest hidden editor."""

    def sizeHint(self) -> QSize:
        current = self.currentWidget()
        return current.sizeHint() if current is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        current = self.currentWidget()
        return (
            current.minimumSizeHint()
            if current is not None
            else super().minimumSizeHint()
        )

    def setCurrentWidget(self, widget: QWidget) -> None:
        super().setCurrentWidget(widget)
        self.updateGeometry()


class FilterConditionRow(QFrame):
    """Render one condition and return one GUI-independent condition value."""

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
        if not condition_id.strip():
            raise ValueError("FilterConditionRow requires a condition_id")
        self.setObjectName("filterConditionRow")
        self._profiles = {profile.name: profile for profile in profiles}
        self.condition_id = condition_id
        self._touched = False
        self.operator_codes: tuple[str, ...] = ()
        self.value_control_kind = "literal"
        self._build_ui()
        self._populate_columns()
        self._refresh_operators()
        self._set_accessible_names()

    @property
    def profile(self) -> ColumnProfile:
        return self._profiles[str(self.column_combo.currentData())]

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(5)

        self.enabled_checkbox = QCheckBox("Use", self)
        self.enabled_checkbox.setChecked(True)
        self.column_label = QLabel("Column", self)
        self.operator_label_widget = QLabel("Operator", self)
        self.value_label = QLabel("Value", self)
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.setObjectName("removeFilterCondition")
        self.column_combo = QComboBox(self)
        self.column_combo.setObjectName("filterColumn")
        self.operator_combo = QComboBox(self)
        self.operator_combo.setObjectName("filterOperator")

        self.value_stack = _CurrentEditorStack(self)
        self.literal_edit = QLineEdit(self.value_stack)
        self.literal_edit.setObjectName("filterValue")
        self.literal_edit.setClearButtonEnabled(True)
        self.choice_combo = QComboBox(self.value_stack)
        self.choice_combo.setObjectName("filterChoice")
        self.membership_edit = QPlainTextEdit(self.value_stack)
        self.membership_edit.setObjectName("filterMembership")
        self.membership_edit.setPlaceholderText("One value per line")
        self.membership_edit.setTabChangesFocus(True)

        self.range_widget = QWidget(self.value_stack)
        range_layout = QHBoxLayout(self.range_widget)
        range_layout.setContentsMargins(0, 0, 0, 0)
        range_layout.setSpacing(6)
        self.range_start = QLineEdit(self.range_widget)
        self.range_start.setObjectName("filterRangeStart")
        self.range_end = QLineEdit(self.range_widget)
        self.range_end.setObjectName("filterRangeEnd")
        range_layout.addWidget(QLabel("From", self.range_widget))
        range_layout.addWidget(self.range_start)
        range_layout.addWidget(QLabel("To", self.range_widget))
        range_layout.addWidget(self.range_end)

        self.datetime_edit = QDateTimeEdit(self.value_stack)
        self.datetime_edit.setObjectName("filterDateTime")
        self.datetime_edit.setCalendarPopup(True)
        self.datetime_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.datetime_range_widget = QWidget(self.value_stack)
        datetime_range_layout = QHBoxLayout(self.datetime_range_widget)
        datetime_range_layout.setContentsMargins(0, 0, 0, 0)
        datetime_range_layout.setSpacing(6)
        self.datetime_range_start = QDateTimeEdit(self.datetime_range_widget)
        self.datetime_range_end = QDateTimeEdit(self.datetime_range_widget)
        for editor in (self.datetime_range_start, self.datetime_range_end):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        datetime_range_layout.addWidget(QLabel("From", self.datetime_range_widget))
        datetime_range_layout.addWidget(self.datetime_range_start)
        datetime_range_layout.addWidget(QLabel("To", self.datetime_range_widget))
        datetime_range_layout.addWidget(self.datetime_range_end)
        self.no_value_label = QLabel("No value required", self.value_stack)

        for widget in (
            self.literal_edit,
            self.choice_combo,
            self.membership_edit,
            self.range_widget,
            self.datetime_edit,
            self.datetime_range_widget,
            self.no_value_label,
        ):
            self.value_stack.addWidget(widget)

        layout.addWidget(self.enabled_checkbox, 0, 0)
        layout.addWidget(self.column_label, 0, 1)
        layout.addWidget(self.operator_label_widget, 0, 2)
        layout.addWidget(self.value_label, 0, 3)
        layout.addWidget(self.remove_button, 0, 4)
        layout.addWidget(
            self.column_combo, 1, 1, Qt.AlignmentFlag.AlignTop
        )
        layout.addWidget(
            self.operator_combo, 1, 2, Qt.AlignmentFlag.AlignTop
        )
        layout.addWidget(self.value_stack, 1, 3, 1, 2)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("filterConditionError")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label, 2, 1, 1, 4)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(2, 2)
        layout.setColumnStretch(3, 3)

        self.column_combo.currentIndexChanged.connect(self._refresh_operators)
        self.operator_combo.currentIndexChanged.connect(self._refresh_value_control)
        self.column_combo.activated.connect(self._mark_touched)
        self.operator_combo.activated.connect(self._mark_touched)
        self.enabled_checkbox.toggled.connect(self._mark_touched)
        self.literal_edit.textEdited.connect(self._mark_touched)
        self.choice_combo.activated.connect(self._mark_touched)
        self.membership_edit.textChanged.connect(self._mark_touched)
        self.range_start.textEdited.connect(self._mark_touched)
        self.range_end.textEdited.connect(self._mark_touched)
        self.datetime_edit.dateTimeChanged.connect(self._mark_touched)
        self.datetime_range_start.dateTimeChanged.connect(self._mark_touched)
        self.datetime_range_end.dateTimeChanged.connect(self._mark_touched)
        self.remove_button.clicked.connect(lambda: self.removeRequested.emit(self))

    def _mark_touched(self, *_args: object) -> None:
        self._touched = True

    def _set_accessible_names(self) -> None:
        suffix = self.condition_id
        self.enabled_checkbox.setAccessibleName(f"Enable filter condition {suffix}")
        self.column_combo.setAccessibleName(f"Column for filter condition {suffix}")
        self.operator_combo.setAccessibleName(f"Operator for filter condition {suffix}")
        self.literal_edit.setAccessibleName(f"Value for filter condition {suffix}")
        self.choice_combo.setAccessibleName(f"Choice for filter condition {suffix}")
        self.membership_edit.setAccessibleName(
            f"Values for filter condition {suffix}, one per line"
        )
        self.range_start.setAccessibleName(f"Start value for filter condition {suffix}")
        self.range_end.setAccessibleName(f"End value for filter condition {suffix}")
        self.datetime_edit.setAccessibleName(f"Date and time for filter condition {suffix}")
        self.datetime_range_start.setAccessibleName(
            f"Start date and time for filter condition {suffix}"
        )
        self.datetime_range_end.setAccessibleName(
            f"End date and time for filter condition {suffix}"
        )
        self.remove_button.setAccessibleName(f"Remove filter condition {suffix}")
        self.error_label.setAccessibleName(f"Error for filter condition {suffix}")

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
        operator = str(self.operator_combo.currentData())
        profile = self.profile
        self.literal_edit.setValidator(None)
        self.literal_edit.setCompleter(None)
        self.range_start.setValidator(None)
        self.range_end.setValidator(None)

        if operator in {"isna", "notna"}:
            self.value_control_kind = "none"
            self.value_stack.setCurrentWidget(self.no_value_label)
        elif operator == "between":
            if profile.kind == ColumnKind.DATETIME:
                self.value_control_kind = "datetime_range"
                self.value_stack.setCurrentWidget(self.datetime_range_widget)
                return
            self.value_control_kind = "range"
            if profile.kind == ColumnKind.NUMBER:
                self.range_start.setValidator(_number_validator(self.range_start))
                self.range_end.setValidator(_number_validator(self.range_end))
                placeholder = "Number"
            else:
                placeholder = "Value"
            self.range_start.setPlaceholderText(placeholder)
            self.range_end.setPlaceholderText(placeholder)
            self.value_stack.setCurrentWidget(self.range_widget)
        elif operator in {"in", "not_in"}:
            self.value_control_kind = "multi"
            self.value_stack.setCurrentWidget(self.membership_edit)
        elif operator in {"==", "!="} and (
            profile.kind == ColumnKind.BOOLEAN or profile.values
        ):
            self.value_control_kind = "choice"
            self.choice_combo.clear()
            values = (True, False) if profile.kind == ColumnKind.BOOLEAN else profile.values
            for value in values:
                self.choice_combo.addItem(str(value), value)
            self.value_stack.setCurrentWidget(self.choice_combo)
        elif profile.kind == ColumnKind.DATETIME:
            self.value_control_kind = "datetime"
            self.value_stack.setCurrentWidget(self.datetime_edit)
        else:
            self.value_control_kind = "literal"
            if profile.kind == ColumnKind.NUMBER:
                self.literal_edit.setValidator(_number_validator(self.literal_edit))
                self.literal_edit.setPlaceholderText("Number")
            else:
                self.literal_edit.setPlaceholderText("Value")
                if profile.values:
                    completer = QCompleter(
                        [str(value) for value in profile.values],
                        self.literal_edit,
                    )
                    completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
                    self.literal_edit.setCompleter(completer)
            self.value_stack.setCurrentWidget(self.literal_edit)

    def set_column(self, column: str) -> None:
        index = self.column_combo.findData(column)
        if index < 0:
            raise ValueError(f"Unknown filter column: {column}")
        self.column_combo.setCurrentIndex(index)
        self._touched = True

    def set_operator(self, operator: str) -> None:
        index = self.operator_combo.findData(operator)
        if index < 0:
            raise ValueError(f"Operator is not valid for the selected column: {operator}")
        self.operator_combo.setCurrentIndex(index)
        self._touched = True

    def set_value(self, value: Any) -> None:
        self._touched = True
        if self.value_control_kind == "none":
            return
        if self.value_control_kind == "datetime_range":
            values = tuple(value or ()) if not isinstance(value, str) else ()
            if len(values) == 2:
                for editor, item in zip(
                    (self.datetime_range_start, self.datetime_range_end),
                    values,
                ):
                    parsed = QDateTime.fromString(str(item), Qt.DateFormat.ISODate)
                    if parsed.isValid():
                        editor.setDateTime(parsed)
            return
        if self.value_control_kind == "range":
            values = (
                tuple(value or ())
                if not isinstance(value, str)
                else tuple(value.split(",", 1))
            )
            if len(values) == 2:
                self.range_start.setText(str(values[0]))
                self.range_end.setText(str(values[1]))
            return
        if self.value_control_kind == "multi":
            values = value if isinstance(value, (tuple, list)) else (value,)
            self.membership_edit.setPlainText(
                "\n".join(str(item) for item in values if item is not None)
            )
            return
        if self.value_control_kind == "choice":
            for index in range(self.choice_combo.count()):
                item = self.choice_combo.itemData(index)
                if item == value or str(item) == str(value):
                    self.choice_combo.setCurrentIndex(index)
                    return
            self.choice_combo.addItem(str(value), value)
            self.choice_combo.setCurrentIndex(self.choice_combo.count() - 1)
            return
        if self.value_control_kind == "datetime":
            parsed = QDateTime.fromString(str(value), Qt.DateFormat.ISODate)
            if parsed.isValid():
                self.datetime_edit.setDateTime(parsed)
            return
        self.literal_edit.setText("" if value is None else str(value))

    def set_condition(self, condition: FilterCondition) -> None:
        self.condition_id = condition.condition_id or self.condition_id
        self._set_accessible_names()
        self.enabled_checkbox.setChecked(condition.enabled)
        self.set_column(condition.column)
        self.set_operator(condition.operator)
        self.set_value(condition.value)

    def set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))
        if message:
            self.column_combo.setFocus(Qt.FocusReason.OtherFocusReason)

    def is_blank(self) -> bool:
        if not self._touched:
            return True
        if self.value_control_kind in {
            "none",
            "choice",
            "datetime",
            "datetime_range",
        }:
            return False
        if self.value_control_kind == "range":
            return not self.range_start.text().strip() and not self.range_end.text().strip()
        if self.value_control_kind == "multi":
            return not self.membership_edit.toPlainText().strip()
        return not self.literal_edit.text().strip()

    def condition(self) -> FilterCondition:
        operator = str(self.operator_combo.currentData())
        value: Any = None
        if self.value_control_kind == "datetime_range":
            value = (
                self.datetime_range_start.dateTime().toPython(),
                self.datetime_range_end.dateTime().toPython(),
            )
        elif self.value_control_kind == "range":
            value = (self.range_start.text().strip(), self.range_end.text().strip())
        elif self.value_control_kind == "multi":
            value = tuple(
                line.strip()
                for line in self.membership_edit.toPlainText().splitlines()
                if line.strip()
            )
        elif self.value_control_kind == "choice":
            value = self.choice_combo.currentData()
        elif self.value_control_kind == "datetime":
            value = self.datetime_edit.dateTime().toPython()
        elif self.value_control_kind == "literal":
            text = self.literal_edit.text()
            if self.profile.kind == ColumnKind.NUMBER:
                try:
                    value = float(text)
                except ValueError:
                    value = text
            else:
                value = text
        return FilterCondition(
            column=str(self.column_combo.currentData()),
            operator=operator,
            value=value,
            condition_id=self.condition_id,
            enabled=self.enabled_checkbox.isChecked(),
        )


__all__ = ["FilterConditionRow", "operator_label"]
