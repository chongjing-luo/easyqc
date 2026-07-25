"""Draft-only Qt QC-list import page over Core-owned parsing and persistence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

import pandas as pd
from PySide6.QtCore import QItemSelectionModel, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLayout,
    QLineEdit,
    QMenu,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.configuration_service import ConfigurationService
from core.table_transform import TableTransformEngine
from core.table_view_service import TableViewError, TableViewService
from gui_qt.columns_dialog import ColumnsDialog
from gui_qt.derived_column_dialog import DerivedColumnDialog
from gui_qt.filter_dialog import FilterDialog
from gui_qt.i18n import translate_ui_text
from gui_qt.sort_dialog import SortDialog
from gui_qt.table_model import QtTableModel
from gui_qt.task_runner import RevisionedTaskController
from gui_qt.theme import set_button_role
from models.column_recipe import ColumnRecipe
from models.table_view_state import (
    ColumnViewState,
    FilterExpression,
    RowWindow,
    SortRule,
    TableViewResult,
    TableViewState,
)


@dataclass(frozen=True)
class _PreparedImportPreview:
    """Worker-built draft/view pair; no Qt object crosses the thread boundary."""

    draft: pd.DataFrame | None
    service: TableViewService
    draft_positions: tuple[int, ...]


class QtQcListImportPage(QWidget):
    """Own one non-authoritative import draft and one explicit apply action."""

    deriveBusyChanged = Signal(bool)
    PAGE_SIZE = 100

    def __init__(
        self,
        configuration: ConfigurationService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(configuration, ConfigurationService):
            raise TypeError("QtQcListImportPage requires ConfigurationService")
        self.configuration = configuration
        self._draft = pd.DataFrame()
        self._current = pd.DataFrame(columns=["ezqcid"])
        self._source_mode = "file"
        self._revision = 0
        self._pending: tuple[int, str] | None = None
        self._preview_service: TableViewService | None = None
        self._preview_state: TableViewState | None = None
        self._preview_result: TableViewResult | None = None
        self._preview_draft_positions: tuple[int, ...] = ()
        self._preview_offset = 0
        self._derive_busy = False
        self._derive_enabled = self.configuration.current_project is not None
        self._draft_revision = 0
        self._derived_draft_result: tuple[int, str, pd.DataFrame] | None = None
        self.filter_dialog: FilterDialog | None = None
        self.sort_dialog: SortDialog | None = None
        self.columns_dialog: ColumnsDialog | None = None
        self.derived_column_dialog: DerivedColumnDialog | None = None

        self.task_controller = RevisionedTaskController(self)
        self.task_controller.resultReady.connect(self._handle_result)
        self.task_controller.errorRaised.connect(self._handle_error)
        self.task_controller.busyChanged.connect(self._set_busy)
        self.setObjectName("qtQcListImportPage")
        self.setAccessibleName("质控名单导入")
        self._build_ui()
        self._render_empty_preview()
        self._set_source_mode("file")
        self._update_actions()

    @property
    def draft(self) -> pd.DataFrame:
        return self._draft.copy(deep=True)

    @property
    def current_row_count(self) -> int:
        return len(self._current)

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    @property
    def derive_busy(self) -> bool:
        return self._derive_busy

    @property
    def preview_columns(self) -> ColumnViewState:
        if self._preview_state is None:
            return ColumnViewState(order=())
        return self._preview_state.columns

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        source_mode_row = QHBoxLayout()
        source_mode_row.addWidget(QLabel("导入方式", self))
        self.source_mode_group = QButtonGroup(self)
        self.source_mode_group.setExclusive(True)
        self.folder_mode_button = QPushButton("文件夹", self)
        self.file_mode_button = QPushButton("文件", self)
        self.text_mode_button = QPushButton("直接输入", self)
        for mode, button in (
            ("folder", self.folder_mode_button),
            ("file", self.file_mode_button),
            ("text", self.text_mode_button),
        ):
            button.setCheckable(True)
            self.source_mode_group.addButton(button)
            button.clicked.connect(
                lambda _checked=False, selected=mode: self._set_source_mode(selected)
            )
            source_mode_row.addWidget(button)
        source_mode_row.addStretch(1)
        layout.addLayout(source_mode_row)

        self.source_stack = QStackedWidget(self)
        self.source_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        path_page = QWidget(self.source_stack)
        path_layout = QHBoxLayout(path_page)
        path_layout.setContentsMargins(0, 0, 0, 0)
        self.source_path_edit = QLineEdit(path_page)
        self.source_path_edit.setObjectName("qcListImportSourcePath")
        self.source_path_edit.setAccessibleName("导入来源路径")
        self.source_path_edit.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.source_path_edit.textChanged.connect(self.source_path_edit.setToolTip)
        self.browse_button = QPushButton("浏览…", path_page)
        self.browse_button.clicked.connect(self._browse_source)
        path_layout.addWidget(self.source_path_edit, 1)
        path_layout.addWidget(self.browse_button)
        self.source_stack.addWidget(path_page)

        text_page = QWidget(self.source_stack)
        text_layout = QVBoxLayout(text_page)
        text_layout.setContentsMargins(0, 0, 0, 0)
        self.direct_text_edit = QTextEdit(text_page)
        self.direct_text_edit.setObjectName("qcListDirectText")
        self.direct_text_edit.setAccessibleName("直接输入名单")
        self.direct_text_edit.setPlaceholderText("用空格、逗号或换行分隔")
        self.direct_text_edit.setMaximumHeight(110)
        text_layout.addWidget(self.direct_text_edit)
        self.source_stack.addWidget(text_page)
        layout.addWidget(self.source_stack)

        single_column_row = QHBoxLayout()
        single_column_row.addWidget(QLabel("单列字段名", self))
        self.single_column_name = QLineEdit(self)
        self.single_column_name.setObjectName("singleColumnName")
        self.single_column_name.setAccessibleName("单列字段名")
        self.single_column_name.setPlaceholderText("例如 ezqcid 或 scanner_model")
        self.single_column_name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        single_column_row.addWidget(self.single_column_name, 1)
        single_column_hint = QLabel("仅在导入单列数据时使用", self)
        single_column_hint.setObjectName("singleColumnHint")
        single_column_row.addWidget(single_column_hint)
        self.read_preview_button = QPushButton("读取并预览", self)
        self.read_preview_button.setObjectName("primaryAction")
        set_button_role(self.read_preview_button, "primary")
        self.read_preview_button.clicked.connect(self.read_preview)
        single_column_row.addWidget(self.read_preview_button)
        layout.addLayout(single_column_row)

        separator = QFrame(self)
        separator.setFrameShape(QFrame.HLine)
        layout.addWidget(separator)

        preview_toolbar = QHBoxLayout()
        preview_toolbar.addWidget(QLabel("导入预览", self))
        preview_toolbar.addStretch(1)
        self.preview_search = QLineEdit(self)
        self.preview_search.setObjectName("qcListPreviewSearch")
        self.preview_search.setAccessibleName("搜索导入预览")
        self.preview_search.setPlaceholderText("搜索预览")
        self.preview_search.setClearButtonEnabled(True)
        self.preview_search.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.preview_search.setMinimumWidth(0)
        self.preview_search.returnPressed.connect(self.run_preview_search)
        preview_toolbar.addWidget(self.preview_search)
        self.filter_button = QPushButton("筛选", self)
        self.sort_button = QPushButton("排序", self)
        self.columns_button = QPushButton("列显示", self)
        self.derive_button = QPushButton("新增列", self)
        self.derive_button.setAccessibleName("为导入草稿新增列")
        self.derive_button.setToolTip("使用导入草稿中的已有列生成普通新列")
        self.filter_button.clicked.connect(self.open_filter_dialog)
        self.sort_button.clicked.connect(self.open_sort_dialog)
        self.columns_button.clicked.connect(self.open_columns_dialog)
        self.derive_button.clicked.connect(self.open_derived_column_dialog)
        preview_toolbar.addWidget(self.filter_button)
        preview_toolbar.addWidget(self.sort_button)
        preview_toolbar.addWidget(self.columns_button)
        preview_toolbar.addWidget(self.derive_button)
        layout.addLayout(preview_toolbar)

        self.preview_table = QTableView(self)
        self.preview_table.setObjectName("qcListImportPreview")
        self.preview_table.setAccessibleName("只读导入预览")
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.preview_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setWordWrap(False)
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        self.preview_table.customContextMenuRequested.connect(
            self._show_preview_context_menu
        )
        layout.addWidget(self.preview_table, 1)

        preview_footer = QHBoxLayout()
        self.preview_range_label = QLabel("0–0 / 0", self)
        preview_footer.addWidget(self.preview_range_label)
        preview_footer.addStretch(1)
        self.previous_button = QPushButton("上一页", self)
        self.next_button = QPushButton("下一页", self)
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        preview_footer.addWidget(self.previous_button)
        preview_footer.addWidget(self.next_button)
        layout.addLayout(preview_footer)

        apply_panel = QFrame(self)
        apply_panel.setObjectName("qcListImportApplyPanel")
        apply_panel.setProperty("surface", "subtle")
        apply_layout = QVBoxLayout(apply_panel)
        apply_layout.setContentsMargins(8, 8, 8, 8)
        apply_mode_row = QHBoxLayout()
        apply_mode_row.addWidget(QLabel("写入：质控前名单", apply_panel))
        self.merge_columns_radio = QRadioButton("按 ezqcid 合并列", apply_panel)
        self.append_rows_radio = QRadioButton("追加行", apply_panel)
        self.merge_columns_radio.setChecked(True)
        self.merge_columns_radio.toggled.connect(self._update_stats)
        self.append_rows_radio.toggled.connect(self._update_stats)
        apply_mode_row.addWidget(self.merge_columns_radio)
        apply_mode_row.addWidget(self.append_rows_radio)
        self.stats_label = QLabel("尚未读取导入数据", apply_panel)
        self.stats_label.setObjectName("qcListImportStats")
        self.stats_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        apply_mode_row.addWidget(self.stats_label, 1)
        apply_layout.addLayout(apply_mode_row)
        apply_action_row = QHBoxLayout()
        apply_action_row.addStretch(1)
        self.clear_button = QPushButton("清空导入数据", apply_panel)
        self.apply_button = QPushButton("写入质控前名单", apply_panel)
        self.apply_button.setObjectName("primaryAction")
        set_button_role(self.apply_button, "primary")
        self.clear_button.clicked.connect(self.clear_draft)
        self.apply_button.clicked.connect(self.apply_draft)
        apply_action_row.addWidget(self.clear_button)
        apply_action_row.addWidget(self.apply_button)
        apply_layout.addLayout(apply_action_row)
        layout.addWidget(apply_panel)

        hint = QLabel(
            "写入前会校验 ezqcid、重复行和字段冲突；失败不会修改原名单。",
            self,
        )
        hint.setObjectName("qcListImportHint")
        hint.setProperty("role", "secondary")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.status_label = QLabel("", self)
        self.status_label.setObjectName("qcListImportStatus")
        self.status_label.setProperty("role", "secondary")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.error_label = QLabel("", self)
        self.error_label.setObjectName("qcListImportError")
        self.error_label.setProperty("role", "error")
        self.error_label.setAccessibleName("质控名单导入错误")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

    def _set_source_mode(self, mode: str) -> None:
        if mode not in {"folder", "file", "text"}:
            raise ValueError(f"Unsupported list-import source mode: {mode}")
        self._source_mode = mode
        button = {
            "folder": self.folder_mode_button,
            "file": self.file_mode_button,
            "text": self.text_mode_button,
        }[mode]
        button.setChecked(True)
        self.source_stack.setCurrentIndex(1 if mode == "text" else 0)
        self.source_stack.setMaximumHeight(
            max(1, self.source_stack.currentWidget().sizeHint().height())
        )
        if mode == "folder":
            self.source_path_edit.setPlaceholderText("选择包含名单目录的文件夹")
        elif mode == "file":
            self.source_path_edit.setPlaceholderText("选择 CSV、Excel、TXT 或 LIST 文件")

    def _browse_source(self) -> None:
        if self._source_mode == "folder":
            selected = QFileDialog.getExistingDirectory(
                self,
                translate_ui_text("选择导入文件夹"),
            )
        else:
            selected, _selected_filter = QFileDialog.getOpenFileName(
                self,
                translate_ui_text("选择导入文件"),
                "",
                translate_ui_text("名单文件 (*.csv *.xlsx *.xls *.txt *.list)"),
            )
        if selected:
            self.source_path_edit.setText(selected)

    def read_preview(self) -> bool:
        mode = self._source_mode
        column_name = self.single_column_name.text()
        if mode == "folder":
            path = self.source_path_edit.text()
            reader = lambda: self._prepare_import_preview(
                self.configuration.draft_from_folder(path, column_name),
                keep_draft=True,
            )
        elif mode == "file":
            path = self.source_path_edit.text()
            reader = lambda: self._prepare_import_preview(
                self.configuration.draft_from_file(path, column_name or None),
                keep_draft=True,
            )
        else:
            text = self.direct_text_edit.toPlainText()
            reader = lambda: self._prepare_import_preview(
                self.configuration.draft_from_text(text, column_name),
                keep_draft=True,
            )
        return self._submit("read", reader)

    def apply_draft(self) -> bool:
        if self._draft.empty:
            self._set_error("请先读取导入数据")
            return False
        draft = self._draft.copy(deep=True)
        mode = "rows" if self.append_rows_radio.isChecked() else "columns"

        def apply() -> pd.DataFrame:
            self.configuration.merge_subjects(draft, mode=mode, notify=False)
            return self.configuration.subjects()

        return self._submit("apply", apply)

    def _submit(self, operation: str, function: Callable[[], object]) -> bool:
        if operation not in {"read", "search", "apply"}:
            raise ValueError(f"Unsupported list-import operation: {operation}")
        if self.task_controller.busy:
            self._set_error("另一项名单导入任务仍在运行")
            return False
        self._revision += 1
        self._pending = (self._revision, operation)
        self._set_error("")
        self.status_label.setText(
            "正在读取导入预览…"
            if operation == "read"
            else "正在搜索导入预览…"
            if operation == "search"
            else "正在写入质控前名单…"
        )
        self.task_controller.submit(self._revision, function)
        return True

    @Slot(int, object)
    def _handle_result(self, revision: int, result: object) -> None:
        if self._pending is None or self._pending[0] != revision:
            return
        operation = self._pending[1]
        try:
            if operation in {"read", "search"}:
                if not isinstance(result, _PreparedImportPreview):
                    raise TypeError("名单预览任务返回了无效结果")
                if operation == "read":
                    if result.draft is None:
                        raise TypeError("名单读取任务缺少导入草稿")
                    self._install_prepared_draft(result)
                else:
                    self._install_preview_service(
                        result.service,
                        draft_positions=result.draft_positions,
                        reset_state=False,
                    )
                self.status_label.setText(
                    (
                        f"已读取 {len(self._draft):,} 条、{len(self._draft.columns):,} 列；尚未写入"
                        if operation == "read"
                        else f"预览搜索完成，匹配 {result.service.source_total:,} 条"
                    )
                )
            else:
                if not isinstance(result, pd.DataFrame):
                    raise TypeError("名单写入任务返回了无效表格")
                self.refresh_current(result)
                self.configuration.publish_subjects_changed()
                self.status_label.setText(f"已写入质控前名单，共 {len(result):,} 条")
        except Exception as exc:
            self._pending = None
            self.status_label.setText("名单导入任务失败")
            self._set_error(str(exc))
            return
        self._pending = None
        self._set_error("")
        self._update_actions()

    @Slot(int, object)
    def _handle_error(self, revision: int, error: object) -> None:
        if self._pending is None or self._pending[0] != revision:
            return
        self._pending = None
        self.status_label.setText("名单导入任务失败")
        self._set_error(str(error).strip() or type(error).__name__)
        self._update_actions()

    @Slot(bool)
    def _set_busy(self, busy: bool) -> None:
        enabled = not busy
        for widget in (
            self.folder_mode_button,
            self.file_mode_button,
            self.text_mode_button,
            self.source_path_edit,
            self.browse_button,
            self.direct_text_edit,
            self.single_column_name,
            self.read_preview_button,
            self.preview_search,
            self.filter_button,
            self.sort_button,
            self.columns_button,
            self.derive_button,
            self.merge_columns_radio,
            self.append_rows_radio,
            self.clear_button,
        ):
            widget.setEnabled(enabled)
        self._update_actions()

    def refresh_current(self, frame: pd.DataFrame) -> None:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("Current QC list must be a pandas DataFrame")
        self._current = frame.copy(deep=True)
        self._update_stats()
        self._update_actions()

    def _install_draft(self, frame: pd.DataFrame) -> None:
        prepared = self._prepare_import_preview(frame.copy(deep=True), keep_draft=True)
        self._install_prepared_draft(prepared)

    @staticmethod
    def _prepare_import_preview(
        frame: pd.DataFrame,
        *,
        keep_draft: bool,
        draft_positions: tuple[int, ...] | None = None,
    ) -> _PreparedImportPreview:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("Import preview requires a pandas DataFrame")
        detached = frame.copy(deep=True).reset_index(drop=True)
        positions = (
            tuple(range(len(detached)))
            if draft_positions is None
            else tuple(int(position) for position in draft_positions)
        )
        if len(positions) != len(detached):
            raise ValueError(
                "Import preview rows and draft positions must have equal length"
            )
        return _PreparedImportPreview(
            draft=detached if keep_draft else None,
            service=TableViewService(detached),
            draft_positions=positions,
        )

    def _install_prepared_draft(
        self,
        prepared: _PreparedImportPreview,
        *,
        reset_state: bool = True,
    ) -> None:
        if prepared.draft is None:
            raise TypeError("Prepared import draft is missing its source table")
        self._draft = prepared.draft
        self._draft_revision += 1
        self._derived_draft_result = None
        self.preview_search.clear()
        self._install_preview_service(
            prepared.service,
            draft_positions=prepared.draft_positions,
            reset_state=reset_state,
        )
        self._update_stats()
        self._update_actions()

    def clear_draft(self) -> None:
        self._draft = pd.DataFrame()
        self._draft_revision += 1
        self._derived_draft_result = None
        self.preview_search.clear()
        self._render_empty_preview()
        self.status_label.setText("已清空导入草稿；质控前名单未改变")
        self._set_error("")
        self._update_stats()
        self._update_actions()

    def run_preview_search(self) -> bool:
        if self._draft.empty:
            return False
        query = self.preview_search.text().strip().casefold()
        draft = self._draft

        def search() -> _PreparedImportPreview:
            if not query:
                filtered = draft
                positions = tuple(range(len(draft)))
            else:
                text = draft.astype("string")
                mask = text.apply(
                    lambda column: column.str.casefold().str.contains(
                        query,
                        regex=False,
                        na=False,
                    )
                ).any(axis=1)
                positions = tuple(
                    position
                    for position, matched in enumerate(mask.tolist())
                    if bool(matched)
                )
                filtered = draft.iloc[list(positions)].reset_index(drop=True)
            return self._prepare_import_preview(
                filtered,
                keep_draft=False,
                draft_positions=positions,
            )

        return self._submit("search", search)

    def _install_preview_service(
        self,
        service: TableViewService,
        *,
        draft_positions: tuple[int, ...],
        reset_state: bool,
    ) -> None:
        previous = self._preview_state
        default = service.default_state(page_size=self.PAGE_SIZE)
        state = default
        if (
            not reset_state
            and previous is not None
            and previous.columns.order == default.columns.order
        ):
            state = replace(previous, page_size=self.PAGE_SIZE)
        try:
            result = service.apply_state(state)
        except TableViewError:
            state = default
            result = service.apply_state(state)
        self._preview_service = service
        self._preview_state = state
        self._preview_result = result
        self._preview_draft_positions = tuple(draft_positions)
        self._preview_offset = 0
        self._render_preview()

    def apply_preview_filter(self, expression: FilterExpression) -> bool:
        if self._preview_service is None or self._preview_state is None:
            return False
        if not isinstance(expression, FilterExpression):
            raise TypeError("Import preview filter requires FilterExpression")
        candidate = self._preview_state.with_filter(expression)
        try:
            result = self._preview_service.apply_state(candidate)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._preview_state = candidate
        self._preview_result = result
        self._preview_offset = 0
        self._render_preview()
        self._set_error("")
        return True

    def apply_preview_columns(self, columns: ColumnViewState) -> bool:
        if self._preview_service is None or self._preview_state is None:
            return False
        if not isinstance(columns, ColumnViewState):
            raise TypeError("Import preview columns require ColumnViewState")
        candidate = replace(self._preview_state, columns=columns)
        try:
            result = self._preview_service.apply_state(candidate)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._preview_state = candidate
        self._preview_result = result
        self._preview_offset = 0
        self._render_preview()
        self._set_error("")
        return True

    def apply_preview_sort(self, rules: tuple[SortRule, ...]) -> bool:
        if self._preview_service is None or self._preview_state is None:
            return False
        if not isinstance(rules, tuple) or not all(
            isinstance(rule, SortRule) for rule in rules
        ):
            raise TypeError("Import preview sort requires SortRule values")
        candidate = self._preview_state.with_sort_rules(rules)
        try:
            result = self._preview_service.apply_state(candidate)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._preview_state = candidate
        self._preview_result = result
        self._preview_offset = 0
        self._render_preview()
        self._set_error("")
        return True

    def open_filter_dialog(self) -> None:
        if self._preview_service is None or self._preview_state is None:
            return
        if self.filter_dialog is not None:
            self.filter_dialog.reject()
        dialog = FilterDialog(
            self._preview_service.profiles,
            self._preview_state.effective_filter,
            self,
        )
        self.filter_dialog = dialog

        def apply(expression: FilterExpression) -> None:
            if self.apply_preview_filter(expression):
                dialog.complete_apply()
            else:
                dialog.set_error(self.error_text)

        dialog.applyRequested.connect(apply)
        dialog.finished.connect(lambda _result: setattr(self, "filter_dialog", None))
        dialog.open()

    def open_sort_dialog(self) -> None:
        if self._preview_service is None or self._preview_state is None:
            return
        if self.sort_dialog is not None:
            self.sort_dialog.reject()
        dialog = SortDialog(
            tuple(self._preview_state.columns.order),
            self._preview_state.sort_rules,
            self,
        )
        self.sort_dialog = dialog

        def apply(rules: object) -> None:
            if not isinstance(rules, tuple) or not all(
                isinstance(rule, SortRule) for rule in rules
            ):
                dialog.set_error("排序窗口返回了无效草稿")
                return
            if self.apply_preview_sort(rules):
                dialog.complete_apply()
            else:
                dialog.set_error(self.error_text)

        dialog.applyRequested.connect(apply)
        dialog.finished.connect(lambda _result: setattr(self, "sort_dialog", None))
        dialog.open()

    def open_columns_dialog(self) -> None:
        if self._preview_service is None or self._preview_state is None:
            return
        if self.columns_dialog is not None:
            self.columns_dialog.reject()
        default = self._preview_service.default_state(page_size=self.PAGE_SIZE).columns
        dialog = ColumnsDialog(self._preview_state.columns, default, self)
        self.columns_dialog = dialog

        def apply(columns: ColumnViewState) -> None:
            if self.apply_preview_columns(columns):
                dialog.complete_apply()
            else:
                dialog.set_error(self.error_text)

        dialog.applyRequested.connect(apply)
        dialog.finished.connect(lambda _result: setattr(self, "columns_dialog", None))
        dialog.open()

    def set_derive_column_enabled(self, enabled: bool) -> None:
        self._derive_enabled = bool(enabled)
        self._update_actions()

    def open_derived_column_dialog(self) -> DerivedColumnDialog | None:
        if not self._derive_enabled or self.configuration.current_project is None:
            self._set_error("请先打开项目")
            return None
        if (
            self.derived_column_dialog is not None
            and self.derived_column_dialog.isVisible()
        ):
            self.derived_column_dialog.raise_()
            self.derived_column_dialog.activateWindow()
            return self.derived_column_dialog
        try:
            draft_source = self._draft.copy(deep=True)
            draft_revision = self._draft_revision
            preview_source = draft_source.head(20).copy(deep=True)
            dialog = DerivedColumnDialog(
                preview_source,
                lambda recipe: self._derive_import_draft_column(
                    recipe,
                    source=draft_source,
                    draft_revision=draft_revision,
                ),
                self,
            )
        except Exception as exc:
            self._set_error(str(exc).strip() or type(exc).__name__)
            return None
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

    def _derive_import_draft_column(
        self,
        recipe: ColumnRecipe,
        *,
        source: pd.DataFrame,
        draft_revision: int,
    ) -> str:
        """Materialize one safe recipe in a detached import draft only."""

        result = TableTransformEngine().derive_column_from_recipe(
            source,
            recipe,
        )
        self._derived_draft_result = (draft_revision, recipe.name, result)
        return recipe.name

    @Slot(bool)
    def _set_derive_busy(self, busy: bool) -> None:
        self._derive_busy = bool(busy)
        self._update_actions()
        self.deriveBusyChanged.emit(self._derive_busy)

    @Slot(str)
    def _derived_column_committed(self, name: str) -> None:
        prepared = self._derived_draft_result
        self._derived_draft_result = None
        if prepared is None:
            self._set_error("新增列任务没有返回导入草稿")
            return
        revision, prepared_name, result = prepared
        if revision != self._draft_revision or prepared_name != name:
            self._set_error("导入草稿已变化，请重新生成新增列")
            return
        try:
            self._replace_draft(result, reset_state=True)
        except Exception as exc:
            self._set_error(str(exc).strip() or type(exc).__name__)
            return
        self.status_label.setText(f"已生成导入草稿列：{name}；尚未写入")
        self._set_error("")

    def _derived_column_dialog_finished(
        self,
        dialog: DerivedColumnDialog,
    ) -> None:
        if self.derived_column_dialog is dialog:
            self.derived_column_dialog = None
        self._derive_busy = False
        self._update_actions()

    def draft_position_for_preview_row(self, row: int) -> int:
        """Map one visible preview row to its authoritative draft position."""

        reference = self.preview_model.row_reference(int(row))
        service_position = reference.source_position
        if service_position < 0 or service_position >= len(
            self._preview_draft_positions
        ):
            raise IndexError("preview row no longer maps to the import draft")
        draft_position = self._preview_draft_positions[service_position]
        if draft_position < 0 or draft_position >= len(self._draft):
            raise IndexError("preview row points outside the import draft")
        return draft_position

    def _selected_draft_positions(self) -> tuple[int, ...]:
        selection_model = self.preview_table.selectionModel()
        if selection_model is None:
            return ()
        return tuple(
            sorted(
                {
                    self.draft_position_for_preview_row(index.row())
                    for index in selection_model.selectedRows()
                }
            )
        )

    def _replace_draft(
        self,
        frame: pd.DataFrame,
        *,
        reset_state: bool,
    ) -> None:
        prepared = self._prepare_import_preview(frame, keep_draft=True)
        self._install_prepared_draft(prepared, reset_state=reset_state)

    def insert_blank_draft_row(self, *, after_position: int | None) -> bool:
        """Insert one blank row without writing the active project list."""

        if (
            self.task_controller.busy
            or self._derive_busy
            or not self._draft.columns.size
        ):
            return False
        if after_position is None:
            insertion = len(self._draft)
        else:
            position = int(after_position)
            if position < 0 or position >= len(self._draft):
                raise IndexError("blank-row insertion position is outside the draft")
            insertion = position + 1
        blank = pd.DataFrame(
            [{column: pd.NA for column in self._draft.columns}],
            columns=self._draft.columns,
        )
        result = pd.concat(
            (
                self._draft.iloc[:insertion],
                blank,
                self._draft.iloc[insertion:],
            ),
            ignore_index=True,
        )
        self._replace_draft(result, reset_state=True)
        if insertion < self.preview_model.rowCount():
            self.preview_table.selectRow(insertion)
        self.status_label.setText("已增加 1 行导入草稿；尚未写入")
        self._set_error("")
        return True

    def delete_selected_draft_rows(self) -> bool:
        """Delete every selected visible row from the detached import draft."""

        if self.task_controller.busy or self._derive_busy:
            return False
        positions = self._selected_draft_positions()
        if not positions:
            return False
        result = self._draft.drop(index=list(positions)).reset_index(drop=True)
        self._replace_draft(result, reset_state=True)
        self.status_label.setText(
            f"已删除 {len(positions):,} 行导入草稿；尚未写入"
        )
        self._set_error("")
        return True

    def create_preview_context_menu(
        self,
        *,
        after_position: int | None,
    ) -> QMenu:
        """Build the row-maintenance menu for one draft source position."""

        menu = QMenu(self.preview_table)
        add_action = menu.addAction(translate_ui_text("增加空行"))
        delete_action = menu.addAction(translate_ui_text("删除选中行"))
        enabled = (
            not self.task_controller.busy
            and not self._derive_busy
            and bool(self._draft.columns.size)
        )
        add_action.setEnabled(enabled)
        delete_action.setEnabled(enabled and bool(self._selected_draft_positions()))
        add_action.triggered.connect(
            lambda _checked=False, position=after_position: self.insert_blank_draft_row(
                after_position=position
            )
        )
        delete_action.triggered.connect(self.delete_selected_draft_rows)
        return menu

    @Slot(object)
    def _show_preview_context_menu(self, position: object) -> None:
        index = self.preview_table.indexAt(position)
        after_position: int | None = None
        if index.isValid():
            selection_model = self.preview_table.selectionModel()
            if selection_model is not None and not selection_model.isRowSelected(
                index.row(),
                index.parent(),
            ):
                selection_model.select(
                    index,
                    QItemSelectionModel.ClearAndSelect
                    | QItemSelectionModel.Rows,
                )
            after_position = self.draft_position_for_preview_row(index.row())
        menu = self.create_preview_context_menu(after_position=after_position)
        menu.exec(self.preview_table.viewport().mapToGlobal(position))

    def _render_empty_preview(self) -> None:
        empty = RowWindow(
            dataframe=pd.DataFrame(),
            source_positions=(),
            offset=0,
            limit=self.PAGE_SIZE,
            matched_total=0,
        )
        if hasattr(self, "preview_model"):
            self.preview_model.set_window(empty)
        else:
            self.preview_model = QtTableModel(empty, self)
            self.preview_table.setModel(self.preview_model)
        self._preview_service = None
        self._preview_state = None
        self._preview_result = None
        self._preview_draft_positions = ()
        self._preview_offset = 0
        self.preview_range_label.setText("0–0 / 0")
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self._update_preview_buttons()

    def _render_preview(self) -> None:
        if (
            self._preview_service is None
            or self._preview_state is None
            or self._preview_result is None
        ):
            self._render_empty_preview()
            return
        window = self._preview_service.get_window(
            self._preview_result,
            self._preview_offset,
            self.PAGE_SIZE,
            columns=self._preview_state.columns.visible_columns,
        )
        self.preview_model.set_window(window)
        self.preview_model.set_sort_rules(self._preview_state.sort_rules)
        total = self._preview_result.matched_total
        start = window.offset + 1 if total else 0
        end = window.offset + len(window.dataframe)
        self.preview_range_label.setText(f"{start:,}–{end:,} / {total:,}")
        self.previous_button.setEnabled(window.offset > 0)
        self.next_button.setEnabled(end < total)
        self._update_preview_buttons()

    def previous_page(self) -> None:
        self._preview_offset = max(0, self._preview_offset - self.PAGE_SIZE)
        self._render_preview()

    def next_page(self) -> None:
        if self._preview_result is None:
            return
        if self._preview_offset + self.PAGE_SIZE < self._preview_result.matched_total:
            self._preview_offset += self.PAGE_SIZE
            self._render_preview()

    def _update_preview_buttons(self) -> None:
        if self._preview_state is None:
            self.filter_button.setText("筛选")
            self.sort_button.setText("排序")
            self.columns_button.setText("列显示")
            return
        filter_count = len(self._preview_state.conditions)
        sort_count = len(self._preview_state.sort_rules)
        visible = len(self._preview_state.columns.visible_columns)
        total = len(self._preview_state.columns.order)
        self.filter_button.setText("筛选" if not filter_count else f"筛选 ({filter_count})")
        self.sort_button.setText("排序" if not sort_count else f"排序 ({sort_count})")
        self.columns_button.setText(f"列显示 ({visible}/{total})")

    def _update_stats(self) -> None:
        if self._draft.empty:
            self.stats_label.setText("尚未读取导入数据")
            return
        if "ezqcid" not in self._draft.columns:
            self.stats_label.setText("写入前需包含 ezqcid")
            return
        identities = self._draft["ezqcid"].map(
            lambda value: "" if value is None or pd.isna(value) else str(value).strip()
        )
        current_ids = (
            set(self._current["ezqcid"].astype(str).str.strip())
            if "ezqcid" in self._current.columns
            else set()
        )
        incoming_ids = set(identities[identities.ne("")])
        matches = len(incoming_ids & current_ids)
        new = len(incoming_ids - current_ids)
        identity_conflicts = int(identities.eq("").sum()) + int(identities.duplicated().sum())
        if self.merge_columns_radio.isChecked():
            field_conflicts = len(
                (set(self._draft.columns) & set(self._current.columns)) - {"ezqcid"}
            )
        else:
            field_conflicts = int(set(self._draft.columns) != set(self._current.columns))
        self.stats_label.setText(
            f"匹配 {matches:,} · 新增 {new:,} · 冲突 {identity_conflicts + field_conflicts:,}"
        )

    def _update_actions(self) -> None:
        busy = self.task_controller.busy
        has_draft = not self._draft.empty
        self.apply_button.setEnabled(not busy and has_draft)
        self.clear_button.setEnabled(not busy and has_draft)
        self.filter_button.setEnabled(not busy and self._preview_service is not None)
        self.sort_button.setEnabled(not busy and self._preview_service is not None)
        self.columns_button.setEnabled(not busy and self._preview_service is not None)
        self.derive_button.setEnabled(
            not busy
            and not self._derive_busy
            and self._derive_enabled
            and self.configuration.current_project is not None
            and has_draft
        )

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def closeEvent(self, event: QCloseEvent) -> None:
        self._pending = None
        self.task_controller.cancel()
        for dialog in (
            self.filter_dialog,
            self.sort_dialog,
            self.columns_dialog,
            self.derived_column_dialog,
        ):
            if dialog is not None:
                dialog.reject()
        super().closeEvent(event)


__all__ = ["QtQcListImportPage"]
