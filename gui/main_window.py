#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC 主窗口模块
包含主要的GUI界面组件
"""

import tkinter as tk
from tkinter import ttk, scrolledtext
import sys
from pathlib import Path
import platform

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_info, log_error, log_warning, log_exception, log_debug, LogContext, log_function

from utils.file_utils import FileUtils
from utils.projects_manager import ProjectManager
from utils.data_manager import DataManager

# 导入对话框和表格显示功能类
from gui.dialog_main import DialogMain
from gui.gui_table import TableDisplay


class EasyQCApp:
    @log_function("EasyQCApp")
    def __init__(self, root):

        
        try:
            with LogContext("初始化EasyQC应用", "EasyQCApp"):
                log_info("开始初始化EasyQC应用程序", "EasyQCApp")
                
                try:

                    self.root = root
                    # 先创建基础组件
                    self.DataM = DataManager()
                    self.FileU = FileUtils()
                    
                    # 创建ProjectManager
                    self.ProjM = None
                    class Callback_ProjM:
                        def __init__(self):
                            self.load_project_to_gui = None
                            
                    Callback_ProjM = Callback_ProjM()
                    # Callback_ProjM.setup_window = self.setup_window
                    Callback_ProjM.load_project_to_gui = self.load_project_to_gui
                    self.ProjM = ProjectManager()
                    self.ProjM.init_projects(app=self, cbProjM=Callback_ProjM)
                    self.ProjM.load_project(self.ProjM.dt.project, fresh_gui=False)
                    self.dt = self.ProjM.dt
                    
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
        
        # 设置窗口图标（如果有的话）
        try:
            pass
        except:
            pass

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
        self.project_label = ttk.Label(self.inner_frame, text="项目管理", style='Title.TLabel')
        self.project_label.place(x=260, y=0, width=100, height=30)
        
        # 项目下拉列表，使用绝对定位和全局样式
        self.projects_list = tk.StringVar()
        self.project_combo = ttk.Combobox(self.inner_frame, textvariable=self.projects_list,state="readonly", style='Project.TCombobox')
        self.project_combo.place(x=5, y=30, width=240, height=35)
        

        # ---------------------------   绑定选择事件（下拉框选择时触发）   ---------------------------
        self.project_combo.bind("<<ComboboxSelected>>", lambda e: self.ProjM.change_project(self.project_combo.get()))
        
        # ---------------------------------   新建项目按钮   ---------------------------------
        self.new_project_btn = ttk.Button(self.inner_frame, text="新建项目", command=self.DialM.create_project, style='Project.TButton')
        self.new_project_btn.place(x=250, y=30, width=100, height=35)

        # ---------------------------------   导入项目按钮   ---------------------------------
        self.import_project_btn = ttk.Button(self.inner_frame, text="导入项目", command=self.DialM.import_project, style='Project.TButton')
        self.import_project_btn.place(x=355, y=30, width=100, height=35)

        # ---------------------------------   移除项目按钮   ---------------------------------
        self.remove_project_btn = ttk.Button(self.inner_frame, text="移除项目", command=self.DialM.remove_project, style='Project.TButton')
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
        self.constant_label = ttk.Label(self.inner_frame, text="常量设置", font=self.font_14)
        self.constant_label.place(x=260, y=0, width=100, height=30)

        # 常量名字文本，文本前有常量名"常量名："
        self.constant_name_label = ttk.Label(self.inner_frame, text="常量名：", font=self.font_13)
        self.constant_name_label.place(x=0, y=30, width=50, height=32)
        self.constant_name = ttk.Entry(self.inner_frame, font=self.font_12)
        self.constant_name.place(x=50, y=30, width=100, height=28)

        # 常量值文本，文本前有常量值"常量值："
        self.constant_value_label = ttk.Label(self.inner_frame, text="值：", font=self.font_13)
        self.constant_value_label.place(x=160, y=30, width=25, height=32)
        self.constant_value = ttk.Entry(self.inner_frame, font=self.font_12)
        self.constant_value.place(x=190, y=30, width=230, height=28)

        # 添加按钮
        self.constant_btn = ttk.Button(self.inner_frame, text="添加", command=self.DialM.add_constant, style='Project.TButton')
        self.constant_btn.place(x=430, y=25, width=77, height=35)


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
        self.variable_title = ttk.Label(self.inner_frame_v, text="变量设置", font=self.font_14)
        self.variable_title.place(x=260, y=0, width=100, height=30)


        # 新建/增加 按钮
        self.new_variable_btn = ttk.Button(self.inner_frame_v, text="设置初始变量", command=self.set_variable, style='Project.TButton')
        self.new_variable_btn.place(x=5, y=25, width=100, height=35)

        # 显示初始变量 按钮
        self.show_initial_table_btn = ttk.Button(self.inner_frame_v, text="显示初始表格", command=lambda:self.TablD.show_df(self.dt.var['ezqc_all']), style='Project.TButton')
        self.show_initial_table_btn.place(x=115, y=25, width=100, height=35)

        # 提取质控结果 按钮
        self.extract_qc_btn = ttk.Button(self.inner_frame_v, text="提取质控结果", command=self.ProjM.load_ratings(), style='Project.TButton')
        self.extract_qc_btn.place(x=225, y=25, width=100, height=35)

        # 过滤和筛选 按钮
        self.filter_btn = ttk.Button(self.inner_frame_v, text="过滤和筛选", command=lambda: self.TablD.filter_sorter("qctable"), style='Project.TButton')
        self.filter_btn.place(x=335, y=25, width=100, height=35)

        # 显示当前变量 按钮
        self.show_current_variable_btn = ttk.Button(self.inner_frame_v, text="显示质控表格", command=lambda: self.TablD.show_df(self.dt.tab['ezqc_qctable']), style='Project.TButton')
        self.show_current_variable_btn.place(x=445, y=25, width=100, height=35)

    def set_variable(self):
        """
        设置变量：弹出一个对话框，500*400
        """
        # 创建设置变量对话框
        self.dialog_setvar = tk.Tk()
        self.dialog_setvar.title("设置变量")
        self.dialog_setvar.geometry("500x450")
        
        # 将对话框居中显示
        self.dialog_setvar.update_idletasks()
        x = (self.dialog_setvar.winfo_screenwidth() - self.dialog_setvar.winfo_width()) // 2
        y = (self.dialog_setvar.winfo_screenheight() - self.dialog_setvar.winfo_height()) // 2
        self.dialog_setvar.geometry(f"+{x}+{y}")

        # =========================    路径导入   =============================
        # 创建主Frame作为容器
        main_frame = ttk.LabelFrame(self.dialog_setvar, text="方法1：通过路径导入")
        main_frame.place(x=10, y=15, width=480, height=70)

        # 创建内部Frame用于放置控件
        inner_frame = ttk.Frame(main_frame)
        inner_frame.place(x=10, y=0, width=460, height=70)

        self.browse_btn1 = ttk.Button(inner_frame, text="浏览", command=self.DialM.browse_path)
        self.browse_btn1.place(x=5, y=5, width=80, height=30)

        self.path_entry = ttk.Entry(inner_frame, font=self.font_12)
        self.path_entry.place(x=90, y=10, width=250, height=25)

        # 提取路径按钮
        self.extract_btn = ttk.Button(inner_frame, text="提取路径", command=self.DialM.extract_path)
        self.extract_btn.place(x=360, y=5, width=100, height=30)

        # ================================================================
        # 创建主Frame作为容器
        main_frame2 = ttk.LabelFrame(self.dialog_setvar, text="方法2：通过文件csv、excel和txt文件导入")
        main_frame2.place(x=10, y=110, width=480, height=70)

        # 创建内部Frame用于放置控件
        inner_frame2 = ttk.Frame(main_frame2)
        inner_frame2.place(x=10, y=0, width=460, height=70)

        self.browse_btn2 = ttk.Button(inner_frame2, text="浏览", command=self.DialM.browse_file)
        self.browse_btn2.place(x=5, y=5, width=80, height=30)
        
        # 文件路径输入框
        self.path_entry2 = ttk.Entry(inner_frame2, font=self.font_12)
        self.path_entry2.place(x=90, y=10, width=250, height=25)

        self.extract_btn = ttk.Button(inner_frame2, text="提取文件", command=self.DialM.extract_file)
        self.extract_btn.place(x=360, y=5, width=100, height=30)

        # ================================================================
        # 创建主Frame作为容器
        main_frame3 = ttk.LabelFrame(self.dialog_setvar, text="方法3：直接输入，以空格分隔、换行符或者回车符分隔各个条目")
        main_frame3.place(x=10, y=210, width=480, height=140)

        # 创建内部Frame用于放置控件
        inner_frame3 = ttk.Frame(main_frame3)
        inner_frame3.place(x=10, y=0, width=460, height=140)

        # 创建带滚动条的文本框
        self.text_entry = scrolledtext.ScrolledText(inner_frame3, font=self.font_12, wrap=tk.WORD, padx=5, pady=5)
        self.text_entry.place(x=10, y=5, width=440, height=80)
        self.text_entry.tag_configure('left', justify='left')

        # 确认按钮
        self.confirm_btn = ttk.Button(inner_frame3, text="确认", command=self.DialM.extract_words)
        self.confirm_btn.place(x=185, y=85, width=100, height=30)

        # ================================================================
        # 创建按钮框架
        button_frame = ttk.Frame(self.dialog_setvar)
        button_frame.place(x=10, y=360, width=480, height=80)

        # 创建按钮并水平排列
        self.show_variable_btn = ttk.Button(button_frame, text="查看新变量", command=lambda: self.TablD.show_df(self.dt.var['ezqc_new']))
        self.show_variable_btn.place(x=10, y=10, width=225, height=30)

        # 筛选按钮
        self.filter_btn = ttk.Button(button_frame, text="筛选新变量", command=lambda: self.TablD.filter_sorter("new"))
        self.filter_btn.place(x=250, y=10, width=225, height=30)

        # 新建/合并 按钮
        self.new_merge_btn = ttk.Button(button_frame, text="新建/合并", command=self.DialM.merge_newdata)
        self.new_merge_btn.place(x=10, y=40, width=140, height=30)

        # 新建/合并 按钮
        self.filter_btn_all = ttk.Button(button_frame, text="筛选总变量", command=lambda: self.TablD.filter_sorter("all"))
        self.filter_btn_all.place(x=170, y=40, width=140, height=30)

        # 查看总变量表格
        self.show_all_variable_btn = ttk.Button(button_frame, text="查看总变量", command=lambda: self.TablD.show_df(self.dt.var['ezqc_all']))
        self.show_all_variable_btn.place(x=330, y=40, width=140, height=30)


    # ----------- 卡片函数（替代 CollapsibleCard）-----------
    def create_collapsible_card(self, qcidx):
        module = self.dt.settings['qcmodule'][qcidx]
        frame = ttk.Frame(self.scroll_area.scrollable_frame)
        frame.showing = module['showing']

        # 顶部栏
        header = ttk.Frame(frame, height=30, width=520)
        header.pack(fill="x", pady=2)

        # 左侧按钮
        toggle_btn = ttk.Button(header, text="展开▼", width=6)
        toggle_btn.pack(side="left", padx=(2, 10), pady=2)

        # 标题（居中）
        title = f"{qcidx} - {module['name']} - {module['label']}"
        total_length = 45
        padding = (total_length - len(title)) // 2
        tmp_tilte = f"{' ' * padding}{title}{' ' * (total_length - padding - len(title))}"
        title_lbl = ttk.Label(header, text=tmp_tilte, font=("Arial", 14, "bold"))
        title_lbl.pack(side="left", expand=True)

        # 右侧按钮
        startqc_btn = ttk.Button(header, text="开始质控", width=8, command=lambda: self.DialM.start_qc(module))
        startqc_btn.pack(side="right", padx=(10, 10), pady=2)

        # 创建外层容器frame,设置固定高度
        # 获取tag的数量
        tag_num = len(module['tags'])
        score_num = len(module['scores'])
        height = 30 + 30 * tag_num + 30 * score_num + 165
        content_ = ttk.Frame(frame, relief="ridge", padding=10, height=height)
        content_.pack(fill="x", pady=1)
        content_.pack_propagate(False)  # 防止frame被子组件撑开
        
        # 创建内层content frame
        content = ttk.Frame(content_)
        content.place(x=0, y=0, width=520, height=height-15)

        # 过滤和筛选按钮
        filter_btn = ttk.Button(content, text="过滤和筛选", style='Project.TButton', command=lambda: self.TablD.filter_sorter(module['name']))
        filter_btn.place(x=5, y=0, width=100, height=30)

        filter_btn = ttk.Button(content, text="显示质控结果", style='Project.TButton', command=lambda: self.TablD.show_df(self.dt.tab[module['name']]))
        filter_btn.place(x=115, y=0, width=110, height=30)

        # 创建评分人标签和输入框
        label = ttk.Label(content, text="评分人:", style='TLabel')
        label.place(x=235, y=0, width=50, height=30)
        # 创建StringVar来跟踪Entry的值
        rater_var = tk.StringVar(value=module['rater'])
        entry_rater = ttk.Entry(content, textvariable=rater_var)
        entry_rater.place(x=285, y=0, height=30, width=120)
        rater_var.trace_add('write', lambda *args: module.__setitem__('rater', rater_var.get()))
        
        # 创建子进程控制复选框
        # subprocess_var = tk.BooleanVar()
        subprocess_var = tk.BooleanVar(value=module['control'])
        subprocess_check = ttk.Checkbutton(content, text="子进程控制", variable=subprocess_var,
                                        command=lambda: module.update({'control': subprocess_var.get()}))
        subprocess_check.place(x=415, y=5, width=100)
        

        # =============================       创建分数设置区域     =============================
        score_frame = ttk.Frame(content)
        score_frame.place(x=5, y=35, width=440)

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
            getattr(self, score_entry_name).place(x=140, y=row_y+3, width=150)
            
            def update_label(*args, key=score_key):
                module['scores'][key]['label'] = getattr(self, f"{module['name']}_{key}_label_var").get()
            score_label_var.trace_add("write", update_label)

            # 分值
            ttk.Label(content, text="分值:", style='TLabel').place(x=310, y=row_y)
            score_entry_name = f"{module['name']}_{score_key}_num"
            # 创建Entry和StringVar来跟踪分数值
            score_var = tk.StringVar(value=tmp_score[score_key]['num'])
            setattr(self, f"{score_entry_name}_var", score_var)
            
            # 创建Entry组件
            score_entry = ttk.Entry(content, width=8, textvariable=score_var)
            setattr(self, score_entry_name, score_entry)
            
            # 添加验证跟踪 - 仅在失去焦点时验证
            def validate_score_on_focus_out(event, key=score_key):
                result = self.DialM.validate_score(score_var.get(), module, key)
                if result is None:
                    score_var.set("")
                    module['scores'][key]['num'] = None
                    module['scores'][key]['num_'] = None
                else:
                    score_var.set(module['scores'][key]['num'])
            
            # 绑定失去焦点事件
            score_entry.bind("<FocusOut>", validate_score_on_focus_out)
            
            # 放置Entry组件
            getattr(self, score_entry_name).place(x=350, y=row_y+3)

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
            getattr(self, tag_entry_name).place(x=140, y=row_y+3, width=130)
            getattr(self, tag_var_name).trace_add("write", lambda *args, k=tag_key: 
                            module['tags'][k].update({'label': getattr(self, tag_var_name).get()}))
            
            # 在分数设置区域的每一行后面添加增加和删除按钮
            add_btn = ttk.Button(content, text="+", padding=pad, command=lambda idx=irow2: self.DialM.add_tag(qcidx, idx+1))
            add_btn.place(x=280, y=row_y+3, width=25, height=25)
            
            if irow2 > 1:
                del_btn = ttk.Button(content, text="-", padding=pad, command=lambda idx=irow2: self.DialM.del_tag(qcidx, idx))
                del_btn.place(x=310, y=row_y+3, width=25, height=25)

        # =========================   代码设置   =========================
        ystart = tag_y + len(tmp_tag) * 30 + 3
        ttk.Label(content, text="代码设置:", style='Bold.TLabel').place(x=5, y=ystart,height=30)

        # 创建一个 Frame 容器，方便放置 Text + Scrollbar
        code_frame = ttk.Frame(content)
        code_frame.place(x=80, y=ystart+3, width=400, height=100)

        # 垂直滚动条
        code_scrollbar = ttk.Scrollbar(code_frame, orient="vertical")
        code_scrollbar.pack(side="right", fill="y")

        # 多行文本框
        code_text = tk.Text(code_frame, wrap=tk.CHAR, font=("Courier", 11), undo=True)
        code_text.pack(side="left", fill="both", expand=True)
        code_scrollbar.config(command=code_text.yview)

        # 如果存在代码内容则加载
        if module['code']:
            code_text.delete('1.0', tk.END)
            code_text.insert('1.0', module['code'])

        # 绑定文本变化事件
        def on_text_change(event):
            module['code'] = code_text.get('1.0', tk.END).strip()         
        code_text.bind("<FocusOut>", on_text_change)
        
        # 创建键盘布局设置区域
        label_layout = ttk.Label(content, text="键盘布局设置:", style='TLabel')
        label_layout.place(x=5, y=ystart+110, width=110)
        browse_btn1 = ttk.Button(content, text="导入", command=self.DialM.import_json,  style='Project.TButton')
        browse_btn1.place(x=110, y=ystart+110, width=60, height=30)
        browse_btn2 = ttk.Button(content, text="导出", command=self.DialM.export_json, style='Project.TButton')
        browse_btn2.place(x=180, y=ystart+110,width=60, height=30)
        browse_btn2 = ttk.Button(content, text="显示键盘布局", command=lambda: self.DialM.show_keyboard(module), style='Project.TButton')
        browse_btn2.place(x=250, y=ystart+110,width=100, height=30)
        
        content_.pack(fill="x", pady=5) if module['showing'] else content_.pack_forget()
        
        # 切换展开/收起
        def toggle():
            if frame.showing:
                content_.pack_forget()   # 隐藏整个内容区
                toggle_btn.config(text="展开▼")
                module['showing'] = False
            else:
                content_.pack(fill="x", pady=5)  # 显示内容区
                toggle_btn.config(text="收起▲")
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

        variable_title = ttk.Label(self.inner_frame_mo, text="质控模块设置", font=self.font_14)
        variable_title.place(x=230, y=-3, width=100, height=30)

        add_module = ttk.Button(self.inner_frame_mo, text="增加模块", command=self.DialM.add_module, style='Project.TButton')
        add_module.place(x=110, y=25, width=100, height=30)

        del_module = ttk.Button(self.inner_frame_mo, text="模块管理", command=self.DialM.manage_module, style='Project.TButton')
        del_module.place(x=230, y=25, width=100, height=30)

        multi_qc = ttk.Button(self.inner_frame_mo, text="多模块质控", command=self.DialM.multi_qc, style='Project.TButton')
        multi_qc.place(x=350, y=25, width=100, height=30)


        self.param_settings = ttk.Frame(self.inner_frame_mo)
        self.param_settings.place(x=0, y=60, width=565, height=500)
        self.scroll_area = create_scrollable_frame(self.param_settings, max_height=400)
        self.scroll_area.pack(fill="both", expand=True)



    def load_project_to_gui(self):
        """加载项目数据到GUI"""
        # 表格的值从settings中获取
        self.DialM.refresh_constant_table()
        if hasattr(self.ProjM, 'dt') and 'projects' in self.ProjM.dt:
            self.project_combo['values'] = list(self.dt.projects.keys())

        if self.dt.project and self.dt.project in self.project_combo['values']:
            self.project_combo.set(self.dt.project) 
        self.load_module_to_gui()

    def load_module_to_gui(self):
        """加载模块到GUI"""
        # 获取所有的key并转换为数字进行排序
        keys = list(self.dt.settings['qcmodule'].keys())
        numeric_keys = [int(k) for k in keys]
        numeric_keys.sort()
        # 清空当前的卡片
        for widget in self.scroll_area.scrollable_frame.winfo_children():
            widget.destroy()
        
        # 按照排序后的数字key顺序创建卡片
        for key in numeric_keys:
            qcindex = str(key)  # 转回字符串形式
            card = self.create_collapsible_card(qcidx=qcindex)
            card.pack(fill="x", padx=5, pady=5)



    def quit_app(self):
        """退出应用"""
        self.ProjM.save_settings()
        self.ProjM.save_table()
        # self.ProjM.save_ratings()
        self.root.destroy()
        log_info("应用退出", "EasyQCApp")