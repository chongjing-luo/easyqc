"""Shared Qt menu renderer for one typed QC row context."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from gui_qt.i18n import LanguageController, protect_user_text
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
        on_copy: Callable[[], None] | None = None,
        on_execute: Callable[[QcModuleMenuEntry], None] | None = None,
        on_open_execute: Callable[[QcModuleMenuEntry], None] | None = None,
        parent: QWidget | None = None,
        language: LanguageController | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(context, QcRowContext):
            raise TypeError("QcRowContextMenu requires QcRowContext")
        if not callable(on_module) or not callable(on_record):
            raise TypeError("QcRowContextMenu callbacks must be callable")
        if any(callback is not None and not callable(callback)
               for callback in (on_copy, on_execute, on_open_execute)):
            raise TypeError("QcRowContextMenu optional callbacks must be callable or None")
        self.context = context
        self.setObjectName("qcRowContextMenu")
        self.setAccessibleName(f"质控操作：{context.easyqcid}")

        self.copy_action = self.addAction("复制")
        self.copy_action.setEnabled(on_copy is not None)
        if on_copy is not None:
            self.copy_action.triggered.connect(lambda _checked=False: on_copy())
        self.modules_menu = self.addMenu("打开质控页")
        self.modules_menu.setObjectName("qcRowModulesMenu")
        self.records_menu = self.addMenu("已有质控记录")
        self.records_menu.setObjectName("qcRowRecordsMenu")
        self.execute_menu = self.addMenu("仅执行命令")
        self.execute_menu.setObjectName("qcRowExecuteMenu")
        self.open_execute_menu = self.addMenu("打开质控页并执行命令")
        self.open_execute_menu.setObjectName("qcRowOpenExecuteMenu")
        self.module_actions: dict[str, QAction] = {}
        self.linked_module_actions: dict[tuple[str, str, str], QAction] = {}
        self.execute_actions: dict[str, QAction] = {}
        self.linked_execute_actions: dict[tuple[str, str, str], QAction] = {}
        self.open_execute_actions: dict[str, QAction] = {}
        self.linked_open_execute_actions: dict[tuple[str, str, str], QAction] = {}
        self.record_actions: dict[tuple[str, str, str], QAction] = {}

        for menu, callback, actions, linked_actions in (
            (self.modules_menu, on_module, self.module_actions, self.linked_module_actions),
            (self.execute_menu, on_execute, self.execute_actions, self.linked_execute_actions),
            (self.open_execute_menu, on_open_execute, self.open_execute_actions, self.linked_open_execute_actions),
        ):
            menu.setEnabled(callback is not None)
            if not context.modules and not context.linked_modules:
                menu.addAction("没有可用的质控模块").setEnabled(False)
            for entry in context.modules + context.linked_modules:
                linked = entry in context.linked_modules
                label = f"{entry.easyqcid} · {entry.label} · {entry.rater}" if linked else entry.label
                action = menu.addAction(label)
                protect_user_text(action, "text")
                key = (entry.easyqcid, entry.module_name, entry.rater)
                action.setData(key if linked else entry.module_name)
                action.setEnabled(entry.enabled and callback is not None)
                if entry.disabled_reason:
                    action.setToolTip(entry.disabled_reason)
                elif entry.read_only:
                    action.setToolTip("此模块将以只读模式打开")
                if callback is not None:
                    action.triggered.connect(
                        lambda _checked=False, selected=entry, handler=callback: handler(selected)
                    )
                if linked:
                    linked_actions[key] = action
                else:
                    actions[entry.module_name] = action

        if not context.records and not context.linked_records:
            action = self.records_menu.addAction("没有已有质控记录")
            action.setEnabled(False)
        for entry in context.records + context.linked_records:
            label = f"{entry.module_label} · {entry.rater}"
            if entry in context.linked_records:
                label = f"{entry.easyqcid} · {label}"
            action = self.records_menu.addAction(
                label
            )
            protect_user_text(action, "text")
            action.setData(entry.key)
            if entry.recorded_at is not None:
                action.setToolTip(entry.recorded_at.strftime("%Y-%m-%d %H:%M:%S"))
            action.triggered.connect(
                lambda _checked=False, selected=entry: on_record(selected)
            )
            self.record_actions[entry.key] = action
        if language is not None:
            language.register_root(self)


__all__ = ["QcRowContextMenu"]
