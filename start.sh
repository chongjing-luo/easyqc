#!/bin/bash
# EasyQC 启动脚本
# 自动生成于 Thu Jun  4 15:28:14 CST 2026
# 自动定位当前 easyqc/ 主线目录；easyqc_back/ 仅作旧版参照，不作为启动目标。

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 激活虚拟环境
if [ -f ".venv/bin/activate" ]; then
    source ".venv/bin/activate"
else
    echo "警告：未找到 .venv/bin/activate，将使用当前 Python 环境"
fi

# 启动 EasyQC
echo "启动 EasyQC..."
python "$SCRIPT_DIR/easyqc.py" "$@"
