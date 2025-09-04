#!/bin/bash
# EasyQC 启动脚本
# 自动生成于 Thu Sep  4 12:36:11 CST 2025

# 切换到项目目录
cd "/Users/luochongjing/Projects/easyqc/main/easyqc"

# 激活虚拟环境
source ".venv/bin/activate"

# 启动 EasyQC
echo "启动 EasyQC..."
python "/Users/luochongjing/Projects/easyqc/main/easyqc/easyqc.py" "$@"
