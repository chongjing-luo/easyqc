
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC 对话框主要功能模块
包含文件浏览、路径选择、变量设置等对话框功能
"""

import tkinter as tk
from tkinter import ttk
from tkinter.ttk import Button,  Label,  Treeview, Checkbutton, Radiobutton
from tkinter import StringVar, BooleanVar,  Scrollbar
from tkinter import messagebox
import os


from utils.logger import log_info, log_error, log_warning, log_exception, log_debug
from core.code_executor import CodeExecutor, CodeExecutorError

from gui.qc_page import QCPageController, QCPageRuntimeContext
from gui.state_adapter import LegacyGUIStateAdapter
from gui.widgets import bind_context_menu



class gui_qcpage:
    def __init__(self):
        try:
            log_debug("gui_qcpage初始化开始")
            # load total var and rater

            # 用于存储当前运行的进程句柄
            self.current_process = None
            self.code_executor = CodeExecutor()
            self.controller = QCPageController(code_executor=self.code_executor)
            log_info("gui_qcpage初始化完成")
        except Exception as e:
            log_exception(f"gui_qcpage初始化失败: {str(e)}")
            raise

    def __del__(self):
        """
        析构函数，确保在对象销毁时关闭所有进程
        """
        try:
            self.close_current_process()
        except:
            pass

    def _call_table_method(self, method_name, *args, **kwargs):
        """调用TableDisplay的方法，避免循环导入"""
        if hasattr(self, 'TablD') and self.TablD:
            method = getattr(self.TablD, method_name)
            return method(*args, **kwargs)
        else:
            log_warning(f"无法调用TableDisplay方法 {method_name}，TablD未初始化")

    def _ensure_controller(self):
        if not hasattr(self, 'controller'):
            self.controller = QCPageController()
        return self.controller

    def _ensure_runtime_context(self):
        if not hasattr(self, "runtime_context"):
            if hasattr(self, "gui_state"):
                self.runtime_context = QCPageRuntimeContext.from_gui_state(self.gui_state)
            else:
                self.runtime_context = QCPageRuntimeContext.from_legacy_dt(getattr(self, "dt", None))
        return self.runtime_context

    def _runtime_settings(self):
        return self._ensure_runtime_context().settings

    def _runtime_tables(self):
        return self._ensure_runtime_context().tables

    def _module_rater_dir(self):
        return self._ensure_runtime_context().module_rater_dir

    def _set_module_rater_dir(self, module_name, rater):
        context = self._ensure_runtime_context()
        path = self._ensure_controller().module_rater_dir(context.output_dir, module_name, rater)
        return context.set_module_rater_dir(path)

    def current_module(self, settings=None):
        if settings is None:
            settings = self._runtime_settings()
        return self._ensure_controller().current_module(settings, self.module_index)

    def open_qcpage_from_main(self, app, module_name):
        """
        从main_window打开qc页面
        :param module: 模块 
        :param project: 项目名
        """
        try:
            log_info(f"开始打开QC页面，模块: {module_name}")
            self.TablD = app.TablD
            project_manager = getattr(app, "ProjM", None)
            self.gui_state = getattr(app, "gui_state", LegacyGUIStateAdapter(project_manager))
            self.runtime_context = QCPageRuntimeContext.from_gui_state(self.gui_state)
            self.module_name = module_name
            self.ezqcid_index = None
            self.watch_mode_ = False
            
            if self.check_module():
                return
            self.module = self.current_module()
            self._set_module_rater_dir(module_name, self._ensure_controller().module_rater(self.module))
            if self.check_table():
                return
            self.gen_present()
            log_debug("保存设置")
            self.gui_state.save_settings()
            log_debug("开始创建QC页面组件")
            self.qcpage_widgets()
            log_info(f"QC页面打开完成，模块: {module_name}")

            # 填充表格数据
            self.populate_listbox()
            self.load_present_to_gui()

        except Exception as e:
            log_exception(f"打开QC页面失败，模块: {module_name}, 错误: {str(e)}")
            messagebox.showerror("错误", f"打开QC页面失败: {str(e)}")
            raise

    def check_table(self):
        # 检查和准备数据表
        log_debug(f"检查模块 {self.module_name} 数据")
        table = self._ensure_controller().ensure_module_table(self._runtime_tables(), self.module_name)

        if not self._ensure_controller().table_has_rows(table):
            log_error("数据为空")
            messagebox.showerror("错误", "数据为空")
            return False

    def check_module(self,module_name=None):
        try:
            log_debug(f"开始检查模块 {module_name}")
            if module_name is None:
                module_name = self.module_name

            controller = self._ensure_controller()
            self.module_index = controller.module_index_by_name(self._runtime_settings(), module_name)
            if self.module_index is None:
                log_error(f"找不到模块: {module_name}")
                messagebox.showerror("错误", f"找不到模块: {module_name}")
                return False
            
            module = self.current_module()
            if module['name'] != module_name:
                log_error(f"模块名不一致: {module['name']} != {module_name}")
                messagebox.showerror("错误", f"模块名不一致: {module['name']} != {module_name}")
                return False
            
            # 检查评分人设置
            rater = controller.module_rater(module)
            if rater is None or rater == '':
                log_error("评分人未设置")
                messagebox.showerror("错误", "请先设置评分人")
                return False
            
            log_debug(f"评分人: {rater}")

            # 检查scores字段
            if 'scores' in module:
                log_debug(f"检查scores字段，类型: {type(module['scores'])}")
                if isinstance(module['scores'], dict):
                    for key, item in module['scores'].items():
                        if isinstance(item, dict) and (not item.get('label') or not item.get('num_')):
                            log_error(f"scores项 {key} 配置不完整: {item}")
                            messagebox.showerror("错误", "请先设置评分项")
                            return False 
                elif isinstance(module['scores'], list):
                    for idx, item in enumerate(module['scores']):
                        if isinstance(item, dict) and (not item.get('label') or not item.get('num_')):
                            log_error(f"scores项 {idx} 配置不完整: {item}")
                            messagebox.showerror("错误", "请先设置评分项")
                            return False
                log_debug("scores字段检查通过")
            
            # 检查tags字段
            if 'tags' in module:
                log_debug(f"检查tags字段，类型: {type(module['tags'])}")
                if isinstance(module['tags'], dict):
                    for key, item in module['tags'].items():
                        if isinstance(item, dict) and not item.get('label'):
                            log_error(f"tags项 {key} 配置不完整: {item}")
                            messagebox.showerror("错误", "请先设置标签项")
                            return
                elif isinstance(module['tags'], list):
                    for idx, item in enumerate(module['tags']):
                        if isinstance(item, dict) and not item.get('label'):
                            log_error(f"tags项 {idx} 配置不完整: {item}")
                            messagebox.showerror("错误", "请先设置标签项")
                            return False
                log_debug("tags字段检查通过")
            
            log_info("qcpage_prep执行完成")

        except Exception as e:
            log_exception(f"qcpage_prep执行失败: {str(e)}")
            messagebox.showerror("错误", f"QC页面准备失败: {str(e)}")
            raise 


    def open_qcpage_from_shell(self, project, module, rater, ezqcid):
        """
        打开qc页面
        :param module: 模块
        :param project: 项目名
        输入模块名和项目名，则打开这个项目这个模块的质控页面，需要先加载项目信息、模块信息和质控信息
        """
        try:
            log_info(f"开始打开QC页面: project={project}, module={module}, rater={rater}, ezqcid={ezqcid}")
            
            self.project = project
            self.module = module
            self.rater = rater
            self.ezqcid = ezqcid
            self._set_module_rater_dir(module, rater)
            
            log_info(f"project: {project}, module: {module}, rater: {rater}, ezqcid: {ezqcid}", "QCPage")
            log_info(f"self.module_index: {self.module_index}")
            
            # 获取模块配置
            module_config = self.current_module()
            log_info(f"获取模块配置成功: {module_config.get('name', 'Unknown')}")
            
            # 创建QC页面窗口
            log_info("开始创建QC页面窗口")
            self.watch_mode_ = True
            self.qcpage_widgets(module=module_config, watch_mode=self.watch_mode_)
            log_info("QC页面窗口创建完成")
            
            # 加载评分数据
            log_info("开始加载评分数据")
            self.load_rating(ezqcid=ezqcid, module=module_config, rater=rater)
            log_info("评分数据加载完成")
            
            # 填充列表
            log_info("开始填充列表")
            module_config = self.current_module()
            self.populate_listbox(module=module_config)
            log_info("列表填充完成")
            
            # 加载到GUI
            log_info("开始加载到GUI")
            self.load_present_to_gui(module=module_config)
            log_info("GUI加载完成")
            
            log_info("QC页面打开成功")
            return True
            
        except Exception as e:
            log_exception(f"打开QC页面时发生错误: {e}")
            log_error(f"错误：打开QC页面失败 - {e}", "QCPage")
            return False

        # self.gui_qcpage.mainloop()

    def qcpage_widgets(self, module=None, watch_mode=None):
        """设置4个qc页面的评分页面（相同代码部分）"""
        try:
            
            if watch_mode is None:
                watch_mode = self.watch_mode_
            if module is None:
                module = self.current_module()
            log_debug(f"开始创建QC页面组件，模块: {module['name']}")

            self.gui_qcpage = tk.Toplevel()

            self.gui_qcpage.geometry('510x500')
            self.gui_qcpage.resizable(True, True)
            # self.gui_qcpage.grab_set()  # 设置为模态窗口
            
            # 设置窗口标题
            module_name = module['name']
            module_label = module.get('label', '未知标签')
            window_title = f"{module_name} - {module_label} - {module['rater']}"
            self.gui_qcpage.title(window_title)
            log_debug(f"QC页面窗口创建成功，标题: {window_title}")
        except Exception as e:
            log_exception(f"创建QC页面窗口失败: {str(e)}")
            messagebox.showerror("错误", f"创建QC页面窗口失败: {str(e)}")
            raise
        
        # 创建主框架
        main_frame = ttk.Frame(self.gui_qcpage, padding="10")
        main_frame.place(x=0, y=0, relwidth=1, relheight=1)
        frame_middle = ttk.Frame(self.gui_qcpage)
        frame_middle.place(x=10, y=10, width=505, height=250)  # 设置框架位置和大小

        # 配置Treeview样式
        style = ttk.Style()
        style.configure("Treeview", font=("Arial", 10))
        self.listbox = Treeview(frame_middle, columns=("index", "ezqcid", "score1", "tag1"), show='headings', selectmode='browse') 
        
        # 设置列表头
        self.listbox.heading("index", text="index")
        self.listbox.heading("ezqcid", text="ezqcid")
        self.listbox.heading("score1", text="score1")
        self.listbox.heading("tag1", text="tag1")
        
        # 配置各列的宽度和对齐方式
        self.listbox.column("index", width=35, minwidth=35, anchor="e")  # 右对齐
        self.listbox.column("ezqcid", width=220, minwidth=220, anchor="e")  # 右对齐
        self.listbox.column("score1", width=40, minwidth=40, anchor="center")  # 居中
        self.listbox.column("tag1", width=40, minwidth=40, anchor="w")  # 左对齐

        # 创建垂直滚动条
        scrollbar = Scrollbar(frame_middle)
        scrollbar.place(x=350, y=0, width=10, height=250)  # 使用place精确定位
        
        # 关联滚动条和列表
        scrollbar.config(command=self.listbox.yview)
        self.listbox.config(yscrollcommand=scrollbar.set)
        self.listbox.place(x=0, y=0, width=350, height=250)  # 使用place精确定位

        # 左键切换到某张图片
        self.listbox.bind('<Double-Button-1>', lambda event: self.navigate_subject(self.listbox.item(self.listbox.selection())['values'][1]))


        # 创建右键菜单
        def show_right_menu(event):
            """
            提取这一行的ezqcid，传递给self.show_right_menu
            """
            # 获取点击的行
            item = self.listbox.identify_row(event.y)
            if item:
                # 获取该行的数据
                values = self.listbox.item(item, 'values')
                ezqcid = values[1]
                self.TablD.show_right_menu(ezqcid, event)
        # 绑定右键菜单
        bind_context_menu(self.listbox, show_right_menu)

        # 第三部分：底部的按钮
        frame_buttons = ttk.Frame(frame_middle)
        frame_buttons.place(x=370, y=0, width=120, height=250)

        # 增加一个Checkbutton
        self.watch_mode = BooleanVar(value=watch_mode)  # 设置默认值为True(选中)
        self.checkbutton = Checkbutton(frame_buttons, text="观察模式", variable=self.watch_mode, onvalue=True, offvalue=False)
        self.checkbutton.place(x=0, y=0, width=120, height=30)

        def refresh_df_list():
            self.populate_listbox()
            self.load_present_to_gui()
        Button(frame_buttons, text="刷新", command=refresh_df_list, style = 'TButton').place(x=0, y=35, width=120, height=30)

        Button(frame_buttons, text="过滤和筛选", command=lambda: self.TablD.filter_sorter(module['name']),
               style = 'TButton').place(x=0, y=70, width=120, height=30)
        Button(frame_buttons, text="Previous", command=lambda: self.navigate_subject(-1),
               style="TButton").place(x=0, y=105, width=120, height=45)
        Button(frame_buttons, text="SaveRating", command=lambda: self.save_rating(),
               style="TButton").place(x=0, y=155, width=120, height=45)
        Button(frame_buttons, text="SaveAndNext", command=lambda: self.navigate_subject(1),
               style="TButton").place(x=0, y=205, width=120, height=45)


        try:
            log_debug(f"开始创建scores组件，scores数量: {len(module['scores'])}")
            score_frame = ttk.Frame(main_frame)
            height_1 = 45*len(module['scores'])
            score_frame.place(x=5, y=255, width=800, height=height_1)

            # 为scores创建StringVar对象
            self.score_vars = {}
            for idx, (i, score) in enumerate(module['scores'].items()):
                log_debug(f"创建score组件 {idx}: key={i}, label={score.get('label', 'N/A')}")
                Label(score_frame, text=f"{score['label']}:", style='TLabel').place(x=0, y=idx*45+2, width=95, height=40)
                
                # 创建StringVar并设置初始值
                def on_score_change(i=i):
                    self._ensure_controller().set_score_value(self.current_module(), i, self.score_vars[i].get())
                    if i == '1':
                        self.update_listbox_row()
                    self.save_rating()
                self.score_vars[i] = StringVar()
                num_ = score['num_'].split(',')
                for j in range(0, len(num_)):
                    inum = num_[j].strip()
                    Radiobutton(score_frame, text=inum, variable=self.score_vars[i],
                                value=inum, command=on_score_change).place(x=95+j*80, y=idx*45, width=75, height=40)

            log_debug("scores组件创建完成")
        except Exception as e:
            log_exception(f"创建scores组件失败: {str(e)}")
            messagebox.showerror("错误", f"创建scores组件失败: {str(e)}")
            raise

        try:
            log_debug(f"开始创建tags组件，tags数量: {len(module['tags'])}")
            tag_frame = ttk.Frame(main_frame)
            tag_frame.place(x=0, y=255+height_1, width=800, height=40)
            
            def on_tag_change(tag_key):
                self._ensure_controller().set_tag_value(self.current_module(), tag_key, self.tag_vars[tag_key].get())
                if tag_key == '1':
                    self.update_listbox_row()
                self.save_rating()
            #  为tags创建BooleanVar对象
            self.tag_vars = {}
            self.tag_checkbuttons = {}
            for idx, (tag_key, tag) in enumerate(module['tags'].items()):
                log_debug(f"创建tag组件 {idx}: key={tag_key}, label={tag.get('label', 'N/A')}")
                Label(tag_frame, text=f"{tag['label']}:", style='TLabel').place(x=0, y=idx*45+2, width=95, height=40)

                self.tag_vars[tag_key] = BooleanVar()
                self.tag_checkbuttons[tag_key] = Checkbutton(tag_frame, text=tag['label'], variable=self.tag_vars[tag_key], 
                command=lambda tag_key=tag_key: on_tag_change(tag_key))
                self.tag_checkbuttons[tag_key].place(x=idx*80, y=0, width=80, height=40)
            
            log_debug("tags组件创建完成")

        except Exception as e:
            log_exception(f"创建tags组件失败: {str(e)}")
            messagebox.showerror("错误", f"创建tags组件失败: {str(e)}")
            raise


        Label(main_frame, text="备注:", style='TLabel').place(x=0, y=300+height_1, width=40, height=30)
        # 增加一个多行文本框和滚动条
        notes_frame = ttk.Frame(main_frame)
        notes_frame.place(x=50, y=300+height_1, width=300, height=45)

        self.notes_text = tk.Text(notes_frame, wrap='word', width=40, height=4)
        self.notes_text.place(x=0, y=0, width=290, height=45)
        
        # 绑定文本变化事件以更新self.present['notes']
        def on_text_change(event):
            self._ensure_controller().set_notes(self.current_module(), self.notes_text.get('1.0', 'end-1c'))
            self.notes_text.edit_modified(False)
            self.save_rating()
        self.notes_text.bind('<<Modified>>', on_text_change)
        
        notes_scrollbar = Scrollbar(notes_frame, orient='vertical', command=self.notes_text.yview)
        notes_scrollbar.place(x=288, y=0, width=12, height=45)
        self.notes_text.config(yscrollcommand=notes_scrollbar.set)
        

        # 两个按钮，删除和清空
        Button(main_frame, text="删除", command=lambda: self.notes_text.delete('1.0', 'end'), style = 'TButton').place(x=355, y=300+height_1, width=65, height=30)
        Button(main_frame, text="清空", command=lambda: self.notes_text.delete('1.0', 'end'), style = 'TButton').place(x=425, y=300+height_1, width=65, height=30)

    def load_present_to_gui(self, module=None):

        if module is None:
            module = self.current_module()

        # 加载scores
        for i in module['scores']:
            str_ = module['scores'][i].get('value', '')
            if str_ == '':
                self.score_vars[i].set(None)
            else:
                self.score_vars[i].set(str_)

        for i in module['tags']:
            self.tag_vars[i].set(module['tags'][i].get('value', False))

        # 加载notes
        self.notes_text.delete('1.0', 'end')
        if 'notes' in module and module['notes'] is not None:
            notes_content = str(module['notes']) if module['notes'] else ''
            self.notes_text.insert('1.0', notes_content)

        self.select_listbox_ezqcid()


    def populate_listbox(self, module=None):
        """填充表格数据"""
        try:
            
            if module is None:
                module = self.current_module()
            log_debug(f"开始填充表格数据，模块: {module['name']}")


            dir_module_rater = self._module_rater_dir()
            # 清空现有数据
            for item in self.listbox.get_children():
                self.listbox.delete(item)
            
            # 初始化ezqcid到index的映射
            self.ezqcid_to_index = {}
            self.index_to_ezqcid = {}
            
            if self._runtime_tables():
                data = self._ensure_controller().module_subject_rows(self._runtime_tables(), module['name'])
                log_debug(f"找到数据表，行数: {len(data)}")
                
                for row_num, (index, row) in enumerate(data.iterrows(), 1):

                    ezqcid = row.get('ezqcid', '')
                    controller = self._ensure_controller()
                    rating_files, rating = controller.load_first_legacy_module_rating(
                        module,
                        dir_module_rater,
                        ezqcid,
                        controller.module_rater(module),
                    )
                    if rating_files and rating is not None:
                        score1 = rating['scores']['1'].get('value','')
                        tag1 = rating['tags']['1'].get('value','')
                    else:
                        score1 = ''
                        tag1 = ''

                    self.listbox.insert('', 'end', values=(row_num, ezqcid, score1, tag1))
                    self.ezqcid_to_index[ezqcid] = row_num
                    self.index_to_ezqcid[row_num] = ezqcid
                
                if self.ezqcid in self.ezqcid_to_index:
                    self.ezqcid_index = self.ezqcid_to_index[self.ezqcid]

                log_info(f"表格数据填充完成，共 {len(data)} 行")
            else:
                log_warning(f"未找到模块 {self.module_name} 的数据表")
                
        except Exception as e:
            log_exception(f"填充表格数据失败: {str(e)}")
            messagebox.showerror("错误", f"填充表格数据失败: {str(e)}")
            raise

    def select_listbox_ezqcid(self, ezqcid_index=None):
        if ezqcid_index is None:
            ezqcid_index = self.ezqcid_index
        if ezqcid_index is not None and ezqcid_index > 0:
            items = self.listbox.get_children()
            if ezqcid_index <= len(items):
                self.listbox.selection_set(items[ezqcid_index-1])
                self.listbox.see(items[ezqcid_index-1])

    def navigate_subject(self, event):
        """
        切换到下一张图片
        :param event: 事件
        """
        try:
            log_debug(f"导航到主题，事件: {event}")
            self.save_rating()
            if event == 1 or event == -1:
                self.ezqcid_index += event
                if self.ezqcid_index < 1:
                    self.ezqcid_index = len(self.listbox.get_children())
                elif self.ezqcid_index > len(self.listbox.get_children()):
                    self.ezqcid_index = 1 
                self.ezqcid = self.index_to_ezqcid[self.ezqcid_index]
                log_debug(f"导航到主题，事件: {event}，ezqcid: {self.ezqcid}", "QCPage")

            else:
                self.ezqcid = event
                self.ezqcid_index = self.ezqcid_to_index[self.ezqcid]

            self.load_rating(ezqcid=self.ezqcid)
            self.open_image(ezqcid=self.ezqcid)
            self.load_present_to_gui()
        except Exception as e:
            log_exception(f"导航主题失败: {str(e)}")
            messagebox.showerror("错误", f"导航主题失败: {str(e)}")

    def save_rating(self):
        """
        保存评分
        """
        try:
            log_debug("开始保存评分")
            module_rater_dir = self._module_rater_dir()
            if not os.path.exists(module_rater_dir):
                os.makedirs(module_rater_dir)

            if self.watch_mode.get():
                log_info(f"观察模式，不保存文件")
                return
            module = self.current_module()
            file_path = self._ensure_controller().save_legacy_module_rating(module, module_rater_dir)

            log_info(f"评分保存完成，文件: {file_path}")
        except Exception as e:
            log_exception(f"保存评分失败: {str(e)}")
            messagebox.showerror("错误", f"保存评分失败: {str(e)}")

    def load_rating(self, ezqcid=None, module=None, rater=None):
        """
        加载评分
        """
        try:
            log_debug("开始加载评分")
            if ezqcid is None:
                ezqcid = self.ezqcid
            if module is None:
                module = self.current_module()
            if rater is None:
                rater = module['rater']
            module_rater_dir = self._module_rater_dir()
            log_debug(f"module_rater_dir: {module_rater_dir}", "QCPage")
            controller = self._ensure_controller()
            rating_files, new_module = controller.load_first_legacy_module_rating(
                module,
                module_rater_dir,
                ezqcid,
                rater,
            )
            rating_filenames = [path.name for path in rating_files]
            
            if rating_files and new_module is not None:
                if len(rating_files) > 1:
                    log_warning(f"评分文件多于1个，ezqcid: {ezqcid}，加载第一条: {rating_filenames[0]}")
                current_module = self.current_module()
                compatibility_issues = controller.find_rating_compatibility_issues(current_module, new_module)
                if compatibility_issues:
                    self.watch_mode_ = True
                    if hasattr(self, "watch_mode") and hasattr(self.watch_mode, "set"):
                        self.watch_mode.set(True)
                    issue_labels = ", ".join(issue_type for issue_type, _key in compatibility_issues)
                    log_warning(
                        f"评分文件 {rating_filenames[0]} 与当前模块配置不一致({issue_labels})，已进入观察模式"
                    )
                self.apply_rating_state(current_module, new_module)

                log_debug(f"成功加载评分文件: {rating_filenames[0]}")

            elif len(rating_files) == 0:
                log_info(f"未找到评分文件，产生新的ezqcid: {ezqcid}")
                self.init_present(module, ezqcid)

        except Exception as e:
            log_exception(f"加载评分失败: {str(e)}")
            messagebox.showerror("错误", f"加载评分失败: {str(e)}")

    def apply_rating_state(self, current_module, rating_module):
        """
        只把评分状态应用到当前模块，避免评分文件覆盖模块配置。
        """
        self._ensure_controller().apply_rating_state(current_module, rating_module)

    def update_listbox_row(self, module=None):
        """更新listbox中特定行的数据"""
        try:

            if self.watch_mode.get():
                log_info(f"观察模式，不更新listbox")
                return

            if module is None:
                module = self.current_module()

            ezqcid = module['ezqcid']
            row_index = self.ezqcid_to_index[ezqcid]
            score1 = module['scores']['1']['value'] if module['scores']['1']['value'] is not None else ''
            tag1 = module['tags']['1']['value'] if module['tags']['1']['value'] is not None else ''
            
            # 获取listbox中的所有item
            items = self.listbox.get_children()
            if row_index <= len(items):
                # 使用item ID来更新特定行
                item_id = items[row_index - 1]  # row_index是从1开始的，items是从0开始的
                self.listbox.item(item_id, values=(row_index, ezqcid, score1, tag1))
                log_debug(f"更新listbox行: ezqcid={ezqcid}, row={row_index}, score1={score1}, tag1={tag1}")
            else:
                log_warning(f"无法更新listbox行: row_index {row_index} 超出范围 {len(items)}")
                
        except Exception as e:
            log_exception(f"更新listbox行失败: {str(e)}")
            messagebox.showerror("错误", f"更新listbox行失败: {str(e)}")

    def init_present(self, module, ezqcid):
        """
        初始化演示数据
        """
        log_debug(f"初始化演示数据，模块: {module['name']}, ezqcid: {ezqcid}")
        controller = self._ensure_controller()
        controller.reset_rating_state(module, ezqcid)
        controller.set_current_module(self._runtime_settings(), self.module_index, module)


    def gen_present(self, module=None):
        """
        生成演示
        """
        try:
            log_debug(f"开始生成演示数据，模块索引: {self.module_index}")
            if module is None:
                module = self.current_module()
                
            if module['ezqcid'] is None:
                log_debug(f"为模块 {module['name']} 创建演示配置")

                first_ezqcid = self._ensure_controller().first_subject_id(self._runtime_tables(), module['name'])
                if first_ezqcid is not None:
                    self.ezqcid = first_ezqcid
                    if os.path.exists(self._module_rater_dir()):
                        self.load_rating()
                    else:
                        self.init_present(module, self.ezqcid) 
            else:
                current_ezqcid = module['ezqcid']
                if not self._ensure_controller().subject_exists(self._runtime_tables(), module['name'], current_ezqcid):
                    self._ensure_controller().set_subject(self.current_module(), None)
                    self.gen_present(module)
                else:
                    self.ezqcid = current_ezqcid
                    self.load_rating()
                log_info(f"演示配置创建完成，模块: {module['name']}")

        except Exception as e:
            log_exception(f"生成演示数据失败: {str(e)}")
            messagebox.showerror("错误", f"生成演示数据失败: {str(e)}")
            raise

    def open_image(self, ezqcid=None):
        """
        打开图片
        :param ezqcid: 图片ID
        """
        try:
            if ezqcid is None:
                ezqcid = self.ezqcid

            log_debug(f"开始打开图片，ID: {ezqcid}")
            code, code_exe = self.gen_code(ezqcid)
            self._ensure_controller().set_code_execution(self.current_module(), code_exe)

            self.exe_code(code_exe)
            log_info(f"图片 {ezqcid} 打开完成")
        except Exception as e:
            log_exception(f"打开图片失败: {str(e)}")
            messagebox.showerror("错误", f"打开图片失败: {str(e)}")


    def gen_code(self,ezqcid, settings=None, module=None, table=None):

        if settings is None:
            settings = self._runtime_settings()

        if module is None:
            module = self.current_module(settings)

        if table is None:
            table = self._ensure_controller().module_table(self._runtime_tables(), module['name'])

        return self._ensure_controller().generate_code(ezqcid, settings, module, table)


    def exe_code(self, code_exe, control=None):
        """
        执行代码
        """
        if control is None:
            control = self.current_module().get('control', False)
        
        if not hasattr(self, 'code_executor'):
            self.code_executor = CodeExecutor()

        try:
            processes = self.code_executor.start_commands(code_exe, control=control)
            self.current_processes = self.code_executor.current_processes
            for i, (cmd, process) in enumerate(zip(code_exe.values(), processes), 1):
                log_info(f"命令 {i}/{len(code_exe)} 已启动，进程ID: {process.pid}")
                log_info(f"执行命令{'带控制' if control else '不带控制'}: {cmd}")
        except CodeExecutorError as e:
            log_error(f"命令执行被拒绝: {str(e)}")
        
        
        

    def close_current_process(self):
        """
        关闭当前运行的进程
        """
        if not hasattr(self, 'code_executor'):
            return

        self.code_executor.close_current_processes()
        self.current_processes = self.code_executor.current_processes
