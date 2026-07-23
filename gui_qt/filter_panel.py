"""Grouped filter draft editor used by the Qt Filter dialog."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gui_qt.filter_condition_row import FilterConditionRow, operator_label
from models.table_view_state import (
    MAX_FILTER_CONDITIONS,
    MAX_FILTER_CONDITIONS_PER_GROUP,
    MAX_FILTER_GROUPS,
    ColumnProfile,
    FilterCondition,
    FilterExpression,
    FilterGroup,
)


class FilterGroupEditor(QGroupBox):
    """Edit one stable-ID filter group without running a query."""

    removeRequested = Signal(object)
    conditionCountChanged = Signal()

    def __init__(
        self,
        profiles: tuple[ColumnProfile, ...],
        group_id: str,
        condition_id_factory: Callable[[], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not group_id.strip():
            raise ValueError("FilterGroupEditor requires a group_id")
        self._profiles = profiles
        self._condition_id_factory = condition_id_factory
        self.group_id = group_id
        self._total_add_allowed = True
        self.condition_rows: list[FilterConditionRow] = []
        self.setObjectName("filterGroup")
        self._build_ui()
        self.add_condition()

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def _build_ui(self) -> None:
        self.setTitle("筛选组")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(QLabel("组内关系", self))
        self.join_combo = QComboBox(self)
        self.join_combo.addItem("满足全部", "all")
        self.join_combo.addItem("满足任一", "any")
        self.join_combo.setAccessibleName(f"筛选组 {self.group_id} 的组内关系")
        self.remove_group_button = QPushButton("删除组", self)
        self.remove_group_button.setAccessibleName(f"删除筛选组 {self.group_id}")
        header.addWidget(self.join_combo)
        header.addStretch(1)
        header.addWidget(self.remove_group_button)
        layout.addLayout(header)

        self.rows_host = QWidget(self)
        self.rows_layout = QVBoxLayout(self.rows_host)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(6)
        self.rows_layout.addStretch(1)
        layout.addWidget(self.rows_host)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("filterGroupError")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName(f"筛选组 {self.group_id} 错误")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.add_condition_button = QPushButton("添加条件", self)
        self.add_condition_button.setAccessibleName(
            f"向筛选组 {self.group_id} 添加条件"
        )
        layout.addWidget(self.add_condition_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.add_condition_button.clicked.connect(
            lambda _checked=False: self.add_condition()
        )
        self.remove_group_button.clicked.connect(lambda: self.removeRequested.emit(self))

    def add_condition(
        self, condition: FilterCondition | None = None
    ) -> FilterConditionRow:
        if len(self.condition_rows) >= MAX_FILTER_CONDITIONS_PER_GROUP:
            raise ValueError(
                f"每个筛选组最多包含 {MAX_FILTER_CONDITIONS_PER_GROUP} 个条件"
            )
        condition_id = (
            condition.condition_id
            if condition is not None and condition.condition_id
            else self._condition_id_factory()
        )
        row = FilterConditionRow(self._profiles, condition_id, self.rows_host)
        row.removeRequested.connect(self.remove_condition)
        if condition is not None:
            row.set_condition(condition)
        self.condition_rows.append(row)
        self.rows_layout.insertWidget(self.rows_layout.count() - 1, row)
        self._update_limit_state()
        self.conditionCountChanged.emit()
        return row

    def remove_condition(self, row: FilterConditionRow) -> None:
        if row not in self.condition_rows:
            return
        self.condition_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        if not self.condition_rows:
            self.add_condition()
        self._update_limit_state()
        self.conditionCountChanged.emit()

    def set_group(self, group: FilterGroup) -> None:
        self.group_id = group.group_id
        self.join_combo.setAccessibleName(f"筛选组 {self.group_id} 的组内关系")
        self.remove_group_button.setAccessibleName(f"删除筛选组 {self.group_id}")
        self.add_condition_button.setAccessibleName(
            f"向筛选组 {self.group_id} 添加条件"
        )
        self.error_label.setAccessibleName(f"筛选组 {self.group_id} 错误")
        index = self.join_combo.findData(group.join)
        if index < 0:
            raise ValueError(f"Unknown filter group join: {group.join}")
        self.join_combo.setCurrentIndex(index)
        for row in self.condition_rows:
            row.setParent(None)
            row.deleteLater()
        self.condition_rows.clear()
        for condition in group.conditions:
            self.add_condition(condition)
        if not self.condition_rows:
            self.add_condition()
        self._update_limit_state()

    def set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))
        if message:
            self.join_combo.setFocus(Qt.FocusReason.OtherFocusReason)

    def conditions(self) -> tuple[FilterCondition, ...]:
        return tuple(row.condition() for row in self.condition_rows if not row.is_blank())

    def group(self) -> FilterGroup:
        return FilterGroup(
            group_id=self.group_id,
            join=str(self.join_combo.currentData()),
            conditions=self.conditions(),
        )

    def _update_limit_state(self) -> None:
        self.add_condition_button.setEnabled(
            self._total_add_allowed
            and len(self.condition_rows) < MAX_FILTER_CONDITIONS_PER_GROUP
        )

    def set_total_add_allowed(self, allowed: bool) -> None:
        self._total_add_allowed = bool(allowed)
        self._update_limit_state()


class FilterPanel(QWidget):
    """Own a grouped filter draft; applied state and query execution live elsewhere."""

    def __init__(self, profiles: tuple[ColumnProfile, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not profiles:
            raise ValueError("FilterPanel requires at least one column profile")
        self.setObjectName("filterPanel")
        self.setAccessibleName("分组筛选编辑器")
        self._profiles = tuple(profiles)
        self._group_sequence = 0
        self._condition_sequence = 0
        self._used_group_ids: set[str] = set()
        self._used_condition_ids: set[str] = set()
        self.group_editors: list[FilterGroupEditor] = []
        self._build_ui()
        self.add_group()

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    @property
    def condition_rows(self) -> list[FilterConditionRow]:
        return self.group_editors[0].condition_rows if self.group_editors else []

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        top = QHBoxLayout()
        top.addWidget(QLabel("组间关系", self))
        self.top_join_combo = QComboBox(self)
        self.top_join_combo.setObjectName("filterTopJoin")
        self.top_join_combo.addItem("满足全部组", "all")
        self.top_join_combo.addItem("满足任一组", "any")
        self.top_join_combo.setAccessibleName("筛选组之间的关系")
        top.addWidget(self.top_join_combo)
        top.addStretch(1)
        self.add_group_button = QPushButton("添加组", self)
        self.add_group_button.setAccessibleName("添加筛选组")
        top.addWidget(self.add_group_button)
        layout.addLayout(top)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.groups_host = QWidget(self.scroll)
        self.groups_layout = QVBoxLayout(self.groups_host)
        self.groups_layout.setContentsMargins(0, 0, 0, 0)
        self.groups_layout.setSpacing(10)
        self.groups_layout.addStretch(1)
        self.scroll.setWidget(self.groups_host)
        layout.addWidget(self.scroll, 1)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("filterDialogError")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("筛选编辑错误")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)
        self.add_group_button.clicked.connect(
            lambda _checked=False: self.add_group()
        )

    def _next_group_id(self) -> str:
        while True:
            self._group_sequence += 1
            candidate = f"group-{self._group_sequence}"
            if candidate not in self._used_group_ids:
                self._used_group_ids.add(candidate)
                return candidate

    def _next_condition_id(self) -> str:
        while True:
            self._condition_sequence += 1
            candidate = f"filter-{self._condition_sequence}"
            if candidate not in self._used_condition_ids:
                self._used_condition_ids.add(candidate)
                return candidate

    def add_group(self, group: FilterGroup | None = None) -> FilterGroupEditor:
        if len(self.group_editors) >= MAX_FILTER_GROUPS:
            raise ValueError(f"筛选最多包含 {MAX_FILTER_GROUPS} 个组")
        incoming_count = max(1, len(group.conditions) if group is not None else 1)
        current_count = sum(
            len(editor.condition_rows) for editor in self.group_editors
        )
        if current_count + incoming_count > MAX_FILTER_CONDITIONS:
            raise ValueError(
                f"筛选最多包含 {MAX_FILTER_CONDITIONS} 个条件"
            )
        group_id = group.group_id if group is not None else self._next_group_id()
        self._used_group_ids.add(group_id)
        if group is not None:
            self._used_condition_ids.update(
                condition.condition_id
                for condition in group.conditions
                if condition.condition_id
            )
        editor = FilterGroupEditor(
            self._profiles,
            group_id,
            self._next_condition_id,
            self.groups_host,
        )
        editor.removeRequested.connect(self.remove_group)
        if group is not None:
            editor.set_group(group)
        self.group_editors.append(editor)
        editor.conditionCountChanged.connect(self._update_condition_limits)
        self.groups_layout.insertWidget(self.groups_layout.count() - 1, editor)
        self._update_group_titles()
        self._update_condition_limits()
        return editor

    def remove_group(self, editor: FilterGroupEditor) -> None:
        if editor not in self.group_editors:
            return
        self.group_editors.remove(editor)
        editor.setParent(None)
        editor.deleteLater()
        if not self.group_editors:
            self.add_group()
        self._update_group_titles()
        self._update_condition_limits()

    def set_expression(self, expression: FilterExpression) -> None:
        if not isinstance(expression, FilterExpression):
            raise TypeError("FilterPanel requires a FilterExpression")
        self.clear_errors()
        self._used_group_ids = {group.group_id for group in expression.groups}
        self._used_condition_ids = {
            condition.condition_id
            for group in expression.groups
            for condition in group.conditions
            if condition.condition_id
        }
        index = self.top_join_combo.findData(expression.group_join)
        if index < 0:
            raise ValueError(f"Unknown top-level filter join: {expression.group_join}")
        self.top_join_combo.setCurrentIndex(index)
        for editor in self.group_editors:
            editor.setParent(None)
            editor.deleteLater()
        self.group_editors.clear()
        for group in expression.groups:
            self.add_group(group)
        if not self.group_editors:
            self.add_group()
        self._update_group_titles()
        self._update_condition_limits()

    def expression(self) -> FilterExpression:
        groups: list[FilterGroup] = []
        for editor in self.group_editors:
            group = editor.group()
            if group.conditions:
                groups.append(group)
        if sum(len(group.conditions) for group in groups) > MAX_FILTER_CONDITIONS:
            raise ValueError(f"筛选最多包含 {MAX_FILTER_CONDITIONS} 个条件")
        return FilterExpression(
            group_join=str(self.top_join_combo.currentData()),
            groups=tuple(groups),
        )

    def reset_draft(self) -> None:
        self.set_expression(FilterExpression())

    def clear_errors(self) -> None:
        self.error_label.setText("")
        self.error_label.setVisible(False)
        for group in self.group_editors:
            group.set_error("")
            for row in group.condition_rows:
                row.set_error("")

    def set_error(
        self,
        message: str,
        *,
        group_id: str | None = None,
        condition_id: str | None = None,
    ) -> None:
        self.clear_errors()
        if condition_id is not None:
            for group in self.group_editors:
                for row in group.condition_rows:
                    if row.condition_id == condition_id and (
                        group_id is None or group.group_id == group_id
                    ):
                        row.set_error(message)
                        self.scroll.ensureWidgetVisible(row)
                        return
        if group_id is not None:
            for group in self.group_editors:
                if group.group_id == group_id:
                    group.set_error(message)
                    self.scroll.ensureWidgetVisible(group)
                    return
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))
        if message:
            self.error_label.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_group_titles(self) -> None:
        for index, editor in enumerate(self.group_editors, start=1):
            editor.setTitle(f"第 {index} 组")

    def _update_condition_limits(self) -> None:
        condition_count = sum(
            len(group.condition_rows) for group in self.group_editors
        )
        can_add = condition_count < MAX_FILTER_CONDITIONS
        for group in self.group_editors:
            group.set_total_add_allowed(can_add)
        self.add_group_button.setEnabled(
            len(self.group_editors) < MAX_FILTER_GROUPS and can_add
        )

    # Stage 5 flat-draft compatibility surface. The dialog uses expressions.
    def add_condition(self, condition: FilterCondition | None = None) -> FilterConditionRow:
        return self.group_editors[0].add_condition(condition)

    def clear_conditions(self) -> None:
        self.set_conditions(())

    def set_conditions(self, conditions: tuple[FilterCondition, ...]) -> None:
        group = FilterGroup("legacy-flat", "all", tuple(conditions))
        self.set_expression(
            FilterExpression("all", (group,)) if conditions else FilterExpression()
        )

    def conditions(self) -> tuple[FilterCondition, ...]:
        return tuple(
            condition
            for group in self.expression().groups
            for condition in group.conditions
        )


__all__ = [
    "FilterConditionRow",
    "FilterGroupEditor",
    "FilterPanel",
    "operator_label",
]
