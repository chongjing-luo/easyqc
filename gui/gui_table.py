

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC GUI表格显示模块
包含DataFrame显示和过滤功能
"""

import tkinter as tk
from tkinter import messagebox
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_info, log_error, log_warning, log_exception, log_debug, LogContext, log_function

from utils.projects_manager import ProjectManager
from utils.data_manager import DataManager 
from gui.state_adapter import LegacyGUIStateAdapter
from gui.table_view import TableTransformDialog, TableView, open_qc_subprocess

class TableDisplay:
    """表格显示功能类"""
    
    def __init__(self, app=None):
        """初始化表格显示功能类
        
        Args:
            app: EasyQCApp实例，用于访问应用程序的属性和方法
        """
        if app is not None:
            self.app = app
            project_manager = getattr(app, 'ProjM', None)
            self.gui_state = getattr(app, 'gui_state', None)
            if self.gui_state is None:
                self.gui_state = LegacyGUIStateAdapter(project_manager)
            self.DataM = self.app.DataM
            services = getattr(self.app, 'services', None)
            self.table_transform = getattr(services, 'table_transform', getattr(self.app, 'table_transform', None))
        else:
            self.ProjM = ProjectManager()
            self.ProjM.init_projects()
            self.ProjM.load_project(self.ProjM.dt.project, fresh_gui=False)
            self.dt = self.ProjM.dt
            self.gui_state = LegacyGUIStateAdapter(self.ProjM, self.dt)
            self.DataM = DataManager()
            self.table_transform = None
        if self.table_transform is not None and hasattr(self.DataM, 'table_transform'):
            self.DataM.table_transform = self.table_transform
        
    
    def show_df(self, df):
        """
        在新窗口中显示DataFrame，带有滚动条和列处理
        创建独立的表格窗口，可以脱离主应用程序存在
        """
        parent = self.app.root if hasattr(self, 'app') and hasattr(self.app, 'root') else None
        self.table_view = TableView(
            parent,
            table_transform=getattr(self, 'table_transform', None),
            on_ezqcid_right_click=self.show_right_menu,
        )
        window = self.table_view.show_df(df)
        self.tree_df = self.table_view.tree
        return window

    def module_names_for_menu(self):
        return self.state_adapter().module_names()

    def rating_menu_items(self, ezqcid):
        return self.state_adapter().rating_menu_items(ezqcid)

    def state_adapter(self):
        if not hasattr(self, 'gui_state'):
            self.gui_state = LegacyGUIStateAdapter(getattr(self, 'ProjM', None), getattr(self, 'dt', None))
        return self.gui_state


    def show_right_menu(self, ezqcid, event):
        """
        显示右键菜单，显示该ezqcid的所有质控结果
        """
        if not self.state_adapter().has_rating_data():
            messagebox.showwarning("警告", "评分数据未加载")
            return
            

        # 创建右键菜单
        menu = tk.Menu(self.app.root if hasattr(self, 'app') and hasattr(self.app, 'root') else None, tearoff=0)
        for module in self.module_names_for_menu():
            label = f'打开图像: {module}'
            menu.add_command(
                label=label,
                command=lambda m=module, e=ezqcid: self.open_image_from_right_menu(e, m)
            )

        
        # 获取该ezqcid的所有评分结果
        for item in self.rating_menu_items(ezqcid):
            menu.add_command(
                label=item['label'],
                command=lambda ezqcid=ezqcid, name=item['name'], rater=item['rater']: self.open_gui(ezqcid, name, rater)
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
        
        open_qc_subprocess(self.state_adapter().current_project_name(), module_name, rater, ezqcid)

        


    def open_image_from_right_menu(self, ezqcid, module_name):
        """
        打开图片
        """
        # 延迟导入，避免循环导入
        from gui.gui_qcpage import gui_qcpage
        
        log_info(f"open_image_from_right_menu: ezqcid={ezqcid}, module_name={module_name}", "TableDisplay")
        state = self.state_adapter()
        module_index = state.module_index_by_name(module_name)
        module = state.module_by_key(module_index)
        table = state.result_table('ezqc_qctable')
        settings = state.settings()
        
        # 创建qcpage实例并保存为类属性，防止被垃圾回收
        if not hasattr(self, 'qcpage_instances'):
            self.qcpage_instances = []
        
        qcpage_instance = gui_qcpage()
        self.qcpage_instances.append(qcpage_instance)  # 保存引用，防止被垃圾回收
        
        code,code_exe = qcpage_instance.gen_code(ezqcid, settings, module, table)
        qcpage_instance.exe_code(code_exe, control=False)
        
    def resolve_filter_source(self, type=None, df=None):
        df, select_filter = self.state_adapter().resolve_filter_source(type, df)
        log_info(f"select_filter: {select_filter}")
        log_debug(f"filter_sorter type: {type}", "TableDisplay")
        return df, select_filter

    def parse_transform_operations(self, query):
        return self.create_table_transform_dialog().parse_operations(query)

    def default_transform_template(self, df=None):
        return self.create_table_transform_dialog().default_template(df)

    def execute_filter_query(self, df, query):
        return self.create_table_transform_dialog().execute_query(df, query)

    def create_table_transform_dialog(self):
        parent = self.app.root if hasattr(self, 'app') and hasattr(self.app, 'root') else None
        return TableTransformDialog(
            parent,
            table_transform=getattr(self, 'table_transform', None),
            data_manager=getattr(self, 'DataM', None),
        )

    def save_filter_result(self, type, df_output, query):
        self.state_adapter().save_filter_result(type, df_output, query)
        if type not in {'new', 'all', 'qctable'}:
            log_info(f"已保存筛选结果到结果表 '{type}'，数据行数: {len(df_output)}")

    def restore_filter_source(self, type, df):
        return self.state_adapter().restore_filter_source(type, df)
    
    def filter_sorter(self, type=None, df=None):
        """
        使用 JSON 结构化操作过滤、排序和转换表格数据
        """
        try:
            df, select_filter = self.resolve_filter_source(type, df)
        except ValueError:
            messagebox.showinfo("信息", "缺乏必要参数")
            return
                
        if df is None or df.empty:
            messagebox.showinfo("信息", "输入数据为空")
            return

        return self.create_table_transform_dialog().open_filter_dialog(
            df,
            select_filter=select_filter,
            result_type=type,
            on_show_df=self.show_df,
            on_save_result=self.save_filter_result,
            on_restore_source=self.restore_filter_source,
            on_empty_result=lambda: log_error("筛选后是空数据集"),
        )


        
