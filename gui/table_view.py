from __future__ import annotations

import json
import keyword
import subprocess
import sys
import tkinter as tk
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
            messagebox.showinfo("信息", "没有数据可显示")
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
        filter_dialog.title("数据过滤")
        filter_dialog.geometry("400x600")

        main_frame = ttk.Frame(filter_dialog)
        main_frame.place(x=10, y=10, width=380, height=150)

        query_label = ttk.Label(main_frame, text="JSON结构化表格转换操作:")
        query_label.place(x=0, y=0)

        text_container = ttk.Frame(main_frame)
        text_container.place(x=0, y=30, width=380, height=120)

        query_text = scrolledtext.ScrolledText(text_container, wrap=tk.WORD, padx=5, pady=5)
        query_text.pack(fill=tk.BOTH, expand=True)
        if select_filter:
            query_text.insert(tk.END, select_filter)
        query_text.tag_configure("left", justify="left")
        query_text.tag_add("left", "1.0", "end")

        button_frame = ttk.Frame(filter_dialog)
        button_frame.place(x=0, y=200, width=380, height=100)

        state = {"saved": False, "query": ""}

        def execute_query():
            state["query"] = query_text.get("1.0", tk.END).strip()
            if state["query"]:
                try:
                    return self.execute_query(df, state["query"])
                except Exception as exc:
                    messagebox.showerror("错误", f"表格转换执行失败: {str(exc)}")
                    return None
            messagebox.showwarning("警告", "请输入 JSON 结构化表格转换操作")
            return None

        def execute():
            df_output = execute_query()
            if on_show_df is not None:
                on_show_df(df_output)

        def insert_template():
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
                result = messagebox.askyesno("警告", "是否保存结果？")
                if result:
                    save_result()
                else:
                    cancel()

            try:
                if filter_dialog.winfo_exists():
                    filter_dialog.destroy()
            except tk.TclError:
                pass

        ttk.Button(button_frame, text="插入模板", command=insert_template).place(x=5, y=0, width=100, height=30)
        ttk.Button(button_frame, text="执行并查看结果", command=execute).place(x=110, y=0, width=140, height=30)
        ttk.Button(button_frame, text="保存结果", command=save_result).place(x=255, y=0, width=110, height=30)
        ttk.Button(button_frame, text="取消", command=cancel).place(x=300, y=35, width=80, height=30)
        ttk.Button(button_frame, text="显示处理前数据", command=lambda: on_show_df(df) if on_show_df else None).place(x=5, y=35, width=140, height=30)
        ttk.Button(button_frame, text="显示处理后数据", command=execute).place(x=155, y=35, width=140, height=30)

        filter_dialog.protocol("WM_DELETE_WINDOW", destroy)
        return filter_dialog


def open_qc_subprocess(project: str, module_name: str, rater: str, ezqcid: str) -> subprocess.Popen:
    project_root = Path(__file__).parent.parent
    return subprocess.Popen(
        [sys.executable, str(project_root / "easyqc.py"), project, module_name, rater, ezqcid],
        shell=False,
    )


__all__ = ["TableView", "TableTransformDialog", "open_qc_subprocess"]
