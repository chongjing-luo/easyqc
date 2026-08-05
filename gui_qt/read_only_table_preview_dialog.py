"""Read-only dialog over one already prepared professional Table snapshot."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from core.table_view_service import TableViewService
from gui_qt.i18n import LanguageController, get_or_create_language_controller
from gui_qt.table_workspace import QtTableWorkspace
from models.table_view_state import TableViewState


class ReadOnlyTablePreviewDialog(QDialog):
    """Own one export-capable, non-mutating Table view over a prepared service."""

    def __init__(
        self,
        service: TableViewService,
        initial_state: TableViewState,
        title: str,
        parent: QWidget | None = None,
        *,
        language: LanguageController | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(service, TableViewService):
            raise TypeError("ReadOnlyTablePreviewDialog requires TableViewService")
        if not isinstance(initial_state, TableViewState):
            raise TypeError("ReadOnlyTablePreviewDialog requires TableViewState")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("ReadOnlyTablePreviewDialog title cannot be blank")
        self.language = language or get_or_create_language_controller()
        self._title_source = title.strip()
        self._workspace_shutdown = False
        self.setObjectName("readOnlyTablePreviewDialog")
        self.setAccessibleName("EasyQC 表格预览")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setModal(False)

        layout = QVBoxLayout(self)
        self.table_workspace = QtTableWorkspace.from_prepared_service(
            service,
            initial_state,
            language=self.language,
            parent=self,
        )
        self.table_workspace.setObjectName("readOnlyTablePreviewWorkspace")
        self.table_workspace.table_view.setAccessibleDescription(
            "只读名单；筛选和排序作用于完整结果。"
        )
        layout.addWidget(self.table_workspace, 1)
        self.resize(980, 680)
        self.language.register_root(self)
        self._retranslate_title()
        self.language.languageChanged.connect(self._retranslate_title)

    def _retranslate_title(self, _language: str | None = None) -> None:
        self.setWindowTitle(self.language.translate_source(self._title_source))

    def _shutdown_workspace(self) -> None:
        if self._workspace_shutdown:
            return
        self._workspace_shutdown = True
        self.table_workspace.close()
        self.language.unregister_root(self)

    def done(self, result: int) -> None:
        self._shutdown_workspace()
        super().done(result)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutdown_workspace()
        super().closeEvent(event)


__all__ = ["ReadOnlyTablePreviewDialog"]
