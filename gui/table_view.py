from __future__ import annotations
# === GUI i18n: 本文件用户可见文字中英文对照 ===
from gui.i18n import tr as _tr

_T = {
    "没有数据可显示":         {"zh": "没有数据可显示",       "en": "No data to display"},
    "数据表格 - EasyQC":      {"zh": "数据表格 - EasyQC",    "en": "Data Table - EasyQC"},
    "数据筛选":               {"zh": "数据筛选",             "en": "Filter & Sort"},
    "快捷操作":               {"zh": "快捷操作",             "en": "Quick Actions"},
    "过滤行:":                {"zh": "过滤行:",              "en": "Filter:"},
    "排序:":                  {"zh": "排序:",                "en": "Sort:"},
    "选择列:":                {"zh": "选择列:",              "en": "Select:"},
    "增加列:":                {"zh": "增加列:",              "en": "Derive:"},
    "删除列:":                {"zh": "删除列:",              "en": "Drop:"},
    "重命名:":                {"zh": "重命名:",              "en": "Rename:"},
    "错误":                   {"zh": "错误",                 "en": "Error"},
    "警告":                   {"zh": "警告",                 "en": "Warning"},
    "表达式错误":             {"zh": "表达式错误",           "en": "Expression error"},
    "表格转换执行失败":       {"zh": "表格转换执行失败",     "en": "Table transform failed"},
    "请输入筛选表达式或JSON操作": {"zh": "请输入筛选表达式或JSON操作", "en": "Please enter filter expression or JSON"},
    "是否保存结果？":         {"zh": "是否保存结果？",       "en": "Save results?"},
    "执行":                   {"zh": "执行",                 "en": "Apply"},
    "保存结果":               {"zh": "保存结果",             "en": "Save Results"},
    "插入模板":               {"zh": "插入模板",             "en": "Insert Template"},
    "处理前":                 {"zh": "处理前",               "en": "Show Before"},
    "处理后":                 {"zh": "处理后",               "en": "Show After"},
    "高级模式(JSON操作)":  {"zh": "高级模式(JSON操作)",  "en": "Advanced (JSON)"}
    ,"信息":                 {"zh": "信息",                 "en": "Info"}
    ,"取消":                   {"zh": "取消",                 "en": "Cancel"},
}


import json
import keyword
import subprocess
import sys
import tkinter as tk

from core.shorthand_filter import parse_shorthand, shorthand_to_string, parse_shorthand_string, ShorthandParseError
from pathlib import Path
from typing import Callable
from tkinter import messagebox, scrolledtext, ttk

import pandas as pd

from core.table_transform import TableTransformEngine, legacy_select_filter_to_operations
from gui.widgets import ScrolledTreeview
from utils.validators import validate_transform_operation


class TableView:
    def __init__(
        self,
        parent,
        table_transform: TableTransformEngine | None = None,
        on_ezqcid_right_click: Callable[[str, object], None] | None = None,
    ):
        self.parent = parent
        self.table_transform = table_transform or TableTransformEngine(max_rows=5000, max_columns=200)
        self.on_ezqcid_right_click = on_ezqcid_right_click
        self.window = None
        self.tree = None

    def show_df(self, df: pd.DataFrame):
        if df is None or df.empty:
            messagebox.showinfo(_tr(_T, "信息"), _tr(_T, "没有数据可显示"))
            return None

        self.window = tk.Toplevel(self.parent)
        self.window.title("数据表格 - EasyQC")
        self.window.geometry("900x700")
        frame = ttk.Frame(self.window)
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        tree_frame = ScrolledTreeview(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.tree = tree_frame.tree
        columns = self.populate_tree(df)
        self.bind_right_click(columns)

        ttk.Label(frame, text=f"总计: {len(df)} 行, {len(df.columns)} 列").pack(anchor=tk.W)
        self.window.bind("<Escape>", lambda event: self.window.destroy())
        self.window.bind("<Control-w>", lambda event: self.window.destroy())
        return self.window

    def populate_tree(self, df: pd.DataFrame) -> list[str]:
        priority_cols = ["ezqcid", "ezqcbatch"]
        existing_priority_cols = [col for col in priority_cols if col in df.columns]
        if existing_priority_cols:
            other_cols = [col for col in df.columns if col not in existing_priority_cols]
            df = df[existing_priority_cols + other_cols]

        columns = ["Index"] + list(df.columns)
        self.tree["columns"] = columns
        self.tree["show"] = "headings"
        for column in columns:
            self.tree.heading(column, text=column)
            self.tree.column(column, width=80 if column == "Index" else 120, minwidth=60, stretch=False, anchor="w")

        for row_num, (_, row) in enumerate(df.iterrows(), 1):
            values = ["" if pd.isna(value) else str(value) for value in row.values]
            self.tree.insert("", "end", values=[str(row_num)] + values)

        return columns

    def bind_right_click(self, columns: list[str]) -> None:
        if self.on_ezqcid_right_click is None or "ezqcid" not in columns:
            return

        ezqcid_index = columns.index("ezqcid")

        def show_right_menu(event):
            item = self.tree.identify_row(event.y)
            if not item:
                return
            values = self.tree.item(item, "values")
            if ezqcid_index < len(values):
                self.on_ezqcid_right_click(values[ezqcid_index], event)

        self.tree.bind("<Button-3>", show_right_menu)
        self.tree.bind("<Button-2>", show_right_menu)


class TableTransformDialog:
    def __init__(
        self,
        parent,
        table_transform: TableTransformEngine | None = None,
        data_manager=None,
    ):
        self.parent = parent
        self.table_transform = table_transform or TableTransformEngine(max_rows=5000, max_columns=200)
        self.data_manager = data_manager

    def parse_operations(self, query: str) -> list[dict]:
        query = query.strip()
        if not query.startswith(("[", "{")):
            legacy_operations = legacy_select_filter_to_operations(query)
            if legacy_operations is not None:
                return legacy_operations
            raise ValueError("请输入 JSON 结构化表格转换操作")

        payload = json.loads(query)
        operations = None
        if isinstance(payload, list):
            operations = payload
        elif isinstance(payload, dict):
            if isinstance(payload.get("operations"), list):
                operations = payload["operations"]
            elif payload.get("operation") or payload.get("type"):
                operations = [payload]
        if operations is None:
            raise ValueError("JSON转换操作必须是操作列表、包含 operations 的对象，或单个 operation 对象")
        invalid_operations = [operation for operation in operations if not validate_transform_operation(operation)]
        if invalid_operations:
            raise ValueError(f"JSON转换操作包含无效操作: {invalid_operations}")
        return operations

    def default_template(self, df: pd.DataFrame | None = None) -> str:
        columns = list(df.columns) if df is not None else []
        id_column = "ezqcid" if "ezqcid" in columns else (columns[0] if columns else "ezqcid")
        numeric_columns = [
            column
            for column in columns
            if pd.api.types.is_numeric_dtype(df[column]) and not pd.api.types.is_bool_dtype(df[column])
        ] if df is not None else ["score"]
        safe_numeric_columns = [
            column
            for column in numeric_columns
            if isinstance(column, str) and column.isidentifier() and not keyword.iskeyword(column)
        ]

        if safe_numeric_columns:
            value_column = safe_numeric_columns[0]
            derived_column = f"{value_column}_valid"
            operations = [
                {"operation": "derive_column", "name": derived_column, "expression": f"notna({value_column})"},
                {"operation": "filter_rows", "conditions": [{"column": derived_column, "operator": "==", "value": True}]},
                {"operation": "sort_rows", "sort_keys": [{"column": value_column, "ascending": False}]},
                {"operation": "select_columns", "columns": [id_column, value_column, derived_column], "include_rest": True},
            ]
        else:
            derived_column = "derived_flag"
            operations = [
                {"operation": "derive_column", "name": derived_column, "expression": "True"},
                {"operation": "filter_rows", "conditions": [{"column": derived_column, "operator": "==", "value": True}]},
                {"operation": "select_columns", "columns": [id_column, derived_column], "include_rest": True},
            ]
        return json.dumps({"operations": operations}, ensure_ascii=False, indent=2)

    def apply_operations(self, df: pd.DataFrame, operations: list[dict]) -> pd.DataFrame:
        return self.table_transform.apply(df, operations)

    def execute_query(self, df: pd.DataFrame, query: str) -> pd.DataFrame:
        query = query.strip()
        if not query:
            raise ValueError("请输入 JSON 结构化表格转换操作")

        operations = self.parse_operations(query)
        if self.data_manager is not None and hasattr(self.data_manager, "transform_table"):
            return self.data_manager.transform_table(df.copy(), operations)
        return self.apply_operations(df.copy(), operations)

    def open_filter_dialog(
        self,
        df: pd.DataFrame,
        select_filter: str | None = None,
        result_type=None,
        on_show_df: Callable[[pd.DataFrame], object] | None = None,
        on_save_result: Callable[[object, pd.DataFrame, str], object] | None = None,
        on_restore_source: Callable[[object, pd.DataFrame], object] | None = None,
        on_empty_result: Callable[[], object] | None = None,
    ):
        filter_dialog = tk.Toplevel(self.parent)
        filter_dialog.title(_tr(_T, "数据筛选"))
        filter_dialog.geometry("680x680")

        # Use a scrollable outer frame so content never gets clipped
        outer = ttk.Frame(filter_dialog)
        outer.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # --- Shorthand Entries (top, always visible) ---
        parsed_sf = parse_shorthand_string(select_filter) if select_filter else {}
        is_legacy_query = select_filter and not parsed_sf

        shorthand_frame = ttk.LabelFrame(outer, text=_tr(_T, "快捷操作"))
        shorthand_frame.pack(fill=tk.X, pady=(0, 8))

        entry_specs = [
            ("过滤行:", "filter"),
            ("排序:", "sort"),
            ("选择列:", "select"),
            ("增加列:", "derive"),
            ("删除列:", "drop"),
            ("重命名:", "rename"),
        ]
        entry_vars = {}
        for label_text, key in entry_specs:
            row = ttk.Frame(shorthand_frame)
            row.pack(fill=tk.X, padx=8, pady=3)
            ttk.Label(row, text=label_text, width=8).pack(side=tk.LEFT)
            var = tk.StringVar(value=parsed_sf.get(key, ""))
            entry_vars[key] = var
            ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(shorthand_frame, foreground="gray", justify=tk.LEFT,
                  text="过滤行: 表达式(如 age > 30)\n"
                       "排序: 列名 [asc|desc]   选择列/删除列: 逗号分隔列名\n"
                       "增加列: 新名 = 表达式   重命名: 旧名 -> 新名"
        ).pack(fill=tk.X, padx=8, pady=(5, 8))

        # --- Collapsible Advanced JSON section ---
        advanced_state = {"expanded": False}

        advanced_header = ttk.Frame(outer)
        advanced_header.pack(fill=tk.X, pady=(0, 4))

        def toggle_advanced():
            if advanced_state["expanded"]:
                advanced_body.pack_forget()
                toggle_btn.config(text="▶ " + _tr(_T, "高级模式(JSON操作)"))
                advanced_state["expanded"] = False
            else:
                advanced_body.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
                toggle_btn.config(text="▼ " + _tr(_T, "高级模式(JSON操作)"))
                advanced_state["expanded"] = True

        toggle_btn = ttk.Button(advanced_header, text="▶ 高级模式(JSON操作)", command=toggle_advanced)
        toggle_btn.pack(side=tk.LEFT)

        advanced_body = ttk.Frame(outer)
        # NOT packed initially (collapsed)

        query_text = scrolledtext.ScrolledText(advanced_body, wrap=tk.WORD, padx=5, pady=5,
                                                height=10)
        query_text.pack(fill=tk.BOTH, expand=True)
        if is_legacy_query:
            query_text.insert(tk.END, select_filter)
        # If legacy query, auto-expand
        if is_legacy_query:
            toggle_advanced()

        # --- Buttons (bottom, always visible) ---
        # Pack buttons FIRST with side=BOTTOM so they are always rendered at the
        # bottom regardless of the collapsible JSON section's expand state.
        button_frame = ttk.Frame(outer)
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))

        state = {"saved": False, "query": ""}

        def execute_query():
            sf_ops = parse_shorthand(
                filter_expr=entry_vars["filter"].get(),
                sort_expr=entry_vars["sort"].get(),
                select_expr=entry_vars["select"].get(),
                derive_expr=entry_vars["derive"].get(),
                drop_expr=entry_vars["drop"].get(),
                rename_expr=entry_vars["rename"].get(),
            )
            json_text = query_text.get("1.0", tk.END).strip()

            if sf_ops and not json_text:
                state["query"] = shorthand_to_string(
                    filter_expr=entry_vars["filter"].get() or None,
                    sort_expr=entry_vars["sort"].get() or None,
                    select_expr=entry_vars["select"].get() or None,
                    derive_expr=entry_vars["derive"].get() or None,
                    drop_expr=entry_vars["drop"].get() or None,
                    rename_expr=entry_vars["rename"].get() or None,
                )
                try:
                    return self.apply_operations(df.copy(), sf_ops)
                except ShorthandParseError as exc:
                    messagebox.showerror(_tr(_T, "错误"), _tr(_T, "表达式错误") + f": {exc}")
                    return None
                except Exception as exc:
                    messagebox.showerror(_tr(_T, "错误"), _tr(_T, "表格转换执行失败") + f": {str(exc)}")
                    return None

            if json_text:
                state["query"] = json_text
                try:
                    return self.execute_query(df, json_text)
                except Exception as exc:
                    messagebox.showerror("错误", f"表格转换执行失败: {str(exc)}")
                    return None

            messagebox.showwarning(_tr(_T, "警告"), _tr(_T, "请输入筛选表达式或JSON操作"))
            return None

        def execute():
            df_output = execute_query()
            if on_show_df is not None:
                on_show_df(df_output)

        def insert_template():
            if not advanced_state["expanded"]:
                toggle_advanced()
            query_text.insert(tk.END, self.default_template(df))

        def save_result():
            df_output = execute_query()
            if df_output is None or df_output.empty:
                if on_empty_result is not None:
                    on_empty_result()
                return
            if result_type:
                if on_save_result is not None:
                    on_save_result(result_type, df_output, state["query"])
            else:
                return df_output.copy()
            state["saved"] = True
            return None

        def cancel():
            filter_dialog.destroy()
            if on_restore_source is not None:
                return on_restore_source(result_type, df)
            return None

        def destroy():
            if not state["saved"]:
                result = messagebox.askyesno(_tr(_T, "警告"), _tr(_T, "是否保存结果？"))
                if result:
                    save_result()
                else:
                    cancel()
            try:
                if filter_dialog.winfo_exists():
                    filter_dialog.destroy()
            except tk.TclError:
                pass

        ttk.Button(button_frame, text=_tr(_T, "执行"), command=execute).pack(side=tk.LEFT, padx=3, ipady=4)
        ttk.Button(button_frame, text=_tr(_T, "保存结果"), command=save_result).pack(side=tk.LEFT, padx=3, ipady=4)
        ttk.Button(button_frame, text=_tr(_T, "插入模板"), command=insert_template).pack(side=tk.LEFT, padx=3, ipady=4)
        ttk.Button(button_frame, text=_tr(_T, "处理前"), command=lambda: on_show_df(df) if on_show_df else None).pack(side=tk.LEFT, padx=3, ipady=4)
        ttk.Button(button_frame, text=_tr(_T, "处理后"), command=execute).pack(side=tk.LEFT, padx=3, ipady=4)
        ttk.Button(button_frame, text=_tr(_T, "取消"), command=cancel).pack(side=tk.RIGHT, padx=3, ipady=4)

        filter_dialog.protocol("WM_DELETE_WINDOW", destroy)
        return filter_dialog


def open_qc_subprocess(project: str, module_name: str, rater: str, ezqcid: str) -> subprocess.Popen:
    project_root = Path(__file__).parent.parent
    return subprocess.Popen(
        [sys.executable, str(project_root / "easyqc.py"), project, module_name, rater, ezqcid],
        shell=False,
    )


__all__ = ["TableView", "TableTransformDialog", "open_qc_subprocess"]
