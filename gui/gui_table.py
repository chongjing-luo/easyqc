

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC GUI表格显示模块
包含DataFrame显示和过滤功能
"""

import tkinter as tk
# === GUI i18n: 本文件用户可见文字中英文对照 ===
from gui.i18n import tr as _tr

_T = {
    "警告":           {"zh": "警告",           "en": "Warning"},
    "信息":           {"zh": "信息",           "en": "Info"},
    "评分数据未加载": {"zh": "评分数据未加载", "en": "Rating data not loaded"},
    "缺乏必要参数":   {"zh": "参数错误",       "en": "Invalid parameters"},
    "输入数据为空":   {"zh": "输入数据为空",   "en": "Input data is empty"},
    "取消":           {"zh": "取消",           "en": "Cancel"},
    "数据工作区":     {"zh": "数据工作区 - EasyQC", "en": "Data Workspace - EasyQC"},
    "旧版筛选无法迁移": {
        "zh": "无法迁移此项目的旧版筛选；已显示全部记录。请在右侧筛选器中重新设置。",
        "en": "This project's legacy filter could not be migrated. All records are shown; recreate the filter in the inspector.",
    },
}

from tkinter import messagebox
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_info, log_error, log_warning, log_exception, log_debug, LogContext, log_function

from utils.data_manager import DataManager 
from core.table_transform import TableTransformError, legacy_select_filter_to_operations
from gui.state_bridge import GUIStateBridge
from gui.table_view import TableTransformDialog, open_qc_subprocess
from gui.table_workspace import TableWorkspace
from models.table_view_state import FilterCondition

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
                self.gui_state = GUIStateBridge(getattr(self, 'project_service', None), getattr(self, 'session_state', None), getattr(self, 'table_service', None))
            self.DataM = self.app.DataM
            services = getattr(self.app, 'services', None)
            self.table_transform = getattr(services, 'table_transform', getattr(self.app, 'table_transform', None))
        else:
            self.ProjM = None  # P2-D: ProjectManager removed
            self.ProjM.init_projects()
            self.ProjM.load_project(self.ProjM.dt.project, fresh_gui=False)
            self.dt = self.ProjM.dt
            self.gui_state = getattr(self.app, 'gui_state', None) or GUIStateBridge(getattr(self.app, 'project_service', None), getattr(self.app, 'session_state', None), getattr(self.app, 'table_service', None))
            self.DataM = DataManager()
            self.table_transform = None
        if self.table_transform is not None and hasattr(self.DataM, 'table_transform'):
            self.DataM.table_transform = self.table_transform
        
    
    def show_df(self, df):
        """
        在统一的只读表格工作区中显示 DataFrame。
        """
        if df is None or df.empty:
            messagebox.showinfo(_tr(_T, "信息"), _tr(_T, "输入数据为空"))
            return None
        return self.open_table_workspace(df)

    def open_table_workspace(self, df, legacy_filter=None):
        """Open one read-only workspace and optionally migrate a legacy filter.

        The legacy value is read only.  A supported narrow SELECT subset is
        normalized into typed conditions; no view action is persisted.
        """
        parent = self.app.root if hasattr(self, 'app') and hasattr(self.app, 'root') else None
        self.table_workspace = TableWorkspace(
            parent,
            df,
            on_open_qc=self.show_right_menu,
            title=_tr(_T, "数据工作区"),
        )
        # Compatibility aliases for callers that only need the owning window
        # or keyboard Treeview. They no longer expose the retired table dialog.
        self.table_view = self.table_workspace
        self.tree_df = self.table_workspace.table.main_tree

        if legacy_filter:
            try:
                conditions = self.legacy_filter_conditions(legacy_filter)
            except (TableTransformError, TypeError, ValueError):
                self.table_workspace.action_error_var.set(_tr(_T, "旧版筛选无法迁移"))
            else:
                if conditions:
                    self.table_workspace.begin_filter_edit()
                    self.table_workspace.set_filter_draft(conditions)
                    if not self.table_workspace.apply_filter_draft():
                        self.table_workspace.cancel_filter_draft()
                        self.table_workspace.action_error_var.set(_tr(_T, "旧版筛选无法迁移"))

        return self.table_workspace.window

    @staticmethod
    def legacy_filter_conditions(legacy_filter):
        """Convert the supported stored SELECT subset into typed conditions."""
        operations = legacy_select_filter_to_operations(str(legacy_filter))
        if operations is None:
            raise TableTransformError("旧版筛选不是受支持的 SELECT 子集")
        if not operations:
            return ()
        if len(operations) != 1 or operations[0].get("operation") != "filter_rows":
            raise TableTransformError("旧版筛选包含不支持的表格操作")

        typed = []
        for item in operations[0].get("conditions", ()):
            operator = item.get("operator")
            value = item.get("value")
            if value is None and operator in {"==", "!="}:
                operator = "isna" if operator == "==" else "notna"
            elif value is None:
                raise TableTransformError("旧版空值筛选使用了不支持的比较符")
            typed.append(
                FilterCondition(
                    column=str(item.get("column", "")),
                    operator=str(operator or ""),
                    value=value,
                )
            )
        return tuple(typed)

    def module_names_for_menu(self):
        return self.state_adapter().module_names()

    def rating_menu_items(self, ezqcid):
        return self.state_adapter().rating_menu_items(ezqcid)

    def state_adapter(self):
        if not hasattr(self, 'gui_state'):
            self.gui_state = getattr(self, 'gui_state', None) or GUIStateBridge(getattr(self, 'project_service', None), getattr(self, 'session_state', None), getattr(self, 'table_service', None))
        return self.gui_state


    def show_right_menu(self, ezqcid, anchor):
        """
        显示右键菜单，显示该ezqcid的所有质控结果
        """
        if not self.state_adapter().has_rating_data():
            messagebox.showwarning(_tr(_T, "警告"), _tr(_T, "评分数据未加载"))
            return
            

        # 创建右键菜单
        menu = tk.Menu(self.app.root if hasattr(self, 'app') and hasattr(self.app, 'root') else None, tearoff=0)

        def dismiss_menu(event=None):
            menu.unpost()
            return "break"

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

        menu.add_separator()
        menu.add_command(label="取消", command=dismiss_menu)
        menu.bind("<Escape>", dismiss_menu)
            
        # 在鼠标位置显示菜单
        try:
            x_root, y_root = self.popup_coordinates(anchor)
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    @staticmethod
    def popup_coordinates(anchor):
        """Return popup coordinates for either a pointer event or Tk widget."""
        if hasattr(anchor, "x_root") and hasattr(anchor, "y_root"):
            return int(anchor.x_root), int(anchor.y_root)
        if all(hasattr(anchor, name) for name in ("winfo_rootx", "winfo_rooty", "winfo_height")):
            return (
                int(anchor.winfo_rootx()),
                int(anchor.winfo_rooty()) + int(anchor.winfo_height()),
            )
        raise ValueError("无法确定 QC 菜单的显示位置")

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
        打开与表格浏览相同的只读筛选/排序工作区。
        """
        try:
            df, select_filter = self.resolve_filter_source(type, df)
        except ValueError:
            messagebox.showinfo(_tr(_T, "信息"), _tr(_T, "缺乏必要参数"))
            return
                
        if df is None or df.empty:
            messagebox.showinfo(_tr(_T, "信息"), _tr(_T, "输入数据为空"))
            return

        return self.open_table_workspace(df, legacy_filter=select_filter)


        
