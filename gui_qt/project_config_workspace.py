"""Typed Qt project, subject, constant and module configuration pages."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QDesktopServices,
    QFontDatabase,
    QKeySequence,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.configuration_service import (
    ConfigurationService,
    ConfigurationSnapshot,
    ProjectListEntry,
)
from core.module_filter import (
    ModuleFilterCompatibilityError,
    normalize_module_filter,
)
from core.table_view_service import TableViewService
from gui_qt.filter_dialog import FilterDialog
from gui_qt.i18n import protect_user_text, translate_ui_text
from gui_qt.qc_list_import_page import QtQcListImportPage
from gui_qt.task_runner import RevisionedTaskController
from gui_qt.theme import set_button_role
from models.qcmodule import Score, Tag
from models.table_view_state import FilterExpression


class QtProjectConfigWorkspace(QWidget):
    """Render typed configuration forms; Core owns every read/write action."""

    MODULE_LABEL_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(
        self,
        configuration: ConfigurationService,
        parent: QWidget | None = None,
        *,
        auto_refresh: bool = True,
        project_loader: Callable[[str], bool] | None = None,
        module_launcher: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(configuration, ConfigurationService):
            raise TypeError("QtProjectConfigWorkspace requires ConfigurationService")
        self.configuration = configuration
        self.project_loader = project_loader
        self.module_launcher = module_launcher
        self._loading = False
        self._selected_module_name: str | None = None
        self._module_form_baseline: tuple | None = None
        self.module_start_buttons: dict[str, QPushButton] = {}
        self._module_filter_subjects = pd.DataFrame(columns=["ezqcid"])
        self._module_filter_profiles = ()
        self._module_filter_expression = FilterExpression()
        self._module_filter_ready_name: str | None = None
        self._module_filter_revision = 0
        self._pending_module_filter: tuple[
            int,
            str,
            str,
            FilterDialog | None,
        ] | None = None
        self._module_filter_dialog: FilterDialog | None = None
        self._io_revision = 0
        self._pending_io: tuple[int, str] | None = None
        self.io_task_controller = RevisionedTaskController(self)
        self.io_task_controller.resultReady.connect(self._handle_io_result)
        self.io_task_controller.errorRaised.connect(self._handle_io_error)
        self.io_task_controller.busyChanged.connect(self._set_io_busy)
        self.module_filter_task_controller = RevisionedTaskController(self)
        self.module_filter_task_controller.resultReady.connect(
            self._handle_module_filter_result
        )
        self.module_filter_task_controller.errorRaised.connect(
            self._handle_module_filter_error
        )
        self.module_filter_task_controller.busyChanged.connect(
            self._set_module_filter_busy
        )
        self.setObjectName("qtProjectConfigWorkspace")
        self.setAccessibleName("EasyQC 项目配置")
        self._build_ui()
        current = self.configuration.current_project
        self.refresh(
            ConfigurationSnapshot(
                projects=self.configuration.projects(),
                current_project_name=current.name if current is not None else "",
                subjects=pd.DataFrame(columns=["ezqcid"]),
                constants={},
                modules=(),
            )
        )
        if auto_refresh:
            self._submit_io("refresh", self.configuration.snapshot)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.project_splitter = QSplitter(Qt.Horizontal, self)
        self.project_splitter.setObjectName("projectSelectionSplitter")

        project_list_panel = QWidget(self.project_splitter)
        project_list_panel.setObjectName("projectListPanel")
        project_list_panel.setProperty("surface", "true")
        project_list_layout = QVBoxLayout(project_list_panel)
        project_list_layout.setContentsMargins(14, 14, 14, 14)
        project_list_layout.setSpacing(10)
        self.project_list_header = QWidget(project_list_panel)
        project_header_layout = QHBoxLayout(self.project_list_header)
        project_header_layout.setContentsMargins(0, 0, 0, 0)
        project_header_layout.setSpacing(6)
        self.project_list_title = QLabel("项目列表", self.project_list_header)
        self.project_list_title.setObjectName("projectListTitle")
        self.project_list_title.setProperty("role", "sectionTitle")
        project_header_layout.addWidget(self.project_list_title)
        project_header_layout.addStretch(1)
        self.project_toolbar = QToolBar("项目列表操作", self.project_list_header)
        self.project_toolbar.setObjectName("configProjectToolbar")
        self.project_toolbar.setProperty("compact", "true")
        self.project_toolbar.setAccessibleName("项目列表操作")
        self.project_toolbar.setMovable(False)
        self.project_toolbar.setFloatable(False)
        self.project_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.project_toolbar.setStyleSheet(
            "QToolButton { padding-left: 2px; padding-right: 2px; }"
        )
        self.new_project_action, self.new_project_button = self._add_toolbar_action(
            self.project_toolbar,
            "新建项目",
            QKeySequence("Ctrl+N"),
            self._prompt_create_project,
        )
        (
            self.import_project_action,
            self.import_project_button,
        ) = self._add_toolbar_action(
            self.project_toolbar,
            "导入项目",
            QKeySequence("Ctrl+I"),
            self._prompt_import_project,
        )
        project_header_layout.addWidget(self.project_toolbar)
        project_list_layout.addWidget(self.project_list_header)

        self.project_list = QListWidget(project_list_panel)
        self.project_list.setObjectName("projectList")
        self.project_list.setAccessibleName("已登记项目")
        protect_user_text(self.project_list, "items")
        self.project_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        project_list_layout.addWidget(self.project_list, 1)

        project_info_panel = QWidget(self.project_splitter)
        project_info_panel.setObjectName("projectInfoPanel")
        project_info_panel.setProperty("surface", "true")
        project_info_layout = QVBoxLayout(project_info_panel)
        project_info_layout.setContentsMargins(16, 14, 16, 14)
        project_info_layout.setSpacing(10)
        project_info_title = QLabel("项目信息", project_info_panel)
        project_info_title.setObjectName("projectInfoTitle")
        project_info_title.setProperty("role", "sectionTitle")
        project_info_layout.addWidget(project_info_title)
        project_form = QFormLayout()
        project_form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.project_name_preview = QLineEdit(project_info_panel)
        self.project_name_preview.setObjectName("projectNamePreview")
        self.project_name_preview.setAccessibleName("项目名称")
        protect_user_text(self.project_name_preview, "toolTip")
        self.project_path_preview = QLineEdit(project_info_panel)
        self.project_path_preview.setObjectName("projectPathPreview")
        self.project_path_preview.setAccessibleName("项目目录")
        protect_user_text(self.project_path_preview, "toolTip")
        self.project_path_preview.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.project_state_preview = QLineEdit(project_info_panel)
        self.project_state_preview.setObjectName("projectStatePreview")
        self.project_state_preview.setAccessibleName("项目状态")
        for field in (
            self.project_name_preview,
            self.project_path_preview,
            self.project_state_preview,
        ):
            field.setReadOnly(True)
        project_form.addRow("名称", self.project_name_preview)
        project_form.addRow("目录", self.project_path_preview)
        project_form.addRow("状态", self.project_state_preview)
        project_info_layout.addLayout(project_form)
        project_info_layout.addStretch(1)

        self.project_detail_toolbar = QToolBar("项目信息操作", project_info_panel)
        self.project_detail_toolbar.setObjectName("projectDetailToolbar")
        self.project_detail_toolbar.setAccessibleName("项目信息操作")
        self.project_detail_toolbar.setMovable(False)
        self.project_detail_toolbar.setFloatable(False)
        self.project_detail_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.project_detail_toolbar.setStyleSheet(
            "QToolButton { padding-left: 2px; padding-right: 2px; }"
        )
        self.reload_projects_action, self.reload_projects_button = self._add_toolbar_action(
            self.project_detail_toolbar,
            "刷新",
            QKeySequence(),
            self._reload_projects,
        )
        (
            self.open_project_directory_action,
            self.open_project_directory_button,
        ) = self._add_toolbar_action(
            self.project_detail_toolbar,
            "打开目录",
            QKeySequence(),
            self._open_selected_project_directory,
        )
        (
            self.remove_project_action,
            self.remove_project_button,
        ) = self._add_toolbar_action(
            self.project_detail_toolbar,
            "取消登记",
            QKeySequence("Ctrl+Shift+Delete"),
            self._confirm_remove_project,
        )
        toolbar_spacer = QWidget(self.project_detail_toolbar)
        toolbar_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.project_detail_toolbar.addWidget(toolbar_spacer)
        self.load_project_action, self.load_project_button = self._add_toolbar_action(
            self.project_detail_toolbar,
            "打开项目",
            QKeySequence("Ctrl+L"),
            self._load_selected_project,
        )
        self.load_project_button.setObjectName("primaryAction")
        set_button_role(self.load_project_button, "primary")
        set_button_role(self.remove_project_button, "danger")
        project_info_layout.addWidget(self.project_detail_toolbar)

        self.project_splitter.addWidget(project_list_panel)
        self.project_splitter.addWidget(project_info_panel)
        self.project_splitter.setCollapsible(0, False)
        self.project_splitter.setCollapsible(1, False)
        self.project_splitter.setStretchFactor(0, 2)
        self.project_splitter.setStretchFactor(1, 3)
        self.project_splitter.setSizes([420, 580])
        layout.addWidget(self.project_splitter, 1)

        # Compatibility-only selector for the existing product shell. It is
        # deliberately not part of the visible project-selection interface.
        self.project_combo = QComboBox(self)
        self.project_combo.setObjectName("internalProjectSelector")
        protect_user_text(self.project_combo, "items")
        self.project_combo.hide()

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("configTabs")
        self.subjects_tab = QtQcListImportPage(self.configuration, self.tabs)
        self.constants_tab = QWidget(self.tabs)
        self.modules_tab = QWidget(self.tabs)
        self.tabs.addTab(self.subjects_tab, "质控名单导入")
        self.tabs.addTab(self.constants_tab, "常量设置")
        self.tabs.addTab(self.modules_tab, "质控模块")
        self._build_constants_tab()
        self._build_modules_tab()
        self.subject_model = self.subjects_tab.preview_model
        self.subject_table = self.subjects_tab.preview_table
        layout.addWidget(self.tabs, 1)

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("configStatus")
        self.status_label.setProperty("role", "secondary")
        self.status_label.setAccessibleName("配置任务状态")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("configError")
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("配置错误")
        layout.addWidget(self.error_label)

        self.project_list.currentItemChanged.connect(self._preview_project_item)
        self.project_combo.currentTextChanged.connect(self.project_combo.setToolTip)

    def _add_toolbar_action(
        self,
        toolbar: QToolBar,
        text: str,
        shortcut: QKeySequence,
        callback: Callable[[], object],
    ) -> tuple[QAction, QWidget]:
        """Add one native action and return its action/tool-button pair."""

        action = QAction(text, toolbar)
        if not shortcut.isEmpty():
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.WindowShortcut)
        action.triggered.connect(callback)
        toolbar.addAction(action)
        button = toolbar.widgetForAction(action)
        button.setAccessibleName(text)
        if isinstance(button, QToolButton):
            button.setAutoRaise(False)
            set_button_role(button, "secondary")
        return action, button

    def _build_constants_tab(self) -> None:
        layout = QVBoxLayout(self.constants_tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        form = QHBoxLayout()
        self.constant_name = QLineEdit(self.constants_tab)
        self.constant_name.setPlaceholderText("常量名")
        self.constant_name.setAccessibleName("常量名")
        self.constant_value = QLineEdit(self.constants_tab)
        self.constant_value.setPlaceholderText("值")
        self.constant_value.setAccessibleName("常量值")
        self.constant_value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.constant_value.textChanged.connect(self.constant_value.setToolTip)
        self.save_constant_button = QPushButton("添加常量", self.constants_tab)
        self.save_constant_button.setObjectName("primaryAction")
        set_button_role(self.save_constant_button, "primary")
        self.cancel_constant_button = QPushButton("取消编辑", self.constants_tab)
        self.cancel_constant_button.hide()
        form.addWidget(QLabel("常量名", self.constants_tab))
        form.addWidget(self.constant_name)
        form.addWidget(QLabel("值", self.constants_tab))
        form.addWidget(self.constant_value, 2)
        form.addWidget(self.cancel_constant_button)
        form.addWidget(self.save_constant_button)
        layout.addLayout(form)

        search_row = QHBoxLayout()
        self.constant_search = QLineEdit(self.constants_tab)
        self.constant_search.setObjectName("constantSearch")
        self.constant_search.setAccessibleName("搜索常量")
        self.constant_search.setPlaceholderText("搜索常量名或值")
        self.constant_search.setClearButtonEnabled(True)
        self.refresh_constants_button = QPushButton("刷新", self.constants_tab)
        search_row.addWidget(self.constant_search, 1)
        search_row.addWidget(self.refresh_constants_button)
        layout.addLayout(search_row)

        self.constants_table = QTableWidget(0, 3, self.constants_tab)
        self.constants_table.setObjectName("constantsTable")
        self.constants_table.setAccessibleName("项目常量")
        self.constants_table.setHorizontalHeaderLabels(["常量名", "值", "操作"])
        constants_header = self.constants_table.horizontalHeader()
        constants_header.setStretchLastSection(False)
        constants_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        constants_header.setSectionResizeMode(1, QHeaderView.Stretch)
        constants_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.constants_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.constants_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.constants_table, 1)
        self.delete_constant_button = QPushButton("删除选中常量", self.constants_tab)
        set_button_role(self.delete_constant_button, "danger")
        layout.addWidget(self.delete_constant_button, 0, Qt.AlignRight)
        self.constant_error_label = QLabel("", self.constants_tab)
        self.constant_error_label.setObjectName("constantError")
        self.constant_error_label.setProperty("role", "error")
        self.constant_error_label.setAccessibleName("常量操作错误")
        self.constant_error_label.setWordWrap(True)
        self.constant_error_label.hide()
        layout.addWidget(self.constant_error_label)
        self.save_constant_button.clicked.connect(self._save_constant)
        self.delete_constant_button.clicked.connect(self._delete_selected_constant)
        self.cancel_constant_button.clicked.connect(self._reset_constant_form)
        self.refresh_constants_button.clicked.connect(lambda: self._refresh_constants())
        self.constant_search.textChanged.connect(self._filter_constants)
        self.constants_table.cellDoubleClicked.connect(self._edit_constant_row)

    def _build_modules_tab(self) -> None:
        layout = QVBoxLayout(self.modules_tab)
        self.module_splitter = QSplitter(Qt.Horizontal, self.modules_tab)
        self.module_splitter.setObjectName("configModuleSplitter")
        left_panel = QWidget(self.module_splitter)
        left_panel.setObjectName("moduleListPanel")
        left_panel.setProperty("surface", "true")
        left = QVBoxLayout(left_panel)
        left.setContentsMargins(14, 14, 14, 14)
        left.setSpacing(10)
        self.module_list_header = QWidget(left_panel)
        module_header_layout = QHBoxLayout(self.module_list_header)
        module_header_layout.setContentsMargins(0, 0, 0, 0)
        module_header_layout.setSpacing(6)
        self.module_list_title = QLabel("模块列表", self.module_list_header)
        self.module_list_title.setObjectName("moduleListTitle")
        self.module_list_title.setProperty("role", "sectionTitle")
        module_header_layout.addWidget(self.module_list_title)
        module_header_layout.addStretch(1)
        self.module_list_toolbar = QToolBar("模块列表操作", self.module_list_header)
        self.module_list_toolbar.setObjectName("configModuleListToolbar")
        self.module_list_toolbar.setProperty("compact", "true")
        self.module_list_toolbar.setAccessibleName("模块列表操作")
        self.module_list_toolbar.setMovable(False)
        self.module_list_toolbar.setFloatable(False)
        self.module_list_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.new_module_action, self.new_module_button = self._add_toolbar_action(
            self.module_list_toolbar,
            "新建模块",
            QKeySequence("Ctrl+Shift+N"),
            self._prepare_new_module,
        )
        self.import_module_action, self.import_module_button = self._add_toolbar_action(
            self.module_list_toolbar,
            "导入模块",
            QKeySequence("Ctrl+Shift+I"),
            self._choose_module_import,
        )
        module_header_layout.addWidget(self.module_list_toolbar)
        left.addWidget(self.module_list_header)
        self.module_list = QListWidget(left_panel)
        self.module_list.setObjectName("moduleList")
        self.module_list.setAccessibleName("质控模块列表")
        protect_user_text(self.module_list, "items")
        self.module_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        left.addWidget(self.module_list, 1)
        move_row = QHBoxLayout()
        self.module_up_button = QPushButton("上移", left_panel)
        self.module_down_button = QPushButton("下移", left_panel)
        move_row.addWidget(self.module_up_button)
        move_row.addWidget(self.module_down_button)
        left.addLayout(move_row)
        self.module_editor_scroll = QScrollArea(self.module_splitter)
        self.module_editor_scroll.setObjectName("configModuleEditorScroll")
        self.module_editor_scroll.setWidgetResizable(True)
        self.module_editor_scroll.setFrameShape(QFrame.NoFrame)
        editor_widget = QWidget(self.module_editor_scroll)
        editor_widget.setObjectName("configModuleEditor")
        editor_widget.setProperty("surface", "true")
        editor = QVBoxLayout(editor_widget)
        editor.setContentsMargins(12, 14, 12, 16)
        editor.setSpacing(10)
        self.module_editor_title = QLabel("模块编辑", editor_widget)
        self.module_editor_title.setObjectName("moduleEditorTitle")
        self.module_editor_title.setProperty("role", "sectionTitle")
        editor.addWidget(self.module_editor_title)
        self.module_basic_title = QLabel("基本信息", editor_widget)
        self.module_basic_title.setProperty("role", "sectionTitle")
        editor.addWidget(self.module_basic_title)
        identity_row = QHBoxLayout()
        self.module_name = QLineEdit(editor_widget)
        self.module_name.setPlaceholderText("模块名称（内部 ID）")
        self.module_name.setAccessibleName("质控模块名称")
        protect_user_text(self.module_name, "toolTip")
        self.module_label = QLineEdit(editor_widget)
        self.module_label.setPlaceholderText("显示标签")
        self.module_label.setAccessibleName("质控模块显示标签")
        protect_user_text(self.module_label, "toolTip")
        self.module_rater = QLineEdit(editor_widget)
        self.module_rater.setPlaceholderText("质控员（留空为只读）")
        self.module_rater.setAccessibleName("质控模块质控员")
        protect_user_text(self.module_rater, "toolTip")
        identity_row.addWidget(self.module_name)
        identity_row.addWidget(self.module_label)
        identity_row.addWidget(self.module_rater)
        editor.addLayout(identity_row)

        self.module_filter_section = QFrame(editor_widget)
        self.module_filter_section.setObjectName("moduleFilterSection")
        self.module_filter_section.setProperty("surface", "subtle")
        filter_layout = QVBoxLayout(self.module_filter_section)
        filter_layout.setContentsMargins(12, 10, 12, 10)
        filter_layout.setSpacing(6)
        self.module_filter_title = QLabel("质控名单", self.module_filter_section)
        self.module_filter_title.setObjectName("moduleFilterTitle")
        self.module_filter_title.setProperty("role", "sectionTitle")
        filter_layout.addWidget(self.module_filter_title)
        filter_row = QHBoxLayout()
        self.module_filter_summary = QLabel("未选择模块", self.module_filter_section)
        self.module_filter_summary.setObjectName("moduleFilterSummary")
        self.module_filter_summary.setAccessibleName("质控名单筛选摘要")
        self.module_filter_summary.setWordWrap(True)
        filter_row.addWidget(self.module_filter_summary, 1)
        self.set_module_filter_button = QPushButton(
            "设置筛选",
            self.module_filter_section,
        )
        self.clear_module_filter_button = QPushButton(
            "清除筛选",
            self.module_filter_section,
        )
        self.set_module_filter_button.setAccessibleName("设置质控名单筛选")
        self.clear_module_filter_button.setAccessibleName("清除质控名单筛选")
        filter_row.addWidget(self.set_module_filter_button)
        filter_row.addWidget(self.clear_module_filter_button)
        filter_layout.addLayout(filter_row)
        self.module_filter_error_label = QLabel("", self.module_filter_section)
        self.module_filter_error_label.setObjectName("moduleFilterError")
        self.module_filter_error_label.setProperty("role", "error")
        self.module_filter_error_label.setAccessibleName("质控名单筛选错误")
        self.module_filter_error_label.setWordWrap(True)
        self.module_filter_error_label.hide()
        filter_layout.addWidget(self.module_filter_error_label)
        editor.addWidget(self.module_filter_section)

        self.score_table = QTableWidget(0, 2, editor_widget)
        self.score_table.setHorizontalHeaderLabels(["评分项", "选项（逗号分隔）"])
        self.score_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.Interactive,
        )
        self.score_table.horizontalHeader().setStretchLastSection(True)
        editor.addWidget(QLabel("评分项", editor_widget))
        editor.addWidget(self.score_table, 1)
        self.tag_table = QTableWidget(0, 1, editor_widget)
        self.tag_table.setHorizontalHeaderLabels(["标签"])
        self.tag_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.Interactive,
        )
        self.tag_table.horizontalHeader().setStretchLastSection(True)
        editor.addWidget(QLabel("标签", editor_widget))
        editor.addWidget(self.tag_table, 1)

        self.module_row_actions_toolbar = QToolBar("模块条目操作", editor_widget)
        self.module_row_actions_toolbar.setObjectName("configModuleRowActions")
        self.module_row_actions_toolbar.setMovable(False)
        self.module_row_actions_toolbar.setFloatable(False)
        self.module_row_actions_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.add_score_action, self.add_score_button = self._add_toolbar_action(
            self.module_row_actions_toolbar,
            "添加评分项",
            QKeySequence(),
            lambda: self._append_table_row(
                self.score_table,
                ("质量", "差,一般,好"),
            ),
        )
        (
            self.remove_score_action,
            self.remove_score_button,
        ) = self._add_toolbar_action(
            self.module_row_actions_toolbar,
            "删除评分项",
            QKeySequence(),
            lambda: self._remove_table_row(self.score_table),
        )
        self.add_tag_action, self.add_tag_button = self._add_toolbar_action(
            self.module_row_actions_toolbar,
            "添加标签",
            QKeySequence(),
            lambda: self._append_table_row(self.tag_table, ("需要复核",)),
        )
        self.remove_tag_action, self.remove_tag_button = self._add_toolbar_action(
            self.module_row_actions_toolbar,
            "删除标签",
            QKeySequence(),
            lambda: self._remove_table_row(self.tag_table),
        )
        editor.addWidget(self.module_row_actions_toolbar)

        self.module_viewer_section = QFrame(editor_widget)
        self.module_viewer_section.setObjectName("moduleViewerSection")
        self.module_viewer_section.setProperty("surface", "subtle")
        viewer_layout = QVBoxLayout(self.module_viewer_section)
        viewer_layout.setContentsMargins(12, 10, 12, 10)
        viewer_layout.setSpacing(6)
        self.module_viewer_title = QLabel("外部图像查看器", self.module_viewer_section)
        self.module_viewer_title.setObjectName("moduleViewerTitle")
        self.module_viewer_title.setProperty("role", "sectionTitle")
        viewer_layout.addWidget(self.module_viewer_title)
        self.module_code = QPlainTextEdit(self.module_viewer_section)
        self.module_code.setObjectName("moduleViewerCommand")
        self.module_code.setPlaceholderText(
            "查看器命令模板，例如：freeview {image} --title {ezqcid}"
        )
        self.module_code.setAccessibleName("外部图像查看器命令模板")
        self.module_code.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self.module_code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.module_code.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.module_code.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.module_code.setMinimumHeight(
            self.module_code.fontMetrics().lineSpacing() * 6
        )
        viewer_layout.addWidget(self.module_code)
        self.module_control = QCheckBox(
            "重新启动前关闭由 EasyQC 管理的查看器",
            self.module_viewer_section,
        )
        viewer_layout.addWidget(self.module_control)
        editor.addWidget(self.module_viewer_section)

        self.module_actions_toolbar = QToolBar("模块编辑操作", editor_widget)
        self.module_actions_toolbar.setObjectName("configModuleActions")
        self.module_actions_toolbar.setAccessibleName("质控模块编辑操作")
        self.module_actions_toolbar.setMovable(False)
        self.module_actions_toolbar.setFloatable(False)
        self.module_actions_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.module_actions_toolbar.setSizePolicy(
            QSizePolicy.Minimum,
            QSizePolicy.Fixed,
        )
        self.delete_module_action, self.delete_module_button = self._add_toolbar_action(
            self.module_actions_toolbar,
            "删除模块",
            QKeySequence("Ctrl+Delete"),
            self._delete_selected_module,
        )
        self.export_module_action, self.export_module_button = self._add_toolbar_action(
            self.module_actions_toolbar,
            "导出模块",
            QKeySequence("Ctrl+E"),
            self._choose_module_export,
        )
        self.module_footer_spacer = QWidget(self.module_actions_toolbar)
        self.module_footer_spacer.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Preferred,
        )
        self.module_actions_toolbar.addWidget(self.module_footer_spacer)
        self.discard_module_action, self.discard_module_button = self._add_toolbar_action(
            self.module_actions_toolbar,
            "放弃更改",
            QKeySequence(),
            self._discard_module_form,
        )
        self.save_module_action, self.save_module_button = self._add_toolbar_action(
            self.module_actions_toolbar,
            "保存模块",
            QKeySequence("Ctrl+S"),
            self._save_module_form,
        )
        self.save_module_button.setObjectName("primaryAction")
        set_button_role(self.save_module_button, "primary")
        set_button_role(self.delete_module_button, "danger")
        editor.addWidget(self.module_actions_toolbar)
        self.module_editor_scroll.setWidget(editor_widget)
        self.module_splitter.addWidget(left_panel)
        self.module_splitter.addWidget(self.module_editor_scroll)
        self.module_splitter.setCollapsible(0, False)
        self.module_splitter.setCollapsible(1, False)
        self.module_splitter.setStretchFactor(0, 2)
        self.module_splitter.setStretchFactor(1, 3)
        self.module_splitter.setSizes([450, 550])
        layout.addWidget(self.module_splitter, 1)
        self.module_launch_status_label = QLabel("", self.modules_tab)
        self.module_launch_status_label.setObjectName("moduleLaunchStatus")
        self.module_launch_status_label.setProperty("role", "secondary")
        self.module_launch_status_label.setAccessibleName("质控启动状态")
        self.module_launch_status_label.setWordWrap(True)
        self.module_launch_status_label.hide()
        layout.addWidget(self.module_launch_status_label)

        self.module_list.currentRowChanged.connect(self._module_row_changed)
        self.module_up_button.clicked.connect(lambda: self._move_selected_module(-1))
        self.module_down_button.clicked.connect(lambda: self._move_selected_module(1))
        self.set_module_filter_button.clicked.connect(
            self._open_module_filter_dialog
        )
        self.clear_module_filter_button.clicked.connect(
            self._clear_module_filter
        )
        self._update_module_filter_action_state()

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def refresh(self, snapshot: ConfigurationSnapshot | None = None) -> None:
        snapshot = self.configuration.snapshot() if snapshot is None else snapshot
        self._loading = True
        try:
            previous_item = self.project_list.currentItem()
            previous_preview = previous_item.text() if previous_item is not None else ""
            previous_entry = (
                previous_item.data(Qt.UserRole) if previous_item is not None else None
            )
            previous_was_current = bool(
                isinstance(previous_entry, ProjectListEntry)
                and previous_entry.is_current
            )
            entries_by_name = {
                entry.name: entry for entry in self.configuration.project_entries()
            }
            missing_entries = set(snapshot.projects) - set(entries_by_name)
            if missing_entries:
                raise ValueError(
                    f"项目预览信息不可用：{sorted(missing_entries)}"
                )
            self.project_list.blockSignals(True)
            self.project_list.clear()
            self.project_combo.clear()
            for project_name in snapshot.projects:
                entry = entries_by_name[project_name]
                self.project_list.addItem(project_name)
                list_item = self.project_list.item(self.project_list.count() - 1)
                list_item.setData(Qt.UserRole, entry)
                list_item.setToolTip(
                    f"{entry.name}\n{entry.path}" if str(entry.path) not in {"", "."} else entry.name
                )
                self.project_combo.addItem(project_name)
                self.project_combo.setItemData(
                    self.project_combo.count() - 1,
                    project_name,
                    Qt.ToolTipRole,
                )
            if snapshot.current_project_name:
                self.project_combo.setCurrentText(snapshot.current_project_name)
            selected_name = snapshot.current_project_name if previous_was_current else previous_preview
            if selected_name not in snapshot.projects:
                selected_name = snapshot.current_project_name
            selected_items = self.project_list.findItems(selected_name, Qt.MatchExactly)
            if selected_items:
                self.project_list.setCurrentItem(selected_items[0])
            elif self.project_list.count():
                self.project_list.setCurrentRow(0)
            self.project_list.blockSignals(False)
            self._preview_project_item(self.project_list.currentItem())
            self._refresh_subjects(snapshot.subjects)
            self._refresh_constants(snapshot.constants)
            self._module_filter_subjects = snapshot.subjects
            self._refresh_modules(self._selected_module_name, snapshot.modules)
        finally:
            self._loading = False

    def _preview_project_item(self, current, _previous=None) -> None:
        """Render one registered project without changing the active project."""

        entry = current.data(Qt.UserRole) if current is not None else None
        if not isinstance(entry, ProjectListEntry):
            self.project_name_preview.clear()
            self.project_path_preview.clear()
            self.project_path_preview.setToolTip("")
            self.project_state_preview.clear()
            self.project_combo.setCurrentIndex(-1)
            self._update_project_action_state()
            return
        path_text = "" if str(entry.path) == "." else str(entry.path)
        self.project_name_preview.setText(entry.name)
        self.project_name_preview.setToolTip(entry.name)
        self.project_path_preview.setText(path_text)
        self.project_path_preview.setToolTip(path_text)
        state_source = (
            "当前打开"
            if entry.is_current
            else "最近打开"
            if entry.is_most_recent
            else "已登记"
        )
        state = translate_ui_text(state_source)
        self.project_state_preview.setText(state)
        self.project_state_preview.setToolTip(state)
        self.project_combo.setCurrentText(entry.name)
        self._update_project_action_state()

    def retranslate_ui(self) -> None:
        """Refresh presentation-only project status without changing selection."""

        self._preview_project_item(self.project_list.currentItem())
        self._retranslate_module_rows()

    def _retranslate_module_rows(self) -> None:
        """Refresh module-row chrome while retaining project-owned labels."""

        for row in range(self.module_list.count()):
            item = self.module_list.item(row)
            row_widget = self.module_list.itemWidget(item)
            if row_widget is None:
                continue
            label = str(row_widget.property("_easyqc_module_label") or "")
            name = str(row_widget.property("_easyqc_module_name") or "")
            rater = str(row_widget.property("_easyqc_module_rater") or "")
            detail_text = f"{name} · {rater or translate_ui_text('只读')}"
            tooltip = f"{label}\n{detail_text}"
            title = row_widget.findChild(QLabel, "moduleRowTitle")
            detail = row_widget.findChild(QLabel, "moduleRowDetail")
            start_button = row_widget.findChild(QPushButton, "moduleRowStart")
            if title is not None:
                title.setText(label)
                title.setToolTip(tooltip)
            if detail is not None:
                detail.setText(detail_text)
                detail.setToolTip(tooltip)
            if start_button is not None:
                start_button.setAccessibleName(
                    f"{translate_ui_text('启动质控')} {label}"
                )

    def _selected_project_entry(self) -> ProjectListEntry | None:
        item = self.project_list.currentItem()
        entry = item.data(Qt.UserRole) if item is not None else None
        return entry if isinstance(entry, ProjectListEntry) else None

    def _update_project_action_state(self) -> None:
        selected = self._selected_project_entry() is not None
        enabled = (
            selected
            and not self.io_task_controller.busy
            and not self.module_filter_write_busy
        )
        self.load_project_action.setEnabled(enabled)
        self.open_project_directory_action.setEnabled(enabled)
        self.remove_project_action.setEnabled(enabled)

    def _reload_projects(self) -> None:
        self._submit_io("refresh", self.configuration.snapshot)

    def _open_selected_project_directory(self) -> None:
        entry = self._selected_project_entry()
        if entry is None:
            return
        path = Path(entry.path)
        if not path.is_dir():
            self._set_error(f"项目目录不存在: {path}")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._set_error(f"无法打开项目目录: {path}")
            return
        self._set_error("")

    def _refresh_subjects(self, frame: pd.DataFrame | None = None) -> None:
        if frame is None:
            frame = (
                self.configuration.subjects()
                if self.configuration.current_project
                else pd.DataFrame(columns=["ezqcid"])
            )
        self.subjects_tab.refresh_current(frame)

    def _refresh_constants(self, constants: dict | None = None) -> None:
        items = list(
            (self.configuration.constants() if constants is None else constants).items()
        )
        self.constants_table.setRowCount(len(items))
        for row, (name, value) in enumerate(items):
            name_item = QTableWidgetItem(str(name))
            name_item.setToolTip(str(name))
            value_item = QTableWidgetItem(str(value))
            value_item.setToolTip(str(value))
            self.constants_table.setItem(row, 0, name_item)
            self.constants_table.setItem(row, 1, value_item)
            actions = QWidget(self.constants_table)
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(0, 0, 0, 0)
            action_layout.setSpacing(4)
            edit_button = QPushButton("编辑", actions)
            delete_button = QPushButton("删除", actions)
            set_button_role(edit_button, "quiet")
            set_button_role(delete_button, "danger")
            edit_button.clicked.connect(
                lambda _checked=False, constant_name=str(name): self._edit_constant_name(
                    constant_name
                )
            )
            delete_button.clicked.connect(
                lambda _checked=False, constant_name=str(name): self._delete_constant(
                    constant_name
                )
            )
            action_layout.addStretch(1)
            action_layout.addWidget(edit_button)
            action_layout.addWidget(delete_button)
            self.constants_table.setCellWidget(row, 2, actions)
        self._filter_constants(self.constant_search.text())

    def _refresh_modules(
        self,
        selected_name: str | None = None,
        modules: tuple | None = None,
    ) -> None:
        modules = self.configuration.modules() if modules is None else modules
        self.module_list.blockSignals(True)
        self.module_list.clear()
        self.module_start_buttons = {}
        for module in modules:
            item_text = module.label
            detail_text = (
                f"{module.name} · {module.rater or translate_ui_text('只读')}"
            )
            tooltip = f"{module.label}\n{detail_text}"
            item = QListWidgetItem("", self.module_list)
            item.setData(Qt.UserRole, module.name)
            item.setData(self.MODULE_LABEL_ROLE, item_text)
            item.setData(
                Qt.AccessibleTextRole,
                f"{module.label}，{detail_text}",
            )
            item.setToolTip(tooltip)
            row_widget = QWidget(self.module_list)
            row_widget.setProperty("_easyqc_module_label", module.label)
            row_widget.setProperty("_easyqc_module_name", module.name)
            row_widget.setProperty("_easyqc_module_rater", module.rater or "")
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(6, 5, 6, 5)
            row_layout.setSpacing(8)
            text_column = QVBoxLayout()
            text_column.setContentsMargins(0, 0, 0, 0)
            text_column.setSpacing(2)
            title = QLabel(module.label, row_widget)
            title.setObjectName("moduleRowTitle")
            protect_user_text(title, "text", "toolTip")
            title.setToolTip(tooltip)
            title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            detail = QLabel(detail_text, row_widget)
            detail.setObjectName("moduleRowDetail")
            protect_user_text(detail, "text", "toolTip")
            detail.setToolTip(tooltip)
            detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            text_column.addWidget(title)
            text_column.addWidget(detail)
            row_layout.addLayout(text_column, 1)
            start_button = QPushButton("启动质控", row_widget)
            start_button.setObjectName("moduleRowStart")
            protect_user_text(start_button, "accessibleName")
            set_button_role(start_button, "primary")
            start_button.setAccessibleName(
                f"{translate_ui_text('启动质控')} {module.label}"
            )
            start_button.setEnabled(self.module_launcher is not None)
            start_button.clicked.connect(
                lambda _checked=False, module_name=module.name: self._start_module_from_row(
                    module_name
                )
            )
            row_layout.addWidget(start_button)
            item.setSizeHint(row_widget.sizeHint())
            self.module_list.setItemWidget(item, row_widget)
            self.module_start_buttons[module.name] = start_button
        target = next(
            (index for index, module in enumerate(modules) if module.name == selected_name),
            0 if modules else -1,
        )
        self.module_list.setCurrentRow(target)
        self.module_list.blockSignals(False)
        if target >= 0:
            self._load_module_form(modules[target])
        else:
            self._selected_module_name = None
            self._module_form_baseline = None
            self._module_filter_ready_name = None
            self.module_filter_summary.setText("未选择模块")
            self._set_module_filter_error("")
            self._update_module_filter_action_state()

    def _module_form_signature(self) -> tuple:
        """Return the editable module fields exactly as rendered."""

        def item_text(table: QTableWidget, row: int, column: int) -> str:
            item = table.item(row, column)
            return item.text().strip() if item is not None else ""

        return (
            self.module_name.text().strip(),
            self.module_label.text().strip(),
            self.module_rater.text().strip(),
            tuple(
                (
                    item_text(self.score_table, row, 0),
                    item_text(self.score_table, row, 1),
                )
                for row in range(self.score_table.rowCount())
            ),
            tuple(
                item_text(self.tag_table, row, 0)
                for row in range(self.tag_table.rowCount())
            ),
            self.module_code.toPlainText().strip(),
            self.module_control.isChecked(),
        )

    def _module_filter_write_allowed(self) -> bool:
        if self._selected_module_name is None or self._module_form_baseline is None:
            self._set_error(
                "请先保存或放弃当前模块更改，再设置或清除质控名单筛选"
            )
            return False
        if self._module_form_signature() != self._module_form_baseline:
            self._set_error(
                "请先保存或放弃当前模块更改，再设置或清除质控名单筛选"
            )
            return False
        return True

    def _set_module_filter_error(self, message: str) -> None:
        self.module_filter_error_label.setText(message)
        self.module_filter_error_label.setVisible(bool(message))

    def show_module_filter_error(self, message: str) -> None:
        """Present module-filter feedback in the module page's visible region."""

        self._set_module_filter_error(str(message))

    def _set_module_filter_summary(
        self,
        expression: FilterExpression,
        matched_total: int,
    ) -> None:
        self.module_filter_summary.setText(
            "全部名单"
            if not expression.groups
            else f"已筛选：{int(matched_total)} 条"
        )

    def _update_module_filter_action_state(self) -> None:
        idle = not self.module_filter_task_controller.busy
        selected = self._selected_module_name is not None
        if not selected:
            self.set_module_filter_button.setEnabled(idle)
            self.clear_module_filter_button.setEnabled(idle)
            return
        self.set_module_filter_button.setEnabled(
            idle
            and self._module_filter_ready_name == self._selected_module_name
        )
        self.clear_module_filter_button.setEnabled(idle)

    @Slot(bool)
    def _set_module_filter_busy(self, busy: bool) -> None:
        enabled = not self.module_filter_write_busy and not self.io_task_controller.busy
        self.project_combo.setEnabled(enabled)
        self.project_list.setEnabled(enabled)
        self.reload_projects_action.setEnabled(enabled)
        self.new_project_action.setEnabled(enabled)
        self.import_project_action.setEnabled(enabled)
        self.tabs.setEnabled(enabled)
        self.subjects_tab.setEnabled(enabled)
        self.constants_tab.setEnabled(enabled)
        self.modules_tab.setEnabled(enabled)
        self._update_project_action_state()
        if busy:
            self.set_module_filter_button.setEnabled(False)
            self.clear_module_filter_button.setEnabled(False)
            return
        self._update_module_filter_action_state()

    @property
    def module_filter_write_busy(self) -> bool:
        pending = self._pending_module_filter
        return bool(
            self.module_filter_task_controller.busy
            and pending is not None
            and pending[1] == "save"
        )

    def _request_module_filter_preview(self, module) -> None:
        module_name = str(module.name)
        if (
            self._pending_module_filter is not None
            and self._pending_module_filter[1] == "save"
        ):
            self._module_filter_ready_name = None
            self.module_filter_summary.setText("正在等待筛选保存…")
            self._set_module_filter_error("")
            self._update_module_filter_action_state()
            return

        if (
            self._module_filter_dialog is not None
            and self._module_filter_dialog.isVisible()
        ):
            self._module_filter_dialog.reject()
        self._module_filter_revision += 1
        revision = self._module_filter_revision
        payload = deepcopy(module.to_legacy_dict())
        subjects = self._module_filter_subjects
        self._pending_module_filter = (
            revision,
            "prepare",
            module_name,
            None,
        )
        self._module_filter_ready_name = None
        self._module_filter_profiles = ()
        self.module_filter_summary.setText("正在计算…")
        self._set_module_filter_error("")

        def prepare_filter():
            table_view = TableViewService(subjects)
            try:
                expression = normalize_module_filter(payload)
            except ModuleFilterCompatibilityError as exc:
                return (
                    module_name,
                    table_view.profiles,
                    FilterExpression(),
                    None,
                    str(exc),
                )
            state = table_view.default_state(
                page_size=max(1, table_view.source_total)
            ).with_filter(expression)
            result = table_view.apply_state(state)
            return (
                module_name,
                table_view.profiles,
                expression,
                result.matched_total,
                "",
            )

        self.module_filter_task_controller.submit(revision, prepare_filter)

    def _reload_selected_module_filter_preview(self) -> None:
        if self._selected_module_name is None:
            self._update_module_filter_action_state()
            return
        module = next(
            (
                item
                for item in self.configuration.modules()
                if item.name == self._selected_module_name
            ),
            None,
        )
        if module is None:
            self._module_filter_ready_name = None
            self.module_filter_summary.setText("未选择模块")
            self._update_module_filter_action_state()
            return
        self._request_module_filter_preview(module)

    @Slot(int, object)
    def _handle_module_filter_result(self, revision: int, result: object) -> None:
        pending = self._pending_module_filter
        if pending is None or pending[0] != revision:
            return
        _revision, operation, expected_name, dialog = pending
        self._pending_module_filter = None

        if operation == "prepare":
            if not isinstance(result, tuple) or len(result) != 5:
                self._handle_module_filter_result_error(
                    expected_name,
                    ValueError("质控名单筛选任务返回了无效结果"),
                )
                return
            module_name, profiles, expression, matched_total, compatibility = result
            if (
                module_name != expected_name
                or self._selected_module_name != expected_name
            ):
                return
            self._module_filter_profiles = tuple(profiles)
            self._module_filter_expression = expression
            self._module_filter_ready_name = module_name
            if compatibility:
                self.module_filter_summary.setText("兼容性错误：旧版筛选不可用")
                self._set_module_filter_error(str(compatibility))
            else:
                self._set_module_filter_summary(expression, int(matched_total))
                self._set_module_filter_error("")
            self._update_module_filter_action_state()
            return

        if operation != "save":
            self._handle_module_filter_result_error(
                expected_name,
                ValueError(f"不支持的质控名单筛选任务: {operation}"),
            )
            return
        if not isinstance(result, tuple) or len(result) != 3:
            self._handle_module_filter_result_error(
                expected_name,
                ValueError("质控名单筛选保存任务返回了无效结果"),
                dialog,
                preserve_ready=True,
            )
            return
        module_name, expression, identities = result
        if module_name != expected_name:
            self._handle_module_filter_result_error(
                expected_name,
                ValueError("质控名单筛选保存结果与所选模块不一致"),
                dialog,
                preserve_ready=True,
            )
            return

        if dialog is not None:
            dialog.complete_apply()
        if self._selected_module_name == module_name:
            self._module_filter_expression = expression
            self._module_filter_ready_name = module_name
            self._set_module_filter_summary(expression, len(identities))
            self._set_module_filter_error("")
            self._set_error("")
        self.configuration.publish_modules_changed()
        if self._selected_module_name != module_name:
            self._reload_selected_module_filter_preview()
        else:
            self._update_module_filter_action_state()

    def _handle_module_filter_result_error(
        self,
        module_name: str,
        error: object,
        dialog: FilterDialog | None = None,
        *,
        preserve_ready: bool = False,
    ) -> None:
        message = str(error).strip() or type(error).__name__
        if dialog is not None:
            dialog.set_error(message)
        if self._selected_module_name == module_name:
            if not preserve_ready:
                self._module_filter_ready_name = None
                self.module_filter_summary.setText("筛选不可用")
            self._set_module_filter_error(message)
            self._set_error(message)
        self._update_module_filter_action_state()

    @Slot(int, object)
    def _handle_module_filter_error(self, revision: int, error: object) -> None:
        pending = self._pending_module_filter
        if pending is None or pending[0] != revision:
            return
        _revision, operation, module_name, dialog = pending
        self._pending_module_filter = None
        self._handle_module_filter_result_error(
            module_name,
            error,
            dialog,
            preserve_ready=operation == "save",
        )
        if operation == "save" and self._selected_module_name != module_name:
            self._reload_selected_module_filter_preview()

    def _filter_dialog_finished(self, dialog: FilterDialog) -> None:
        pending = self._pending_module_filter
        if pending is not None and pending[3] is dialog:
            self._pending_module_filter = (
                pending[0],
                pending[1],
                pending[2],
                None,
            )
        if self._module_filter_dialog is dialog:
            self._module_filter_dialog = None

    def _open_module_filter_dialog(self) -> None:
        if not self._module_filter_write_allowed():
            return
        if (
            self.module_filter_task_controller.busy
            or self._module_filter_ready_name != self._selected_module_name
        ):
            self._set_error("质控名单筛选信息仍在加载")
            return
        dialog = FilterDialog(
            self._module_filter_profiles,
            self._module_filter_expression,
            self,
        )
        module_name = self._selected_module_name
        dialog.applyRequested.connect(
            lambda expression, target=dialog, name=module_name: (
                self._submit_module_filter_save(name, expression, target)
            )
        )
        dialog.finished.connect(
            lambda _result, target=dialog: self._filter_dialog_finished(target)
        )
        self._module_filter_dialog = dialog
        self._set_error("")
        dialog.open()

    def _submit_module_filter_save(
        self,
        module_name: str,
        expression: FilterExpression,
        dialog: FilterDialog | None = None,
    ) -> None:
        if not self._module_filter_write_allowed():
            if dialog is not None:
                dialog.set_error(self.error_text)
            return
        if module_name != self._selected_module_name:
            message = "所选模块已变化，请重新打开质控名单筛选"
            if dialog is not None:
                dialog.set_error(message)
            self._set_error(message)
            return
        if self.module_filter_task_controller.busy:
            message = "另一项质控名单筛选任务仍在运行"
            if dialog is not None:
                dialog.set_error(message)
            self._set_error(message)
            return
        if self.io_task_controller.busy:
            message = "项目配置任务仍在运行，请稍后重试"
            if dialog is not None:
                dialog.set_error(message)
            self._set_error(message)
            return
        try:
            expected_state = self.configuration.capture_settings_state()
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            if dialog is not None:
                dialog.set_error(message)
            self._set_error(message)
            return
        self._module_filter_revision += 1
        revision = self._module_filter_revision
        self._pending_module_filter = (
            revision,
            "save",
            module_name,
            dialog,
        )
        self._set_error("")

        def save_filter():
            identities = self.configuration.save_module_filter(
                module_name,
                expression,
                notify=False,
                expected_state=expected_state,
            )
            return module_name, expression, identities

        self.module_filter_task_controller.submit(revision, save_filter)

    def _clear_module_filter(self) -> None:
        if not self._module_filter_write_allowed():
            return
        self._submit_module_filter_save(
            self._selected_module_name,
            FilterExpression(),
        )

    def _start_module_from_row(self, module_name: str) -> bool:
        selected_label = module_name
        for row in range(self.module_list.count()):
            item = self.module_list.item(row)
            if item.data(Qt.UserRole) == module_name:
                self.module_list.setCurrentItem(item)
                selected_label = (
                    item.data(self.MODULE_LABEL_ROLE) or module_name
                )
                break
        self.set_module_launch_feedback("")
        if self.module_launcher is None:
            self.set_module_launch_feedback("启动失败：当前界面无法启动质控")
            return False
        try:
            accepted = bool(self.module_launcher(module_name))
        except Exception as exc:
            self.set_module_launch_feedback(f"启动失败：{exc}")
            return False
        if not accepted:
            if not self.module_launch_status_label.text():
                self.set_module_launch_feedback(
                    "启动失败：质控启动请求未被接受"
                )
            return False
        self.set_module_launch_feedback(f"已启动质控：{selected_label}")
        self._set_error("")
        return True

    def set_module_launch_feedback(self, message: str) -> None:
        """Show one launch outcome on the page that owns the launch button."""

        text = str(message).strip()
        self.module_launch_status_label.setText(text)
        self.module_launch_status_label.setVisible(bool(text))
        if text.startswith("启动失败"):
            self._set_error(text.removeprefix("启动失败："))

    def _load_selected_project(self) -> None:
        entry = self._selected_project_entry()
        if entry is None:
            return
        name = entry.name
        if self.project_loader is not None:
            if not self.project_loader(name):
                self._set_error("项目打开请求未被接受")
            else:
                self._set_error("")
                self.status_label.setText("正在加载项目…")
            return
        configuration = self.configuration

        def load_project() -> ConfigurationSnapshot:
            configuration.load_project(name, notify=False)
            return configuration.snapshot()

        self._submit_io("load_project", load_project)

    def create_project(self, name: str, path: str) -> bool:
        try:
            self.configuration.create_project(name, path)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._selected_module_name = None
        self._set_error("")
        self.refresh()
        return True

    def import_project(self, path: str) -> bool:
        try:
            self.configuration.import_project(path)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._selected_module_name = None
        self._set_error("")
        self.refresh()
        return True

    def remove_project(self, name: str) -> bool:
        try:
            self.configuration.remove_project(name)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._selected_module_name = None
        self._set_error("")
        self.refresh()
        return True

    def _prompt_create_project(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, accepted = QInputDialog.getText(
            self,
            translate_ui_text("新建 EasyQC 项目"),
            translate_ui_text("项目名称"),
        )
        if not accepted or not name.strip():
            return
        directory = QFileDialog.getExistingDirectory(
            self,
            translate_ui_text("选择项目上级目录"),
        )
        if directory:
            self.create_project(name, directory)

    def _prompt_import_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            translate_ui_text("选择 EasyQC 项目目录"),
        )
        if directory:
            configuration = self.configuration

            def import_project() -> ConfigurationSnapshot:
                configuration.import_project(directory, notify=False)
                return configuration.snapshot()

            self._submit_io("import_project", import_project)

    def _confirm_remove_project(self) -> None:
        entry = self._selected_project_entry()
        if entry is None:
            return
        name = entry.name
        answer = QMessageBox.question(
            self,
            translate_ui_text("取消项目登记"),
            translate_ui_text(f"取消登记 {name}？项目文件不会被删除。"),
        )
        if answer == QMessageBox.Yes:
            self.remove_project(name)

    def _filter_constants(self, query: str) -> None:
        normalized = str(query).strip().casefold()
        for row in range(self.constants_table.rowCount()):
            name_item = self.constants_table.item(row, 0)
            value_item = self.constants_table.item(row, 1)
            haystack = " ".join(
                item.text() for item in (name_item, value_item) if item is not None
            ).casefold()
            self.constants_table.setRowHidden(
                row,
                bool(normalized and normalized not in haystack),
            )

    def _edit_constant_name(self, name: str) -> None:
        for row in range(self.constants_table.rowCount()):
            item = self.constants_table.item(row, 0)
            if item is not None and item.text() == name:
                self._edit_constant_row(row, 0)
                return

    def _edit_constant_row(self, row: int, _column: int) -> None:
        name_item = self.constants_table.item(row, 0)
        value_item = self.constants_table.item(row, 1)
        if name_item is None or value_item is None:
            return
        self.constant_name.setText(name_item.text())
        self.constant_name.setReadOnly(True)
        self.constant_value.setText(value_item.text())
        self.save_constant_button.setText("保存")
        self.cancel_constant_button.show()
        self.constant_value.setFocus()
        self.constant_value.selectAll()

    def _reset_constant_form(self) -> None:
        self.constant_name.setReadOnly(False)
        self.constant_name.clear()
        self.constant_value.clear()
        self.save_constant_button.setText("添加常量")
        self.cancel_constant_button.hide()

    def _set_constant_error(self, message: str) -> None:
        self.constant_error_label.setText(message)
        self.constant_error_label.setVisible(bool(message))
        self._set_error(message)

    def _save_constant(self) -> None:
        try:
            self.configuration.set_constant(
                self.constant_name.text(),
                self.constant_value.text(),
            )
        except Exception as exc:
            self._set_constant_error(str(exc))
            return
        self._reset_constant_form()
        self._set_constant_error("")
        self._refresh_constants()

    def _delete_selected_constant(self) -> None:
        row = self.constants_table.currentRow()
        if row < 0 or self.constants_table.item(row, 0) is None:
            return
        self._delete_constant(self.constants_table.item(row, 0).text())

    def _delete_constant(self, name: str) -> None:
        try:
            self.configuration.delete_constant(name)
        except Exception as exc:
            self._set_constant_error(str(exc))
            return
        if self.constant_name.text() == name:
            self._reset_constant_form()
        self._set_constant_error("")
        self._refresh_constants()

    def _module_row_changed(self, row: int) -> None:
        if self._loading or row < 0:
            return
        modules = self.configuration.modules()
        if row < len(modules):
            self._load_module_form(modules[row])

    def _load_module_form(self, module) -> None:
        self._selected_module_name = module.name
        self.module_name.setText(module.name)
        self.module_name.setToolTip(module.name)
        self.module_label.setText(module.label)
        self.module_label.setToolTip(module.label)
        self.module_rater.setText(module.rater or "")
        self.module_rater.setToolTip(module.rater or "")
        self.module_code.setPlainText(module.code or "")
        self.module_control.setChecked(module.control)
        self.score_table.setRowCount(len(module.scores))
        for row, score in enumerate(module.scores.values()):
            label_item = QTableWidgetItem(score.label or "")
            label_item.setToolTip(score.label or "")
            values_item = QTableWidgetItem(score.num_ or "")
            values_item.setToolTip(score.num_ or "")
            self.score_table.setItem(row, 0, label_item)
            self.score_table.setItem(row, 1, values_item)
        self.tag_table.setRowCount(len(module.tags))
        for row, tag in enumerate(module.tags.values()):
            tag_item = QTableWidgetItem(tag.label or "")
            tag_item.setToolTip(tag.label or "")
            self.tag_table.setItem(row, 0, tag_item)
        self._module_form_baseline = self._module_form_signature()
        self._request_module_filter_preview(module)

    def _prepare_new_module(self) -> None:
        self._selected_module_name = None
        self._module_form_baseline = None
        self.module_name.clear()
        self.module_name.setToolTip("")
        self.module_label.clear()
        self.module_label.setToolTip("")
        self.module_rater.clear()
        self.module_rater.setToolTip("")
        self.module_code.clear()
        self.module_control.setChecked(False)
        self.score_table.setRowCount(1)
        self.score_table.setItem(0, 0, QTableWidgetItem("质量"))
        self.score_table.setItem(0, 1, QTableWidgetItem("差,一般,好"))
        self.tag_table.setRowCount(1)
        self.tag_table.setItem(0, 0, QTableWidgetItem("需要复核"))
        if (
            self._pending_module_filter is not None
            and self._pending_module_filter[1] == "prepare"
        ):
            self._pending_module_filter = None
            self.module_filter_task_controller.cancel()
        self._module_filter_ready_name = None
        self.module_filter_summary.setText("请先保存模块")
        self._set_module_filter_error("")
        self._update_module_filter_action_state()

    def _discard_module_form(self) -> None:
        if self._selected_module_name is None:
            self._prepare_new_module()
            self._set_error("")
            return
        module = next(
            (
                item
                for item in self.configuration.modules()
                if item.name == self._selected_module_name
            ),
            None,
        )
        if module is None:
            self._selected_module_name = None
            self._refresh_modules()
            return
        self._load_module_form(module)
        self._set_error("")

    @staticmethod
    def _append_table_row(table: QTableWidget, values: tuple[str, ...]) -> None:
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(value))
        table.setCurrentCell(row, 0)

    @staticmethod
    def _remove_table_row(table: QTableWidget) -> None:
        row = table.currentRow()
        if row >= 0 and table.rowCount() > 1:
            table.removeRow(row)

    def add_module(self, name: str, label: str) -> bool:
        try:
            self.configuration.add_module(name, label)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._selected_module_name = name
        self._set_error("")
        self._refresh_modules(name)
        return True

    def move_module(self, name: str, delta: int) -> bool:
        try:
            moved = self.configuration.move_module(name, delta)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._selected_module_name = name
        self._set_error("")
        self._refresh_modules(name)
        return moved

    def _move_selected_module(self, delta: int) -> None:
        if self._selected_module_name:
            self.move_module(self._selected_module_name, delta)

    def _save_module_form(self) -> None:
        name = self.module_name.text().strip()
        label = self.module_label.text().strip() or name
        scores = {}
        for row in range(self.score_table.rowCount()):
            score_label = self.score_table.item(row, 0)
            score_values = self.score_table.item(row, 1)
            values = score_values.text().strip() if score_values else ""
            scores[str(row + 1)] = Score(
                key=str(row + 1),
                label=score_label.text().strip() if score_label else "",
                num=values,
                num_=values,
            )
        tags = {}
        for row in range(self.tag_table.rowCount()):
            tag_label = self.tag_table.item(row, 0)
            tags[str(row + 1)] = Tag(
                key=str(row + 1),
                label=tag_label.text().strip() if tag_label else "",
            )
        if self._selected_module_name is None:
            if self.add_module(name, label):
                module = next(item for item in self.configuration.modules() if item.name == name)
            else:
                return
        else:
            module = next(
                item for item in self.configuration.modules() if item.name == self._selected_module_name
            )
        module = deepcopy(module)
        module.name = name
        module.label = label
        module.rater = self.module_rater.text().strip() or None
        module.code = self.module_code.toPlainText().strip() or None
        module.control = self.module_control.isChecked()
        module.scores = scores
        module.tags = tags
        try:
            self.configuration.save_module(
                module,
                original_name=self._selected_module_name or name,
            )
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._selected_module_name = name
        self._set_error("")
        self._refresh_modules(name)

    def _delete_selected_module(self) -> None:
        if not self._selected_module_name:
            return
        try:
            self.configuration.remove_module(self._selected_module_name)
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._selected_module_name = None
        self._set_error("")
        self._refresh_modules()

    def import_module(self, path: str) -> bool:
        try:
            self.configuration.import_module_file(path)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._set_error("")
        self._refresh_modules()
        return True

    def export_selected_module(self, path: str) -> bool:
        if not self._selected_module_name:
            return False
        try:
            self.configuration.export_module(self._selected_module_name, path)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._set_error("")
        return True

    def _choose_module_import(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            translate_ui_text("导入质控模块"),
            "",
            translate_ui_text("JSON 文件 (*.json)"),
        )
        if path:
            configuration = self.configuration

            def import_module() -> tuple:
                configuration.import_module_file(path, notify=False)
                return configuration.modules()

            self._submit_io("import_module", import_module)

    def _choose_module_export(self) -> None:
        if not self._selected_module_name:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            translate_ui_text("导出质控模块"),
            f"qcmodule_{self._selected_module_name}.json",
            translate_ui_text("JSON 文件 (*.json)"),
        )
        if path:
            configuration = self.configuration
            module_name = self._selected_module_name
            self._submit_io(
                "export_module",
                lambda: configuration.export_module(module_name, path),
            )

    def _submit_io(self, operation: str, function: Callable[[], object]) -> bool:
        if self.io_task_controller.busy:
            self._set_error("另一项配置任务仍在运行")
            return False
        if self.module_filter_write_busy:
            self._set_error("质控名单筛选事务正在完成，请稍候")
            return False
        labels = {
            "refresh": "正在加载配置…",
            "load_project": "正在加载项目…",
            "import_project": "正在导入项目…",
            "import_module": "正在导入质控模块…",
            "export_module": "正在导出质控模块…",
        }
        if operation not in labels:
            raise ValueError(f"Unsupported background operation: {operation}")
        self._io_revision += 1
        self._pending_io = (self._io_revision, operation)
        self._set_error("")
        self.status_label.setText(labels[operation])
        self.io_task_controller.submit(self._io_revision, function)
        return True

    @Slot(int, object)
    def _handle_io_result(self, revision: int, result: object) -> None:
        if self._pending_io is None or self._pending_io[0] != revision:
            return
        operation = self._pending_io[1]
        try:
            if operation in {"refresh", "load_project", "import_project"}:
                if not isinstance(result, ConfigurationSnapshot):
                    raise TypeError("项目加载任务返回了无效快照")
                if operation != "refresh":
                    self.configuration.publish_project_changed()
                self._selected_module_name = None
                self.refresh(result)
            elif operation == "import_module":
                if not isinstance(result, tuple):
                    raise TypeError("模块导入任务返回了无效模块列表")
                self.configuration.publish_modules_changed()
                self._refresh_modules(modules=result)
            elif operation != "export_module":
                raise ValueError(f"Unsupported background operation: {operation}")
        except Exception as exc:
            self._pending_io = None
            self.status_label.setText("配置任务失败")
            self._set_error(str(exc))
            return
        completion = {
            "refresh": "配置已加载",
            "load_project": "项目已加载",
            "import_project": "项目导入完成",
            "import_module": "质控模块导入完成",
            "export_module": "导出完成",
        }
        self._pending_io = None
        self._set_error("")
        self.status_label.setText(completion[operation])

    @Slot(int, object)
    def _handle_io_error(self, revision: int, error: object) -> None:
        if self._pending_io is None or self._pending_io[0] != revision:
            return
        self._pending_io = None
        self.status_label.setText("配置任务失败")
        self._set_error(str(error).strip() or type(error).__name__)

    @Slot(bool)
    def _set_io_busy(self, busy: bool) -> None:
        enabled = not busy and not self.module_filter_write_busy
        self.project_combo.setEnabled(enabled)
        self.project_list.setEnabled(enabled)
        self.reload_projects_action.setEnabled(enabled)
        self.new_project_action.setEnabled(enabled)
        self.import_project_action.setEnabled(enabled)
        self._update_project_action_state()
        self.tabs.setEnabled(enabled)
        self.subjects_tab.setEnabled(enabled)
        self.constants_tab.setEnabled(enabled)
        self.modules_tab.setEnabled(enabled)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._pending_io = None
        self.io_task_controller.cancel()
        self._pending_module_filter = None
        self.module_filter_task_controller.cancel()
        super().closeEvent(event)

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))


__all__ = ["QtProjectConfigWorkspace"]
