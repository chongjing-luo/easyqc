"""Typed Qt project, subject, constant and module configuration pages."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

import pandas as pd
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.configuration_service import ConfigurationService, ConfigurationSnapshot
from gui_qt.table_model import QtTableModel
from gui_qt.task_runner import RevisionedTaskController
from models.qcmodule import Score, Tag
from models.table_view_state import RowWindow


class QtProjectConfigWorkspace(QWidget):
    """Render typed configuration forms; Core owns every read/write action."""

    def __init__(
        self,
        configuration: ConfigurationService,
        parent: QWidget | None = None,
        *,
        auto_refresh: bool = True,
        project_loader: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(configuration, ConfigurationService):
            raise TypeError("QtProjectConfigWorkspace requires ConfigurationService")
        self.configuration = configuration
        self.project_loader = project_loader
        self._loading = False
        self._selected_module_name: str | None = None
        self._io_revision = 0
        self._pending_io: tuple[int, str] | None = None
        self.io_task_controller = RevisionedTaskController(self)
        self.io_task_controller.resultReady.connect(self._handle_io_result)
        self.io_task_controller.errorRaised.connect(self._handle_io_error)
        self.io_task_controller.busyChanged.connect(self._set_io_busy)
        self.setObjectName("qtProjectConfigWorkspace")
        self.setAccessibleName("EasyQC project configuration")
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

        project_bar = QFrame(self)
        project_bar.setObjectName("configHeader")
        project_layout = QHBoxLayout(project_bar)
        project_layout.setContentsMargins(12, 9, 12, 9)
        title = QLabel("Project configuration", project_bar)
        title.setObjectName("configTitle")
        project_layout.addWidget(title)
        project_layout.addStretch(1)
        project_layout.addWidget(QLabel("Project", project_bar))
        self.project_combo = QComboBox(project_bar)
        self.project_combo.setObjectName("projectSelector")
        self.load_project_button = QPushButton("Load", project_bar)
        self.new_project_button = QPushButton("New…", project_bar)
        self.import_project_button = QPushButton("Import…", project_bar)
        self.remove_project_button = QPushButton("Unregister", project_bar)
        project_layout.addWidget(self.project_combo)
        project_layout.addWidget(self.load_project_button)
        project_layout.addWidget(self.new_project_button)
        project_layout.addWidget(self.import_project_button)
        project_layout.addWidget(self.remove_project_button)
        layout.addWidget(project_bar)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("configTabs")
        self.subjects_tab = QWidget(self.tabs)
        self.constants_tab = QWidget(self.tabs)
        self.modules_tab = QWidget(self.tabs)
        self.tabs.addTab(self.subjects_tab, "Subjects")
        self.tabs.addTab(self.constants_tab, "Constants")
        self.tabs.addTab(self.modules_tab, "QC modules")
        self._build_subjects_tab()
        self._build_constants_tab()
        self._build_modules_tab()
        layout.addWidget(self.tabs, 1)

        self.status_label = QLabel("", self)
        self.status_label.setObjectName("configStatus")
        self.status_label.setAccessibleName("Configuration task status")
        layout.addWidget(self.status_label)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("configError")
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("Configuration error")
        layout.addWidget(self.error_label)

        self.load_project_button.clicked.connect(self._load_selected_project)
        self.new_project_button.clicked.connect(self._prompt_create_project)
        self.import_project_button.clicked.connect(self._prompt_import_project)
        self.remove_project_button.clicked.connect(self._confirm_remove_project)

    def _build_subjects_tab(self) -> None:
        layout = QVBoxLayout(self.subjects_tab)
        summary_row = QHBoxLayout()
        self.subject_summary = QLabel("", self.subjects_tab)
        self.subject_summary.setObjectName("subjectSummary")
        self.subject_mode_hint = QLabel(
            "Import replaces or explicitly merges through Core validation.",
            self.subjects_tab,
        )
        self.subject_mode_hint.setObjectName("panelHint")
        summary_row.addWidget(self.subject_summary)
        summary_row.addStretch(1)
        summary_row.addWidget(self.subject_mode_hint)
        layout.addLayout(summary_row)
        empty = RowWindow(
            dataframe=pd.DataFrame(columns=["ezqcid"]),
            source_positions=(),
            offset=0,
            limit=200,
            matched_total=0,
        )
        self.subject_model = QtTableModel(empty, self)
        self.subject_table = QTableView(self.subjects_tab)
        self.subject_table.setObjectName("configSubjectTable")
        self.subject_table.setAccessibleName("Configured subject table")
        self.subject_table.setModel(self.subject_model)
        self.subject_table.setAlternatingRowColors(True)
        self.subject_table.setEditTriggers(QTableView.NoEditTriggers)
        self.subject_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.subject_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.subject_table, 1)
        actions = QHBoxLayout()
        self.replace_subjects_button = QPushButton("Replace from CSV…", self.subjects_tab)
        self.merge_rows_button = QPushButton("Append rows…", self.subjects_tab)
        self.merge_columns_button = QPushButton("Merge columns…", self.subjects_tab)
        actions.addWidget(self.replace_subjects_button)
        actions.addWidget(self.merge_rows_button)
        actions.addWidget(self.merge_columns_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.replace_subjects_button.clicked.connect(lambda: self._choose_subject_csv("replace"))
        self.merge_rows_button.clicked.connect(lambda: self._choose_subject_csv("rows"))
        self.merge_columns_button.clicked.connect(lambda: self._choose_subject_csv("columns"))

    def _build_constants_tab(self) -> None:
        layout = QVBoxLayout(self.constants_tab)
        form = QHBoxLayout()
        self.constant_name = QLineEdit(self.constants_tab)
        self.constant_name.setPlaceholderText("Constant name")
        self.constant_value = QLineEdit(self.constants_tab)
        self.constant_value.setPlaceholderText("Value")
        self.save_constant_button = QPushButton("Add / update", self.constants_tab)
        self.save_constant_button.setObjectName("primaryAction")
        form.addWidget(self.constant_name)
        form.addWidget(self.constant_value, 2)
        form.addWidget(self.save_constant_button)
        layout.addLayout(form)
        self.constants_table = QTableWidget(0, 2, self.constants_tab)
        self.constants_table.setObjectName("constantsTable")
        self.constants_table.setHorizontalHeaderLabels(["Name", "Value"])
        self.constants_table.horizontalHeader().setStretchLastSection(True)
        self.constants_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.constants_table.setEditTriggers(QTableWidget.NoEditTriggers)
        layout.addWidget(self.constants_table, 1)
        self.delete_constant_button = QPushButton("Delete selected", self.constants_tab)
        layout.addWidget(self.delete_constant_button, 0, Qt.AlignRight)
        self.save_constant_button.clicked.connect(self._save_constant)
        self.delete_constant_button.clicked.connect(self._delete_selected_constant)

    def _build_modules_tab(self) -> None:
        layout = QHBoxLayout(self.modules_tab)
        left = QVBoxLayout()
        self.module_list = QListWidget(self.modules_tab)
        self.module_list.setObjectName("moduleList")
        self.module_list.setAccessibleName("Ordered QC modules")
        left.addWidget(self.module_list, 1)
        move_row = QHBoxLayout()
        self.module_up_button = QPushButton("Move up", self.modules_tab)
        self.module_down_button = QPushButton("Move down", self.modules_tab)
        move_row.addWidget(self.module_up_button)
        move_row.addWidget(self.module_down_button)
        left.addLayout(move_row)
        layout.addLayout(left, 1)

        editor = QVBoxLayout()
        identity_row = QHBoxLayout()
        self.module_name = QLineEdit(self.modules_tab)
        self.module_name.setPlaceholderText("Module name")
        self.module_label = QLineEdit(self.modules_tab)
        self.module_label.setPlaceholderText("Display label")
        self.module_rater = QLineEdit(self.modules_tab)
        self.module_rater.setPlaceholderText("Rater (blank = watch mode)")
        identity_row.addWidget(self.module_name)
        identity_row.addWidget(self.module_label)
        identity_row.addWidget(self.module_rater)
        editor.addLayout(identity_row)
        self.module_code = QTextEdit(self.modules_tab)
        self.module_code.setPlaceholderText("Allowlisted viewer command template")
        self.module_code.setMaximumHeight(100)
        editor.addWidget(self.module_code)
        self.module_control = QCheckBox("Close controlled viewers before relaunch", self.modules_tab)
        editor.addWidget(self.module_control)

        self.score_table = QTableWidget(0, 2, self.modules_tab)
        self.score_table.setHorizontalHeaderLabels(["Score label", "Choices (comma-separated)"])
        self.score_table.setColumnWidth(0, 240)
        self.score_table.horizontalHeader().setStretchLastSection(True)
        editor.addWidget(QLabel("Scores", self.modules_tab))
        editor.addWidget(self.score_table, 1)
        self.tag_table = QTableWidget(0, 1, self.modules_tab)
        self.tag_table.setHorizontalHeaderLabels(["Tag label"])
        self.tag_table.setColumnWidth(0, 320)
        self.tag_table.horizontalHeader().setStretchLastSection(True)
        editor.addWidget(QLabel("Tags", self.modules_tab))
        editor.addWidget(self.tag_table, 1)

        row_actions = QHBoxLayout()
        self.add_score_button = QPushButton("Add score", self.modules_tab)
        self.remove_score_button = QPushButton("Remove score", self.modules_tab)
        self.add_tag_button = QPushButton("Add tag", self.modules_tab)
        self.remove_tag_button = QPushButton("Remove tag", self.modules_tab)
        row_actions.addWidget(self.add_score_button)
        row_actions.addWidget(self.remove_score_button)
        row_actions.addWidget(self.add_tag_button)
        row_actions.addWidget(self.remove_tag_button)
        editor.addLayout(row_actions)

        actions = QHBoxLayout()
        self.new_module_button = QPushButton("New module", self.modules_tab)
        self.delete_module_button = QPushButton("Delete", self.modules_tab)
        self.save_module_button = QPushButton("Save module", self.modules_tab)
        self.save_module_button.setObjectName("primaryAction")
        self.import_module_button = QPushButton("Import…", self.modules_tab)
        self.export_module_button = QPushButton("Export…", self.modules_tab)
        actions.addWidget(self.new_module_button)
        actions.addWidget(self.delete_module_button)
        actions.addWidget(self.import_module_button)
        actions.addWidget(self.export_module_button)
        actions.addStretch(1)
        actions.addWidget(self.save_module_button)
        editor.addLayout(actions)
        layout.addLayout(editor, 3)

        self.module_list.currentRowChanged.connect(self._module_row_changed)
        self.module_up_button.clicked.connect(lambda: self._move_selected_module(-1))
        self.module_down_button.clicked.connect(lambda: self._move_selected_module(1))
        self.new_module_button.clicked.connect(self._prepare_new_module)
        self.delete_module_button.clicked.connect(self._delete_selected_module)
        self.save_module_button.clicked.connect(self._save_module_form)
        self.import_module_button.clicked.connect(self._choose_module_import)
        self.export_module_button.clicked.connect(self._choose_module_export)
        self.add_score_button.clicked.connect(lambda: self._append_table_row(self.score_table, ("Quality", "Poor,Fair,Good")))
        self.remove_score_button.clicked.connect(lambda: self._remove_table_row(self.score_table))
        self.add_tag_button.clicked.connect(lambda: self._append_table_row(self.tag_table, ("Needs review",)))
        self.remove_tag_button.clicked.connect(lambda: self._remove_table_row(self.tag_table))

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def refresh(self, snapshot: ConfigurationSnapshot | None = None) -> None:
        snapshot = self.configuration.snapshot() if snapshot is None else snapshot
        self._loading = True
        try:
            self.project_combo.clear()
            self.project_combo.addItems(snapshot.projects)
            if snapshot.current_project_name:
                self.project_combo.setCurrentText(snapshot.current_project_name)
            self._refresh_subjects(snapshot.subjects)
            self._refresh_constants(snapshot.constants)
            self._refresh_modules(self._selected_module_name, snapshot.modules)
        finally:
            self._loading = False

    def _refresh_subjects(self, frame: pd.DataFrame | None = None) -> None:
        if frame is None:
            frame = (
                self.configuration.subjects()
                if self.configuration.current_project
                else pd.DataFrame(columns=["ezqcid"])
            )
        window = RowWindow(
            dataframe=frame.copy(deep=True),
            source_positions=tuple(range(len(frame))),
            offset=0,
            limit=max(1, len(frame)),
            matched_total=len(frame),
        )
        self.subject_model.set_window(window)
        self.subject_summary.setText(f"{len(frame):,} subjects · {len(frame.columns)} columns")

    def _refresh_constants(self, constants: dict | None = None) -> None:
        items = list(
            (self.configuration.constants() if constants is None else constants).items()
        )
        self.constants_table.setRowCount(len(items))
        for row, (name, value) in enumerate(items):
            self.constants_table.setItem(row, 0, QTableWidgetItem(str(name)))
            self.constants_table.setItem(row, 1, QTableWidgetItem(str(value)))

    def _refresh_modules(
        self,
        selected_name: str | None = None,
        modules: tuple | None = None,
    ) -> None:
        modules = self.configuration.modules() if modules is None else modules
        self.module_list.blockSignals(True)
        self.module_list.clear()
        for position, module in enumerate(modules, start=1):
            self.module_list.addItem(f"{position:>2}  {module.name} · {module.label}")
        target = next(
            (index for index, module in enumerate(modules) if module.name == selected_name),
            0 if modules else -1,
        )
        self.module_list.setCurrentRow(target)
        self.module_list.blockSignals(False)
        if target >= 0:
            self._load_module_form(modules[target])

    def _load_selected_project(self) -> None:
        name = self.project_combo.currentText()
        if not name:
            return
        if self.project_loader is not None:
            if not self.project_loader(name):
                self._set_error("Project load request was not accepted")
            else:
                self._set_error("")
                self.status_label.setText("Loading project…")
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

        name, accepted = QInputDialog.getText(self, "New EasyQC project", "Project name")
        if not accepted or not name.strip():
            return
        directory = QFileDialog.getExistingDirectory(self, "Choose project parent directory")
        if directory:
            self.create_project(name, directory)

    def _prompt_import_project(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose an EasyQC project directory")
        if directory:
            configuration = self.configuration

            def import_project() -> ConfigurationSnapshot:
                configuration.import_project(directory, notify=False)
                return configuration.snapshot()

            self._submit_io("import_project", import_project)

    def _confirm_remove_project(self) -> None:
        name = self.project_combo.currentText()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Unregister project",
            f"Unregister {name}? Project files will not be deleted.",
        )
        if answer == QMessageBox.Yes:
            self.remove_project(name)

    def replace_subjects(self, frame: pd.DataFrame) -> bool:
        try:
            self.configuration.replace_subjects(frame)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._set_error("")
        self._refresh_subjects()
        return True

    def import_subjects(self, path: str, *, mode: str) -> bool:
        try:
            self.configuration.import_subject_csv(path, mode=mode)
        except Exception as exc:
            self._set_error(str(exc))
            return False
        self._set_error("")
        self._refresh_subjects()
        return True

    def _choose_subject_csv(self, mode: str) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose subject CSV",
            "",
            "CSV files (*.csv)",
        )
        if path:
            configuration = self.configuration

            def import_subjects() -> pd.DataFrame:
                configuration.import_subject_csv(path, mode=mode, notify=False)
                return configuration.subjects()

            self._submit_io("import_subjects", import_subjects)

    def _save_constant(self) -> None:
        try:
            self.configuration.set_constant(
                self.constant_name.text(),
                self.constant_value.text(),
            )
        except Exception as exc:
            self._set_error(str(exc))
            return
        self.constant_name.clear()
        self.constant_value.clear()
        self._set_error("")
        self._refresh_constants()

    def _delete_selected_constant(self) -> None:
        row = self.constants_table.currentRow()
        if row < 0 or self.constants_table.item(row, 0) is None:
            return
        name = self.constants_table.item(row, 0).text()
        try:
            self.configuration.delete_constant(name)
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._set_error("")
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
        self.module_label.setText(module.label)
        self.module_rater.setText(module.rater or "")
        self.module_code.setPlainText(module.code or "")
        self.module_control.setChecked(module.control)
        self.score_table.setRowCount(len(module.scores))
        for row, score in enumerate(module.scores.values()):
            self.score_table.setItem(row, 0, QTableWidgetItem(score.label or ""))
            self.score_table.setItem(row, 1, QTableWidgetItem(score.num_ or ""))
        self.tag_table.setRowCount(len(module.tags))
        for row, tag in enumerate(module.tags.values()):
            self.tag_table.setItem(row, 0, QTableWidgetItem(tag.label or ""))

    def _prepare_new_module(self) -> None:
        self._selected_module_name = None
        self.module_name.clear()
        self.module_label.clear()
        self.module_rater.clear()
        self.module_code.clear()
        self.module_control.setChecked(False)
        self.score_table.setRowCount(1)
        self.score_table.setItem(0, 0, QTableWidgetItem("Quality"))
        self.score_table.setItem(0, 1, QTableWidgetItem("Poor,Fair,Good"))
        self.tag_table.setRowCount(1)
        self.tag_table.setItem(0, 0, QTableWidgetItem("Needs review"))

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
            "Import QC module",
            "",
            "JSON files (*.json)",
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
            "Export QC module",
            f"qcmodule_{self._selected_module_name}.json",
            "JSON files (*.json)",
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
            self._set_error("Another configuration task is already running")
            return False
        labels = {
            "refresh": "Loading configuration…",
            "load_project": "Loading project…",
            "import_project": "Importing project…",
            "import_subjects": "Importing subjects…",
            "import_module": "Importing QC module…",
            "export_module": "Exporting QC module…",
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
                    raise TypeError("Project load returned an invalid snapshot")
                if operation != "refresh":
                    self.configuration.publish_project_changed()
                self._selected_module_name = None
                self.refresh(result)
            elif operation == "import_subjects":
                if not isinstance(result, pd.DataFrame):
                    raise TypeError("Subject import returned an invalid table")
                self.configuration.publish_subjects_changed()
                self._refresh_subjects(result)
            elif operation == "import_module":
                if not isinstance(result, tuple):
                    raise TypeError("Module import returned an invalid module list")
                self.configuration.publish_modules_changed()
                self._refresh_modules(modules=result)
            elif operation != "export_module":
                raise ValueError(f"Unsupported background operation: {operation}")
        except Exception as exc:
            self._pending_io = None
            self.status_label.setText("Configuration task failed")
            self._set_error(str(exc))
            return
        completion = {
            "refresh": "Configuration loaded",
            "load_project": "Project loaded",
            "import_project": "Project import complete",
            "import_subjects": "Subject import complete",
            "import_module": "QC module import complete",
            "export_module": "Export complete",
        }
        self._pending_io = None
        self._set_error("")
        self.status_label.setText(completion[operation])

    @Slot(int, object)
    def _handle_io_error(self, revision: int, error: object) -> None:
        if self._pending_io is None or self._pending_io[0] != revision:
            return
        self._pending_io = None
        self.status_label.setText("Configuration task failed")
        self._set_error(str(error).strip() or type(error).__name__)

    @Slot(bool)
    def _set_io_busy(self, busy: bool) -> None:
        enabled = not busy
        self.project_combo.setEnabled(enabled)
        self.load_project_button.setEnabled(enabled)
        self.new_project_button.setEnabled(enabled)
        self.import_project_button.setEnabled(enabled)
        self.remove_project_button.setEnabled(enabled)
        self.tabs.setEnabled(enabled)

    def closeEvent(self, event: QCloseEvent) -> None:
        self._pending_io = None
        self.io_task_controller.cancel()
        super().closeEvent(event)

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))


__all__ = ["QtProjectConfigWorkspace"]
