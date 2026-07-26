"""Installation-scoped constant, module, and command settings for Qt."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.code_executor import CodeExecutor
from core.module_repository import ModuleRecord
from core.template_service import TemplateService
from gui_qt.i18n import LanguageController, protect_user_text
from gui_qt.module_template_editor import QtModuleTemplateEditor
from gui_qt.theme import (
    CONTENT_MARGIN,
    CONTROL_SPACING,
    SECTION_SPACING,
    set_button_role,
)
from models.qcmodule import QCModule


class QtCrossProjectSettingsPage(QWidget):
    """Present one installation's template library without project coupling."""

    MODULE_ID_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(
        self,
        templates: TemplateService,
        executor: CodeExecutor,
        language: LanguageController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(templates, TemplateService):
            raise TypeError(
                "QtCrossProjectSettingsPage requires TemplateService"
            )
        if not isinstance(executor, CodeExecutor):
            raise TypeError(
                "QtCrossProjectSettingsPage requires CodeExecutor"
            )
        if not isinstance(language, LanguageController):
            raise TypeError(
                "QtCrossProjectSettingsPage requires LanguageController"
            )
        self.templates = templates
        self.executor = executor
        self.language = language
        self._editing_constant_name: str | None = None
        self._editing_module_id: str | None = None
        self._execution_saved = False
        self.setObjectName("crossProjectSettingsPage")
        self._build_ui()
        self.language.languageChanged.connect(self.retranslate_ui)
        self.refresh()
        self.retranslate_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("crossProjectSettingsTabs")
        layout.addWidget(self.tabs)
        self._build_constants_tab()
        self._build_modules_tab()
        self._build_execution_tab()
        self.tabs.addTab(self.constants_tab, "")
        self.tabs.addTab(self.modules_tab, "")
        self.tabs.addTab(self.execution_tab, "")

    def _new_tab_layout(self, tab: QWidget) -> QVBoxLayout:
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(
            CONTENT_MARGIN,
            CONTENT_MARGIN,
            CONTENT_MARGIN,
            CONTENT_MARGIN,
        )
        layout.setSpacing(SECTION_SPACING)
        return layout

    def _build_constants_tab(self) -> None:
        self.constants_tab = QWidget(self.tabs)
        self.constants_tab.setObjectName("constantTemplatesTab")
        layout = self._new_tab_layout(self.constants_tab)

        form_panel = QFrame(self.constants_tab)
        form_panel.setProperty("surface", "true")
        form = QHBoxLayout(form_panel)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(CONTROL_SPACING)
        self.constant_name = QLineEdit(form_panel)
        self.constant_value = QLineEdit(form_panel)
        self.cancel_constant_button = QPushButton(form_panel)
        self.save_constant_button = QPushButton(form_panel)
        set_button_role(self.save_constant_button, "primary")
        form.addWidget(self.constant_name, 2)
        form.addWidget(self.constant_value, 4)
        form.addWidget(self.cancel_constant_button)
        form.addWidget(self.save_constant_button)
        layout.addWidget(form_panel)

        search_row = QHBoxLayout()
        self.constant_search = QLineEdit(self.constants_tab)
        self.refresh_constants_button = QPushButton(self.constants_tab)
        search_row.addWidget(self.constant_search, 1)
        search_row.addWidget(self.refresh_constants_button)
        layout.addLayout(search_row)

        self.constants_table = QTableWidget(0, 2, self.constants_tab)
        self.constants_table.setObjectName("constantTemplatesTable")
        self.constants_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.constants_table.setSelectionMode(QTableWidget.SingleSelection)
        self.constants_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.constants_table.setAlternatingRowColors(True)
        self.constants_table.verticalHeader().hide()
        self.constants_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.ResizeToContents,
        )
        self.constants_table.horizontalHeader().setSectionResizeMode(
            1,
            QHeaderView.Stretch,
        )
        protect_user_text(self.constants_table, "items")
        layout.addWidget(self.constants_table, 1)

        self.constant_empty_label = QLabel(self.constants_tab)
        self.constant_empty_label.setProperty("role", "secondary")
        self.constant_empty_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.constant_empty_label)

        constant_footer = QHBoxLayout()
        self.constant_error_label = QLabel(self.constants_tab)
        self.constant_error_label.setProperty("role", "error")
        self.constant_error_label.setWordWrap(True)
        constant_footer.addWidget(self.constant_error_label)
        constant_footer.addStretch(1)
        self.delete_constant_button = QPushButton(self.constants_tab)
        set_button_role(self.delete_constant_button, "danger")
        constant_footer.addWidget(self.delete_constant_button)
        layout.addLayout(constant_footer)

        self.save_constant_button.clicked.connect(self._save_constant)
        self.cancel_constant_button.clicked.connect(self._reset_constant_form)
        self.refresh_constants_button.clicked.connect(self.refresh_constants)
        self.delete_constant_button.clicked.connect(
            self._delete_selected_constant
        )
        self.constant_search.textChanged.connect(self._filter_constants)
        self.constants_table.cellDoubleClicked.connect(
            self._edit_constant_row
        )

    def _build_modules_tab(self) -> None:
        self.modules_tab = QWidget(self.tabs)
        self.modules_tab.setObjectName("moduleTemplatesTab")
        layout = self._new_tab_layout(self.modules_tab)
        splitter = QSplitter(Qt.Horizontal, self.modules_tab)
        splitter.setChildrenCollapsible(False)

        list_panel = QFrame(splitter)
        list_panel.setProperty("surface", "true")
        left = QVBoxLayout(list_panel)
        left.setContentsMargins(14, 14, 14, 14)
        left.setSpacing(CONTROL_SPACING)
        module_header = QHBoxLayout()
        self.module_list_title = QLabel(list_panel)
        self.module_list_title.setProperty("role", "sectionTitle")
        self.new_module_button = QPushButton(list_panel)
        self.import_module_button = QPushButton(list_panel)
        module_header.addWidget(self.module_list_title)
        module_header.addStretch(1)
        module_header.addWidget(self.new_module_button)
        module_header.addWidget(self.import_module_button)
        left.addLayout(module_header)

        self.module_list = QListWidget(list_panel)
        self.module_list.setObjectName("moduleTemplatesList")
        self.module_list.setAlternatingRowColors(True)
        self.module_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        protect_user_text(self.module_list, "items")
        left.addWidget(self.module_list, 1)

        self.module_empty_label = QLabel(list_panel)
        self.module_empty_label.setProperty("role", "secondary")
        self.module_empty_label.setAlignment(Qt.AlignCenter)
        left.addWidget(self.module_empty_label)

        module_actions = QHBoxLayout()
        self.export_module_button = QPushButton(list_panel)
        self.delete_module_button = QPushButton(list_panel)
        set_button_role(self.delete_module_button, "danger")
        module_actions.addWidget(self.export_module_button)
        module_actions.addWidget(self.delete_module_button)
        left.addLayout(module_actions)

        self.module_editor = QtModuleTemplateEditor(
            self.language,
            self.modules_tab,
        )
        self.module_editor_scroll = QScrollArea(splitter)
        self.module_editor_scroll.setObjectName("moduleTemplateEditorScroll")
        self.module_editor_scroll.setWidgetResizable(True)
        self.module_editor_scroll.setFrameShape(QFrame.NoFrame)
        self.module_editor_scroll.setWidget(self.module_editor)
        splitter.addWidget(list_panel)
        splitter.addWidget(self.module_editor_scroll)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([380, 600])
        layout.addWidget(splitter, 1)

        self.module_error_label = QLabel(self.modules_tab)
        self.module_error_label.setProperty("role", "error")
        self.module_error_label.setWordWrap(True)
        layout.addWidget(self.module_error_label)

        self.new_module_button.clicked.connect(self.new_module_template)
        self.import_module_button.clicked.connect(self._choose_module_import)
        self.export_module_button.clicked.connect(self._choose_module_export)
        self.delete_module_button.clicked.connect(
            self._confirm_delete_selected_module
        )
        self.module_list.currentRowChanged.connect(self._module_row_changed)
        self.module_editor.saveRequested.connect(self._save_module_template)

    def _build_execution_tab(self) -> None:
        self.execution_tab = QWidget(self.tabs)
        self.execution_tab.setObjectName("commandExecutionTab")
        layout = self._new_tab_layout(self.execution_tab)

        self.execution_intro = QLabel(self.execution_tab)
        self.execution_intro.setProperty("role", "secondary")
        self.execution_intro.setWordWrap(True)
        layout.addWidget(self.execution_intro)

        self.execution_group = QGroupBox(self.execution_tab)
        group = QVBoxLayout(self.execution_group)
        group.setSpacing(CONTROL_SPACING)
        self.direct_radio = QRadioButton(self.execution_group)
        self.direct_detail = QLabel(self.execution_group)
        self.direct_detail.setProperty("role", "secondary")
        self.direct_detail.setWordWrap(True)
        self.shell_radio = QRadioButton(self.execution_group)
        self.shell_detail = QLabel(self.execution_group)
        self.shell_detail.setProperty("role", "secondary")
        self.shell_detail.setWordWrap(True)
        group.addWidget(self.direct_radio)
        group.addWidget(self.direct_detail)
        group.addSpacing(CONTROL_SPACING)
        group.addWidget(self.shell_radio)
        group.addWidget(self.shell_detail)
        layout.addWidget(self.execution_group)

        self.execution_gate = QLabel(self.execution_tab)
        self.execution_gate.setProperty("role", "secondary")
        self.execution_gate.setWordWrap(True)
        layout.addWidget(self.execution_gate)
        layout.addStretch(1)

        execution_footer = QHBoxLayout()
        self.execution_error_label = QLabel(self.execution_tab)
        self.execution_error_label.setProperty("role", "error")
        self.execution_error_label.setWordWrap(True)
        self.execution_status_label = QLabel(self.execution_tab)
        self.execution_status_label.setProperty("role", "secondary")
        self.save_execution_button = QPushButton(self.execution_tab)
        set_button_role(self.save_execution_button, "primary")
        execution_footer.addWidget(self.execution_error_label)
        execution_footer.addStretch(1)
        execution_footer.addWidget(self.execution_status_label)
        execution_footer.addWidget(self.save_execution_button)
        layout.addLayout(execution_footer)
        self.save_execution_button.clicked.connect(self._save_execution_mode)

    def refresh(self) -> None:
        self.refresh_constants()
        self.refresh_modules()
        self.refresh_execution()

    @Slot()
    def refresh_constants(self) -> None:
        selected = self._editing_constant_name
        try:
            constants = self.templates.constants()
        except Exception as exc:
            self._set_constant_error(str(exc))
            return
        self.constants_table.setRowCount(0)
        for row, (name, value) in enumerate(
            sorted(constants.items(), key=lambda item: item[0].casefold())
        ):
            self.constants_table.insertRow(row)
            name_item = QTableWidgetItem(name)
            value_item = QTableWidgetItem(str(value))
            name_item.setToolTip(name)
            value_item.setToolTip(str(value))
            self.constants_table.setItem(row, 0, name_item)
            self.constants_table.setItem(row, 1, value_item)
            if name == selected:
                self.constants_table.selectRow(row)
        self.constant_empty_label.setVisible(not constants)
        self._set_constant_error("")
        self._filter_constants(self.constant_search.text())

    @Slot()
    def _save_constant(self) -> None:
        try:
            self.templates.set_constant(
                self.constant_name.text(),
                self.constant_value.text(),
                old_name=self._editing_constant_name,
            )
        except Exception as exc:
            self._set_constant_error(str(exc))
            return
        self._reset_constant_form()
        self.refresh_constants()

    @Slot(int, int)
    def _edit_constant_row(self, row: int, _column: int) -> None:
        name_item = self.constants_table.item(row, 0)
        value_item = self.constants_table.item(row, 1)
        if name_item is None or value_item is None:
            return
        self._editing_constant_name = name_item.text()
        self.constant_name.setText(name_item.text())
        self.constant_name.setReadOnly(True)
        self.constant_value.setText(value_item.text())
        self._set_constant_error("")
        self.retranslate_ui()

    @Slot()
    def _reset_constant_form(self) -> None:
        self._editing_constant_name = None
        self.constant_name.setReadOnly(False)
        self.constant_name.clear()
        self.constant_value.clear()
        self._set_constant_error("")
        self.retranslate_ui()

    @Slot()
    def _delete_selected_constant(self) -> None:
        row = self.constants_table.currentRow()
        item = self.constants_table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        try:
            self.templates.delete_constant(item.text())
        except Exception as exc:
            self._set_constant_error(str(exc))
            return
        self._reset_constant_form()
        self.refresh_constants()

    @Slot(str)
    def _filter_constants(self, query: str) -> None:
        normalized = str(query).strip().casefold()
        for row in range(self.constants_table.rowCount()):
            text = " ".join(
                (
                    self.constants_table.item(row, column).text()
                    if self.constants_table.item(row, column) is not None
                    else ""
                )
                for column in range(2)
            ).casefold()
            self.constants_table.setRowHidden(
                row,
                bool(normalized and normalized not in text),
            )

    def _set_constant_error(self, message: str) -> None:
        text = str(message).strip()
        self.constant_error_label.setText(text)
        self.constant_error_label.setVisible(bool(text))

    def refresh_modules(self, preferred_id: str | None = None) -> None:
        selected_id = preferred_id or self._editing_module_id
        snapshot = self.templates.modules()
        self.module_list.blockSignals(True)
        self.module_list.clear()
        selected_row = -1
        for row, record in enumerate(snapshot.records):
            text = (
                record.module.name
                if record.module.label == record.module.name
                else f"{record.module.name} — {record.module.label}"
            )
            item = QListWidgetItem(text, self.module_list)
            item.setData(self.MODULE_ID_ROLE, record.module_id)
            item.setToolTip(text)
            if record.module_id == selected_id:
                selected_row = row
        self.module_list.blockSignals(False)
        self.module_empty_label.setVisible(not snapshot.records)
        error = "\n".join(item.message for item in snapshot.errors)
        self._set_module_error(error)
        if snapshot.records:
            self.module_list.setCurrentRow(
                selected_row if selected_row >= 0 else 0
            )
            self._module_row_changed(self.module_list.currentRow())
        else:
            self.new_module_template()
        self._update_module_actions()

    @Slot(int)
    def _module_row_changed(self, row: int) -> None:
        item = self.module_list.item(row) if row >= 0 else None
        if item is None:
            return
        module_id = item.data(self.MODULE_ID_ROLE)
        try:
            record = self.templates.module(module_id)
        except Exception as exc:
            self._set_module_error(str(exc))
            return
        self._editing_module_id = record.module_id
        self.module_editor.load_module(record.module)
        self._update_module_actions()

    @Slot()
    def new_module_template(self) -> None:
        self._editing_module_id = None
        self.module_list.clearSelection()
        self.module_editor.clear()
        self._set_module_error("")
        self._update_module_actions()

    @Slot(object)
    def _save_module_template(self, module: object) -> None:
        if not isinstance(module, QCModule):
            self._set_module_error("Invalid QC module candidate")
            return
        try:
            if self._editing_module_id is None:
                snapshot = self.templates.modules()
                if snapshot.errors:
                    raise ValueError(
                        "; ".join(item.message for item in snapshot.errors)
                    )
                display_order = (
                    max(
                        (
                            record.display_order
                            for record in snapshot.records
                        ),
                        default=0,
                    )
                    + 10
                )
                saved = self.templates.add_module(
                    module,
                    display_order=display_order,
                )
            else:
                current = self.templates.module(self._editing_module_id)
                saved = self.templates.save_module(
                    ModuleRecord.create(
                        module,
                        scope="template",
                        display_order=current.display_order,
                        module_id=current.module_id,
                    )
                )
        except Exception as exc:
            self._set_module_error(str(exc))
            self.module_editor.set_error(str(exc))
            return
        self._editing_module_id = saved.module_id
        self._set_module_error("")
        self.module_editor.set_error("")
        self.refresh_modules(saved.module_id)

    def delete_module_template(self, module_id: str) -> bool:
        try:
            deleted = self.templates.delete_module(module_id)
        except Exception as exc:
            self._set_module_error(str(exc))
            return False
        if self._editing_module_id == module_id:
            self._editing_module_id = None
        self._set_module_error("")
        self.refresh_modules()
        return deleted

    @Slot()
    def _confirm_delete_selected_module(self) -> None:
        if self._editing_module_id is None:
            return
        answer = QMessageBox.question(
            self,
            self.language.tr("cross.delete_module"),
            self.language.tr("cross.delete_module_confirm"),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.delete_module_template(self._editing_module_id)

    @Slot()
    def _choose_module_import(self) -> None:
        path, _selected = QFileDialog.getOpenFileName(
            self,
            self.language.tr("cross.import_module"),
            "",
            self.language.tr("cross.json_files"),
        )
        if not path:
            return
        try:
            saved = self.templates.import_module(path)
        except Exception as exc:
            self._set_module_error(str(exc))
            return
        self._set_module_error("")
        self.refresh_modules(saved.module_id)

    @Slot()
    def _choose_module_export(self) -> None:
        if self._editing_module_id is None:
            return
        record = self.templates.module(self._editing_module_id)
        path, _selected = QFileDialog.getSaveFileName(
            self,
            self.language.tr("cross.export_module"),
            f"qcmodule_{record.module.name}.json",
            self.language.tr("cross.json_files"),
        )
        if not path:
            return
        try:
            self.templates.export_module(record.module_id, path)
        except Exception as exc:
            self._set_module_error(str(exc))
            return
        self._set_module_error("")

    def _set_module_error(self, message: str) -> None:
        text = str(message).strip()
        self.module_error_label.setText(text)
        self.module_error_label.setVisible(bool(text))

    def _update_module_actions(self) -> None:
        selected = self._editing_module_id is not None
        self.export_module_button.setEnabled(selected)
        self.delete_module_button.setEnabled(selected)

    def refresh_execution(self) -> None:
        try:
            enabled = self.templates.shell_enabled()
        except Exception as exc:
            self._set_execution_error(str(exc))
            return
        self.shell_radio.setChecked(enabled)
        self.direct_radio.setChecked(not enabled)
        self.executor.set_shell_enabled(enabled)
        self._set_execution_error("")

    @Slot()
    def _save_execution_mode(self) -> None:
        enabled = self.shell_radio.isChecked()
        try:
            self.templates.set_shell_enabled(enabled)
        except Exception as exc:
            self._set_execution_error(str(exc))
            return
        self.executor.set_shell_enabled(enabled)
        self._execution_saved = True
        self._set_execution_error("")
        self.retranslate_ui()

    def _set_execution_error(self, message: str) -> None:
        text = str(message).strip()
        self.execution_error_label.setText(text)
        self.execution_error_label.setVisible(bool(text))

    @Slot()
    def retranslate_ui(self) -> None:
        tr = self.language.tr
        self.tabs.setTabText(0, tr("cross.constants_tab"))
        self.tabs.setTabText(1, tr("cross.modules_tab"))
        self.tabs.setTabText(2, tr("cross.execution_tab"))
        self.constant_name.setPlaceholderText(tr("cross.constant_name"))
        self.constant_value.setPlaceholderText(tr("cross.constant_value"))
        self.cancel_constant_button.setText(tr("cross.cancel"))
        self.cancel_constant_button.setVisible(
            self._editing_constant_name is not None
        )
        self.save_constant_button.setText(
            tr("cross.save_changes")
            if self._editing_constant_name is not None
            else tr("cross.add_constant")
        )
        self.constant_search.setPlaceholderText(tr("cross.search_constants"))
        self.refresh_constants_button.setText(tr("cross.refresh"))
        self.constants_table.setHorizontalHeaderLabels(
            [tr("cross.name"), tr("cross.value")]
        )
        self.constant_empty_label.setText(tr("cross.no_constants"))
        self.delete_constant_button.setText(tr("cross.delete_constant"))
        self.module_list_title.setText(tr("cross.module_list"))
        self.new_module_button.setText(tr("cross.new_module"))
        self.import_module_button.setText(tr("cross.import_module"))
        self.export_module_button.setText(tr("cross.export_module"))
        self.delete_module_button.setText(tr("cross.delete_module"))
        self.module_empty_label.setText(tr("cross.no_modules"))
        self.module_editor.retranslate_ui()
        self.execution_intro.setText(tr("settings.viewer_intro"))
        self.execution_group.setTitle(tr("settings.viewer_group"))
        self.direct_radio.setText(tr("settings.direct_title"))
        self.direct_detail.setText(tr("settings.direct_detail"))
        self.shell_radio.setText(tr("settings.shell_title"))
        self.shell_detail.setText(tr("settings.shell_detail"))
        self.execution_gate.setText(tr("settings.no_gate"))
        self.save_execution_button.setText(tr("cross.save_execution"))
        self.execution_status_label.setText(
            tr("cross.execution_saved") if self._execution_saved else ""
        )
        self.execution_status_label.setVisible(self._execution_saved)
        self.setAccessibleName(tr("cross.page_accessible"))


__all__ = ["QtCrossProjectSettingsPage"]
