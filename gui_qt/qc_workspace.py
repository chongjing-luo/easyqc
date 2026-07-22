"""Qt adapter for one toolkit-neutral interactive QC session."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTextEdit,
    QToolBar,
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
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(6)
        title_row = QHBoxLayout()
        title_column = QVBoxLayout()
        self.module_label = QLabel("", header)
        self.module_label.setObjectName("qcTitle")
        self.module_label.setWordWrap(True)
        self.module_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.subject_label = QLabel("", header)
        self.subject_label.setObjectName("qcSubject")
        self.subject_label.setWordWrap(True)
        self.subject_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        title_column.addWidget(self.module_label)
        title_column.addWidget(self.subject_label)
        title_row.addLayout(title_column, 1)
        self.viewer_toolbar = QToolBar("Viewer", header)
        self.viewer_toolbar.setObjectName("qcViewerToolbar")
        self.viewer_toolbar.setMovable(False)
        self.viewer_toolbar.setFloatable(False)
        self.viewer_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.viewer_action, self.viewer_button = self._add_toolbar_action(
            self.viewer_toolbar,
            "Open viewer",
            QKeySequence("Ctrl+Shift+V"),
            self._launch_viewer,
        )
        self.viewer_button.setObjectName("primaryAction")
        self.viewer_button.setAccessibleName("Open external QC viewer")
        title_row.addWidget(self.viewer_toolbar)
        header_layout.addLayout(title_row)
        self.watch_badge = QLabel("", header)
        self.watch_badge.setObjectName("watchBadge")
        self.watch_badge.setWordWrap(True)
        self.watch_badge.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header_layout.addWidget(self.watch_badge)
        layout.addWidget(header)

        self.qc_splitter = QSplitter(Qt.Horizontal, self)
        self.qc_splitter.setObjectName("qcSplitter")
        self.subject_list = QListWidget(self.qc_splitter)
        self.subject_list.setObjectName("qcSubjectList")
        self.subject_list.setAccessibleName("QC subject sequence")
        self.subject_list.setMinimumWidth(self._subject_list_accessible_width())

        self.editor_scroll = QScrollArea(self.qc_splitter)
        self.editor_scroll.setObjectName("qcEditorScroll")
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setFrameShape(QFrame.NoFrame)
        editor = QWidget(self.editor_scroll)
        editor.setObjectName("qcEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(14, 8, 14, 14)
        editor_layout.setSpacing(12)

        module = self.workflow.current_module
        for key, score in module.scores.items():
            group_box = QGroupBox(score.label, editor)
            group_box.setObjectName("scoreGroup")
            group_box.setToolTip(score.label)
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
                button.setToolTip(label)
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
                checkbox.setToolTip(tag.label)
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
        self.editor_scroll.setWidget(editor)

        self.qc_splitter.addWidget(self.subject_list)
        self.qc_splitter.addWidget(self.editor_scroll)
        self.qc_splitter.setCollapsible(0, False)
        self.qc_splitter.setCollapsible(1, False)
        self.qc_splitter.setStretchFactor(0, 0)
        self.qc_splitter.setStretchFactor(1, 1)
        self.qc_splitter.setSizes([230, 760])
        layout.addWidget(self.qc_splitter, 1)

        self.action_toolbar = QToolBar("QC actions", self)
        self.action_toolbar.setObjectName("qcActionToolbar")
        self.action_toolbar.setAccessibleName("QC navigation and save actions")
        self.action_toolbar.setMovable(False)
        self.action_toolbar.setFloatable(False)
        self.action_toolbar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.previous_action, self.previous_button = self._add_toolbar_action(
            self.action_toolbar,
            "Previous",
            QKeySequence("Alt+Left"),
            lambda: self._navigate_delta(-1),
        )
        self.next_action, self.next_button = self._add_toolbar_action(
            self.action_toolbar,
            "Next",
            QKeySequence("Alt+Right"),
            lambda: self._navigate_delta(1),
        )
        self.action_toolbar.addSeparator()
        self.dirty_label = QLabel("", self.action_toolbar)
        self.dirty_label.setObjectName("qcDirtyState")
        self.dirty_label.setMinimumWidth(0)
        self.dirty_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.action_toolbar.addWidget(self.dirty_label)
        self.discard_action, self.discard_button = self._add_toolbar_action(
            self.action_toolbar,
            "Discard changes",
            QKeySequence("Ctrl+D"),
            self._discard_changes,
        )
        self.save_action, self.save_button = self._add_toolbar_action(
            self.action_toolbar,
            "Save",
            QKeySequence("Ctrl+S"),
            self._save,
        )
        self.save_next_action, self.save_next_button = self._add_toolbar_action(
            self.action_toolbar,
            "Save && Next",
            QKeySequence("Ctrl+Return"),
            self._save_next,
        )
        self.save_next_button.setObjectName("primaryAction")
        layout.addWidget(self.action_toolbar)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("qcError")
        self.error_label.setAccessibleName("QC action error")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        self.subject_list.currentRowChanged.connect(self._subject_row_changed)
        self.notes_edit.textChanged.connect(self._notes_changed)

    def _add_toolbar_action(
        self,
        toolbar: QToolBar,
        text: str,
        shortcut: QKeySequence,
        callback,
    ) -> tuple[QAction, QWidget]:
        """Add one native action and return its action/tool-button pair."""

        action = QAction(text, toolbar)
        action.setShortcut(shortcut)
        action.setShortcutContext(Qt.WindowShortcut)
        action.triggered.connect(callback)
        toolbar.addAction(action)
        button = toolbar.widgetForAction(action)
        button.setAccessibleName(text)
        return action, button

    def _subject_list_accessible_width(self) -> int:
        """Return a font/style-derived minimum for sequence plus identity text."""

        text_width = self.subject_list.fontMetrics().horizontalAdvance(
            "0000   WWWWWWWW"
        )
        scroll_extent = self.style().pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent,
            None,
            self.subject_list,
        )
        return text_width + scroll_extent + 2 * self.subject_list.frameWidth() + 8

    def changeEvent(self, event: QEvent) -> None:
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange) and hasattr(
            self,
            "subject_list",
        ):
            self.subject_list.setMinimumWidth(self._subject_list_accessible_width())
        super().changeEvent(event)

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
            self.module_label.setToolTip(self.module_label.text())
            self.subject_label.setText(
                f"{self.workflow.current_ezqcid}  ·  "
                f"{self.workflow.current_index + 1} of {len(self.workflow.subject_ids)}"
            )
            self.subject_label.setToolTip(self.subject_label.text())
            self.subject_list.blockSignals(True)
            self.subject_list.clear()
            for position, identity in enumerate(self.workflow.subject_ids, start=1):
                self.subject_list.addItem(f"{position:>4}   {identity}")
                self.subject_list.item(self.subject_list.count() - 1).setToolTip(
                    f"{position:>4}   {identity}"
                )
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
                    legacy_button.setToolTip(legacy_button.text())
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
            self.save_action.setEnabled(not read_only)
            self.save_next_action.setEnabled(
                not read_only and self.workflow.current_index < len(self.workflow.subject_ids) - 1
            )
            self.previous_action.setEnabled(self.workflow.current_index > 0)
            self.next_action.setEnabled(
                self.workflow.current_index < len(self.workflow.subject_ids) - 1
            )
            self.viewer_action.setEnabled(bool((module.code or "").strip()))
            self.dirty_label.setText("Unsaved changes" if self.workflow.dirty else "Saved state")
            self.discard_action.setEnabled(self.workflow.dirty)
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
        self.discard_action.setEnabled(True)
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
        self.discard_action.setEnabled(True)
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
        self.discard_action.setEnabled(True)
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
        self.viewer_action.setText(f"Viewer open ({len(processes)})")

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
        self.viewer_action.setText("Open viewer")
        self._refresh()

    def _navigate_delta(self, delta: int) -> None:
        try:
            self.workflow.move(delta)
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self.viewer_action.setText("Open viewer")
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
