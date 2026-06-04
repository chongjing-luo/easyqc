#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件工具模块
提供文件操作相关的实用函数
"""

import json
import os
import shutil
from pathlib import Path
from typing import Any, Optional

from utils.logger import log_error, log_info

class FileUtils:
    """文件操作工具类"""
    
    def __init__(self):
        self.supported_formats = ['.csv', '.json']

    def read_file(self, file_path: str) -> Optional[str]:
        """读取文件内容"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            log_error(f"文件未找到: {file_path}", "FileUtils", show_popup=False)
            return None
        except Exception as e:
            log_error(f"读取文件时出错: {e}", "FileUtils", show_popup=False)
            return None


    def copy_file(self, dir_from: str, dir_to: str, files: str, subdir_list: list=None):
        source_root = Path(dir_from)
        target_root = Path(dir_to)
        filenames = [files] if isinstance(files, str) else list(files)
        subdirs = subdir_list or [None]
        copied_files = []

        for subdir in subdirs:
            source_dir = source_root / subdir if subdir else source_root
            target_dir = target_root / subdir if subdir else target_root
            target_dir.mkdir(parents=True, exist_ok=True)

            for filename in filenames:
                source_file = source_dir / filename
                target_file = target_dir / filename
                if not source_file.exists():
                    log_error(f"源文件不存在: {source_file}", "FileUtils", show_popup=False)
                    continue
                shutil.copy2(source_file, target_file)
                copied_files.append(str(target_file))
                log_info(f"复制文件: {source_file} -> {target_file}", "FileUtils")

        return copied_files

    @staticmethod
    def atomic_write(file_path: str | os.PathLike[str], content: str, encoding: str = 'utf-8') -> None:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")

        try:
            with open(temp_path, 'w', encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    @staticmethod
    def safe_json_load(file_path: str | os.PathLike[str]) -> Any:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    @staticmethod
    def safe_json_save(file_path: str | os.PathLike[str], data: Any, indent: int = 4) -> None:
        content = json.dumps(data, indent=indent, ensure_ascii=False)
        FileUtils.atomic_write(file_path, content)
