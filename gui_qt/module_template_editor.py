"""Focused Qt editor for one detached QC-module template candidate."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_qt.i18n import LanguageController
from gui_qt.module_tag_editor import (
    ModuleTagEditor,
    normalize_stored_tag_labels,
    sync_score_table_height,
)
from gui_qt.theme import (
    CONTROL_SPACING,
    SECTION_SPACING,
    set_button_role,
)
from models.qcmodule import QCModule, Score, Tag


class QtModuleTemplateEditor(QWidget):
    """Edit one module candidate without reading or writing persistence."""

    saveRequested = Signal(object)

    def __init__(
        self,
        language: LanguageController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(language, LanguageController):
            raise TypeError("QtModuleTemplateEditor requires LanguageController")
        self.language = language
        self._baseline = QCModule(name="", label="")
        self.setObjectName("moduleTemplateEditor")
        self.setProperty("surface", "true")
        self._build_ui()
        self.clear()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(SECTION_SPACING)

        self.editor_title = QLabel(self)
        self.editor_title.setProperty("role", "sectionTitle")
        layout.addWidget(self.editor_title)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(SECTION_SPACING)
        form.setVerticalSpacing(CONTROL_SPACING)
        self.name_label = QLabel(self)
        self.label_label = QLabel(self)
        self.rater_label = QLabel(self)
        self.module_name = QLineEdit(self)
        self.module_label = QLineEdit(self)
        self.module_rater = QLineEdit(self)
        form.addRow(self.name_label, self.module_name)
        form.addRow(self.label_label, self.module_label)
        form.addRow(self.rater_label, self.module_rater)
        layout.addLayout(form)

        self.scores_group = QGroupBox(self)
        scores_layout = QVBoxLayout(self.scores_group)
        scores_layout.setSpacing(CONTROL_SPACING)
        self.score_table = QTableWidget(0, 2, self.scores_group)
        self.score_table.horizontalHeader().setStretchLastSection(True)
        self.score_table.verticalHeader().hide()
        self.score_table.setAlternatingRowColors(True)
        scores_layout.addWidget(self.score_table)
        score_actions = QHBoxLayout()
        self.add_score_button = QPushButton(self.scores_group)
        self.remove_score_button = QPushButton(self.scores_group)
        score_actions.addWidget(self.add_score_button)
        score_actions.addWidget(self.remove_score_button)
        score_actions.addStretch(1)
        scores_layout.addLayout(score_actions)
        layout.addWidget(self.scores_group)

        self.tags_group = QGroupBox(self)
        tags_layout = QVBoxLayout(self.tags_group)
        tags_layout.setSpacing(CONTROL_SPACING)
        self.tag_editor = ModuleTagEditor(self.language, self.tags_group)
        tags_layout.addWidget(self.tag_editor)
        layout.addWidget(self.tags_group)

        self.viewer_group = QGroupBox(self)
        viewer_layout = QVBoxLayout(self.viewer_group)
        viewer_layout.setSpacing(CONTROL_SPACING)
        self.module_code = QPlainTextEdit(self.viewer_group)
        self.module_code.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self.module_code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.module_code.setMinimumHeight(
            self.module_code.fontMetrics().lineSpacing() * 6
        )
        self.module_control = QCheckBox(self.viewer_group)
        viewer_layout.addWidget(self.module_code)
        viewer_layout.addWidget(self.module_control)
        layout.addWidget(self.viewer_group)

        self.error_label = QLabel(self)
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.discard_button = QPushButton(self)
        self.save_button = QPushButton(self)
        set_button_role(self.save_button, "primary")
        actions.addWidget(self.discard_button)
        actions.addWidget(self.save_button)
        layout.addLayout(actions)

        self.add_score_button.clicked.connect(
            lambda: self._append_score_row(("Quality", "Poor,Fair,Good"))
        )
        self.remove_score_button.clicked.connect(self._remove_current_score)
        self.discard_button.clicked.connect(self.discard)
        self.save_button.clicked.connect(self._request_save)

    @staticmethod
    def _append_row(table: QTableWidget, values: tuple[str, ...]) -> None:
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(value))
        table.setCurrentCell(row, 0)

    @staticmethod
    def _remove_current_row(table: QTableWidget) -> None:
        row = table.currentRow()
        if row >= 0:
            table.removeRow(row)

    def _append_score_row(self, values: tuple[str, str]) -> None:
        """Append one editable score row and resynchronize native height."""

        self._append_row(self.score_table, values)
        sync_score_table_height(self.score_table)

    def _remove_current_score(self) -> None:
        """Remove the selected score row and resynchronize native height."""

        self._remove_current_row(self.score_table)
        sync_score_table_height(self.score_table)

    def clear(self) -> None:
        """Install one blank baseline for creating a template."""

        self.load_module(QCModule(name="", label=""))
        self._append_score_row(("Quality", "Poor,Fair,Good"))

    def load_module(self, module: QCModule) -> None:
        """Load one detached baseline; hidden fields are retained on save."""

        if not isinstance(module, QCModule):
            raise TypeError("module template editor requires QCModule")
        self._baseline = deepcopy(module)
        self.module_name.setText(module.name)
        self.module_label.setText(module.label)
        self.module_rater.setText(module.rater or "")
        self.module_code.setPlainText(module.code or "")
        self.module_control.setChecked(bool(module.control))
        self.score_table.setRowCount(0)
        for score in module.scores.values():
            self._append_row(
                self.score_table,
                (score.label or "", score.num_ or score.num or ""),
            )
        if self.score_table.rowCount():
            self.score_table.setCurrentCell(0, 0)
            self.score_table.scrollToTop()
        sync_score_table_height(self.score_table)
        self.tag_editor.set_tags(
            normalize_stored_tag_labels(
                tag.label for tag in module.tags.values()
            )
        )
        self.set_error("")

    def candidate(self) -> QCModule:
        """Return one detached, configuration-only module candidate."""

        name = self.module_name.text().strip()
        if not name:
            raise ValueError(self.language.tr("cross.module_name_required"))
        module = deepcopy(self._baseline)
        module.name = name
        module.label = self.module_label.text().strip() or name
        module.rater = self.module_rater.text().strip() or None
        module.code = self.module_code.toPlainText().strip() or None
        module.control = self.module_control.isChecked()
        module.scores = {}
        for row in range(self.score_table.rowCount()):
            label_item = self.score_table.item(row, 0)
            values_item = self.score_table.item(row, 1)
            label = label_item.text().strip() if label_item is not None else ""
            values = values_item.text().strip() if values_item is not None else ""
            if not label and not values:
                continue
            key = str(len(module.scores) + 1)
            module.scores[key] = Score(key, label, values, values)
        module.tags = {}
        for label in self.tag_editor.tags():
            key = str(len(module.tags) + 1)
            module.tags[key] = Tag(key, label.strip())
        module.easyqcid = None
        module.code_exe = None
        module.notes = None
        module.time = None
        for score in module.scores.values():
            score.value = None
        for tag in module.tags.values():
            tag.value = False
        return module

    @Slot()
    def discard(self) -> None:
        self.load_module(self._baseline)
        if not self._baseline.name and not self.score_table.rowCount():
            self._append_score_row(("Quality", "Poor,Fair,Good"))

    @Slot()
    def _request_save(self) -> None:
        try:
            candidate = self.candidate()
        except Exception as exc:
            self.set_error(str(exc))
            return
        self.saveRequested.emit(candidate)

    def set_error(self, message: str) -> None:
        text = str(message).strip()
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    @Slot()
    def retranslate_ui(self) -> None:
        tr = self.language.tr
        self.editor_title.setText(tr("cross.module_editor"))
        self.name_label.setText(tr("cross.name"))
        self.label_label.setText(tr("cross.label"))
        self.rater_label.setText(tr("cross.rater"))
        self.module_name.setPlaceholderText(tr("cross.module_name_placeholder"))
        self.module_label.setPlaceholderText(tr("cross.module_label_placeholder"))
        self.module_rater.setPlaceholderText(tr("cross.module_rater_placeholder"))
        self.scores_group.setTitle(tr("cross.scores"))
        self.score_table.setHorizontalHeaderLabels(
            [tr("cross.score_label"), tr("cross.score_values")]
        )
        self.add_score_button.setText(tr("cross.add_score"))
        self.remove_score_button.setText(tr("cross.remove_score"))
        self.tags_group.setTitle(tr("cross.tags"))
        self.tag_editor.retranslate_ui()
        self.viewer_group.setTitle(tr("cross.viewer"))
        self.module_code.setPlaceholderText(tr("cross.viewer_placeholder"))
        self.module_control.setText(tr("cross.viewer_control"))
        self.discard_button.setText(tr("cross.discard"))
        self.save_button.setText(tr("cross.save_module"))
        self.setAccessibleName(tr("cross.module_editor_accessible"))
        sync_score_table_height(self.score_table)


__all__ = ["QtModuleTemplateEditor"]
