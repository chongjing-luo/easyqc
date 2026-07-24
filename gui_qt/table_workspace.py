"""Professional Qt Table workspace over the GUI-independent Core service."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

import pandas as pd
from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QSplitter,
    QTableView,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.table_export_service import (
    DEFAULT_EXPORT_CHUNK_ROWS,
    ExportReceipt,
    TableExportCancelled,
    TableExportError,
    TableExportService,
)
from core.table_view_service import QcIdentityError, TableViewError, TableViewService
from gui_qt.columns_dialog import ColumnsDialog
from gui_qt.columns_panel import ColumnsPanel
from gui_qt.derived_column_dialog import DerivedColumnDialog
from gui_qt.filter_dialog import FilterDialog
from gui_qt.filter_panel import FilterPanel, operator_label
from gui_qt.i18n import LanguageController, translate_ui_text
from gui_qt.qc_row_context_menu import QcRowContextMenu
from gui_qt.sort_dialog import SortDialog
from gui_qt.sort_panel import SortPanel
from gui_qt.table_model import QtTableModel, QtTableRowReference
from gui_qt.task_runner import RevisionedTaskController
from gui_qt.theme import set_button_role
from models.column_recipe import ColumnRecipe
from models.qc_row_context import QcModuleMenuEntry, QcRecordMenuEntry, QcRowContext
from models.table_view_state import (
    ColumnViewState,
    FilterCondition,
    FilterExpression,
    FilterGroup,
    SortRule,
    TableViewResult,
    TableViewState,
)


class QtTableWorkspace(QWidget):
    """Coordinate typed view state, bounded rendering and identity-safe actions."""

    exportProgress = Signal(int, int, int)
    deriveBusyChanged = Signal(bool)
    PINNED_SURFACE_FRACTION = 0.45

    def __init__(
        self,
        source: pd.DataFrame,
        *,
        on_open_qc: Callable[[str], None] | None = None,
        derive_column_callback: Callable[[ColumnRecipe], str] | None = None,
        derive_preview_source: Callable[[], pd.DataFrame] | None = None,
        on_derived_column_committed: Callable[[str], None] | None = None,
        row_context_provider: Callable[[str], QcRowContext] | None = None,
        on_open_qc_module: (
            Callable[[str, QcModuleMenuEntry], None] | None
        ) = None,
        on_open_qc_record: Callable[[QcRecordMenuEntry], None] | None = None,
        page_size: int = 200,
        background_row_threshold: int = 10_000,
        language: LanguageController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(source, pd.DataFrame):
            raise TypeError("QtTableWorkspace source must be a pandas DataFrame")
        if derive_preview_source is not None and not callable(derive_preview_source):
            raise TypeError("derive_preview_source must be callable")
        self.setObjectName("qtTableWorkspace")
        self.setAccessibleName("EasyQC 表格工作区")
        self.language = language
        self._pinned_width_update_pending = False
        self._pinned_width_timer = QTimer(self)
        self._pinned_width_timer.setSingleShot(True)
        self._pinned_width_timer.timeout.connect(
            self._apply_scheduled_pinned_width_update
        )
        self._scroll_refresh_timer = QTimer(self)
        self._scroll_refresh_timer.setSingleShot(True)
        self._scroll_refresh_timer.timeout.connect(
            self._refresh_external_scrollbars
        )
        self.service = TableViewService(source)
        self.export_service = TableExportService(self.service)
        if background_row_threshold <= 0:
            raise ValueError("background_row_threshold must be greater than zero")
        self.background_row_threshold = int(background_row_threshold)
        self.task_controller = RevisionedTaskController(self)
        self.task_controller.resultReady.connect(self._accept_background_result)
        self.task_controller.errorRaised.connect(self._handle_background_error)
        self.task_controller.busyChanged.connect(self._set_background_busy)
        self.export_task_controller = RevisionedTaskController(self)
        self.export_task_controller.resultReady.connect(self._accept_export_result)
        self.export_task_controller.errorRaised.connect(self._handle_export_error)
        self.export_task_controller.busyChanged.connect(self._set_export_busy)
        self.exportProgress.connect(self._update_export_progress)
        self._pending_state: TableViewState | None = None
        self._pending_reset_page = False
        self._export_cancel_event: Event | None = None
        self._export_revision: int | None = None
        self.last_export_receipt: ExportReceipt | None = None
        self.on_open_qc = on_open_qc
        self.derive_column_callback = derive_column_callback
        self.derive_preview_source = derive_preview_source
        self.on_derived_column_committed = on_derived_column_committed
        self.row_context_provider = row_context_provider
        self.on_open_qc_module = on_open_qc_module
        self.on_open_qc_record = on_open_qc_record
        self.active_row_context_menu: QcRowContextMenu | None = None
        self.initial_state = self.service.default_state(page_size=page_size)
        self.applied_state = self.initial_state
        self.draft_state: TableViewState | None = None
        self.filter_dialog: FilterDialog | None = None
        self.sort_dialog: SortDialog | None = None
        self.columns_dialog: ColumnsDialog | None = None
        self.derived_column_dialog: DerivedColumnDialog | None = None
        self._derive_busy = False
        self._inspector_origin_revision: int | None = None
        self.result = self.service.apply_state(self.applied_state)
        self.row_window = self.service.get_window(
            self.result,
            0,
            columns=self.applied_state.columns.visible_columns,
        )
        self.page_offset = 0
        self.selected_source_position: int | None = None
        self.selection_outside_view = False
        self._rendering = False
        self._build_ui()
        self._render_result()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        self.action_toolbar = QToolBar("表格操作", self)
        self.action_toolbar.setObjectName("tableToolbar")
        self.action_toolbar.setAccessibleName("表格操作")
        self.action_toolbar.setMovable(False)
        self.action_toolbar.setFloatable(False)
        self.action_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._toolbar_shortcuts: list[QShortcut] = []

        self.filter_action = self._add_toolbar_action(
            self.action_toolbar,
            "筛选",
            QKeySequence("Ctrl+Shift+F"),
            self.open_filter_inspector,
        )
        self.sort_action = self._add_toolbar_action(
            self.action_toolbar,
            "排序",
            QKeySequence("Ctrl+Shift+S"),
            self.open_sort_inspector,
        )
        self.columns_action = self._add_toolbar_action(
            self.action_toolbar,
            "列显示",
            QKeySequence("Ctrl+Shift+C"),
            self.open_columns_inspector,
        )
        self.derive_action = (
            self._add_toolbar_action(
                self.action_toolbar,
                "新增列",
                QKeySequence("Ctrl+Shift+D"),
                self.open_derived_column_dialog,
            )
            if self.derive_column_callback is not None
            else None
        )
        self.action_toolbar.addSeparator()
        self.filter_button = self.action_toolbar.widgetForAction(self.filter_action)
        self.sort_button = self.action_toolbar.widgetForAction(self.sort_action)
        self.columns_button = self.action_toolbar.widgetForAction(self.columns_action)
        self.derive_button = (
            self.action_toolbar.widgetForAction(self.derive_action)
            if self.derive_action is not None
            else None
        )
        self.filter_button.setObjectName("filterButton")
        self.sort_button.setObjectName("sortButton")
        self.columns_button.setObjectName("columnsButton")
        if self.derive_button is not None:
            self.derive_button.setObjectName("deriveColumnButton")
        self.find_edit = QLineEdit(self.action_toolbar)
        self.find_edit.setObjectName("findIdentity")
        self.find_edit.setAccessibleName("查找精确 ezqcid")
        self.find_edit.setPlaceholderText("查找精确 ezqcid")
        self.find_edit.setClearButtonEnabled(True)
        self.action_toolbar.addWidget(self.find_edit)
        self.find_action = self._add_toolbar_action(
            self.action_toolbar,
            "查找",
            QKeySequence("Ctrl+F"),
            lambda: self.find_identity_exact(self.find_edit.text()),
        )
        self.export_action = self._add_toolbar_action(
            self.action_toolbar,
            "导出…",
            QKeySequence("Ctrl+E"),
            self._choose_export_destination,
        )
        self.cancel_export_action = self._add_toolbar_action(
            self.action_toolbar,
            "取消导出",
            QKeySequence("Ctrl+Shift+E"),
            self.cancel_export,
        )
        self.cancel_export_action.setVisible(False)
        self.find_button = self.action_toolbar.widgetForAction(self.find_action)
        self.critical_actions = tuple(
            action
            for action in (
            self.filter_action,
            self.sort_action,
            self.columns_action,
            self.derive_action,
            self.find_action,
            self.export_action,
            self.cancel_export_action,
            )
            if action is not None
        )
        self.critical_shortcuts = tuple(self._toolbar_shortcuts)
        self.open_qc_action = QAction(self)
        self.open_qc_action.setObjectName("internalOpenQcAction")
        self.open_qc_action.setVisible(False)
        self.open_qc_action.setEnabled(False)
        self.open_qc_button = None
        layout.addWidget(self.action_toolbar)

        self.applied_toolbar = QToolBar("已应用视图", self)
        self.applied_toolbar.setObjectName("appliedFilterChips")
        self.applied_toolbar.setAccessibleName("已应用筛选")
        self.applied_toolbar.setMovable(False)
        self.applied_toolbar.setFloatable(False)
        self.applied_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        layout.addWidget(self.applied_toolbar)

        self.workspace_splitter = QSplitter(Qt.Horizontal, self)
        self.workspace_splitter.setObjectName("tableWorkspaceSplitter")
        self.workspace_splitter.setAccessibleName("表格与视图设置")
        self.workspace_splitter.setChildrenCollapsible(False)
        self.table_panel = QWidget(self.workspace_splitter)
        self.table_panel.setObjectName("tableMainPanel")
        self.table_panel.setMinimumWidth(0)
        table_panel_layout = QVBoxLayout(self.table_panel)
        table_panel_layout.setContentsMargins(0, 0, 0, 0)
        table_panel_layout.setSpacing(6)

        self.empty_state_label = QLabel(
            "没有可显示的质控前名单。",
            self.table_panel,
        )
        self.empty_state_label.setObjectName("previewEmptyState")
        self.empty_state_label.setProperty("role", "secondary")
        self.empty_state_label.setWordWrap(True)
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        table_panel_layout.addWidget(self.empty_state_label)

        self.table_surface = QFrame(self.table_panel)
        self.table_surface.setObjectName("tableSurface")
        self.table_surface.setProperty("surface", "true")
        self.table_surface.setMinimumWidth(0)
        self.table_surface.installEventFilter(self)
        table_layout = QGridLayout(self.table_surface)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)
        table_layout.setColumnStretch(1, 1)
        table_layout.setRowStretch(0, 1)

        self.table_model = QtTableModel(
            self.row_window,
            self,
            language=self.language,
        )
        self.table_view = QTableView(self.table_surface)
        self.table_view.setObjectName("previewTable")
        self.table_view.setAccessibleName("EasyQC 质控前名单")
        self.table_view.setAccessibleDescription(
            "只读名单；筛选和排序作用于完整结果。"
        )
        self.pinned_view = QTableView(self.table_surface)
        self.pinned_view.setObjectName("pinnedIdentityTable")
        self.pinned_view.setAccessibleName("固定 ezqcid 列")
        self._configure_table(self.table_view)
        self._configure_table(self.pinned_view)
        self.table_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.pinned_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_view.setModel(self.table_model)
        self.pinned_view.setModel(self.table_model)
        self.pinned_view.setSelectionModel(self.table_view.selectionModel())
        self.pinned_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pinned_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pinned_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.pinned_view.verticalHeader().setVisible(True)
        self.table_view.verticalHeader().setVisible(False)
        self.pinned_view.horizontalHeader().setStretchLastSection(False)
        self.table_view.horizontalHeader().setStretchLastSection(True)
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.pinned_view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.pinned_view.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.pinned_view.setMinimumWidth(0)
        self.table_view.setMinimumWidth(0)
        table_layout.addWidget(self.pinned_view, 0, 0)
        table_layout.addWidget(self.table_view, 0, 1)
        self.vertical_scrollbar = QScrollBar(Qt.Vertical, self.table_surface)
        self.vertical_scrollbar.setObjectName("sharedTableVerticalScrollBar")
        self.vertical_scrollbar.setAccessibleName("表格纵向滚动")
        table_layout.addWidget(self.vertical_scrollbar, 0, 2)
        self.horizontal_scrollbar = QScrollBar(Qt.Horizontal, self.table_surface)
        self.horizontal_scrollbar.setObjectName("sharedTableHorizontalScrollBar")
        self.horizontal_scrollbar.setAccessibleName("表格横向滚动")
        table_layout.addWidget(self.horizontal_scrollbar, 1, 0, 1, 2)
        self.scrollbar_corner = QWidget(self.table_surface)
        self.scrollbar_corner.setFixedSize(
            self.vertical_scrollbar.sizeHint().width(),
            self.horizontal_scrollbar.sizeHint().height(),
        )
        table_layout.addWidget(self.scrollbar_corner, 1, 2)

        table_panel_layout.addWidget(self.table_surface, 1)

        footer = QFrame(self.table_panel)
        footer.setObjectName("tableFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 7, 10, 7)
        self.count_label = QLabel("", footer)
        self.count_label.setObjectName("tableStatus")
        self.count_label.setAccessibleName("表格行数")
        self.range_label = QLabel("", footer)
        self.columns_status_label = QLabel("", footer)
        self.sort_status_label = QLabel("", footer)
        self.export_status_label = QLabel("", footer)
        self.export_status_label.setAccessibleName("表格导出状态")
        self.derive_status_label = QLabel("", footer)
        self.derive_status_label.setAccessibleName("新增列状态")
        self.selection_status_label = QLabel("未选择记录", footer)
        for status_label in (
            self.count_label,
            self.range_label,
            self.columns_status_label,
            self.sort_status_label,
            self.export_status_label,
            self.derive_status_label,
            self.selection_status_label,
        ):
            status_label.setMinimumWidth(0)
            status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            status_label.setProperty("role", "secondary")
        footer_layout.addWidget(self.count_label)
        footer_layout.addWidget(self.range_label)
        footer_layout.addWidget(self.columns_status_label)
        footer_layout.addWidget(self.sort_status_label)
        footer_layout.addWidget(self.export_status_label)
        footer_layout.addWidget(self.derive_status_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.selection_status_label)
        footer_layout.addWidget(QLabel("每页", footer))
        self.page_size_combo = QComboBox(footer)
        for size in (25, 50, 100, 200, 500):
            self.page_size_combo.addItem(str(size), size)
        if self.page_size_combo.findData(self.applied_state.page_size) < 0:
            self.page_size_combo.insertItem(0, str(self.applied_state.page_size), self.applied_state.page_size)
        self.previous_button = QPushButton("上一页", footer)
        self.next_button = QPushButton("下一页", footer)
        footer_layout.addWidget(self.page_size_combo)
        footer_layout.addWidget(self.previous_button)
        footer_layout.addWidget(self.next_button)
        table_panel_layout.addWidget(footer)

        self.error_label = QLabel("", self.table_panel)
        self.error_label.setObjectName("tableError")
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("表格操作错误")
        table_panel_layout.addWidget(self.error_label)

        self._build_view_inspector()
        self.workspace_splitter.addWidget(self.table_panel)
        self.workspace_splitter.addWidget(self.view_inspector)
        self.workspace_splitter.setCollapsible(0, False)
        self.workspace_splitter.setCollapsible(1, True)
        self.workspace_splitter.setStretchFactor(0, 4)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.setSizes((800, 320))
        self.view_inspector.hide()
        layout.addWidget(self.workspace_splitter, 1)

        self.find_edit.returnPressed.connect(lambda: self.find_identity_exact(self.find_edit.text()))
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        self.page_size_combo.currentIndexChanged.connect(self._page_size_changed)
        self.table_view.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.pinned_view.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.pinned_view.horizontalHeader().sectionResized.connect(self._pinned_section_resized)
        self.table_view.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table_view.customContextMenuRequested.connect(
            lambda point: self._open_row_context_menu(self.table_view, point)
        )
        self.pinned_view.customContextMenuRequested.connect(
            lambda point: self._open_row_context_menu(self.pinned_view, point)
        )
        self.table_view.horizontalScrollBar().rangeChanged.connect(
            self._sync_horizontal_scroll_range
        )
        self.table_view.horizontalScrollBar().valueChanged.connect(
            self._sync_horizontal_scroll_value_from_view
        )
        self.horizontal_scrollbar.valueChanged.connect(
            self._set_main_horizontal_scroll
        )
        self.table_view.verticalScrollBar().rangeChanged.connect(
            self._sync_vertical_scroll_range
        )
        self.table_view.verticalScrollBar().valueChanged.connect(
            self._sync_vertical_scroll_value_from_view
        )
        self.pinned_view.verticalScrollBar().valueChanged.connect(
            self._sync_vertical_scroll_value_from_view
        )
        self.vertical_scrollbar.valueChanged.connect(
            self._set_shared_vertical_scroll
        )

    def _build_view_inspector(self) -> None:
        """Compose one draft-only Filter/Sort/Columns inspector."""

        self.view_inspector = QFrame(self.workspace_splitter)
        self.view_inspector.setObjectName("viewInspector")
        self.view_inspector.setProperty("surface", "true")
        self.view_inspector.setAccessibleName("表格视图设置")
        self.view_inspector.setMinimumWidth(0)
        self.view_inspector.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        inspector_layout = QVBoxLayout(self.view_inspector)
        inspector_layout.setContentsMargins(8, 8, 8, 8)
        inspector_layout.setSpacing(8)

        header = QHBoxLayout()
        self.inspector_title = QLabel("视图设置", self.view_inspector)
        self.inspector_title.setProperty("role", "sectionTitle")
        header.addWidget(self.inspector_title)
        header.addStretch(1)
        self.inspector_close_button = QPushButton("关闭", self.view_inspector)
        self.inspector_close_button.setObjectName("inspectorCloseButton")
        self.inspector_close_button.setAccessibleName("关闭表格视图设置")
        header.addWidget(self.inspector_close_button)
        inspector_layout.addLayout(header)

        self.inspector_scroll = QScrollArea(self.view_inspector)
        self.inspector_scroll.setObjectName("tableInspectorScroll")
        self.inspector_scroll.setAccessibleName("可滚动表格视图草稿")
        self.inspector_scroll.setWidgetResizable(True)
        self.inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.inspector_tabs = QTabWidget(self.inspector_scroll)
        self.inspector_tabs.setObjectName("tableInspector")
        self.inspector_tabs.setAccessibleName("筛选、排序和列设置草稿")
        self.inspector_tabs.setMinimumWidth(0)
        self._rebuild_inspector_panels()
        self.inspector_scroll.setWidget(self.inspector_tabs)
        inspector_layout.addWidget(self.inspector_scroll, 1)

        self.inspector_error_label = QLabel("", self.view_inspector)
        self.inspector_error_label.setObjectName("tableInspectorError")
        self.inspector_error_label.setProperty("role", "error")
        self.inspector_error_label.setAccessibleName("表格视图设置错误")
        self.inspector_error_label.setWordWrap(True)
        self.inspector_error_label.setVisible(False)
        inspector_layout.addWidget(self.inspector_error_label)

        action_row = QHBoxLayout()
        self.inspector_reset_button = QPushButton("重置", self.view_inspector)
        self.inspector_cancel_button = QPushButton("取消", self.view_inspector)
        self.inspector_apply_button = QPushButton("应用", self.view_inspector)
        self.inspector_reset_button.setAccessibleName("重置全部视图草稿")
        self.inspector_cancel_button.setAccessibleName("取消表格视图编辑")
        self.inspector_apply_button.setAccessibleName("应用表格视图草稿")
        set_button_role(self.inspector_apply_button, "primary")
        action_row.addWidget(self.inspector_reset_button)
        action_row.addStretch(1)
        action_row.addWidget(self.inspector_cancel_button)
        action_row.addWidget(self.inspector_apply_button)
        inspector_layout.addLayout(action_row)

        self.inspector_close_button.clicked.connect(self.close_view_inspector)
        self.inspector_cancel_button.clicked.connect(self.close_view_inspector)
        self.inspector_reset_button.clicked.connect(self.reset_view_inspector)
        self.inspector_apply_button.clicked.connect(self.apply_view_inspector)

    def _rebuild_inspector_panels(self) -> None:
        """Recreate draft panels when the connected table schema changes."""

        while self.inspector_tabs.count():
            panel = self.inspector_tabs.widget(0)
            self.inspector_tabs.removeTab(0)
            panel.deleteLater()
        self.inspector_filter_panel = FilterPanel(
            self.service.profiles,
            self.inspector_tabs,
        )
        self.inspector_sort_panel = SortPanel(
            tuple(self.initial_state.columns.order),
            self.inspector_tabs,
        )
        self.inspector_columns_panel = ColumnsPanel(
            self._columns_with_current_widths(self.applied_state.columns),
            self.inspector_tabs,
            language=self.language,
        )
        self.inspector_tabs.addTab(self.inspector_filter_panel, "筛选")
        self.inspector_tabs.addTab(self.inspector_sort_panel, "排序")
        self.inspector_tabs.addTab(self.inspector_columns_panel, "列显示")
        self._inspector_origin_revision = None

    def _load_inspector_draft(self) -> None:
        self.inspector_filter_panel.set_expression(
            self.applied_state.effective_filter
        )
        self.inspector_sort_panel.set_rules(self.applied_state.sort_rules)
        self.inspector_columns_panel.set_state(
            self._columns_with_current_widths(self.applied_state.columns)
        )
        self._set_inspector_error("")
        self._inspector_origin_revision = self.applied_state.revision

    def _open_view_inspector(self, index: int) -> None:
        if self.view_inspector.isHidden():
            self._load_inspector_draft()
        self.inspector_tabs.setCurrentIndex(index)
        self.view_inspector.show()
        available = max(1, self.workspace_splitter.contentsRect().width())
        target = max(1, int(available * 0.35))
        self.workspace_splitter.setSizes((max(1, available - target), target))
        self.inspector_tabs.setFocus(Qt.FocusReason.ShortcutFocusReason)

    def open_filter_inspector(self) -> None:
        self._open_view_inspector(0)

    def open_sort_inspector(self) -> None:
        self._open_view_inspector(1)

    def open_columns_inspector(self) -> None:
        self._open_view_inspector(2)

    def reset_view_inspector(self) -> None:
        self.inspector_filter_panel.set_expression(
            self.initial_state.effective_filter
        )
        self.inspector_sort_panel.set_rules(self.initial_state.sort_rules)
        self.inspector_columns_panel.set_state(self.initial_state.columns)
        self._set_inspector_error("")

    def close_view_inspector(self) -> None:
        self.view_inspector.hide()
        self._inspector_origin_revision = None
        self._set_inspector_error("")

    def apply_view_inspector(self) -> bool:
        """Commit the three typed drafts as one revision-safe view state."""

        if self._inspector_origin_revision != self.applied_state.revision:
            self._set_inspector_error(
                "已应用视图发生变化，请重新打开视图设置后再试。"
            )
            return False
        try:
            expression = self.inspector_filter_panel.expression()
            rules = self.inspector_sort_panel.rules()
            columns = self.inspector_columns_panel.state()
        except (TypeError, ValueError) as exc:
            self._set_inspector_error(str(exc))
            return False
        duplicate_columns = sorted(
            column
            for column, count in Counter(rule.column for rule in rules).items()
            if count > 1
        )
        if duplicate_columns:
            self.inspector_tabs.setCurrentWidget(self.inspector_sort_panel)
            self.inspector_sort_panel.set_error(
                "每一列只能用于一条排序规则："
                + ", ".join(duplicate_columns)
            )
            return False
        candidate = replace(
            self.applied_state.with_filter(expression),
            sort_rules=rules,
            columns=columns,
        )
        if not self._commit_state(
            candidate,
            reset_page=True,
            capture_current_widths=False,
        ):
            group_id, condition_id = self._locate_filter_error(
                expression,
                self.error_text,
            )
            if group_id is not None or condition_id is not None:
                self.inspector_tabs.setCurrentWidget(self.inspector_filter_panel)
                self.inspector_filter_panel.set_error(
                    self.error_text,
                    group_id=group_id,
                    condition_id=condition_id,
                )
            else:
                self._set_inspector_error(self.error_text)
            return False
        self.close_view_inspector()
        return True

    def _set_inspector_error(self, message: str) -> None:
        self.inspector_error_label.setText(str(message))
        self.inspector_error_label.setVisible(bool(message))

    def _add_toolbar_action(
        self,
        toolbar: QToolBar,
        text: str,
        shortcut: QKeySequence,
        callback: Callable[[], Any],
    ) -> QAction:
        action = QAction(text, toolbar)
        action.setToolTip(f"{text} ({shortcut.toString(QKeySequence.PortableText)})")
        action.triggered.connect(callback)
        toolbar.addAction(action)
        button = toolbar.widgetForAction(action)
        if isinstance(button, QToolButton):
            button.setAutoRaise(False)
        shortcut_binding = QShortcut(shortcut, self)
        shortcut_binding.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut_binding.activated.connect(action.trigger)
        self._toolbar_shortcuts.append(shortcut_binding)
        return action

    @staticmethod
    def _configure_table(table: QTableView) -> None:
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSortingEnabled(False)
        table.setWordWrap(False)
        table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        table.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)

    @staticmethod
    def _set_scroll_value(scrollbar: QScrollBar, value: int) -> None:
        scrollbar.setValue(int(value))

    def _sync_horizontal_scroll_range(self, minimum: int, maximum: int) -> None:
        source = self.table_view.horizontalScrollBar()
        previous = self.horizontal_scrollbar.blockSignals(True)
        try:
            self.horizontal_scrollbar.setRange(int(minimum), int(maximum))
            self.horizontal_scrollbar.setSingleStep(source.singleStep())
            self.horizontal_scrollbar.setPageStep(source.pageStep())
            self.horizontal_scrollbar.setValue(source.value())
        finally:
            self.horizontal_scrollbar.blockSignals(previous)

    def _sync_horizontal_scroll_value_from_view(self, value: int) -> None:
        self._set_scroll_value(self.horizontal_scrollbar, value)

    def _set_main_horizontal_scroll(self, value: int) -> None:
        self._set_scroll_value(self.table_view.horizontalScrollBar(), value)

    def _sync_vertical_scroll_range(self, minimum: int, maximum: int) -> None:
        source = self.table_view.verticalScrollBar()
        previous = self.vertical_scrollbar.blockSignals(True)
        try:
            self.vertical_scrollbar.setRange(int(minimum), int(maximum))
            self.vertical_scrollbar.setSingleStep(source.singleStep())
            self.vertical_scrollbar.setPageStep(source.pageStep())
            self.vertical_scrollbar.setValue(source.value())
        finally:
            self.vertical_scrollbar.blockSignals(previous)
        self._set_scroll_value(
            self.pinned_view.verticalScrollBar(),
            source.value(),
        )

    def _sync_vertical_scroll_value_from_view(self, value: int) -> None:
        self._set_scroll_value(self.vertical_scrollbar, value)
        self._set_scroll_value(self.table_view.verticalScrollBar(), value)
        self._set_scroll_value(self.pinned_view.verticalScrollBar(), value)

    def _set_shared_vertical_scroll(self, value: int) -> None:
        self._set_scroll_value(self.table_view.verticalScrollBar(), value)
        self._set_scroll_value(self.pinned_view.verticalScrollBar(), value)

    def _refresh_external_scrollbars(self) -> None:
        self._update_pinned_view_width()
        horizontal = self.table_view.horizontalScrollBar()
        vertical = self.table_view.verticalScrollBar()
        self._sync_horizontal_scroll_range(
            horizontal.minimum(),
            horizontal.maximum(),
        )
        self._sync_vertical_scroll_range(
            vertical.minimum(),
            vertical.maximum(),
        )
        self.scrollbar_corner.setFixedSize(
            self.vertical_scrollbar.sizeHint().width(),
            self.horizontal_scrollbar.sizeHint().height(),
        )

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
            QEvent.Type.ScreenChangeInternal,
        ) and hasattr(self, "table_surface"):
            self._schedule_pinned_width_update()
            self._schedule_external_scrollbar_refresh()
        return handled

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if (
            watched is getattr(self, "table_surface", None)
            and event.type() == QEvent.Type.Resize
        ):
            self._schedule_pinned_width_update()
            self._schedule_external_scrollbar_refresh()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "table_surface"):
            self._schedule_pinned_width_update()
            self._schedule_external_scrollbar_refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if hasattr(self, "table_surface"):
            self._schedule_pinned_width_update()
            self._schedule_external_scrollbar_refresh()

    def _schedule_pinned_width_update(self) -> None:
        if self._pinned_width_update_pending:
            return
        self._pinned_width_update_pending = True
        self._pinned_width_timer.start(0)

    def _schedule_external_scrollbar_refresh(self) -> None:
        if not self._scroll_refresh_timer.isActive():
            self._scroll_refresh_timer.start(0)

    def _apply_scheduled_pinned_width_update(self) -> None:
        self._pinned_width_update_pending = False
        self._update_pinned_view_width()

    def _pinned_content_width(self) -> int:
        """Return the current metric-derived width of all pinned sections."""

        columns = tuple(str(column) for column in self.table_model.snapshot().columns)
        pinned = set(self.applied_state.columns.pinned)
        header = self.pinned_view.horizontalHeader()
        sections_width = sum(
            header.sectionSize(section)
            for section, column in enumerate(columns)
            if column in pinned and not header.isSectionHidden(section)
        )
        return (
            sections_width
            + self.pinned_view.verticalHeader().width()
            + 2 * self.pinned_view.frameWidth()
        )

    def _update_pinned_view_width(self) -> None:
        if not self.pinned_view.isVisibleTo(self):
            return
        available_width = max(0, self.table_surface.contentsRect().width())
        cap = int(available_width * self.PINNED_SURFACE_FRACTION)
        target = min(self._pinned_content_width(), cap)
        if self.pinned_view.width() != target:
            self.pinned_view.setFixedWidth(target)
            layout = self.table_surface.layout()
            layout.invalidate()
            layout.activate()
            self._schedule_external_scrollbar_refresh()

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    @property
    def filter_count(self) -> int:
        return len(self.applied_state.conditions)

    @property
    def applied_chip_texts(self) -> tuple[str, ...]:
        return tuple(self._condition_summary(condition) for condition in self.applied_state.conditions)

    @property
    def visible_range(self) -> tuple[int, int]:
        if not self.result.matched_total:
            return (0, 0)
        return (
            self.page_offset + 1,
            min(self.result.matched_total, self.page_offset + len(self.row_window.dataframe)),
        )

    def _close_open_dialogs(self) -> None:
        for attribute in (
            "filter_dialog",
            "sort_dialog",
            "columns_dialog",
            "derived_column_dialog",
        ):
            dialog = getattr(self, attribute)
            if dialog is not None:
                dialog.reject()
                setattr(self, attribute, None)

    def _selected_identity(self) -> str:
        if self.selected_source_position is None:
            return ""
        position = self.service.find_result_position(
            self.result,
            self.selected_source_position,
        )
        if position is None:
            return ""
        try:
            window = self.service.get_window(
                self.result,
                position,
                1,
                columns=("ezqcid",),
            )
        except TableViewError:
            return ""
        value = window.dataframe.iloc[0]["ezqcid"]
        return "" if pd.isna(value) else str(value).strip()

    @staticmethod
    def _compatible_state(
        previous: TableViewState,
        service: TableViewService,
    ) -> TableViewState:
        default = service.default_state(page_size=previous.page_size)
        available = set(default.columns.order)
        required_pinned = tuple(default.columns.pinned)
        pinned = required_pinned + tuple(
            column
            for column in previous.columns.order
            if column in previous.columns.pinned
            and column in available
            and column not in required_pinned
        )
        retained = [
            column
            for column in previous.columns.order
            if column in available and column not in pinned
        ]
        appended = [
            column
            for column in default.columns.order
            if column not in retained and column not in pinned
        ]
        order = pinned + tuple(retained + appended)
        hidden = tuple(
            column
            for column in previous.columns.hidden
            if column in available and column not in pinned
        )
        widths = tuple(
            (column, width)
            for column, width in previous.columns.widths
            if column in available
        )
        expression = previous.effective_filter
        compatible_groups: list[FilterGroup] = []
        for group in expression.groups:
            conditions = tuple(
                condition
                for condition in group.conditions
                if condition.column in available
            )
            if conditions:
                compatible_groups.append(
                    FilterGroup(group.group_id, group.join, conditions)
                )
        return TableViewState(
            columns=ColumnViewState(
                order=order,
                hidden=hidden,
                widths=widths,
                pinned=pinned,
            ),
            filter=FilterExpression(
                expression.group_join,
                tuple(compatible_groups),
            ),
            sort_rules=tuple(
                rule for rule in previous.sort_rules if rule.column in available
            ),
            density=previous.density,
            page_size=previous.page_size,
            revision=previous.revision + 1,
        )

    def replace_service(
        self,
        service: TableViewService,
        *,
        preserve_state: bool,
    ) -> None:
        """Atomically replace a prepared source and retain compatible view state."""

        if not isinstance(service, TableViewService):
            raise TypeError("replace_service requires TableViewService")
        previous_identity = self._selected_identity()
        previous_source_position = self.selected_source_position
        previous = replace(
            self.applied_state,
            columns=self._columns_with_current_widths(self.applied_state.columns),
        )
        self._close_open_dialogs()
        self.close_view_inspector()
        self.task_controller.cancel()
        self._pending_state = None
        self._pending_reset_page = False
        self.service = service
        self.export_service = TableExportService(service)
        self.initial_state = service.default_state(page_size=previous.page_size)
        candidate = (
            self._compatible_state(previous, service)
            if preserve_state
            else self.initial_state
        )
        try:
            next_result = service.apply_state(candidate)
        except TableViewError:
            candidate = self.initial_state
            next_result = service.apply_state(candidate)
        self.applied_state = candidate
        self.draft_state = None
        self.result = next_result
        self._rebuild_inspector_panels()
        self.page_offset = 0
        self.selected_source_position = None
        self.selection_outside_view = False
        self._render_result()
        if previous_identity:
            try:
                position = service.find_identity(self.result, previous_identity)
            except QcIdentityError:
                position = None
            if position is not None:
                self.selected_source_position = int(self.result.source_positions[position])
                self.page_offset = (
                    position // self.applied_state.page_size
                ) * self.applied_state.page_size
                self._render_result()
            elif previous_source_position is not None:
                self.selected_source_position = previous_source_position
                self.selection_outside_view = True
                self._update_status()

    def open_filter_dialog(self) -> FilterDialog:
        """Open one non-blocking modal draft over the current applied filter."""

        if self.filter_dialog is not None and self.filter_dialog.isVisible():
            self.filter_dialog.raise_()
            self.filter_dialog.activateWindow()
            return self.filter_dialog
        dialog = FilterDialog(
            self.service.profiles,
            self.applied_state.effective_filter,
            self,
        )
        dialog.applyRequested.connect(
            lambda expression, current=dialog: self._apply_filter_from_dialog(
                current,
                expression,
            )
        )
        dialog.finished.connect(
            lambda _result, current=dialog: self._filter_dialog_finished(current)
        )
        self.filter_dialog = dialog
        dialog.open()
        return dialog

    def _filter_dialog_finished(self, dialog: FilterDialog) -> None:
        if self.filter_dialog is dialog:
            self.filter_dialog = None

    def open_sort_dialog(self) -> SortDialog:
        """Open one non-blocking modal draft over the applied sort rules."""

        if self.sort_dialog is not None and self.sort_dialog.isVisible():
            self.sort_dialog.raise_()
            self.sort_dialog.activateWindow()
            return self.sort_dialog
        dialog = SortDialog(
            self.applied_state.columns.order,
            self.applied_state.sort_rules,
            self,
        )
        dialog.applyRequested.connect(
            lambda rules, current=dialog: self._apply_sort_from_dialog(
                current,
                rules,
            )
        )
        dialog.finished.connect(
            lambda _result, current=dialog: self._sort_dialog_finished(current)
        )
        self.sort_dialog = dialog
        dialog.open()
        return dialog

    def _sort_dialog_finished(self, dialog: SortDialog) -> None:
        if self.sort_dialog is dialog:
            self.sort_dialog = None

    def _apply_sort_from_dialog(self, dialog: SortDialog, rules: object) -> None:
        if not isinstance(rules, tuple) or not all(
            isinstance(rule, SortRule) for rule in rules
        ):
            dialog.set_error("排序窗口返回了无效草稿")
            return
        if not self.apply_sort_rules(rules):
            dialog.set_error(self.error_text)
            return
        dialog.complete_apply()

    def open_columns_dialog(self) -> ColumnsDialog:
        """Open one non-blocking modal draft over the applied column layout."""

        if self.columns_dialog is not None and self.columns_dialog.isVisible():
            self.columns_dialog.raise_()
            self.columns_dialog.activateWindow()
            return self.columns_dialog
        dialog = ColumnsDialog(
            self._columns_with_current_widths(self.applied_state.columns),
            self.initial_state.columns,
            self,
            language=self.language,
        )
        dialog.applyRequested.connect(
            lambda columns, current=dialog: self._apply_columns_from_dialog(
                current,
                columns,
            )
        )
        dialog.finished.connect(
            lambda _result, current=dialog: self._columns_dialog_finished(current)
        )
        self.columns_dialog = dialog
        dialog.open()
        return dialog

    def _columns_dialog_finished(self, dialog: ColumnsDialog) -> None:
        if self.columns_dialog is dialog:
            self.columns_dialog = None

    def _apply_columns_from_dialog(
        self,
        dialog: ColumnsDialog,
        columns: object,
    ) -> None:
        if not isinstance(columns, ColumnViewState):
            dialog.set_error("列设置窗口返回了无效草稿")
            return
        if not self.apply_column_state(columns):
            dialog.set_error(self.error_text)
            return
        dialog.complete_apply()

    @property
    def derive_busy(self) -> bool:
        return self._derive_busy

    def set_derive_column_enabled(self, enabled: bool) -> None:
        if self.derive_action is not None:
            self.derive_action.setEnabled(bool(enabled) and not self._derive_busy)

    def open_derived_column_dialog(self) -> DerivedColumnDialog | None:
        """Open one modal one-time calculation over a bounded source preview."""

        if self.derive_column_callback is None:
            self._set_error("当前表格不能写入新增列")
            return None
        if (
            self.derived_column_dialog is not None
            and self.derived_column_dialog.isVisible()
        ):
            self.derived_column_dialog.raise_()
            self.derived_column_dialog.activateWindow()
            return self.derived_column_dialog
        try:
            if self.derive_preview_source is not None:
                preview_source = self.derive_preview_source()
                if not isinstance(preview_source, pd.DataFrame):
                    raise TypeError("新增列预览来源必须是表格")
                preview_source = preview_source.head(10).copy(deep=True)
            else:
                preview_state = self.service.default_state(page_size=10)
                preview_result = self.service.apply_state(preview_state)
                preview_source = self.service.get_window(
                    preview_result,
                    0,
                    10,
                    columns=preview_state.columns.order,
                ).dataframe
        except Exception as exc:
            self._set_error(str(exc).strip() or type(exc).__name__)
            return None
        dialog = DerivedColumnDialog(
            preview_source,
            self.derive_column_callback,
            self,
        )
        dialog.busyChanged.connect(self._set_derive_busy)
        dialog.columnCommitted.connect(self._derived_column_committed)
        dialog.finished.connect(
            lambda _result, current=dialog: self._derived_column_dialog_finished(
                current
            )
        )
        self.derived_column_dialog = dialog
        self._set_error("")
        dialog.open()
        return dialog

    @Slot(bool)
    def _set_derive_busy(self, busy: bool) -> None:
        next_busy = bool(busy)
        if self._derive_busy == next_busy:
            return
        self._derive_busy = next_busy
        if self.derive_action is not None:
            self.derive_action.setEnabled(not next_busy)
        self.deriveBusyChanged.emit(next_busy)

    @Slot(str)
    def _derived_column_committed(self, name: str) -> None:
        self.derive_status_label.setText(f"已生成列：{name}")
        self._set_error("")
        if self.on_derived_column_committed is None:
            return
        try:
            self.on_derived_column_committed(name)
        except Exception as exc:
            self._set_error(str(exc).strip() or type(exc).__name__)

    def _derived_column_dialog_finished(
        self,
        dialog: DerivedColumnDialog,
    ) -> None:
        if self.derived_column_dialog is dialog:
            self.derived_column_dialog = None

    def _apply_filter_from_dialog(
        self,
        dialog: FilterDialog,
        expression: object,
    ) -> None:
        if not isinstance(expression, FilterExpression):
            dialog.set_error("筛选窗口返回了无效草稿")
            return
        candidate = self.applied_state.with_filter(expression)
        if not self._commit_state(candidate, reset_page=True):
            group_id, condition_id = self._locate_filter_error(
                expression,
                self.error_text,
            )
            dialog.set_error(
                self.error_text,
                group_id=group_id,
                condition_id=condition_id,
            )
            return
        dialog.complete_apply()

    def _locate_filter_error(
        self,
        expression: FilterExpression,
        message: str,
    ) -> tuple[str | None, str | None]:
        for group in expression.groups:
            if group.group_id in message:
                for condition in group.conditions:
                    if condition.condition_id in message:
                        return group.group_id, condition.condition_id
                return group.group_id, None
        for group in expression.groups:
            for condition in group.conditions:
                probe = FilterExpression(
                    "all",
                    (FilterGroup(group.group_id, "all", (condition,)),),
                )
                try:
                    self.service.validate_state(self.applied_state.with_filter(probe))
                except TableViewError:
                    return group.group_id, condition.condition_id
        return None, None

    def begin_filter_edit(self) -> None:
        if self.draft_state is None:
            self.draft_state = replace(self.applied_state)

    def set_filter_draft(self, conditions: tuple[FilterCondition, ...]) -> None:
        if self.draft_state is None:
            self.begin_filter_edit()
        self.draft_state = self.draft_state.with_conditions(tuple(conditions))

    def cancel_filter_draft(self) -> None:
        self.draft_state = None

    def apply_filter_draft(self) -> bool:
        if self.draft_state is None:
            self.begin_filter_edit()
        candidate = self.draft_state
        if not self._commit_state(candidate, reset_page=True):
            return False
        self.draft_state = None
        return True

    def remove_applied_condition(self, condition_id: str) -> bool:
        expression = self.applied_state.effective_filter
        removed = False
        groups: list[FilterGroup] = []
        for group in expression.groups:
            conditions = tuple(
                condition
                for condition in group.conditions
                if condition.condition_id != condition_id
            )
            removed = removed or len(conditions) != len(group.conditions)
            if conditions:
                groups.append(FilterGroup(group.group_id, group.join, conditions))
        if not removed:
            return False
        return self._commit_state(
            self.applied_state.with_filter(
                FilterExpression(expression.group_join, tuple(groups))
            ),
            reset_page=True,
        )

    def apply_sort_rules(self, rules: tuple[SortRule, ...]) -> bool:
        return self._commit_state(
            replace(self.applied_state, sort_rules=tuple(rules)),
            reset_page=True,
        )

    def cycle_header_sort(self, column: str, *, additive: bool = False) -> bool:
        rules = list(self.applied_state.sort_rules)
        index = next((i for i, rule in enumerate(rules) if rule.column == column), None)
        if not additive:
            if index == 0 and rules[0].ascending:
                candidate = (SortRule(column, False),)
            elif index == 0:
                candidate = ()
            else:
                candidate = (SortRule(column, True),)
        elif index is None:
            candidate = tuple(rules + [SortRule(column, True)])
        elif rules[index].ascending:
            rules[index] = SortRule(column, False)
            candidate = tuple(rules)
        else:
            rules.pop(index)
            candidate = tuple(rules)
        return self.apply_sort_rules(candidate)

    def _header_clicked(self, section: int) -> None:
        columns = tuple(self.table_model.snapshot().columns)
        if 0 <= section < len(columns):
            additive = bool(Qt.KeyboardModifier.ShiftModifier & QApplication.keyboardModifiers())
            self.cycle_header_sort(str(columns[section]), additive=additive)

    def apply_column_state(self, columns: ColumnViewState) -> bool:
        current_widths = dict(
            self._columns_with_current_widths(self.applied_state.columns).widths
        )
        current_widths.update(dict(columns.widths))
        columns = replace(
            columns,
            widths=tuple(
                (column, current_widths[column])
                for column in columns.order
                if column in current_widths
            ),
        )
        return self._commit_state(
            replace(self.applied_state, columns=columns),
            reset_page=False,
            capture_current_widths=False,
        )

    def _choose_export_destination(self) -> None:
        destination, _selected_filter = QFileDialog.getSaveFileName(
            self,
            translate_ui_text("导出当前表格视图"),
            "",
            translate_ui_text("CSV 文件 (*.csv)"),
        )
        if destination:
            self.start_export(destination)

    def start_export(
        self,
        destination: str | Path,
        *,
        chunk_size: int = DEFAULT_EXPORT_CHUNK_ROWS,
    ) -> bool:
        """Submit one captured applied-result export without blocking Qt."""

        if self.export_task_controller.busy:
            self._set_error("表格导出任务仍在运行")
            return False
        try:
            output_path = Path(destination)
        except TypeError:
            self._set_error("导出位置必须是文件路径")
            return False
        token = Event()
        revision = self.result.state.revision
        result = self.result
        columns = self.applied_state.columns.visible_columns
        exporter = self.export_service
        self._export_cancel_event = token
        self._export_revision = revision
        self.last_export_receipt = None
        self.export_status_label.setText(
            f"正在导出 0 / {result.matched_total:,} 行…"
        )
        self._set_error("")
        self.export_task_controller.submit(
            revision,
            lambda: exporter.export_applied_csv(
                result,
                columns,
                output_path,
                token,
                lambda completed, total: self.exportProgress.emit(
                    revision,
                    completed,
                    total,
                ),
                chunk_size=chunk_size,
            ),
        )
        return True

    def cancel_export(self) -> bool:
        """Request cooperative cancellation of the current chunked export."""

        if not self.export_task_controller.busy or self._export_cancel_event is None:
            return False
        self._export_cancel_event.set()
        self.export_status_label.setText("正在取消导出…")
        self.cancel_export_action.setEnabled(False)
        return True

    @Slot(int, int, int)
    def _update_export_progress(
        self,
        revision: int,
        completed: int,
        total: int,
    ) -> None:
        if revision != self._export_revision:
            return
        self.export_status_label.setText(
            f"正在导出 {completed:,} / {total:,} 行…"
        )

    @Slot(int, object)
    def _accept_export_result(self, revision: int, result: object) -> None:
        if revision != self._export_revision:
            return
        if not isinstance(result, ExportReceipt):
            self._handle_export_error(
                revision,
                TableExportError("后台表格导出任务返回了无效回执"),
            )
            return
        self.last_export_receipt = result
        self.export_status_label.setText(
            f"已导出 {result.rows:,} 行 · {result.destination.name}"
        )
        self._set_error("")
        self._clear_export_request()

    @Slot(int, object)
    def _handle_export_error(self, revision: int, error: object) -> None:
        if revision != self._export_revision:
            return
        if isinstance(error, TableExportCancelled):
            self.export_status_label.setText("导出已取消")
            self._set_error("")
        else:
            message = str(error).strip() or type(error).__name__
            self.export_status_label.setText("导出失败")
            self._set_error(message)
        self._clear_export_request()

    @Slot(bool)
    def _set_export_busy(self, busy: bool) -> None:
        self.export_action.setEnabled(not busy)
        self.cancel_export_action.setVisible(busy)
        self.cancel_export_action.setEnabled(busy)

    def _clear_export_request(self) -> None:
        self._export_cancel_event = None
        self._export_revision = None

    def _page_size_changed(self) -> None:
        page_size = self.page_size_combo.currentData()
        if page_size is not None and int(page_size) != self.applied_state.page_size:
            self.set_page_size(int(page_size))

    def set_page_size(self, page_size: int) -> bool:
        return self._commit_state(
            replace(self.applied_state, page_size=int(page_size)),
            reset_page=True,
        )

    def previous_page(self) -> bool:
        if self.page_offset <= 0:
            return False
        self.page_offset = max(0, self.page_offset - self.applied_state.page_size)
        self._render_result()
        return True

    def next_page(self) -> bool:
        offset = self.page_offset + self.applied_state.page_size
        if offset >= self.result.matched_total:
            return False
        self.page_offset = offset
        self._render_result()
        return True

    def find_identity_exact(self, identity: str | None = None) -> bool:
        query = (self.find_edit.text() if identity is None else identity).strip()
        if not query:
            self._set_error("请输入精确的 ezqcid")
            return False
        try:
            result_position = self.service.find_identity(self.result, query)
        except QcIdentityError:
            self._set_error("当前结果中没有 ezqcid 列")
            return False
        if result_position is None:
            self._set_error(f"没有匹配的 ezqcid：{query}")
            return False
        self.selected_source_position = int(self.result.source_positions[result_position])
        self.page_offset = (
            result_position // self.applied_state.page_size
        ) * self.applied_state.page_size
        self._set_error("")
        self._render_result()
        return True

    def select_source_position(self, source_position: int) -> bool:
        source_position = int(source_position)
        result_position = self.service.find_result_position(
            self.result,
            source_position,
        )
        if result_position is None:
            self.selected_source_position = source_position
            self.selection_outside_view = True
            self.table_view.clearSelection()
            self._update_status()
            return False
        self.selected_source_position = source_position
        self.page_offset = (
            result_position // self.applied_state.page_size
        ) * self.applied_state.page_size
        self._render_result()
        return bool(self.table_view.selectionModel().selectedRows())

    def _selection_changed(self, *_args: Any) -> None:
        if self._rendering:
            return
        selected = self.table_view.selectionModel().selectedRows()
        if not selected:
            return
        reference = self.table_model.row_reference(selected[0].row())
        self.selected_source_position = reference.source_position
        self.selection_outside_view = False
        self._set_error("")
        self._update_status()

    def open_selected_qc(self) -> bool:
        selected = self.table_view.selectionModel().selectedRows()
        if not selected:
            self._set_error(
                "所选记录不在当前视图中"
                if self.selected_source_position is not None
                else "请先选择一行"
            )
            return False
        return self.open_qc_reference(self.table_model.row_reference(selected[0].row()))

    def open_qc_reference(self, reference: QtTableRowReference) -> bool:
        if not isinstance(reference, QtTableRowReference):
            raise TypeError("open_qc_reference requires QtTableRowReference")
        position = reference.result_position
        if (
            position < 0
            or position >= self.result.matched_total
            or self.result.source_positions[position] != reference.source_position
            or self.selected_source_position != reference.source_position
        ):
            self._set_error("所选记录引用已失效，请重新选择")
            return False
        local_row = position - self.row_window.offset
        if local_row < 0 or local_row >= self.table_model.rowCount():
            self._set_error("所选记录不在当前视图中")
            return False
        current = self.table_model.row_reference(local_row)
        if current.source_position != reference.source_position:
            self._set_error("所选记录引用已失效，请重新选择")
            return False
        try:
            identity = self.service.validate_qc_identity(self.result, position)
        except QcIdentityError as exc:
            self._set_error(str(exc))
            return False
        if self.on_open_qc is None:
            self._set_error("Qt 预览模式不能打开质控")
            return False
        self._set_error("")
        self.on_open_qc(identity)
        return True

    def set_row_context_actions(
        self,
        provider: Callable[[str], QcRowContext] | None,
        on_module: Callable[[str, QcModuleMenuEntry], None] | None,
        on_record: Callable[[QcRecordMenuEntry], None] | None,
    ) -> None:
        """Install or clear the three callbacks required by row menus."""

        supplied = (provider, on_module, on_record)
        if any(item is not None for item in supplied) and not all(
            callable(item) for item in supplied
        ):
            raise TypeError("QC row context actions must be all callable or all None")
        self.row_context_provider = provider
        self.on_open_qc_module = on_module
        self.on_open_qc_record = on_record
        if self.active_row_context_menu is not None:
            self.active_row_context_menu.close()
            self.active_row_context_menu.deleteLater()
            self.active_row_context_menu = None

    def _open_row_context_menu(
        self,
        view: QTableView,
        point: QPoint,
    ) -> None:
        if view not in {self.table_view, self.pinned_view}:
            raise ValueError("QC row menu requires one of this workspace's tables")
        index = view.indexAt(point)
        if not index.isValid():
            return
        view.setCurrentIndex(index)
        view.selectRow(index.row())
        reference = self.table_model.row_reference(index.row())
        try:
            identity = self.service.validate_qc_identity(
                self.result,
                reference.result_position,
            )
            if identity != reference.ezqcid:
                raise TableViewError("所选记录引用已失效，请重新选择")
            if (
                self.row_context_provider is None
                or self.on_open_qc_module is None
                or self.on_open_qc_record is None
            ):
                raise TableViewError("当前表格不能打开质控菜单")
            provider = self.row_context_provider
            module_callback = self.on_open_qc_module
            record_callback = self.on_open_qc_record
            context = provider(identity)
            menu = QcRowContextMenu(
                context,
                on_module=lambda entry: module_callback(identity, entry),
                on_record=record_callback,
                parent=view,
            )
        except (ArithmeticError, RuntimeError, TypeError, ValueError) as exc:
            self._set_error(str(exc).strip() or type(exc).__name__)
            return
        if self.active_row_context_menu is not None:
            self.active_row_context_menu.close()
            self.active_row_context_menu.deleteLater()
        self.active_row_context_menu = menu
        self._set_error("")
        menu.popup(view.viewport().mapToGlobal(point))

    def _commit_state(
        self,
        candidate: TableViewState,
        *,
        reset_page: bool,
        capture_current_widths: bool = True,
    ) -> bool:
        if capture_current_widths:
            candidate = replace(
                candidate,
                columns=self._columns_with_current_widths(candidate.columns),
            )
        next_state = replace(candidate, revision=self.applied_state.revision + 1)
        try:
            validated_state = self.service.validate_state(next_state)
        except TableViewError as exc:
            self._set_error(str(exc))
            return False
        if self.service.source_total >= self.background_row_threshold:
            self._pending_state = validated_state
            self._pending_reset_page = reset_page
            self._set_error("")
            self.task_controller.submit(
                validated_state.revision,
                lambda state=validated_state: self.service.apply_state(state),
            )
            return True
        try:
            next_result = self.service.apply_state(validated_state)
        except TableViewError as exc:
            self._set_error(str(exc))
            return False
        self._apply_result(next_result, reset_page=reset_page)
        return True

    def _apply_result(self, next_result: TableViewResult, *, reset_page: bool) -> None:
        self.applied_state = next_result.state
        self.result = next_result
        if reset_page:
            self.page_offset = 0
        self._set_error("")
        self._render_result()

    @Slot(int, object)
    def _accept_background_result(self, revision: int, result: object) -> None:
        pending = self._pending_state
        if pending is None or revision != pending.revision:
            return
        if not isinstance(result, TableViewResult):
            self._handle_background_error(
                revision,
                TableViewError("后台表格查询返回了无效结果"),
            )
            return
        reset_page = self._pending_reset_page
        self._pending_state = None
        self._pending_reset_page = False
        self._apply_result(result, reset_page=reset_page)

    @Slot(int, object)
    def _handle_background_error(self, revision: int, error: object) -> None:
        pending = self._pending_state
        if pending is None or revision != pending.revision:
            return
        self._pending_state = None
        self._pending_reset_page = False
        message = str(error).strip() or type(error).__name__
        self._set_error(message)

    @Slot(bool)
    def _set_background_busy(self, busy: bool) -> None:
        self.inspector_apply_button.setEnabled(not busy)
        if busy:
            self.count_label.setText("正在应用…")
        else:
            self._update_status()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._pending_state = None
        self._pending_reset_page = False
        self._pinned_width_timer.stop()
        self._scroll_refresh_timer.stop()
        self._pinned_width_update_pending = False
        self._close_open_dialogs()
        self.close_view_inspector()
        self.task_controller.cancel()
        if self._export_cancel_event is not None:
            self._export_cancel_event.set()
        self.export_task_controller.cancel()
        super().closeEvent(event)

    def _columns_with_current_widths(self, columns: ColumnViewState) -> ColumnViewState:
        if not hasattr(self, "table_model"):
            return columns
        widths = dict(columns.widths)
        pinned = set(columns.pinned)
        for section, column in enumerate(self.table_model.snapshot().columns):
            column = str(column)
            view = self.pinned_view if column in pinned else self.table_view
            widths[column] = view.columnWidth(section)
        ordered = tuple(
            (column, widths[column])
            for column in columns.order
            if column in widths
        )
        return replace(columns, widths=ordered)

    def _render_result(self) -> None:
        self._rendering = True
        try:
            self.row_window = self.service.get_window(
                self.result,
                self.page_offset,
                self.applied_state.page_size,
                columns=self.applied_state.columns.visible_columns,
            )
            self.page_offset = self.row_window.offset
            self.table_model.set_window(self.row_window)
            self.table_model.set_sort_rules(self.applied_state.sort_rules)
            self._configure_columns()
            self._restore_selection()
            self._render_chips()
            self._update_status()
        finally:
            self._rendering = False
        self._schedule_external_scrollbar_refresh()

    def _configure_columns(self) -> None:
        visible = tuple(self.table_model.snapshot().columns)
        pinned = set(self.applied_state.columns.pinned)
        for index, column in enumerate(visible):
            width = self.applied_state.columns.width_for(str(column), 132)
            self.table_view.setColumnWidth(index, width)
            self.pinned_view.setColumnWidth(index, width)
            self.pinned_view.setColumnHidden(index, str(column) not in pinned)
            self.table_view.setColumnHidden(index, str(column) in pinned)
        has_pinned = any(str(column) in pinned for column in visible)
        self.pinned_view.setVisible(has_pinned)
        if has_pinned:
            self._update_pinned_view_width()
            self._schedule_pinned_width_update()

        header = self.table_view.horizontalHeader()
        if self.applied_state.sort_rules:
            primary = self.applied_state.sort_rules[0]
            try:
                section = [str(column) for column in visible].index(primary.column)
            except ValueError:
                header.setSortIndicatorShown(False)
            else:
                header.setSortIndicator(
                    section,
                    Qt.AscendingOrder if primary.ascending else Qt.DescendingOrder,
                )
                header.setSortIndicatorShown(True)
        else:
            header.setSortIndicatorShown(False)

    def _pinned_section_resized(self, section: int, _old: int, _size: int) -> None:
        columns = tuple(str(column) for column in self.table_model.snapshot().columns)
        if (
            0 <= section < len(columns)
            and columns[section] in self.applied_state.columns.pinned
        ):
            self._schedule_pinned_width_update()

    def _restore_selection(self) -> None:
        selection_model = self.table_view.selectionModel()
        selection_model.clearSelection()
        if self.selected_source_position is None:
            self.selection_outside_view = False
            return
        try:
            local_row = self.row_window.source_positions.index(self.selected_source_position)
        except ValueError:
            self.selection_outside_view = True
            return
        index = self.table_model.index(local_row, 0)
        selection_model.select(
            index,
            QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
        )
        selection_model.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
        self.selection_outside_view = False

    def _render_chips(self) -> None:
        for action in self.applied_toolbar.actions():
            self.applied_toolbar.removeAction(action)
            action.deleteLater()
        for condition in self.applied_state.conditions:
            summary = self._condition_summary(condition)
            action = QAction(f"{summary}  ×", self.applied_toolbar)
            action.setData(condition.condition_id)
            remove_text = (
                self.language.tr("table.filter.remove", summary=summary)
                if self.language is not None
                else f"移除筛选 {summary}"
            )
            action.setToolTip(remove_text)
            action.triggered.connect(
                lambda _checked=False, condition_id=condition.condition_id: self.remove_applied_condition(condition_id)
            )
            self.applied_toolbar.addAction(action)
            chip = self.applied_toolbar.widgetForAction(action)
            if chip is not None:
                chip.setObjectName("filterChip")
                chip.setAccessibleName(remove_text)
                if isinstance(chip, QToolButton):
                    chip.setAutoRaise(False)
        self.applied_toolbar.setVisible(bool(self.applied_state.conditions))

    def _condition_summary(self, condition: FilterCondition) -> str:
        if condition.value is None:
            value = ""
        elif isinstance(condition.value, (tuple, list)):
            value = ", ".join(str(item) for item in condition.value)
        else:
            value = str(condition.value)
        operator = operator_label(condition.operator)
        if self.language is not None:
            operator = self.language.translate_source(operator)
        return f"{condition.column} {operator}{(' ' + value) if value else ''}"

    def _ui_text(self, source: str) -> str:
        if self.language is not None:
            return self.language.translate_source(source)
        return translate_ui_text(source)

    def retranslate_ui(self) -> None:
        """Refresh dynamic table presentation without touching view state."""

        self.table_model.retranslate_ui()
        self._render_chips()
        self._update_status()

    def _update_status(self) -> None:
        matched = self.result.matched_total
        total = self.result.source_total
        self.count_label.setText(
            self._ui_text(f"{matched:,} / {total:,} 行")
        )
        self.empty_state_label.setVisible(matched == 0)
        start, end = self.visible_range
        self.range_label.setText(
            self._ui_text(f"第 {start:,}–{end:,} 行")
        )
        visible = len(self.applied_state.columns.visible_columns)
        column_total = len(self.applied_state.columns.order)
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
        self.sort_status_label.setText(
            " · ".join(
                f"{priority} {rule.column} {'↑' if rule.ascending else '↓'}"
                for priority, rule in enumerate(self.applied_state.sort_rules, start=1)
            )
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
        page_index = self.page_size_combo.findData(self.applied_state.page_size)
        if page_index >= 0:
            self.page_size_combo.blockSignals(True)
            self.page_size_combo.setCurrentIndex(page_index)
            self.page_size_combo.blockSignals(False)
        self.previous_button.setEnabled(self.page_offset > 0)
        self.next_button.setEnabled(
            self.page_offset + self.applied_state.page_size < self.result.matched_total
        )
        self.open_qc_action.setEnabled(False)

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))


__all__ = ["QtTableWorkspace"]
