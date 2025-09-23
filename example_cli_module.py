#!/usr/bin/env python3
"""
命令行添加模块示例
使用方法: python example_cli_module.py
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from utils.data_manager import DataManager
from utils.projects_manager import ProjectManager

def main():
    # 初始化数据管理器和项目管理器
    dt = DataManager()
    ProjM = ProjectManager()
    
    # 加载现有设置
    ProjM.load_settings()
    
    print("=== 命令行添加模块 ===")
    print("请输入模块信息:")
    
    # 获取用户输入
    name = input("模块名称: ").strip()
    if not name:
        print("错误: 模块名称不能为空")
        return
    
    label = input("模块标题: ").strip()
    if not label:
        print("错误: 模块标题不能为空")
        return
    
    index_input = input("模块索引 (数字): ").strip()
    if not index_input:
        print("错误: 模块索引不能为空")
        return
    
    # 调用非GUI版本的change_module_index
    success = ProjM.change_module_index_cli(name, label, index_input)
    
    if success:
        print(f"✓ 成功添加模块: {name}")
        # 保存设置
        ProjM.save_settings()
        print("✓ 设置已保存")
    else:
        print("✗ 添加模块失败")

if __name__ == "__main__":
    main()
