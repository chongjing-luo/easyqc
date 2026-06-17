#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC 主窗口模块
包含主要的GUI界面组件
"""

import tkinter as tk
# === GUI i18n: 本文件用户可见文字中英文对照 ===
from gui.i18n import tr as _tr

_T = {
    "EasyQC - 质量控制分析工具": {"zh": "EasyQC - 质量控制分析工具", "en": "EasyQC - Quality Control Analysis Tool"},
    "项目管理":         {"zh": "项目管理",         "en": "Project Management"},
    "新建项目":         {"zh": "新建项目",         "en": "New Project"},
    "导入项目":         {"zh": "导入项目",         "en": "Import Project"},
    "移除项目":         {"zh": "移除项目",         "en": "Remove Project"},
    "常量设置":         {"zh": "常量设置",         "en": "Constants"},
    "常量名:":          {"zh": "常量名: ",         "en": "Name: "},
    "值:":              {"zh": "值: ",             "en": "Value: "},
    "常量名":           {"zh": "常量名",           "en": "Name"},
    "值":               {"zh": "值",               "en": "Value"},
    "添加":             {"zh": "添加",             "en": "Add"},
    "变量设置":         {"zh": "变量设置",         "en": "Variables"},
    "导入受试者变量":   {"zh": "导入受试者变量",   "en": "Import Subject Variables"},
    "查看受试者表":     {"zh": "查看受试者表",     "en": "View Subject Table"},
    "聚合质控评分":     {"zh": "聚合质控评分",     "en": "Aggregate QC Ratings"},
    "数据筛选":         {"zh": "数据筛选",         "en": "Filter & Sort"},
    "查看质控结果表":   {"zh": "查看质控结果表",   "en": "View QC Results Table"},
    "设置变量":         {"zh": "设置变量",         "en": "Set Variables"},
    "方式1: 从文件夹导入": {"zh": "方式1: 从文件夹导入", "en": "Method 1: Import from Folder"},
    "浏览":             {"zh": "浏览",             "en": "Browse"},
    "读取文件夹":       {"zh": "读取文件夹",       "en": "Read Folder"},
    "方式2: 从文件导入(CSV/Excel/TXT)": {"zh": "方式2: 从文件导入(CSV/Excel/TXT)", "en": "Method 2: Import from File (CSV/Excel/TXT)"},
    "读取文件":         {"zh": "读取文件",         "en": "Read File"},
    "方式3: 直接输入(空格/换行分隔)": {"zh": "方式3: 直接输入(空格/换行分隔)", "en": "Method 3: Direct Input (space/newline)"},
    "确定":             {"zh": "确定",             "en": "OK"},
    "查看待合并变量":   {"zh": "查看待合并变量",   "en": "View Pending Variables"},
    "筛选待合并变量":   {"zh": "筛选待合并变量",   "en": "Filter Pending Variables"},
    "合并":             {"zh": "新建/合并",        "en": "Merge"},
    "筛选主变量表":     {"zh": "筛选主变量表",     "en": "Filter Master Variables"},
    "查看主变量表":     {"zh": "查看主变量表",     "en": "View Master Variables"},
    "展开▼":            {"zh": "展开▼",            "en": "Expand ▼"},
    "收起▲":            {"zh": "收起▲",            "en": "Collapse ▲"},
    "开始质控":         {"zh": "开始质控",         "en": "Start QC"},
    "查看模块结果":     {"zh": "查看模块结果",     "en": "View Module Results"},
    "评分者:":          {"zh": "评分者: ",         "en": "Rater: "},
    "单实例模式":       {"zh": "单实例模式",       "en": "Single Instance"},
    "评分尺度:":        {"zh": "评分尺度: ",       "en": "Score Scale: "},
    "查看器命令":       {"zh": "查看器命令",       "en": "Viewer Command"},
    "导出模块配置":     {"zh": "导出模块配置",     "en": "Export Module Config"},
    "质控模块设置":     {"zh": "质控模块设置",     "en": "QC Modules"},
    "添加模块":         {"zh": "添加模块",         "en": "Add Module"},
    "导入模块":         {"zh": "导入模块",         "en": "Import Module"},
    "模块管理":         {"zh": "模块管理",         "en": "Manage Modules"},
    "取消":             {"zh": "取消",             "en": "Cancel"},
    "代码设置":         {"zh": "代码设置",         "en": "Viewer Command"},
}

from tkinter import ttk, scrolledtext
import sys
from pathlib import Path
import platform

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_info, log_error, log_warning, log_exception, log_debug, LogContext, log_function

from core.table_service import TABLE_QCTABLE
from core.event_bus import EventBus, EventType
from utils.file_utils import FileUtils
from utils.data_manager import DataManager

# 导入对话框和表格显示功能类
from gui.dialog_main import DialogMain
from gui.gui_table import TableDisplay
from gui.state_bridge import GUIStateBridge


class EasyQCApp:
    @log_function("EasyQCApp")
    def __init__(self, root, services=None):

        
        try:
            with LogContext("初始化EasyQC应用", "EasyQCApp"):
                log_info("开始初始化EasyQC应用程序", "EasyQCApp")
                
                try:

                    self.root = root
                    self.services = services
                    self.project_service = getattr(services, "project_service", None)
                    self.rating_service = getattr(services, "rating_service", None)
                    self.table_service = getattr(services, "table_service", None)
                    self.code_executor = getattr(services, "code_executor", None)
                    self.table_transform = getattr(services, "table_transform", None)
                    # 先创建基础组件
                    self.DataM = DataManager()
                    if self.table_transform is not None:
                        self.DataM.table_transform = self.table_transform
                    self.FileU = FileUtils()
                    
                    # P2 step 3: use GUIStateBridge (service-backed, no ProjectManager).
                    # The bridge delegates to project_service + session_state +
                    # table_service. Project refresh on change is driven by the
                    # PROJECT_CHANGED event (see _on_project_changed).
                    self.session_state = getattr(services, "session_state", None) or __import__(
                        "core.session_state", fromlist=["SessionState"]
                    ).SessionState()
                    self.gui_state = GUIStateBridge(
                        project_service=self.project_service,
                        session_state=self.session_state,
                        table_service=self.table_service,
                    )
                    # load the last project into the service layer
                    if self.project_service is not None:
                        last = self.project_service.registry.last_project
                        if last and last in self.project_service.registry.projects:
                            self.project_service.load(last)
                            # sync tables from service into session state
                            if self.table_service is not None:
                                loaded = self.table_service.load_legacy_state_tables(
                                    self.project_service.current_project
                                )
                                if loaded is not None:
                                    self.gui_state.apply_loaded_tables(loaded)
                    self.ProjM = None  # legacy marker; no longer instantiated
                    
                    # 创建DialogMain（需要ProjM已存在）
                    self.DialM = DialogMain(self)
                    self.TablD = TableDisplay(self)

                except Exception as e:
                    log_error(f"项目管理器初始化失败: {e}", "EasyQCApp")
                    raise
                
                # 设置用户界面
                self.setup_global_styles()
                self.setup_window()
                self.load_project_to_gui()
                log_info("用户界面设置完成", "EasyQCApp")

                # P1-D (AC-10, ADR-002): subscribe to typed service events on the
                # shared EventBus. Handlers are kept lightweight — the GUI today
                # is pull-based (dialogs re-call load_*_to_gui after each op),
                # so these handlers only log + flag staleness; they do NOT force
                # a second refresh (that would double-render). P2 will migrate
                # the pull-based refresh to be event-driven.
                self._subscribe_event_bus()
                
        except Exception as e:
            log_exception("EasyQC应用程序初始化失败", "EasyQCApp")
            raise
    
    
    def setup_global_styles(self):
        """设置全局样式配置"""

        # 获取电脑的操作系统
        self.platform = platform.system()
        if self.platform == 'Windows':
            self.ft_family = '微软雅黑'
        elif self.platform == 'Darwin':
            self.ft_family = 'AppleGothic'
        else:
            self.ft_family = 'Arial'

        self.font_14 = (self.ft_family, 14)
        self.font_13 = (self.ft_family, 13)
        self.font_12 = (self.ft_family, 12)
        self.font_10 = (self.ft_family, 10)

        self.style = ttk.Style()
        
        # 按钮样式
        self.style.configure('Project.TButton', font=self.font_13,padding=(-10, -10, -10, -10))
        # 标签样式
        self.style.configure('Title.TLabel', font=self.font_14, foreground='#333333') 
        # 标签样式
        self.style.configure('TLabel', font=self.font_13, foreground='#333333', anchor='center', justify='center')

        self.style.configure('Bold.TLabel', font=(self.ft_family, 13, 'bold'), foreground='#333333', anchor='center', justify='center')
        # 下拉框样式
        self.style.configure('Project.TCombobox',  font=self.font_12)  
        # 框架样式
        self.style.configure('Project.TLabelframe',  borderwidth=2, relief='groove')
        # 框架标签样式
        self.style.configure('Project.TLabelframe.Label',   font=self.font_14, foreground='#555555')
        
    def setup_window(self):
        """设置主窗口"""
        self.root.title("EasyQC - 质量控制分析工具")
        self.root.geometry("600x940")
        self.root.resizable(True, True)
        
        self.project_manager_widget()
        self.constant_widget()
        self.variable_widget()
        self.module_widget()
        
            

    def project_manager_widget(self):
        """项目管理组件:
        左侧是下拉条，显示了当前的所有项目，用户可以从下拉条中选择项目；
        右侧有两个按钮，一个是新建项目，一个是导入项目；
        新建项目时，弹出一个对话框，让用户输入项目名称和结果路径；
        下拉拉条，单击项目名称，会加载这个项目；右键单击，会弹出选项，"移除该项目"，单击会再次提示确认
        可以设置下拉条的长度、高度，按钮的长度、高度；文本的字体、大小、颜色
        上面的整个部分有有个四角圆弧的曲线框起来，框的上边缘是一个标签，标签的文字是"项目管理"
        """
        # 创建一个Frame作为容器，使用绝对定位和全局样式
        self.project_frame = ttk.LabelFrame(self.root, text="", style='Project.TLabelframe')
        self.project_frame.place(x=10, y=-15, width=580, height=100)

        # 创建内部Frame用于放置控件，使用绝对定位
        self.inner_frame = ttk.Frame(self.project_frame)
        self.inner_frame.place(x=10, y=0, width=580, height=100)
        
        # 在框架内部添加"项目管理"标签，使用绝对定位和全局样式
        self.project_label = ttk.Label(self.inner_frame, text=_tr(_T, "项目管理"), style='Title.TLabel')
        self.project_label.place(x=240, y=0, width=100, height=30)
        
        # 项目下拉列表，使用绝对定位和全局样式
        self.projects_list = tk.StringVar()
        self.project_combo = ttk.Combobox(self.inner_frame, textvariable=self.projects_list,state="readonly", style='Project.TCombobox')
        self.project_combo.place(x=5, y=30, width=240, height=35)
        

        # ---------------------------   绑定选择事件（下拉框选择时触发）   ---------------------------
        self.project_combo.bind("<<ComboboxSelected>>", lambda e: self.gui_state.change_project(self.project_combo.get()))
        
        # ---------------------------------   新建项目按钮   ---------------------------------
        self.new_project_btn = ttk.Button(self.inner_frame, text=_tr(_T, "新建项目"), command=self.DialM.create_project, style='Project.TButton')
        self.new_project_btn.place(x=250, y=30, width=100, height=35)

        # ---------------------------------   导入项目按钮   ---------------------------------
        self.import_project_btn = ttk.Button(self.inner_frame, text=_tr(_T, "导入项目"), command=self.DialM.import_project, style='Project.TButton')
        self.import_project_btn.place(x=355, y=30, width=100, height=35)

        # ---------------------------------   移除项目按钮   ---------------------------------
        self.remove_project_btn = ttk.Button(self.inner_frame, text=_tr(_T, "移除项目"), command=self.DialM.remove_project, style='Project.TButton')
        self.remove_project_btn.place(x=460, y=30, width=100, height=35)
    # ===================================================================================


    def constant_widget(self):
        """
        固定的组件，比如项目管理、分析管理、结果管理
        """

        # 创建一个Frame作为容器，使用绝对定位
        self.constant_frame = ttk.LabelFrame(self.root, text="")
        self.constant_frame.place(x=10, y=80, width=580, height=200)

        # 创建内部Frame用于放置控件，使用绝对定位
        self.inner_frame = ttk.Frame(self.constant_frame)
        self.inner_frame.place(x=10, y=0, width=580, height=200)
        
        # 在框架内部添加"项目管理"标签，使用绝对定位
        self.constant_label = ttk.Label(self.inner_frame, text=_tr(_T, "常量设置"), font=self.font_14)
        self.constant_label.place(x=240, y=0, width=100, height=30)

        # 常量名字文本，文本前有常量名"常量名："
        self.constant_name_label = ttk.Label(self.inner_frame, text=_tr(_T, "常量名:"), font=self.font_13)
        self.constant_name_label.place(x=0, y=30, width=50, height=32)
        self.constant_name = ttk.Entry(self.inner_frame, font=self.font_12)
        self.constant_name.place(x=60, y=30, width=120, height=28)

        # 常量值文本，文本前有常量值"常量值："
        self.constant_value_label = ttk.Label(self.inner_frame, text=_tr(_T, "值:"), font=self.font_13)
        self.constant_value_label.place(x=195, y=30, width=25, height=32)
        self.constant_value = ttk.Entry(self.inner_frame, font=self.font_12)
        self.constant_value.place(x=225, y=30, width=230, height=28)

        # 添加按钮
        self.constant_btn = ttk.Button(self.inner_frame, text=_tr(_T, "添加"), command=self.DialM.add_constant, style='Project.TButton')
        self.constant_btn.place(x=475, y=27, width=80, height=30)


        # 创建表格和滚动条
        self.constant_table = ttk.Treeview(self.inner_frame, columns=("const_name", "const_value"), show="headings", height=4)
        
        # 设置列宽比例为1:3
        self.constant_table.column("const_name", width=120)  # 540/4 = 135
        self.constant_table.column("const_value", width=390)      # 540*3/4 = 405
        
        # 设置表头标题
        self.constant_table.heading("const_name", text="常量名")
        self.constant_table.heading("const_value", text="值")

        # 创建垂直滚动条
        self.vsb = ttk.Scrollbar(self.inner_frame, orient="vertical", command=self.constant_table.yview)
        self.constant_table.configure(yscrollcommand=self.vsb.set)

        # 创建水平滚动条
        self.hsb = ttk.Scrollbar(self.inner_frame, orient="horizontal", command=self.constant_table.xview)
        self.constant_table.configure(xscrollcommand=self.hsb.set)



        # 放置表格和滚动条
        self.constant_table.place(x=20, y=65, width=510, height=100)
        self.vsb.place(x=530, y=65, height=100)

        # 绑定双击表格中行的按钮
        self.constant_table.bind("<Double-1>", self.DialM.edit_constant)

    def variable_widget(self):
        """
        变量的组件，比如变量管理、变量设置
        """
        # 创建一个Frame作为容器，使用绝对定位
        self.variable_frame = ttk.LabelFrame(self.root, text="")
        self.variable_frame.place(x=10, y=280, width=580, height=90)

        # 创建内部Frame用于放置控件，使用绝对定位
        self.inner_frame_v = ttk.Frame(self.variable_frame)
        self.inner_frame_v.place(x=10, y=0, width=580, height=90)

        # 设置标题 变量设置，居中放置
        self.variable_title = ttk.Label(self.inner_frame_v, text=_tr(_T, "变量设置"), font=self.font_14)
        self.variable_title.place(x=240, y=0, width=100, height=30)


        # 新建/增加 按钮
        self.new_variable_btn = ttk.Button(self.inner_frame_v, text=_tr(_T, "导入受试者变量"), command=self.set_variable, style='Project.TButton')
        self.new_variable_btn.place(x=5, y=25, width=100, height=35)

        # 显示初始变量 按钮
        self.show_initial_table_btn = ttk.Button(self.inner_frame_v, text=_tr(_T, "查看受试者表"), command=lambda:self.TablD.show_df(self.gui_state.all_variable_table()), style='Project.TButton')
        self.show_initial_table_btn.place(x=115, y=25, width=100, height=35)

        # 提取质控结果 按钮
        self.extract_qc_btn = ttk.Button(self.inner_frame_v, text=_tr(_T, "聚合质控评分"), command=self.extract_qc_results, style='Project.TButton')
        self.extract_qc_btn.place(x=225, y=25, width=100, height=35)

        # 过滤和筛选 按钮
        self.filter_btn = ttk.Button(self.inner_frame_v, text=_tr(_T, "数据筛选"), command=lambda: self.TablD.filter_sorter("qctable"), style='Project.TButton')
        self.filter_btn.place(x=335, y=25, width=100, height=35)

        # 显示当前变量 按钮
        def show_qctable():
            self.TablD.show_df(self.gui_state.qctable_for_display())
        self.show_current_variable_btn = ttk.Button(self.inner_frame_v, text=_tr(_T, "查看质控结果表"), command=show_qctable, style='Project.TButton')
        self.show_current_variable_btn.place(x=445, y=25, width=100, height=35)

    def set_variable(self):
        """
        设置变量：弹出一个对话框，500*400
        """
        # 创建设置变量对话框
        self.dialog_setvar = tk.Toplevel(self.root)
        self.dialog_setvar.title("设置变量")
        self.dialog_setvar.geometry("500x450")
        
        # 将对话框居中显示
        self.dialog_setvar.update_idletasks()
        x = (self.dialog_setvar.winfo_screenwidth() - self.dialog_setvar.winfo_width()) // 2
        y = (self.dialog_setvar.winfo_screenheight() - self.dialog_setvar.winfo_height()) // 2
        self.dialog_setvar.geometry(f"+{x}+{y}")

        # =========================    路径导入   =============================
        # 创建主Frame作为容器
        main_frame = ttk.LabelFrame(self.dialog_setvar, text=_tr(_T, "方式1: 从文件夹导入"))
        main_frame.place(x=10, y=15, width=480, height=70)

        # 创建内部Frame用于放置控件
        inner_frame = ttk.Frame(main_frame)
        inner_frame.place(x=10, y=0, width=460, height=70)

        self.browse_btn1 = ttk.Button(inner_frame, text=_tr(_T, "浏览"), command=self.DialM.browse_path)
        self.browse_btn1.place(x=5, y=5, width=80, height=30)

        self.path_entry = ttk.Entry(inner_frame, font=self.font_12)
        self.path_entry.place(x=90, y=10, width=250, height=25)

        # 提取路径按钮
        self.extract_btn = ttk.Button(inner_frame, text=_tr(_T, "读取文件夹"), command=self.DialM.extract_path)
        self.extract_btn.place(x=360, y=5, width=100, height=30)

        # ================================================================
        # 创建主Frame作为容器
        main_frame2 = ttk.LabelFrame(self.dialog_setvar, text=_tr(_T, "方式2: 从文件导入(CSV/Excel/TXT)"))
        main_frame2.place(x=10, y=110, width=480, height=70)

        # 创建内部Frame用于放置控件
        inner_frame2 = ttk.Frame(main_frame2)
        inner_frame2.place(x=10, y=0, width=460, height=70)

        self.browse_btn2 = ttk.Button(inner_frame2, text=_tr(_T, "浏览"), command=self.DialM.browse_file)
        self.browse_btn2.place(x=5, y=5, width=80, height=30)
        
        # 文件路径输入框
        self.path_entry2 = ttk.Entry(inner_frame2, font=self.font_12)
        self.path_entry2.place(x=90, y=10, width=250, height=25)

        self.extract_btn = ttk.Button(inner_frame2, text=_tr(_T, "读取文件"), command=self.DialM.extract_file)
        self.extract_btn.place(x=360, y=5, width=100, height=30)

        # ================================================================
        # 创建主Frame作为容器
        main_frame3 = ttk.LabelFrame(self.dialog_setvar, text=_tr(_T, "方式3: 直接输入(空格/换行分隔)"))
        main_frame3.place(x=10, y=210, width=480, height=140)

        # 创建内部Frame用于放置控件
        inner_frame3 = ttk.Frame(main_frame3)
        inner_frame3.place(x=10, y=0, width=460, height=140)

        # 创建带滚动条的文本框
        self.text_entry = scrolledtext.ScrolledText(inner_frame3, font=self.font_12, wrap=tk.WORD, padx=5, pady=5)
        self.text_entry.place(x=10, y=5, width=440, height=80)
        self.text_entry.tag_configure('left', justify='left')

        # 确认按钮
        self.confirm_btn = ttk.Button(inner_frame3, text=_tr(_T, "确定"), command=self.DialM.extract_words)
        self.confirm_btn.place(x=185, y=85, width=100, height=30)

        # ================================================================
        # 创建按钮框架
        button_frame = ttk.Frame(self.dialog_setvar)
        button_frame.place(x=10, y=360, width=480, height=80)

        # 创建按钮并水平排列
        self.show_variable_btn = ttk.Button(button_frame, text=_tr(_T, "查看待合并变量"), command=lambda: self.TablD.show_df(self.gui_state.new_variable_table()))
        self.show_variable_btn.place(x=10, y=10, width=225, height=30)

        # 筛选按钮
        self.filter_btn = ttk.Button(button_frame, text=_tr(_T, "筛选待合并变量"), command=lambda: self.TablD.filter_sorter("new"))
        self.filter_btn.place(x=250, y=10, width=225, height=30)

        # 新建/合并 按钮
        self.new_merge_btn = ttk.Button(button_frame, text=_tr(_T, "合并"), command=self.DialM.merge_newdata)
        self.new_merge_btn.place(x=10, y=40, width=140, height=30)

        # 新建/合并 按钮
        self.filter_btn_all = ttk.Button(button_frame, text=_tr(_T, "筛选主变量表"), command=lambda: self.TablD.filter_sorter("all"))
        self.filter_btn_all.place(x=170, y=40, width=140, height=30)

        # 查看总变量表格
        self.show_all_variable_btn = ttk.Button(button_frame, text=_tr(_T, "查看主变量表"), command=lambda: self.TablD.show_df(self.gui_state.all_variable_table()))
        self.show_all_variable_btn.place(x=330, y=40, width=140, height=30)


    # ----------- 卡片函数（替代 CollapsibleCard）-----------
    def create_collapsible_card(self, qcidx):
        module = self.gui_state.module_by_key(qcidx)
        frame = ttk.Frame(self.scroll_area.scrollable_frame)
        frame.showing = module['showing']

        # 顶部栏
        header = ttk.Frame(frame, height=30, width=520)
        header.pack(fill="x", pady=2)

        # 左侧按钮
        toggle_btn = ttk.Button(header, text=_tr(_T, "展开▼"), width=6)
        toggle_btn.place(x=0, y=2, width=80, height=26)

        # 标题（居中）
        title = f"{qcidx} - {module['name']} - {module['label']}"
        title_lbl = ttk.Label(header, text=title, font=("Arial", 12, "bold"))
        title_lbl.place(x=85, y=2, width=340, height=26)

        # 右侧按钮
        startqc_btn = ttk.Button(header, text=_tr(_T, "开始质控"), width=8, command=lambda: self.DialM.start_qc(module))
        startqc_btn.place(x=430, y=2, width=100, height=26)

        # 创建外层容器frame,设置固定高度
        # 获取tag的数量
        tag_num = len(module['tags'])
        score_num = len(module['scores'])
        height = 30 + 30 * tag_num + 30 * score_num + 65
        content_ = ttk.Frame(frame, relief="ridge", padding=10, height=height)
        content_.pack(fill="x", pady=1)
        content_.pack_propagate(False)  # 防止frame被子组件撑开
        
        # 创建内层content frame
        content = ttk.Frame(content_)
        content.place(x=0, y=0, width=520, height=height-15)

        # 过滤和筛选按钮
        filter_btn = ttk.Button(content, text=_tr(_T, "数据筛选"), style='Project.TButton', command=lambda: self.TablD.filter_sorter(module['name']))
        filter_btn.place(x=5, y=0, width=100, height=30)

        filter_btn = ttk.Button(content, text=_tr(_T, "查看模块结果"), style='Project.TButton', command=lambda: self.TablD.show_df(self.gui_state.result_table(module['name'])))
        filter_btn.place(x=115, y=0, width=110, height=30)

        # 创建评分人标签和输入框
        label = ttk.Label(content, text=_tr(_T, "评分者:"), style='TLabel')
        label.place(x=235, y=0, width=50, height=30)
        # 创建StringVar来跟踪Entry的值
        rater_var = tk.StringVar(value=module['rater'])
        entry_rater = ttk.Entry(content, textvariable=rater_var)
        entry_rater.place(x=285, y=0, height=30, width=120)
        rater_var.trace_add('write', lambda *args: self.gui_state.update_module_field(qcidx, 'rater', rater_var.get()))
        
        # 创建子进程控制复选框
        # subprocess_var = tk.BooleanVar()
        subprocess_var = tk.BooleanVar(value=module['control'])
        subprocess_check = ttk.Checkbutton(content, text=_tr(_T, "单实例模式"), variable=subprocess_var,
                                        command=lambda: self.gui_state.update_module_field(qcidx, 'control', subprocess_var.get()))
        subprocess_check.place(x=415, y=5, width=100)
        

        # =============================       创建分数设置区域     =============================
        score_frame = ttk.Frame(content)
        score_frame.place(x=5, y=40, width=440)

        # 创建分数设置区域
        tmp_score = module['scores']
        score_y = 35
        for idx, score_key in enumerate(tmp_score.keys(), start=1):
            # 每一行的纵向位置
            row_y = score_y + (idx-1) * 30
            ttk.Label(content, text=f"score {score_key}:", style='Bold.TLabel').place(x=5, y=row_y)
            ttk.Label(content, text="输入标签:", style='TLabel').place(x=80, y=row_y)
            # 创建StringVar来追踪label的值
            score_entry_name = f"{module['name']}_{score_key}_label"
            score_label_var = tk.StringVar(value=tmp_score[score_key]['label'])
            setattr(self, f"{score_entry_name}_var", score_label_var)
            setattr(self, score_entry_name, ttk.Entry(content, width=8, textvariable=score_label_var))
            getattr(self, score_entry_name).place(x=150, y=row_y, width=150, height=25)
            
            def update_label(*args, key=score_key):
                self.gui_state.update_score_fields(qcidx, key, label=getattr(self, f"{module['name']}_{key}_label_var").get())
            score_label_var.trace_add("write", update_label)

            # 分值
            ttk.Label(content, text=_tr(_T, "评分尺度:"), style='TLabel').place(x=310, y=row_y)
            score_entry_name = f"{module['name']}_{score_key}_num"
            # 创建Entry和StringVar来跟踪分数值
            score_var = tk.StringVar(value=tmp_score[score_key]['num'])
            setattr(self, f"{score_entry_name}_var", score_var)
            
            # 创建Entry组件
            score_entry = ttk.Entry(content, width=8, textvariable=score_var)
            score_entry.place(x=350, y=row_y, width=90, height=25)
            setattr(self, score_entry_name, score_entry)
            
            
            # 添加验证跟踪 - 仅在失去焦点时验证
            # G4 fix: capture score_entry_name via default arg (closure late-binding
            # bug) and use StringVar.set("") to actually clear the Entry widget
            # (the old setattr(...,"") replaced the StringVar attribute with a
            # plain string, so the Entry never cleared).
            def validate_score_on_focus_out(event, key=score_key, name=score_entry_name):
                num = event.widget.get().strip()
                result = self.DialM.validate_score(num)
                if result is None:
                    getattr(self, f"{name}_var").set("")
                    event.widget.delete(0, tk.END)
                    self.gui_state.update_score_fields(qcidx, key, num=None, num_=None)
                else:
                    self.gui_state.update_score_fields(qcidx, key, num=num, num_=result)

            # 绑定失去焦点事件
            score_entry.bind("<FocusOut>", lambda event, key=score_key: validate_score_on_focus_out(event, key))
            
            # 放置Entry组件
            getattr(self, score_entry_name).place(x=350, y=row_y)

            # 在分数设置区域的每一行后面添加增加和删除按钮
            pad = (-28,-10,-15,-10)
            add_btn = ttk.Button(content, text="+", padding=pad, command=lambda i=idx: self.DialM.add_score(qcidx, i+1))
            add_btn.place(x=450, y=row_y+3, width=25, height=25)
            if idx > 1:
                del_btn = ttk.Button(content, text="-", padding=pad, command=lambda i=idx: self.DialM.del_score(qcidx, i))
                del_btn.place(x=480, y=row_y+3, width=25, height=25)

        # 创建标签设置区域
        tmp_tag = module['tags']
        tag_y = score_y + len(tmp_score) * 30 + 3
        for irow2, tag_key in enumerate(tmp_tag.keys(), start=1):
            row_y = tag_y + (irow2-1) * 30
            ttk.Label(content, text=f"tag {tag_key}:", style='Bold.TLabel').place(x=5, y=row_y)
            ttk.Label(content, text="输入标签:").place(x=80, y=row_y)
            tag_entry_name = f"{module['name']}_{tag_key}"
            tag_var_name = f"{tag_entry_name}_var"
            setattr(self, tag_var_name, tk.StringVar(value=module['tags'][tag_key]['label']))
            setattr(self, tag_entry_name, ttk.Entry(content, textvariable=getattr(self, tag_var_name)))
            getattr(self, tag_entry_name).place(x=150, y=row_y, width=130, height=25)
            getattr(self, tag_var_name).trace_add(
                "write",
                lambda *args, k=tag_key, var_name=tag_var_name: self.gui_state.update_tag_fields(
                    qcidx,
                    k,
                    label=getattr(self, var_name).get(),
                ),
            )
            
            # 在分数设置区域的每一行后面添加增加和删除按钮
            add_btn = ttk.Button(content, text="+", padding=pad, command=lambda idx=irow2: self.DialM.add_tag(qcidx, idx+1))
            add_btn.place(x=300, y=row_y+3, width=25, height=25)
            
            if irow2 > 1:
                del_btn = ttk.Button(content, text="-", padding=pad, command=lambda idx=irow2: self.DialM.del_tag(qcidx, idx))
                del_btn.place(x=330, y=row_y+3, width=25, height=25)

        # =========================   代码设置   =========================
        def code_setting():
            code_frame = tk.Toplevel(self.root)
            code_frame.title("代码设置")
            code_frame.geometry("800x600")
            code_frame.transient(self.root)

            # 创建底部Frame用于放置按钮
            btn_frame = ttk.Frame(code_frame)
            btn_frame.pack(side="bottom", fill="x", pady=10)

            # 代码文本框
            code_text = tk.Text(code_frame, wrap=tk.CHAR, font=("Courier", 11), undo=True)
            code_text.pack(side="top", fill="both", expand=True)

            if self.gui_state.module_by_key(qcidx).get('code'):
                code_text.insert('1.0', self.gui_state.module_by_key(qcidx)['code'])

            # 确认按钮
            def confirm():
                self.gui_state.update_module_field(qcidx, 'code', code_text.get('1.0', tk.END).strip())
                code_frame.destroy()

            # 直接用pack布局让按钮可见且居中
            confirm_btn = ttk.Button(btn_frame, text=_tr(_T, "确定"), command=confirm)
            cancel_btn = ttk.Button(btn_frame, text=_tr(_T, "取消"), command=code_frame.destroy)
            confirm_btn.pack(side="left", padx=(0, 2), ipadx=20, ipady=5, expand=True)
            cancel_btn.pack(side="left", padx=(2, 0), ipadx=20, ipady=5, expand=True)


        ystart = tag_y + len(tmp_tag) * 30 + 3
        code_btn = ttk.Button(content, text=_tr(_T, "查看器命令"), command=code_setting, style='Project.TButton')
        code_btn.place(x=5, y=ystart, width=105, height=30)

        export_btn = ttk.Button(content, text=_tr(_T, "导出模块配置"), command=lambda: self.DialM.export_module(module['name']), style='Project.TButton')
        export_btn.place(x=120, y=ystart, width=150, height=30)
        
        content_.pack(fill="x", pady=5) if module['showing'] else content_.pack_forget()
        
        # 切换展开/收起
        def toggle():
            if frame.showing:
                content_.pack_forget()   # 隐藏整个内容区
                toggle_btn.config(text=_tr(_T, "展开▼"))
                module['showing'] = False
            else:
                content_.pack(fill="x", pady=5)  # 显示内容区
                toggle_btn.config(text=_tr(_T, "收起▲"))
                module['showing'] = True
            frame.showing = not frame.showing

        toggle_btn.config(command=toggle)

        return frame    


    def module_widget(self):
        """
        模块的组件，比如模块管理、模块设置
        """
        # ----------- 滚动容器函数（替代 ScrollableFrame）-----------
        def create_scrollable_frame(parent, max_height=300):
            container = ttk.Frame(parent)
            canvas = tk.Canvas(container, height=max_height)
            scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
            scrollable_frame = ttk.Frame(canvas)

            def _on_frame_configure(event):
                # 获取内容区域的包围盒
                bbox = canvas.bbox("all")
                if bbox:
                    x0, y0, x1, y1 = bbox
                    canvas.configure(scrollregion=(x0, y0, x1, y1 + 20))

            scrollable_frame.bind("<Configure>", _on_frame_configure)

            window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw", width=545)
            canvas.configure(yscrollcommand=scrollbar.set)

            canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            container.scrollable_frame = scrollable_frame
            return container


        # ----------- 主 UI 构建 -----------
        self.module_frame = ttk.LabelFrame(self.root, text="")
        self.module_frame.place(x=10, y=370, width=580, height=555)

        self.inner_frame_mo = ttk.Frame(self.module_frame)
        self.inner_frame_mo.place(x=10, y=0, width=580, height=555)

        variable_title = ttk.Label(self.inner_frame_mo, text=_tr(_T, "质控模块设置"), font=self.font_14)
        variable_title.place(x=220, y=-3, width=120, height=30)

        add_module = ttk.Button(self.inner_frame_mo, text=_tr(_T, "添加模块"), command=self.DialM.add_module, style='Project.TButton')
        add_module.place(x=100, y=25, width=100, height=30)

        load_module = ttk.Button(self.inner_frame_mo, text=_tr(_T, "导入模块"), command=self.DialM.import_module, style='Project.TButton')
        load_module.place(x=230, y=25, width=100, height=30)

        del_module = ttk.Button(self.inner_frame_mo, text=_tr(_T, "模块管理"), command=self.DialM.manage_module, style='Project.TButton')
        del_module.place(x=360, y=25, width=100, height=30)


        self.param_settings = ttk.Frame(self.inner_frame_mo)
        self.param_settings.place(x=0, y=60, width=565, height=500)
        self.scroll_area = create_scrollable_frame(self.param_settings, max_height=400)
        self.scroll_area.pack(fill="both", expand=True)



    def load_project_to_gui(self):
        """加载项目数据到GUI"""
        self._sync_project_service_from_legacy_state()
        self._sync_legacy_tables_from_service()
        # 表格的值从settings中获取
        self.DialM.refresh_constant_table()
        if self.gui_state.has_project_registry():
            self.project_combo['values'] = self.gui_state.project_names()

        current_project = self.gui_state.current_project_name()
        if current_project and current_project in self.project_combo['values']:
            self.project_combo.set(current_project) 
        self.load_module_to_gui()

    def _sync_project_service_from_legacy_state(self):
        if self.project_service is None:
            return False

        project_name = self.gui_state.current_project_name()
        if not project_name:
            return False

        if hasattr(self.project_service, "reload_registry"):
            self.project_service.reload_registry()

        try:
            self.project_service.load(project_name)
        except (KeyError, FileNotFoundError, ValueError) as exc:
            log_warning(f"服务层项目同步失败: {project_name}: {exc}", "EasyQCApp")
            return False
        return True

    def extract_qc_results(self):
        """Extract QC ratings, preferring services while preserving legacy fallback."""
        if self._load_ratings_from_service():
            return
        self.gui_state.load_ratings()

    def _load_ratings_from_service(self):
        if self.rating_service is None or self.table_service is None:
            return False

        project = self.gui_state.current_project_model()
        subjects = self.gui_state.all_variable_table()
        if project is None or subjects is None:
            return False

        rating_service = self._rating_service_for_project(project)
        if rating_service is None:
            return False

        loaded_ratings = rating_service.load_legacy_state(subjects)
        self.gui_state.apply_loaded_ratings(loaded_ratings)
        self.table_service.save_table(project, TABLE_QCTABLE, loaded_ratings.qctable)
        # P3-D / F-AGG-6: no intermediate _orig/_orig_wide files — pure in-memory
        # pipeline. Only the final ezqc_qctable.csv is persisted.
        return True

    def _rating_service_for_project(self, project):
        if self.rating_service is None:
            return None

        try:
            active_project = self.rating_service.project
        except (AttributeError, ValueError):
            active_project = None

        if active_project is not None and active_project.name == project.name and active_project.path == project.path:
            return self.rating_service

        return self.rating_service.__class__(project)

    def _sync_legacy_tables_from_service(self):
        """Synchronize legacy dt.var/dt.tab tables through the service bridge."""
        if self.table_service is None:
            return

        project = self.gui_state.current_project_model()
        if project is None:
            return

        loaded_tables = self.table_service.load_legacy_state_tables(
            project,
            module_names=self.gui_state.module_names(),
        )
        self.gui_state.apply_loaded_tables(loaded_tables)

    def load_module_to_gui(self):
        """加载模块到GUI"""
        # G9: preserve scroll position across the full rebuild so adding/removing
        # a score/tag doesn't snap the view back to the top.
        canvas = getattr(self.scroll_area, "canvas", None)
        scroll_y = canvas.yview()[0] if canvas else 0.0

        # 清空当前的卡片
        for widget in self.scroll_area.scrollable_frame.winfo_children():
            widget.destroy()

        # 按照排序后的数字key顺序创建卡片
        for qcindex in self.gui_state.module_keys():
            card = self.create_collapsible_card(qcidx=qcindex)
            card.pack(fill="x", padx=5, pady=5)

        # restore scroll position after the new widgets are laid out
        if canvas:
            self.root.update_idletasks()
            canvas.yview_moveto(scroll_y)



    def quit_app(self):
        """退出应用"""
        self.teardown_event_bus()
        self.gui_state.save_project_state()
        # self.ProjM.save_ratings()
        self.root.destroy()
        log_info("应用退出", "EasyQCApp")

    # ---- P1-D: EventBus subscription (AC-10, ADR-002) ----

    def _subscribe_event_bus(self) -> None:
        """Subscribe to typed service events. Stores handler references so
        ``teardown_event_bus`` can unsubscribe the exact same callables.
        Safe to call when no services/event_bus are wired (CLI/standalone)."""
        bus = getattr(self, "services", None)
        bus = getattr(bus, "event_bus", None)
        if bus is None:
            bus = getattr(getattr(self, "project_service", None), "event_bus", None)
        if bus is None:
            log_debug("未发现 EventBus,跳过订阅", "EasyQCApp")
            return
        self.event_bus = bus
        # bind handlers as instance attributes for stable identity (unsubscribe)
        self._project_changed_handler = self._on_project_changed
        self._modules_changed_handler = self._on_modules_changed
        bus.subscribe(EventType.PROJECT_CHANGED, self._project_changed_handler)
        bus.subscribe(EventType.MODULES_CHANGED, self._modules_changed_handler)
        log_debug("已订阅 PROJECT_CHANGED / MODULES_CHANGED 事件", "EasyQCApp")

    def teardown_event_bus(self) -> None:
        """Unsubscribe handlers. Idempotent — safe to call multiple times
        (tkinter destroy may re-enter)."""
        bus = getattr(self, "event_bus", None)
        if bus is None:
            return
        handler = getattr(self, "_project_changed_handler", None)
        if handler is not None:
            bus.unsubscribe(EventType.PROJECT_CHANGED, handler)
            self._project_changed_handler = None
        handler = getattr(self, "_modules_changed_handler", None)
        if handler is not None:
            bus.unsubscribe(EventType.MODULES_CHANGED, handler)
            self._modules_changed_handler = None
        log_debug("已退订 EventBus 处理器", "EasyQCApp")

    def _on_project_changed(self, _event=None) -> None:
        """Handle PROJECT_CHANGED: reload tables into session state and refresh
        the GUI (replaces the old ProjectManager cbProjM.load_project_to_gui).

        Reentrancy guard: load_project_to_gui calls _sync_project_service which
        calls project_service.load(), which emits PROJECT_CHANGED again — without
        this guard that is infinite recursion."""
        if getattr(self, "_refreshing", False):
            return
        self._refreshing = True
        try:
            log_debug("收到 PROJECT_CHANGED 事件,刷新项目视图", "EasyQCApp")
            if self.project_service is not None and self.table_service is not None:
                cp = self.project_service.current_project
                if cp is not None:
                    loaded = self.table_service.load_legacy_state_tables(cp)
                    if loaded is not None:
                        self.gui_state.apply_loaded_tables(loaded)
            self.load_project_to_gui()
        except Exception as e:
            log_error(f"PROJECT_CHANGED 刷新失败: {e}", "EasyQCApp")
        finally:
            self._refreshing = False

    def _on_modules_changed(self, _event=None) -> None:
        """Handle MODULES_CHANGED. Lightweight: log staleness."""
        log_debug("收到 MODULES_CHANGED 事件,标记模块视图为待刷新", "EasyQCApp")
