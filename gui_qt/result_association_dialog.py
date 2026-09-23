"""Graphical, transactional editor for read-only result association rules."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from uuid import uuid4

import pandas as pd
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.table_view_service import TableViewService
from gui_qt.filter_dialog import FilterDialog
from gui_qt.i18n import (
    LanguageController, get_or_create_language_controller, protect_user_text,
)
from gui_qt.theme import configure_combo_box_presentation, set_button_role
from models.qcmodule import QCModule
from models.rating import Rating
from models.result_association import ResultAssociationRule
from models.table_view_state import (
    FilterExpression, filter_expression_from_json_object,
    filter_expression_to_json_object,
)


@dataclass
class _RuleDraft:
    """Unvalidated form state, including incomplete and unchecked field edits."""

    rule_id: str
    name: str
    source_module: str
    source_rater: str
    source_key: str
    target_key: str
    field_rows: list[tuple[str, str, bool]] = field(default_factory=list)
    source_filter: dict | None = None
    target_filter: dict | None = None


class ResultAssociationDialog(QDialog):
    """Emit a complete rule tuple; the owner validates/saves it outside the UI.

    Inputs are snapshots and are never mutated. Apply freezes the form until
    ``set_error`` restores its drafts or ``complete_apply`` accepts the dialog.
    This editor performs no file writes and never launches a viewer.
    """

    applyRequested = Signal(object)

    def __init__(
        self, subjects: pd.DataFrame, modules: tuple[QCModule, ...],
        ratings: tuple[Rating, ...], rules: tuple[ResultAssociationRule, ...],
        parent: QWidget | None = None, language: LanguageController | None = None,
    ) -> None:
        super().__init__(parent)
        self.language = language or get_or_create_language_controller()
        self._service = TableViewService(subjects)
        self._columns = tuple(str(column) for column in subjects.columns)
        self._modules = {module.name: module for module in modules}
        self._ratings = tuple(ratings)
        self._drafts = [
            _RuleDraft(
                rule.rule_id, rule.name, rule.source_module, rule.source_rater,
                rule.source_key, rule.target_key,
                [(source, output, True) for source, output in rule.fields],
                deepcopy(rule.source_filter), deepcopy(rule.target_filter),
            ) for rule in rules
        ]
        self._selected = -1
        self._pending = False
        self._loading = False
        self._filter_dialog: FilterDialog | None = None
        self.setObjectName("resultAssociationDialog")
        self.setModal(True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._build_ui()
        self.language.languageChanged.connect(self.retranslate_ui)
        self.retranslate_ui()
        self._refresh_rule_list(0 if self._drafts else -1)
        self.resize(800, 650)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.hint_label = QLabel(self)
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)
        self.body = QWidget(self)
        body_layout = QHBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        list_layout = QVBoxLayout()
        self.rule_list = QListWidget(self.body)
        self.rule_list.setMinimumWidth(120)
        self.rule_list.setMaximumWidth(220)
        protect_user_text(self.rule_list, "items")
        list_layout.addWidget(self.rule_list, 1)
        self.add_button = QPushButton(self.body)
        self.delete_button = QPushButton(self.body)
        list_layout.addWidget(self.add_button)
        list_layout.addWidget(self.delete_button)
        body_layout.addLayout(list_layout, 1)

        self.scroll_area = QScrollArea(self.body)
        self.scroll_area.setWidgetResizable(True)
        self.editor = QWidget(self.scroll_area)
        editor_layout = QVBoxLayout(self.editor)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.name_edit = QLineEdit(self.editor)
        protect_user_text(self.name_edit, "text")
        self.source_module_combo = self._combo()
        for name in dict.fromkeys((*self._modules, *(rating.module_name for rating in self._ratings))):
            self.source_module_combo.addItem(name, name)
        self.source_rater_combo = self._combo(editable=True)
        self.source_key_combo = self._combo()
        self.target_key_combo = self._combo()
        for combo in (self.source_key_combo, self.target_key_combo):
            for column in self._columns:
                combo.addItem(column, column)
        self._form_labels = []
        for source, control in (
            ("关联名称", self.name_edit), ("来源模块", self.source_module_combo),
            ("来源评分者", self.source_rater_combo), ("来源匹配列", self.source_key_combo),
            ("目标匹配列", self.target_key_combo),
        ):
            label = QLabel(self.editor)
            label.setBuddy(control)
            self._form_labels.append((source, label, control))
            form.addRow(label, control)
        editor_layout.addLayout(form)
        self.source_filter_button = QPushButton(self.editor)
        self.target_filter_button = QPushButton(self.editor)
        editor_layout.addWidget(self.source_filter_button)
        editor_layout.addWidget(self.target_filter_button)
        self.fields_table = QTableWidget(0, 2, self.editor)
        self.fields_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.fields_table.verticalHeader().hide()
        self.fields_table.setMinimumHeight(160)
        editor_layout.addWidget(self.fields_table, 1)
        self.scroll_area.setWidget(self.editor)
        body_layout.addWidget(self.scroll_area, 3)
        layout.addWidget(self.body, 1)

        self.empty_label = QLabel(self)
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        self.error_label = QLabel(self)
        self.error_label.setTextFormat(Qt.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setProperty("role", "error")
        self.error_label.hide()
        protect_user_text(self.error_label, "text")
        layout.addWidget(self.error_label)
        self.button_box = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel, self)
        self.apply_button = self.button_box.button(QDialogButtonBox.Apply)
        self.cancel_button = self.button_box.button(QDialogButtonBox.Cancel)
        set_button_role(self.apply_button, "primary")
        layout.addWidget(self.button_box)

        self.rule_list.currentRowChanged.connect(self._select_rule)
        self.add_button.clicked.connect(self._add_rule)
        self.delete_button.clicked.connect(self._delete_rule)
        self.source_module_combo.currentIndexChanged.connect(self._source_module_changed)
        self.source_filter_button.clicked.connect(lambda: self._open_filter("source"))
        self.target_filter_button.clicked.connect(lambda: self._open_filter("target"))
        self.apply_button.clicked.connect(self._request_apply)
        self.cancel_button.clicked.connect(self.reject)

    def _combo(self, *, editable: bool = False) -> QComboBox:
        combo = QComboBox(self.editor)
        combo.setEditable(editable)
        combo.setMinimumContentsLength(12)
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        configure_combo_box_presentation(combo)
        protect_user_text(combo, "items")
        if editable:
            protect_user_text(combo.lineEdit(), "text")
        return combo

    def retranslate_ui(self, _language: str | None = None) -> None:
        text = self.language.translate_source
        self.setWindowTitle(text("结果关联"))
        self.setAccessibleName(self.windowTitle())
        self.hint_label.setText(text("选择来源字段并填写新列名；关联只读，不会复制或修改评分。"))
        self.empty_label.setText(text("新增规则后配置关联；应用时保存全部规则。"))
        for source, label, control in self._form_labels:
            label.setText(text(source))
            control.setAccessibleName(text(source))
        for source, control in (
            ("关联规则", self.rule_list), ("添加规则", self.add_button),
            ("删除规则", self.delete_button), ("来源筛选…", self.source_filter_button),
            ("目标筛选…", self.target_filter_button), ("应用", self.apply_button),
            ("取消", self.cancel_button),
        ):
            if isinstance(control, QPushButton):
                control.setText(text(source))
            control.setAccessibleName(text(source))
        self.fields_table.setHorizontalHeaderLabels([text("来源字段"), text("输出列名")])
        self.fields_table.setAccessibleName(text("来源字段"))

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    @staticmethod
    def _select_combo(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        if index < 0:
            combo.addItem(value, value)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def _capture_fields(self) -> list[tuple[str, str, bool]]:
        return [
            (self.fields_table.item(row, 0).data(Qt.UserRole),
             self.fields_table.item(row, 1).text(),
             self.fields_table.item(row, 0).checkState() == Qt.Checked)
            for row in range(self.fields_table.rowCount())
        ]

    def _capture_current(self) -> None:
        if self._selected < 0:
            return
        draft = self._drafts[self._selected]
        draft.name = self.name_edit.text()
        draft.source_module = self.source_module_combo.currentData() or ""
        draft.source_rater = self.source_rater_combo.currentText()
        draft.source_key = self.source_key_combo.currentData() or ""
        draft.target_key = self.target_key_combo.currentData() or ""
        draft.field_rows = self._capture_fields()
        self.rule_list.item(self._selected).setText(draft.name or self.language.translate_source("新关联"))

    def _select_rule(self, row: int) -> None:
        self._capture_current()
        self._load_rule(row)

    def _load_rule(self, row: int) -> None:
        self._selected = row
        self.editor.setEnabled(row >= 0)
        self.delete_button.setEnabled(row >= 0)
        self.empty_label.setVisible(row < 0)
        if row < 0:
            return
        draft = self._drafts[row]
        self._loading = True
        self.name_edit.setText(draft.name)
        self._select_combo(self.source_module_combo, draft.source_module)
        self._select_combo(self.source_key_combo, draft.source_key)
        self._select_combo(self.target_key_combo, draft.target_key)
        self._populate_raters(draft.source_rater)
        self._populate_fields(draft.field_rows)
        self._loading = False

    def _refresh_rule_list(self, row: int) -> None:
        with QSignalBlocker(self.rule_list):
            self.rule_list.clear()
            for draft in self._drafts:
                self.rule_list.addItem(draft.name or self.language.translate_source("新关联"))
            self.rule_list.setCurrentRow(row)
        self._load_rule(row)

    def _add_rule(self) -> None:
        self._capture_current()
        self._drafts.append(_RuleDraft(
            str(uuid4()), self.language.translate_source("新关联"),
            self.source_module_combo.itemData(0) or "", "",
            self._columns[0] if self._columns else "",
            self._columns[0] if self._columns else "",
        ))
        self._refresh_rule_list(len(self._drafts) - 1)
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _delete_rule(self) -> None:
        if self._selected >= 0:
            del self._drafts[self._selected]
            self._refresh_rule_list(min(self._selected, len(self._drafts) - 1))

    def _populate_raters(self, current: str) -> None:
        module_name = self.source_module_combo.currentData()
        choices = [rating.rater for rating in self._ratings if rating.module_name == module_name]
        module = self._modules.get(module_name)
        if module is not None and module.rater:
            choices.append(module.rater)
        self.source_rater_combo.clear()
        self.source_rater_combo.addItems(list(dict.fromkeys(choices)))
        self.source_rater_combo.setEditText(current)

    def _populate_fields(self, existing: list[tuple[str, str, bool]]) -> None:
        module_name = self.source_module_combo.currentData()
        sources = [rating for rating in self._ratings if rating.module_name == module_name]
        if module_name in self._modules:
            sources.insert(0, self._modules[module_name])
        fields = list(dict.fromkeys(
            [f"{prefix}{key}" for source in sources
               for prefix, values in (("score", source.scores), ("tag", source.tags))
               for key in values]
            + ["notes"]
        ))
        stored = {name for name, _output, _enabled in existing}
        rows = existing + [(name, f"{module_name}_{name}", False) for name in fields if name not in stored]
        self.fields_table.setRowCount(len(rows))
        for row, (name, output, enabled) in enumerate(rows):
            item = QTableWidgetItem(name)
            item.setData(Qt.UserRole, name)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if enabled else Qt.Unchecked)
            self.fields_table.setItem(row, 0, item)
            self.fields_table.setItem(row, 1, QTableWidgetItem(output))

    def _source_module_changed(self) -> None:
        if not self._loading:
            existing = self._capture_fields()
            self._populate_raters(self.source_rater_combo.currentText())
            self._populate_fields(existing)

    def _open_filter(self, side: str) -> None:
        if self._selected < 0:
            return
        draft = self._drafts[self._selected]
        try:
            expression = filter_expression_from_json_object(getattr(draft, f"{side}_filter"))
            self._service.validate_state(self._service.default_state().with_filter(expression))
        except (ValueError, TypeError) as exc:
            self.set_error(str(exc))
            return
        dialog = FilterDialog(self._service.profiles, expression, self)
        self.language.register_root(dialog)
        dialog.applyRequested.connect(lambda expression: self._apply_filter(dialog, draft, side, expression))
        self._filter_dialog = dialog
        dialog.open()

    def _apply_filter(self, dialog: FilterDialog, draft: _RuleDraft, side: str,
                      expression: FilterExpression) -> None:
        try:
            normalized = self._service.validate_state(self._service.default_state().with_filter(expression)).effective_filter
            payload = filter_expression_to_json_object(normalized) if normalized.groups else None
        except (ValueError, TypeError) as exc:
            dialog.set_error(str(exc))
            return
        setattr(draft, f"{side}_filter", payload)
        dialog.complete_apply()

    def _request_apply(self) -> None:
        if self._pending:
            return
        self._capture_current()
        rules = []
        outputs = set(self._columns)
        try:
            for draft in self._drafts:
                if any(not value.strip() for value in (draft.name, draft.source_module, draft.source_rater, draft.source_key, draft.target_key)):
                    raise ValueError("关联规则名称、模块、评分者和匹配列不能为空")
                fields = tuple((name, output.strip()) for name, output, enabled in draft.field_rows if enabled)
                if not fields or any(not output for _name, output in fields):
                    raise ValueError("至少选择一个来源字段并填写输出列名")
                for _name, output in fields:
                    if output in outputs:
                        raise ValueError("输出列名不能重复或覆盖现有列")
                    outputs.add(output)
                rules.append(ResultAssociationRule(
                    draft.rule_id, draft.name, draft.source_module, draft.source_rater,
                    draft.source_key, draft.target_key, fields,
                    deepcopy(draft.source_filter), deepcopy(draft.target_filter),
                ))
        except (ValueError, TypeError) as exc:
            self.set_error(str(exc))
            return
        self.error_label.hide()
        self._pending = True
        self.body.setEnabled(False)
        self.apply_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.applyRequested.emit(tuple(rules))

    def set_error(self, message: str) -> None:
        """Report a failed validation/save without discarding any draft edits."""
        self._pending = False
        self.error_label.setText(self.language.translate_source(message))
        self.error_label.setVisible(bool(message))
        self.body.setEnabled(True)
        self.apply_button.setEnabled(True)
        self.cancel_button.setEnabled(True)

    def complete_apply(self) -> None:
        """Accept only after the owner has successfully saved the emitted rules."""
        if self._pending:
            self._pending = False
            self.accept()

    def reject(self) -> None:
        if not self._pending:
            super().reject()

    def closeEvent(self, event) -> None:
        if self._pending:
            event.ignore()
        else:
            super().closeEvent(event)


__all__ = ["ResultAssociationDialog"]
