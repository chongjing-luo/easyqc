"""Qt product shell exposing the six user tasks as direct navigation pages."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QSize, Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent, QFont, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.app_services import AppServices
from core.configuration_service import ConfigurationSnapshot
from core.event_bus import Event, EventType
from core.project_context_service import (
    PreparedProjectContext,
    ProjectContextError,
    ProjectContextSnapshot,
)
from core.qc_workflow_service import QcWorkflowService
from core.table_view_service import TableViewService
from gui_qt.project_config_workspace import QtProjectConfigWorkspace
from gui_qt.qc_results_page import QtQcResultsPage
from gui_qt.qc_workspace import QtQcControllerWindow, QtQcWorkspace
from gui_qt.table_workspace import QtTableWorkspace
from gui_qt.task_runner import RevisionedTaskController


class QtMainWindow(QMainWindow):
    """Own one accepted project context and at most one live QC workflow."""

    NAVIGATION_LABELS = (
        "项目选择",
        "质控名单导入",
        "质控前名单",
        "常量设置",
        "质控模块",
        "质控结果",
    )

    def __init__(
        self,
        services: AppServices,
        source: pd.DataFrame | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(services, AppServices):
            raise TypeError("QtMainWindow requires AppServices")
        if source is not None and not isinstance(source, pd.DataFrame):
            raise TypeError("QtMainWindow table source must be a pandas DataFrame or None")
        self.services = services
        self.context_service = services.project_context_service
        self._injected_preview = source is not None
        self._closing = False
        self._updating_controls = False
        self._active_module_name = ""
        self._context_revision = 0
        self._pending_context: tuple[int, str, bool] | None = None
        self._event_subscriptions: list[tuple[EventType, object]] = []
        self.context_task_controller = RevisionedTaskController(self)
        self.context_task_controller.resultReady.connect(self._handle_context_result)
        self.context_task_controller.errorRaised.connect(self._handle_context_error)
        self.context_task_controller.busyChanged.connect(self._set_context_busy)

        initial_source = (
            source.copy(deep=True)
            if source is not None
            else pd.DataFrame(columns=["ezqcid"])
        )
        self.current_context = ProjectContextSnapshot(
            project_names=tuple(services.project_service.list_all()),
            project_name="",
            project_path=None,
            context_revision=0,
            subjects=pd.DataFrame(columns=["ezqcid"]),
            constants={},
            modules=(),
            table_view_service=TableViewService(initial_source),
        )
        self.qc_workspace: QtQcWorkspace | None = None
        self.qc_controller: QtQcControllerWindow | None = None

        self.setObjectName("qtPreviewWindow")
        self.setAccessibleName("EasyQC 表格预览" if self._injected_preview else "EasyQC 工作区")
        self.setWindowTitle("EasyQC")
        self.resize(1360, 840)
        self._build_content(initial_source)
        self._subscribe_events()

        if self._injected_preview:
            self.table_workspace.replace_service(
                self.current_context.table_view_service,
                preserve_state=False,
            )
            self.results_page.replace_service(
                self.current_context.table_view_service,
                preserve_state=False,
            )
            self._sync_preview_aliases()
            self._update_context_controls()
        else:
            self._submit_context(
                "initial",
                self.context_service.prepare_initial,
                preserve_qc=False,
            )

    def _build_content(self, source: pd.DataFrame) -> None:
        central = QWidget(self)
        central.setObjectName("qtPreviewRoot")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        navigation_panel = QWidget(central)
        navigation_panel.setObjectName("primaryNavigationPanel")
        navigation_layout = QVBoxLayout(navigation_panel)
        navigation_layout.setContentsMargins(12, 16, 12, 12)
        navigation_layout.setSpacing(10)
        product_name = QLabel("EasyQC", navigation_panel)
        product_name.setObjectName("productName")
        product_name.setAccessibleName("EasyQC")
        navigation_layout.addWidget(product_name)
        self.navigation = QListWidget(navigation_panel)
        self.navigation.setObjectName("primaryNavigation")
        self.navigation.setAccessibleName("EasyQC 功能导航")
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        navigation_font = QFont(self.navigation.font())
        if navigation_font.pointSizeF() > 0:
            navigation_font.setPointSizeF(navigation_font.pointSizeF() + 1)
        elif navigation_font.pixelSize() > 0:
            navigation_font.setPixelSize(navigation_font.pixelSize() + 1)
        self.navigation.setFont(navigation_font)
        self.navigation.setSpacing(4)
        self.navigation.addItems(self.NAVIGATION_LABELS)
        for row in range(self.navigation.count()):
            self._set_navigation_item_height(row, line_count=1)
        navigation_layout.addWidget(self.navigation, 1)
        minimum_navigation_width = max(
            self.navigation.fontMetrics().horizontalAdvance("质控名单导入") + 48,
            132,
        )
        navigation_panel.setMinimumWidth(minimum_navigation_width)
        navigation_panel.setMaximumWidth(max(220, minimum_navigation_width))
        layout.addWidget(navigation_panel)

        self.workspace_stack = QStackedWidget(central)
        self.workspace_stack.setObjectName("workspaceStack")
        self.workspace_stack.setAccessibleName("EasyQC 当前功能页")

        self.project_page = QWidget(self.workspace_stack)
        self.project_page.setObjectName("projectSelectionPage")
        project_layout = QVBoxLayout(self.project_page)
        project_layout.setContentsMargins(18, 16, 18, 16)
        project_layout.setSpacing(8)
        self.shell_status_label = QLabel("", self.project_page)
        self.shell_status_label.setObjectName("shellStatus")
        self.shell_status_label.setAccessibleName("项目状态")
        self.shell_status_label.setWordWrap(True)
        self.shell_status_label.setSizePolicy(
            QSizePolicy.Ignored,
            QSizePolicy.Preferred,
        )
        project_layout.addWidget(self.shell_status_label)
        self.shell_error_label = QLabel("", self.project_page)
        self.shell_error_label.setObjectName("shellError")
        self.shell_error_label.setAccessibleName("项目操作错误")
        self.shell_error_label.setWordWrap(True)
        self.shell_error_label.setSizePolicy(
            QSizePolicy.Ignored,
            QSizePolicy.Preferred,
        )
        project_layout.addWidget(self.shell_error_label)
        self.config_workspace = QtProjectConfigWorkspace(
            self.services.configuration_service,
            parent=self.project_page,
            auto_refresh=False,
            project_loader=self.load_project,
            module_launcher=self.start_qc_module,
        )
        project_layout.addWidget(self.config_workspace, 1)
        self.project_combo = self.config_workspace.project_combo

        # Reuse the already tested configuration sections as direct pages while
        # their behavior is split into focused page classes in later slices.
        self.config_workspace.tabs.removeTab(
            self.config_workspace.tabs.indexOf(self.config_workspace.modules_tab)
        )
        self.config_workspace.tabs.removeTab(
            self.config_workspace.tabs.indexOf(self.config_workspace.constants_tab)
        )
        self.config_workspace.tabs.removeTab(
            self.config_workspace.tabs.indexOf(self.config_workspace.subjects_tab)
        )
        self.config_workspace.tabs.hide()

        self.constants_page = QWidget(self.workspace_stack)
        self.constants_page.setObjectName("constantsSettingsPage")
        constants_layout = QVBoxLayout(self.constants_page)
        constants_layout.setContentsMargins(18, 16, 18, 16)
        constants_layout.addWidget(self.config_workspace.constants_tab)
        self.config_workspace.constants_tab.show()

        self.qc_list_import_page = QWidget(self.workspace_stack)
        self.qc_list_import_page.setObjectName("qcListImportPage")
        variables_layout = QVBoxLayout(self.qc_list_import_page)
        variables_layout.setContentsMargins(18, 16, 18, 16)
        self.qc_list_import_scroll = QScrollArea(self.qc_list_import_page)
        self.qc_list_import_scroll.setObjectName("qcListImportScroll")
        self.qc_list_import_scroll.setAccessibleName("可滚动质控名单导入页")
        self.qc_list_import_scroll.setWidgetResizable(True)
        self.qc_list_import_scroll.setFrameShape(QFrame.NoFrame)
        self.qc_list_import_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.qc_list_import_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.qc_list_import_scroll.setWidget(self.config_workspace.subjects_tab)
        variables_layout.addWidget(self.qc_list_import_scroll)
        self.config_workspace.subjects_tab.show()
        self.variables_page = self.qc_list_import_page

        self.pre_qc_list_page = QWidget(self.workspace_stack)
        self.pre_qc_list_page.setObjectName("preQcListPage")
        table_layout = QVBoxLayout(self.pre_qc_list_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.shell_empty_label = QLabel(
            "尚未打开项目，请在“项目选择”中创建或导入项目。",
            self.pre_qc_list_page,
        )
        self.shell_empty_label.setObjectName("shellEmptyState")
        self.shell_empty_label.setWordWrap(True)
        table_layout.addWidget(self.shell_empty_label)
        self.table_workspace = QtTableWorkspace(
            source,
            derive_column_callback=(
                None
                if self._injected_preview
                else self._persist_derived_subject_column
            ),
            derive_preview_source=(
                None
                if self._injected_preview
                else self._derived_subject_preview
            ),
            on_derived_column_committed=(
                None
                if self._injected_preview
                else self._derived_subject_column_committed
            ),
            parent=self.pre_qc_list_page,
        )
        self.table_workspace.deriveBusyChanged.connect(
            lambda _busy: self._update_context_controls()
        )
        self.config_workspace.subjects_tab.deriveBusyChanged.connect(
            lambda _busy: self._update_context_controls()
        )
        table_layout.addWidget(self.table_workspace, 1)
        self.subjects_page = self.pre_qc_list_page

        self.modules_page = QWidget(self.workspace_stack)
        self.modules_page.setObjectName("qcModulesPage")
        modules_layout = QVBoxLayout(self.modules_page)
        modules_layout.setContentsMargins(18, 16, 18, 16)
        modules_layout.addWidget(self.config_workspace.modules_tab)
        self.config_workspace.modules_tab.show()
        self.module_combo = QComboBox(self.modules_page)
        self.module_combo.setObjectName("internalModuleSelector")
        self.module_combo.setAccessibleName("当前质控模块")
        self.module_combo.hide()

        self.results_page = QtQcResultsPage(
            source,
            refresh_callback=self.refresh_results,
            derive_column_callback=(
                None
                if self._injected_preview
                else self._persist_derived_subject_column
            ),
            derive_preview_source=(
                None
                if self._injected_preview
                else self._derived_subject_preview
            ),
            on_derived_column_committed=(
                None
                if self._injected_preview
                else self._derived_subject_column_committed
            ),
            parent=self.workspace_stack,
        )
        self.results_workspace = self.results_page.table_workspace
        self.results_workspace.deriveBusyChanged.connect(
            lambda _busy: self._update_context_controls()
        )

        self.direct_pages = (
            self.project_page,
            self.qc_list_import_page,
            self.pre_qc_list_page,
            self.constants_page,
            self.modules_page,
            self.results_page,
        )
        for page in self.direct_pages:
            self.workspace_stack.addWidget(page)
        (
            self.project_page_index,
            self.qc_list_import_page_index,
            self.pre_qc_list_page_index,
            self.constants_page_index,
            self.modules_page_index,
            self.results_page_index,
        ) = range(len(self.direct_pages))
        self.variables_page_index = self.qc_list_import_page_index
        self.subjects_page_index = self.pre_qc_list_page_index
        self.table_page = self.pre_qc_list_page
        self.table_tab_index = self.pre_qc_list_page_index
        self.config_page = self.project_page
        self.config_tab_index = self.project_page_index
        layout.addWidget(self.workspace_stack, 1)

        self.setCentralWidget(central)

        self.reload_action = QAction("刷新项目", self)
        self.reload_action.setShortcut(QKeySequence("Ctrl+R"))
        self.reload_action.setShortcutContext(Qt.WindowShortcut)
        self.reload_action.triggered.connect(self.refresh_context)
        self.addAction(self.reload_action)
        self.reload_button = None
        self.project_combo.activated.connect(self._project_selected)
        self.project_combo.currentTextChanged.connect(self.project_combo.setToolTip)
        self.module_combo.currentIndexChanged.connect(self._module_selected)
        self.module_combo.currentTextChanged.connect(self.module_combo.setToolTip)
        self.navigation.currentRowChanged.connect(self.workspace_stack.setCurrentIndex)
        self.navigation.setCurrentRow(
            self.pre_qc_list_page_index if self._injected_preview else self.project_page_index
        )
        self._sync_preview_aliases()

    def _subscribe_events(self) -> None:
        for event_type, callback in (
            (EventType.PROJECT_CHANGED, self._on_project_facts_changed),
            (EventType.SUBJECTS_CHANGED, self._on_project_facts_changed),
            (EventType.MODULES_CHANGED, self._on_project_facts_changed),
            (EventType.RATING_SAVED, self._on_rating_saved),
        ):
            self.services.event_bus.subscribe(event_type, callback)
            self._event_subscriptions.append((event_type, callback))

    def _unsubscribe_events(self) -> None:
        for event_type, callback in self._event_subscriptions:
            self.services.event_bus.unsubscribe(event_type, callback)
        self._event_subscriptions.clear()

    def _sync_preview_aliases(self) -> None:
        # Stable aliases retained for preview/package/component callers.
        self.table_view_service = self.table_workspace.service
        self.table_result = self.table_workspace.result
        self.table_window = self.table_workspace.row_window
        self.table_model = self.table_workspace.table_model
        self.table_view = self.table_workspace.table_view
        self.status_label = self.table_workspace.count_label
        self.empty_state_label = self.table_workspace.empty_state_label

    @property
    def active_workflow(self) -> QcWorkflowService | None:
        return self.qc_workspace.workflow if self.qc_workspace is not None else None

    def _set_error(self, message: str) -> None:
        self.shell_error_label.setText(message)
        self.shell_error_label.setVisible(bool(message))

    def _submit_context(self, operation: str, function, *, preserve_qc: bool) -> bool:
        if self.context_task_controller.busy:
            self._set_error("另一项项目加载任务仍在运行")
            return False
        self._context_revision += 1
        self._pending_context = (self._context_revision, operation, preserve_qc)
        self._set_error("")
        self.shell_status_label.setText(
            "正在加载项目数据…" if operation != "rating_refresh" else "正在刷新质控结果…"
        )
        self.context_task_controller.submit(self._context_revision, function)
        return True

    def load_project(self, name: str) -> bool:
        if self.active_workflow is not None and self.active_workflow.dirty:
            self._set_error("请先保存或放弃当前质控修改，再切换项目")
            return False
        name = str(name).strip()
        if not name:
            self._set_error("请选择要打开的项目")
            return False
        return self._submit_context(
            "project_load",
            lambda: self.context_service.prepare_project(name),
            preserve_qc=False,
        )

    def refresh_context(self) -> bool:
        if self.active_workflow is not None and self.active_workflow.dirty:
            self._set_error("请先保存或放弃当前质控修改，再刷新项目")
            return False
        return self._submit_context(
            "refresh",
            self.context_service.prepare_initial,
            preserve_qc=False,
        )

    def refresh_results(self) -> bool:
        """Refresh derived rating results without replacing a clean QC session."""

        if self.active_workflow is not None and self.active_workflow.dirty:
            message = "请先保存或放弃当前质控修改，再刷新结果"
            self._set_error(message)
            self.results_page.show_error(message)
            return False
        return self._submit_context(
            "results_refresh",
            self.context_service.prepare_initial,
            preserve_qc=True,
        )

    def _project_selected(self, _index: int) -> None:
        name = self.project_combo.currentData() or self.project_combo.currentText()
        if name and name != self.current_context.project_name:
            self.load_project(str(name))

    @Slot(int, object)
    def _handle_context_result(self, revision: int, result: object) -> None:
        pending = self._pending_context
        if pending is None or pending[0] != revision:
            return
        operation, preserve_qc = pending[1], pending[2]
        try:
            if isinstance(result, PreparedProjectContext):
                snapshot = self.context_service.activate(result)
            elif isinstance(result, ProjectContextSnapshot):
                snapshot = result
            else:
                raise TypeError("Project materialization returned an invalid context")
            self._apply_context(snapshot, preserve_qc=preserve_qc)
        except Exception as exc:
            self._pending_context = None
            self.shell_status_label.setText("项目加载失败")
            message = str(exc).strip() or type(exc).__name__
            self._set_error(message)
            self.results_page.show_error(message)
            return
        self._pending_context = None
        self.shell_status_label.setText(
            "质控结果已刷新"
            if operation in {"rating_refresh", "results_refresh"}
            else (
                f"已加载项目：{snapshot.project_name}"
                if snapshot.has_project
                else "尚未加载项目"
            )
        )
        self._set_error("")

    @Slot(int, object)
    def _handle_context_error(self, revision: int, error: object) -> None:
        if self._pending_context is None or self._pending_context[0] != revision:
            return
        self._pending_context = None
        self.shell_status_label.setText("项目加载失败")
        message = str(error).strip() or type(error).__name__
        self._set_error(message)
        self.results_page.show_error(message)

    @Slot(bool)
    def _set_context_busy(self, _busy: bool) -> None:
        self.results_page.set_refresh_busy(_busy)
        self._update_context_controls()

    def _apply_context(
        self,
        snapshot: ProjectContextSnapshot,
        *,
        preserve_qc: bool,
    ) -> None:
        previous = self.current_context
        same_project = (
            previous.project_name == snapshot.project_name
            and previous.project_path == snapshot.project_path
            and snapshot.has_project
        )
        previous_module = self._active_module_name
        self.current_context = snapshot
        self.table_workspace.replace_service(
            snapshot.table_view_service,
            preserve_state=bool(preserve_qc and same_project),
        )
        self.results_page.replace_service(
            snapshot.table_view_service,
            preserve_state=bool(preserve_qc and same_project),
        )
        self.table_workspace.on_open_qc = (
            lambda identity, context_revision=snapshot.context_revision: self._open_table_qc(
                identity,
                context_revision,
            )
            if snapshot.has_project
            else None
        )
        self._sync_preview_aliases()
        self._refresh_selectors(snapshot, previous_module if same_project else "")
        self.config_workspace.refresh(
            ConfigurationSnapshot(
                projects=snapshot.project_names,
                current_project_name=snapshot.project_name,
                subjects=snapshot.subjects.copy(deep=True),
                constants=deepcopy(snapshot.constants),
                modules=tuple(deepcopy(snapshot.modules)),
            )
        )

        if not snapshot.has_project or snapshot.subjects.empty or not snapshot.modules:
            self._clear_qc_workspace()
        elif not (preserve_qc and same_project and self.qc_controller is not None):
            # Opening a project only prepares configuration. QC starts solely
            # from a module-row action and therefore never appears implicitly.
            self._clear_qc_workspace()
        self.shell_empty_label.setVisible(not snapshot.has_project)
        self._update_context_controls()

    def _refresh_selectors(
        self,
        snapshot: ProjectContextSnapshot,
        preferred_module: str,
    ) -> None:
        self._updating_controls = True
        try:
            self.project_combo.clear()
            for name in snapshot.project_names:
                self.project_combo.addItem(name, name)
                self.project_combo.setItemData(
                    self.project_combo.count() - 1,
                    name,
                    Qt.ToolTipRole,
                )
            project_index = self.project_combo.findData(snapshot.project_name)
            self.project_combo.setCurrentIndex(project_index)

            self.module_combo.clear()
            for module in snapshot.modules:
                rater = str(module.rater or "").strip() or "只读"
                self.module_combo.addItem(
                    f"{module.label} · {module.name} · {rater}",
                    module.name,
                )
                self.module_combo.setItemData(
                    self.module_combo.count() - 1,
                    self.module_combo.itemText(self.module_combo.count() - 1),
                    Qt.ToolTipRole,
                )
            module_index = self.module_combo.findData(preferred_module)
            if module_index < 0 and self.module_combo.count():
                module_index = 0
            self.module_combo.setCurrentIndex(module_index)
            self._active_module_name = str(self.module_combo.currentData() or "")
            self._set_project_navigation_context(
                snapshot.project_name if snapshot.has_project else ""
            )
        finally:
            self._updating_controls = False

    def _set_project_navigation_context(self, project_name: str) -> None:
        """Show the active project only as secondary text on 项目选择."""

        item = self.navigation.item(self.project_page_index)
        if item is None:
            return
        name = str(project_name).strip()
        item.setText(
            self.NAVIGATION_LABELS[self.project_page_index]
            if not name
            else f"{self.NAVIGATION_LABELS[self.project_page_index]}\n{name}"
        )
        item.setToolTip(name)
        item.setData(
            Qt.AccessibleTextRole,
            "项目选择" if not name else f"项目选择，当前项目 {name}",
        )
        self._set_navigation_item_height(
            self.project_page_index,
            line_count=2 if name else 1,
        )

    def _set_navigation_item_height(self, row: int, *, line_count: int) -> None:
        item = self.navigation.item(int(row))
        if item is None:
            return
        height = self.navigation.fontMetrics().lineSpacing() * int(line_count) + 16
        item.setSizeHint(QSize(0, height))

    def _persist_derived_subject_column(
        self,
        name: str,
        expression: str,
    ) -> str:
        """Worker-side Core call; event publication stays on the GUI thread."""

        return self.services.configuration_service.derive_subject_column(
            name,
            expression,
            notify=False,
        )

    def _derived_subject_preview(self) -> pd.DataFrame:
        """Return ordinary subject columns, excluding rebuildable result fields."""

        return self.current_context.subjects.head(10).copy(deep=True)

    @Slot(str)
    def _derived_subject_column_committed(self, _name: str) -> None:
        self.services.configuration_service.publish_subjects_changed()

    def _reject_qc_launch(self, message: str) -> bool:
        text = str(message).strip() or "未知错误"
        self._set_error(text)
        self.config_workspace.set_module_launch_feedback(
            f"启动失败：{text}"
        )
        return False

    def start_qc_module(self, module_name: str) -> bool:
        """Install the exact requested module through the existing QC factory."""

        requested = str(module_name).strip()
        if self._injected_preview or self.context_task_controller.busy:
            return self._reject_qc_launch("当前状态无法启动质控")
        if not self.current_context.has_project:
            return self._reject_qc_launch("请先打开项目")
        if self.current_context.subjects.empty:
            return self._reject_qc_launch("质控前名单为空")
        module_names = {module.name for module in self.current_context.modules}
        if requested not in module_names:
            return self._reject_qc_launch(f"质控模块不存在: {requested}")
        workflow = self.active_workflow
        if workflow is not None and workflow.dirty:
            return self._reject_qc_launch("请先保存或放弃当前质控修改")
        identities = tuple(self.current_context.subjects["ezqcid"].astype(str))
        initial = (
            workflow.current_ezqcid
            if workflow is not None and workflow.current_ezqcid in set(identities)
            else identities[0]
        )
        try:
            replacement = self.context_service.create_qc_workflow(
                self.current_context,
                module_name=requested,
                initial_ezqcid=initial,
                navigation_ids=identities,
            )
        except Exception as exc:
            return self._reject_qc_launch(str(exc))
        self._install_qc_workspace(replacement, requested)
        self._updating_controls = True
        try:
            self.module_combo.setCurrentIndex(
                self.module_combo.findData(requested)
            )
        finally:
            self._updating_controls = False
        self.shell_status_label.setText(f"已启动质控：{requested}")
        self._set_error("")
        return True

    def _module_selected(self, _index: int) -> None:
        if self._updating_controls or not self.current_context.has_project:
            return
        module_name = str(self.module_combo.currentData() or "")
        if not module_name or module_name == self._active_module_name:
            return
        if not self.start_qc_module(module_name):
            self._restore_active_module_selector()

    def _restore_active_module_selector(self) -> None:
        self._updating_controls = True
        try:
            self.module_combo.setCurrentIndex(
                self.module_combo.findData(self._active_module_name)
            )
        finally:
            self._updating_controls = False

    def _table_navigation_ids(self) -> tuple[str, ...]:
        result = self.table_workspace.result
        return tuple(
            self.table_workspace.service.validate_qc_identity(result, position)
            for position in range(result.matched_total)
        )

    def _open_table_qc(self, identity: str, context_revision: int) -> None:
        if context_revision != self.current_context.context_revision:
            self._set_error("该表格操作属于已失效的项目上下文")
            return
        if self.active_workflow is not None and self.active_workflow.dirty:
            self._set_error("请先保存或放弃当前质控修改，再打开其他名单")
            return
        module_name = str(self.module_combo.currentData() or "")
        try:
            navigation = self._table_navigation_ids()
            replacement = self.context_service.create_qc_workflow(
                self.current_context,
                module_name=module_name,
                initial_ezqcid=identity,
                navigation_ids=navigation,
            )
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._install_qc_workspace(replacement, module_name)
        self._set_error("")

    def _install_qc_workspace(
        self,
        workflow: QcWorkflowService,
        module_name: str,
    ) -> None:
        replacement = QtQcControllerWindow(workflow)
        replacement.workspace.draftStateChanged.connect(self._on_qc_draft_changed)
        replacement.closed.connect(
            lambda controller=replacement: self._qc_controller_closed(controller)
        )
        previous = self.qc_controller
        if previous is not None:
            previous.close()
        self.qc_controller = replacement
        self.qc_workspace = replacement.workspace
        self._active_module_name = module_name
        self._on_qc_draft_changed(workflow.dirty)
        replacement.show()
        replacement.raise_()
        replacement.activateWindow()

    def _qc_controller_closed(self, controller: QtQcControllerWindow) -> None:
        if self.qc_controller is not controller:
            return
        self.qc_controller = None
        self.qc_workspace = None
        self._active_module_name = ""
        self._update_context_controls()

    def _clear_qc_workspace(self, *, discard_draft: bool = False) -> bool:
        controller = self.qc_controller
        if controller is not None:
            closed = (
                controller.close_discarding_draft()
                if discard_draft
                else controller.close()
            )
            if not closed:
                return False
        self.qc_controller = None
        self.qc_workspace = None
        self._active_module_name = ""
        return True

    @Slot(bool)
    def _on_qc_draft_changed(self, _dirty: bool) -> None:
        self._update_context_controls()

    def _update_context_controls(self) -> None:
        busy = self.context_task_controller.busy
        derive_busy = (
            self.table_workspace.derive_busy
            or self.results_workspace.derive_busy
            or self.config_workspace.subjects_tab.derive_busy
        )
        dirty = bool(self.active_workflow is not None and self.active_workflow.dirty)
        enabled = (
            not busy
            and not derive_busy
            and not dirty
            and not self._injected_preview
        )
        self.project_combo.setEnabled(enabled and bool(self.current_context.project_names))
        self.reload_action.setEnabled(enabled and self.current_context.has_project)
        self.module_combo.setEnabled(enabled and bool(self.current_context.modules))
        self.table_workspace.set_derive_column_enabled(
            enabled and self.current_context.has_project
        )
        self.results_workspace.set_derive_column_enabled(
            enabled and self.current_context.has_project
        )
        self.config_workspace.subjects_tab.set_derive_column_enabled(
            enabled and self.current_context.has_project
        )
        self.config_workspace.setEnabled(enabled)
        for section in (
            self.config_workspace.constants_tab,
            self.config_workspace.subjects_tab,
            self.config_workspace.modules_tab,
        ):
            section.setEnabled(enabled)

    def _on_project_facts_changed(self, _event: Event) -> None:
        if self._closing or self._injected_preview:
            return
        if self.active_workflow is not None and self.active_workflow.dirty:
            self._set_error("项目内容已改变；请先保存或放弃当前质控修改，再刷新")
            return
        self._submit_context(
            "facts_refresh",
            self.context_service.prepare_initial,
            preserve_qc=False,
        )

    def _on_rating_saved(self, event: Event) -> None:
        if self._closing or self._injected_preview:
            return
        data = event.data or {}
        if data.get("context_revision") != self.current_context.context_revision:
            return
        self._submit_context(
            "rating_refresh",
            self.context_service.prepare_initial,
            preserve_qc=True,
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        workflow = self.active_workflow
        if workflow is not None and workflow.dirty:
            answer = QMessageBox.question(
                self,
                "尚未保存",
                "放弃当前未保存的质控修改并关闭 EasyQC？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self._closing = True
        self._unsubscribe_events()
        self.context_task_controller.cancel()
        self.table_workspace.close()
        self.results_page.close()
        self.config_workspace.close()
        self._clear_qc_workspace(discard_draft=True)
        super().closeEvent(event)


__all__ = ["QtMainWindow"]
