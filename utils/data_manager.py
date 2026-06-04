#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据管理器模块
负责处理所有数据相关的操作，包括数据加载、处理、分析等
"""

import os
import sys
from pathlib import Path
from typing import Any
import pandas as pd
import re


# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_info, log_error, log_warning, log_exception, log_debug, LogContext, log_function
from core.table_transform import TableTransformEngine

class DataManager:
    """数据管理器类"""
    
    @log_function("DataManager")
    def __init__(self):
        """初始化数据管理器"""
        self.table_transform = TableTransformEngine()


    def get_list(self, path:str):
        """
        提取数据
        :param path 给出一个路径，提取路径下一级所有文件夹的，转化成df
        :return: 数据框
        """
        items = []
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                items.append(item)
        return pd.DataFrame(items, columns=[0])

    def read_list(self, path:str):
        """
        读取list文件
        :param path: list文件路径, 可以是'.csv', '.xlsx', '.xls', '.txt', '.list'
        :return: 数据框
        """
        if path.endswith('.list') or path.endswith('.txt'):
            with open(path, 'r') as f:
                lines = [line.strip() for line in f.readlines()]  # 去除每行的换行符
            df = pd.DataFrame(lines, columns=['path'])
        elif path.endswith('.csv'):
            df = pd.read_csv(path)
        elif path.endswith('.xlsx') or path.endswith('.xls'):
            df = pd.read_excel(path)
        else:
            log_error(f"不支持的文件格式: {path}", "DataManager")
            df = None
        return df

    def extract_words_as_df(self, text:str):
        """
        text 是一段字符串，以空格，逗号或者换行符分隔，提取所有的单个，生成一列df
        """
        words = re.split(r'[ ,\n]+', text)
        words = [word for word in words if word]
        return pd.DataFrame(words, columns=['0'])

    def set_varname_batch(self, df:pd.DataFrame, varname:str=None, batch:str=None):
        """
        设置变量名和批次
        :param df: 数据框
        :param varname: 变量名
        :param batch: 批次
        """
        df = df.copy()

        # 如果ezqcid列不存在，则添加
        if varname and 'ezqcid' not in df.columns:
            if varname not in df.columns:
                log_error(f"变量列不存在: {varname}", "DataManager", show_popup=False)
            else:
                df['ezqcid'] = df[varname]
        # 如果ezqcbatch列不存在，或者当前的ezqcbatch和batch不同，则添加
        if batch and ('ezqcbatch' not in df.columns or df.empty or df['ezqcbatch'].iloc[0] != batch):
            df['ezqcbatch'] = batch

        return df

    def transform_table(self, df: pd.DataFrame, operations: list[dict[str, Any]]) -> pd.DataFrame:
        return self.table_transform.apply(df, operations)
