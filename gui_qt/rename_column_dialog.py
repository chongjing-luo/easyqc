"""One-column rename draft shared by import and master-list workspaces."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QVBoxLayout, QWidget,
)

from gui_qt.i18n import LanguageController, protect_user_text, translate_ui_text
from gui_qt.theme import configure_combo_box_presentation, set_button_role


class RenameColumnDialog(QDialog):
    """Emit (old, new) names; errors retain the draft, Cancel performs no write."""

    renameRequested = Signal(str, str)

    def __init__(
        self, columns: tuple[str, ...], parent: QWidget | None = None, *,
        protected_columns: tuple[str, ...] = (),
        language: LanguageController | None = None,
    ) -> None:
        super().__init__(parent)
        if not columns or any(not isinstance(name, str) or not name for name in columns):
            raise ValueError("Column names must be non-empty strings")
        if len(set(columns)) != len(columns):
            raise ValueError("Column names must be unique")
        self.language = language
        self._columns = frozenset(columns)
        self._pending = False
        self.setObjectName("renameColumnDialog")
        self.setWindowTitle(self._text("重命名列"))
        self.setModal(True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        layout = QVBoxLayout(self)
        hint = QLabel(self._text("只修改列名，数据值和评分记录保持不变。"), self)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.source_combo = QComboBox(self)
        for column in columns:
            if column not in protected_columns:
                self.source_combo.addItem(column, column)
        self.source_combo.setAccessibleName(self._text("原列名"))
        self.source_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.source_combo.setMinimumContentsLength(20)
        self.source_combo.setMaxVisibleItems(12)
        configure_combo_box_presentation(self.source_combo)
        protect_user_text(self.source_combo, "items", "toolTip")
        self.name_edit = QLineEdit(self)
        self.name_edit.setAccessibleName(self._text("新列名"))
        protect_user_text(self.name_edit, "text")
        form.addRow(self._text("原列名"), self.source_combo)
        form.addRow(self._text("新列名"), self.name_edit)
        layout.addLayout(form)
        self.error_label = QLabel(self)
        self.error_label.setTextFormat(Qt.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setProperty("role", "error")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.button_box = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel, self)
        self.apply_button = self.button_box.button(QDialogButtonBox.Apply)
        self.apply_button.setText(self._text("应用"))
        self.button_box.button(QDialogButtonBox.Cancel).setText(self._text("取消"))
        set_button_role(self.apply_button, "primary")
        layout.addWidget(self.button_box)
        self.source_combo.currentIndexChanged.connect(self._select_source)
        self.apply_button.clicked.connect(self._request_rename)
        self.button_box.rejected.connect(self.reject)
        self._select_source()
        self.resize(480, 230)

    def _text(self, source: str) -> str:
        return self.language.translate_source(source) if self.language else translate_ui_text(source)

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def _select_source(self) -> None:
        self.name_edit.setText(self.source_combo.currentData() or "")
        self.source_combo.setToolTip(self.source_combo.currentText())
        self.name_edit.selectAll()

    def _request_rename(self) -> None:
        if self._pending:
            return
        old, new = self.source_combo.currentData(), self.name_edit.text().strip()
        if not old or not new or old == new:
            self.set_error("请输入不同的非空新列名")
        elif new in self._columns:
            self.set_error("新列名已存在")
        else:
            self._pending = True
            self.apply_button.setEnabled(False)
            self.source_combo.setEnabled(False)
            self.name_edit.setEnabled(False)
            self.button_box.button(QDialogButtonBox.Cancel).setEnabled(False)
            self.renameRequested.emit(old, new)

    def set_error(self, message: str) -> None:
        self._pending = False
        self.error_label.setText(self._text(message))
        self.error_label.setVisible(bool(message))
        self.apply_button.setEnabled(self.source_combo.count() > 0)
        self.source_combo.setEnabled(True)
        self.name_edit.setEnabled(True)
        self.button_box.button(QDialogButtonBox.Cancel).setEnabled(True)

    def complete_rename(self) -> None:
        self._pending = False
        self.accept()

    def reject(self) -> None:
        if not self._pending:
            super().reject()

    def closeEvent(self, event) -> None:
        if self._pending:
            event.ignore()
        else:
            super().closeEvent(event)
