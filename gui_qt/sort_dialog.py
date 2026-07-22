"""Standard transactional dialog around the ordered sort draft editor."""

from __future__ import annotations

from collections import Counter

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from gui_qt.sort_panel import SortPanel
from models.table_view_state import SortRule


class SortDialog(QDialog):
    """Emit one complete sort draft; never mutate applied Table state itself."""

    applyRequested = Signal(object)

    def __init__(
        self,
        columns: tuple[str, ...],
        applied_rules: tuple[SortRule, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not all(isinstance(rule, SortRule) for rule in applied_rules):
            raise TypeError("SortDialog requires SortRule values")
        self.setObjectName("sortDialog")
        self.setWindowTitle("Sort rows")
        self.setAccessibleName("Sort table rows")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._apply_pending = False

        layout = QVBoxLayout(self)
        self.editor = SortPanel(tuple(columns), self)
        self.editor.set_rules(tuple(applied_rules))
        layout.addWidget(self.editor, 1)

        buttons = (
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Reset
        )
        self.button_box = QDialogButtonBox(buttons, self)
        layout.addWidget(self.button_box)
        self.apply_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Apply
        )
        self.cancel_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.reset_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Reset
        )
        self.apply_button.setAccessibleName("Apply sort draft")
        self.cancel_button.setAccessibleName("Cancel sort editing")
        self.reset_button.setAccessibleName("Clear sort draft")
        self.apply_button.clicked.connect(self._request_apply)
        self.cancel_button.clicked.connect(self.reject)
        self.reset_button.clicked.connect(
            lambda _checked=False: self.editor.set_rules(())
        )
        self.resize(760, 480)

    def _request_apply(self) -> None:
        if self._apply_pending:
            return
        rules = self.editor.rules()
        duplicates = sorted(
            column
            for column, count in Counter(rule.column for rule in rules).items()
            if count > 1
        )
        if duplicates:
            self.set_error(
                "Each sort column can only be used once: " + ", ".join(duplicates)
            )
            return
        self.editor.set_error("")
        self._apply_pending = True
        self.apply_button.setEnabled(False)
        self.applyRequested.emit(rules)

    def set_error(self, message: str) -> None:
        self._apply_pending = False
        self.apply_button.setEnabled(True)
        self.editor.set_error(message)

    def complete_apply(self) -> None:
        if self._apply_pending:
            self.accept()

    def reject(self) -> None:
        self._apply_pending = False
        super().reject()


__all__ = ["SortDialog"]
