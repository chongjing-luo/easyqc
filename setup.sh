#!/bin/bash
# ######################################################################
# EasyQC 项目安装脚本
# 创建Python虚拟环境并安装项目依赖
# 
# 使用方法：
#   ./setup.sh          # 安装虚拟环境
# 
# Author: chongjing.luo@mail.bnu.edu.cn
# Date: 2024.09-04
# ######################################################################

# 获取脚本所在目录并切换到项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT=$SCRIPT_DIR
cd "$PROJECT_ROOT"

# 设置环境目录
ENV_DIR=".venv"

# 检查操作系统类型
OS_TYPE=$(uname)
echo "检测到操作系统: $OS_TYPE"

# 函数：检查Python版本
check_python() {
    echo "检查Python环境..."
    
    # 检查Python 3.10是否可用
    if command -v python3.10 &> /dev/null; then
        PYTHON_CMD="python3.10"
        echo "找到Python 3.10"
    elif command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        if [[ "$PYTHON_VERSION" == "3.10" ]] || [[ "$PYTHON_VERSION" > "3.10" ]]; then
            PYTHON_CMD="python3"
            echo "找到Python $PYTHON_VERSION"
        else
            echo "错误：需要Python 3.10或更高版本，当前版本：$PYTHON_VERSION"
            exit 1
        fi
    else
        echo "错误：未找到Python 3，请先安装Python 3.10或更高版本"
        exit 1
    fi
}

# 函数：安装Python（如果需要）
install_python() {
    echo "正在安装Python 3.10..."
    
    if [ "$OS_TYPE" == "Linux" ]; then
        # Ubuntu/Debian
        if command -v apt &> /dev/null; then
            sudo apt update
            sudo apt install -y python3.10 python3.10-venv python3.10-dev
        # CentOS/RHEL
        elif command -v yum &> /dev/null; then
            sudo yum install -y python3.10 python3.10-venv python3.10-devel
        else
            echo "错误：无法识别Linux发行版，请手动安装Python 3.10"
            exit 1
        fi
    elif [ "$OS_TYPE" == "Darwin" ]; then
        # macOS
        if command -v brew &> /dev/null; then
            brew install python@3.10
        else
            echo "错误：请先安装Homebrew，然后运行: brew install python@3.10"
            exit 1
        fi
    else
        echo "错误：不支持的操作系统: $OS_TYPE"
        exit 1
    fi
}

# 函数：创建虚拟环境
create_venv() {
    echo "=========================================="
    echo "创建Python虚拟环境"
    echo "=========================================="
    
    # 检查Python
    check_python
    
    # 创建环境目录
    if [ ! -d "$ENV_DIR" ]; then
        mkdir -p "$ENV_DIR"
        echo "创建环境目录: $ENV_DIR"
    fi
    
    # 创建虚拟环境
    VENV_PATH="$ENV_DIR"
    if [ -d "$VENV_PATH" ]; then
        echo "虚拟环境已存在，将重新创建..."
        rm -rf "$VENV_PATH"
    fi
    
    echo "正在创建虚拟环境..."
    $PYTHON_CMD -m venv "$VENV_PATH"
    
    if [ $? -ne 0 ]; then
        echo "错误：创建虚拟环境失败"
        exit 1
    fi
    
    echo "虚拟环境创建成功: $VENV_PATH"
}

# 函数：安装依赖
install_dependencies() {
    echo "=========================================="
    echo "安装项目依赖"
    echo "=========================================="
    
    VENV_PATH="$ENV_DIR"
    
    # 激活虚拟环境
    echo "激活虚拟环境..."
    source "$VENV_PATH/bin/activate"
    
    # 升级pip
    echo "升级pip..."
    pip install --upgrade pip
    
    # 安装依赖
    echo "安装项目依赖..."
    if [ -f "$PROJECT_ROOT/requirements.txt" ]; then
        pip install -r "$PROJECT_ROOT/requirements.txt"
    else
        echo "错误：未找到requirements.txt文件"
        exit 1
    fi
    
    if [ $? -ne 0 ]; then
        echo "错误：安装依赖失败"
        exit 1
    fi
    
    echo "依赖安装完成"
}

# 函数：验证安装
verify_installation() {
    echo "=========================================="
    echo "验证安装"
    echo "=========================================="
    
    VENV_PATH="$ENV_DIR"
    source "$VENV_PATH/bin/activate"
    
    # 验证tkinter
    echo "验证tkinter..."
    python -c "import tkinter; print('✓ tkinter 可用')" || echo "⚠ 警告：tkinter 可能未正确安装"
    
    # 验证主要依赖
    echo "验证主要依赖..."
    python -c "
import numpy; print('✓ numpy 可用')
import pandas; print('✓ pandas 可用')
import scipy; print('✓ scipy 可用')
import matplotlib; print('✓ matplotlib 可用')
import seaborn; print('✓ seaborn 可用')
import sklearn; print('✓ scikit-learn 可用')
import nibabel; print('✓ nibabel 可用')
import pydicom; print('✓ pydicom 可用')
import PIL; print('✓ Pillow 可用')
import pandasql; print('✓ pandasql 可用')
print('所有依赖验证完成')
" || echo "⚠ 部分依赖验证失败"
    
    # 获取Python路径
    PYTHON_PATH=$(which python)
    echo "Python路径: $PYTHON_PATH"
}

# 函数：创建启动脚本
create_start_script() {
    echo "=========================================="
    echo "创建启动脚本"
    echo "=========================================="
    
    VENV_PATH="$ENV_DIR"
    
    cat > start.sh << EOF
#!/bin/bash
# EasyQC 启动脚本
# 自动生成于 $(date)

# 切换到项目目录
cd "$PROJECT_ROOT"

# 激活虚拟环境
source "$VENV_PATH/bin/activate"

# 启动 EasyQC
echo "启动 EasyQC..."
python "$PROJECT_ROOT/easyqc.py" "\$@"
EOF
    
    chmod +x start.sh
    
    echo "启动脚本已创建: start.sh"
    echo ""
    echo "使用方法:"
    echo "  ./start.sh                    # 启动 GUI 界面"
    echo "  ./start.sh project module rater ezqcid  # 直接打开 QC 页面"
}

# 函数：显示使用说明
show_usage() {
    echo "=========================================="
    echo "EasyQC 安装完成！"
    echo "=========================================="
    echo ""
    echo "环境信息："
    echo "  项目根目录: $PROJECT_ROOT"
    echo "  环境目录: $ENV_DIR"
    echo "  环境路径: $ENV_DIR"
    echo ""
    echo "启动方法："
    echo "  1. 使用启动脚本（推荐）:"
    echo "     ./start.sh"
    echo ""
    echo "  2. 手动激活环境："
    echo "     source $PROJECT_ROOT/$ENV_DIR/bin/activate"
    echo "     python $PROJECT_ROOT/easyqc.py"
    echo ""
    echo "命令行模式："
    echo "  ./start.sh project module rater ezqcid"
    echo ""
    echo "帮助信息："
    echo "  ./start.sh --help"
    echo ""
}

# 主安装流程
main() {
    echo "=========================================="
    echo "EasyQC 项目安装脚本"
    echo "=========================================="
    echo "项目根目录: $PROJECT_ROOT"
    echo "脚本目录: $SCRIPT_DIR"
    echo ""
    
    # 检查是否在正确的目录
    if [ ! -f "$SCRIPT_DIR/easyqc.py" ]; then
        echo "错误：未找到 easyqc.py 文件，请确保在正确的项目目录中运行此脚本"
        exit 1
    fi
    
    # 检查requirements.txt
    if [ ! -f "$SCRIPT_DIR/requirements.txt" ]; then
        echo "错误：未找到 requirements.txt 文件"
        exit 1
    fi
    
    # 执行安装步骤
    create_venv
    install_dependencies
    verify_installation
    create_start_script
    show_usage
    
    echo "安装完成！"
}

# 运行主函数
main "$@"