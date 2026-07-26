"""Qt preview-and-copy dialogs for project-owned template candidates."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.configuration_service import ConfigurationService
from core.project_template_service import ProjectTemplateService
from core.template_service import TemplateService
from gui_qt.i18n import LanguageController
from gui_qt.module_template_editor import QtModuleTemplateEditor
from gui_qt.theme import CONTENT_MARGIN, CONTROL_SPACING, SECTION_SPACING


class ConstantTemplateCopyDialog(QDialog):
    """Preview/edit one constant template and copy it into the project."""

    def __init__(
        self,
        templates: TemplateService,
        copier: ProjectTemplateService,
        configuration: ConfigurationService,
        language: LanguageController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(templates, TemplateService):
            raise TypeError("constant copy dialog requires TemplateService")
        if not isinstance(copier, ProjectTemplateService):
            raise TypeError(
                "constant copy dialog requires ProjectTemplateService"
            )
        if not isinstance(configuration, ConfigurationService):
            raise TypeError(
                "constant copy dialog requires ConfigurationService"
            )
        if not isinstance(language, LanguageController):
            raise TypeError("constant copy dialog requires LanguageController")
        self.templates = templates
        self.copier = copier
        self.configuration = configuration
        self.language = language
        self.copied_name: str | None = None
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CONTENT_MARGIN,
            CONTENT_MARGIN,
            CONTENT_MARGIN,
            CONTENT_MARGIN,
        )
        layout.setSpacing(SECTION_SPACING)
        form = QFormLayout()
        form.setVerticalSpacing(CONTROL_SPACING)
        self.source_label = QLabel(self)
        self.name_label = QLabel(self)
        self.value_label = QLabel(self)
        self.template_combo = QComboBox(self)
        self.template_combo.setMaxVisibleItems(8)
        self.candidate_name = QLineEdit(self)
        self.candidate_value = QLineEdit(self)
        form.addRow(self.source_label, self.template_combo)
        form.addRow(self.name_label, self.candidate_name)
        form.addRow(self.value_label, self.candidate_value)
        layout.addLayout(form)

        self.empty_label = QLabel(self)
        self.empty_label.setProperty("role", "secondary")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        self.error_label = QLabel(self)
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            Qt.Horizontal,
            self,
        )
        self.copy_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.cancel_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(self.button_box)
        self.template_combo.currentIndexChanged.connect(
            self._load_selected_template
        )
        self.button_box.accepted.connect(self._copy)
        self.button_box.rejected.connect(self.reject)
        self.language.languageChanged.connect(self.retranslate_ui)
        self._load_templates()
        self.retranslate_ui()

    def _load_templates(self) -> None:
        try:
            constants = self.templates.constants()
        except Exception as exc:
            self._set_error(str(exc))
            self.copy_button.setEnabled(False)
            return
        self.template_combo.clear()
        for name in sorted(constants, key=str.casefold):
            self.template_combo.addItem(name, name)
        available = self.template_combo.count() > 0
        self.empty_label.setVisible(not available)
        self.copy_button.setEnabled(available)
        if available:
            self._load_selected_template(0)

    @Slot(int)
    def _load_selected_template(self, index: int) -> None:
        if index < 0:
            return
        name = self.template_combo.itemData(index)
        try:
            value = self.templates.constant(name)
        except Exception as exc:
            self._set_error(str(exc))
            return
        self.candidate_name.setText(name)
        self.candidate_value.setText(str(value))
        self._set_error("")

    @Slot()
    def _copy(self) -> None:
        template_name = self.template_combo.currentData()
        if not template_name:
            return
        try:
            self.copied_name = self.copier.copy_constant(
                template_name,
                self.configuration,
                candidate_name=self.candidate_name.text(),
                candidate_value=self.candidate_value.text(),
            )
        except Exception as exc:
            self._set_error(str(exc))
            return
        self.accept()

    def _set_error(self, message: str) -> None:
        text = str(message).strip()
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    @Slot()
    def retranslate_ui(self) -> None:
        tr = self.language.tr
        self.setWindowTitle(tr("cross.copy_constant_title"))
        self.source_label.setText(tr("cross.template_source"))
        self.name_label.setText(tr("cross.name"))
        self.value_label.setText(tr("cross.value"))
        self.empty_label.setText(tr("cross.no_constants"))
        self.copy_button.setText(tr("cross.copy_to_project"))
        self.cancel_button.setText(tr("cross.cancel"))


class ModuleTemplateCopyDialog(QDialog):
    """Preview/edit one module template and copy it into the project."""

    def __init__(
        self,
        templates: TemplateService,
        copier: ProjectTemplateService,
        configuration: ConfigurationService,
        language: LanguageController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(templates, TemplateService):
            raise TypeError("module copy dialog requires TemplateService")
        if not isinstance(copier, ProjectTemplateService):
            raise TypeError("module copy dialog requires ProjectTemplateService")
        if not isinstance(configuration, ConfigurationService):
            raise TypeError("module copy dialog requires ConfigurationService")
        if not isinstance(language, LanguageController):
            raise TypeError("module copy dialog requires LanguageController")
        self.templates = templates
        self.copier = copier
        self.configuration = configuration
        self.language = language
        self.copied_name: str | None = None
        self.setModal(True)
        self.resize(820, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CONTENT_MARGIN,
            CONTENT_MARGIN,
            CONTENT_MARGIN,
            CONTENT_MARGIN,
        )
        layout.setSpacing(SECTION_SPACING)
        self.source_label = QLabel(self)
        self.source_combo = QComboBox(self)
        self.source_combo.setMaxVisibleItems(8)
        source_row = QFormLayout()
        source_row.addRow(self.source_label, self.source_combo)
        layout.addLayout(source_row)

        self.editor = QtModuleTemplateEditor(language, self)
        self.editor.save_button.hide()
        editor_scroll = QScrollArea(self)
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QScrollArea.NoFrame)
        editor_scroll.setWidget(self.editor)
        layout.addWidget(editor_scroll, 1)

        self.empty_label = QLabel(self)
        self.empty_label.setProperty("role", "secondary")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        self.error_label = QLabel(self)
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            Qt.Horizontal,
            self,
        )
        self.copy_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.cancel_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(self.button_box)
        self.source_combo.currentIndexChanged.connect(
            self._load_selected_template
        )
        self.button_box.accepted.connect(self._copy)
        self.button_box.rejected.connect(self.reject)
        self.language.languageChanged.connect(self.retranslate_ui)
        self._load_templates()
        self.retranslate_ui()

    def _load_templates(self) -> None:
        snapshot = self.templates.modules()
        self.source_combo.clear()
        for record in snapshot.records:
            text = (
                record.module.name
                if record.module.label == record.module.name
                else f"{record.module.name} — {record.module.label}"
            )
            self.source_combo.addItem(text, record.module_id)
        available = self.source_combo.count() > 0
        self.empty_label.setVisible(not available)
        self.copy_button.setEnabled(available)
        if snapshot.errors:
            self._set_error(
                "\n".join(item.message for item in snapshot.errors)
            )
        if available:
            self._load_selected_template(0)

    @Slot(int)
    def _load_selected_template(self, index: int) -> None:
        if index < 0:
            return
        try:
            record = self.templates.module(
                self.source_combo.itemData(index)
            )
        except Exception as exc:
            self._set_error(str(exc))
            return
        self.editor.load_module(record.module)
        if not self.templates.modules().errors:
            self._set_error("")

    @Slot()
    def _copy(self) -> None:
        template_id = self.source_combo.currentData()
        if not template_id:
            return
        try:
            candidate = self.editor.candidate()
            self.copied_name = self.copier.copy_module_to_project(
                template_id,
                self.configuration,
                candidate=candidate,
            )
        except Exception as exc:
            self._set_error(str(exc))
            self.editor.set_error(str(exc))
            return
        self.accept()

    def _set_error(self, message: str) -> None:
        text = str(message).strip()
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    @Slot()
    def retranslate_ui(self) -> None:
        tr = self.language.tr
        self.setWindowTitle(tr("cross.copy_module_title"))
        self.source_label.setText(tr("cross.template_source"))
        self.empty_label.setText(tr("cross.no_modules"))
        self.copy_button.setText(tr("cross.copy_to_project"))
        self.cancel_button.setText(tr("cross.cancel"))
        self.editor.retranslate_ui()


__all__ = [
    "ConstantTemplateCopyDialog",
    "ModuleTemplateCopyDialog",
]
