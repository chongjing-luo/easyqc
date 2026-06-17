
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC 对话框主要功能模块
包含文件浏览、路径选择、变量设置等对话框功能
"""

import tkinter as tk
# === GUI i18n: 本文件用户可见文字中英文对照 ===
# 切换语言: 改 gui/i18n.py 的 LANG = "zh" / "en"
from gui.i18n import tr as _tr

_T = {
    "选择文件夹":             {"zh": "选择文件夹",           "en": "Select Folder"},
    "选择文件":               {"zh": "选择文件",             "en": "Select File"},
    "错误":                   {"zh": "错误",                 "en": "Error"},
    "选择文件夹时发生错误":   {"zh": "选择文件夹时发生错误", "en": "Error selecting folder"},
    "选择文件时发生错误":     {"zh": "选择文件时发生错误",   "en": "Error selecting file"},
}

from tkinter import filedialog, messagebox
import os
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_error
from utils.data_manager import DataManager
from utils.validators import validate_score as validate_score_value
from gui.dialogs import ConstantDialog, ModuleDialog, ProjectDialog, ScoreTagEditor, VariableDialog
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
        self.DataM = DataManager()
        self.TablD = TableDisplay(app)
        self.qcpage = gui_qcpage()
        self.ProjectD = ProjectDialog(app)
        self.ConstantD = ConstantDialog(app)
        self.VariableD = VariableDialog(app, self.DataM, self.TablD)
        self.ModuleD = ModuleDialog(app, self.qcpage)
        self.ScoreTagE = ScoreTagEditor(app)
    
    def browse_path(self):
        """通过图形化界面获取文件夹路径"""
        try:
            # 打开文件夹选择对话框
            directory = filedialog.askdirectory(
                parent=self.app.root,
                title=_tr(_T, '选择文件夹'),
                initialdir=os.path.expanduser("~")  # 从用户主目录开始
            )
            
            # 只有当用户选择了文件夹时才更新输入框
            if directory and isinstance(directory, str):
                self.app.path_entry.delete(0, tk.END)
                self.app.path_entry.insert(0, directory)
                
        except Exception as e:
            messagebox.showerror(_tr(_T, "错误"), _tr(_T, "选择文件夹时发生错误") + f": {str(e)}")
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
                title=_tr(_T, '选择文件'),
                filetypes=filetypes,
                initialdir=os.path.expanduser("~")  # 从用户主目录开始
            )
            
            # 只有当用户选择了文件时才更新输入框
            if filename and isinstance(filename, str):
                self.app.path_entry2.delete(0, tk.END)
                self.app.path_entry2.insert(0, filename)
                
        except Exception as e:
            messagebox.showerror(_tr(_T, "错误"), _tr(_T, "选择文件时发生错误") + f": {str(e)}")
            log_error(f"文件选择对话框错误: {str(e)}", "EasyQCApp")

    def create_project(self):
        """创建新项目对话框"""
        return self.ProjectD.create_project()

    def import_project(self):
        """导入项目对话框"""
        return self.ProjectD.import_project()

    def remove_project(self):
        """移除项目"""
        return self.ProjectD.remove_project()

    # #############################   variable   #################################
    def add_constant(self):
        """添加常量
        获取常量名和值，添加到self.settings['constants']
        并更新表格
        """
        return self.ConstantD.add_constant()


    def refresh_constant_table(self):
        """刷新常量表格数据"""
        return self.ConstantD.refresh_constant_table()


    def edit_constant(self, event):
        """编辑常量"""
        return self.ConstantD.edit_constant(event)


    # ############################################################

    def extract_path(self):
        """
        提取路径，使用DataManager的get_list获取，生成self.tmp_list_df,
        """
        return self.VariableD.extract_path()
    
    def set_varname(self):
        """
        设置变量名
        """
        return self.VariableD.set_varname()


    def extract_file(self):
        """
        提取文件，使用DataManager的get_list获取，生成self.tmp_list_df,
        """
        return self.VariableD.extract_file()

    def extract_words(self):
        """
        从文本中提取所有单词
        """
        return self.VariableD.extract_words()
        
        

    def merge_newdata(self):
        return self.VariableD.merge_newdata()


    ##########################      ####################################


    def new_merge(self):
        return self.VariableD.new_merge()


        

    def show_all_variable(self):
        return self.VariableD.show_all_variable()


    # #####################################################################################
    def add_module(self, name=None, label=None, index=None):
        """添加新模块对话框"""
        return self.ModuleD.add_module(name=name, label=label, index=index)

    def change_module_index(self, index, module):
        return self.ModuleD.change_module_index(index, module)

    def import_module(self):
        return self.ModuleD.import_module()



    def export_module(self, module_name):
        return self.ModuleD.export_module(module_name)




    def manage_module(self):
        return self.ModuleD.manage_module()



    def start_qc(self, module):
        """开始质控，打开质控页面"""
        return self.ModuleD.start_qc(module)

    def add_score(self, qcindex, idx):
        return self.ScoreTagE.add_score(qcindex, idx)

    def del_score(self, qcindex, idx):
        return self.ScoreTagE.del_score(qcindex, idx)


    def validate_score(self, value, show_error=True):
        if self is None:
            return validate_score_value(value)
        return self.ScoreTagE.validate_score(value, show_error=show_error)

    def add_tag(self, qcindex, idx):
        return self.ScoreTagE.add_tag(qcindex, idx)

    def del_tag(self, qcindex, idx):
        return self.ScoreTagE.del_tag(qcindex, idx)
