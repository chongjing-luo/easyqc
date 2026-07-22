"""Professional Qt Table workspace over the GUI-independent Core service."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

import pandas as pd
from PySide6.QtCore import QEvent, QItemSelectionModel, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QTableView,
    QToolBar,
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
from gui_qt.filter_dialog import FilterDialog
from gui_qt.filter_panel import operator_label
from gui_qt.sort_dialog import SortDialog
from gui_qt.table_model import QtTableModel, QtTableRowReference
from gui_qt.task_runner import RevisionedTaskController
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
    PINNED_SURFACE_FRACTION = 0.45

    def __init__(
        self,
        source: pd.DataFrame,
        *,
        on_open_qc: Callable[[str], None] | None = None,
        page_size: int = 200,
        background_row_threshold: int = 10_000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(source, pd.DataFrame):
            raise TypeError("QtTableWorkspace source must be a pandas DataFrame")
        self.setObjectName("qtTableWorkspace")
        self.setAccessibleName("EasyQC Table workspace")
        self._pinned_width_update_pending = False
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
        self.initial_state = self.service.default_state(page_size=page_size)
        self.applied_state = self.initial_state
        self.draft_state: TableViewState | None = None
        self.filter_dialog: FilterDialog | None = None
        self.sort_dialog: SortDialog | None = None
        self.columns_dialog: ColumnsDialog | None = None
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

        title_row = QHBoxLayout()
        table_title = QLabel("Subjects", self)
        table_title.setObjectName("tableTitle")
        self.count_label = QLabel("", self)
        self.count_label.setObjectName("tableStatus")
        self.count_label.setAccessibleName("Table row count")
        self.count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        title_row.addWidget(table_title)
        title_row.addStretch(1)
        title_row.addWidget(self.count_label)
        layout.addLayout(title_row)

        self.action_toolbar = QToolBar("Table actions", self)
        self.action_toolbar.setObjectName("tableToolbar")
        self.action_toolbar.setAccessibleName("Table actions")
        self.action_toolbar.setMovable(False)
        self.action_toolbar.setFloatable(False)
        self.action_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._toolbar_shortcuts: list[QShortcut] = []

        self.filter_action = self._add_toolbar_action(
            self.action_toolbar,
            "Filter",
            QKeySequence("Ctrl+Shift+F"),
            self.open_filter_dialog,
        )
        self.sort_action = self._add_toolbar_action(
            self.action_toolbar,
            "Sort",
            QKeySequence("Ctrl+Shift+S"),
            self.open_sort_dialog,
        )
        self.columns_action = self._add_toolbar_action(
            self.action_toolbar,
            "Columns",
            QKeySequence("Ctrl+Shift+C"),
            self.open_columns_dialog,
        )
        self.action_toolbar.addSeparator()
        self.filter_button = self.action_toolbar.widgetForAction(self.filter_action)
        self.sort_button = self.action_toolbar.widgetForAction(self.sort_action)
        self.columns_button = self.action_toolbar.widgetForAction(self.columns_action)
        self.filter_button.setObjectName("filterButton")
        self.sort_button.setObjectName("sortButton")
        self.columns_button.setObjectName("columnsButton")
        self.find_edit = QLineEdit(self.action_toolbar)
        self.find_edit.setObjectName("findIdentity")
        self.find_edit.setAccessibleName("Exact ezqcid to find")
        self.find_edit.setPlaceholderText("Find exact ezqcid")
        self.find_edit.setClearButtonEnabled(True)
        self.action_toolbar.addWidget(self.find_edit)
        self.find_action = self._add_toolbar_action(
            self.action_toolbar,
            "Find",
            QKeySequence("Ctrl+F"),
            lambda: self.find_identity_exact(self.find_edit.text()),
        )
        self.export_action = self._add_toolbar_action(
            self.action_toolbar,
            "Export…",
            QKeySequence("Ctrl+E"),
            self._choose_export_destination,
        )
        self.cancel_export_action = self._add_toolbar_action(
            self.action_toolbar,
            "Cancel export",
            QKeySequence("Ctrl+Shift+E"),
            self.cancel_export,
        )
        self.cancel_export_action.setVisible(False)
        self.open_qc_action = self._add_toolbar_action(
            self.action_toolbar,
            "Open QC…",
            QKeySequence("Ctrl+Return"),
            self.open_selected_qc,
        )
        self.find_button = self.action_toolbar.widgetForAction(self.find_action)
        self.open_qc_button = self.action_toolbar.widgetForAction(self.open_qc_action)
        self.open_qc_button.setObjectName("primaryAction")
        self.open_qc_button.setAccessibleName("Open selected subject QC")
        self.critical_actions = (
            self.filter_action,
            self.sort_action,
            self.columns_action,
            self.find_action,
            self.export_action,
            self.cancel_export_action,
            self.open_qc_action,
        )
        self.critical_shortcuts = tuple(self._toolbar_shortcuts)
        layout.addWidget(self.action_toolbar)

        self.applied_toolbar = QToolBar("Applied view", self)
        self.applied_toolbar.setObjectName("appliedFilterChips")
        self.applied_toolbar.setAccessibleName("Applied filters")
        self.applied_toolbar.setMovable(False)
        self.applied_toolbar.setFloatable(False)
        self.applied_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        layout.addWidget(self.applied_toolbar)

        self.empty_state_label = QLabel(
            "No project table is connected to Qt Preview. Use the default GUI for real QC.",
            self,
        )
        self.empty_state_label.setObjectName("previewEmptyState")
        self.empty_state_label.setWordWrap(True)
        self.empty_state_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty_state_label)

        self.table_surface = QFrame(self)
        self.table_surface.setObjectName("tableSurface")
        self.table_surface.setMinimumWidth(0)
        table_layout = QHBoxLayout(self.table_surface)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)

        self.table_model = QtTableModel(self.row_window, self)
        self.table_view = QTableView(self.table_surface)
        self.table_view.setObjectName("previewTable")
        self.table_view.setAccessibleName("EasyQC subject preview table")
        self.table_view.setAccessibleDescription(
            "Read-only subject rows. Filter and sort operate on the complete result."
        )
        self.pinned_view = QTableView(self.table_surface)
        self.pinned_view.setObjectName("pinnedIdentityTable")
        self.pinned_view.setAccessibleName("Pinned ezqcid column")
        self._configure_table(self.table_view)
        self._configure_table(self.pinned_view)
        self.table_view.setModel(self.table_model)
        self.pinned_view.setModel(self.table_model)
        self.pinned_view.setSelectionModel(self.table_view.selectionModel())
        self.pinned_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.pinned_view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.pinned_view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.pinned_view.verticalHeader().setVisible(True)
        self.table_view.verticalHeader().setVisible(False)
        self.pinned_view.horizontalHeader().setStretchLastSection(False)
        self.table_view.horizontalHeader().setStretchLastSection(False)
        self.table_view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.pinned_view.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.pinned_view.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.pinned_view.setMinimumWidth(0)
        self.table_view.setMinimumWidth(0)
        table_layout.addWidget(self.pinned_view)
        table_layout.addWidget(self.table_view, 1)

        layout.addWidget(self.table_surface, 1)

        footer = QFrame(self)
        footer.setObjectName("tableFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 7, 10, 7)
        self.range_label = QLabel("", footer)
        self.columns_status_label = QLabel("", footer)
        self.sort_status_label = QLabel("", footer)
        self.export_status_label = QLabel("", footer)
        self.export_status_label.setAccessibleName("Table export status")
        self.selection_status_label = QLabel("No row selected", footer)
        for status_label in (
            self.range_label,
            self.columns_status_label,
            self.sort_status_label,
            self.export_status_label,
            self.selection_status_label,
        ):
            status_label.setMinimumWidth(0)
            status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        footer_layout.addWidget(self.range_label)
        footer_layout.addWidget(self.columns_status_label)
        footer_layout.addWidget(self.sort_status_label)
        footer_layout.addWidget(self.export_status_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(self.selection_status_label)
        footer_layout.addWidget(QLabel("Rows", footer))
        self.page_size_combo = QComboBox(footer)
        for size in (25, 50, 100, 200, 500):
            self.page_size_combo.addItem(str(size), size)
        if self.page_size_combo.findData(self.applied_state.page_size) < 0:
            self.page_size_combo.insertItem(0, str(self.applied_state.page_size), self.applied_state.page_size)
        self.previous_button = QPushButton("Previous", footer)
        self.next_button = QPushButton("Next", footer)
        footer_layout.addWidget(self.page_size_combo)
        footer_layout.addWidget(self.previous_button)
        footer_layout.addWidget(self.next_button)
        layout.addWidget(footer)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("tableError")
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("Table action error")
        layout.addWidget(self.error_label)

        self.find_edit.returnPressed.connect(lambda: self.find_identity_exact(self.find_edit.text()))
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        self.page_size_combo.currentIndexChanged.connect(self._page_size_changed)
        self.table_view.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.pinned_view.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.pinned_view.horizontalHeader().sectionResized.connect(self._pinned_section_resized)
        self.table_view.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table_view.doubleClicked.connect(lambda _index: self.open_selected_qc())
        self.pinned_view.doubleClicked.connect(lambda _index: self.open_selected_qc())
        self.table_view.verticalScrollBar().valueChanged.connect(self.pinned_view.verticalScrollBar().setValue)
        self.pinned_view.verticalScrollBar().valueChanged.connect(self.table_view.verticalScrollBar().setValue)

    def _add_toolbar_action(
        self,
        toolbar: QToolBar,
        text: str,
        shortcut: QKeySequence,
        callback: Callable[[], Any],
    ) -> QAction:
        action = QAction(text, toolbar)
        action.setToolTip(f"{text} ({shortcut.toString(QKeySequence.NativeText)})")
        action.triggered.connect(callback)
        toolbar.addAction(action)
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

    def event(self, event: QEvent) -> bool:
        handled = super().event(event)
        if event.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
            QEvent.Type.ScreenChangeInternal,
        ) and hasattr(self, "table_surface"):
            self._schedule_pinned_width_update()
        return handled

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "table_surface"):
            self._schedule_pinned_width_update()

    def _schedule_pinned_width_update(self) -> None:
        if self._pinned_width_update_pending:
            return
        self._pinned_width_update_pending = True
        QTimer.singleShot(0, self._apply_scheduled_pinned_width_update)

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
        self.pinned_view.setFixedWidth(min(self._pinned_content_width(), cap))

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
        for attribute in ("filter_dialog", "sort_dialog", "columns_dialog"):
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
            dialog.set_error("Sort dialog returned an invalid draft")
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
            dialog.set_error("Columns dialog returned an invalid draft")
            return
        if not self.apply_column_state(columns):
            dialog.set_error(self.error_text)
            return
        dialog.complete_apply()

    def _apply_filter_from_dialog(
        self,
        dialog: FilterDialog,
        expression: object,
    ) -> None:
        if not isinstance(expression, FilterExpression):
            dialog.set_error("Filter dialog returned an invalid draft")
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
            "Export applied table",
            "",
            "CSV files (*.csv)",
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
            self._set_error("A table export is already running")
            return False
        try:
            output_path = Path(destination)
        except TypeError:
            self._set_error("Export destination must be a filesystem path")
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
            f"Exporting 0 / {result.matched_total:,} rows…"
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
        self.export_status_label.setText("Cancelling export…")
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
            f"Exporting {completed:,} / {total:,} rows…"
        )

    @Slot(int, object)
    def _accept_export_result(self, revision: int, result: object) -> None:
        if revision != self._export_revision:
            return
        if not isinstance(result, ExportReceipt):
            self._handle_export_error(
                revision,
                TableExportError("Background table export returned an invalid receipt"),
            )
            return
        self.last_export_receipt = result
        self.export_status_label.setText(
            f"Exported {result.rows:,} rows · {result.destination.name}"
        )
        self._set_error("")
        self._clear_export_request()

    @Slot(int, object)
    def _handle_export_error(self, revision: int, error: object) -> None:
        if revision != self._export_revision:
            return
        if isinstance(error, TableExportCancelled):
            self.export_status_label.setText("Export cancelled")
            self._set_error("")
        else:
            message = str(error).strip() or type(error).__name__
            self.export_status_label.setText("Export failed")
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
            self._set_error("Enter an exact ezqcid")
            return False
        try:
            result_position = self.service.find_identity(self.result, query)
        except QcIdentityError:
            self._set_error("The applied result has no ezqcid column")
            return False
        if result_position is None:
            self._set_error(f"No exact ezqcid match: {query}")
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
                "The selected record is outside the current view"
                if self.selected_source_position is not None
                else "Select a row before opening QC"
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
            self._set_error("The selected record is outside the current view")
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
            self._set_error("QC opening is unavailable in Qt Preview")
            return False
        self._set_error("")
        self.on_open_qc(identity)
        return True

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
                TableViewError("Background table query returned an invalid result"),
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
        if busy:
            self.count_label.setText("Applying…")
        else:
            self._update_status()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._pending_state = None
        self._pending_reset_page = False
        self._close_open_dialogs()
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
            action.setToolTip(f"Remove filter {summary}")
            action.triggered.connect(
                lambda _checked=False, condition_id=condition.condition_id: self.remove_applied_condition(condition_id)
            )
            self.applied_toolbar.addAction(action)
            chip = self.applied_toolbar.widgetForAction(action)
            if chip is not None:
                chip.setObjectName("filterChip")
                chip.setAccessibleName(f"Remove filter {summary}")
        self.applied_toolbar.setVisible(bool(self.applied_state.conditions))

    @staticmethod
    def _condition_summary(condition: FilterCondition) -> str:
        if condition.value is None:
            value = ""
        elif isinstance(condition.value, (tuple, list)):
            value = ", ".join(str(item) for item in condition.value)
        else:
            value = str(condition.value)
        return f"{condition.column} {operator_label(condition.operator)}{(' ' + value) if value else ''}"

    def _update_status(self) -> None:
        matched = self.result.matched_total
        total = self.result.source_total
        self.count_label.setText(f"{matched:,} / {total:,} rows")
        self.empty_state_label.setVisible(matched == 0)
        start, end = self.visible_range
        self.range_label.setText(f"Rows {start:,}–{end:,}")
        visible = len(self.applied_state.columns.visible_columns)
        column_total = len(self.applied_state.columns.order)
        self.columns_status_label.setText(f"Columns {visible}/{column_total}")
        self.filter_action.setText(f"Filter ({len(self.applied_state.conditions)})")
        self.sort_action.setText(f"Sort ({len(self.applied_state.sort_rules)})")
        self.columns_action.setText(f"Columns ({visible}/{column_total})")
        self.sort_status_label.setText(
            " · ".join(
                f"{priority} {rule.column} {'↑' if rule.ascending else '↓'}"
                for priority, rule in enumerate(self.applied_state.sort_rules, start=1)
            )
        )
        if self.selection_outside_view:
            self.selection_status_label.setText("Selected record is outside this view")
        elif self.selected_source_position is not None:
            self.selection_status_label.setText(
                f"Selected source row {self.selected_source_position + 1:,}"
            )
        else:
            self.selection_status_label.setText("No row selected")
        page_index = self.page_size_combo.findData(self.applied_state.page_size)
        if page_index >= 0:
            self.page_size_combo.blockSignals(True)
            self.page_size_combo.setCurrentIndex(page_index)
            self.page_size_combo.blockSignals(False)
        self.previous_button.setEnabled(self.page_offset > 0)
        self.next_button.setEnabled(
            self.page_offset + self.applied_state.page_size < self.result.matched_total
        )
        has_current_selection = bool(self.table_view.selectionModel().selectedRows())
        self.open_qc_action.setEnabled(self.on_open_qc is not None and has_current_selection)

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))


__all__ = ["QtTableWorkspace"]
