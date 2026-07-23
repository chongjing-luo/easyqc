"""Focused one-time derived-column dialog for the pre-QC list."""

from __future__ import annotations

from collections.abc import Callable
import keyword
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
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.table_transform import TableTransformEngine
from gui_qt.i18n import protect_user_text
from gui_qt.task_runner import RevisionedTaskController


class DerivedColumnDialog(QDialog):
    """Preview a restricted expression, then persist it once in a worker."""

    busyChanged = Signal(bool)
    columnCommitted = Signal(str)

    def __init__(
        self,
        preview_source: pd.DataFrame,
        persist_column: Callable[[str, str], str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(preview_source, pd.DataFrame):
            raise TypeError("DerivedColumnDialog preview source must be a DataFrame")
        if not callable(persist_column):
            raise TypeError("DerivedColumnDialog requires a persistence callback")
        self._preview_source = preview_source.head(10).copy(deep=True)
        self._persist_column = persist_column
        self._engine = TableTransformEngine()
        self._revision = 0
        self._pending_request: tuple[int, str, str] | None = None
        self.committed_column = ""

        self.setObjectName("derivedColumnDialog")
        self.setWindowTitle("新增列")
        self.setAccessibleName("使用已有列生成新列")
        self.setModal(True)

        self.task_controller = RevisionedTaskController(self)
        self.task_controller.resultReady.connect(self._handle_commit_result)
        self.task_controller.errorRaised.connect(self._handle_commit_error)
        self.task_controller.busyChanged.connect(self._set_busy)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(self)
        self.name_edit.setObjectName("derivedColumnName")
        self.name_edit.setAccessibleName("新列名")
        self.name_edit.setPlaceholderText("例如：age_next")
        form.addRow("新列名", self.name_edit)
        layout.addLayout(form)

        editor_row = QHBoxLayout()
        columns_side = QVBoxLayout()
        columns_label = QLabel("现有列（双击插入）", self)
        self.columns_list = QListWidget(self)
        self.columns_list.setObjectName("derivedColumnSources")
        self.columns_list.setAccessibleName("可用于表达式的现有列")
        protect_user_text(self.columns_list, "items")
        for column in self._preview_source.columns:
            item = QListWidgetItem(str(column), self.columns_list)
            if not str(column).isidentifier():
                item.setToolTip("该列名包含空格或标点，不能直接用于表达式")
        columns_side.addWidget(columns_label)
        columns_side.addWidget(self.columns_list, 1)
        editor_row.addLayout(columns_side, 1)

        expression_side = QVBoxLayout()
        expression_side.addWidget(QLabel("计算表达式", self))
        self.expression_edit = QPlainTextEdit(self)
        self.expression_edit.setObjectName("derivedColumnExpression")
        self.expression_edit.setAccessibleName("新增列计算表达式")
        self.expression_edit.setPlaceholderText("例如：age + 1")
        self.expression_edit.setMinimumHeight(
            self.expression_edit.fontMetrics().lineSpacing() * 4
        )
        expression_side.addWidget(self.expression_edit, 1)
        help_label = QLabel(
            "支持算术、比较以及 abs、round、isna、notna、fillna、"
            "contains、startswith、endswith、isin；不支持 Python 代码或 SQL。",
            self,
        )
        help_label.setWordWrap(True)
        expression_side.addWidget(help_label)
        editor_row.addLayout(expression_side, 2)
        layout.addLayout(editor_row, 1)

        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("前 10 行预览", self))
        preview_header.addStretch(1)
        self.preview_button = QPushButton("预览结果", self)
        self.preview_button.setAccessibleName("预览新增列结果")
        preview_header.addWidget(self.preview_button)
        layout.addLayout(preview_header)

        self.preview_table = QTableWidget(0, 0, self)
        self.preview_table.setObjectName("derivedColumnPreview")
        self.preview_table.setAccessibleName("新增列前十行预览")
        protect_user_text(self.preview_table, "headers")
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_table.verticalHeader().hide()
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.preview_table, 2)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("derivedColumnError")
        self.error_label.setAccessibleName("新增列错误")
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
        self.generate_button.setAccessibleName("确认生成并写入新列")
        self.cancel_button.setAccessibleName("取消新增列")
        layout.addWidget(self.button_box)

        self.columns_list.itemDoubleClicked.connect(self._insert_column)
        self.preview_button.clicked.connect(self.preview)
        self.generate_button.clicked.connect(self._request_commit)
        self.cancel_button.clicked.connect(self.reject)
        self.resize(780, 620)

    def _request_values(self) -> tuple[str, str]:
        name = self.name_edit.text().strip()
        expression = self.expression_edit.toPlainText().strip()
        if not name:
            raise ValueError("新增列名不能为空")
        if not name.isidentifier() or keyword.iskeyword(name):
            raise ValueError("新增列名必须是不含空格或标点的有效字段名")
        if name in self._preview_source.columns:
            raise ValueError(f"列已存在: {name}")
        if not expression:
            raise ValueError("新增列表达式不能为空")
        return name, expression

    def _evaluate_preview(self) -> tuple[str, str, pd.DataFrame]:
        name, expression = self._request_values()
        result = self._engine.derive_column(
            self._preview_source,
            name,
            expression,
        )
        return name, expression, result

    @Slot(QListWidgetItem)
    def _insert_column(self, item: QListWidgetItem) -> None:
        column = item.text()
        if not column.isidentifier():
            self._set_error(
                f"列名 '{column}' 包含空格或标点，不能直接用于表达式"
            )
            return
        cursor = self.expression_edit.textCursor()
        cursor.insertText(column)
        self.expression_edit.setTextCursor(cursor)
        self.expression_edit.setFocus()
        self._set_error("")

    def preview(self) -> bool:
        try:
            name, _expression, result = self._evaluate_preview()
        except (ArithmeticError, TypeError, ValueError) as exc:
            self._set_error(str(exc))
            return False
        columns = [
            column
            for column in ("ezqcid", name)
            if column in result.columns
        ]
        self.preview_table.clear()
        self.preview_table.setColumnCount(len(columns))
        self.preview_table.setHorizontalHeaderLabels(columns)
        self.preview_table.setRowCount(len(result))
        for row in range(len(result)):
            for column_index, column in enumerate(columns):
                value = result.iloc[row][column]
                self.preview_table.setItem(
                    row,
                    column_index,
                    QTableWidgetItem(self._display_value(value)),
                )
        if columns:
            self.preview_table.horizontalHeader().setSectionResizeMode(
                0,
                QHeaderView.ResizeToContents,
            )
        self._set_error("")
        return True

    def _request_commit(self) -> None:
        if self.task_controller.busy:
            return
        try:
            name, expression, _preview = self._evaluate_preview()
        except (ArithmeticError, TypeError, ValueError) as exc:
            self._set_error(str(exc))
            return
        self._revision += 1
        revision = self._revision
        self._pending_request = (revision, name, expression)
        self._set_error("")
        self.task_controller.submit(
            revision,
            lambda: self._persist_column(name, expression),
        )

    @Slot(int, object)
    def _handle_commit_result(self, revision: int, result: Any) -> None:
        pending = self._pending_request
        if pending is None or pending[0] != revision:
            return
        name = pending[1]
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
            self.expression_edit,
            self.columns_list,
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
