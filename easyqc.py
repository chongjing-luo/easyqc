#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC 主启动程序
负责启动GUI应用程序，不包含具体的界面和数据处理逻辑
支持命令行参数直接打开QC页面
"""

import sys
import os
import argparse
from pathlib import Path

# 获取电脑的操作系统
import platform
os_name = platform.system()

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_info, log_error, log_warning, log_exception, log_debug, LogContext, log_function, clear_old_logs

def parse_arguments():
    """
    解析命令行参数
    """
    parser = argparse.ArgumentParser(
        description='EasyQC - 医学影像质量控制工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python3 easyqc.py                                    # 启动GUI界面
  python3 easyqc.py project module rater ezqcid        # 直接打开QC页面
  
参数说明:
  project   - 项目名称
  module    - 模块名称  
  rater     - 评分者名称
  ezqcid    - 受试者ID
        """
    )
    
    parser.add_argument(
        'args', 
        nargs='*', 
        help='可选参数：project module rater ezqcid'
    )
    
    return parser.parse_args()

@log_function("EasyQC")
def open_qcpage_from_shell(project, module, rater, ezqcid):
    """
    从命令行直接打开QC页面
    """
    try:
        log_info(f"开始从命令行打开QC页面: project={project}, module={module}, rater={rater}, ezqcid={ezqcid}")
        
        # 导入必要的模块
        from utils.projects_manager import ProjectManager
        from gui.gui_qcpage import gui_qcpage
        import tkinter as tk
        
        # 初始化项目管理器
        pm = ProjectManager()
        pm.init_projects()
        
        # 加载指定项目
        if project not in pm.dt.projects:
            log_error(f"项目 '{project}' 不存在")
            print(f"错误：项目 '{project}' 不存在")
            print(f"可用项目: {list(pm.dt.projects.keys())}")
            return False
            
        pm.load_project(project, fresh_gui=False)
        log_info(f"成功加载项目: {project}")
        
        # 检查模块是否存在
        module_found = False
        module_index = None
        for idx, mod in pm.dt.settings['qcmodule'].items():
            if mod.get('name') == module:
                module_found = True
                module_index = idx
                break
                
        if not module_found:
            log_error(f"模块 '{module}' 在项目 '{project}' 中不存在")
            print(f"错误：模块 '{module}' 在项目 '{project}' 中不存在")
            available_modules = [mod.get('name') for mod in pm.dt.settings['qcmodule'].values()]
            print(f"可用模块: {available_modules}")
            return False
            
        log_info(f"找到模块: {module} (索引: {module_index})")
        
        # 创建QC页面实例
        qcpage_instance = gui_qcpage()
        qcpage_instance.dt = pm.dt
        qcpage_instance.module_index = module_index
        qcpage_instance.ezqcid = ezqcid
        qcpage_instance.module_name = module
        qcpage_instance.rater = rater
        
        # 设置输出目录
        qcpage_instance.dt.dir_module_rater = os.path.join(
            qcpage_instance.dt.output_dir, 'RatingFiles', module, rater
        )
        
        # 调用open_qcpage_from_shell方法
        log_info("开始调用open_qcpage_from_shell方法")
        success = qcpage_instance.open_qcpage_from_shell(project, module, rater, ezqcid)
        log_info(f"open_qcpage_from_shell返回结果: {success}")
        
        if not success:
            log_error("QC页面创建失败，停止执行")
            print("错误：QC页面创建失败")
            return False
        
        log_info("QC页面创建完成，启动GUI主循环")
        
        # 检查gui_qcpage是否存在
        if not hasattr(qcpage_instance, 'gui_qcpage') or qcpage_instance.gui_qcpage is None:
            log_error("gui_qcpage未正确创建")
            print("错误：gui_qcpage未正确创建")
            return False
            
        log_info("开始启动GUI主循环")
        qcpage_instance.gui_qcpage.mainloop()
        
        return True
        
    except Exception as e:
        log_exception(f"从命令行打开QC页面时发生错误: {e}")
        print(f"从命令行打开QC页面时发生错误：{e}")
        return False

@log_function("EasyQC")
def main():
    """
    主启动函数
    负责初始化和启动EasyQC应用程序
    """
    try:
        # 解析命令行参数
        args = parse_arguments()
        
        # 检查是否有4个参数（project, module, rater, ezqcid）
        if len(args.args) == 4:
            project, module, rater, ezqcid = args.args
            log_info(f"检测到命令行参数: project={project}, module={module}, rater={rater}, ezqcid={ezqcid}")
            
            # 直接打开QC页面
            success = open_qcpage_from_shell(project, module, rater, ezqcid)
            if not success:
                sys.exit(1)
            return
        elif len(args.args) > 0:
            print("错误：参数数量不正确")
            print("用法：python3 easyqc.py [project module rater ezqcid]")
            print("或者：python3 easyqc.py  # 启动GUI界面")
            sys.exit(1)
        
        # 默认启动GUI界面
        log_info("开始启动EasyQC应用程序")
        
        # 导入GUI模块
        from gui.main_window import EasyQCApp
        import tkinter as tk
        
        log_info("成功导入GUI模块")
        clear_old_logs()
        
        # 创建主窗口
        root = tk.Tk()
        log_info("创建主窗口成功")
        
        # 创建应用程序实例
        app = EasyQCApp(root)
        log_info("创建应用程序实例成功")
        
        # 设置窗口关闭事件
        root.protocol("WM_DELETE_WINDOW", app.quit_app)
        
        # 启动主循环
        log_info("启动GUI主循环")
        root.mainloop()
        
    except ImportError as e:
        log_error(f"无法导入必要的模块: {e}")
        print(f"错误：无法导入必要的模块 - {e}")
        print("请确保所有依赖模块都已正确安装")
        sys.exit(1)
    except Exception as e:
        log_exception(f"启动应用程序时发生错误: {e}")
        print(f"启动应用程序时发生错误：{e}")
        sys.exit(1)

if __name__ == "__main__":
    main()