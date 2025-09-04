#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EasyQC 日志和错误处理系统
提供统一的日志记录和错误报告功能
"""

import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Any
import tkinter as tk
from tkinter import messagebox

class EasyQCLogger:
    """EasyQC统一日志管理器"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super(EasyQCLogger, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        """初始化日志系统"""
        if self._initialized:
            return
        
        self.project_root = Path(__file__).parent.parent
        self.log_dir = self.project_root / "logs"
        self.log_dir.mkdir(exist_ok=True)
        
        # 设置日志文件路径
        self.log_file = self.log_dir / f"easyqc_{datetime.now().strftime('%Y%m%d')}.log"
        
        # 配置日志格式
        self.log_format = "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
        self.date_format = "%Y-%m-%d %H:%M:%S"
        
        # 初始化日志器
        self.logger = logging.getLogger("EasyQC")
        self.logger.setLevel(logging.DEBUG)
        
        # 清除已有的处理器
        self.logger.handlers.clear()
        
        # 文件处理器
        file_handler = logging.FileHandler(self.log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(self.log_format, self.date_format)
        file_handler.setFormatter(file_formatter)
        self.logger.addHandler(file_handler)
        
        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter("%(levelname)s - %(message)s")
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)
        
        self._initialized = True
        self.info("EasyQC日志系统初始化完成")
    
    def debug(self, message: str, module: str = None):
        """记录调试信息"""
        self._log(logging.DEBUG, message, module)
    
    def info(self, message: str, module: str = None):
        """记录一般信息"""
        self._log(logging.INFO, message, module)
    
    def warning(self, message: str, module: str = None):
        """记录警告信息"""
        self._log(logging.WARNING, message, module)
    
    def error(self, message: str, module: str = None, show_popup: bool = True):
        """记录错误信息"""
        self._log(logging.ERROR, message, module)
        if show_popup:
            self.show_error_popup("错误", message)
    
    def critical(self, message: str, module: str = None, show_popup: bool = True):
        """记录严重错误信息"""
        self._log(logging.CRITICAL, message, module)
        if show_popup:
            self.show_error_popup("严重错误", message)
    
    def exception(self, message: str, module: str = None, show_popup: bool = True):
        """记录异常信息（包含堆栈跟踪）"""
        exc_info = sys.exc_info()
        if exc_info[0] is not None:
            tb_str = ''.join(traceback.format_exception(*exc_info))
            full_message = f"{message}\n异常详情:\n{tb_str}"
            self._log(logging.ERROR, full_message, module)
            
            if show_popup:
                # 弹窗只显示简化的错误信息
                error_type = exc_info[0].__name__
                error_msg = str(exc_info[1]) if exc_info[1] else "未知错误"
                popup_message = f"{message}\n\n错误类型: {error_type}\n错误信息: {error_msg}"
                self.show_error_popup("异常错误", popup_message)
        else:
            self.error(message, module, show_popup)
    
    def _log(self, level: int, message: str, module: str = None):
        """内部日志记录方法"""
        if module:
            logger_name = f"EasyQC.{module}"
            module_logger = logging.getLogger(logger_name)
            module_logger.setLevel(self.logger.level)
            # 确保模块日志器使用相同的处理器
            if not module_logger.handlers:
                for handler in self.logger.handlers:
                    module_logger.addHandler(handler)
            module_logger.log(level, message)
        else:
            self.logger.log(level, message)
    
    def show_error_popup(self, title: str, message: str):
        """显示错误弹窗"""
        try:
            # 创建临时的根窗口（如果没有的话）
            root = None
            try:
                # 尝试获取现有的根窗口
                root = tk._default_root
                if root is None:
                    raise tk.TclError
            except (tk.TclError, AttributeError):
                # 如果没有根窗口，创建一个临时的
                root = tk.Tk()
                root.withdraw()  # 隐藏主窗口
            
            # 显示错误对话框
            messagebox.showerror(title, message)
            
            # 如果是临时创建的根窗口，销毁它
            if root and root != tk._default_root:
                root.destroy()
                
        except Exception as e:
            # 如果弹窗失败，至少在控制台输出错误
            print(f"弹窗显示失败: {e}")
            print(f"{title}: {message}")
    
    def show_info_popup(self, title: str, message: str):
        """显示信息弹窗"""
        try:
            root = None
            try:
                root = tk._default_root
                if root is None:
                    raise tk.TclError
            except (tk.TclError, AttributeError):
                root = tk.Tk()
                root.withdraw()
            
            messagebox.showinfo(title, message)
            
            if root and root != tk._default_root:
                root.destroy()
                
        except Exception as e:
            print(f"弹窗显示失败: {e}")
            print(f"{title}: {message}")
    
    def show_warning_popup(self, title: str, message: str):
        """显示警告弹窗"""
        try:
            root = None
            try:
                root = tk._default_root
                if root is None:
                    raise tk.TclError
            except (tk.TclError, AttributeError):
                root = tk.Tk()
                root.withdraw()
            
            messagebox.showwarning(title, message)
            
            if root and root != tk._default_root:
                root.destroy()
                
        except Exception as e:
            print(f"弹窗显示失败: {e}")
            print(f"{title}: {message}")
    
    def log_function_call(self, func_name: str, module: str = None, **kwargs):
        """记录函数调用"""
        args_str = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
        message = f"调用函数: {func_name}({args_str})"
        self.debug(message, module)
    
    def log_function_result(self, func_name: str, result: Any, module: str = None):
        """记录函数返回结果"""
        message = f"函数 {func_name} 返回: {result}"
        self.debug(message, module)
    
    def log_file_operation(self, operation: str, file_path: str, module: str = None):
        """记录文件操作"""
        message = f"文件操作: {operation} - {file_path}"
        self.info(message, module)
    
    def get_log_file_path(self) -> str:
        """获取当前日志文件路径"""
        return str(self.log_file)
    
    def clear_old_logs(self, days: int = 30):
        """清理旧日志文件"""
        try:
            current_time = datetime.now()
            for log_file in self.log_dir.glob("easyqc_*.log"):
                file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
                if (current_time - file_time).days > days:
                    log_file.unlink()
                    self.info(f"删除旧日志文件: {log_file.name}")
        except Exception as e:
            self.error(f"清理旧日志文件时发生错误: {e}")

# 全局日志器实例
logger = EasyQCLogger()

# 便捷函数
def log_debug(message: str, module: str = None):
    """记录调试信息"""
    logger.debug(message, module)

def log_info(message: str, module: str = None):
    """记录一般信息"""
    logger.info(message, module)

def log_warning(message: str, module: str = None, show_popup: bool = False):
    """记录警告信息"""
    logger.warning(message, module)
    if show_popup:
        logger.show_warning_popup("警告", message)

def log_error(message: str, module: str = None, show_popup: bool = True):
    """记录错误信息"""
    logger.error(message, module, show_popup)

def log_critical(message: str, module: str = None, show_popup: bool = True):
    """记录严重错误信息"""
    logger.critical(message, module, show_popup)

def log_exception(message: str, module: str = None, show_popup: bool = True):
    """记录异常信息"""
    logger.exception(message, module, show_popup)

def show_success_message(message: str, title: str = "成功"):
    """显示成功消息"""
    logger.info(f"成功: {message}")
    logger.show_info_popup(title, message)

def show_error_message(message: str, title: str = "错误"):
    """显示错误消息"""
    logger.error(message, show_popup=False)
    logger.show_error_popup(title, message)

def show_warning_message(message: str, title: str = "警告"):
    """显示警告消息"""
    logger.warning(message)
    logger.show_warning_popup(title, message)

def clear_old_logs(days: int = 30):
    """清理旧日志文件"""
    logger.clear_old_logs(days)

# 装饰器：自动记录函数调用和异常
def log_function(module: str = None):
    """函数调用日志装饰器"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            func_name = func.__name__
            try:
                logger.log_function_call(func_name, module, **kwargs)
                result = func(*args, **kwargs)
                logger.log_function_result(func_name, "成功", module)
                return result
            except Exception as e:
                logger.exception(f"函数 {func_name} 执行失败: {str(e)}", module)
                raise
        return wrapper
    return decorator

# 上下文管理器：自动记录代码块执行
class LogContext:
    """日志上下文管理器"""
    
    def __init__(self, operation: str, module: str = None):
        self.operation = operation
        self.module = module
        self.start_time = None
    
    def __enter__(self):
        self.start_time = datetime.now()
        logger.info(f"开始执行: {self.operation}", self.module)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = datetime.now() - self.start_time
        if exc_type is None:
            logger.info(f"完成执行: {self.operation} (耗时: {duration.total_seconds():.2f}秒)", self.module)
        else:
            logger.exception(f"执行失败: {self.operation} (耗时: {duration.total_seconds():.2f}秒)", self.module)
        return False  # 不抑制异常