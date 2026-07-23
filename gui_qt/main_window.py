"""Qt product shell exposing the six user tasks as direct navigation pages."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
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
from gui_qt.qc_workspace import QtQcWorkspace
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

        self.setObjectName("qtPreviewWindow")
        self.setAccessibleName(
            "EasyQC Qt preview" if self._injected_preview else "EasyQC workspace"
        )
        self.setWindowTitle(
            "EasyQC — Qt Table Preview"
            if self._injected_preview
            else "EasyQC — Quality control workspace"
        )
        self.resize(1360, 840)
        self._build_content(initial_source)
        self._subscribe_events()

        if self._injected_preview:
            self.table_workspace.replace_service(
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
        self.navigation.addItems(self.NAVIGATION_LABELS)
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
        self.shell_status_label.setAccessibleName("Project context status")
        self.shell_status_label.setWordWrap(True)
        self.shell_status_label.setSizePolicy(
            QSizePolicy.Ignored,
            QSizePolicy.Preferred,
        )
        project_layout.addWidget(self.shell_status_label)
        self.shell_error_label = QLabel("", self.project_page)
        self.shell_error_label.setObjectName("shellError")
        self.shell_error_label.setAccessibleName("Project routing error")
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

        self.qc_list_import_page = QWidget(self.workspace_stack)
        self.qc_list_import_page.setObjectName("qcListImportPage")
        variables_layout = QVBoxLayout(self.qc_list_import_page)
        variables_layout.setContentsMargins(18, 16, 18, 16)
        variables_layout.addWidget(self.config_workspace.subjects_tab)
        self.variables_page = self.qc_list_import_page

        self.pre_qc_list_page = QWidget(self.workspace_stack)
        self.pre_qc_list_page.setObjectName("preQcListPage")
        table_layout = QVBoxLayout(self.pre_qc_list_page)
        table_layout.setContentsMargins(0, 0, 0, 0)
        self.shell_empty_label = QLabel(
            "No project is loaded. Open 项目选择 to create or import one.",
            self.pre_qc_list_page,
        )
        self.shell_empty_label.setObjectName("shellEmptyState")
        self.shell_empty_label.setWordWrap(True)
        table_layout.addWidget(self.shell_empty_label)
        self.table_workspace = QtTableWorkspace(source, parent=self.pre_qc_list_page)
        table_layout.addWidget(self.table_workspace, 1)
        self.subjects_page = self.pre_qc_list_page

        self.modules_page = QWidget(self.workspace_stack)
        self.modules_page.setObjectName("qcModulesPage")
        modules_layout = QVBoxLayout(self.modules_page)
        modules_layout.setContentsMargins(18, 16, 18, 16)
        modules_layout.addWidget(self.config_workspace.modules_tab)
        self.module_combo = QComboBox(self.modules_page)
        self.module_combo.setObjectName("internalModuleSelector")
        self.module_combo.setAccessibleName("当前质控模块")
        self.module_combo.hide()

        self.results_page = QWidget(self.workspace_stack)
        self.results_page.setObjectName("qcResultsPage")
        results_layout = QVBoxLayout(self.results_page)
        results_layout.setContentsMargins(18, 16, 18, 16)
        results_title = QLabel("质控结果", self.results_page)
        results_title.setObjectName("qcResultsTitle")
        results_layout.addWidget(results_title)
        results_layout.addStretch(1)

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

        # The old embedded QC host remains hidden until GUI-R6 replaces it with
        # the approved separate compact controller window.
        self.qc_page = QWidget(central)
        self.qc_page.setObjectName("internalQcHost")
        self.qc_page.hide()
        self.qc_layout = QVBoxLayout(self.qc_page)
        self.qc_layout.setContentsMargins(0, 0, 0, 0)
        self.qc_placeholder = QLabel(
            "Load a project with list entries and a QC module to begin review.",
            self.qc_page,
        )
        self.qc_placeholder.setObjectName("qcEmptyState")
        self.qc_placeholder.setWordWrap(True)
        self.qc_layout.addWidget(self.qc_placeholder)
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
            self._set_error("Another project materialization is already running")
            return False
        self._context_revision += 1
        self._pending_context = (self._context_revision, operation, preserve_qc)
        self._set_error("")
        self.shell_status_label.setText(
            "Loading project data…" if operation != "rating_refresh" else "Refreshing rating results…"
        )
        self.context_task_controller.submit(self._context_revision, function)
        return True

    def load_project(self, name: str) -> bool:
        if self.active_workflow is not None and self.active_workflow.dirty:
            self._set_error("Save or discard the current QC draft before changing project")
            return False
        name = str(name).strip()
        if not name:
            self._set_error("Select a project to load")
            return False
        return self._submit_context(
            "project_load",
            lambda: self.context_service.prepare_project(name),
            preserve_qc=False,
        )

    def refresh_context(self) -> bool:
        if self.active_workflow is not None and self.active_workflow.dirty:
            self._set_error("Save or discard the current QC draft before reloading")
            return False
        return self._submit_context(
            "refresh",
            self.context_service.prepare_initial,
            preserve_qc=False,
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
            self.shell_status_label.setText("Project load failed")
            self._set_error(str(exc).strip() or type(exc).__name__)
            return
        self._pending_context = None
        self.shell_status_label.setText(
            "Rating results refreshed"
            if operation == "rating_refresh"
            else (
                f"Loaded {snapshot.project_name}"
                if snapshot.has_project
                else "No project is loaded"
            )
        )
        self._set_error("")

    @Slot(int, object)
    def _handle_context_error(self, revision: int, error: object) -> None:
        if self._pending_context is None or self._pending_context[0] != revision:
            return
        self._pending_context = None
        self.shell_status_label.setText("Project load failed")
        self._set_error(str(error).strip() or type(error).__name__)

    @Slot(bool)
    def _set_context_busy(self, _busy: bool) -> None:
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
        elif not (preserve_qc and same_project and self.qc_workspace is not None):
            module_name = str(self.module_combo.currentData() or "")
            initial = str(snapshot.subjects.iloc[0]["ezqcid"])
            try:
                workflow = self.context_service.create_qc_workflow(
                    snapshot,
                    module_name=module_name,
                    initial_ezqcid=initial,
                    navigation_ids=tuple(snapshot.subjects["ezqcid"].astype(str)),
                )
            except Exception as exc:
                self._clear_qc_workspace()
                self._set_error(f"QC workspace unavailable: {exc}")
            else:
                self._install_qc_workspace(workflow, module_name)
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
                rater = str(module.rater or "").strip() or "read-only"
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

    def _module_selected(self, _index: int) -> None:
        if self._updating_controls or not self.current_context.has_project:
            return
        module_name = str(self.module_combo.currentData() or "")
        if not module_name or module_name == self._active_module_name:
            return
        workflow = self.active_workflow
        if workflow is not None and workflow.dirty:
            self._set_error("Save or discard the current QC draft before changing module")
            self._restore_active_module_selector()
            return
        initial = (
            workflow.current_ezqcid
            if workflow is not None
            and workflow.current_ezqcid in set(self.current_context.subjects["ezqcid"].astype(str))
            else str(self.current_context.subjects.iloc[0]["ezqcid"])
        )
        try:
            replacement = self.context_service.create_qc_workflow(
                self.current_context,
                module_name=module_name,
                initial_ezqcid=initial,
                navigation_ids=tuple(self.current_context.subjects["ezqcid"].astype(str)),
            )
        except Exception as exc:
            self._set_error(str(exc))
            self._restore_active_module_selector()
            return
        self._install_qc_workspace(replacement, module_name)
        self._set_error("")

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
            self._set_error("The Table action belongs to a stale project context")
            return
        if self.active_workflow is not None and self.active_workflow.dirty:
            self._set_error("Save or discard the current QC draft before opening another queue")
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
        replacement = QtQcWorkspace(workflow, parent=self.qc_page)
        replacement.draftStateChanged.connect(self._on_qc_draft_changed)
        previous = self.qc_workspace
        if previous is not None:
            previous.close()
            previous.setParent(None)
            previous.deleteLater()
        self.qc_placeholder.hide()
        self.qc_workspace = replacement
        self.qc_layout.addWidget(replacement)
        self._active_module_name = module_name
        self._on_qc_draft_changed(workflow.dirty)

    def _clear_qc_workspace(self) -> None:
        if self.qc_workspace is not None:
            self.qc_workspace.close()
            self.qc_workspace.setParent(None)
            self.qc_workspace.deleteLater()
            self.qc_workspace = None
        self._active_module_name = ""
        self.qc_placeholder.show()

    @Slot(bool)
    def _on_qc_draft_changed(self, _dirty: bool) -> None:
        self._update_context_controls()

    def _update_context_controls(self) -> None:
        busy = self.context_task_controller.busy
        dirty = bool(self.active_workflow is not None and self.active_workflow.dirty)
        enabled = not busy and not dirty and not self._injected_preview
        self.project_combo.setEnabled(enabled and bool(self.current_context.project_names))
        self.reload_action.setEnabled(enabled and self.current_context.has_project)
        self.module_combo.setEnabled(enabled and bool(self.current_context.modules))
        self.config_workspace.setEnabled(not busy and not dirty and not self._injected_preview)
        for section in (
            self.config_workspace.constants_tab,
            self.config_workspace.subjects_tab,
            self.config_workspace.modules_tab,
        ):
            section.setEnabled(not busy and not dirty and not self._injected_preview)

    def _on_project_facts_changed(self, _event: Event) -> None:
        if self._closing or self._injected_preview:
            return
        if self.active_workflow is not None and self.active_workflow.dirty:
            self._set_error("Project facts changed while a QC draft is open; save or discard, then reload")
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
                "Unsaved QC draft",
                "Discard the unsaved QC draft and close EasyQC?",
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
        self.config_workspace.close()
        if self.qc_workspace is not None:
            self.qc_workspace.close()
        super().closeEvent(event)


__all__ = ["QtMainWindow"]
