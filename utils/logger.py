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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

from platformdirs import PlatformDirs


LOG_DIR_ENV = "EASYQC_LOG_DIR"


@dataclass(frozen=True)
class LogDirectoryDecision:
    """One validated runtime-log destination decision."""

    source: str
    path: Path | None
    attempted_path: Path | None
    problem: str | None = None


@dataclass(frozen=True)
class LoggingStatus:
    """Immutable, toolkit-neutral result of configuring EasyQC logging."""

    directory_source: str
    file_logging_enabled: bool
    stream_logging_enabled: bool
    active_log_file: Path | None
    attempted_log_dir: Path | None
    warning_message: str | None


def _decision_problem(
    source: str,
    attempted_path: Path | None,
    problem: str,
) -> LogDirectoryDecision:
    return LogDirectoryDecision(
        source=source,
        path=None,
        attempted_path=attempted_path,
        problem=problem,
    )


def _normalize_log_path(
    source: str,
    candidate: Path,
    *,
    non_absolute_problem: str | None,
) -> LogDirectoryDecision:
    """Expand and resolve one candidate, degrading only expected path errors."""

    attempted_path = candidate
    try:
        attempted_path = candidate.expanduser()
        if non_absolute_problem is not None and not attempted_path.is_absolute():
            return _decision_problem(
                source,
                attempted_path,
                non_absolute_problem,
            )
        path = attempted_path.resolve()
    except (OSError, RuntimeError) as exc:
        return _decision_problem(
            source,
            attempted_path,
            f"path resolution failed: {type(exc).__name__}: {exc}",
        )
    return LogDirectoryDecision(source, path, attempted_path)


def resolve_log_dir(
    project_root: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> LogDirectoryDecision:
    """Select one log directory without creating or writing it."""

    if project_root is not None:
        return _normalize_log_path(
            "project_root",
            Path(project_root) / "logs",
            non_absolute_problem=None,
        )

    environment = os.environ if environ is None else environ
    override = environment.get(LOG_DIR_ENV, "").strip()
    if override:
        return _normalize_log_path(
            "environment",
            Path(override),
            non_absolute_problem=(
                f"{LOG_DIR_ENV} must be an absolute path after expanding '~'"
            ),
        )

    try:
        native_path = Path(PlatformDirs("EasyQC", appauthor=False).user_log_path)
    except (OSError, RuntimeError) as exc:
        return _decision_problem(
            "platformdirs",
            None,
            f"native log directory lookup failed: {type(exc).__name__}: {exc}",
        )

    return _normalize_log_path(
        "platformdirs",
        native_path,
        non_absolute_problem="platformdirs returned a non-absolute log path",
    )


def _logging_warning(decision: LogDirectoryDecision, problem: str) -> str:
    attempted = str(decision.attempted_path) if decision.attempted_path else "unavailable"
    return (
        "File logging is unavailable; EasyQC will continue, but diagnostic "
        f"records may not be saved. Source: {decision.source}; attempted: "
        f"{attempted}; reason: {problem}"
    )


class EasyQCLogger:
    """EasyQC统一日志管理器"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls, *args, **kwargs):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super(EasyQCLogger, cls).__new__(cls)
        return cls._instance
    
    def __init__(self, project_root: str | Path | None = None):
        """初始化日志系统"""
        if self._initialized and project_root is None:
            return

        self.logger = logging.getLogger("EasyQC")
        if self._initialized:
            for handler in list(self.logger.handlers):
                handler.close()

        self.logger.handlers.clear()
        for name, candidate in logging.Logger.manager.loggerDict.items():
            if name.startswith("EasyQC.") and isinstance(candidate, logging.Logger):
                candidate.handlers.clear()
                candidate.propagate = True

        self.project_root = Path(project_root) if project_root is not None else None
        decision = resolve_log_dir(project_root=project_root)
        self.log_dir = decision.path
        self.log_file: Path | None = None

        # 配置日志格式
        self.log_format = "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s"
        self.date_format = "%Y-%m-%d %H:%M:%S"
        self.logger.setLevel(logging.DEBUG)

        stream = sys.stdout if sys.stdout is not None else sys.stderr
        stream_logging_enabled = stream is not None
        if stream is not None:
            console_handler = logging.StreamHandler(stream)
            console_handler.setLevel(logging.INFO)
            console_formatter = logging.Formatter("%(levelname)s - %(message)s")
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)

        file_problem = decision.problem
        if decision.path is not None:
            candidate_log_file = decision.path / f"easyqc_{datetime.now().strftime('%Y%m%d')}.log"
            try:
                decision.path.mkdir(parents=True, exist_ok=True)
                file_handler = logging.FileHandler(candidate_log_file, encoding="utf-8")
            except OSError as exc:
                file_problem = f"{type(exc).__name__}: {exc}"
            else:
                file_handler.setLevel(logging.DEBUG)
                file_formatter = logging.Formatter(self.log_format, self.date_format)
                file_handler.setFormatter(file_formatter)
                self.logger.addHandler(file_handler)
                self.log_file = candidate_log_file

        file_logging_enabled = self.log_file is not None
        warning_message = (
            None
            if file_logging_enabled
            else _logging_warning(decision, file_problem or "unknown file logging error")
        )
        self.status = LoggingStatus(
            directory_source=decision.source,
            file_logging_enabled=file_logging_enabled,
            stream_logging_enabled=stream_logging_enabled,
            active_log_file=self.log_file,
            attempted_log_dir=decision.attempted_path,
            warning_message=warning_message,
        )

        self._initialized = True
        self.info("EasyQC日志系统初始化完成")
        if warning_message is not None:
            self.warning(warning_message, "Logger")
    
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
            module_logger.handlers.clear()
            module_logger.propagate = True
            module_logger.log(level, message)
        else:
            self.logger.log(level, message)
    
    def show_error_popup(self, title: str, message: str):
        """兼容旧 API：logger 不再负责 GUI 弹窗。"""
        self.info(f"{title}: {message}", "Popup")
    
    def show_info_popup(self, title: str, message: str):
        """兼容旧 API：logger 不再负责 GUI 弹窗。"""
        self.info(f"{title}: {message}", "Popup")
    
    def show_warning_popup(self, title: str, message: str):
        """兼容旧 API：logger 不再负责 GUI 弹窗。"""
        self.warning(f"{title}: {message}", "Popup")
    
    def log_function_call(
        self,
        func_name: str,
        module: str = None,
        args: tuple[Any, ...] | None = None,
        kwargs: dict[str, Any] | None = None,
    ):
        """记录函数调用"""
        args = args or ()
        kwargs = kwargs or {}
        args_text = [repr(arg) for arg in args]
        args_text.extend([f"{key}={value!r}" for key, value in kwargs.items()])
        message = f"调用函数: {func_name}({', '.join(args_text)})"
        self.debug(message, module)
    
    def log_function_result(self, func_name: str, result: Any, module: str = None):
        """记录函数返回结果"""
        message = f"函数 {func_name} 返回: {result}"
        self.debug(message, module)
    
    def log_file_operation(self, operation: str, file_path: str, module: str = None):
        """记录文件操作"""
        message = f"文件操作: {operation} - {file_path}"
        self.info(message, module)
    
    def get_log_file_path(self) -> str | None:
        """获取当前日志文件路径"""
        return str(self.log_file) if self.log_file is not None else None
    
    def clear_old_logs(self, days: int = 30):
        """清理旧日志文件"""
        if self.log_dir is None or not self.status.file_logging_enabled:
            return
        try:
            current_time = datetime.now()
            for log_file in self.log_dir.glob("easyqc_*.log"):
                file_time = datetime.fromtimestamp(log_file.stat().st_mtime)
                if (current_time - file_time).days > days:
                    log_file.unlink()
                    self.info(f"删除旧日志文件: {log_file.name}")
        except OSError as e:
            self.warning(f"清理旧日志文件时发生错误: {e}")

# 全局日志器实例
logger = EasyQCLogger()


def get_logging_status() -> LoggingStatus:
    """Return the current immutable logging-health snapshot."""
    return logger.status

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
        @wraps(func)
        def wrapper(*args, **kwargs):
            func_name = func.__name__
            try:
                logger.log_function_call(func_name, module, args=args, kwargs=kwargs)
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
