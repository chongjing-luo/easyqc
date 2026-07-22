"""Standard transactional dialog around the typed grouped filter editor."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from gui_qt.filter_panel import FilterPanel
from models.table_view_state import ColumnProfile, FilterExpression


class FilterDialog(QDialog):
    """Emit one complete filter draft; never mutate applied Table state itself."""

    applyRequested = Signal(object)

    def __init__(
        self,
        profiles: tuple[ColumnProfile, ...],
        applied_filter: FilterExpression,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(applied_filter, FilterExpression):
            raise TypeError("FilterDialog requires a FilterExpression")
        self.setObjectName("filterDialog")
        self.setWindowTitle("Filter rows")
        self.setAccessibleName("Filter table rows")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._apply_pending = False

        layout = QVBoxLayout(self)
        self.editor = FilterPanel(profiles, self)
        self.editor.set_expression(applied_filter)
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
        self.apply_button.setAccessibleName("Apply filter draft")
        self.cancel_button.setAccessibleName("Cancel filter editing")
        self.reset_button.setAccessibleName("Reset filter draft")

        self.apply_button.clicked.connect(self._request_apply)
        self.cancel_button.clicked.connect(self.reject)
        self.reset_button.clicked.connect(self.editor.reset_draft)
        self.resize(840, 620)

    def _request_apply(self) -> None:
        if self._apply_pending:
            return
        self.editor.clear_errors()
        expression = self.editor.expression()
        self._apply_pending = True
        self.apply_button.setEnabled(False)
        self.applyRequested.emit(expression)

    def set_error(
        self,
        message: str,
        *,
        group_id: str | None = None,
        condition_id: str | None = None,
    ) -> None:
        self._apply_pending = False
        self.apply_button.setEnabled(True)
        self.editor.set_error(
            message,
            group_id=group_id,
            condition_id=condition_id,
        )

    def complete_apply(self) -> None:
        if self._apply_pending:
            self.accept()

    def reject(self) -> None:
        self._apply_pending = False
        super().reject()


__all__ = ["FilterDialog"]
