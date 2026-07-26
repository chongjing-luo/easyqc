"""Transactional filter-backed dialog for defining row-deletion targets."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from gui_qt.filter_panel import FilterPanel
from gui_qt.theme import set_button_role
from models.table_view_state import ColumnProfile, FilterExpression


class DeleteRowsDialog(QDialog):
    """Emit one non-empty filter expression without mutating table data."""

    deleteRequested = Signal(object)

    def __init__(
        self,
        profiles: tuple[ColumnProfile, ...],
        initial_filter: FilterExpression,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(initial_filter, FilterExpression):
            raise TypeError("DeleteRowsDialog requires a FilterExpression")
        self.setObjectName("deleteRowsDialog")
        self.setWindowTitle("按条件删除行")
        self.setAccessibleName("按筛选条件删除表格行")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._delete_pending = False

        layout = QVBoxLayout(self)
        warning = QLabel(
            "设置要删除行必须满足的条件。空条件不能执行删除。",
            self,
        )
        warning.setObjectName("deleteRowsWarning")
        warning.setProperty("role", "secondary")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self.editor = FilterPanel(profiles, self)
        self.editor.set_expression(initial_filter)
        layout.addWidget(self.editor, 1)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset,
            self,
        )
        self.delete_button = self.button_box.addButton(
            "删除匹配行",
            QDialogButtonBox.ButtonRole.DestructiveRole,
        )
        self.cancel_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.reset_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Reset
        )
        self.cancel_button.setText("取消")
        self.reset_button.setText("重置")
        self.delete_button.setAccessibleName("确认删除条件草稿")
        self.cancel_button.setAccessibleName("取消删除行")
        self.reset_button.setAccessibleName("重置删除行条件")
        set_button_role(self.delete_button, "danger")
        layout.addWidget(self.button_box)

        self.delete_button.clicked.connect(self._request_delete)
        self.cancel_button.clicked.connect(self.reject)
        self.reset_button.clicked.connect(self.editor.reset_draft)
        self.resize(840, 620)

    @property
    def error_text(self) -> str:
        return self.editor.error_text

    def _request_delete(self) -> None:
        if self._delete_pending:
            return
        self.editor.clear_errors()
        expression = self.editor.expression()
        if not expression.groups:
            self.editor.set_error("至少添加一个删除条件")
            return
        self._delete_pending = True
        self.delete_button.setEnabled(False)
        self.deleteRequested.emit(expression)

    def set_error(self, message: str) -> None:
        self._delete_pending = False
        self.delete_button.setEnabled(True)
        self.editor.set_error(message)

    def complete_delete(self) -> None:
        if self._delete_pending:
            self.accept()

    def reject(self) -> None:
        self._delete_pending = False
        super().reject()


__all__ = ["DeleteRowsDialog"]
