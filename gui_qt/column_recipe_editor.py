"""Qt editor for safe, ordered derived-column recipes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gui_qt.i18n import protect_user_text
from models.column_recipe import ColumnRecipe, RecipeStep, RecipeValue


@dataclass(frozen=True, slots=True)
class _OperationUi:
    label: str
    fields: tuple[tuple[str, str, str], ...] = ()


OPERATION_UI: dict[str, _OperationUi] = {
    "trim": _OperationUi("文本 · 去除首尾空格"),
    "lower": _OperationUi("文本 · 转为小写"),
    "upper": _OperationUi("文本 · 转为大写"),
    "title": _OperationUi("文本 · 单词首字母大写"),
    "length": _OperationUi("文本 · 计算长度"),
    "replace_literal": _OperationUi(
        "文本 · 替换固定内容",
        (("old", "text", "查找"), ("new", "text_empty", "替换为")),
    ),
    "remove_prefix": _OperationUi(
        "文本 · 删除前缀", (("prefix", "text", "前缀"),)
    ),
    "remove_suffix": _OperationUi(
        "文本 · 删除后缀", (("suffix", "text", "后缀"),)
    ),
    "slice": _OperationUi(
        "提取 · 按位置截取",
        (("start", "optional_int", "起始位置"), ("end", "optional_int", "结束位置")),
    ),
    "split_take": _OperationUi(
        "提取 · 分隔后取一段",
        (("delimiter", "text", "分隔符"), ("index", "int", "段序号（从 0 开始）")),
    ),
    "before": _OperationUi(
        "提取 · 取分隔符之前", (("delimiter", "text", "分隔符"),)
    ),
    "after": _OperationUi(
        "提取 · 取分隔符之后", (("delimiter", "text", "分隔符"),)
    ),
    "between": _OperationUi(
        "提取 · 取两个标记之间",
        (("start", "text", "起始标记"), ("end", "text", "结束标记")),
    ),
    "path_name": _OperationUi("路径文本 · 文件名"),
    "path_parent": _OperationUi("路径文本 · 上级路径"),
    "path_suffix": _OperationUi("路径文本 · 扩展名"),
    "path_stem": _OperationUi("路径文本 · 去掉最后一个扩展名"),
    "prepend": _OperationUi(
        "组合 · 在前面添加", (("value", "value", "添加内容"),)
    ),
    "append": _OperationUi(
        "组合 · 在后面添加", (("value", "value", "添加内容"),)
    ),
    "to_number": _OperationUi("数值 · 转为数值"),
    "add": _OperationUi("数值 · 加", (("value", "value", "加数"),)),
    "subtract": _OperationUi("数值 · 减", (("value", "value", "减数"),)),
    "multiply": _OperationUi("数值 · 乘", (("value", "value", "乘数"),)),
    "divide": _OperationUi("数值 · 除", (("value", "value", "除数"),)),
    "absolute": _OperationUi("数值 · 绝对值"),
    "round": _OperationUi(
        "数值 · 四舍五入", (("digits", "int", "小数位数"),)
    ),
    "fill_missing": _OperationUi(
        "空值 · 填充空值", (("value", "value", "填充值"),)
    ),
    "conditional": _OperationUi(
        "条件 · 根据判断生成值",
        (
            ("operator", "operator", "判断方式"),
            ("compare_to", "value", "比较对象"),
            ("when_true", "value", "条件成立"),
            ("when_false", "value", "条件不成立"),
        ),
    ),
}


def _default_step(operation: str) -> RecipeStep:
    defaults: dict[str, Any] = {
        "replace_literal": {"old": "old", "new": "new"},
        "remove_prefix": {"prefix": "prefix"},
        "remove_suffix": {"suffix": ".nii.gz"},
        "slice": {"start": None, "end": None},
        "split_take": {"delimiter": "_", "index": 0},
        "before": {"delimiter": "_"},
        "after": {"delimiter": "_"},
        "between": {"start": "[", "end": "]"},
        "prepend": {"value": RecipeValue.literal("")},
        "append": {"value": RecipeValue.literal("")},
        "add": {"value": RecipeValue.literal(0)},
        "subtract": {"value": RecipeValue.literal(0)},
        "multiply": {"value": RecipeValue.literal(1)},
        "divide": {"value": RecipeValue.literal(1)},
        "round": {"digits": 0},
        "fill_missing": {"value": RecipeValue.literal("")},
        "conditional": {
            "operator": "eq",
            "compare_to": RecipeValue.literal(""),
            "when_true": RecipeValue.literal(""),
            "when_false": RecipeValue.current(),
        },
    }
    return RecipeStep.create(operation, **defaults.get(operation, {}))


class ValueSourceEditor(QWidget):
    """Edit one typed current/column/literal recipe operand."""

    def __init__(self, columns: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind_combo = QComboBox(self)
        self.kind_combo.addItem("当前步骤值", "current")
        self.kind_combo.addItem("其他列", "column")
        self.kind_combo.addItem("固定值", "literal")

        self.pages = QStackedWidget(self)
        self.pages.addWidget(QLabel("使用上一步的结果", self.pages))
        self.column_combo = QComboBox(self.pages)
        protect_user_text(self.column_combo, "items")
        for column in columns:
            self.column_combo.addItem(column, column)
        self.pages.addWidget(self.column_combo)

        literal_page = QWidget(self.pages)
        literal_layout = QHBoxLayout(literal_page)
        literal_layout.setContentsMargins(0, 0, 0, 0)
        self.literal_type_combo = QComboBox(literal_page)
        self.literal_type_combo.addItem("文本", "text")
        self.literal_type_combo.addItem("整数", "integer")
        self.literal_type_combo.addItem("小数", "decimal")
        self.literal_type_combo.addItem("布尔值", "boolean")
        self.literal_type_combo.addItem("空值", "blank")
        self.literal_edit = QLineEdit(literal_page)
        self.literal_edit.setPlaceholderText("输入固定值")
        self.boolean_combo = QComboBox(literal_page)
        self.boolean_combo.addItem("是", True)
        self.boolean_combo.addItem("否", False)
        literal_layout.addWidget(self.literal_type_combo)
        literal_layout.addWidget(self.literal_edit, 1)
        literal_layout.addWidget(self.boolean_combo)
        self.pages.addWidget(literal_page)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.kind_combo)
        layout.addWidget(self.pages, 1)
        self.kind_combo.currentIndexChanged.connect(self._sync_page)
        self.literal_type_combo.currentIndexChanged.connect(self._sync_literal_type)
        self._sync_page()
        self._sync_literal_type()

    def _sync_page(self) -> None:
        self.pages.setCurrentIndex(self.kind_combo.currentIndex())

    def _sync_literal_type(self) -> None:
        value_type = self.literal_type_combo.currentData()
        self.literal_edit.setVisible(value_type in {"text", "integer", "decimal"})
        self.boolean_combo.setVisible(value_type == "boolean")

    def set_value(self, value: RecipeValue) -> None:
        index = self.kind_combo.findData(value.kind)
        self.kind_combo.setCurrentIndex(max(0, index))
        if value.kind == "column":
            column_index = self.column_combo.findData(value.value)
            if column_index < 0:
                raise ValueError(f"未知引用列: {value.value}")
            self.column_combo.setCurrentIndex(column_index)
        elif value.kind == "literal":
            literal = value.value
            if literal is None:
                value_type = "blank"
            elif isinstance(literal, bool):
                value_type = "boolean"
                self.boolean_combo.setCurrentIndex(0 if literal else 1)
            elif isinstance(literal, int):
                value_type = "integer"
                self.literal_edit.setText(str(literal))
            elif isinstance(literal, float):
                value_type = "decimal"
                self.literal_edit.setText(str(literal))
            else:
                value_type = "text"
                self.literal_edit.setText(str(literal))
            self.literal_type_combo.setCurrentIndex(
                self.literal_type_combo.findData(value_type)
            )
        self._sync_page()
        self._sync_literal_type()

    def value(self) -> RecipeValue:
        kind = self.kind_combo.currentData()
        if kind == "current":
            return RecipeValue.current()
        if kind == "column":
            if self.column_combo.currentIndex() < 0:
                raise ValueError("没有可引用的列")
            return RecipeValue.column(str(self.column_combo.currentData()))
        value_type = self.literal_type_combo.currentData()
        text = self.literal_edit.text()
        if value_type == "blank":
            literal: object = None
        elif value_type == "boolean":
            literal = bool(self.boolean_combo.currentData())
        elif value_type == "integer":
            try:
                literal = int(text)
            except ValueError as exc:
                raise ValueError("固定值必须是整数") from exc
        elif value_type == "decimal":
            try:
                literal = float(text)
            except ValueError as exc:
                raise ValueError("固定值必须是数值") from exc
        else:
            literal = text
        return RecipeValue.literal(literal)


class StepParameterEditor(QWidget):
    """Edit parameters for one selected operation."""

    def __init__(self, columns: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = columns
        self._step: RecipeStep | None = None
        self._field_widgets: dict[str, QWidget] = {}
        self._value_editors: dict[str, ValueSourceEditor] = {}
        self._content: QWidget | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

    def set_step(self, step: RecipeStep | None) -> None:
        self._step = step
        self._field_widgets.clear()
        self._value_editors.clear()
        if self._content is not None:
            self._layout.removeWidget(self._content)
            self._content.deleteLater()
        self._content = QWidget(self)
        form = QFormLayout(self._content)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        if step is None:
            empty = QLabel(
                "添加并选择一个步骤后，可在这里设置参数。\n"
                "不添加步骤时会直接复制来源列。",
                self._content,
            )
            empty.setWordWrap(True)
            form.addRow(empty)
            self._layout.addWidget(self._content)
            return

        spec = OPERATION_UI[step.operation]
        for name, field_type, label in spec.fields:
            widget = self._create_field(field_type, step.parameters.get(name))
            self._field_widgets[name] = widget
            form.addRow(label, widget)
        if step.operation == "conditional":
            operator_combo = self._field_widgets["operator"]
            operator_combo.currentIndexChanged.connect(
                self._sync_conditional_compare
            )
            self._sync_conditional_compare()

        self.error_policy_combo = QComboBox(self._content)
        self.error_policy_combo.addItem("遇到错误时停止", "fail")
        self.error_policy_combo.addItem("错误行置空", "blank")
        self.error_policy_combo.addItem("错误行保留本步输入", "keep")
        self.error_policy_combo.setCurrentIndex(
            self.error_policy_combo.findData(step.on_error)
        )
        form.addRow("错误处理", self.error_policy_combo)
        self._layout.addWidget(self._content)

    def _create_field(self, field_type: str, value: object) -> QWidget:
        if field_type.startswith("text"):
            widget = QLineEdit(self._content)
            widget.setText("" if value is None else str(value))
            return widget
        if field_type == "int":
            widget = QSpinBox(self._content)
            widget.setRange(-1_000_000, 1_000_000)
            widget.setValue(int(value or 0))
            return widget
        if field_type == "optional_int":
            widget = QLineEdit(self._content)
            widget.setPlaceholderText("留空表示不限")
            widget.setText("" if value is None else str(value))
            return widget
        if field_type == "operator":
            widget = QComboBox(self._content)
            for label, operator in (
                ("等于", "eq"),
                ("不等于", "ne"),
                ("大于", "gt"),
                ("大于等于", "ge"),
                ("小于", "lt"),
                ("小于等于", "le"),
                ("包含", "contains"),
                ("以此开头", "starts_with"),
                ("以此结尾", "ends_with"),
                ("为空", "is_missing"),
                ("不为空", "not_missing"),
            ):
                widget.addItem(label, operator)
            widget.setCurrentIndex(widget.findData(value))
            return widget
        editor = ValueSourceEditor(self._columns, self._content)
        editor.set_value(value if isinstance(value, RecipeValue) else RecipeValue.literal(""))
        self._value_editors[self._field_name_for_editor(editor)] = editor
        return editor

    def _field_name_for_editor(self, editor: ValueSourceEditor) -> str:
        del editor
        spec = OPERATION_UI[self._step.operation] if self._step is not None else None
        if spec is None:
            return ""
        occupied = set(self._value_editors)
        return next(
            name
            for name, field_type, _label in spec.fields
            if field_type == "value" and name not in occupied
        )

    def set_value_parameter(self, name: str, value: RecipeValue) -> None:
        editor = self._value_editors.get(name)
        if editor is None:
            raise ValueError(f"当前步骤没有值参数: {name}")
        editor.set_value(value)

    def _sync_conditional_compare(self) -> None:
        operator = self._field_widgets["operator"].currentData()
        self._field_widgets["compare_to"].setEnabled(
            operator not in {"is_missing", "not_missing"}
        )

    def step(self) -> RecipeStep:
        if self._step is None:
            raise ValueError("尚未选择转换步骤")
        spec = OPERATION_UI[self._step.operation]
        parameters: dict[str, object] = {}
        for name, field_type, _label in spec.fields:
            if (
                self._step.operation == "conditional"
                and name == "compare_to"
                and self._field_widgets["operator"].currentData()
                in {"is_missing", "not_missing"}
            ):
                continue
            widget = self._field_widgets[name]
            if field_type.startswith("text"):
                parameters[name] = widget.text()
            elif field_type == "int":
                parameters[name] = widget.value()
            elif field_type == "optional_int":
                text = widget.text().strip()
                try:
                    parameters[name] = None if not text else int(text)
                except ValueError as exc:
                    raise ValueError(f"参数 {name} 必须是整数或留空") from exc
            elif field_type == "operator":
                parameters[name] = widget.currentData()
            else:
                parameters[name] = self._value_editors[name].value()
        return RecipeStep.create(
            self._step.operation,
            on_error=str(self.error_policy_combo.currentData()),
            **parameters,
        )


class ColumnRecipeEditor(QWidget):
    """Compose a source column and an ordered list of safe transformations."""

    stepsChanged = Signal()
    validationError = Signal(str)

    def __init__(self, columns: tuple[str, ...], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = tuple(str(column) for column in columns)
        if not self._columns:
            raise ValueError("新增列编辑器至少需要一个来源列")
        self._steps: list[RecipeStep] = []
        self._selected_row = -1
        self._changing_selection = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        source_form = QFormLayout()
        self.source_combo = QComboBox(self)
        self.source_combo.setObjectName("derivedColumnSource")
        self.source_combo.setAccessibleName("新增列主要来源列")
        protect_user_text(self.source_combo, "items")
        for column in self._columns:
            self.source_combo.addItem(column, column)
        source_form.addRow("主要来源列", self.source_combo)
        layout.addLayout(source_form)

        add_row = QHBoxLayout()
        self.operation_combo = QComboBox(self)
        self.operation_combo.setObjectName("derivedColumnOperation")
        for operation, spec in OPERATION_UI.items():
            self.operation_combo.addItem(spec.label, operation)
        self.add_button = QPushButton("添加步骤", self)
        self.add_button.setAccessibleName("添加所选转换步骤")
        add_row.addWidget(self.operation_combo, 1)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)

        splitter = QSplitter(Qt.Horizontal, self)
        steps_panel = QWidget(splitter)
        steps_layout = QVBoxLayout(steps_panel)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.addWidget(QLabel("转换步骤（从上到下执行）", steps_panel))
        self.step_list = QListWidget(steps_panel)
        self.step_list.setObjectName("derivedColumnSteps")
        self.step_list.setAccessibleName("有序转换步骤")
        steps_layout.addWidget(self.step_list, 1)
        buttons = QHBoxLayout()
        self.move_up_button = QPushButton("上移", steps_panel)
        self.move_down_button = QPushButton("下移", steps_panel)
        self.remove_button = QPushButton("删除", steps_panel)
        buttons.addWidget(self.move_up_button)
        buttons.addWidget(self.move_down_button)
        buttons.addWidget(self.remove_button)
        steps_layout.addLayout(buttons)

        details = QGroupBox("步骤设置", splitter)
        details_layout = QVBoxLayout(details)
        self.parameter_editor = StepParameterEditor(self._columns, details)
        details_layout.addWidget(self.parameter_editor)
        details_layout.addStretch(1)
        splitter.addWidget(steps_panel)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)

        self.add_button.clicked.connect(
            lambda: self.add_step(str(self.operation_combo.currentData()))
        )
        self.step_list.currentRowChanged.connect(self._select_row)
        self.move_up_button.clicked.connect(lambda: self._move_selected(-1))
        self.move_down_button.clicked.connect(lambda: self._move_selected(1))
        self.remove_button.clicked.connect(self._remove_selected)
        self.parameter_editor.set_step(None)
        self._update_buttons()

    def set_source_column(self, column: str) -> None:
        index = self.source_combo.findData(column)
        if index < 0:
            raise ValueError(f"未知来源列: {column}")
        self.source_combo.setCurrentIndex(index)

    def set_steps(self, steps: tuple[RecipeStep, ...]) -> None:
        for step in steps:
            if step.operation not in OPERATION_UI:
                raise ValueError(f"不支持的新增列操作: {step.operation}")
        self._changing_selection = True
        self._steps = list(steps)
        self.step_list.clear()
        for index, step in enumerate(self._steps, start=1):
            self.step_list.addItem(self._step_label(index, step))
        self._selected_row = -1
        self._changing_selection = False
        self.step_list.setCurrentRow(0 if self._steps else -1)
        self.parameter_editor.set_step(self._steps[0] if self._steps else None)
        self._selected_row = 0 if self._steps else -1
        self._update_buttons()
        self.stepsChanged.emit()

    def add_step(self, operation: str) -> None:
        if operation not in OPERATION_UI:
            raise ValueError(f"不支持的新增列操作: {operation}")
        if not self._save_selected():
            return
        self._steps.append(_default_step(operation))
        self._refresh_list(len(self._steps) - 1)
        self.stepsChanged.emit()

    def recipe(self, name: str) -> ColumnRecipe:
        if not self._save_selected():
            raise ValueError("当前步骤参数无效")
        return ColumnRecipe(
            name=name,
            source_column=str(self.source_combo.currentData()),
            steps=tuple(self._steps),
        )

    def _select_row(self, row: int) -> None:
        if self._changing_selection:
            return
        previous = self._selected_row
        if previous >= 0 and previous != row and not self._save_selected():
            self._changing_selection = True
            self.step_list.setCurrentRow(previous)
            self._changing_selection = False
            return
        self._selected_row = row
        self.parameter_editor.set_step(
            self._steps[row] if 0 <= row < len(self._steps) else None
        )
        self._update_buttons()

    def _save_selected(self) -> bool:
        if not (0 <= self._selected_row < len(self._steps)):
            return True
        try:
            self._steps[self._selected_row] = self.parameter_editor.step()
        except (TypeError, ValueError) as exc:
            self.validationError.emit(str(exc))
            return False
        return True

    def _move_selected(self, offset: int) -> None:
        row = self._selected_row
        target = row + offset
        if not (0 <= row < len(self._steps) and 0 <= target < len(self._steps)):
            return
        if not self._save_selected():
            return
        self._steps[row], self._steps[target] = self._steps[target], self._steps[row]
        self._refresh_list(target)
        self.stepsChanged.emit()

    def _remove_selected(self) -> None:
        row = self._selected_row
        if not (0 <= row < len(self._steps)):
            return
        del self._steps[row]
        self._refresh_list(min(row, len(self._steps) - 1))
        self.stepsChanged.emit()

    def _refresh_list(self, selected: int) -> None:
        self._changing_selection = True
        self.step_list.clear()
        for index, step in enumerate(self._steps, start=1):
            self.step_list.addItem(self._step_label(index, step))
        self._selected_row = selected if self._steps else -1
        self.step_list.setCurrentRow(self._selected_row)
        self._changing_selection = False
        self.parameter_editor.set_step(
            self._steps[self._selected_row] if self._selected_row >= 0 else None
        )
        self._update_buttons()

    @staticmethod
    def _step_label(index: int, step: RecipeStep) -> str:
        del index
        return OPERATION_UI[step.operation].label

    def _update_buttons(self) -> None:
        row = self._selected_row
        self.move_up_button.setEnabled(row > 0)
        self.move_down_button.setEnabled(0 <= row < len(self._steps) - 1)
        self.remove_button.setEnabled(0 <= row < len(self._steps))


__all__ = [
    "ColumnRecipeEditor",
    "OPERATION_UI",
    "StepParameterEditor",
    "ValueSourceEditor",
]
