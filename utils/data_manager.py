#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据管理器模块
负责处理所有数据相关的操作，包括数据加载、处理、分析等
"""

import os
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Callable, Optional
import pandas as pd
import re
from pandasql import sqldf


# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 导入日志系统
from utils.logger import log_info, log_error, log_warning, log_exception, log_debug, LogContext, log_function
from pandasql import sqldf

class DataManager:
    """数据管理器类"""
    
    @log_function("DataManager")
    def __init__(self):
        """初始化数据管理器"""
        pass


    def get_list(self, path:str):
        """
        提取数据
        :param path 给出一个路径，提取路径下一级所有文件夹的，转化成df
        :return: 数据框
        """
        df = pd.DataFrame()
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                df = pd.concat([df, pd.DataFrame([item])], ignore_index=True)
        return df

    def read_list(self, path:str):
        """
        读取list文件
        :param path: list文件路径, 可以是'.csv', '.xlsx', '.xls', '.txt', '.list'
        :return: 数据框
        """
        if path.endswith('.list') or path.endswith('.txt'):
            with open(path, 'r') as f:
                lines = f.readlines()
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
            df['ezqcid'] = df[varname]
        # 如果ezqcbatch列不存在，或者当前的ezqcbatch和batch不同，则添加
        if batch and ('ezqcbatch' not in df.columns or df['ezqcbatch'].iloc[0] != batch):
            df['ezqcbatch'] = batch

        return df


    def select_filter_sorter(self, df:pd.DataFrame, filter_words:str):
        """
        选择筛选器和排序器
        :param df: 数据框
        :param words: sql
        """
        df = df.copy()
        # 获取数据框的所有列名
        all_varname = list(df.columns)
        # 从filter_words中提取SELECT和FROM之间的部分
        select_match = re.search(r'SELECT\s+(.*?)\s+FROM', filter_words, re.IGNORECASE)
        if select_match and '_othervar_' in select_match.group(1):
            # 获取SELECT部分的内容并分割（假设各个成分以逗号分隔）
            select_vars = [var.strip() for var in select_match.group(1).split(',')]
            # 找出已经在SELECT中指定的变量
            specified_vars = [var for var in select_vars if var != '_othervar_']
            # 获取剩余的变量（从all_varname中排除已指定的变量）
            remaining_vars = [var for var in all_varname if var not in specified_vars]
            # 替换_othervar_为剩余变量（用逗号分隔）
            select_clause = select_match.group(1).replace('_othervar_', ', '.join(remaining_vars))
            # 更新filter_words中的SELECT部分
            filter_words = filter_words.replace(select_match.group(1), select_clause)

        # 使用pandasql执行SQL查询
        try:
            # 创建包含DataFrame的局部环境
            local_env = {'df': df}
            # 执行SQL查询
            result_df = sqldf(filter_words, local_env)
            if result_df is not None and not result_df.empty:
                return result_df
            else:
                log_warning("SQL查询结果为空", "DataManager")
                return df  # 返回原始数据而不是None
        except Exception as e:
            log_error(f"SQL查询执行失败: {str(e)}", "DataManager")
            return df  # 出错时返回原始数据而不是None



        