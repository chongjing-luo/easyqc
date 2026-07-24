"""Safe one-time derived-column dialog for the authoritative QC list."""

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
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.table_transform import TableTransformEngine
from gui_qt.column_recipe_editor import ColumnRecipeEditor
from gui_qt.task_runner import RevisionedTaskController
from models.column_recipe import ColumnRecipe


class DerivedColumnDialog(QDialog):
    """Preview a typed transformation chain, then persist it in a worker."""

    busyChanged = Signal(bool)
    columnCommitted = Signal(str)

    def __init__(
        self,
        preview_source: pd.DataFrame,
        persist_column: Callable[[ColumnRecipe], str],
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
        self._engine = TableTransformEngine()
        self._revision = 0
        self._pending_request: tuple[int, ColumnRecipe] | None = None
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
        self.name_edit.setPlaceholderText("例如：scan_key")
        form.addRow("新列名", self.name_edit)
        layout.addLayout(form)

        helper = QLabel(
            "选择主要来源列，再按顺序添加转换步骤。路径操作只处理单元格文本，"
            "不会读取文件；配方和中间结果不会保存。",
            self,
        )
        helper.setWordWrap(True)
        layout.addWidget(helper)

        columns = tuple(str(column) for column in self._preview_source.columns)
        self.editor = ColumnRecipeEditor(columns, self)
        self.editor.setObjectName("derivedColumnRecipeEditor")
        self.editor.setAccessibleName("新增列转换步骤编辑器")
        self.editor.validationError.connect(self._set_error)
        layout.addWidget(self.editor, 3)

        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("前 20 行预览", self))
        preview_header.addStretch(1)
        self.preview_button = QPushButton("预览结果", self)
        self.preview_button.setAccessibleName("预览新增列结果")
        preview_header.addWidget(self.preview_button)
        layout.addLayout(preview_header)

        self.preview_table = QTableWidget(0, 0, self)
        self.preview_table.setObjectName("derivedColumnPreview")
        self.preview_table.setAccessibleName("新增列前二十行预览")
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

        self.preview_button.clicked.connect(self.preview)
        self.generate_button.clicked.connect(self._request_commit)
        self.cancel_button.clicked.connect(self.reject)
        self.resize(920, 760)

    def _request_recipe(self) -> ColumnRecipe:
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("新增列名不能为空")
        if not name.isidentifier() or keyword.iskeyword(name):
            raise ValueError("新增列名必须是不含空格或标点的有效字段名")
        if name in self._preview_source.columns:
            raise ValueError(f"列已存在: {name}")
        return self.editor.recipe(name)

    def _evaluate_preview(
        self,
    ) -> tuple[ColumnRecipe, pd.DataFrame]:
        recipe = self._request_recipe()
        result = self._engine.derive_column_from_recipe(
            self._preview_source,
            recipe,
        )
        return recipe, result

    def preview(self) -> bool:
        try:
            recipe, result = self._evaluate_preview()
        except (ArithmeticError, TypeError, ValueError) as exc:
            self._set_error(str(exc))
            return False
        source = self._preview_source[recipe.source_column]
        columns = ["ezqcid", "原始值", recipe.name]
        self.preview_table.clear()
        self.preview_table.setColumnCount(len(columns))
        self.preview_table.setHorizontalHeaderLabels(columns)
        self.preview_table.horizontalHeaderItem(0).setData(
            Qt.ItemDataRole.UserRole,
            "ezqcid",
        )
        self.preview_table.horizontalHeaderItem(2).setData(
            Qt.ItemDataRole.UserRole,
            recipe.name,
        )
        self.preview_table.setRowCount(len(result))
        for row in range(len(result)):
            identity = (
                result.iloc[row]["ezqcid"]
                if "ezqcid" in result.columns
                else row + 1
            )
            values = (identity, source.iloc[row], result.iloc[row][recipe.name])
            for column_index, value in enumerate(values):
                self.preview_table.setItem(
                    row,
                    column_index,
                    QTableWidgetItem(self._display_value(value)),
                )
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
            recipe, _preview = self._evaluate_preview()
        except (ArithmeticError, TypeError, ValueError) as exc:
            self._set_error(str(exc))
            return
        self._revision += 1
        revision = self._revision
        self._pending_request = (revision, recipe)
        self._set_error("")
        self.task_controller.submit(
            revision,
            lambda: self._persist_column(recipe),
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
