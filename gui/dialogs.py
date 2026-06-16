from __future__ import annotations

import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from gui.state_adapter import LegacyGUIStateAdapter
from gui.widgets import DialogBase, bind_context_menu
from utils.file_utils import FileUtils
from utils.logger import log_debug, log_error, log_info
from utils.validators import validate_score as validate_score_value


def _gui_state_from_app(app):
    gui_state = getattr(app, "gui_state", None)
    if gui_state is not None:
        return gui_state
    return LegacyGUIStateAdapter(getattr(app, "ProjM", None))


class ProjectDialog(DialogBase):
    def __init__(self, app):
        self.app = app
        self.gui_state = _gui_state_from_app(app)

    def _refresh_project_combo(self, selected_project=None):
        self.app.project_combo["values"] = self.gui_state.project_names()
        if selected_project is None:
            selected_project = self.gui_state.current_project_name()
        if selected_project:
            self.app.project_combo.set(selected_project)

    def create_project(self):
        dialog = tk.Toplevel(self.app.root)
        dialog.title("新建项目")
        dialog.geometry("500x200")

        ttk.Label(dialog, text="项目名称:").grid(row=0, column=0, padx=10, pady=10, sticky=tk.W)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(dialog, textvariable=name_var, width=30)
        name_entry.grid(row=0, column=1, padx=10, pady=10)

        ttk.Label(dialog, text="项目路径:").grid(row=1, column=0, padx=10, pady=10, sticky=tk.W)
        path_var = tk.StringVar()
        path_entry = ttk.Entry(dialog, textvariable=path_var, width=30)
        path_entry.grid(row=1, column=1, padx=10, pady=10)

        def browse_path():
            path = filedialog.askdirectory(title="选择项目路径")
            if path:
                path_entry.delete(0, tk.END)
                path_entry.insert(0, path)
                path_var.set(path)

        ttk.Button(dialog, text="浏览路径", command=browse_path).grid(row=1, column=2, padx=5, pady=10)

        def confirm():
            name = name_entry.get().strip()
            path = path_entry.get().strip()

            if not name:
                messagebox.showwarning("警告", "请输入项目名称")
                return
            if not path:
                messagebox.showwarning("警告", "请选择项目路径")
                return
            if self.gui_state.has_project(name):
                messagebox.showwarning("警告", "项目名称已存在")
                return

            try:
                self.gui_state.create_and_load_project(name, path)
                self._refresh_project_combo(name)
                dialog.destroy()
            except Exception as e:
                messagebox.showerror("错误", f"创建项目失败: {str(e)}")

        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=2, column=0, columnspan=3, pady=20)
        ttk.Button(button_frame, text="确认", command=confirm).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def import_project(self):
        path = filedialog.askdirectory(title="选择要导入的项目路径")
        if not path:
            return
        try:
            self.gui_state.import_project_from_dir(path)
            self._refresh_project_combo()
        except Exception as e:
            messagebox.showerror(
                "错误",
                f"导入项目失败，路径可能非法（需含 settings_*.json）：\n{e}",
            )

    def remove_project(self):
        dialog = tk.Toplevel(self.app.root)
        dialog.title("移除项目")
        dialog.geometry("700x400")
        dialog.transient(self.app.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(dialog, text="双击条目移除项目", padding=10).pack()

        scrollbar = ttk.Scrollbar(dialog)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        listbox = tk.Listbox(dialog, width=40, height=15, yscrollcommand=scrollbar.set)
        listbox.pack(side=tk.LEFT, padx=10, pady=5, fill=tk.BOTH, expand=True)
        listbox.config(exportselection=False)
        listbox.config(font=("微软雅黑", 12))
        scrollbar.config(command=listbox.yview)

        for display_text in self.gui_state.project_display_rows():
            listbox.insert(tk.END, display_text)

        def on_double_click(event):
            selection = listbox.curselection()
            if not selection:
                return
            selected_item = listbox.get(selection[0])
            project_name = selected_item.split(" - ")[0]

            if messagebox.askyesno("确认", f"确定要移除项目 '{project_name}' 吗?"):
                self.gui_state.remove_project(project_name)
                self._refresh_project_combo()
                listbox.delete(selection[0])

        listbox.bind("<Double-Button-1>", on_double_click)


class ConstantDialog(DialogBase):
    def __init__(self, app):
        self.app = app
        self.gui_state = _gui_state_from_app(app)

    def add_constant(self):
        constant_name = self.app.constant_name.get()
        constant_value = self.app.constant_value.get()
        if not constant_name or not constant_value:
            messagebox.showerror("错误", "请输入常量名和值")
            return

        if self.gui_state.has_constant(constant_name):
            messagebox.showerror("错误", "常量名已存在")
            return

        if not constant_name.isidentifier():
            messagebox.showerror("错误", "常量名不能包含特殊字符")
            return

        self.gui_state.set_constant(constant_name, constant_value)
        self.refresh_constant_table()

        self.app.constant_name.delete(0, tk.END)
        self.app.constant_value.delete(0, tk.END)

    def refresh_constant_table(self):
        if not hasattr(self.app, "constant_table"):
            return

        for item in self.app.constant_table.get_children():
            self.app.constant_table.delete(item)

        for constant_name, constant_value in self.gui_state.constant_items():
            self.app.constant_table.insert("", "end", values=(str(constant_name), str(constant_value)))

    def edit_constant(self, event):
        selected_item = self.app.constant_table.selection()
        if not selected_item:
            return

        constant_name, constant_value = self.app.constant_table.item(selected_item, "values")
        log_debug(f"编辑常量: {constant_name}={constant_value}", "ConstantDialog")

        dialog = tk.Toplevel(self.app.root)
        dialog.title("编辑常量")
        dialog.geometry("400x180")

        name_frame = ttk.Frame(dialog)
        name_frame.pack(fill=tk.X, padx=100, pady=10)
        ttk.Label(name_frame, text="常量名：", font=self.app.font_13).pack(side=tk.LEFT)
        name_entry = ttk.Entry(name_frame, font=self.app.font_12)
        name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        name_entry.insert(0, constant_name)

        value_frame = ttk.Frame(dialog)
        value_frame.pack(fill=tk.X, padx=20, pady=10)
        ttk.Label(value_frame, text="值：", font=self.app.font_13).pack(side=tk.LEFT)
        value_entry = ttk.Entry(value_frame, font=self.app.font_12)
        value_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        value_entry.insert(0, constant_value)

        button_frame = ttk.Frame(dialog)
        button_frame.pack(fill=tk.X, padx=20, pady=20)

        def update_constant():
            new_name = name_entry.get().strip()
            new_value = value_entry.get().strip()

            if not new_name or not new_value:
                messagebox.showerror("错误", "常量名和值不能为空")
                return

            if new_name != constant_name and self.gui_state.has_constant(new_name):
                messagebox.showerror("错误", "常量名已存在")
                return

            if not new_name.isidentifier():
                messagebox.showerror("错误", "常量名不能包含特殊字符")
                return

            self.gui_state.rename_constant(constant_name, new_name, new_value)
            self.refresh_constant_table()
            dialog.destroy()

        def delete_constant():
            if messagebox.askyesno("确认", "确定要删除这个常量吗?"):
                self.gui_state.delete_constant(constant_name)
                self.refresh_constant_table()
                dialog.destroy()

        button_pack_options = {"padx": 5, "pady": 10, "ipadx": 10, "ipady": 4}
        ttk.Button(button_frame, text="修改", command=update_constant).pack(side=tk.LEFT, **button_pack_options)
        ttk.Button(button_frame, text="删除", command=delete_constant).pack(side=tk.LEFT, **button_pack_options)
        ttk.Button(button_frame, text="取消", command=dialog.destroy).pack(side=tk.RIGHT, **button_pack_options)


class VariableDialog(DialogBase):
    def __init__(self, app, data_manager, table_display):
        self.app = app
        self.DataM = data_manager
        self.TablD = table_display
        self.gui_state = _gui_state_from_app(app)

    def extract_path(self):
        path = self.app.path_entry.get()
        if path:
            self.gui_state.set_new_variable_table(self.DataM.get_list(path))
            self.set_varname()

    def set_varname(self):
        self.dialog = tk.Toplevel(self.app.root)
        self.dialog.title("设置变量名")
        self.dialog.geometry("400x280")

        ttk.Label(self.dialog, text="请设置变量名:", font=self.app.font_12).place(x=10, y=10)
        varname_entry = ttk.Entry(self.dialog, font=self.app.font_12)
        varname_entry.place(x=120, y=10, width=250)

        def set_varname():
            varname = varname_entry.get()
            if varname:
                df = self.gui_state.prepare_new_variable_table(varname)
                self.TablD.show_df(df)
                self.dialog.destroy()

        ttk.Button(self.dialog, text="确定", command=set_varname).place(x=120, y=150)
        ttk.Button(self.dialog, text="取消", command=self.dialog.destroy).place(x=220, y=150)

    def extract_file(self):
        if not self.app.path_entry2.get():
            messagebox.showerror("错误", "请先选择文件")
            return

        file_ext = os.path.splitext(self.app.path_entry2.get())[1].lower()
        if file_ext not in [".csv", ".xlsx", ".xls", ".txt", ".list"]:
            messagebox.showerror("错误", "请选择csv、excel、txt、list文件")
            return

        path = self.app.path_entry2.get()
        if path:
            self.gui_state.set_new_variable_table(self.DataM.read_list(path))
            if path.endswith(".list") or path.endswith(".txt"):
                self.set_varname()

    def extract_words(self):
        text = self.app.text_entry.get("1.0", tk.END)
        if not text:
            messagebox.showerror("错误", "请输入文本")
            return

        self.gui_state.set_new_variable_table(self.DataM.extract_words_as_df(text))
        self.set_varname()

    def merge_newdata(self):
        self.dialog2 = tk.Toplevel(self.app.root)
        self.dialog2.title("设置变量名")
        self.dialog2.geometry("400x300")

        column_combo = None
        batch_entry = None

        df = self.gui_state.new_variable_merge_source()
        if "ezqcid" in df.columns:
            ttk.Label(self.dialog2, text="表格中已有ezqcid列，无需设置", font=self.app.font_13).place(x=10, y=10)
        else:
            ttk.Label(self.dialog2, text="请选择要设置为ezqcid的列:", font=self.app.font_13).place(x=10, y=10)
            column_combo = ttk.Combobox(self.dialog2, values=list(df.columns), font=self.app.font_12, state="readonly")
            column_combo.place(x=10, y=50)

        if "ezqcbatch" in df.columns:
            ttk.Label(self.dialog2, text="表格中已有ezqcbatch列，无需设置", font=self.app.font_13).place(x=10, y=90)
        else:
            ttk.Label(self.dialog2, text="请设置批次:", font=self.app.font_13).place(x=10, y=90)
            batch_entry = ttk.Entry(self.dialog2, font=self.app.font_12)
            batch_entry.place(x=10, y=120)
            batch_entry.insert(0, "1")

        def set_var():
            raw_df = df.copy()

            if not column_combo:
                varname = None
            elif column_combo and not column_combo.get():
                messagebox.showerror("错误", "变量名不能为空")
                return None
            else:
                varname = column_combo.get()

            if not batch_entry:
                batch = None
            elif batch_entry and not batch_entry.get():
                messagebox.showerror("错误", "批次不能为空")
                return None
            else:
                batch = batch_entry.get()

            return self.DataM.set_varname_batch(raw_df, varname, batch)

        def show_tmp_df():
            df_tmp = set_var()
            self.TablD.show_df(df_tmp)

        ttk.Button(self.dialog2, text="查看", command=show_tmp_df).place(x=20, y=200)

        def set_var_():
            df_to_merge = set_var()
            if df_to_merge is None:
                return
            # G2 fix: warn if a new column name collides with an existing
            # constant name. generate_code merges {row, **constants}, so a
            # same-named column would shadow the constant and break template
            # substitution silently.
            constant_names = set(self.gui_state.constants().keys())
            conflicts = [str(c) for c in df_to_merge.columns if str(c) in constant_names]
            if conflicts:
                if not messagebox.askyesno(
                    "警告",
                    "以下列名与常量名冲突，可能导致代码模板替换异常：\n"
                    + ", ".join(conflicts)
                    + "\n是否仍要合并？",
                ):
                    return
            self.gui_state.set_filtered_variable_table(df_to_merge)
            self.dialog2.destroy()
            self.new_merge()

        ttk.Button(self.dialog2, text="确定", command=set_var_).place(x=120, y=200)
        ttk.Button(self.dialog2, text="取消", command=self.dialog2.destroy).place(x=220, y=200)

    def new_merge(self):
        if not self.gui_state.has_all_variable_rows():
            tmp = self.gui_state.filtered_variable_table()
            if tmp is None or len(tmp) == 0:
                messagebox.showinfo("信息", "没有可显示的数据")
                return
            self.gui_state.set_all_variable_table(tmp)
            self.gui_state.save_all_variable_table()
            messagebox.showinfo("信息", "合并完成")
            return

        merge_dialog = tk.Toplevel(self.app.root)
        merge_dialog.title("合并变量")
        merge_dialog.geometry("300x300")

        ttk.Label(merge_dialog, text="已经存在总变量，请问是否合并变量?", font=self.app.font_13).pack(pady=20)

        button_frame = ttk.Frame(merge_dialog)
        button_frame.pack(pady=20)
        self.df_tmp = self.gui_state.filtered_variable_table()
        if self.df_tmp is None or len(self.df_tmp) == 0:
            messagebox.showinfo("信息", "没有可显示的数据")
            return

        def fresh_tables():
            self.gui_state.refresh_project_after_variable_merge()
            merge_dialog.destroy()

        def merge_as_rows():
            self.gui_state.merge_all_variables_as_rows(self.df_tmp)
            messagebox.showinfo("信息", "合并完成")
            fresh_tables()

        ttk.Button(button_frame, text="合并成新行", command=merge_as_rows).pack(pady=5)

        def merge_as_columns():
            self.gui_state.merge_all_variables_as_columns(self.df_tmp)
            fresh_tables()

        ttk.Button(button_frame, text="合并成新列", command=merge_as_columns).pack(pady=5)

        def replace():
            self.gui_state.set_all_variable_table(self.df_tmp)
            fresh_tables()

        ttk.Button(button_frame, text="替换", command=replace).pack(pady=5)
        ttk.Button(button_frame, text="取消", command=merge_dialog.destroy).pack(pady=5)

    def show_all_variable(self):
        df = self.gui_state.all_variable_table()
        if df is not None:
            self.TablD.show_df(df)


class ModuleDialog(DialogBase):
    def __init__(self, app, qcpage):
        self.app = app
        self.qcpage = qcpage
        self.gui_state = _gui_state_from_app(app)

    def _next_module_index(self) -> str:
        return self.gui_state.next_module_index()

    def _reorder_modules(self) -> None:
        self.gui_state.reorder_modules()

    def _refresh_module_table(self, table) -> None:
        for child in table.get_children():
            table.delete(child)
        for index, name, label in self.gui_state.module_table_rows():
            table.insert("", "end", values=(index, name, label))

    def load_module_file(self, file_path):
        return FileUtils.safe_json_load(file_path)

    def add_module(self, name=None, label=None, index=None):
        title = "添加新模块" if name is None else "修改模块"
        dialog = tk.Toplevel(self.app.root)
        dialog.title(title)
        dialog.geometry("400x280")
        dialog.transient(self.app.root)

        ttk.Label(dialog, text="模块名称:", font=self.app.font_13).place(x=30, y=30)
        name_entry = ttk.Entry(dialog, font=self.app.font_12)
        name_entry.place(x=120, y=30, width=230)
        if name is not None:
            name_entry.insert(0, name)

        ttk.Label(dialog, text="模块标题:", font=self.app.font_13).place(x=30, y=80)
        label_entry = ttk.Entry(dialog, font=self.app.font_12)
        label_entry.place(x=120, y=80, width=230)
        if label is not None:
            label_entry.insert(0, label)

        ttk.Label(dialog, text="模块序号:", font=self.app.font_13).place(x=30, y=130)
        index_entry = ttk.Entry(dialog, font=self.app.font_12)
        index_entry.place(x=120, y=130, width=230)
        index_entry.insert(0, str(index) if index is not None else self._next_module_index())

        def create_module():
            name_ = name_entry.get().strip()
            label_ = label_entry.get().strip()
            index_ = index_entry.get().strip()
            log_debug(f"创建模块: {name_} {label_} {index_}", "ModuleDialog")

            if not all([name_, label_, index_]):
                messagebox.showerror("错误", "所有字段都必须填写")
                return
            if not index_.isdigit():
                messagebox.showerror("错误", "序号必须是正整数")
                return
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name_):
                messagebox.showerror("错误", "模块名称必须以字母或下划线开头，后面可以是字母、数字或下划线")
                return

            if self.gui_state.module_name_exists(name_) and name is None:
                messagebox.showerror("错误", "模块名称已存在")
                return

            if name is not None and self.gui_state.module_name_exists(name_, exclude_name=name):
                messagebox.showerror("错误", "模块名称已存在")
                return

            if name is not None:
                self.gui_state.modify_module(index, name_, label_, index_)
            else:
                self.gui_state.add_module(name_, label_, index_)

            self.app.load_module_to_gui()
            dialog.destroy()

        text = "创建" if name is None else "修改"
        ttk.Button(dialog, text=text, command=create_module).place(x=120, y=200, width=80)
        ttk.Button(dialog, text="取消", command=dialog.destroy).place(x=220, y=200, width=80)

    def change_module_index(self, index, module):
        frame = tk.Toplevel(self.app.root)
        frame.title("修改模块信息")
        frame.geometry("400x280")
        frame.transient(self.app.root)
        frame.grab_set()

        result = {"success": False}

        ttk.Label(frame, text="模块名称:", font=self.app.font_13).place(x=30, y=30)
        name_entry = ttk.Entry(frame, font=self.app.font_12)
        name_entry.place(x=120, y=30, width=230)
        name_entry.insert(0, module["name"])

        ttk.Label(frame, text="模块标题:", font=self.app.font_13).place(x=30, y=80)
        label_entry = ttk.Entry(frame, font=self.app.font_12)
        label_entry.place(x=120, y=80, width=230)
        label_entry.insert(0, module["label"])

        ttk.Label(frame, text="模块序号:", font=self.app.font_13).place(x=30, y=130)
        index_entry = ttk.Entry(frame, font=self.app.font_12)
        index_entry.place(x=120, y=130, width=230)
        index_entry.insert(0, index)

        def close_dialog():
            frame.grab_release()
            frame.destroy()

        def change_module():
            module_name = name_entry.get().strip()
            module_label = label_entry.get().strip()
            module_index = index_entry.get().strip()
            if not all([module_name, module_label, module_index]):
                messagebox.showerror("错误", "所有字段都必须填写")
                return
            if not module_index.isdigit():
                messagebox.showerror("错误", "序号必须是正整数")
                return

            if self.gui_state.module_name_exists(module_name):
                messagebox.showerror("错误", "模块名称已存在,请修改模块名称")
                return

            module["name"] = module_name
            module["label"] = module_label
            self.gui_state.insert_module(module_index, module)
            result["success"] = True
            close_dialog()

        ttk.Button(frame, text="确定", command=change_module).place(x=120, y=170, width=80)
        ttk.Button(frame, text="取消", command=close_dialog).place(x=220, y=170, width=80)

        frame.wait_window()
        return result["success"]

    def import_module(self):
        file_path = filedialog.askopenfilename(
            title="请选择qcmodule模块文件",
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")],
        )
        if not file_path:
            return

        try:
            module = self.load_module_file(file_path)

            if not self.gui_state.check_module(module):
                messagebox.showerror("错误", "模块文件不合法")
                return

            module_name = module["name"]
            index = self.gui_state.module_index_by_name(module_name)
            if index is not None:
                if not messagebox.askyesno("警告", "模块名称已存在，请修改模块名称"):
                    messagebox.showinfo("提示", "放弃导入")
                    return
                if self.change_module_index(index, module):
                    self.gui_state.save_settings()
                    self.app.load_module_to_gui()
                    messagebox.showinfo("提示", "模块导入成功")
                return

            self.gui_state.insert_module(self._next_module_index(), module)
            self.app.load_module_to_gui()
            messagebox.showinfo("提示", "模块导入成功")
        except Exception as e:
            messagebox.showerror("错误", f"导入模块失败: {str(e)}")

    def export_module(self, module_name):
        folder_selected = filedialog.askdirectory(title="请选择导出路径")
        if folder_selected:
            self.gui_state.export_module(module_name, os.path.join(folder_selected, f"qcmodule_{module_name}.json"))

    def manage_module(self):
        dialog = tk.Toplevel(self.app.root)
        dialog.title("管理模块")
        dialog.geometry("400x300")
        dialog.transient(self.app.root)

        ttk.Label(dialog, text="左键双击修改模块设置，右键单击删除模块", font=self.app.font_13).pack(pady=5)
        table_frame = ttk.Frame(dialog)
        table_frame.pack(fill=tk.BOTH, padx=20, pady=5, expand=True)

        table_scroll = ttk.Scrollbar(table_frame)
        table_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        table = ttk.Treeview(
            table_frame,
            columns=("序号", "名称", "标题"),
            show="headings",
            yscrollcommand=table_scroll.set,
        )
        table.heading("序号", text="序号")
        table.heading("名称", text="名称")
        table.heading("标题", text="标题")
        table.column("序号", width=50)
        table.column("名称", width=150)
        table.column("标题", width=200)

        table_scroll.config(command=table.yview)
        table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._refresh_module_table(table)

        def on_double_click(event):
            selection = table.selection()
            if not selection:
                return
            index, name, label = table.item(selection[0], "values")
            self.add_module(index=index, name=name, label=label)
            self._refresh_module_table(table)

        table.bind("<Double-1>", on_double_click)

        def on_right_click(event):
            try:
                selection = table.selection()
                if not selection:
                    return
                index = table.item(selection[0], "values")[0]

                if not self.gui_state.can_delete_module():
                    messagebox.showerror("错误", "只剩下1个模块，不可删除，您可以修改")
                    return

                if not messagebox.askyesno("确认", "是否删除该模块?"):
                    return

                self.gui_state.delete_module(index)
                self.app.load_module_to_gui()
                self._refresh_module_table(table)
            except Exception as e:
                log_error(f"右键删除模块时发生错误: {e}", "ModuleDialog")

        bind_context_menu(table, on_right_click, system_name=self.gui_state.system_name())

        def add_module():
            self.add_module()
            self._refresh_module_table(table)

        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=5)
        ttk.Button(button_frame, text="增加模块", command=add_module).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="完成", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def start_qc(self, module):
        try:
            log_info(f"开始质控模块: {module.get('name', '未知')}", "ModuleDialog")
            self.qcpage.open_qcpage_from_main(self.app, module["name"])
        except Exception as e:
            log_error(f"打开质控页面时发生错误: {e}", "ModuleDialog")
            messagebox.showerror("错误", f"无法打开质控页面: {e}")


class ScoreTagEditor(DialogBase):
    _MSGBOX = """分值必须为以下形式：\n
        (1): "Poor,Fair,Good" - 字符串形式的分类标签，用逗号分隔\n
        (2): "1,2,3,4" - 表示1,2,3,4的连续数值\n
        (3): "0-3" - 表示0,1,2,3的连续数值范围"""

    def __init__(self, app):
        self.app = app
        self.gui_state = _gui_state_from_app(app)

    def add_score(self, qcindex, idx):
        self.gui_state.add_score(qcindex, idx)
        self.app.load_module_to_gui()

    def del_score(self, qcindex, idx):
        self.gui_state.delete_score(qcindex, idx)
        self.app.load_module_to_gui()

    def validate_score(self, value, show_error=True):
        try:
            result = validate_score_value(value)
            if result is None and value.strip() and show_error:
                messagebox.showerror("错误", self._MSGBOX)
            return result
        except ValueError as e:
            if show_error:
                messagebox.showerror("错误", f"{self._MSGBOX}\n\n错误详情: {str(e)}")
            return None

    def add_tag(self, qcindex, idx):
        self.gui_state.add_tag(qcindex, idx)
        self.app.load_module_to_gui()

    def del_tag(self, qcindex, idx):
        self.gui_state.delete_tag(qcindex, idx)
        self.app.load_module_to_gui()


__all__ = ["ProjectDialog", "ConstantDialog", "VariableDialog", "ModuleDialog", "ScoreTagEditor"]
