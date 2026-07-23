"""Compact Qt controller for one toolkit-neutral interactive QC session."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.qc_workflow_service import QcWorkflowService


class QtQcQueueModel(QAbstractTableModel):
    """Virtual read-only projection of one ordered QC queue."""

    HEADERS = ("序号", "ezqcid", "评分", "标签")

    def __init__(self, workflow: QcWorkflowService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.workflow = workflow

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.workflow.subject_ids)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        position = index.row()
        identity = self.workflow.subject_ids[position]
        score, tags = self.workflow.queue_summary(identity)
        values = (position + 1, identity, score, tags)
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            return str(values[index.column()])
        if role == Qt.UserRole:
            return identity
        if role == Qt.TextAlignmentRole:
            horizontal = Qt.AlignLeft if index.column() == 1 else Qt.AlignCenter
            return horizontal | Qt.AlignVCenter
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation,
        role: int = Qt.DisplayRole,
    ):
        if (
            orientation == Qt.Horizontal
            and role == Qt.DisplayRole
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def identity_at(self, visible_row: int) -> str:
        if visible_row < 0 or visible_row >= self.rowCount():
            raise IndexError("QC queue row is outside the visible list")
        return self.workflow.subject_ids[visible_row]

    def visible_row_for(self, identity: str) -> int | None:
        normalized = str(identity).strip()
        if normalized == self.workflow.current_ezqcid:
            return self.workflow.current_index
        return next(
            (
                visible_row
                for visible_row, identity in enumerate(self.workflow.subject_ids)
                if identity == normalized
            ),
            None,
        )

    def current_visible_row(self) -> int | None:
        return self.workflow.current_index

    def neighbor_identity(self, identity: str, delta: int) -> str | None:
        normalized = str(identity).strip()
        row = (
            self.workflow.current_index
            if normalized == self.workflow.current_ezqcid
            else self.visible_row_for(normalized)
        )
        if row is None:
            return None
        target = row + int(delta)
        return self.identity_at(target) if 0 <= target < self.rowCount() else None

    def refresh_identity(self, identity: str) -> None:
        normalized = str(identity).strip()
        row = (
            self.workflow.current_index
            if normalized == self.workflow.current_ezqcid
            else self.visible_row_for(normalized)
        )
        if row is not None:
            self.dataChanged.emit(
                self.index(row, 2),
                self.index(row, 3),
                [Qt.DisplayRole, Qt.ToolTipRole],
            )


class QtQcWorkspace(QWidget):
    """Render one compact QC draft; Core owns viewer and rating side effects."""

    draftStateChanged = Signal(bool)
    filterRequested = Signal()

    def __init__(self, workflow: QcWorkflowService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not isinstance(workflow, QcWorkflowService):
            raise TypeError("QtQcWorkspace requires QcWorkflowService")
        self.workflow = workflow
        self._loading = False
        self._manual_read_only = False
        self._filter_busy = False
        self._score_groups: dict[str, QButtonGroup] = {}
        self.score_buttons: dict[str, dict[str | None, QRadioButton]] = {}
        self._legacy_score_buttons: dict[str, QRadioButton] = {}
        self.tag_boxes: dict[str, QCheckBox] = {}
        self.setObjectName("qtQcWorkspace")
        self.setAccessibleName("EasyQC 质控控制器")
        self._build_ui()
        self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        top = QWidget(self)
        top.setObjectName("qcQueueAndActions")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        self.queue_model = QtQcQueueModel(self.workflow, self)
        self.queue_table = QTableView(top)
        self.queue_table.setObjectName("qcQueueTable")
        self.queue_table.setAccessibleName("质控名单")
        self.queue_table.setModel(self.queue_model)
        self.queue_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.queue_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.queue_table.verticalHeader().hide()
        header = self.queue_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.queue_table.setMinimumHeight(210)
        top_layout.addWidget(self.queue_table, 1)

        self.action_column = QWidget(top)
        self.action_column.setObjectName("qcActionColumn")
        action_layout = QVBoxLayout(self.action_column)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(7)
        self.read_only_box = QCheckBox("只读", self.action_column)
        self.read_only_box.setAccessibleName("只读模式")
        action_layout.addWidget(self.read_only_box)
        self.filter_action, self.filter_button = self._add_action_button(
            "筛选名单",
            QKeySequence("Ctrl+F"),
            self._prompt_queue_filter,
        )
        self.previous_action, self.previous_button = self._add_action_button(
            "上一个",
            QKeySequence("Alt+Left"),
            lambda: self._navigate_visible(-1),
        )
        self.next_action, self.next_button = self._add_action_button(
            "下一个",
            QKeySequence("Alt+Right"),
            lambda: self._navigate_visible(1),
        )
        self.save_action, self.save_button = self._add_action_button(
            "保存",
            QKeySequence("Ctrl+S"),
            self._save,
        )
        self.save_next_action, self.save_next_button = self._add_action_button(
            "保存并下一个",
            QKeySequence("Ctrl+Return"),
            self._save_next,
        )
        self.save_next_button.setObjectName("primaryAction")
        self.action_controls = (
            self.read_only_box,
            self.filter_button,
            self.previous_button,
            self.next_button,
            self.save_button,
            self.save_next_button,
        )
        widest = max(
            control.fontMetrics().horizontalAdvance(control.text())
            for control in self.action_controls
        )
        self.action_column.setMinimumWidth(widest + 40)
        action_layout.addStretch(1)
        top_layout.addWidget(self.action_column)
        layout.addWidget(top, 3)

        self.editor_scroll = QScrollArea(self)
        self.editor_scroll.setObjectName("qcEditorScroll")
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setFrameShape(QFrame.NoFrame)
        editor = QWidget(self.editor_scroll)
        editor.setObjectName("qcEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 4, 0, 4)
        editor_layout.setSpacing(8)

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
            choices = [(None, "未评"), *((value, value) for value in score.allowed_values)]
            for value, label in choices:
                button = QRadioButton(label, group_box)
                button.setAccessibleName(f"{score.label}: {label}")
                button.setToolTip(label)
                button_group.addButton(button)
                self.score_buttons[key][value] = button
                group_layout.addWidget(button)
                button.toggled.connect(
                    lambda checked, score_key=key, score_value=value: self._score_changed(
                        score_key,
                        score_value,
                        checked,
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
            tags_box = QGroupBox("标签", editor)
            tags_box.setObjectName("tagGroup")
            tags_layout = QHBoxLayout(tags_box)
            for key, tag in module.tags.items():
                checkbox = QCheckBox(tag.label, tags_box)
                checkbox.setAccessibleName(f"质控标签: {tag.label}")
                checkbox.setToolTip(tag.label)
                checkbox.toggled.connect(
                    lambda checked, tag_key=key: self._tag_changed(tag_key, checked)
                )
                self.tag_boxes[key] = checkbox
                tags_layout.addWidget(checkbox)
            tags_layout.addStretch(1)
            editor_layout.addWidget(tags_box)

        notes_box = QGroupBox("备注", editor)
        notes_box.setObjectName("notesGroup")
        notes_layout = QVBoxLayout(notes_box)
        self.notes_edit = QTextEdit(notes_box)
        self.notes_edit.setObjectName("qcNotes")
        self.notes_edit.setAccessibleName("质控备注")
        self.notes_edit.setPlaceholderText("填写简短备注…")
        self.notes_edit.setMinimumHeight(90)
        notes_layout.addWidget(self.notes_edit)
        editor_layout.addWidget(notes_box)
        editor_layout.addStretch(1)
        self.editor_scroll.setWidget(editor)
        layout.addWidget(self.editor_scroll, 2)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("qcError")
        self.error_label.setAccessibleName("质控操作错误")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.queue_table.doubleClicked.connect(self._queue_double_clicked)
        self.read_only_box.toggled.connect(self._read_only_changed)
        self.notes_edit.textChanged.connect(self._notes_changed)

    def _add_action_button(
        self,
        text: str,
        shortcut: QKeySequence,
        callback,
    ) -> tuple[QAction, QPushButton]:
        action = QAction(text, self)
        action.setShortcut(shortcut)
        action.setShortcutContext(Qt.WindowShortcut)
        action.triggered.connect(callback)
        self.addAction(action)
        button = QPushButton(text, self.action_column)
        button.setAccessibleName(text)
        button.clicked.connect(callback)
        self.action_column.layout().addWidget(button)
        return action, button

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    @property
    def filter_busy(self) -> bool:
        return self._filter_busy

    def _effective_read_only(self) -> bool:
        return self.workflow.watch_mode or self._manual_read_only

    def _set_action_enabled(
        self,
        action: QAction,
        button: QPushButton,
        enabled: bool,
    ) -> None:
        action.setEnabled(enabled)
        button.setEnabled(enabled)

    def _refresh(self) -> None:
        self._loading = True
        try:
            module = self.workflow.current_module
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
                    legacy_button.setText(f"{score.value}（旧评分值，不在当前选项中）")
                    legacy_button.setAccessibleName(
                        f"{score.label}: {score.value}，旧评分值，不在当前选项中"
                    )
                    legacy_button.setToolTip(legacy_button.text())
                    legacy_button.setEnabled(False)
                    legacy_button.show()
                    legacy_button.setChecked(True)
            for key, tag in module.tags.items():
                self.tag_boxes[key].setChecked(tag.value)
            self.notes_edit.setPlainText(module.notes or "")

            core_read_only = self.workflow.watch_mode
            self.read_only_box.blockSignals(True)
            self.read_only_box.setChecked(core_read_only or self._manual_read_only)
            self.read_only_box.setEnabled(not core_read_only and not self._filter_busy)
            self.read_only_box.setToolTip(
                self.workflow.read_only_reason if core_read_only else "仅查看，不修改评分"
            )
            self.read_only_box.blockSignals(False)
            read_only = self._effective_read_only()
            for buttons in self.score_buttons.values():
                for button in buttons.values():
                    button.setEnabled(not read_only and not self._filter_busy)
            for checkbox in self.tag_boxes.values():
                checkbox.setEnabled(not read_only and not self._filter_busy)
            self.notes_edit.setReadOnly(read_only or self._filter_busy)

            previous_identity = self.queue_model.neighbor_identity(
                self.workflow.current_ezqcid,
                -1,
            )
            next_identity = self.queue_model.neighbor_identity(
                self.workflow.current_ezqcid,
                1,
            )
            self._set_action_enabled(
                self.previous_action,
                self.previous_button,
                previous_identity is not None and not self._filter_busy,
            )
            self._set_action_enabled(
                self.next_action,
                self.next_button,
                next_identity is not None and not self._filter_busy,
            )
            self._set_action_enabled(
                self.save_action,
                self.save_button,
                not read_only and not self._filter_busy,
            )
            self._set_action_enabled(
                self.save_next_action,
                self.save_next_button,
                not read_only and not self._filter_busy and next_identity is not None,
            )
            self._set_action_enabled(
                self.filter_action,
                self.filter_button,
                not self._filter_busy,
            )
            self.queue_model.refresh_identity(self.workflow.current_ezqcid)
            current_row = self.queue_model.current_visible_row()
            if current_row is None:
                self.queue_table.clearSelection()
            else:
                current = self.queue_model.index(current_row, 1)
                self.queue_table.setCurrentIndex(current)
                self.queue_table.selectRow(current_row)
                self.queue_table.scrollTo(current)
        finally:
            self._loading = False
        self.draftStateChanged.emit(self.workflow.dirty)

    def _read_only_changed(self, checked: bool) -> None:
        if self._loading:
            return
        if self.workflow.watch_mode:
            self._refresh()
            return
        if checked and self.workflow.dirty:
            self._set_error("请先保存当前修改，再进入只读模式")
            self._refresh()
            return
        self._manual_read_only = bool(checked)
        self._set_error("")
        self._refresh()

    def _score_changed(self, key: str, value: str | None, checked: bool) -> None:
        if self._loading or not checked:
            return
        if self._effective_read_only():
            self._refresh()
            return
        try:
            self.workflow.set_score(key, value)
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self.queue_model.refresh_identity(self.workflow.current_ezqcid)
        self.draftStateChanged.emit(True)

    def _tag_changed(self, key: str, checked: bool) -> None:
        if self._loading:
            return
        if self._effective_read_only():
            self._refresh()
            return
        try:
            self.workflow.set_tag(key, checked)
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self.queue_model.refresh_identity(self.workflow.current_ezqcid)
        self.draftStateChanged.emit(True)

    def _notes_changed(self) -> None:
        if self._loading:
            return
        if self._effective_read_only():
            self._refresh()
            return
        try:
            self.workflow.set_notes(self.notes_edit.toPlainText())
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self.draftStateChanged.emit(True)

    def _save(self) -> None:
        try:
            self.workflow.save()
        except Exception as exc:
            self._set_error(str(exc))
            return
        self._set_error("")
        self._refresh()

    def _save_next(self) -> None:
        next_identity = self.queue_model.neighbor_identity(
            self.workflow.current_ezqcid,
            1,
        )
        try:
            self.workflow.save()
            if next_identity is not None:
                self.workflow.navigate_to(next_identity)
                self.workflow.launch_viewer()
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self._refresh()

    def _navigate_visible(self, delta: int) -> None:
        identity = self.queue_model.neighbor_identity(
            self.workflow.current_ezqcid,
            delta,
        )
        if identity is not None:
            self._open_identity(identity)

    def _queue_double_clicked(self, index: QModelIndex) -> None:
        if index.isValid():
            self._open_identity(self.queue_model.identity_at(index.row()))

    def _open_identity(self, identity: str) -> None:
        try:
            self.workflow.navigate_to(identity)
            self.workflow.launch_viewer()
        except Exception as exc:
            self._set_error(str(exc))
            self._refresh()
            return
        self._set_error("")
        self._refresh()

    def _prompt_queue_filter(self) -> None:
        if self.workflow.dirty:
            self._set_error("请先保存当前修改，再筛选名单")
            return
        if self._filter_busy:
            self._set_error("质控名单筛选事务正在完成，请稍候")
            return
        self._set_error("")
        self.filterRequested.emit()

    def set_filter_busy(self, busy: bool) -> None:
        self._filter_busy = bool(busy)
        self._refresh()

    def set_filter_summary(self, matched_total: int, *, filtered: bool) -> None:
        count = int(matched_total)
        self.filter_button.setToolTip(
            f"当前模块筛选匹配 {count} 条"
            if filtered
            else f"全部质控名单 · {count} 条"
        )

    def show_filter_error(self, message: str) -> None:
        self._set_error(str(message))

    def _set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(bool(message))

    def close_workflow(self) -> None:
        self.workflow.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.close_workflow()
        super().closeEvent(event)


class QtQcControllerWindow(QMainWindow):
    """Own one separate compact QC window and its dirty-close decision."""

    closed = Signal()

    def __init__(self, workflow: QcWorkflowService) -> None:
        super().__init__(None)
        if not isinstance(workflow, QcWorkflowService):
            raise TypeError("QtQcControllerWindow requires QcWorkflowService")
        self._force_discard_close = False
        self.setObjectName("qtQcControllerWindow")
        self.setAccessibleName("EasyQC 质控控制器窗口")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("EasyQC")
        self.workspace = QtQcWorkspace(workflow, self)
        self.setCentralWidget(self.workspace)
        self.resize(560, 720)

    @property
    def workflow(self) -> QcWorkflowService:
        return self.workspace.workflow

    def close_discarding_draft(self) -> bool:
        self._force_discard_close = True
        return self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.workspace.filter_busy and not self._force_discard_close:
            self.workspace.show_filter_error("质控名单筛选事务正在完成，请稍候")
            event.ignore()
            return
        if self.workflow.dirty and not self._force_discard_close:
            answer = QMessageBox.question(
                self,
                "尚未保存",
                "放弃当前未保存的质控修改并关闭？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.workspace.close_workflow()
        self.closed.emit()
        super().closeEvent(event)


__all__ = ["QtQcControllerWindow", "QtQcQueueModel", "QtQcWorkspace"]
