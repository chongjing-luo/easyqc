
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC 对话框主要功能模块
包含文件浏览、路径选择、变量设置等对话框功能
"""

import tkinter as tk
from tkinter import N, ttk, messagebox, filedialog, scrolledtext
import os
import sys
from pathlib import Path
import pandas as pd
import re
import json

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_info, log_error, log_warning, log_exception, log_debug, LogContext, log_function
from utils.data_manager import DataManager
from gui.gui_table import TableDisplay
from gui.gui_qcpage import gui_qcpage

class DialogMain:
    """对话框主要功能类"""
    
    def __init__(self, app):
        """初始化对话框功能类
        
        Args:
            app_instance: EasyQCApp实例，用于访问应用程序的属性和方法
        """
        self.app = app
        self.ProjM = app.ProjM
        self.DataM = DataManager()
        self.TablD = TableDisplay(app)
        self.dt = self.app.ProjM.dt
        self.qcpage = gui_qcpage()
    
    def browse_path(self):
        """通过图形化界面获取文件夹路径"""
        try:
            # 打开文件夹选择对话框
            directory = filedialog.askdirectory(
                parent=self.app.root,
                title='选择文件夹',
                initialdir=os.path.expanduser("~")  # 从用户主目录开始
            )
            
            # 只有当用户选择了文件夹时才更新输入框
            if directory and isinstance(directory, str):
                self.app.path_entry.delete(0, tk.END)
                self.app.path_entry.insert(0, directory)
                
        except Exception as e:
            messagebox.showerror("错误", f"选择文件夹时发生错误: {str(e)}")
            log_error(f"文件夹选择对话框错误: {str(e)}", "EasyQCApp")
            
            
    def browse_file(self):
        try:
            # 打开文件选择对话框,限制文件类型为csv、excel和txt
            filetypes = (
                ('CSV files', '*.csv'),
                ('Excel files', '*.xlsx *.xls'),
                ('Text files', '*.txt'),
                ('All files', '*.*')
            )
            
            # 使用元组而不是列表来定义filetypes
            filename = filedialog.askopenfilename(
                parent=self.app.root,
                title='选择文件',
                filetypes=filetypes,
                initialdir=os.path.expanduser("~")  # 从用户主目录开始
            )
            
            # 只有当用户选择了文件时才更新输入框
            if filename and isinstance(filename, str):
                self.app.path_entry2.delete(0, tk.END)
                self.app.path_entry2.insert(0, filename)
                
        except Exception as e:
            messagebox.showerror("错误", f"选择文件时发生错误: {str(e)}")
            log_error(f"文件选择对话框错误: {str(e)}", "EasyQCApp")

    def import_json(self):
        pass
    
    def export_json(self):
        pass

    def create_project(self):
        """创建新项目对话框"""
        dialog = tk.Tk()
        dialog.title("新建项目")
        dialog.geometry("500x200")
        # dialog.grab_set()
        
        # 项目名称输入
        ttk.Label(dialog, text="项目名称:").grid(row=0, column=0, padx=10, pady=10, sticky=tk.W)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(dialog, textvariable=name_var, width=30)
        name_entry.grid(row=0, column=1, padx=10, pady=10)
        
        # 项目路径输入
        ttk.Label(dialog, text="项目路径:").grid(row=1, column=0, padx=10, pady=10, sticky=tk.W)
        path_var = tk.StringVar()
        path_entry = ttk.Entry(dialog, textvariable=path_var, width=30)
        path_entry.grid(row=1, column=1, padx=10, pady=10)
        
        # 浏览按钮
        def browse_path():
            path = filedialog.askdirectory(title="选择项目路径")
            if path:
                # 清空输入框并重新设置值
                path_entry.delete(0, tk.END)
                path_entry.insert(0, path)
                path_var.set(path)
        
        ttk.Button(dialog, text="浏览路径", command=browse_path).grid(row=1, column=2, padx=5, pady=10)
        
        # 确认和取消按钮
        def confirm():
            name = name_entry.get().strip()
            path = path_entry.get().strip()
            
            if not name:
                messagebox.showwarning("警告", "请输入项目名称")
                return
            if not path:
                messagebox.showwarning("警告", "请选择项目路径")
                return

            # 检查项目名称是否已存在
            if name in self.dt.projects:
                messagebox.showwarning("警告", "项目名称已存在")
                return
            
            # success = False
            try:
                self.app.ProjM.create_project(name, path)
                self.app.ProjM.load_project(name)
                self.app.project_combo['values'] = list(self.dt.projects.keys())
                self.app.project_combo.set(name)
                dialog.destroy()
            except Exception as e:
                messagebox.showerror("错误", f"创建项目失败: {str(e)}")
            
        
        button_frame = ttk.Frame(dialog)
        button_frame.grid(row=2, column=0, columnspan=3, pady=20)
        
        ttk.Button(button_frame, text="确认", command=confirm).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="取消", command=dialog.destroy).pack(side=tk.LEFT, padx=5)

    def import_project(self):
        """导入项目对话框"""
        path = filedialog.askdirectory(title="选择要导入的项目路径")
        if path:
            self.app.ProjM.load_project(output_dir=path)
            # 更新项目列表
            self.app.project_combo['values'] = list(self.dt.projects.keys())
            # 选择新导入的项目
            self.app.project_combo.set(self.dt.project)

    def remove_project(self):
        """移除项目"""
        # 创建项目移除对话框
        dialog = tk.Toplevel(self.app.root)
        dialog.title("移除项目")
        dialog.geometry("700x400")
        dialog.transient(self.app.root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        # 添加提示标签
        ttk.Label(dialog, text="双击条目移除项目", padding=10).pack()
        
        # 创建滚动条
        scrollbar = ttk.Scrollbar(dialog)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 创建列表框显示所有项目
        listbox = tk.Listbox(dialog, width=40, height=15, yscrollcommand=scrollbar.set)
        listbox.pack(side=tk.LEFT, padx=10, pady=5, fill=tk.BOTH, expand=True)
        listbox.config(exportselection=False)
        listbox.config(font=("微软雅黑", 12))
        # 使listbox内容随窗口横向拉伸自动显示更多内容
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        listbox.pack_configure(fill=tk.BOTH, expand=True)
        
        # 配置滚动条
        scrollbar.config(command=listbox.yview)
        
        # 填充项目列表，显示项目名称和路径
        for project, info in self.dt.projects.items():
            # 检查info是否为字典类型
            if isinstance(info, dict) and 'path' in info:
                display_text = f"{project} - {info['path']}"
            else:
                display_text = f"{project} - {info}"
            listbox.insert(tk.END, display_text)
            
        # 双击处理函数
        def on_double_click(event):
            selection = listbox.curselection()
            if selection:
                # 获取选中的项目信息
                selected_item = listbox.get(selection[0])
                # 从显示文本中提取项目名称
                project_name = selected_item.split(" - ")[0]
                
                if messagebox.askyesno("确认", f"确定要移除项目 '{project_name}' 吗?"):
                    self.app.ProjM.rm_project(project_name)
                    # 更新主窗口的项目下拉列表
                    self.app.project_combo['values'] = list(self.dt.projects.keys())
                    # 如果删除的是当前项目，重置当前项目
                    if project_name == self.dt.project:
                        self.app.ProjM.load_project(self.app.project)
                    # 从列表框中移除该项目
                    listbox.delete(selection[0])
                    
        # 绑定双击事件
        listbox.bind('<Double-Button-1>', on_double_click)

    # #############################   variable   #################################
    def add_constant(self):
        """添加常量
        获取常量名和值，添加到self.settings['constants']
        并更新表格
        """
        # 创建常量设置对话框
        constant_name = self.app.constant_name.get()
        constant_value = self.app.constant_value.get()
        if not constant_name or not constant_value:
            messagebox.showerror("错误", "请输入常量名和值")
            return

        # 检查变量名是否已存在
        if constant_name in self.dt.settings['constants']:
            messagebox.showerror("错误", "常量名已存在")
            return
        
        # 检查变量名是否能够成为df的列名
        if not constant_name.isidentifier():
            messagebox.showerror("错误", "常量名不能包含特殊字符")
            return

        self.dt.settings['constants'][constant_name] = constant_value
        self.refresh_constant_table()
        self.app.ProjM.save_settings()

        # 清除输入框内容
        self.app.constant_name.delete(0, tk.END)
        self.app.constant_value.delete(0, tk.END)


    def refresh_constant_table(self):
        """刷新常量表格数据"""
        # 检查constant_table是否已经创建
        if not hasattr(self.app, 'constant_table'):
            return
            
        settings_constants = self.dt.settings['constants']
        for item in self.app.constant_table.get_children():
            self.app.constant_table.delete(item)
        
        # 重新加载数据
        for constant_name, constant_value in settings_constants.items():
            self.app.constant_table.insert("", "end", values=(str(constant_name), str(constant_value)))


    def edit_constant(self, event):
        """编辑常量"""
        # 获取选中的行
        selected_item = self.app.constant_table.selection()
        if selected_item:
            # 获取选中行的常量名和值
            constant_name, constant_value = self.app.constant_table.item(selected_item, 'values')
            print(constant_name, constant_value)
            
            # 创建编辑对话框
            dialog = tk.Tk()
            dialog.title("编辑常量")
            dialog.geometry("400x180")
            
            # 常量名输入框
            name_frame = ttk.Frame(dialog)
            name_frame.pack(fill=tk.X, padx=100, pady=10)
            ttk.Label(name_frame, text="常量名：", font=self.app.font_13).pack(side=tk.LEFT)
            name_entry = ttk.Entry(name_frame, font=self.app.font_12)
            name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            name_entry.insert(0, constant_name)  # 直接插入默认值
            
            # 常量值输入框
            value_frame = ttk.Frame(dialog)
            value_frame.pack(fill=tk.X, padx=20, pady=10)
            ttk.Label(value_frame, text="值：", font=self.app.font_13).pack(side=tk.LEFT)
            value_entry = ttk.Entry(value_frame, font=self.app.font_12)
            value_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            value_entry.insert(0, constant_value)  # 直接插入默认值
            
            # 按钮框架
            button_frame = ttk.Frame(dialog)
            button_frame.pack(fill=tk.X, padx=20, pady=20)
            
            # 修改按钮回调函数
            def update_constant():
                new_name = name_entry.get().strip()
                new_value = value_entry.get().strip()
                
                if not new_name or not new_value:
                    messagebox.showerror("错误", "常量名和值不能为空")
                    return
                
                # 如果修改了常量名，检查新名称是否已存在
                if new_name != constant_name and new_name in self.dt.settings['constants']:
                    messagebox.showerror("错误", "常量名已存在")
                    return
                
                # 检查新的变量名是否合法
                if not new_name.isidentifier():
                    messagebox.showerror("错误", "常量名不能包含特殊字符")
                    return
                
                # 更新常量
                if new_name != constant_name:
                    del self.dt.settings['constants'][constant_name]
                self.dt.settings['constants'][new_name] = new_value
                self.app.ProjM.save_settings()
                self.refresh_constant_table()
                dialog.destroy()
            
            # 删除按钮回调函数
            def delete_constant():
                if messagebox.askyesno("确认", "确定要删除这个常量吗?"):
                    del self.dt.settings['constants'][constant_name]
                    self.app.ProjM.save_settings()
                    self.refresh_constant_table()
                    dialog.destroy()
            
            # 添加按钮
            ttk.Button(button_frame, text="修改", command=update_constant, style='Project.TButton').pack(side=tk.LEFT, padx=5)
            ttk.Button(button_frame, text="删除", command=delete_constant, style='Project.TButton').pack(side=tk.LEFT, padx=5)
            ttk.Button(button_frame, text="取消", command=dialog.destroy, style='Project.TButton').pack(side=tk.RIGHT, padx=5)


    # ############################################################

    def extract_path(self):
        """
        提取路径，使用DataManager的get_list获取，生成self.tmp_list_df,
        """
        path = self.app.path_entry.get()
        if path:
            self.dt.var['ezqc_new'] = self.DataM.get_list(path)
            self.set_varname()
    
    def set_varname(self):
        """
        设置变量名
        """
        self.dialog = tk.Toplevel(self.app.root)
        self.dialog.title("设置变量名")
        self.dialog.geometry("400x280")

        # 在弹出框中增加两个文本输入框，一个是变量名，一个是批次
        ttk.Label(self.dialog, text="请设置变量名:", font=self.app.font_12).place(x=10, y=10)
        varname_entry = ttk.Entry(self.dialog, font=self.app.font_12)
        varname_entry.place(x=120, y=10,width=250)

        def set_varname():
            varname = varname_entry.get()
            if varname:
                if varname not in self.dt.var['ezqc_new'].columns and len(self.dt.var['ezqc_new'].columns) == 1:
                    self.dt.var['ezqc_new'].columns = [varname]

                self.dt.var['ezqc_new'] = self.dt.var['ezqc_new'].sort_values(by=varname, ascending=True)
                self.dt.var['ezqc_filter'] = self.dt.var['ezqc_new'].copy()
                self.TablD.show_df(self.dt.var['ezqc_new'])
                self.dialog.destroy()

        ttk.Button(self.dialog, text="确定", command=set_varname).place(x=120, y=150)
        ttk.Button(self.dialog, text="取消", command=self.dialog.destroy).place(x=220, y=150)


    def extract_file(self):
        """
        提取文件，使用DataManager的get_list获取，生成self.tmp_list_df,
        """
        # 如果为空，则跳过，如果不为空，先查是否是csv、excel或者txt,list文件
        if not self.app.path_entry2.get():
            messagebox.showerror("错误", "请先选择文件")
            return
        # 检查文件类型
        file_ext = os.path.splitext(self.app.path_entry2.get())[1].lower()
        if file_ext not in ['.csv', '.xlsx', '.xls', '.txt', '.list']:
            messagebox.showerror("错误", "请选择csv、excel、txt、list文件")
            return

        path = self.app.path_entry2.get()
        if path:
            self.dt.var['ezqc_new'] = self.DataM.read_list(path)
            self.set_varname()

    def extract_words(self):
        """
        从文本中提取所有单词
        """
        text = self.app.text_entry.get("1.0", tk.END)
        if not text:
            messagebox.showerror("错误", "请输入文本")
            return

        self.dt.var['ezqc_new'] = self.DataM.extract_words_as_df(text)
        self.set_varname()
        
        

    def merge_newdata(self):
        # 弹出一个对话框，询问将df列的变量，各处一个文本输入框来确定，在增加另一个文本输入框，询问该变量的批次
        self.dialog2 = tk.Tk()
        self.dialog2.title("设置变量名")
        self.dialog2.geometry("400x300")

        # 初始化变量
        column_combo = None
        batch_entry = None
        
        # 检查df是否已有ezqcid列
        df = self.dt.var['ezqc_filter'].copy() if self.dt.var['ezqc_filter'] is not None else self.dt.var['ezqc_new'].copy()
        if 'ezqcid' in df.columns:
            ttk.Label(self.dialog2, text="表格中已有ezqcid列，无需设置", font=self.app.font_13).place(x=10, y=10)
        else:
            ttk.Label(self.dialog2, text="请选择要设置为ezqcid的列:", font=self.app.font_13).place(x=10, y=10)
            column_combo = ttk.Combobox(self.dialog2, values=list(df.columns), font=self.app.font_12, state='readonly')
            column_combo.place(x=10, y=50)
            
        # 检查df是否已有ezqcbatch列
        if 'ezqcbatch' in df.columns:
            ttk.Label(self.dialog2, text="表格中已有ezqcbatch列，无需设置", font=self.app.font_13).place(x=10, y=90)
        else:
            ttk.Label(self.dialog2, text="请设置批次:", font=self.app.font_13).place(x=10, y=90)
            batch_entry = ttk.Entry(self.dialog2, font=self.app.font_12)
            batch_entry.place(x=10, y=120)
            batch_entry.insert(0, "1")  # 设置默认值为ezqcbatch

        # 确定按钮
        def set_var():
            raw_df = df.copy()
            
            # 检查变量名控件是否存在
            if not column_combo:
                varname = None
            elif column_combo and not column_combo.get():
                messagebox.showerror("错误", "变量名不能为空")
                return None
            else:
                varname = column_combo.get()

            # 检查批次控件是否存在
            if not batch_entry:
                batch = None
            elif batch_entry and not batch_entry.get():
                messagebox.showerror("错误", "批次不能为空")
                return None
            else:
                batch = batch_entry.get()

            result_df = self.DataM.set_varname_batch(raw_df, varname, batch)
            return result_df

        def show_tmp_df():
            df = set_var()
            self.TablD.show_df(df) 
        ttk.Button(self.dialog2, text="查看", command=show_tmp_df).place(x=20, y=200)

        def set_var_():
            self.dt.var['ezqc_filter'] = set_var()
            self.dialog2.destroy()
            self.new_merge()
        ttk.Button(self.dialog2, text="确定", command=set_var_).place(x=120, y=200)
        ttk.Button(self.dialog2, text="取消", command=self.dialog2.destroy).place(x=220, y=200)


    ##########################      ####################################


    def new_merge(self):

        if self.dt.var['ezqc_all'] is None or len(self.dt.var['ezqc_all']) == 0:
            tmp = self.dt.var['ezqc_filter'].copy()
            # 检查是否存在filter数据
            if tmp is None or len(tmp) == 0:
                messagebox.showinfo("信息", "没有可显示的数据")
                return
            self.dt.var['ezqc_all'] = tmp
            self.ProjM.save_table('ezqc_all')
            messagebox.showinfo("信息", "合并完成")

        else:
            
            # 创建合并变量选项对话框
            merge_dialog = tk.Tk()
            merge_dialog.title("合并变量")
            merge_dialog.geometry("300x300")
            
            # 提示标签
            ttk.Label(merge_dialog, text="已经存在总变量，请问是否合并变量?", 
                     font=self.app.font_13).pack(pady=20)
            
            # 按钮框架
            button_frame = ttk.Frame(merge_dialog)
            button_frame.pack(pady=20)
            self.df_tmp = self.dt.var['ezqc_filter'].copy()
            # 检查是否存在filter数据
            if self.df_tmp is None or len(self.df_tmp) == 0:
                messagebox.showinfo("信息", "没有可显示的数据")
                return
            
            def fresh_tables():
                self.ProjM.save_table('ezqc_all')
                self.ProjM.save_table('table',delete=True)
                self.ProjM.load_project(self.dt.project)
                merge_dialog.destroy()

            # 合并成新行按钮
            def merge_as_rows():
                self.dt.var['ezqc_all'] = pd.concat([self.dt.var['ezqc_all'], self.df_tmp]) if self.dt.var['ezqc_all'] is not None else self.df_tmp
                messagebox.showinfo("信息", "合并完成")
                fresh_tables()
                
            ttk.Button(button_frame, text="合并成新行", command=merge_as_rows).pack(pady=5)
            
            # 合并成新列按钮
            def merge_as_columns():
                self.dt.var['ezqc_all'] = pd.merge(self.dt.var['ezqc_all'], self.df_tmp, on='ezqcid', how='outer') if self.dt.var['ezqc_all'] is not None else self.df_tmp.copy()
                fresh_tables()
            ttk.Button(button_frame, text="合并成新列", command=merge_as_columns).pack(pady=5)
            
            # 替换按钮
            def replace():
                self.dt.var['ezqc_all'] = self.df_tmp
                fresh_tables()
            ttk.Button(button_frame, text="替换",command=replace).pack(pady=5)
            
            # 取消按钮
            ttk.Button(button_frame, text="取消",command=merge_dialog.destroy).pack(pady=5)


        

    def show_all_variable(self):
        pass


    # #####################################################################################
    def add_module(self, name=None, label=None, index=None):
        """添加新模块对话框"""
        # 创建对话框
        title = "添加新模块" if name is None else "修改模块"
        dialog = tk.Toplevel(self.app.root)
        dialog.title(title)
        dialog.geometry("400x280")
        # dialog.grab_set()
        dialog.transient(self.app.root)

        # 模块名称
        ttk.Label(dialog, text="模块名称:", font=self.app.font_13).place(x=30, y=30)
        name_entry = ttk.Entry(dialog, font=self.app.font_12)
        name_entry.place(x=120, y=30, width=230)
        if name is not None:
            name_entry.insert(0, name)

        # 模块标题
        ttk.Label(dialog, text="模块标题:", font=self.app.font_13).place(x=30, y=80)
        label_entry = ttk.Entry(dialog, font=self.app.font_12)
        label_entry.place(x=120, y=80, width=230)
        if label is not None:
            label_entry.insert(0, label)

        # 序号输入
        if index is not None:
            next_index = str(index)
        else:
            if self.dt.settings.get('qcmodule'):
                tmp_index = max(int(idx) for idx in self.dt.settings['qcmodule'].keys())
                next_index = str(tmp_index + 1)
            else:
                next_index = "1"

        ttk.Label(dialog, text="模块序号:", font=self.app.font_13).place(x=30, y=130)
        index_entry = ttk.Entry(dialog, font=self.app.font_12)
        index_entry.place(x=120, y=130, width=230)
        index_entry.insert(0, next_index)

        # 按钮回调函数
        def create_module():
            name_ = name_entry.get().strip()
            label_ = label_entry.get().strip()
            index_ = index_entry.get().strip()
            print(f"{name_} {label_} {index_}")

            if not all([name_, label_, index_]):
                messagebox.showerror("错误", "所有字段都必须填写")
                return
            if not index_.isdigit():
                messagebox.showerror("错误", "序号必须是正整数")
                return
            if not re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name_):
                messagebox.showerror("错误", "模块名称必须以字母或下划线开头，后面可以是字母、数字或下划线")
                return

            names = [item['name'] for item in self.dt.settings['qcmodule'].values()]
            if name_ in names and name is None:
                messagebox.showerror("错误", "模块名称已存在")
                return

            if name in names:
                names.remove(name)
            if name is not None and name_ in names:
                messagebox.showerror("错误", "模块序号已存在")
                return

            if name is not None:
                self.ProjM.modify_qcmodule(index, index_=index_, name_=name_, label_=label_)
            else:
                self.ProjM.add_qcmodule(self.dt.settings['qcmodule'], name =name_, label =label_, index=index_)

            # 重新整理 index，确保连续
            sorted_modules = sorted(self.dt.settings['qcmodule'].items(), key=lambda x: int(x[0]))
            reordered_modules = {}
            for i, (old_index, module_data) in enumerate(sorted_modules, 1):
                reordered_modules[str(i)] = module_data
            self.dt.settings['qcmodule'] = reordered_modules
            # # 删除self.dt.settings['qcpresent']中没有在self.dt.settings['qcmodule']中的模块
            # modules_ = [item['name'] for item in self.dt.settings['qcmodule'].values()]
            # self.dt.settings['qcpresent'] = [item for item in self.dt.settings['qcpresent'] if item in modules_]

            self.ProjM.save_settings()
            self.app.load_module_to_gui()

            dialog.destroy()

        # 按钮
        text = "创建" if name is None else "修改"
        ttk.Button(dialog, text=text, command=create_module).place(x=120, y=200, width=80)
        ttk.Button(dialog, text="取消", command=dialog.destroy).place(x=220, y=200, width=80)

    def change_module_index(self, index, module):
        frame = tk.Toplevel(self.app.root)
        frame.title("修改模块信息")
        frame.geometry("400x280")
        frame.transient(self.app.root)
        frame.grab_set()  # 设置为模态对话框
        
        # 用于跟踪操作结果
        result = {'success': False}
        
        ttk.Label(frame, text="模块名称:", font=self.app.font_13).place(x=30, y=30)
        name_entry = ttk.Entry(frame, font=self.app.font_12)
        name_entry.place(x=120, y=30, width=230)
        name_entry.insert(0, module['name'])

        ttk.Label(frame, text="模块标题:", font=self.app.font_13).place(x=30, y=80)
        label_entry = ttk.Entry(frame, font=self.app.font_12)
        label_entry.place(x=120, y=80, width=230)
        label_entry.insert(0, module['label'])

        ttk.Label(frame, text="模块序号:", font=self.app.font_13).place(x=30, y=130)
        index_entry = ttk.Entry(frame, font=self.app.font_12)
        index_entry.place(x=120, y=130, width=230)
        index_entry.insert(0, index)
        
        def change_module():
            index_ = next((i for i, m in self.dt.settings['qcmodule'].items() if m['name'] == name_entry.get()), None)
            if index_ is not None:
                messagebox.showerror("错误", "模块名称已存在,请修改模块名称")
            else:
                module['name'] = name_entry.get()
                module['label'] = label_entry.get()
                index = index_entry.get()
                self.dt.settings['qcmodule'] = self.ProjM.add_key(self.dt.settings['qcmodule'], int(index), module)
                result['success'] = True  # 标记操作成功
                frame.grab_release()  # 释放模态状态
                frame.destroy()

        def cancel_dialog():
            frame.grab_release()  # 释放模态状态
            frame.destroy()
            
        ttk.Button(frame, text="确定", command=change_module).place(x=120, y=170, width=80)
        ttk.Button(frame, text="取消", command=cancel_dialog).place(x=220, y=170, width=80)
        
        # 等待对话框关闭
        frame.wait_window()
        
        # 返回操作结果
        return result['success']

    def import_module(self):
        file_path = filedialog.askopenfilename(
            title="请选择qcmodule模块文件",
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")]
        )
        if not file_path:
            return
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                module = json.load(f)

            if not self.ProjM.check_module(module):
                messagebox.showerror("错误", "模块文件不合法")
                return

            module_name = module['name']
            index = next((i for i, m in self.dt.settings['qcmodule'].items() if m['name'] == module_name), None)
            if index is not None:
                # 弹出一个框警告用户，模块名称已存在，请修改模块名称
                if not messagebox.askyesno("警告", "模块名称已存在，请修改模块名称"):
                    messagebox.showinfo("提示", "放弃导入")
                    return
                if self.change_module_index(index, module):
                    self.ProjM.save_settings()
                    self.app.load_module_to_gui()
                    messagebox.showinfo("提示", "模块导入成功")
            else:
                self.ProjM.import_module(module, check=False)
                self.ProjM.save_settings()
                self.app.load_module_to_gui()
                messagebox.showinfo("提示", "模块导入成功")
        except Exception as e:
            messagebox.showerror("错误", f"导入模块失败: {str(e)}")



    def export_module(self, module_name):
        folder_selected = filedialog.askdirectory(title="请选择导出路径")
        if folder_selected:
            self.ProjM.export_module(module_name, os.path.join(folder_selected, f"qcmodule_{module_name}.json"))




    def manage_module(self):
        
        # 弹出一个框
        dialog = tk.Toplevel(self.app.root)
        dialog.title("管理模块")
        dialog.geometry("400x300")
        dialog.transient(self.app.root)

        # 一句文字，双击修改模块设置，右键删除模块
        ttk.Label(dialog, text="左键双击修改模块设置，右键单击删除模块", font=self.app.font_13).pack(pady=5)
        table_frame = ttk.Frame(dialog)
        table_frame.pack(fill=tk.BOTH, padx=20, pady=5, expand=True)
        
        # 创建带滚动条的表格框架
        table_scroll = ttk.Scrollbar(table_frame)
        table_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        table = ttk.Treeview(table_frame, columns=("序号", "名称", "标题"),show="headings",yscrollcommand=table_scroll.set)
        table.heading("序号", text="序号")
        table.heading("名称", text="名称")
        table.heading("标题", text="标题")
        table.column("序号", width=50)
        table.column("名称", width=150)
        table.column("标题", width=200)

        table_scroll.config(command=table.yview)
        table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 将self.dt.setting['qcmodule']放进去
        for index, module in self.dt.settings['qcmodule'].items():
            table.insert("", "end", values=(index, module['name'], module['label']))
        # 绑定双击
        def on_double_click(event):
            item = table.selection()[0]
            index = table.item(item, "values")[0]
            name = table.item(item, "values")[1]
            label = table.item(item, "values")[2]
            self.add_module(index=index, name=name, label=label)
            # 删除后刷新表格
            for child in table.get_children():
                table.delete(child)
            for index, module in self.dt.settings['qcmodule'].items():
                table.insert("", "end", values=(index, module['name'], module['label']))
        table.bind("<Double-1>", on_double_click)

        # 右键弹出一个菜单，菜单上是"删除该模块"，点击这个菜单，执行self.delete_module(index)
        def on_right_click(event):
            try:
                if not table.selection():
                    return
                item = table.selection()[0]
                index = table.item(item, "values")[0]
                module_name = table.item(item, "values")[1]  # 获取要删除的模块名称

                # 检查还剩几个module，如果只剩一个了，就弹框"只剩下1个模块，不可删除，可以修改"
                if len(self.dt.settings['qcmodule']) == 1:
                    messagebox.showerror("错误", "只剩下1个模块，不可删除，您可以修改")
                    return
                
                if not messagebox.askyesno("确认", "是否删除该模块?"):
                    return
                
                self.dt.settings['qcmodule'] = self.ProjM.add_key(self.dt.settings['qcmodule'], index)
                self.ProjM.save_settings()
                self.app.load_module_to_gui()
                
                # 刷新表格
                for child in table.get_children():
                    table.delete(child)
                for index, module in self.dt.settings['qcmodule'].items():
                    table.insert("", "end", values=(index, module['name'], module['label']))
                
            except Exception as e:
                log_error(f"右键删除模块时发生错误: {e}")
        
        # 跨平台右键事件绑定
        if self.dt.system == "Darwin":  # macOS
            table.bind("<Button-2>", on_right_click)  # macOS右键
            table.bind("<Control-Button-1>", on_right_click)  # macOS Ctrl+左键
        else:  # Windows和Linux
            table.bind("<Button-3>", on_right_click)

        def add_module():
            self.add_module()
            for child in table.get_children():
                table.delete(child)
            for index, module in self.dt.settings['qcmodule'].items():
                table.insert("", "end", values=(index, module['name'], module['label']))

        # 增加一个按钮，点击这个按钮，执行self.add_module()
        button_frame = ttk.Frame(dialog)
        button_frame.pack(pady=5)
        ttk.Button(button_frame, text="增加模块", command=add_module).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="完成", command=dialog.destroy).pack(side=tk.LEFT, padx=5)



    def start_qc(self, module):
        """开始质控，打开质控页面"""
        try:
            log_info(f"开始质控模块: {module.get('name','未知')}")
            self.qcpage.open_qcpage_from_main(self.app, module['name'])
        except Exception as e:
            log_error(f"打开质控页面时发生错误: {e}")
            messagebox.showerror("错误", f"无法打开质控页面: {e}")

    def add_score(self, qcindex, idx):
        module = {'label': None, 'num': None, 'num_': None, 'value': None}
        self.dt.settings['qcmodule'][qcindex]['scores'] = self.ProjM.add_key(self.dt.settings['qcmodule'][qcindex]['scores'], idx, module)
        self.ProjM.save_settings()
        self.app.load_module_to_gui()

    def del_score(self, qcindex, idx):
        self.dt.settings['qcmodule'][qcindex]['scores'] = self.ProjM.add_key(self.dt.settings['qcmodule'][qcindex]['scores'], idx)
        self.ProjM.save_settings()
        self.app.load_module_to_gui()


    def validate_score(self, value, show_error=True):
        
        value = value.strip()
        msgbox = """分值必须为以下形式：\n
        (1): "Poor,Fair,Good" - 字符串形式的分类标签，用逗号分隔\n
        (2): "1,2,3,4" - 表示1,2,3,4的连续数值\n
        (3): "0-3" - 表示0,1,2,3的连续数值范围"""
        
        # 检查是否为空
        if not value:
            return None
            
        try:
            # 判断分类一：字符串形式的分类标签，用逗号分隔
            pattern1 = r'^\s*[a-zA-Z0-9_ ]+\s*(,\s*[a-zA-Z0-9_ ]+\s*)*,?\s*$'
            if re.match(pattern1, value) and ',' in value:
                # 提取分类标签列表
                labels = [label.strip() for label in value.split(',')]
                # 检查是否有重复标签
                if len(labels) != len(set(labels)):
                    if show_error:
                        messagebox.showerror("错误", "分类标签中不能有重复项")
                    return None
                result = labels
 
            # 判断分类二：数字范围格式 "数字-数字"
            elif re.match(r'^\s*(\d+)\s*-\s*(\d+)\s*$', value) and '-' in value:
                match2 = re.match(r'^\s*(\d+)\s*-\s*(\d+)\s*$', value)
                start = int(match2.group(1))
                end = int(match2.group(2))
                if start > end:
                    if show_error:
                        messagebox.showerror("错误", "范围起始值不能大于结束值")
                    return None
                result = ','.join(str(i) for i in range(start, end + 1))

            # 判断分类三：单个数字表示连续数值
            elif re.match(r'^\s*(\d+)\s*$', value):
                match3 = re.match(r'^\s*(\d+)\s*$', value)
                max_val = int(match3.group(1))
                if max_val <= 0:
                    if show_error:
                        messagebox.showerror("错误", "数值必须大于0")
                    return None
                result = ','.join(str(i) for i in range(1, max_val + 1))
            
            else:
                if show_error:
                    messagebox.showerror("错误", msgbox)
                return None
                
            return result
            
        except ValueError as e:
            if show_error:
                messagebox.showerror("错误", f"{msgbox}\n\n错误详情: {str(e)}")
            return None

    def add_tag(self, qcindex, idx):
        module = {'label': None, 'value': None}
        self.dt.settings['qcmodule'][qcindex]['tags'] = self.ProjM.add_key(self.dt.settings['qcmodule'][qcindex]['tags'], idx, module)
        self.ProjM.save_settings()
        self.app.load_module_to_gui()

    def del_tag(self, qcindex, idx):
        self.dt.settings['qcmodule'][qcindex]['tags'] = self.ProjM.add_key(self.dt.settings['qcmodule'][qcindex]['tags'], idx)
        self.ProjM.save_settings()
        self.app.load_module_to_gui()


