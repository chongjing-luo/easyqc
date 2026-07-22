"""Standard transactional dialog around the column-layout draft editor."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from gui_qt.columns_panel import ColumnsPanel
from models.table_view_state import ColumnViewState


class ColumnsDialog(QDialog):
    """Emit one complete column draft; keep Reset and Cancel local."""

    applyRequested = Signal(object)

    def __init__(
        self,
        applied_state: ColumnViewState,
        default_state: ColumnViewState,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(applied_state, ColumnViewState) or not isinstance(
            default_state, ColumnViewState
        ):
            raise TypeError("ColumnsDialog requires ColumnViewState values")
        if set(applied_state.order) != set(default_state.order):
            raise ValueError("Applied and default column states must describe one table")
        self.setObjectName("columnsDialog")
        self.setWindowTitle("Choose columns")
        self.setAccessibleName("Choose table columns")
        self.setModal(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._default_state = default_state
        self._apply_pending = False

        layout = QVBoxLayout(self)
        self.editor = ColumnsPanel(applied_state, self)
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
        self.apply_button.setAccessibleName("Apply column draft")
        self.cancel_button.setAccessibleName("Cancel column editing")
        self.reset_button.setAccessibleName("Restore default columns")
        self.apply_button.clicked.connect(self._request_apply)
        self.cancel_button.clicked.connect(self.reject)
        self.reset_button.clicked.connect(
            lambda _checked=False: self.editor.set_state(self._default_state)
        )
        self.resize(620, 560)

    def _request_apply(self) -> None:
        if self._apply_pending:
            return
        self.editor.set_error("")
        self._apply_pending = True
        self.apply_button.setEnabled(False)
        self.applyRequested.emit(self.editor.state())

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


__all__ = ["ColumnsDialog"]
