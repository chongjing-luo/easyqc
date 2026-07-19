"""Qt adapter for one toolkit-neutral interactive QC session."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.qc_workflow_service import QcWorkflowService


class QtQcWorkspace(QWidget):
    """Render and edit one QC draft; Core owns all process and file actions."""

    draftStateChanged = Signal(bool)

    def __init__(self, workflow: QcWorkflowService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not isinstance(workflow, QcWorkflowService):
            raise TypeError("QtQcWorkspace requires QcWorkflowService")
        self.workflow = workflow
        self._loading = False
        self._score_groups: dict[str, QButtonGroup] = {}
        self.score_buttons: dict[str, dict[str | None, QRadioButton]] = {}
        self._legacy_score_buttons: dict[str, QRadioButton] = {}
        self.tag_boxes: dict[str, QCheckBox] = {}
        self.setObjectName("qtQcWorkspace")
        self.setAccessibleName("EasyQC rating workspace")
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header = QFrame(self)
        header.setObjectName("qcHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        title_column = QVBoxLayout()
        self.module_label = QLabel("", header)
        self.module_label.setObjectName("qcTitle")
        self.subject_label = QLabel("", header)
        self.subject_label.setObjectName("qcSubject")
        title_column.addWidget(self.module_label)
        title_column.addWidget(self.subject_label)
        header_layout.addLayout(title_column)
        header_layout.addStretch(1)
        self.watch_badge = QLabel("", header)
        self.watch_badge.setObjectName("watchBadge")
        self.watch_badge.setWordWrap(True)
        self.watch_badge.setMaximumWidth(420)
        header_layout.addWidget(self.watch_badge)
        self.viewer_button = QPushButton("Open viewer", header)
        self.viewer_button.setObjectName("primaryAction")
        self.viewer_button.setAccessibleName("Open external QC viewer")
        header_layout.addWidget(self.viewer_button)
        layout.addWidget(header)

        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setObjectName("qcSplitter")
        self.subject_list = QListWidget(splitter)
        self.subject_list.setObjectName("qcSubjectList")
        self.subject_list.setAccessibleName("QC subject sequence")
        self.subject_list.setMinimumWidth(190)
        self.subject_list.setMaximumWidth(300)

        editor_scroll = QScrollArea(splitter)
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QFrame.NoFrame)
        editor = QWidget(editor_scroll)
        editor.setObjectName("qcEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(14, 8, 14, 14)
        editor_layout.setSpacing(12)

        module = self.workflow.current_module
        for key, score in module.scores.items():
            group_box = QGroupBox(score.label, editor)
            group_box.setObjectName("scoreGroup")
            group_layout = QHBoxLayout(group_box)
            button_group = QButtonGroup(group_box)
            button_group.setExclusive(True)
            self._score_groups[key] = button_group
            self.score_buttons[key] = {}
            for value, label in [(None, "Not rated")] + [
                (value, value) for value in score.allowed_values
            ]:
                button = QRadioButton(label, group_box)
                button.setAccessibleName(f"{score.label}: {label}")
                button_group.addButton(button)
                self.score_buttons[key][value] = button
                group_layout.addWidget(button)
                button.toggled.connect(
                    lambda checked, score_key=key, score_value=value: self._score_changed(
                        score_key, score_value, checked
                    )
                )
            legacy_button = QRadioButton("", group_box)
            legacy_button.setEnabled(False)
            legacy_button.hide()
            button_group.addButton(legacy_button)
            self._legacy_score_buttons[key] = legacy_button
            group_layout.addWidget(legacy_button)
            group_layout.addStretch(1)
            editor_layout.addWidget(group_box)

        if module.tags:
            tags_box = QGroupBox("Tags", editor)
            tags_box.setObjectName("tagGroup")
            tags_layout = QHBoxLayout(tags_box)
            for key, tag in module.tags.items():
                checkbox = QCheckBox(tag.label, tags_box)
                checkbox.setAccessibleName(f"QC tag: {tag.label}")
                checkbox.toggled.connect(
                    lambda checked, tag_key=key: self._tag_changed(tag_key, checked)
                )
                self.tag_boxes[key] = checkbox
                tags_layout.addWidget(checkbox)
            tags_layout.addStretch(1)
            editor_layout.addWidget(tags_box)

        notes_box = QGroupBox("Notes", editor)
        notes_box.setObjectName("notesGroup")
        notes_layout = QVBoxLayout(notes_box)
        self.notes_edit = QTextEdit(notes_box)
        self.notes_edit.setObjectName("qcNotes")
        self.notes_edit.setAccessibleName("QC notes draft")
        self.notes_edit.setPlaceholderText("Add concise review notes…")
        self.notes_edit.setMinimumHeight(130)
        notes_layout.addWidget(self.notes_edit)
        editor_layout.addWidget(notes_box)
        editor_layout.addStretch(1)
        editor_scroll.setWidget(editor)

        splitter.addWidget(self.subject_list)
        splitter.addWidget(editor_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 760])
        layout.addWidget(splitter, 1)

        footer = QFrame(self)
        footer.setObjectName("qcFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 8, 12, 8)
        self.previous_button = QPushButton("Previous", footer)
        self.next_button = QPushButton("Next", footer)
        self.discard_button = QPushButton("Discard changes", footer)
        self.save_button = QPushButton("Save", footer)
        self.save_next_button = QPushButton("Save && Next", footer)
        self.save_next_button.setObjectName("primaryAction")
        footer_layout.addWidget(self.previous_button)
        footer_layout.addWidget(self.next_button)
        footer_layout.addStretch(1)
        self.dirty_label = QLabel("", footer)
        self.dirty_label.setObjectName("qcDirtyState")
        footer_layout.addWidget(self.dirty_label)
        footer_layout.addWidget(self.discard_button)
        footer_layout.addWidget(self.save_button)
        footer_layout.addWidget(self.save_next_button)
        layout.addWidget(footer)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("qcError")
        self.error_label.setAccessibleName("QC action error")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        self.viewer_button.clicked.connect(self._launch_viewer)
        self.previous_button.clicked.connect(lambda: self._navigate_delta(-1))
        self.next_button.clicked.connect(lambda: self._navigate_delta(1))
        self.discard_button.clicked.connect(self._discard_changes)
        self.save_button.clicked.connect(self._save)
        self.save_next_button.clicked.connect(self._save_next)
        self.subject_list.currentRowChanged.connect(self._subject_row_changed)
        self.notes_edit.textChanged.connect(self._notes_changed)

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def _refresh(self) -> None:
        self._loading = True
        try:
            module = self.workflow.current_module
            self.module_label.setText(
                f"{module.label}  ·  {module.name}  ·  {module.rater or 'no rater'}"
            )
            self.subject_label.setText(
                f"{self.workflow.current_ezqcid}  ·  "
                f"{self.workflow.current_index + 1} of {len(self.workflow.subject_ids)}"
            )
            self.subject_list.blockSignals(True)
            self.subject_list.clear()
            for position, identity in enumerate(self.workflow.subject_ids, start=1):
                self.subject_list.addItem(f"{position:>4}   {identity}")
            self.subject_list.setCurrentRow(self.workflow.current_index)
            self.subject_list.blockSignals(False)

            for key, score in module.scores.items():
                legacy_button = self._legacy_score_buttons[key]
                button = self.score_buttons[key].get(score.value)
                if score.value is None:
                    legacy_button.hide()
                    self.score_buttons[key][None].setChecked(True)
                elif button is not None:
                    legacy_button.hide()
                    button.setChecked(True)
                else:
                    legacy_button.setText(
                        f"{score.value} (saved legacy value; not in current schema)"
                    )
                    legacy_button.setAccessibleName(
                        f"{score.label}: {score.value} saved legacy value; "
                        "not in current schema"
                    )
                    legacy_button.setEnabled(False)
                    legacy_button.show()
                    legacy_button.setChecked(True)
            for key, tag in module.tags.items():
                self.tag_boxes[key].setChecked(tag.value)
            self.notes_edit.setPlainText(module.notes or "")

            read_only = self.workflow.watch_mode
            reason = self.workflow.read_only_reason
            self.watch_badge.setText(
                f"Read-only · {reason}" if read_only else "Rating mode"
            )
            self.watch_badge.setProperty("readOnly", read_only)
            self.watch_badge.style().unpolish(self.watch_badge)
            self.watch_badge.style().polish(self.watch_badge)
            for buttons in self.score_buttons.values():
                for button in buttons.values():
                    button.setEnabled(not read_only)
            for checkbox in self.tag_boxes.values():
                checkbox.setEnabled(not read_only)
            self.notes_edit.setReadOnly(read_only)
            self.save_button.setEnabled(not read_only)
            self.save_next_button.setEnabled(
                not read_only and self.workflow.current_index < len(self.workflow.subject_ids) - 1
            )
            self.previous_button.setEnabled(self.workflow.current_index > 0)
            self.next_button.setEnabled(
                self.workflow.current_index < len(self.workflow.subject_ids) - 1
            )
            self.viewer_button.setEnabled(bool((module.code or "").strip()))
            self.dirty_label.setText("Unsaved changes" if self.workflow.dirty else "Saved state")
            self.discard_button.setEnabled(self.workflow.dirty)
        finally:
            self._loading = False
        self.draftStateChanged.emit(self.workflow.dirty)

    def _score_changed(self, key: str, value: str | None, checked: bool) -> None:
        if self._loading or not checked:
            return
        try:
            self.workflow.set_score(key, value)
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self.dirty_label.setText("Unsaved changes")
        self.discard_button.setEnabled(True)
        self.draftStateChanged.emit(True)

    def _tag_changed(self, key: str, checked: bool) -> None:
        if self._loading:
            return
        try:
            self.workflow.set_tag(key, checked)
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self.dirty_label.setText("Unsaved changes")
        self.discard_button.setEnabled(True)
        self.draftStateChanged.emit(True)

    def _notes_changed(self) -> None:
        if self._loading:
            return
        try:
            self.workflow.set_notes(self.notes_edit.toPlainText())
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self.dirty_label.setText("Unsaved changes")
        self.discard_button.setEnabled(True)
        self.draftStateChanged.emit(True)

    def _discard_changes(self) -> None:
        try:
            self.workflow.discard_changes()
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._set_error("")
        self._refresh()

    def _launch_viewer(self) -> None:
        try:
            processes = self.workflow.launch_viewer()
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._set_error("")
        self.viewer_button.setText(f"Viewer open ({len(processes)})")

    def _save(self) -> None:
        try:
            path = self.workflow.save()
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._set_error("")
        self.dirty_label.setText(f"Saved · {path.name}")
        self._refresh()

    def _save_next(self) -> None:
        try:
            self.workflow.save_and_move(1)
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._set_error("")
        self.viewer_button.setText("Open viewer")
        self._refresh()

    def _navigate_delta(self, delta: int) -> None:
        try:
            self.workflow.move(delta)
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self.viewer_button.setText("Open viewer")
        self._refresh()

    def _subject_row_changed(self, row: int) -> None:
        if self._loading or row < 0:
            return
        try:
            self.workflow.navigate_to(row)
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self._refresh()

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def closeEvent(self, event) -> None:  # noqa: N802
        self.workflow.close()
        super().closeEvent(event)


__all__ = ["QtQcWorkspace"]
