"""Safe one-time EasyQC Formula dialog for an authoritative table."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.formula_engine import (
    FormulaEngine,
    FormulaEvaluation,
    FormulaEvaluationError,
)
from core.formula_parser import FormulaError, ParsedFormula
from gui_qt.formula_editor import FormulaEditorWidget
from gui_qt.i18n import set_translatable_table_columns
from gui_qt.task_runner import RevisionedTaskController
from gui_qt.theme import set_button_role
from models.derived_formula import DerivedColumnFormula


class DerivedColumnDialog(QDialog):
    """Preview one formula, then commit it against a context-owned full source."""

    busyChanged = Signal(bool)
    columnCommitted = Signal(str)

    def __init__(
        self,
        preview_source: pd.DataFrame,
        persist_column: Callable[[DerivedColumnFormula], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(preview_source, pd.DataFrame):
            raise TypeError("DerivedColumnDialog preview source must be a DataFrame")
        if preview_source.columns.empty:
            raise ValueError("新增列预览来源至少需要一个列")
        if not callable(persist_column):
            raise TypeError("DerivedColumnDialog requires a persistence callback")
        self._preview_source = preview_source.head(20).copy(deep=True)
        self._persist_column = persist_column
        self._engine = FormulaEngine()
        self._revision = 0
        self._pending_request: tuple[int, DerivedColumnFormula] | None = None
        self.committed_column = ""

        self.setObjectName("derivedColumnDialog")
        self.setWindowTitle("新增列")
        self.setAccessibleName("使用快捷模板或 EasyQC 公式生成新列")
        self.setModal(True)

        self.task_controller = RevisionedTaskController(self)
        self.task_controller.resultReady.connect(self._handle_commit_result)
        self.task_controller.errorRaised.connect(self._handle_commit_error)
        self.task_controller.busyChanged.connect(self._set_busy)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.name_edit = QLineEdit(self)
        self.name_edit.setObjectName("derivedColumnName")
        self.name_edit.setAccessibleName("新列名")
        self.name_edit.setPlaceholderText("例如：scan_key 或 QC 分组")
        form.addRow("新列名", self.name_edit)
        layout.addLayout(form)

        helper = QLabel(
            "快捷模板和高级公式共用同一套安全计算规则。公式、模板和中间结果不会保存；"
            "只写入最终普通列。",
            self,
        )
        helper.setWordWrap(True)
        helper.setProperty("role", "secondary")
        layout.addWidget(helper)

        columns = tuple(str(column) for column in self._preview_source.columns)
        self.editor = FormulaEditorWidget(columns, self)
        self.editor.setObjectName("derivedColumnFormulaEditor")
        self.editor.setAccessibleName("新增列公式编辑器")
        self.editor.validationError.connect(self._formula_validation_changed)
        layout.addWidget(self.editor, 3)

        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("前 20 行预览", self))
        preview_header.addStretch(1)
        self.preview_button = QPushButton("预览结果", self)
        self.preview_button.setAccessibleName("预览新增列结果和逐行错误")
        set_button_role(self.preview_button, "secondary")
        preview_header.addWidget(self.preview_button)
        layout.addLayout(preview_header)

        self.preview_table = QTableWidget(0, 0, self)
        self.preview_table.setObjectName("derivedColumnPreview")
        self.preview_table.setAccessibleName("新增列前二十行公式预览")
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.verticalHeader().hide()
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.preview_table, 2)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("derivedColumnError")
        self.error_label.setAccessibleName("新增列错误")
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.generate_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.cancel_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.generate_button.setText("生成列")
        self.cancel_button.setText("取消")
        self.generate_button.setAccessibleName("确认计算完整数据并写入新列")
        self.cancel_button.setAccessibleName("取消新增列")
        set_button_role(self.generate_button, "primary")
        set_button_role(self.cancel_button, "secondary")
        layout.addWidget(self.button_box)

        self.preview_button.clicked.connect(self.preview)
        self.generate_button.clicked.connect(self._request_commit)
        self.cancel_button.clicked.connect(self.reject)
        self.resize(980, 820)

    def _request_formula(self) -> DerivedColumnFormula:
        request = DerivedColumnFormula(
            self.name_edit.text(),
            self.editor.formula(),
        )
        if request.name in self._preview_source.columns:
            raise ValueError(f"列已存在: {request.name}")
        return request

    def _evaluate_preview(
        self,
    ) -> tuple[DerivedColumnFormula, ParsedFormula, FormulaEvaluation]:
        request = self._request_formula()
        parsed = self._engine.parser.parse(request.expression)
        evaluation = self._engine.evaluate(self._preview_source, parsed)
        return request, parsed, evaluation

    def preview(self) -> bool:
        try:
            request, parsed, evaluation = self._evaluate_preview()
        except (ArithmeticError, FormulaError, TypeError, ValueError) as exc:
            self._set_error(str(exc))
            return False
        self._render_preview(request, parsed, evaluation)
        self._set_error(self._row_error_summary(evaluation))
        return True

    def _render_preview(
        self,
        request: DerivedColumnFormula,
        parsed: ParsedFormula,
        evaluation: FormulaEvaluation,
    ) -> None:
        has_identity = "easyqcid" in self._preview_source.columns
        referenced = [
            column
            for column in parsed.referenced_columns
            if not (has_identity and column == "easyqcid")
        ]
        headers = [
            "easyqcid" if has_identity else "行",
            *referenced,
            request.name,
            "错误",
        ]
        self.preview_table.clear()
        self.preview_table.setColumnCount(len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        if has_identity:
            self.preview_table.horizontalHeaderItem(0).setData(
                Qt.ItemDataRole.UserRole,
                "easyqcid",
            )
        for column_index, column in enumerate(referenced, start=1):
            self.preview_table.horizontalHeaderItem(column_index).setData(
                Qt.ItemDataRole.UserRole,
                column,
            )
        result_column_index = 1 + len(referenced)
        self.preview_table.horizontalHeaderItem(result_column_index).setData(
            Qt.ItemDataRole.UserRole,
            request.name,
        )
        set_translatable_table_columns(
            self.preview_table,
            len(headers) - 1,
        )
        self.preview_table.setRowCount(len(self._preview_source))

        for row in range(len(self._preview_source)):
            identity = (
                self._preview_source.iloc[row]["easyqcid"]
                if has_identity
                else row + 1
            )
            values = [
                identity,
                *(
                    self._preview_source.iloc[row][column]
                    for column in referenced
                ),
                evaluation.values.iloc[row],
                evaluation.errors.iloc[row],
            ]
            for column_index, value in enumerate(values):
                self.preview_table.setItem(
                    row,
                    column_index,
                    QTableWidgetItem(self._display_value(value)),
                )

        header = self.preview_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        if headers:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)

    @staticmethod
    def _row_error_summary(evaluation: FormulaEvaluation) -> str:
        try:
            evaluation.raise_for_errors()
        except FormulaEvaluationError as exc:
            return str(exc)
        return ""

    def _request_commit(self) -> None:
        if self.task_controller.busy:
            return
        try:
            request, _parsed, evaluation = self._evaluate_preview()
            evaluation.raise_for_errors()
        except (ArithmeticError, FormulaError, TypeError, ValueError) as exc:
            self._set_error(str(exc))
            return
        self._revision += 1
        revision = self._revision
        self._pending_request = (revision, request)
        self._set_error("")
        self.task_controller.submit(
            revision,
            lambda: self._persist_column(request),
        )

    @Slot(int, object)
    def _handle_commit_result(self, revision: int, result: Any) -> None:
        pending = self._pending_request
        if pending is None or pending[0] != revision:
            return
        name = pending[1].name
        if result != name:
            self._handle_commit_error(
                revision,
                TypeError("新增列保存任务没有返回正确的列名"),
            )
            return
        self._pending_request = None
        self.committed_column = name
        self.columnCommitted.emit(name)
        self.accept()

    @Slot(int, object)
    def _handle_commit_error(self, revision: int, error: object) -> None:
        if self._pending_request is None or self._pending_request[0] != revision:
            return
        self._pending_request = None
        self._set_error(str(error).strip() or type(error).__name__)

    @Slot(bool)
    def _set_busy(self, busy: bool) -> None:
        for control in (
            self.name_edit,
            self.editor,
            self.preview_button,
            self.generate_button,
            self.cancel_button,
        ):
            control.setEnabled(not busy)
        self.busyChanged.emit(bool(busy))

    def reject(self) -> None:
        if self.task_controller.busy:
            return
        super().reject()

    @Slot(str)
    def _formula_validation_changed(self, message: str) -> None:
        self._set_error(message)

    @Slot(str)
    def _set_error(self, message: str) -> None:
        self.error_label.setText(str(message))
        self.error_label.setVisible(bool(message))

    @staticmethod
    def _display_value(value: Any) -> str:
        if value is None:
            return ""
        if pd.api.types.is_scalar(value) and bool(pd.isna(value)):
            return ""
        return str(value)


__all__ = ["DerivedColumnDialog"]
