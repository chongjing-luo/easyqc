"""Safe one-time EasyQC Formula dialog for an authoritative table."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import pandas as pd
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QComboBox,
    QTableWidget,
    QTabWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.assignment import add_group_column, assign_raters
from core.formula_engine import (
    FormulaEngine,
    FormulaEvaluation,
    FormulaEvaluationError,
)
from core.formula_parser import FormulaError, ParsedFormula
from gui_qt.formula_editor import FormulaEditorWidget
from gui_qt.i18n import set_translatable_table_columns
from gui_qt.task_runner import RevisionedTaskController
from gui_qt.theme import set_button_role
from models.derived_formula import DerivedColumnFormula


class DerivedColumnDialog(QDialog):
    """Preview one formula, then commit it against a context-owned full source."""

    busyChanged = Signal(bool)
    columnCommitted = Signal(str)

    def __init__(
        self,
        preview_source: pd.DataFrame,
        persist_column: Callable[[DerivedColumnFormula], str],
        parent: QWidget | None = None,
        *,
        row_count: int = 0,
        existing_columns: tuple[str, ...] = (),
        group_handler: Callable[[str, int], str] | None = None,
        assignment_handler: Callable[[list, int, object], int] | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(preview_source, pd.DataFrame):
            raise TypeError("DerivedColumnDialog preview source must be a DataFrame")
        if preview_source.columns.empty:
            raise ValueError("新增列预览来源至少需要一个列")
        if not callable(persist_column):
            raise TypeError("DerivedColumnDialog requires a persistence callback")
        self._preview_source = preview_source.head(20).copy(deep=True)
        self._persist_column = persist_column
        self._row_count = max(int(row_count), 0)
        self._existing_columns = frozenset(str(c) for c in existing_columns) or frozenset(
            str(c) for c in self._preview_source.columns
        )
        self._group_handler = group_handler
        self._assignment_handler = assignment_handler
        self._engine = FormulaEngine()
        self._revision = 0
        self._pending_request: (
            tuple[int, tuple[str, Any]] | None
        ) = None
        self.committed_column = ""

        self.setObjectName("derivedColumnDialog")
        self.setWindowTitle("新增列")
        self.setAccessibleName("使用快捷模板或 EasyQC 公式生成新列")
        self.setModal(True)

        self.task_controller = RevisionedTaskController(self)
        self.task_controller.resultReady.connect(self._handle_commit_result)
        self.task_controller.errorRaised.connect(self._handle_commit_error)
        self.task_controller.busyChanged.connect(self._set_busy)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.name_edit = QLineEdit(self)
        self.name_edit.setObjectName("derivedColumnName")
        self.name_edit.setAccessibleName("新列名")
        self.name_edit.setPlaceholderText("例如：scan_key 或 QC 分组")
        form.addRow("新列名", self.name_edit)
        layout.addLayout(form)

        helper = QLabel(
            "快捷模板和高级公式共用同一套安全计算规则。公式、模板和中间结果不会保存；"
            "只写入最终普通列。",
            self,
        )
        helper.setWordWrap(True)
        helper.setProperty("role", "secondary")
        layout.addWidget(helper)

        columns = tuple(str(column) for column in self._preview_source.columns)

        # Mode tabs: formula (default) / grouping / rater assignment. The two
        # extra modes are alternative ways to produce a column inside this one
        # dialog; they are hidden when their handler is not provided.
        self.mode_tabs = QTabWidget(self)
        self.mode_tabs.setObjectName("derivedColumnModes")
        self.mode_tabs.setAccessibleName("新增列方式")

        formula_page = QWidget(self)
        formula_layout = QVBoxLayout(formula_page)
        formula_layout.setContentsMargins(0, 0, 0, 0)
        self.editor = FormulaEditorWidget(columns, formula_page)
        self.editor.setObjectName("derivedColumnFormulaEditor")
        self.editor.setAccessibleName("新增列公式编辑器")
        self.editor.validationError.connect(self._formula_validation_changed)
        formula_layout.addWidget(self.editor)
        self.mode_tabs.addTab(formula_page, "公式")

        self.group_page = self._build_group_page()
        self.mode_tabs.addTab(self.group_page, "编组")

        self.assignment_page = self._build_assignment_page()
        self.mode_tabs.addTab(self.assignment_page, "评分者分配")

        if self._group_handler is None:
            self.mode_tabs.removeTab(self.mode_tabs.indexOf(self.group_page))
        if self._assignment_handler is None:
            self.mode_tabs.removeTab(self.mode_tabs.indexOf(self.assignment_page))
        self.mode_tabs.currentChanged.connect(lambda _idx: self._clear_error_and_preview())
        layout.addWidget(self.mode_tabs, 3)

        preview_header = QHBoxLayout()
        preview_header.addWidget(QLabel("前 20 行预览", self))
        preview_header.addStretch(1)
        self.preview_button = QPushButton("预览结果", self)
        self.preview_button.setAccessibleName("预览新增列结果和逐行错误")
        set_button_role(self.preview_button, "secondary")
        preview_header.addWidget(self.preview_button)
        layout.addLayout(preview_header)

        self.preview_table = QTableWidget(0, 0, self)
        self.preview_table.setObjectName("derivedColumnPreview")
        self.preview_table.setAccessibleName("新增列前二十行公式预览")
        self.preview_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.verticalHeader().hide()
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.preview_table, 2)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("derivedColumnError")
        self.error_label.setAccessibleName("新增列错误")
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self.generate_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.cancel_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.generate_button.setText("生成列")
        self.cancel_button.setText("取消")
        self.generate_button.setAccessibleName("确认计算完整数据并写入新列")
        self.cancel_button.setAccessibleName("取消新增列")
        set_button_role(self.generate_button, "primary")
        set_button_role(self.cancel_button, "secondary")
        layout.addWidget(self.button_box)

        self.preview_button.clicked.connect(self.preview)
        self.generate_button.clicked.connect(self._request_commit)
        self.cancel_button.clicked.connect(self.reject)
        self.resize(980, 820)

    # ---- grouping mode page ----

    def _build_group_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        form = QFormLayout()
        self.group_size_spin = QSpinBox(page)
        self.group_size_spin.setRange(1, 1_000_000)
        self.group_size_spin.setValue(30)
        self.group_size_spin.setAccessibleName("每组行数")
        self.group_size_spin.lineEdit().setAccessibleName("每组行数")
        form.addRow("每组行数", self.group_size_spin)
        self.group_order_combo = QComboBox(page)
        self.group_order_combo.setObjectName("groupOrderCombo")
        self.group_order_combo.setAccessibleName("分组方式")
        self.group_order_combo.addItem("顺序", "sequential")
        self.group_order_combo.addItem("倒序", "reverse")
        self.group_order_combo.addItem("随机", "random")
        form.addRow("分组方式", self.group_order_combo)
        self.group_seed_edit = QLineEdit(page)
        self.group_seed_edit.setPlaceholderText("留空则每次随机")
        self.group_seed_edit.setAccessibleName("随机种子")
        self.group_seed_edit.setEnabled(False)
        form.addRow("随机种子（可选）", self.group_seed_edit)
        layout.addLayout(form)
        self.group_order_combo.currentIndexChanged.connect(
            lambda _idx: self.group_seed_edit.setEnabled(
                self.group_order_combo.currentData() == "random"
            )
        )
        self.group_summary_label = QLabel("", page)
        self.group_summary_label.setWordWrap(True)
        self.group_summary_label.setProperty("role", "secondary")
        layout.addWidget(self.group_summary_label)
        hint = QLabel(
            "顺序：按当前表格行顺序编号分组（建议先排序）；倒序：从最后一行开始编号；"
            "随机：成员随机进入各组，每组人数相同。",
            page,
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "secondary")
        layout.addWidget(hint)
        layout.addStretch(1)
        self.group_size_spin.valueChanged.connect(lambda _: self._update_group_summary())
        self._update_group_summary()
        return page

    def _update_group_summary(self) -> None:
        size = self.group_size_spin.value()
        groups = (self._row_count + size - 1) // size if size else 0
        self.group_summary_label.setText(
            f"当前表格 {self._row_count} 行，每组 {size} 行，将生成 {groups} 组。"
        )

    # ---- rater assignment mode page ----

    def _build_assignment_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        form = QFormLayout()
        self.assign_raters_edit = QLineEdit(page)
        self.assign_raters_edit.setPlaceholderText("例如：张三, 李四, 王五, 赵六")
        self.assign_raters_edit.setAccessibleName("评分者名单")
        form.addRow("评分者（逗号分隔）", self.assign_raters_edit)
        self.assign_per_image_spin = QSpinBox(page)
        self.assign_per_image_spin.setRange(1, 20)
        self.assign_per_image_spin.setValue(1)
        self.assign_per_image_spin.setAccessibleName("每图评分人数")
        self.assign_per_image_spin.lineEdit().setAccessibleName("每图评分人数")
        form.addRow("每图评分人数", self.assign_per_image_spin)
        self.assign_seed_edit = QLineEdit(page)
        self.assign_seed_edit.setPlaceholderText("留空则每次随机")
        self.assign_seed_edit.setAccessibleName("随机种子")
        form.addRow("随机种子（可选）", self.assign_seed_edit)
        self.assign_column_edit = QLineEdit("rater", page)
        self.assign_column_edit.setAccessibleName("分配结果列名")
        form.addRow("分配结果列名", self.assign_column_edit)
        layout.addLayout(form)
        self.assign_stats_label = QLabel("", page)
        self.assign_stats_label.setWordWrap(True)
        self.assign_stats_label.setProperty("role", "secondary")
        layout.addWidget(self.assign_stats_label)
        hint = QLabel(
            "每张图像随机分配给多位评分者（每图不重复），总量在评分者之间均衡；"
            "结果导出为独立分配表（easyqcid 会按每图人数重复出现），不写回质控前名单。",
            page,
        )
        hint.setWordWrap(True)
        hint.setProperty("role", "secondary")
        layout.addWidget(hint)
        layout.addStretch(1)
        return page

    def _assignment_inputs(self) -> tuple[list[str], int, int | None, str]:
        raw = self.assign_raters_edit.text()
        names: list[str] = []
        for part in re.split(r"[,，;；\s]+", raw):
            part = part.strip()
            if part and part not in names:
                names.append(part)
        seed_text = self.assign_seed_edit.text().strip()
        seed = int(seed_text) if seed_text else None
        column = self.assign_column_edit.text().strip() or "rater"
        return names, self.assign_per_image_spin.value(), seed, column

    def _validate_assignment(self) -> tuple[list[str], int, int | None, str]:
        raters, per_image, seed, column = self._assignment_inputs()
        if not raters:
            raise ValueError("请输入评分者名单")
        if per_image > len(raters):
            raise ValueError("每图人数不能超过评分者数")
        return raters, per_image, seed, column

    # ---- mode routing ----

    def _current_mode(self) -> str:
        page = self.mode_tabs.currentWidget()
        if page is self.group_page:
            return "group"
        if page is self.assignment_page:
            return "assignment"
        return "formula"

    def _clear_error_and_preview(self) -> None:
        self._set_error("")

    def _request_formula(self) -> DerivedColumnFormula:
        request = DerivedColumnFormula(
            self.name_edit.text(),
            self.editor.formula(),
        )
        if request.name in self._preview_source.columns:
            raise ValueError(f"列已存在: {request.name}")
        return request

    def _evaluate_preview(
        self,
    ) -> tuple[DerivedColumnFormula, ParsedFormula, FormulaEvaluation]:
        request = self._request_formula()
        parsed = self._engine.parser.parse(request.expression)
        evaluation = self._engine.evaluate(self._preview_source, parsed)
        return request, parsed, evaluation

    def preview(self) -> bool:
        mode = self._current_mode()
        if mode == "group":
            return self._preview_group()
        if mode == "assignment":
            return self._preview_assignment()
        try:
            request, parsed, evaluation = self._evaluate_preview()
        except (ArithmeticError, FormulaError, TypeError, ValueError) as exc:
            self._set_error(str(exc))
            return False
        self._render_preview(request, parsed, evaluation)
        self._set_error(self._row_error_summary(evaluation))
        return True

    def _preview_group(self) -> bool:
        try:
            name, size, order, seed = self._request_group_payload()
            preview = add_group_column(
                self._preview_source, size=size, column=name,
                order=order, seed=seed,
            )
        except (ArithmeticError, TypeError, ValueError) as exc:
            self._set_error(str(exc))
            return False
        self._render_plain_preview(preview)
        self._set_error("")
        return True

    def _preview_assignment(self) -> bool:
        try:
            raters, per_image, seed, column = self._validate_assignment()
            preview = assign_raters(
                self._preview_source, raters=raters, per_image=per_image,
                seed=seed, column=column,
            )
        except (ArithmeticError, TypeError, ValueError) as exc:
            self._set_error(str(exc))
            return False
        counts = preview[column].value_counts().to_dict()
        stats = ", ".join(f"{name}: {count}" for name, count in counts.items())
        expected = self._row_count * per_image // max(len(raters), 1)
        self.assign_stats_label.setText(
            f"预览统计（前{len(self._preview_source)}行）: {stats} ｜ "
            f"全表 {self._row_count} 行 × 每图 {per_image} 人 ≈ 每人 {expected} 张"
        )
        self._render_plain_preview(preview)
        self._set_error("")
        return True

    def _render_plain_preview(self, frame: pd.DataFrame) -> None:
        headers = [str(c) for c in frame.columns]
        self.preview_table.clear()
        self.preview_table.setColumnCount(len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        self.preview_table.setRowCount(min(len(frame), 20))
        for row in range(min(len(frame), 20)):
            record = frame.iloc[row]
            for col_idx, name in enumerate(headers):
                self.preview_table.setItem(
                    row, col_idx,
                    QTableWidgetItem(self._display_value(record[name])),
                )
        header = self.preview_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        if headers:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)

    def _render_preview(
        self,
        request: DerivedColumnFormula,
        parsed: ParsedFormula,
        evaluation: FormulaEvaluation,
    ) -> None:
        has_identity = "easyqcid" in self._preview_source.columns
        referenced = [
            column
            for column in parsed.referenced_columns
            if not (has_identity and column == "easyqcid")
        ]
        headers = [
            "easyqcid" if has_identity else "行",
            *referenced,
            request.name,
            "错误",
        ]
        self.preview_table.clear()
        self.preview_table.setColumnCount(len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        if has_identity:
            self.preview_table.horizontalHeaderItem(0).setData(
                Qt.ItemDataRole.UserRole,
                "easyqcid",
            )
        for column_index, column in enumerate(referenced, start=1):
            self.preview_table.horizontalHeaderItem(column_index).setData(
                Qt.ItemDataRole.UserRole,
                column,
            )
        result_column_index = 1 + len(referenced)
        self.preview_table.horizontalHeaderItem(result_column_index).setData(
            Qt.ItemDataRole.UserRole,
            request.name,
        )
        set_translatable_table_columns(
            self.preview_table,
            len(headers) - 1,
        )
        self.preview_table.setRowCount(len(self._preview_source))

        for row in range(len(self._preview_source)):
            identity = (
                self._preview_source.iloc[row]["easyqcid"]
                if has_identity
                else row + 1
            )
            values = [
                identity,
                *(
                    self._preview_source.iloc[row][column]
                    for column in referenced
                ),
                evaluation.values.iloc[row],
                evaluation.errors.iloc[row],
            ]
            for column_index, value in enumerate(values):
                self.preview_table.setItem(
                    row,
                    column_index,
                    QTableWidgetItem(self._display_value(value)),
                )

        header = self.preview_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        if headers:
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setStretchLastSection(True)

    @staticmethod
    def _row_error_summary(evaluation: FormulaEvaluation) -> str:
        try:
            evaluation.raise_for_errors()
        except FormulaEvaluationError as exc:
            return str(exc)
        return ""

    def _request_group_payload(self) -> tuple[str, int, str, int | None]:
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("请输入非空列名")
        if name in self._existing_columns:
            raise ValueError(f"列已存在: {name}")
        if self._group_handler is None:
            raise ValueError("当前表格不能新增编组列")
        order = self.group_order_combo.currentData() or "sequential"
        seed_text = self.group_seed_edit.text().strip()
        seed = int(seed_text) if seed_text else None
        return name, self.group_size_spin.value(), order, seed

    def _request_commit(self) -> None:
        if self.task_controller.busy:
            return
        mode = self._current_mode()
        if mode == "group":
            try:
                name, size, order, seed = self._request_group_payload()
            except (ArithmeticError, TypeError, ValueError) as exc:
                self._set_error(str(exc))
                return
            self._revision += 1
            revision = self._revision
            self._pending_request = (revision, ("group", name))
            self._set_error("")
            self.task_controller.submit(
                revision,
                lambda: self._group_handler(name, size, order, seed),
            )
            return
        if mode == "assignment":
            try:
                raters, per_image, seed, column = self._validate_assignment()
            except (ArithmeticError, TypeError, ValueError) as exc:
                self._set_error(str(exc))
                return
            if self._assignment_handler is None:
                self._set_error("当前表格不能分配评分者")
                return
            self._revision += 1
            revision = self._revision
            self._pending_request = (revision, ("assignment", column))
            self._set_error("")
            self.task_controller.submit(
                revision,
                lambda: self._assignment_handler(raters, per_image, seed),
            )
            return
        try:
            request, _parsed, evaluation = self._evaluate_preview()
            evaluation.raise_for_errors()
        except (ArithmeticError, FormulaError, TypeError, ValueError) as exc:
            self._set_error(str(exc))
            return
        self._revision += 1
        revision = self._revision
        self._pending_request = (revision, ("formula", request))
        self._set_error("")
        self.task_controller.submit(
            revision,
            lambda: self._persist_column(request),
        )

    @Slot(int, object)
    def _handle_commit_result(self, revision: int, result: Any) -> None:
        pending = self._pending_request
        if pending is None or pending[0] != revision:
            return
        kind, payload = pending[1]
        if kind == "formula":
            name = payload.name
            if result != name:
                self._handle_commit_error(
                    revision,
                    TypeError("新增列保存任务没有返回正确的列名"),
                )
                return
        else:
            name = payload
            if kind == "assignment":
                if result != 1:
                    self._handle_commit_error(
                        revision,
                        TypeError("评分者分配任务没有返回成功结果"),
                    )
                    return
            elif result != name:
                self._handle_commit_error(
                    revision,
                    TypeError("编组任务没有返回正确的列名"),
                )
                return
        self._pending_request = None
        self.committed_column = name
        self.columnCommitted.emit(name)
        self.accept()

    @Slot(int, object)
    def _handle_commit_error(self, revision: int, error: object) -> None:
        if self._pending_request is None or self._pending_request[0] != revision:
            return
        self._pending_request = None
        self._set_error(str(error).strip() or type(error).__name__)

    @Slot(bool)
    def _set_busy(self, busy: bool) -> None:
        for control in (
            self.name_edit,
            self.mode_tabs,
            self.preview_button,
            self.generate_button,
            self.cancel_button,
        ):
            control.setEnabled(not busy)
        self.busyChanged.emit(bool(busy))

    def reject(self) -> None:
        if self.task_controller.busy:
            return
        super().reject()

    @Slot(str)
    def _formula_validation_changed(self, message: str) -> None:
        self._set_error(message)

    @Slot(str)
    def _set_error(self, message: str) -> None:
        self.error_label.setText(str(message))
        self.error_label.setVisible(bool(message))

    @staticmethod
    def _display_value(value: Any) -> str:
        if value is None:
            return ""
        if pd.api.types.is_scalar(value) and bool(pd.isna(value)):
            return ""
        return str(value)


__all__ = ["DerivedColumnDialog"]
