

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC GUI表格显示模块
包含DataFrame显示和过滤功能
"""

from locale import D_FMT
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import sys, platform, os
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_info, log_error, log_warning, log_exception, log_debug, LogContext, log_function

import pandas as pd
from utils.projects_manager import ProjectManager
from utils.data_manager import DataManager 

class TableDisplay:
    """表格显示功能类"""
    
    def __init__(self, app=None):
        """初始化表格显示功能类
        
        Args:
            app: EasyQCApp实例，用于访问应用程序的属性和方法
        """
        if app is not None:
            self.app = app
            self.dt = self.app.ProjM.dt
            self.ProjM = self.app.ProjM
            self.DataM = self.app.DataM
        else:
            self.ProjM = ProjectManager()
            self.ProjM.init_projects()
            self.ProjM.load_project(self.ProjM.dt.project, fresh_gui=False)
            self.dt = self.ProjM.dt
            self.DataM = DataManager()
        
    
    def show_df(self, df):
        """
        在新窗口中显示DataFrame，带有滚动条和列处理
        创建独立的表格窗口，可以脱离主应用程序存在
        """
        if df is None or df.empty:
            messagebox.showinfo("信息", "没有数据可显示")
            return
            
        # 创建独立的根窗口，而不是Toplevel
        df_window = tk.Tk()
        df_window.title("数据表格 - EasyQC")
        df_window.geometry("900x700")
        df_window.minsize(600, 400)
        
        # 创建主框架，使用grid布局以更好地控制滚动条
        main_frame = ttk.Frame(df_window)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # 配置grid权重
        main_frame.grid_rowconfigure(0, weight=1)
        main_frame.grid_columnconfigure(0, weight=1)
        
        # 创建Treeview
        self.tree_df = ttk.Treeview(main_frame)
        self.tree_df.grid(row=0, column=0, sticky='nsew')
        
        # 创建垂直滚动条
        v_scrollbar = ttk.Scrollbar(main_frame, orient=tk.VERTICAL, command=self.tree_df.yview)
        v_scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree_df.configure(yscrollcommand=v_scrollbar.set)

        # 创建水平滚动条
        h_scrollbar = ttk.Scrollbar(main_frame, orient=tk.HORIZONTAL, command=self.tree_df.xview)
        h_scrollbar.grid(row=1, column=0, sticky='ew')
        self.tree_df.configure(xscrollcommand=h_scrollbar.set)
        
        df = df.copy()
        # 如果存在ezqcid和ezqcbatch列，将它们移到最前面
        priority_cols = ['ezqcid', 'ezqcbatch']
        existing_priority_cols = [col for col in priority_cols if col in df.columns]
        if existing_priority_cols:
            other_cols = [col for col in df.columns if col not in existing_priority_cols]
            df = df[existing_priority_cols + other_cols]

        # 设置列,包含index列和数据列
        columns = ['Index'] + list(df.columns)
        self.tree_df['columns'] = columns
        self.tree_df['show'] = 'headings'
        
        # 设置列标题和宽度
        # 计算每列内容的最大宽度
        col_widths = {}
        
        # 计算Index列宽度
        col_widths['Index'] = 60  # Index列固定宽度为60
        
        # 计算其他列的宽度
        for col in df.columns:
            # 获取列标题宽度
            header_width = len(str(col)) * 8
            
            # 获取该列所有值的最大宽度（限制最大检查行数以提高性能）
            sample_size = min(100, len(df))
            sample_data = df[col].head(sample_size) if len(df) > sample_size else df[col]
            
            max_content_width = max(
                (len(str(val)) * 8 for val in sample_data),
                default=80  # 如果列为空，默认宽度80
            )
            
            # 取列标题宽度和内容最大宽度的较大值，设置合理的最小和最大宽度
            col_width = max(header_width, max_content_width, 80)
            col_width = min(col_width, 300)  # 限制最大宽度为300
            col_widths[col] = col_width
        
        # 设置每列的宽度和标题
        for col in columns:
            self.tree_df.heading(col, text=col)
            # 设置列属性：width为初始宽度，minwidth为最小宽度，stretch=True允许拉伸
            self.tree_df.column(col, width=col_widths[col], minwidth=60, stretch=False, anchor='w')
        
        # 插入数据
        for row_num, (index, row) in enumerate(df.iterrows(), 1):
            # 在values列表开头添加行号
            # 处理NaN值，将其显示为空字符串而不是"nan"
            processed_values = []
            for val in row.values:
                if pd.isna(val):
                    processed_values.append('')  # NaN值显示为空
                else:
                    processed_values.append(str(val))
            
            values = [str(row_num)] + processed_values
            self.tree_df.insert('', 'end', values=values)
        
        # 添加状态栏显示数据信息
        status_frame = ttk.Frame(df_window)
        status_frame.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        status_label = ttk.Label(status_frame, text=f"总计: {len(df)} 行, {len(df.columns)} 列")
        status_label.pack(side=tk.LEFT)
        
        # 创建右键菜单
        def show_right_menu(event):
            """
            提取这一行的ezqcid，传递给self.show_right_menu
            """
            # 获取点击的行
            item = self.tree_df.identify_row(event.y)
            if item:
                # 获取该行的数据
                values = self.tree_df.item(item, 'values')
                if values and len(values) > 1:  # 确保有数据且至少有ezqcid列
                    # 找到ezqcid列的索引（通常是第2列，因为第1列是Index）
                    ezqcid_index = None
                    for i, col in enumerate(columns):
                        if col == 'ezqcid':
                            ezqcid_index = i
                            break
                    
                    if ezqcid_index is not None and ezqcid_index < len(values):
                        ezqcid = values[ezqcid_index]
                        # 调用show_right_menu方法
                        print(f"ezqcid: {ezqcid}")
                        self.show_right_menu(ezqcid, event)
        # 绑定右键菜单
        if platform.system() in ["Linux", "Windows"]:
            self.tree_df.bind("<Button-3>", show_right_menu)
        elif platform.system() == "Darwin":
            self.tree_df.bind("<Button-2>", show_right_menu)

        # 绑定键盘快捷键
        df_window.bind('<Escape>', lambda e: df_window.destroy())
        df_window.bind('<Control-w>', lambda e: df_window.destroy())
        
        # 让窗口获得焦点
        df_window.focus_force()
        
        # 返回窗口对象，以便调用者可以进一步操作
        return df_window


    def show_right_menu(self, ezqcid, event):
        """
        显示右键菜单，显示该ezqcid的所有质控结果
        """
        if not hasattr(self.ProjM.dt, 'rating_dict'):
            messagebox.showwarning("警告", "评分数据未加载")
            return
            

        qcmodules = [v['name'] for k, v in self.dt.settings['qcmodule'].items()]
        # 创建右键菜单
        menu = tk.Menu(self.app.root if hasattr(self, 'app') and hasattr(self.app, 'root') else None, tearoff=0)
        for module in qcmodules:
            label = f'打开图像: {module}'
            menu.add_command(
                label=label,
                command=lambda m=module, e=ezqcid: self.open_image_from_right_menu(e, m)
            )

        
        # 获取该ezqcid的所有评分结果
        if hasattr(self.ProjM.dt, 'rating_dict') and ezqcid in self.ProjM.dt.rating_dict:
            rating_data = self.ProjM.dt.rating_dict[ezqcid]
            if rating_data:
                keys = list(rating_data.keys())
                for key in keys:
                    label = f'打开评分结果: {key}'
                    rater = rating_data[key]['rater']
                    name = rating_data[key]['name']
                    menu.add_command(
                        label=label,
                        command=lambda ezqcid=ezqcid, name=name, rater=rater: self.open_gui(ezqcid,name, rater)
                    )
            
        # 在鼠标位置显示菜单
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def open_gui(self, ezqcid, module_name, rater):
        """
        处理右键菜单点击事件
        """
        log_info(f"右键菜单点击: ezqcid={ezqcid}, module={module_name}, rater={rater}")
        
        project_root = Path(__file__).parent
        project_root = project_root.parent
        cmd = f"python3 {project_root}/easyqc.py {self.dt.project} {module_name} {rater} {ezqcid}"
        os.system(cmd)

        


    def open_image_from_right_menu(self, ezqcid, module_name):
        """
        打开图片
        """
        # 延迟导入，避免循环导入
        from gui.gui_qcpage import gui_qcpage
        # print(f"module_name: {module_name}")
        
        print(f"open_image_from_right_menu: ezqcid={ezqcid}, module_name={module_name}")
        module_index = [k for k, v in self.dt.settings['qcmodule'].items() if v.get('name') == module_name][0]
        module = self.dt.settings['qcmodule'][module_index]
        table = self.dt.tab['ezqc_qctable']
        
        # 创建qcpage实例并保存为类属性，防止被垃圾回收
        if not hasattr(self, 'qcpage_instances'):
            self.qcpage_instances = []
        
        qcpage_instance = gui_qcpage()
        self.qcpage_instances.append(qcpage_instance)  # 保存引用，防止被垃圾回收
        
        # 设置必要的属性
        qcpage_instance.dt = self.dt
        qcpage_instance.module_index = module_index
        
        code,code_exe = qcpage_instance.gen_code(ezqcid, self.dt.settings, module, table)
        qcpage_instance.exe_code(code_exe, control=False)
        
    
    def filter_sorter(self, type=None, df=None):
        """
        过滤和排序数据，使用pandas SQL语法
        """
        select_filter = None
        if type is None:
            df = df.copy()
        elif df is None and type is not None:
            if type == 'new':
                df = self.dt.var['ezqc_new'].copy() if self.dt.var['ezqc_new'] is not None else None
            elif type == 'all':
                select_filter = self.dt.settings['var_select_filter']
                df = self.dt.var['ezqc_all'].copy()
            elif type == 'qctable':
                pass
            else:
                matching_indices = [k for k, v in self.dt.settings['qcmodule'].items() if v.get('name') == type]
                index = matching_indices[0] if matching_indices else None
                select_filter = self.dt.settings['qcmodule'][index]['select_filter'] if index is not None else None
                df = self.dt.tab.get('ezqc_qctable', None)
                if df is not None:
                    df = df.copy()
                else:
                    df = self.dt.var.get('ezqc_all', None)
                    if df is not None:
                        df = df.copy()
        else:
            messagebox.showinfo("信息", "缺乏必要参数")
            return
                
        if df is None or df.empty:
            messagebox.showinfo("信息", "输入数据为空")
            return

        # 创建过滤对话框
        filter_dialog = tk.Tk()
        filter_dialog.title("数据过滤")
        filter_dialog.geometry("400x600")
        
        # 创建主框架
        main_frame = ttk.Frame(filter_dialog)
        main_frame.place(x=10, y=10, width=380, height=150)
        
        # SQL查询输入框
        sql_label = ttk.Label(main_frame, text="SQL查询语句:")
        sql_label.place(x=0, y=0)
        
        # 创建文本框容器frame
        text_container = ttk.Frame(main_frame)
        text_container.place(x=0, y=30, width=380, height=120)
        
        # 创建带滚动条的文本框
        sql_text = scrolledtext.ScrolledText(text_container, wrap=tk.WORD, padx=5, pady=5)
        sql_text.pack(fill=tk.BOTH,  expand=True)
        if select_filter:
            sql_text.insert(tk.END, select_filter)
        # 设置文本对齐方式为左对齐
        sql_text.tag_configure( "left", justify='left' )
        sql_text.tag_add("left", "1.0", "end")
        
        # 按钮框架
        button_frame = ttk.Frame(filter_dialog)
        button_frame.place(x=0, y=200, width=380, height=100)
        
        # 执行查询按钮
        def execute_query():
            nonlocal df  # 声明使用外部作用域的df变量
            self.tmp_query = sql_text.get("1.0", tk.END).strip()
            if self.tmp_query:
                try:
                    # 这里应该调用数据管理器的查询方法
                    for col in df.columns:
                        if not pd.api.types.is_numeric_dtype(df[col]):
                            df[col] = df[col].astype(str)
                    result_df = self.DataM.select_filter_sorter(df, self.tmp_query)
                    return result_df
                except Exception as e:
                    messagebox.showerror("错误", f"查询执行失败: {str(e)}")
                    return None
            else:
                messagebox.showwarning("警告", "请输入SQL查询语句")
                return None

        def execute():
            df_output = execute_query()  
            self.show_df(df_output)
        
        def insert_tmp():
            default_query = "SELECT * FROM df WHERE column_name2 > 0 and sex IN ('female', 'other')"
            sql_text.insert(tk.END, default_query)

        def save_var(type=None):
            df_output = execute_query()   
            if df_output is None or df_output.empty:
                log_error(f"筛选后是空数据集")
                return
            if type:
                if type == 'new':
                    self.dt.var['ezqc_filter'] = df_output.copy()   
                elif type == 'all':
                    self.dt.var['ezqc_all'] = df_output.copy()
                elif type == 'qctable':
                    pass
                else:
                    if not hasattr(self.dt, 'tab') or self.dt.tab is None:
                        self.dt.tab = {}
                    self.dt.tab[type] = df_output.copy()
                    index = [k for k, v in self.dt.settings['qcmodule'].items() if v.get('name') == type][0]
                    self.dt.settings['qcmodule'][index]['select_filter'] = self.tmp_query
                    self.ProjM.save_settings()
                    log_info(f"已保存筛选结果到 self.dt.tab['{type}']，数据行数: {len(df_output)}")
            else:
                return df_output.copy()
            self.save_filter_df = True

             
        def cancel_(type=None):
            filter_dialog.destroy()
            nonlocal df
            if type is not None:
                if type == 'new':
                    self.dt.var['ezqc_filter'] = df.copy()
                elif type == 'all':
                    self.dt.var['ezqc_all'] = df.copy()
                elif type == 'qctable':
                    pass
                else:
                    self.dt.tab[type] = df.copy()
            else:
                return df.copy()

        self.save_filter_df = False

        def destroy_(type=None):
            if not self.save_filter_df:
                result = messagebox.askyesno("警告", "是否保存结果？")
                if result:
                    save_var(type)
                else:
                    cancel_(type)
            
            # 检查窗口是否仍然存在
            try:
                if filter_dialog.winfo_exists():
                    filter_dialog.destroy()
            except tk.TclError:
                # 窗口已经被销毁，忽略错误
                pass
            

        ttk.Button(button_frame, text="插入模板", command=insert_tmp).place(x=5, y=0, width=100, height=30)
        ttk.Button(button_frame, text="执行并查看结果", command=execute).place(x=110, y=0, width=140, height=30)
        ttk.Button(button_frame, text="保存结果", command=lambda: save_var(type)).place(x=255, y=0, width=110, height=30)
        ttk.Button(button_frame, text="取消", command=lambda: cancel_(type)).place(x=300, y=35, width=80, height=30)
        ttk.Button(button_frame, text="显示处理前数据", command=lambda: self.show_df(df)).place(x=5, y=35, width=140, height=30)
        ttk.Button(button_frame, text="显示处理后数据", command=execute).place(x=155, y=35, width=140, height=30)
        
        # 绑定窗口关闭事件
        filter_dialog.protocol("WM_DELETE_WINDOW", lambda: destroy_(type))


        