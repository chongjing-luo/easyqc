"""Shared quick-template and advanced editor for EasyQC Formula."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.formula_engine import FormulaEngine
from core.formula_parser import FormulaError, FormulaParser
from gui_qt.formula_templates import (
    column_reference,
    render_cleanup_formula,
    render_concatenate_formula,
    render_conditional_formula,
    render_extract_formula,
    render_fixed_formula,
    render_numeric_columns_formula,
    render_numeric_fixed_formula,
)
from gui_qt.i18n import protect_user_text
from gui_qt.theme import set_button_role


class FormulaQuickTemplatePanel(QWidget):
    """Render six common tasks into visible EasyQC Formula text."""

    formulaRendered = Signal(str)
    errorRaised = Signal(str)

    _TEMPLATES = (
        ("固定值", "fixed"),
        ("连接两列", "concatenate"),
        ("按分隔符提取", "extract"),
        ("条件生成", "conditional"),
        ("文本清理与大小写", "cleanup"),
        ("数值计算", "numeric"),
    )

    def __init__(
        self,
        columns: tuple[str, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not columns:
            raise ValueError("公式模板至少需要一个可用列")
        self._columns = columns
        self.setObjectName("formulaQuickTemplates")
        self.setAccessibleName("快捷公式模板")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        template_row = QFormLayout()
        template_row.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.template_combo = QComboBox(self)
        self.template_combo.setObjectName("formulaTemplateType")
        self.template_combo.setAccessibleName("快捷模板类型")
        for label, template_id in self._TEMPLATES:
            self.template_combo.addItem(label, template_id)
        template_row.addRow("模板", self.template_combo)
        layout.addLayout(template_row)

        self.pages = QStackedWidget(self)
        self.pages.setObjectName("formulaTemplatePages")
        self.pages.setAccessibleName("快捷模板参数")
        self.pages.addWidget(self._build_fixed_page())
        self.pages.addWidget(self._build_concatenate_page())
        self.pages.addWidget(self._build_extract_page())
        self.pages.addWidget(self._build_conditional_page())
        self.pages.addWidget(self._build_cleanup_page())
        self.pages.addWidget(self._build_numeric_page())
        self.pages.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )
        layout.addWidget(self.pages)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.generate_button = QPushButton("生成公式", self)
        self.generate_button.setObjectName("generateQuickFormula")
        self.generate_button.setAccessibleName("用快捷模板生成公式")
        set_button_role(self.generate_button, "primary")
        action_row.addWidget(self.generate_button)
        layout.addLayout(action_row)
        layout.addStretch(1)

        self.template_combo.currentIndexChanged.connect(
            self._sync_template_page
        )
        self.fixed_type_combo.currentIndexChanged.connect(
            self._sync_fixed_value_editor
        )
        self.numeric_right_kind_combo.currentIndexChanged.connect(
            self._sync_numeric_right_editor
        )
        self.generate_button.clicked.connect(self._render_current)
        self._sync_fixed_value_editor()
        self._sync_numeric_right_editor()
        self._sync_template_page(self.template_combo.currentIndex())
        self.setFocusProxy(self.template_combo)

    def _column_combo(
        self,
        parent: QWidget,
        accessible_name: str,
    ) -> QComboBox:
        combo = QComboBox(parent)
        combo.setAccessibleName(accessible_name)
        protect_user_text(combo, "items")
        for column in self._columns:
            combo.addItem(column, column)
        return combo

    @staticmethod
    def _page() -> tuple[QWidget, QFormLayout]:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        return page, form

    def _build_fixed_page(self) -> QWidget:
        page, form = self._page()
        self._fixed_form = form
        self.fixed_type_combo = QComboBox(page)
        self.fixed_type_combo.setAccessibleName("固定值类型")
        for label, value_type in (
            ("文本", "text"),
            ("整数", "integer"),
            ("小数", "decimal"),
            ("布尔值", "boolean"),
            ("空值", "blank"),
        ):
            self.fixed_type_combo.addItem(label, value_type)
        self.fixed_value_edit = QLineEdit(page)
        self.fixed_value_edit.setAccessibleName("固定值内容")
        self.fixed_value_edit.setPlaceholderText("输入固定值")
        self.fixed_boolean_combo = QComboBox(page)
        self.fixed_boolean_combo.setAccessibleName("固定布尔值")
        self.fixed_boolean_combo.addItem("是", True)
        self.fixed_boolean_combo.addItem("否", False)
        form.addRow("类型", self.fixed_type_combo)
        form.addRow("值", self.fixed_value_edit)
        form.addRow("布尔值", self.fixed_boolean_combo)
        return page

    def _build_concatenate_page(self) -> QWidget:
        page, form = self._page()
        self.concatenate_left_combo = self._column_combo(page, "连接左列")
        self.concatenate_separator_edit = QLineEdit(page)
        self.concatenate_separator_edit.setAccessibleName("连接分隔文本")
        self.concatenate_separator_edit.setText("_")
        self.concatenate_right_combo = self._column_combo(page, "连接右列")
        form.addRow("左列", self.concatenate_left_combo)
        form.addRow("中间文本", self.concatenate_separator_edit)
        form.addRow("右列", self.concatenate_right_combo)
        return page

    def _build_extract_page(self) -> QWidget:
        page, form = self._page()
        self.extract_column_combo = self._column_combo(page, "提取来源列")
        self.extract_delimiter_edit = QLineEdit(page)
        self.extract_delimiter_edit.setAccessibleName("提取分隔符")
        self.extract_delimiter_edit.setText("_")
        self.extract_position_combo = QComboBox(page)
        self.extract_position_combo.setAccessibleName("提取位置")
        self.extract_position_combo.addItem("分隔符之前", "before")
        self.extract_position_combo.addItem("分隔符之后", "after")
        form.addRow("来源列", self.extract_column_combo)
        form.addRow("分隔符", self.extract_delimiter_edit)
        form.addRow("保留", self.extract_position_combo)
        return page

    def _build_conditional_page(self) -> QWidget:
        page, form = self._page()
        self.condition_column_combo = self._column_combo(page, "条件来源列")
        self.condition_operator_combo = QComboBox(page)
        self.condition_operator_combo.setAccessibleName("条件比较符")
        for operator in ("=", "<>", "<", "<=", ">", ">="):
            self.condition_operator_combo.addItem(operator, operator)
        self.condition_compare_edit = QLineEdit(page)
        self.condition_compare_edit.setAccessibleName("条件比较文本")
        self.condition_true_edit = QLineEdit(page)
        self.condition_true_edit.setAccessibleName("条件成立文本")
        self.condition_false_edit = QLineEdit(page)
        self.condition_false_edit.setAccessibleName("条件不成立文本")
        form.addRow("来源列", self.condition_column_combo)
        form.addRow("比较", self.condition_operator_combo)
        form.addRow("等于文本", self.condition_compare_edit)
        form.addRow("成立时", self.condition_true_edit)
        form.addRow("不成立时", self.condition_false_edit)
        return page

    def _build_cleanup_page(self) -> QWidget:
        page, form = self._page()
        self.cleanup_column_combo = self._column_combo(page, "文本清理来源列")
        self.cleanup_operation_combo = QComboBox(page)
        self.cleanup_operation_combo.setAccessibleName("文本清理方式")
        for label, operation in (
            ("去除首尾空白", "trim"),
            ("转为大写", "upper"),
            ("转为小写", "lower"),
            ("去空白并转大写", "trim_upper"),
            ("去空白并转小写", "trim_lower"),
        ):
            self.cleanup_operation_combo.addItem(label, operation)
        form.addRow("来源列", self.cleanup_column_combo)
        form.addRow("处理", self.cleanup_operation_combo)
        return page

    def _build_numeric_page(self) -> QWidget:
        page, form = self._page()
        self._numeric_form = form
        self.numeric_left_combo = self._column_combo(page, "数值计算左列")
        self.numeric_operator_combo = QComboBox(page)
        self.numeric_operator_combo.setAccessibleName("数值运算符")
        for operator in ("+", "-", "*", "/"):
            self.numeric_operator_combo.addItem(operator, operator)
        self.numeric_right_kind_combo = QComboBox(page)
        self.numeric_right_kind_combo.setAccessibleName("数值计算右值类型")
        self.numeric_right_kind_combo.addItem("已有列", "column")
        self.numeric_right_kind_combo.addItem("固定数值", "fixed")
        self.numeric_right_column_combo = self._column_combo(
            page,
            "数值计算右列",
        )
        self.numeric_right_value_edit = QLineEdit(page)
        self.numeric_right_value_edit.setAccessibleName("数值计算固定数值")
        self.numeric_right_value_edit.setPlaceholderText("例如：12 或 0.5")
        form.addRow("左列", self.numeric_left_combo)
        form.addRow("运算", self.numeric_operator_combo)
        form.addRow("右值类型", self.numeric_right_kind_combo)
        form.addRow("右列", self.numeric_right_column_combo)
        form.addRow("固定数值", self.numeric_right_value_edit)
        return page

    def set_template(self, template_id: str) -> None:
        index = self.template_combo.findData(template_id)
        if index < 0:
            raise ValueError(f"未知快捷模板: {template_id}")
        self.template_combo.setCurrentIndex(index)

    @Slot(int)
    def _sync_template_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        current = self.pages.currentWidget()
        if current is not None:
            self.pages.setMaximumHeight(current.sizeHint().height())
        self.pages.updateGeometry()

    @Slot()
    def _sync_fixed_value_editor(self) -> None:
        value_type = self.fixed_type_combo.currentData()
        show_text = value_type in {"text", "integer", "decimal"}
        show_boolean = value_type == "boolean"
        self.fixed_value_edit.setVisible(show_text)
        self._fixed_form.labelForField(self.fixed_value_edit).setVisible(
            show_text
        )
        self.fixed_boolean_combo.setVisible(show_boolean)
        self._fixed_form.labelForField(self.fixed_boolean_combo).setVisible(
            show_boolean
        )

    @Slot()
    def _sync_numeric_right_editor(self) -> None:
        use_column = self.numeric_right_kind_combo.currentData() == "column"
        self.numeric_right_column_combo.setVisible(use_column)
        self._numeric_form.labelForField(
            self.numeric_right_column_combo
        ).setVisible(use_column)
        self.numeric_right_value_edit.setVisible(not use_column)
        self._numeric_form.labelForField(
            self.numeric_right_value_edit
        ).setVisible(not use_column)

    def _fixed_value(self) -> object:
        value_type = self.fixed_type_combo.currentData()
        text = self.fixed_value_edit.text()
        if value_type == "text":
            return text
        if value_type == "integer":
            try:
                return int(text.strip())
            except ValueError as exc:
                raise ValueError("固定值必须是整数") from exc
        if value_type == "decimal":
            try:
                return float(text.strip())
            except ValueError as exc:
                raise ValueError("固定值必须是小数") from exc
        if value_type == "boolean":
            return bool(self.fixed_boolean_combo.currentData())
        return None

    def _numeric_fixed_value(self) -> int | float:
        text = self.numeric_right_value_edit.text().strip()
        if not text:
            raise ValueError("请输入固定数值")
        try:
            return float(text) if any(char in text for char in ".eE") else int(text)
        except ValueError as exc:
            raise ValueError("固定数值格式无效") from exc

    def _renderer(self, template_id: str) -> Callable[[], str]:
        renderers: dict[str, Callable[[], str]] = {
            "fixed": lambda: render_fixed_formula(self._fixed_value()),
            "concatenate": lambda: render_concatenate_formula(
                str(self.concatenate_left_combo.currentData()),
                self.concatenate_separator_edit.text(),
                str(self.concatenate_right_combo.currentData()),
            ),
            "extract": lambda: render_extract_formula(
                str(self.extract_column_combo.currentData()),
                self.extract_delimiter_edit.text(),
                position=str(self.extract_position_combo.currentData()),
            ),
            "conditional": lambda: render_conditional_formula(
                str(self.condition_column_combo.currentData()),
                str(self.condition_operator_combo.currentData()),
                self.condition_compare_edit.text(),
                self.condition_true_edit.text(),
                self.condition_false_edit.text(),
            ),
            "cleanup": lambda: render_cleanup_formula(
                str(self.cleanup_column_combo.currentData()),
                operation=str(self.cleanup_operation_combo.currentData()),
            ),
            "numeric": self._render_numeric,
        }
        return renderers[template_id]

    def _render_numeric(self) -> str:
        left = str(self.numeric_left_combo.currentData())
        operator = str(self.numeric_operator_combo.currentData())
        if self.numeric_right_kind_combo.currentData() == "column":
            return render_numeric_columns_formula(
                left,
                operator,
                str(self.numeric_right_column_combo.currentData()),
            )
        return render_numeric_fixed_formula(
            left,
            operator,
            self._numeric_fixed_value(),
        )

    @Slot()
    def _render_current(self) -> None:
        template_id = str(self.template_combo.currentData())
        try:
            formula = self._renderer(template_id)()
            FormulaParser().parse(formula)
        except (KeyError, TypeError, ValueError, FormulaError) as exc:
            self.errorRaised.emit(str(exc))
            return
        self.errorRaised.emit("")
        self.formulaRendered.emit(formula)


class FormulaEditorWidget(QWidget):
    """One shared formula draft with quick and advanced authoring surfaces."""

    formulaChanged = Signal(str)
    validationError = Signal(str)

    def __init__(
        self,
        columns: tuple[str, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        normalized_columns = tuple(str(column) for column in columns)
        if not normalized_columns:
            raise ValueError("公式编辑器至少需要一个可用列")
        if len(set(normalized_columns)) != len(normalized_columns):
            raise ValueError("公式编辑器不能接收重复列名")
        self._columns = normalized_columns
        self._parser = FormulaParser()
        self.setObjectName("formulaEditor")
        self.setAccessibleName("EasyQC 公式编辑器")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("formulaModes")
        self.tabs.setAccessibleName("公式输入方式")
        self.quick_panel = FormulaQuickTemplatePanel(self._columns, self.tabs)
        self.tabs.addTab(self.quick_panel, "快捷模板")
        self.tabs.addTab(self._build_advanced_page(), "高级公式")
        layout.addWidget(self.tabs)

        formula_group = QGroupBox("当前公式", self)
        formula_group.setObjectName("currentFormulaGroup")
        formula_layout = QVBoxLayout(formula_group)
        helper = QLabel(
            "公式只计算当前表格中的值；不会运行 Python、SQL、正则或文件操作。",
            formula_group,
        )
        helper.setWordWrap(True)
        helper.setProperty("role", "secondary")
        formula_layout.addWidget(helper)
        self.formula_edit = QPlainTextEdit(formula_group)
        self.formula_edit.setObjectName("formulaText")
        self.formula_edit.setAccessibleName("当前 EasyQC 公式")
        self.formula_edit.setPlaceholderText(
            '例如：IF([site] = "A", UPPER([filename]), [filename])'
        )
        self.formula_edit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.formula_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        formula_layout.addWidget(self.formula_edit, 1)
        self.status_label = QLabel("请输入公式", formula_group)
        self.status_label.setObjectName("formulaStatus")
        self.status_label.setAccessibleName("公式语法状态")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("state", "empty")
        self.status_label.setProperty("role", "secondary")
        formula_layout.addWidget(self.status_label)
        layout.addWidget(formula_group, 1)

        self.quick_panel.formulaRendered.connect(self._apply_template_formula)
        self.quick_panel.errorRaised.connect(self._set_template_error)
        self.formula_edit.textChanged.connect(self._formula_text_changed)
        self.setFocusProxy(self.formula_edit)
        self._formula_text_changed()

    def _build_advanced_page(self) -> QWidget:
        page = QWidget(self.tabs)
        page.setObjectName("advancedFormulaTools")
        page.setAccessibleName("高级公式插入工具")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        column_form = QFormLayout()
        column_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        column_row = QWidget(page)
        column_layout = QHBoxLayout(column_row)
        column_layout.setContentsMargins(0, 0, 0, 0)
        self.column_combo = QComboBox(column_row)
        self.column_combo.setObjectName("formulaColumn")
        self.column_combo.setAccessibleName("要插入的表格列")
        protect_user_text(self.column_combo, "items")
        for column in self._columns:
            self.column_combo.addItem(column, column)
        self.insert_column_button = QPushButton("插入列", column_row)
        self.insert_column_button.setAccessibleName("在光标处插入所选列")
        column_layout.addWidget(self.column_combo, 1)
        column_layout.addWidget(self.insert_column_button)
        column_form.addRow("表格列", column_row)

        function_row = QWidget(page)
        function_layout = QHBoxLayout(function_row)
        function_layout.setContentsMargins(0, 0, 0, 0)
        self.function_combo = QComboBox(function_row)
        self.function_combo.setObjectName("formulaFunction")
        self.function_combo.setAccessibleName("要插入的公式函数")
        for spec in FormulaEngine.function_catalog():
            self.function_combo.addItem(spec.name, spec.name)
        self.insert_function_button = QPushButton("插入函数", function_row)
        self.insert_function_button.setAccessibleName("在光标处插入所选函数")
        function_layout.addWidget(self.function_combo, 1)
        function_layout.addWidget(self.insert_function_button)
        column_form.addRow("函数", function_row)
        layout.addLayout(column_form)

        details = QGroupBox("函数说明", page)
        details_layout = QVBoxLayout(details)
        self.function_signature = QLabel(details)
        self.function_signature.setObjectName("formulaFunctionSignature")
        self.function_signature.setAccessibleName("公式函数签名")
        self.function_signature.setTextInteractionFlags(
            self.function_signature.textInteractionFlags()
        )
        self.function_description = QLabel(details)
        self.function_description.setObjectName("formulaFunctionDescription")
        self.function_description.setAccessibleName("公式函数说明")
        self.function_description.setWordWrap(True)
        self.function_example = QLabel(details)
        self.function_example.setObjectName("formulaFunctionExample")
        self.function_example.setAccessibleName("公式函数示例")
        self.function_example.setWordWrap(True)
        details_layout.addWidget(self.function_signature)
        details_layout.addWidget(self.function_description)
        details_layout.addWidget(self.function_example)
        layout.addWidget(details)
        layout.addStretch(1)

        self.function_combo.currentIndexChanged.connect(
            self._update_function_details
        )
        self.insert_column_button.clicked.connect(self._insert_column)
        self.insert_function_button.clicked.connect(self._insert_function)
        self._update_function_details()
        return page

    def formula(self) -> str:
        return self.formula_edit.toPlainText().strip()

    def set_formula(self, formula: str) -> None:
        if not isinstance(formula, str):
            raise TypeError("公式必须是文本")
        if self.formula_edit.toPlainText() == formula:
            return
        self.formula_edit.setPlainText(formula)

    @Slot(str)
    def _apply_template_formula(self, formula: str) -> None:
        self.set_formula(formula)
        self.formula_edit.setFocus()
        cursor = self.formula_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.formula_edit.setTextCursor(cursor)

    @Slot(str)
    def _set_template_error(self, message: str) -> None:
        if not message:
            return
        self._set_status(message, "invalid")
        self.validationError.emit(message)

    @Slot()
    def _formula_text_changed(self) -> None:
        formula = self.formula()
        if not formula:
            self._set_status("请输入公式", "empty")
            self.formulaChanged.emit("")
            return
        try:
            parsed = self._parser.parse(formula)
            missing = [
                column
                for column in parsed.referenced_columns
                if column not in self._columns
            ]
            if missing:
                raise ValueError(f"未知列: {missing[0]}")
        except (FormulaError, ValueError) as exc:
            message = str(exc)
            self._set_status(message, "invalid")
            self.validationError.emit(message)
        else:
            count = len(parsed.referenced_columns)
            self._set_status(f"公式有效 · 引用 {count} 列", "valid")
            self.validationError.emit("")
        self.formulaChanged.emit(formula)

    def _set_status(self, message: str, state: str) -> None:
        self.status_label.setText(message)
        self.status_label.setProperty("state", state)
        self.status_label.setProperty(
            "role",
            "error" if state == "invalid" else "secondary",
        )
        style = self.status_label.style()
        style.unpolish(self.status_label)
        style.polish(self.status_label)

    @Slot()
    def _update_function_details(self) -> None:
        name = str(self.function_combo.currentData())
        spec = next(
            item
            for item in FormulaEngine.function_catalog()
            if item.name == name
        )
        self.function_signature.setText(spec.signature)
        self.function_description.setText(spec.description_zh)
        self.function_example.setText(f"示例：{spec.example}")

    @Slot()
    def _insert_column(self) -> None:
        reference = column_reference(str(self.column_combo.currentData()))
        cursor = self.formula_edit.textCursor()
        cursor.insertText(reference)
        self.formula_edit.setTextCursor(cursor)
        self.formula_edit.setFocus()

    @Slot()
    def _insert_function(self) -> None:
        name = str(self.function_combo.currentData())
        cursor = self.formula_edit.textCursor()
        cursor.insertText(f"{name}()")
        cursor.movePosition(QTextCursor.MoveOperation.PreviousCharacter)
        self.formula_edit.setTextCursor(cursor)
        self.formula_edit.setFocus()


__all__ = [
    "FormulaEditorWidget",
    "FormulaQuickTemplatePanel",
]
