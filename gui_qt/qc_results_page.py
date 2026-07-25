"""Direct read-only Qt page for one accepted QC results projection."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget

from core.table_view_service import TableViewService
from gui_qt.i18n import LanguageController, translate_ui_text
from gui_qt.table_workspace import QtTableWorkspace
from models.derived_formula import DerivedColumnFormula


class _QtQcResultsTableWorkspace(QtTableWorkspace):
    """Localize only the results presentation over shared Table behavior."""

    def _rebuild_inspector_panels(self) -> None:
        super()._rebuild_inspector_panels()
        for index, label in enumerate(("筛选", "排序", "列显示")):
            self.inspector_tabs.setTabText(index, self._ui_text(label))

    def _update_status(self) -> None:
        super()._update_status()
        matched = self.result.matched_total
        total = self.result.source_total
        start, end = self.visible_range
        visible = len(self.applied_state.columns.visible_columns)
        column_total = len(self.applied_state.columns.order)
        self.count_label.setText(
            self._ui_text(f"{matched:,} / {total:,} 行")
        )
        self.range_label.setText(
            self._ui_text(f"第 {start:,}–{end:,} 行")
        )
        self.columns_status_label.setText(
            self._ui_text(f"列 {visible}/{column_total}")
        )
        self.filter_action.setText(
            self._ui_text(
                f"筛选 ({len(self.applied_state.conditions)})"
            )
        )
        self.sort_action.setText(
            self._ui_text(f"排序 ({len(self.applied_state.sort_rules)})")
        )
        self.columns_action.setText(
            self._ui_text(f"列显示 ({visible}/{column_total})")
        )
        if self.selection_outside_view:
            self.selection_status_label.setText(
                self._ui_text("所选记录不在当前视图中")
            )
        elif self.selected_source_position is not None:
            self.selection_status_label.setText(
                self._ui_text(
                    f"已选原始第 {self.selected_source_position + 1:,} 行"
                )
            )
        else:
            self.selection_status_label.setText(
                self._ui_text("未选择记录")
            )


class QtQcResultsPage(QWidget):
    """Compose shared read-only Table behavior with results refresh feedback."""

    def __init__(
        self,
        source: pd.DataFrame,
        *,
        refresh_callback: Callable[[], bool],
        derive_column_callback: (
            Callable[[DerivedColumnFormula], str] | None
        ) = None,
        derive_preview_source: Callable[[], pd.DataFrame] | None = None,
        on_derived_column_committed: Callable[[str], None] | None = None,
        language: LanguageController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(source, pd.DataFrame):
            raise TypeError("QtQcResultsPage source must be a pandas DataFrame")
        if not callable(refresh_callback):
            raise TypeError("QtQcResultsPage refresh_callback must be callable")
        self._refresh_callback = refresh_callback
        self._derive_column_callback = derive_column_callback
        self._derive_preview_source = derive_preview_source
        self._on_derived_column_committed = on_derived_column_committed
        self.language = language
        self._refresh_busy = False
        self.setObjectName("qcResultsPage")
        self.setAccessibleName("质控结果")
        self._build_ui(source)

    def _build_ui(self, source: pd.DataFrame) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        self.table_workspace = _QtQcResultsTableWorkspace(
            source,
            derive_column_callback=self._derive_column_callback,
            derive_preview_source=self._derive_preview_source,
            on_derived_column_committed=self._on_derived_column_committed,
            page_size=25,
            language=self.language,
            parent=self,
        )
        self.table_workspace.setObjectName("qcResultsWorkspace")
        self.table_workspace.setAccessibleName("质控结果表格工具")
        self.table_workspace.action_toolbar.setObjectName("qcResultsToolbar")
        self.table_workspace.applied_toolbar.setObjectName("qcResultsAppliedView")
        self.table_workspace.workspace_splitter.setObjectName("qcResultsTableSplitter")
        self.table_workspace.table_panel.setObjectName("qcResultsTablePanel")
        self.table_workspace.table_surface.setObjectName("qcResultsTableSurface")
        self.table_workspace.table_view.setObjectName("qcResultsTable")
        self.table_workspace.table_view.setAccessibleName("质控结果表格")
        self.table_workspace.table_view.setAccessibleDescription(
            "只读质控结果；筛选、排序和列设置作用于完整结果。"
        )
        self.table_workspace.pinned_view.setObjectName("qcResultsPinnedTable")
        self.table_workspace.pinned_view.setAccessibleName("固定 ezqcid 列")
        self.table_workspace.empty_state_label.setObjectName("qcResultsEmptyState")
        self.table_workspace.count_label.setObjectName("qcResultsStatus")
        self.table_workspace.error_label.setObjectName("qcResultsTableError")
        self.table_workspace.view_inspector.setObjectName("qcResultsViewInspector")
        self.table_workspace.inspector_scroll.setObjectName("qcResultsInspectorScroll")
        self.table_workspace.inspector_tabs.setObjectName("qcResultsInspector")
        self.table_workspace.empty_state_label.setText("没有可显示的质控结果")
        self.table_workspace.find_edit.setPlaceholderText("搜索 ezqcid")
        self.table_workspace.find_edit.setObjectName("qcResultsFindIdentity")
        self.table_workspace.find_edit.setAccessibleName("搜索精确 ezqcid")
        self.table_workspace.find_action.setText("查找")
        self.table_workspace.export_action.setText("导出…")
        self.table_workspace.export_action.setToolTip("导出当前质控结果视图")
        self.table_workspace.previous_button.setText("上一页")
        self.table_workspace.next_button.setText("下一页")
        self.table_workspace.inspector_close_button.setText("关闭")
        self.table_workspace.inspector_reset_button.setText("重置")
        self.table_workspace.inspector_cancel_button.setText("取消")
        self.table_workspace.inspector_apply_button.setText("应用")
        for label in self.table_workspace.findChildren(QLabel):
            if label.text() == "View options":
                label.setText("视图设置")
            elif label.text() == "Rows":
                label.setText("每页")

        self.refresh_action = QAction("刷新结果", self)
        self.refresh_action.setObjectName("refreshQcResultsAction")
        self.refresh_action.setToolTip("刷新质控结果")
        self.refresh_action.setShortcut(QKeySequence("F5"))
        self.refresh_action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        self.refresh_action.triggered.connect(self.request_refresh)
        self.table_workspace.action_toolbar.insertAction(
            self.table_workspace.filter_action,
            self.refresh_action,
        )
        self.table_workspace.action_toolbar.insertSeparator(
            self.table_workspace.filter_action
        )
        self.refresh_button = self.table_workspace.action_toolbar.widgetForAction(
            self.refresh_action
        )
        self.refresh_button.setObjectName("refreshQcResultsButton")
        if isinstance(self.refresh_button, QToolButton):
            self.refresh_button.setAutoRaise(False)
        layout.addWidget(self.table_workspace, 1)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("qcResultsError")
        self.error_label.setProperty("role", "error")
        self.error_label.setAccessibleName("质控结果错误")
        self.error_label.setWordWrap(True)
        self.error_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.error_label.hide()
        layout.addWidget(self.error_label)

    def request_refresh(self) -> bool:
        """Request one shell-owned refresh without reading result files here."""

        if self._refresh_busy:
            return False
        self.show_error("")
        try:
            started = bool(self._refresh_callback())
        except Exception as exc:
            self.show_error(str(exc).strip() or type(exc).__name__)
            return False
        if not started:
            if not self.error_label.text():
                self.show_error("质控结果刷新请求未启动")
            return False
        self.show_error("")
        return True

    def replace_service(
        self,
        service: TableViewService,
        *,
        preserve_state: bool,
    ) -> None:
        """Replace the prepared projection while retaining compatible view state."""

        self.table_workspace.replace_service(service, preserve_state=preserve_state)
        self.set_refresh_busy(False)
        self.show_error("")

    def set_refresh_busy(self, busy: bool) -> None:
        self._refresh_busy = bool(busy)
        source = "刷新中…" if self._refresh_busy else "刷新结果"
        self.refresh_action.setText(translate_ui_text(source))
        self.refresh_action.setEnabled(not self._refresh_busy)

    def show_error(self, message: str) -> None:
        text = str(message)
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.table_workspace.close()
        super().closeEvent(event)


__all__ = ["QtQcResultsPage"]
