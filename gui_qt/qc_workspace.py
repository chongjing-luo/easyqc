"""Compact Qt controller for one toolkit-neutral interactive QC session."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.qc_workflow_service import QcWorkflowService
from gui_qt.i18n import (
    LanguageController,
    get_or_create_language_controller,
    protect_user_text,
    translate_ui_text,
)
from gui_qt.qc_row_context_menu import QcRowContextMenu
from gui_qt.theme import CONTROL_HEIGHT, set_button_role
from models.qc_row_context import QcModuleMenuEntry, QcRecordMenuEntry, QcRowContext


DEFAULT_VISIBLE_QUEUE_ROWS = 8
DEFAULT_CONTROLLER_WIDTH = 560
DEFAULT_CONTROLLER_HEIGHT = 720
SCREEN_EDGE_MARGIN = 48
TAG_GRID_COLUMNS = 2


class _QcEditorScrollArea(QScrollArea):
    """Prefer the full editor height while retaining a scrollable zero minimum."""

    def sizeHint(self):  # noqa: N802
        hint = super().sizeHint()
        editor = self.widget()
        if editor is not None:
            hint.setHeight(editor.sizeHint().height() + 2 * self.frameWidth())
        return hint


class QtQcQueueModel(QAbstractTableModel):
    """Virtual read-only projection of one ordered QC queue."""

    HEADERS = ("序号", "ezqcid", "评分", "标签")

    def __init__(
        self,
        workflow: QcWorkflowService,
        language: LanguageController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.workflow = workflow
        self.language = language or get_or_create_language_controller()
        self.language.languageChanged.connect(self._retranslate_headers)

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
            return self.language.translate_source(self.HEADERS[section])
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

    def _retranslate_headers(self, _language: str) -> None:
        self.headerDataChanged.emit(
            Qt.Horizontal,
            0,
            len(self.HEADERS) - 1,
        )


class QtQcWorkspace(QWidget):
    """Render one compact QC draft; Core owns viewer and rating side effects."""

    draftStateChanged = Signal(bool)
    filterRequested = Signal()

    def __init__(
        self,
        workflow: QcWorkflowService,
        parent: QWidget | None = None,
        *,
        language: LanguageController | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(workflow, QcWorkflowService):
            raise TypeError("QtQcWorkspace requires QcWorkflowService")
        self.workflow = workflow
        self.language = language or get_or_create_language_controller()
        self._loading = False
        self._manual_read_only = workflow.initial_read_only
        self._filter_busy = False
        self._score_groups: dict[str, QButtonGroup] = {}
        self.score_buttons: dict[str, dict[str | None, QPushButton]] = {}
        self._legacy_score_buttons: dict[str, QPushButton] = {}
        self.tag_boxes: dict[str, QCheckBox] = {}
        self.row_context_provider: Callable[[str], QcRowContext] | None = None
        self.on_open_qc_module: (
            Callable[[str, QcModuleMenuEntry], None] | None
        ) = None
        self.on_open_qc_record: Callable[[QcRecordMenuEntry], None] | None = None
        self.active_row_context_menu: QcRowContextMenu | None = None
        self.setObjectName("qtQcWorkspace")
        self.setAccessibleName("EasyQC 质控控制器")
        self._build_ui()
        self._refresh()
        self.language.languageChanged.connect(self.retranslate_ui)
        self.language.register_root(self)
        self.retranslate_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        top = QWidget(self)
        top.setObjectName("qcQueueAndActions")
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)

        self.queue_model = QtQcQueueModel(
            self.workflow,
            self.language,
            self,
        )
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
        self.queue_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue_table.verticalHeader().hide()
        header = self.queue_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        queue_row_height = self.queue_table.verticalHeader().defaultSectionSize()
        queue_header_height = header.sizeHint().height()
        horizontal_scroll_height = (
            self.queue_table.horizontalScrollBar().sizeHint().height()
        )
        self.queue_table.setMinimumHeight(
            queue_header_height
            + DEFAULT_VISIBLE_QUEUE_ROWS * queue_row_height
            + horizontal_scroll_height
            + 2 * self.queue_table.frameWidth()
        )
        self.queue_table.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding,
        )
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
        set_button_role(self.save_next_button, "primary")
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
        action_control_height = max(CONTROL_HEIGHT, queue_row_height)
        for index, control in enumerate(self.action_controls):
            control.setMinimumHeight(action_control_height)
            if control is self.read_only_box:
                control.setMaximumHeight(action_control_height)
                control.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
            else:
                control.setSizePolicy(
                    QSizePolicy.Preferred,
                    QSizePolicy.Expanding,
                )
                action_layout.setStretch(index, 1)
        top_layout.addWidget(self.action_column)
        top.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(top, 1)

        self.editor_scroll = _QcEditorScrollArea(self)
        self.editor_scroll.setObjectName("qcEditorScroll")
        self.editor_scroll.setWidgetResizable(True)
        self.editor_scroll.setFrameShape(QFrame.NoFrame)
        self.editor_scroll.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Preferred,
        )
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
            protect_user_text(group_box, "title", "toolTip")
            group_layout = QHBoxLayout(group_box)
            button_group = QButtonGroup(group_box)
            button_group.setExclusive(True)
            self._score_groups[key] = button_group
            self.score_buttons[key] = {}
            choices = [(None, "未评"), *((value, value) for value in score.allowed_values)]
            for value, label in choices:
                button = QPushButton(label, group_box)
                if value is not None:
                    protect_user_text(
                        button,
                        "accessibleName",
                        "text",
                        "toolTip",
                    )
                button.setCheckable(True)
                button.setMinimumHeight(CONTROL_HEIGHT)
                button.setMinimumWidth(72)
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
            legacy_button = QPushButton("", group_box)
            legacy_button.setCheckable(True)
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
            tags_layout = QGridLayout(tags_box)
            for index, (key, tag) in enumerate(module.tags.items()):
                checkbox = QCheckBox(tag.label, tags_box)
                protect_user_text(
                    checkbox,
                    "text",
                    "toolTip",
                )
                checkbox.setAccessibleName(f"质控标签: {tag.label}")
                checkbox.setToolTip(tag.label)
                checkbox.toggled.connect(
                    lambda checked, tag_key=key: self._tag_changed(tag_key, checked)
                )
                self.tag_boxes[key] = checkbox
                row, column = divmod(index, TAG_GRID_COLUMNS)
                tags_layout.addWidget(
                    checkbox,
                    row,
                    column,
                    Qt.AlignLeft | Qt.AlignVCenter,
                )
            for column in range(TAG_GRID_COLUMNS):
                tags_layout.setColumnStretch(column, 1)
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
        layout.addWidget(self.editor_scroll)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("qcError")
        self.error_label.setProperty("role", "error")
        self.error_label.setAccessibleName("质控操作错误")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.queue_table.doubleClicked.connect(self._queue_double_clicked)
        self.queue_table.customContextMenuRequested.connect(
            self._open_queue_context_menu
        )
        self.read_only_box.toggled.connect(self._read_only_changed)
        self.notes_edit.textChanged.connect(self._notes_changed)

    def recommended_initial_height(self) -> int:
        """Return content-led height before the controller applies screen bounds."""

        editor = self.editor_scroll.widget()
        editor.layout().activate()
        own_layout = self.layout()
        margins = own_layout.contentsMargins()
        visible_error_height = (
            self.error_label.sizeHint().height()
            if self.error_label.isVisible()
            else 0
        )
        return (
            margins.top()
            + self.queue_table.minimumHeight()
            + own_layout.spacing()
            + editor.sizeHint().height()
            + visible_error_height
            + margins.bottom()
        )

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

    def retranslate_ui(self, _language: str | None = None) -> None:
        self.language.localize_widget_tree(self)
        self.queue_model._retranslate_headers(self.language.language)

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

    def set_row_context_actions(
        self,
        provider: Callable[[str], QcRowContext] | None,
        on_module: Callable[[str, QcModuleMenuEntry], None] | None,
        on_record: Callable[[QcRecordMenuEntry], None] | None,
    ) -> None:
        supplied = (provider, on_module, on_record)
        if any(item is not None for item in supplied) and not all(
            callable(item) for item in supplied
        ):
            raise TypeError("QC row context actions must be all callable or all None")
        self.row_context_provider = provider
        self.on_open_qc_module = on_module
        self.on_open_qc_record = on_record
        if self.active_row_context_menu is not None:
            self.active_row_context_menu.close()
            self.active_row_context_menu.deleteLater()
            self.active_row_context_menu = None

    def _open_queue_context_menu(self, point: QPoint) -> None:
        index = self.queue_table.indexAt(point)
        if not index.isValid():
            return
        self.queue_table.setCurrentIndex(index)
        self.queue_table.selectRow(index.row())
        identity = self.queue_model.identity_at(index.row())
        try:
            if (
                self.row_context_provider is None
                or self.on_open_qc_module is None
                or self.on_open_qc_record is None
            ):
                raise ValueError("当前质控表格不能打开质控菜单")
            provider = self.row_context_provider
            module_callback = self.on_open_qc_module
            record_callback = self.on_open_qc_record
            context = provider(identity)
            menu = QcRowContextMenu(
                context,
                on_module=lambda entry: module_callback(identity, entry),
                on_record=record_callback,
                parent=self.queue_table,
            )
        except (ArithmeticError, RuntimeError, TypeError, ValueError) as exc:
            self._set_error(str(exc).strip() or type(exc).__name__)
            return
        if self.active_row_context_menu is not None:
            self.active_row_context_menu.close()
            self.active_row_context_menu.deleteLater()
        self.active_row_context_menu = menu
        self._set_error("")
        menu.popup(self.queue_table.viewport().mapToGlobal(point))

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
        self.language.unregister_root(self)
        self.close_workflow()
        super().closeEvent(event)


class QtQcControllerWindow(QMainWindow):
    """Own one separate compact QC window and its dirty-close decision."""

    closed = Signal()

    def __init__(
        self,
        workflow: QcWorkflowService,
        *,
        language: LanguageController | None = None,
    ) -> None:
        super().__init__(None)
        if not isinstance(workflow, QcWorkflowService):
            raise TypeError("QtQcControllerWindow requires QcWorkflowService")
        self._force_discard_close = False
        self.language = language or get_or_create_language_controller()
        self.setObjectName("qtQcControllerWindow")
        self.setAccessibleName("EasyQC 质控控制器窗口")
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowTitle("EasyQC")
        self.workspace = QtQcWorkspace(
            workflow,
            self,
            language=self.language,
        )
        self.setCentralWidget(self.workspace)
        available = self.screen().availableGeometry()
        width_cap = max(1, available.width() - SCREEN_EDGE_MARGIN)
        height_cap = max(1, available.height() - SCREEN_EDGE_MARGIN)
        target_width = min(DEFAULT_CONTROLLER_WIDTH, width_cap)
        target_height = min(
            max(
                DEFAULT_CONTROLLER_HEIGHT,
                self.workspace.recommended_initial_height(),
            ),
            height_cap,
        )
        self.resize(target_width, target_height)
        self.language.register_root(self)

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
                translate_ui_text("尚未保存"),
                translate_ui_text("放弃当前未保存的质控修改并关闭？"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.workspace.close_workflow()
        self.language.unregister_root(self)
        self.closed.emit()
        super().closeEvent(event)


__all__ = [
    "DEFAULT_VISIBLE_QUEUE_ROWS",
    "QtQcControllerWindow",
    "QtQcQueueModel",
    "QtQcWorkspace",
]
