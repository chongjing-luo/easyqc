"""Searchable transactional checklist for deleting exact table columns."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_qt.i18n import LanguageController, translate_ui_text
from gui_qt.theme import set_button_role


_COLUMN_ROLE = int(Qt.ItemDataRole.UserRole)


class DeleteColumnsDialog(QDialog):
    """Emit exact checked columns while keeping Cancel and Reset local."""

    deleteRequested = Signal(object)

    def __init__(
        self,
        columns: tuple[str, ...],
        parent: QWidget | None = None,
        *,
        protected_columns: tuple[str, ...] = (),
        language: LanguageController | None = None,
    ) -> None:
        super().__init__(parent)
        if (
            not isinstance(columns, tuple)
            or not columns
            or not all(isinstance(column, str) and column for column in columns)
        ):
            raise TypeError("DeleteColumnsDialog requires non-empty column names")
        if len(set(columns)) != len(columns):
            raise ValueError("DeleteColumnsDialog columns cannot repeat")
        if not isinstance(protected_columns, tuple) or not all(
            column in columns for column in protected_columns
        ):
            raise ValueError("Protected deletion columns must exist")
        self.language = language
        self._protected = frozenset(protected_columns)
        self._delete_pending = False
        self.setObjectName("deleteColumnsDialog")
        self.setWindowTitle("删除列")
        self.setAccessibleName("选择要删除的表格列")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._build_ui(columns)

    def _build_ui(self, columns: tuple[str, ...]) -> None:
        layout = QVBoxLayout(self)
        hint = QLabel("勾选要删除的列；可以一次删除多列。", self)
        hint.setObjectName("deleteColumnsHint")
        hint.setProperty("role", "secondary")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.search_label = QLabel("搜索列", self)
        self.search_edit = QLineEdit(self)
        self.search_label.setBuddy(self.search_edit)
        self.search_edit.setPlaceholderText("搜索列")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("搜索可删除列")
        layout.addWidget(self.search_label)
        layout.addWidget(self.search_edit)

        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("deleteColumnList")
        self.list_widget.setAccessibleName("要删除的表格列")
        self.list_widget.setAlternatingRowColors(True)
        for column in columns:
            item = QListWidgetItem(self.list_widget)
            item.setData(_COLUMN_ROLE, column)
            protected = column in self._protected
            item.setText(
                self._ui_text(f"{column}   · 受保护")
                if protected
                else column
            )
            item.setData(Qt.ItemDataRole.AccessibleTextRole, item.text())
            flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
            if protected:
                flags &= ~Qt.ItemFlag.ItemIsEnabled
            else:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            item.setFlags(flags)
            item.setCheckState(Qt.CheckState.Unchecked)
        layout.addWidget(self.list_widget, 1)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("deleteColumnsError")
        self.error_label.setProperty("role", "error")
        self.error_label.setAccessibleName("删除列错误")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset,
            self,
        )
        self.delete_button = self.button_box.addButton(
            "删除所选列",
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
        self.delete_button.setAccessibleName("确认删除所选列")
        self.cancel_button.setAccessibleName("取消删除列")
        self.reset_button.setAccessibleName("清除删除列选择")
        set_button_role(self.delete_button, "danger")
        layout.addWidget(self.button_box)

        self.search_edit.textChanged.connect(self._refresh_search)
        self.list_widget.itemChanged.connect(lambda _item: self.set_error(""))
        self.delete_button.clicked.connect(self._request_delete)
        self.cancel_button.clicked.connect(self.reject)
        self.reset_button.clicked.connect(self.reset_selection)
        self.resize(620, 560)

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def _ui_text(self, source: str) -> str:
        if self.language is not None:
            return self.language.translate_source(source)
        return translate_ui_text(source)

    def item_for_column(self, column: str) -> QListWidgetItem:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.data(_COLUMN_ROLE) == column:
                return item
        raise KeyError(column)

    def selected_columns(self) -> tuple[str, ...]:
        return tuple(
            str(item.data(_COLUMN_ROLE))
            for row in range(self.list_widget.count())
            if (item := self.list_widget.item(row)).checkState()
            == Qt.CheckState.Checked
            and item.flags() & Qt.ItemFlag.ItemIsEnabled
        )

    def _refresh_search(self, query: str) -> None:
        needle = str(query).strip().casefold()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            column = str(item.data(_COLUMN_ROLE))
            item.setHidden(needle not in column.casefold())

    def reset_selection(self) -> None:
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                item.setCheckState(Qt.CheckState.Unchecked)
        self.set_error("")

    def _request_delete(self) -> None:
        if self._delete_pending:
            return
        columns = self.selected_columns()
        if not columns:
            self.set_error("至少选择一个要删除的列")
            return
        self._delete_pending = True
        self.delete_button.setEnabled(False)
        self.deleteRequested.emit(columns)

    def set_error(self, message: str) -> None:
        self._delete_pending = False
        self.delete_button.setEnabled(True)
        self.error_label.setText(self._ui_text(str(message)))
        self.error_label.setVisible(bool(message))

    def complete_delete(self) -> None:
        if self._delete_pending:
            self.accept()

    def reject(self) -> None:
        self._delete_pending = False
        super().reject()


__all__ = ["DeleteColumnsDialog"]
