"""Unified, read-only table workspace with typed view controls.

The workspace has one primary contract: an in-memory ``DataFrame`` is rendered
through ``TableViewService`` and explicit view-state commands.  Its only
external side effect is an optional, user-triggered QC callback.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import re
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

import pandas as pd

from core.table_view_service import QcIdentityError, TableViewError, TableViewService
from gui.i18n import tr as _tr
from gui.table_widgets import TableRowReference, WindowedTable
from models.table_view_state import (
    ColumnKind,
    ColumnProfile,
    ColumnViewState,
    FilterCondition,
    SortRule,
    TableViewState,
)


_T = {
    "数据工作区": {"zh": "数据工作区", "en": "Data workspace"},
    "筛选": {"zh": "筛选", "en": "Filters"},
    "排序": {"zh": "排序", "en": "Sort"},
    "列": {"zh": "列", "en": "Columns"},
    "查找 ezqcid": {"zh": "查找 ezqcid", "en": "Find ezqcid"},
    "查找": {"zh": "查找", "en": "Find"},
    "打开 QC…": {"zh": "打开 QC…", "en": "Open QC…"},
    "撤销": {"zh": "撤销", "en": "Undo"},
    "重置视图": {"zh": "重置视图", "en": "Reset view"},
    "紧凑": {"zh": "紧凑", "en": "Compact"},
    "舒适": {"zh": "舒适", "en": "Comfortable"},
    "匹配全部条件": {"zh": "匹配全部条件", "en": "Match all conditions"},
    "添加条件": {"zh": "+ 添加条件", "en": "+ Add condition"},
    "应用": {"zh": "应用", "en": "Apply"},
    "取消": {"zh": "取消", "en": "Cancel"},
    "清除全部": {"zh": "清除全部", "en": "Clear all"},
    "升序": {"zh": "升序", "en": "Ascending"},
    "降序": {"zh": "降序", "en": "Descending"},
    "应用排序": {"zh": "应用排序", "en": "Apply sort"},
    "清除排序": {"zh": "清除排序", "en": "Clear sort"},
    "显示/隐藏": {"zh": "显示/隐藏", "en": "Show / hide"},
    "上移": {"zh": "上移", "en": "Up"},
    "下移": {"zh": "下移", "en": "Down"},
    "重置列": {"zh": "重置列", "en": "Reset columns"},
    "可见": {"zh": "可见", "en": "Visible"},
    "列名": {"zh": "列名", "en": "Column"},
    "上一页": {"zh": "上一页", "en": "Previous"},
    "下一页": {"zh": "下一页", "en": "Next"},
    "每页": {"zh": "每页", "en": "Rows per page"},
    "只读": {"zh": "只读", "en": "Read-only"},
    "没有匹配记录": {"zh": "没有匹配记录。表头和筛选条件仍然可用。", "en": "No matching records. Headers and filters remain available."},
    "所选记录不在当前视图": {"zh": "所选记录不在当前视图", "en": "Selected record is outside this view"},
    "未选择记录": {"zh": "未选择记录", "en": "No row selected"},
    "选择值": {"zh": "选择值", "en": "Select values"},
    "从": {"zh": "从", "en": "From"},
    "到": {"zh": "到", "en": "To"},
    "删除": {"zh": "删除", "en": "Remove"},
    "等于": {"zh": "等于", "en": "is"},
    "不等于": {"zh": "不等于", "en": "is not"},
    "包含": {"zh": "包含", "en": "contains"},
    "开头是": {"zh": "开头是", "en": "starts with"},
    "结尾是": {"zh": "结尾是", "en": "ends with"},
    "属于": {"zh": "属于任一", "en": "is any of"},
    "不属于": {"zh": "不属于", "en": "is none of"},
    "为空": {"zh": "为空", "en": "is missing"},
    "不为空": {"zh": "不为空", "en": "is not missing"},
    "大于": {"zh": "大于", "en": "is greater than"},
    "大于等于": {"zh": "大于等于", "en": "is at least"},
    "小于": {"zh": "小于", "en": "is less than"},
    "小于等于": {"zh": "小于等于", "en": "is at most"},
    "介于": {"zh": "介于", "en": "is between"},
    "没有可撤销的视图操作": {"zh": "没有可撤销的视图操作", "en": "No view action to undo"},
    "ezqcid 不能隐藏": {"zh": "ezqcid 是 QC 身份列，不能隐藏", "en": "ezqcid is the QC identity column and cannot be hidden"},
    "请输入 ezqcid": {"zh": "请输入 ezqcid", "en": "Enter an ezqcid"},
    "未找到 ezqcid": {"zh": "在已应用的结果中未找到 ezqcid", "en": "ezqcid was not found in the applied result"},
    "行计数": {"zh": "{matched} / {total} 行", "en": "{matched} / {total} rows"},
    "列计数": {"zh": "{visible} / {total} 列", "en": "{visible} / {total} columns"},
    "源位置": {"zh": "源位置 {position}", "en": "source {position}"},
    "列不存在": {"zh": "列不存在: {column}", "en": "Column does not exist: {column}"},
    "每页错误": {"zh": "每页行数必须大于零", "en": "Rows per page must be greater than zero"},
    "结果缺少 ezqcid": {"zh": "已应用的结果缺少 ezqcid 列", "en": "The applied result has no ezqcid column"},
    "QC 缺少身份列": {"zh": "表格缺少 QC 身份列 '{column}'", "en": "The table has no QC identity column '{column}'"},
    "QC 位置失效": {"zh": "所选记录位置已失效，请重新选择", "en": "The selected row is stale; select it again"},
    "QC 身份为空": {"zh": "所选记录的 {column} 为空，无法打开 QC", "en": "The selected row has an empty {column}; QC cannot be opened"},
    "QC 身份重复": {"zh": "{column} '{identity}' 在源表中不唯一，无法安全打开 QC", "en": "{column} '{identity}' is not unique in the source table; QC cannot be opened safely"},
    "数值无效": {"zh": "列 '{column}' 需要数值", "en": "Column '{column}' needs a number"},
    "布尔无效": {"zh": "列 '{column}' 需要 true/false", "en": "Column '{column}' needs true/false"},
    "日期无效": {"zh": "列 '{column}' 需要日期/时间", "en": "Column '{column}' needs a date/time value"},
    "筛选值为空": {"zh": "筛选列 '{column}' 的值不能为空", "en": "A filter value is required for column '{column}'"},
    "筛选列表为空": {"zh": "筛选列 '{column}' 的值列表不能为空", "en": "Select at least one value for column '{column}'"},
    "筛选区间无效": {"zh": "筛选列 '{column}' 的区间必须包含两个值", "en": "Enter both range bounds for column '{column}'"},
    "筛选无效": {"zh": "筛选条件无效", "en": "The filter condition is invalid"},
}


_OPERATOR_KEYS = {
    "==": "等于",
    "!=": "不等于",
    "contains": "包含",
    "startswith": "开头是",
    "endswith": "结尾是",
    "in": "属于",
    "not_in": "不属于",
    "isna": "为空",
    "notna": "不为空",
    ">": "大于",
    ">=": "大于等于",
    "<": "小于",
    "<=": "小于等于",
    "between": "介于",
}


def _operator_label(code: str) -> str:
    return _tr(_T, _OPERATOR_KEYS[code])


def _localized_service_error(error: Exception) -> str:
    """Translate known service errors without weakening their specificity."""

    message = str(error)
    if _tr(_T, "只读") != "Read-only":
        return message
    patterns = (
        (r"表格缺少 QC 身份列 '([^']+)'", "QC 缺少身份列", ("column",)),
        (r"所选记录位置已失效", "QC 位置失效", ()),
        (r"所选记录的 ([^ ]+) 为空", "QC 身份为空", ("column",)),
        (r"([^ ]+) '([^']+)' 在源表中不唯一", "QC 身份重复", ("column", "identity")),
        (r"列 '([^']+)' 需要数值", "数值无效", ("column",)),
        (r"列 '([^']+)' 需要 true/false", "布尔无效", ("column",)),
        (r"列 '([^']+)' 需要日期/时间", "日期无效", ("column",)),
        (r"筛选列 '([^']+)' 的值不能为空", "筛选值为空", ("column",)),
        (r"筛选列 '([^']+)' 的值列表不能为空", "筛选列表为空", ("column",)),
        (r"筛选列 '([^']+)' 的区间必须包含两个值", "筛选区间无效", ("column",)),
    )
    for pattern, key, names in patterns:
        match = re.search(pattern, message)
        if match:
            values = dict(zip(names, match.groups()))
            return _tr(_T, key).format(**values)
    return _tr(_T, "筛选无效")


class FilterConditionRow(ttk.Frame):
    """One natural-language ``column / operator / typed value`` editor row."""

    _BASE_OPERATORS = {
        ColumnKind.TEXT: ("==", "!=", "contains", "startswith", "endswith", "isna", "notna"),
        ColumnKind.NUMBER: ("==", "!=", ">", ">=", "<", "<=", "between", "isna", "notna"),
        ColumnKind.BOOLEAN: ("==", "!=", "isna", "notna"),
        ColumnKind.DATETIME: ("==", "!=", ">", ">=", "<", "<=", "between", "isna", "notna"),
    }

    def __init__(
        self,
        parent,
        profiles: tuple[ColumnProfile, ...],
        *,
        condition_id: str,
        on_remove: Callable[["FilterConditionRow"], None],
        condition: FilterCondition | None = None,
    ) -> None:
        super().__init__(parent, padding=(0, 4))
        self._profiles = {profile.name: profile for profile in profiles}
        self.condition_id = condition_id
        self._on_remove = on_remove
        self.column_var = tk.StringVar()
        self.operator_var = tk.StringVar()
        self.value_var = tk.StringVar()
        self.multi_entry_var = tk.StringVar()
        self.range_start_var = tk.StringVar()
        self.range_end_var = tk.StringVar()
        self.operator_codes: tuple[str, ...] = ()
        self.value_control_kind = "literal"

        self.column_combo = ttk.Combobox(
            self,
            textvariable=self.column_var,
            values=tuple(self._profiles),
            state="readonly",
            width=14,
        )
        self.operator_combo = ttk.Combobox(
            self,
            textvariable=self.operator_var,
            state="readonly",
            width=14,
        )
        self.value_host = ttk.Frame(self)
        self.value_host.grid_columnconfigure(0, weight=1)
        self.literal_entry = ttk.Entry(self.value_host, textvariable=self.value_var, width=18)
        self.choice_combo = ttk.Combobox(
            self.value_host,
            textvariable=self.value_var,
            state="readonly",
            width=17,
        )
        self.range_frame = ttk.Frame(self.value_host)
        ttk.Label(self.range_frame, text=_tr(_T, "从")).grid(row=0, column=0, padx=(0, 3))
        ttk.Entry(self.range_frame, textvariable=self.range_start_var, width=8).grid(row=0, column=1)
        ttk.Label(self.range_frame, text=_tr(_T, "到")).grid(row=0, column=2, padx=3)
        ttk.Entry(self.range_frame, textvariable=self.range_end_var, width=8).grid(row=0, column=3)
        self.multi_list = tk.Listbox(
            self.value_host,
            selectmode=tk.MULTIPLE,
            exportselection=False,
            height=3,
            width=20,
            takefocus=True,
        )
        self.multi_entry_frame = ttk.Frame(self.value_host)
        self.multi_entry_frame.grid_columnconfigure(0, weight=1)
        ttk.Entry(self.multi_entry_frame, textvariable=self.multi_entry_var, width=14).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Button(self.multi_entry_frame, text="+", width=3, command=self.add_multi_value).grid(
            row=0, column=1, padx=(3, 0)
        )
        ttk.Button(self.multi_entry_frame, text="−", width=3, command=self.remove_multi_value).grid(
            row=0, column=2, padx=(3, 0)
        )
        self.custom_multi_list = tk.Listbox(
            self.multi_entry_frame,
            selectmode=tk.EXTENDED,
            exportselection=False,
            height=3,
            width=20,
            takefocus=True,
        )
        self.custom_multi_list.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(3, 0))
        self.remove_button = ttk.Button(
            self,
            text=_tr(_T, "删除"),
            command=lambda: self._on_remove(self),
            width=7,
        )

        self.column_combo.grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.operator_combo.grid(row=0, column=1, sticky="ew", padx=5)
        self.remove_button.grid(row=0, column=2, padx=(5, 0), sticky="e")
        self.value_host.grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="nsew",
            pady=(5, 0),
        )
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self.column_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh_for_column())
        self.operator_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_value_control())

        if condition is None:
            first_column = next(iter(self._profiles), "")
            self.column_var.set(first_column)
            self.refresh_for_column()
        else:
            self.set_condition(condition)

    @property
    def profile(self) -> ColumnProfile:
        return self._profiles[self.column_var.get()]

    def refresh_for_column(self) -> None:
        """Refresh valid operators and the value control for the selected type."""

        profile = self.profile
        operators = list(self._BASE_OPERATORS[profile.kind])
        if profile.kind != ColumnKind.BOOLEAN:
            insertion = max(0, len(operators) - 2)
            operators[insertion:insertion] = ["in", "not_in"]
        self.operator_codes = tuple(operators)
        labels = tuple(_operator_label(code) for code in self.operator_codes)
        current_code = self._selected_operator_code()
        self.operator_combo.configure(values=labels)
        if current_code not in self.operator_codes:
            current_code = self.operator_codes[0]
        self.operator_var.set(_operator_label(current_code))
        self._refresh_value_control()

    def _selected_operator_code(self) -> str:
        selected = self.operator_var.get()
        for code in self.operator_codes:
            if _operator_label(code) == selected:
                return code
        return self.operator_codes[0] if self.operator_codes else "=="

    @property
    def operator_code(self) -> str:
        return self._selected_operator_code()

    def select_operator(self, code: str) -> None:
        """Select one type-valid operator by its internal typed code."""

        if code not in self.operator_codes:
            raise ValueError(f"Operator is not valid for this column: {code}")
        self.operator_var.set(_operator_label(code))
        self._refresh_value_control()

    def _refresh_value_control(self) -> None:
        for widget in (
            self.literal_entry,
            self.choice_combo,
            self.range_frame,
            self.multi_list,
            self.multi_entry_frame,
        ):
            widget.grid_remove()

        operator = self.operator_code
        profile = self.profile
        if operator in {"isna", "notna"}:
            self.value_control_kind = "none"
            return
        if operator == "between":
            self.value_control_kind = "range"
            self.range_frame.grid(row=0, column=0, sticky="w")
            return
        if operator in {"in", "not_in"}:
            if profile.values:
                self.value_control_kind = "multi"
                self.multi_list.delete(0, tk.END)
                for value in profile.values:
                    self.multi_list.insert(tk.END, str(value))
                self.multi_list.grid(row=0, column=0, sticky="ew")
            else:
                self.value_control_kind = "multi-entry"
                self.multi_entry_frame.grid(row=0, column=0, sticky="ew")
            return
        if profile.kind == ColumnKind.BOOLEAN or profile.values:
            self.value_control_kind = "choice"
            values = ("True", "False") if profile.kind == ColumnKind.BOOLEAN else tuple(str(value) for value in profile.values)
            self.choice_combo.configure(values=values)
            self.choice_combo.grid(row=0, column=0, sticky="ew")
            return
        self.value_control_kind = "literal"
        self.literal_entry.grid(row=0, column=0, sticky="ew")

    def set_condition(self, condition: FilterCondition) -> None:
        self.condition_id = condition.condition_id or self.condition_id
        self.column_var.set(condition.column)
        self.refresh_for_column()
        if condition.operator in self.operator_codes:
            self.operator_var.set(_operator_label(condition.operator))
        self._refresh_value_control()
        if condition.operator == "between" and condition.value is not None:
            values = tuple(condition.value) if not isinstance(condition.value, str) else tuple(condition.value.split(",", 1))
            if len(values) == 2:
                self.range_start_var.set(str(values[0]))
                self.range_end_var.set(str(values[1]))
        elif condition.operator in {"in", "not_in"} and condition.value is not None:
            selected = {str(value) for value in (condition.value if not isinstance(condition.value, str) else (condition.value,))}
            if self.value_control_kind == "multi":
                for index, value in enumerate(self.multi_list.get(0, tk.END)):
                    if value in selected:
                        self.multi_list.selection_set(index)
            else:
                self.custom_multi_list.delete(0, tk.END)
                for value in selected:
                    self.custom_multi_list.insert(tk.END, value)
        elif condition.value is not None:
            self.value_var.set(str(condition.value))

    def is_blank(self) -> bool:
        if self.operator_code in {"isna", "notna"}:
            return False
        if self.value_control_kind == "range":
            return not self.range_start_var.get().strip() and not self.range_end_var.get().strip()
        if self.value_control_kind == "multi":
            return not self.multi_list.curselection()
        if self.value_control_kind == "multi-entry":
            return not self.custom_multi_list.get(0, tk.END)
        return not self.value_var.get().strip()

    def condition(self) -> FilterCondition:
        operator = self.operator_code
        value: Any = None
        if self.value_control_kind == "range":
            value = (self.range_start_var.get(), self.range_end_var.get())
        elif self.value_control_kind == "multi":
            value = tuple(self.multi_list.get(index) for index in self.multi_list.curselection())
        elif self.value_control_kind == "multi-entry":
            value = tuple(self.custom_multi_list.get(0, tk.END))
        elif self.value_control_kind != "none":
            value = self.value_var.get()
        return FilterCondition(
            column=self.column_var.get(),
            operator=operator,
            value=value,
            condition_id=self.condition_id,
        )

    def add_multi_value(self) -> bool:
        """Add one literal to a high-cardinality membership picker."""

        value = self.multi_entry_var.get().strip()
        if not value:
            return False
        existing = set(self.custom_multi_list.get(0, tk.END))
        if value not in existing:
            self.custom_multi_list.insert(tk.END, value)
        self.multi_entry_var.set("")
        return True

    def remove_multi_value(self) -> bool:
        selection = self.custom_multi_list.curselection()
        if not selection:
            return False
        for index in reversed(selection):
            self.custom_multi_list.delete(index)
        return True


class TableWorkspace:
    """Compose one canonical table view state and its Tk controls."""

    def __init__(
        self,
        parent,
        source: pd.DataFrame,
        *,
        on_open_qc: Callable[[str, Any], None] | None = None,
        title: str | None = None,
    ) -> None:
        self.service = TableViewService(source)
        self.on_open_qc = on_open_qc
        self.initial_state = self.service.default_state()
        self.applied_state = self.initial_state
        self.draft_state: TableViewState | None = None
        self.result = self.service.apply_state(self.applied_state)
        self.row_window = self.service.get_window(self.result, 0)
        self.page_offset = 0
        self.selected_source_position: int | None = None
        self.selection_outside_view = False
        self._undo_state: TableViewState | None = None
        self._condition_sequence = 0
        self.filter_rows: list[FilterConditionRow] = []
        self.chip_texts: tuple[str, ...] = ()
        self.inspector_visible = True

        self.window = tk.Toplevel(parent)
        self.window.title(title or _tr(_T, "数据工作区"))
        self.window.geometry("1180x760")
        self.window.minsize(820, 520)
        self.window.resizable(True, True)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self._tree_style = f"TW{id(self)}.Treeview"
        self._configure_styles()
        self._create_variables()
        self._build_layout()
        self.begin_filter_edit()
        self._bind_keys()
        self._render_result()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.window)
        style.configure("Workspace.Toolbar.TFrame", padding=(10, 8))
        style.configure("Workspace.Status.TLabel", foreground="#52606d", padding=(6, 4))
        style.configure("Workspace.Error.TLabel", foreground="#b42318", padding=(6, 2))
        style.configure("Workspace.Primary.TButton", padding=(10, 5))
        style.configure("Workspace.Chip.TButton", padding=(7, 3))
        style.configure(self._tree_style, rowheight=24)

    def _create_variables(self) -> None:
        self.find_var = tk.StringVar()
        self.density_var = tk.StringVar(value=_tr(_T, "紧凑"))
        self.page_size_var = tk.StringVar(value=str(self.applied_state.page_size))
        self.row_count_var = tk.StringVar()
        self.column_count_var = tk.StringVar()
        self.visible_range_var = tk.StringVar()
        self.sort_status_var = tk.StringVar()
        self.selection_status_var = tk.StringVar(value=_tr(_T, "未选择记录"))
        self.filter_error_var = tk.StringVar()
        self.action_error_var = tk.StringVar()
        self.sort_column_var = tk.StringVar(value=self.applied_state.columns.order[0] if self.applied_state.columns.order else "")
        self.sort_direction_var = tk.StringVar(value=_tr(_T, "升序"))

    def _build_layout(self) -> None:
        self.window.grid_rowconfigure(2, weight=1)
        self.window.grid_columnconfigure(0, weight=1)

        self.toolbar = ttk.Frame(self.window, style="Workspace.Toolbar.TFrame")
        self.toolbar.grid(row=0, column=0, sticky="ew")
        self.toolbar.grid_columnconfigure(7, weight=1)

        self.filter_button = ttk.Button(self.toolbar, text=_tr(_T, "筛选"), command=self.begin_filter_edit)
        self.sort_button = ttk.Button(self.toolbar, text=_tr(_T, "排序"), command=lambda: self.open_inspector("sort"))
        self.columns_button = ttk.Button(self.toolbar, text=_tr(_T, "列"), command=lambda: self.open_inspector("columns"))
        self.undo_button = ttk.Button(self.toolbar, text=_tr(_T, "撤销"), command=self.undo_view_state)
        self.reset_button = ttk.Button(self.toolbar, text=_tr(_T, "重置视图"), command=self.reset_view_state)
        for index, widget in enumerate((self.filter_button, self.sort_button, self.columns_button, self.undo_button, self.reset_button)):
            widget.grid(row=0, column=index, padx=(0, 6))

        ttk.Label(self.toolbar, text=_tr(_T, "查找 ezqcid")).grid(row=0, column=7, sticky="e", padx=(12, 4))
        self.find_entry = ttk.Entry(self.toolbar, textvariable=self.find_var, width=18)
        self.find_entry.grid(row=0, column=8, sticky="e")
        ttk.Button(self.toolbar, text=_tr(_T, "查找"), command=self.find_identity_exact).grid(row=0, column=9, padx=(4, 8))
        self.density_combo = ttk.Combobox(
            self.toolbar,
            textvariable=self.density_var,
            values=(_tr(_T, "紧凑"), _tr(_T, "舒适")),
            state="readonly",
            width=12,
        )
        self.density_combo.grid(row=0, column=10, padx=(0, 8))
        self.density_combo.bind("<<ComboboxSelected>>", lambda _event: self.set_density_from_control())
        self.open_qc_button = ttk.Button(
            self.toolbar,
            text=_tr(_T, "打开 QC…"),
            command=self.open_selected_qc,
            style="Workspace.Primary.TButton",
        )
        self.open_qc_button.grid(row=0, column=11)

        self.chips_frame = ttk.Frame(self.window, padding=(10, 2, 10, 7))
        self.chips_frame.grid(row=1, column=0, sticky="ew")

        self.body = ttk.Frame(self.window, padding=(10, 0, 10, 0))
        self.body.grid(row=2, column=0, sticky="nsew")
        self.body.grid_rowconfigure(1, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        self.empty_frame = ttk.Frame(self.body, padding=(8, 5))
        ttk.Label(self.empty_frame, text=_tr(_T, "没有匹配记录")).pack(side=tk.LEFT)
        ttk.Button(self.empty_frame, text=_tr(_T, "清除全部"), command=self.clear_filters).pack(side=tk.LEFT, padx=6)
        ttk.Button(self.empty_frame, text=_tr(_T, "撤销"), command=self.undo_view_state).pack(side=tk.LEFT)
        self.empty_frame.grid(row=0, column=0, sticky="ew")

        self.table = WindowedTable(
            self.body,
            on_selection=self._on_table_selection,
            on_activate=lambda _reference: self.open_selected_qc(),
            on_context=self._open_context_qc,
            on_header_sort=self.on_header_sort,
            tree_style=self._tree_style,
        )
        self.table.grid(row=1, column=0, sticky="nsew")

        self.inspector = ttk.Notebook(self.body, width=360)
        self.inspector.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(10, 0))
        self.filter_panel = ttk.Frame(self.inspector, padding=10)
        self.sort_panel = ttk.Frame(self.inspector, padding=10)
        self.columns_panel = ttk.Frame(self.inspector, padding=10)
        self.inspector.add(self.filter_panel, text=_tr(_T, "筛选"))
        self.inspector.add(self.sort_panel, text=_tr(_T, "排序"))
        self.inspector.add(self.columns_panel, text=_tr(_T, "列"))
        self._build_filter_panel()
        self._build_sort_panel()
        self._build_columns_panel()

        self.status = ttk.Frame(self.window, padding=(10, 5))
        self.status.grid(row=3, column=0, sticky="ew")
        self.status.grid_columnconfigure(5, weight=1)
        ttk.Label(self.status, textvariable=self.row_count_var, style="Workspace.Status.TLabel").grid(row=0, column=0)
        ttk.Label(self.status, textvariable=self.column_count_var, style="Workspace.Status.TLabel").grid(row=0, column=1)
        ttk.Label(self.status, textvariable=self.visible_range_var, style="Workspace.Status.TLabel").grid(row=0, column=2)
        ttk.Label(self.status, textvariable=self.sort_status_var, style="Workspace.Status.TLabel").grid(row=0, column=3)
        ttk.Label(self.status, text=_tr(_T, "只读"), style="Workspace.Status.TLabel").grid(row=0, column=4)
        ttk.Label(self.status, textvariable=self.selection_status_var, style="Workspace.Status.TLabel").grid(row=0, column=5, sticky="w")
        ttk.Label(self.status, text=_tr(_T, "每页")).grid(row=0, column=6, padx=(8, 3))
        self.page_size_combo = ttk.Combobox(
            self.status,
            textvariable=self.page_size_var,
            values=("25", "50", "100", "200", "500"),
            state="readonly",
            width=5,
        )
        self.page_size_combo.grid(row=0, column=7)
        self.page_size_combo.bind("<<ComboboxSelected>>", lambda _event: self.set_page_size(int(self.page_size_var.get())))
        self.previous_button = ttk.Button(self.status, text=_tr(_T, "上一页"), command=self.previous_page)
        self.next_button = ttk.Button(self.status, text=_tr(_T, "下一页"), command=self.next_page)
        self.previous_button.grid(row=0, column=8, padx=(8, 3))
        self.next_button.grid(row=0, column=9)
        self.error_label = ttk.Label(self.window, textvariable=self.action_error_var, style="Workspace.Error.TLabel")
        self.error_label.grid(row=4, column=0, sticky="ew", padx=10)

    def _build_filter_panel(self) -> None:
        self.filter_panel.grid_columnconfigure(0, weight=1)
        ttk.Label(self.filter_panel, text=_tr(_T, "匹配全部条件")).grid(row=0, column=0, sticky="w")
        self.filter_rows_frame = ttk.Frame(self.filter_panel)
        self.filter_rows_frame.grid(row=1, column=0, sticky="nsew", pady=6)
        self.filter_rows_frame.grid_columnconfigure(0, weight=1)
        buttons = ttk.Frame(self.filter_panel)
        buttons.grid(row=2, column=0, sticky="ew")
        ttk.Button(buttons, text=_tr(_T, "添加条件"), command=self.add_filter_row).pack(side=tk.LEFT)
        ttk.Button(buttons, text=_tr(_T, "清除全部"), command=self.clear_filter_rows).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text=_tr(_T, "取消"), command=self.cancel_filter_draft).pack(side=tk.RIGHT)
        ttk.Button(buttons, text=_tr(_T, "应用"), command=self.apply_filter_draft, style="Workspace.Primary.TButton").pack(side=tk.RIGHT, padx=5)
        ttk.Label(self.filter_panel, textvariable=self.filter_error_var, style="Workspace.Error.TLabel", wraplength=320).grid(row=3, column=0, sticky="ew")

    def _build_sort_panel(self) -> None:
        ttk.Label(self.sort_panel, text=_tr(_T, "列名")).grid(row=0, column=0, sticky="w")
        self.sort_column_combo = ttk.Combobox(
            self.sort_panel,
            textvariable=self.sort_column_var,
            values=self.initial_state.columns.order,
            state="readonly",
            width=24,
        )
        self.sort_column_combo.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        self.sort_direction_combo = ttk.Combobox(
            self.sort_panel,
            textvariable=self.sort_direction_var,
            values=(_tr(_T, "升序"), _tr(_T, "降序")),
            state="readonly",
            width=24,
        )
        self.sort_direction_combo.grid(row=2, column=0, sticky="ew")
        ttk.Button(self.sort_panel, text=_tr(_T, "应用排序"), command=self.apply_sort_control).grid(row=3, column=0, sticky="ew", pady=(10, 4))
        ttk.Button(self.sort_panel, text=_tr(_T, "清除排序"), command=self.clear_sort).grid(row=4, column=0, sticky="ew")
        self.sort_panel.grid_columnconfigure(0, weight=1)

    def _build_columns_panel(self) -> None:
        self.columns_panel.grid_rowconfigure(0, weight=1)
        self.columns_panel.grid_columnconfigure(0, weight=1)
        self.columns_tree = ttk.Treeview(
            self.columns_panel,
            columns=("visible", "column"),
            show="headings",
            selectmode="browse",
            height=16,
        )
        self.columns_tree.heading("visible", text=_tr(_T, "可见"))
        self.columns_tree.heading("column", text=_tr(_T, "列名"))
        self.columns_tree.column("visible", width=65, stretch=False, anchor=tk.CENTER)
        self.columns_tree.column("column", width=220, stretch=True, anchor=tk.W)
        self.columns_tree.grid(row=0, column=0, sticky="nsew")
        button_frame = ttk.Frame(self.columns_panel)
        button_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(button_frame, text=_tr(_T, "显示/隐藏"), command=self.toggle_selected_column).pack(side=tk.LEFT)
        ttk.Button(button_frame, text=_tr(_T, "上移"), command=lambda: self.move_selected_column(-1)).pack(side=tk.LEFT, padx=4)
        ttk.Button(button_frame, text=_tr(_T, "下移"), command=lambda: self.move_selected_column(1)).pack(side=tk.LEFT)
        ttk.Button(button_frame, text=_tr(_T, "重置列"), command=self.reset_columns).pack(side=tk.RIGHT)

    def _bind_keys(self) -> None:
        self.window.bind("<Control-f>", self._focus_find, add="+")
        self.window.bind("<Escape>", self._handle_escape, add="+")

    def _focus_find(self, _event=None) -> str:
        self.find_entry.focus_set()
        self.find_entry.selection_range(0, tk.END)
        return "break"

    def _handle_escape(self, _event=None) -> str:
        if self.draft_state is not None:
            self.cancel_filter_draft()
        elif self.inspector_visible:
            self.close_inspector()
        else:
            self.close()
        return "break"

    def open_inspector(self, panel: str = "filter") -> None:
        tabs = {"filter": self.filter_panel, "sort": self.sort_panel, "columns": self.columns_panel}
        if not self.inspector_visible:
            self.inspector.grid()
            self.inspector_visible = True
        self.inspector.select(tabs[panel])

    def close_inspector(self) -> None:
        self.inspector.grid_remove()
        self.inspector_visible = False

    def close(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()

    def begin_filter_edit(self) -> None:
        self.open_inspector("filter")
        if self.draft_state is not None:
            return
        self.draft_state = deepcopy(self.applied_state)
        self.filter_error_var.set("")
        self._rebuild_filter_rows(self.draft_state.conditions)

    def set_filter_draft(self, conditions: tuple[FilterCondition, ...]) -> None:
        if self.draft_state is None:
            self.begin_filter_edit()
        normalized_ids = self._ensure_condition_ids(conditions)
        self.draft_state = replace(self.draft_state, conditions=normalized_ids)
        self._rebuild_filter_rows(normalized_ids)

    def _ensure_condition_ids(self, conditions: tuple[FilterCondition, ...]) -> tuple[FilterCondition, ...]:
        prepared = []
        for condition in conditions:
            if condition.condition_id:
                prepared.append(condition)
                continue
            self._condition_sequence += 1
            prepared.append(replace(condition, condition_id=f"filter-{self._condition_sequence}"))
        return tuple(prepared)

    def _next_condition_id(self) -> str:
        self._condition_sequence += 1
        return f"filter-{self._condition_sequence}"

    def _rebuild_filter_rows(self, conditions: tuple[FilterCondition, ...]) -> None:
        for row in self.filter_rows:
            row.destroy()
        self.filter_rows.clear()
        if conditions:
            for condition in conditions:
                self.add_filter_row(condition)
        else:
            self.add_filter_row()

    def add_filter_row(self, condition: FilterCondition | None = None) -> FilterConditionRow:
        row = FilterConditionRow(
            self.filter_rows_frame,
            self.service.profiles,
            condition_id=condition.condition_id if condition and condition.condition_id else self._next_condition_id(),
            on_remove=self.remove_filter_row,
            condition=condition,
        )
        row.grid(row=len(self.filter_rows), column=0, sticky="ew")
        self.filter_rows.append(row)
        return row

    def remove_filter_row(self, row: FilterConditionRow) -> None:
        if row not in self.filter_rows:
            return
        self.filter_rows.remove(row)
        row.destroy()
        for index, remaining in enumerate(self.filter_rows):
            remaining.grid_configure(row=index)
        if not self.filter_rows:
            self.add_filter_row()

    def clear_filter_rows(self) -> None:
        if self.draft_state is None:
            self.begin_filter_edit()
        self.draft_state = replace(self.draft_state, conditions=())
        self._rebuild_filter_rows(())
        self.filter_error_var.set("")

    def _collect_filter_rows(self) -> tuple[FilterCondition, ...]:
        if len(self.filter_rows) == 1 and not (self.draft_state and self.draft_state.conditions) and self.filter_rows[0].is_blank():
            return ()
        return tuple(row.condition() for row in self.filter_rows)

    def apply_filter_draft(self) -> bool:
        if self.draft_state is None:
            # The inspector stays visible after Apply so users can refine the
            # displayed controls directly without reopening an identical pane.
            self.draft_state = deepcopy(self.applied_state)
        conditions = self._ensure_condition_ids(self._collect_filter_rows())
        self.draft_state = replace(self.draft_state, conditions=conditions)
        base = self._state_with_current_widths()
        candidate = replace(base, conditions=conditions)
        if not self._commit_state(candidate, reset_page=True):
            self.filter_error_var.set(self.action_error_var.get())
            return False
        self.draft_state = None
        self.filter_error_var.set("")
        return True

    def cancel_filter_draft(self) -> None:
        self.draft_state = None
        self.filter_error_var.set("")
        for row in self.filter_rows:
            row.destroy()
        self.filter_rows.clear()

    def remove_applied_condition(self, condition_id: str) -> bool:
        conditions = tuple(condition for condition in self.applied_state.conditions if condition.condition_id != condition_id)
        if len(conditions) == len(self.applied_state.conditions):
            return False
        return self._commit_state(replace(self._state_with_current_widths(), conditions=conditions), reset_page=True)

    def clear_filters(self) -> bool:
        if not self.applied_state.conditions:
            return False
        return self._commit_state(replace(self._state_with_current_widths(), conditions=()), reset_page=True)

    def on_header_sort(self, column: str) -> bool:
        current = self.applied_state.sort_rules[0] if self.applied_state.sort_rules else None
        if current is None or current.column != column:
            rules = (SortRule(column, True),)
        elif current.ascending:
            rules = (SortRule(column, False),)
        else:
            rules = ()
        return self._commit_state(replace(self._state_with_current_widths(), sort_rules=rules), reset_page=True)

    def apply_sort_control(self) -> bool:
        column = self.sort_column_var.get()
        ascending = self.sort_direction_var.get() == _tr(_T, "升序")
        return self._commit_state(
            replace(self._state_with_current_widths(), sort_rules=(SortRule(column, ascending),)),
            reset_page=True,
        )

    def clear_sort(self) -> bool:
        if not self.applied_state.sort_rules:
            return False
        return self._commit_state(replace(self._state_with_current_widths(), sort_rules=()), reset_page=True)

    def set_column_visibility(self, column: str, visible: bool) -> bool:
        if column not in self.applied_state.columns.order:
            self.action_error_var.set(_tr(_T, "列不存在").format(column=column))
            return False
        if column == "ezqcid" and not visible:
            self.action_error_var.set(_tr(_T, "ezqcid 不能隐藏"))
            return False
        base = self._state_with_current_widths()
        hidden = set(base.columns.hidden)
        hidden.discard(column) if visible else hidden.add(column)
        columns = replace(base.columns, hidden=tuple(name for name in base.columns.order if name in hidden))
        return self._commit_state(replace(base, columns=columns), reset_page=False)

    def move_column(self, column: str, delta: int) -> bool:
        base = self._state_with_current_widths()
        order = list(base.columns.order)
        if column not in order:
            return False
        current = order.index(column)
        target = max(0, min(len(order) - 1, current + delta))
        if target == current:
            return False
        order.insert(target, order.pop(current))
        columns = replace(base.columns, order=tuple(order))
        return self._commit_state(replace(base, columns=columns), reset_page=False)

    def _selected_column_name(self) -> str | None:
        selection = self.columns_tree.selection()
        if not selection:
            return None
        values = self.columns_tree.item(selection[0], "values")
        return str(values[1]) if len(values) > 1 else None

    def toggle_selected_column(self) -> bool:
        column = self._selected_column_name()
        if column is None:
            return False
        return self.set_column_visibility(column, column in self.applied_state.columns.hidden)

    def move_selected_column(self, delta: int) -> bool:
        column = self._selected_column_name()
        return self.move_column(column, delta) if column else False

    def reset_columns(self) -> bool:
        base = self._state_with_current_widths()
        return self._commit_state(replace(base, columns=self.initial_state.columns), reset_page=False)

    def set_page_size(self, page_size: int) -> bool:
        if page_size <= 0:
            self.action_error_var.set(_tr(_T, "每页错误"))
            return False
        return self._commit_state(replace(self._state_with_current_widths(), page_size=int(page_size)), reset_page=True)

    def previous_page(self) -> bool:
        if self.page_offset <= 0:
            return False
        self._remember_current_widths()
        self.page_offset = max(0, self.page_offset - self.applied_state.page_size)
        self._render_result()
        return True

    def next_page(self) -> bool:
        next_offset = self.page_offset + self.applied_state.page_size
        if next_offset >= self.result.matched_total:
            return False
        self._remember_current_widths()
        self.page_offset = next_offset
        self._render_result()
        return True

    def set_density_from_control(self) -> bool:
        density = "compact" if self.density_var.get() == _tr(_T, "紧凑") else "comfortable"
        return self.set_density(density)

    def set_density(self, density: str) -> bool:
        return self._commit_state(replace(self._state_with_current_widths(), density=density), reset_page=False)

    def undo_view_state(self) -> bool:
        if self._undo_state is None:
            self.action_error_var.set(_tr(_T, "没有可撤销的视图操作"))
            return False
        previous = self._undo_state
        if not self._commit_state(previous, reset_page=True, record_undo=False):
            return False
        self._undo_state = None
        self._update_action_states()
        return True

    def reset_view_state(self) -> bool:
        candidate = replace(self.initial_state, revision=self.applied_state.revision)
        return self._commit_state(candidate, reset_page=True)

    def find_identity_exact(self) -> bool:
        query = self.find_var.get().strip()
        if not query:
            self.action_error_var.set(_tr(_T, "请输入 ezqcid"))
            return False
        if "ezqcid" not in self.result.dataframe.columns:
            self.action_error_var.set(_tr(_T, "结果缺少 ezqcid"))
            return False
        identities = self.result.dataframe["ezqcid"].map(lambda value: "" if pd.isna(value) else str(value).strip())
        matches = [int(index) for index, matched in enumerate(identities.eq(query).tolist()) if matched]
        if not matches:
            self.action_error_var.set(_tr(_T, "未找到 ezqcid"))
            return False
        result_position = matches[0]
        self._remember_current_widths()
        self.selected_source_position = self.result.source_positions[result_position]
        self.selection_outside_view = False
        self.page_offset = (result_position // self.applied_state.page_size) * self.applied_state.page_size
        self.action_error_var.set("")
        self._render_result()
        return True

    def select_source_position(self, source_position: int) -> bool:
        try:
            result_position = self.result.source_positions.index(int(source_position))
        except ValueError:
            self.selected_source_position = int(source_position)
            self.selection_outside_view = True
            self.table.clear_selection()
            self._update_action_states()
            return False
        self._remember_current_widths()
        self.selected_source_position = int(source_position)
        self.selection_outside_view = False
        self.page_offset = (result_position // self.applied_state.page_size) * self.applied_state.page_size
        self._render_result()
        return self.table.selected_row is not None

    def _on_table_selection(self, reference: TableRowReference) -> None:
        self.selected_source_position = reference.source_position
        self.selection_outside_view = False
        self.action_error_var.set("")
        self._update_action_states()

    def _open_context_qc(self, _reference: TableRowReference, event: Any) -> None:
        self.open_selected_qc(anchor=event)

    def open_selected_qc(self, anchor: Any | None = None) -> bool:
        reference = self.table.selected_row
        if reference is None or self.selected_source_position != reference.source_position:
            self.action_error_var.set(_tr(_T, "所选记录不在当前视图") if self.selected_source_position is not None else _tr(_T, "未选择记录"))
            self._update_action_states()
            return False
        try:
            identity = self.service.validate_qc_identity(self.result, reference.result_position)
        except QcIdentityError as exc:
            self.action_error_var.set(_localized_service_error(exc))
            return False
        if self.on_open_qc is None:
            return False
        self.action_error_var.set("")
        self.on_open_qc(identity, anchor or self.open_qc_button)
        return True

    def _state_with_current_widths(self) -> TableViewState:
        if not hasattr(self, "table"):
            return self.applied_state
        rendered = dict(self.table.current_widths())
        if not rendered:
            return self.applied_state
        widths = dict(self.applied_state.columns.widths)
        widths.update(rendered)
        ordered_widths = tuple((column, widths[column]) for column in self.applied_state.columns.order if column in widths)
        columns = replace(self.applied_state.columns, widths=ordered_widths)
        return replace(self.applied_state, columns=columns)

    def _remember_current_widths(self) -> None:
        self.applied_state = self._state_with_current_widths()
        self.result.state = self.applied_state

    def _commit_state(
        self,
        candidate: TableViewState,
        *,
        reset_page: bool,
        record_undo: bool = True,
    ) -> bool:
        next_state = replace(candidate, revision=self.applied_state.revision + 1)
        previous = self._state_with_current_widths() if record_undo else self.applied_state
        try:
            next_result = self.service.apply_state(next_state)
        except TableViewError as exc:
            self.action_error_var.set(_localized_service_error(exc))
            return False
        if record_undo:
            self._undo_state = previous
        self.applied_state = next_result.state
        self.result = next_result
        if reset_page:
            self.page_offset = 0
        self.action_error_var.set("")
        self._render_result()
        return True

    def _render_result(self) -> None:
        self.row_window = self.service.get_window(
            self.result,
            self.page_offset,
            self.applied_state.page_size,
        )
        self.page_offset = self.row_window.offset
        sort_rule = self.applied_state.sort_rules[0] if self.applied_state.sort_rules else None
        self.table.render_window(self.row_window, self.applied_state.columns, sort_rule)

        if self.selected_source_position is None:
            self.selection_outside_view = False
        elif self.selected_source_position not in self.result.source_positions:
            self.selection_outside_view = True
            self.table.clear_selection()
        else:
            self.selection_outside_view = False
            self.table.select_source_position(self.selected_source_position)

        style = ttk.Style(self.window)
        style.configure(self._tree_style, rowheight=24 if self.applied_state.density == "compact" else 32)
        self.density_var.set(_tr(_T, "紧凑") if self.applied_state.density == "compact" else _tr(_T, "舒适"))
        self.page_size_var.set(str(self.applied_state.page_size))
        self._render_chips()
        self._render_columns_manager()
        self._update_status()
        self._update_action_states()

    def _render_chips(self) -> None:
        for child in self.chips_frame.winfo_children():
            child.destroy()
        texts = []
        for condition in self.applied_state.conditions:
            text = self._condition_summary(condition)
            texts.append(text)
            ttk.Button(
                self.chips_frame,
                text=f"{text}  ×",
                command=lambda condition_id=condition.condition_id: self.remove_applied_condition(condition_id),
                style="Workspace.Chip.TButton",
            ).pack(side=tk.LEFT, padx=(0, 5))
        if self.applied_state.conditions:
            ttk.Button(self.chips_frame, text=_tr(_T, "清除全部"), command=self.clear_filters).pack(side=tk.LEFT)
        self.chip_texts = tuple(texts)

    @staticmethod
    def _display_filter_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (tuple, list)):
            return ", ".join(str(item) for item in value)
        return str(value)

    def _condition_summary(self, condition: FilterCondition) -> str:
        label = _operator_label(condition.operator)
        value = self._display_filter_value(condition.value)
        return f"{condition.column} {label}{(' ' + value) if value else ''}"

    def _render_columns_manager(self) -> None:
        previous_column = self._selected_column_name() if hasattr(self, "columns_tree") else None
        children = self.columns_tree.get_children()
        if children:
            self.columns_tree.delete(*children)
        for index, column in enumerate(self.applied_state.columns.order):
            visible = column not in self.applied_state.columns.hidden
            iid = f"column-{index}"
            self.columns_tree.insert("", "end", iid=iid, values=("●" if visible else "○", column))
            if column == previous_column:
                self.columns_tree.selection_set(iid)

    def _update_status(self) -> None:
        matched = self.result.matched_total
        total = self.result.source_total
        visible_columns = len(self.applied_state.columns.visible_columns)
        total_columns = len(self.applied_state.columns.order)
        self.row_count_var.set(_tr(_T, "行计数").format(matched=f"{matched:,}", total=f"{total:,}"))
        self.column_count_var.set(_tr(_T, "列计数").format(visible=visible_columns, total=total_columns))
        self.filter_button.configure(text=f"{_tr(_T, '筛选')} {len(self.applied_state.conditions)}")
        self.sort_button.configure(text=f"{_tr(_T, '排序')} {len(self.applied_state.sort_rules)}")
        self.columns_button.configure(text=f"{_tr(_T, '列')} {visible_columns}/{total_columns}")
        if matched:
            start = self.page_offset + 1
            end = min(matched, self.page_offset + self.applied_state.page_size)
        else:
            start = end = 0
        self.visible_range_var.set(f"{start:,}–{end:,}")
        if self.applied_state.sort_rules:
            rule = self.applied_state.sort_rules[0]
            self.sort_status_var.set(f"{rule.column} {'↑' if rule.ascending else '↓'}")
            self.sort_column_var.set(rule.column)
            self.sort_direction_var.set(_tr(_T, "升序") if rule.ascending else _tr(_T, "降序"))
        else:
            self.sort_status_var.set("")
        if self.selection_outside_view:
            self.selection_status_var.set(_tr(_T, "所选记录不在当前视图"))
        elif self.table.selected_row is not None:
            source_text = _tr(_T, "源位置").format(position=self.table.selected_row.source_position)
            self.selection_status_var.set(f"ezqcid · {source_text}")
        else:
            self.selection_status_var.set(_tr(_T, "未选择记录"))
        if matched == 0:
            self.empty_frame.grid()
        else:
            self.empty_frame.grid_remove()

    def _update_action_states(self) -> None:
        can_open = self.on_open_qc is not None and self.table.selected_row is not None and not self.selection_outside_view
        self.open_qc_button.state(["!disabled"] if can_open else ["disabled"])
        self.undo_button.state(["!disabled"] if self._undo_state is not None else ["disabled"])
        self.previous_button.state(["!disabled"] if self.page_offset > 0 else ["disabled"])
        has_next = self.page_offset + self.applied_state.page_size < self.result.matched_total
        self.next_button.state(["!disabled"] if has_next else ["disabled"])


__all__ = ["FilterConditionRow", "TableWorkspace"]
