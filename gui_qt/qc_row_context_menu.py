"""Shared Qt menu renderer for one typed QC row context."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from gui_qt.i18n import protect_user_text
from models.qc_row_context import (
    QcModuleMenuEntry,
    QcRecordMenuEntry,
    QcRowContext,
)


class QcRowContextMenu(QMenu):
    """Render module and historical-record actions without resolving facts."""

    def __init__(
        self,
        context: QcRowContext,
        *,
        on_module: Callable[[QcModuleMenuEntry], None],
        on_record: Callable[[QcRecordMenuEntry], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(context, QcRowContext):
            raise TypeError("QcRowContextMenu requires QcRowContext")
        if not callable(on_module) or not callable(on_record):
            raise TypeError("QcRowContextMenu callbacks must be callable")
        self.context = context
        self.setObjectName("qcRowContextMenu")
        self.setAccessibleName(f"质控操作：{context.ezqcid}")

        self.modules_menu = self.addMenu("质控模块")
        self.modules_menu.setObjectName("qcRowModulesMenu")
        self.records_menu = self.addMenu("已有质控记录")
        self.records_menu.setObjectName("qcRowRecordsMenu")
        self.module_actions: dict[str, QAction] = {}
        self.record_actions: dict[tuple[str, str, str], QAction] = {}

        if not context.modules:
            action = self.modules_menu.addAction("没有可用的质控模块")
            action.setEnabled(False)
        for entry in context.modules:
            action = self.modules_menu.addAction(entry.label)
            protect_user_text(action, "text")
            action.setData(entry.module_name)
            action.setEnabled(entry.enabled)
            if entry.disabled_reason:
                action.setToolTip(entry.disabled_reason)
            elif entry.read_only:
                action.setToolTip("此模块将以只读模式打开")
            action.triggered.connect(
                lambda _checked=False, selected=entry: on_module(selected)
            )
            self.module_actions[entry.module_name] = action

        if not context.records:
            action = self.records_menu.addAction("没有已有质控记录")
            action.setEnabled(False)
        for entry in context.records:
            action = self.records_menu.addAction(
                f"{entry.module_label} · {entry.rater}"
            )
            protect_user_text(action, "text")
            action.setData(entry.key)
            if entry.recorded_at is not None:
                action.setToolTip(entry.recorded_at.strftime("%Y-%m-%d %H:%M:%S"))
            action.triggered.connect(
                lambda _checked=False, selected=entry: on_record(selected)
            )
            self.record_actions[entry.key] = action


__all__ = ["QcRowContextMenu"]
