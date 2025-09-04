# EasyQC - 医学影像质量控制工具

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Author](https://img.shields.io/badge/Author-chongjing.luo-orange.svg)](mailto:chongjing.luo@mail.bnu.edu.cn)

EasyQC 是一个专业的医学影像质量控制工具，专为神经影像研究设计，支持多种医学影像格式的质量评估和评分。

## ✨ 主要特性

- 🧠 **神经影像支持**：支持 NIfTI、DICOM 等医学影像格式
- 🎯 **质量控制**：提供专业的影像质量评估工具
- 📊 **数据管理**：完整的项目和数据管理系统
- 🖥️ **用户友好**：直观的图形用户界面
- 📈 **统计分析**：内置数据分析和可视化功能
- 🔧 **可扩展性**：模块化设计，易于扩展

## 🚀 快速开始

### 系统要求

- Python 3.10 或更高版本
- 支持的操作系统：macOS、Linux、Windows
- 内存：建议 8GB 以上
- 存储：至少 2GB 可用空间

### 安装步骤

1. **克隆项目**
   ```bash
   git clone https://github.com/chongjing-luo/easyqc.git
   cd easyqc
   ```

2. **运行安装脚本**
   ```bash
   chmod +x setup.sh
   ./setup.sh
   ```

3. **启动应用**
   ```bash
   ./start.sh
   ```

### 手动安装

如果您更喜欢手动安装：

```bash
# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate  # Linux/macOS
# 或
.venv\Scripts\activate     # Windows

# 安装依赖
pip install -r requirements.txt

# 启动应用
python easyqc.py
```

## 📖 使用说明

### GUI 模式

启动图形界面：
```bash
./start.sh
```

### 命令行模式

直接打开特定的 QC 页面：
```bash
./start.sh <project> <module> <rater> <ezqcid>
```

参数说明：
- `project`: 项目名称
- `module`: 模块名称
- `rater`: 评分者名称
- `ezqcid`: 受试者ID

### 帮助信息

```bash
./start.sh --help
```

## 🏗️ 项目结构

```
easyqc/
├── easyqc.py              # 主程序入口
├── setup.sh               # 安装脚本
├── start.sh               # 启动脚本
├── requirements.txt       # Python依赖
├── projects.json          # 项目配置
├── gui/                   # 图形界面模块
│   ├── main_window.py     # 主窗口
│   ├── gui_qcpage.py      # QC页面
│   ├── gui_table.py       # 表格显示
│   └── dialog_main.py     # 对话框
├── utils/                 # 工具模块
│   ├── data_manager.py    # 数据管理
│   ├── projects_manager.py # 项目管理
│   ├── file_utils.py      # 文件工具
│   ├── logger.py          # 日志系统
│   └── qcpage.py          # QC页面逻辑
├── logs/                  # 日志文件
└── .venv/                 # 虚拟环境（自动生成）
```

## 📦 依赖包

### 核心依赖
- **数据处理**: numpy, pandas, scipy
- **图像处理**: nibabel (NIfTI), pydicom (DICOM), Pillow
- **可视化**: matplotlib, seaborn
- **科学计算**: scikit-learn
- **数据库查询**: pandasql

### 系统依赖
- **GUI框架**: tkinter (Python标准库)
- **文件处理**: pathlib, json, pickle (Python标准库)
- **多线程**: threading (Python标准库)

## 🔧 开发指南

### 环境设置

1. 确保已安装 Python 3.10+
2. 克隆项目并运行安装脚本
3. 激活虚拟环境进行开发

### 代码规范

- 使用 Python 3.10+ 语法
- 遵循 PEP 8 代码风格
- 添加适当的注释和文档字符串
- 使用日志系统记录重要信息

### 贡献指南

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 📝 更新日志

### v1.0.0 (2024-09-04)
- ✨ 初始版本发布
- 🎯 支持基本的医学影像质量控制
- 🖥️ 完整的图形用户界面
- 📊 项目和数据管理功能
- 🔧 自动化安装脚本

## 🤝 贡献者

- **chongjing.luo** - *项目创建者和主要开发者* - [chongjing-luo](https://github.com/chongjing-luo)

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 📞 联系方式

- **作者**: chongjing.luo@mail.bnu.edu.cn
- **机构**: 北京师范大学认知神经科学与学习国家重点实验室
- **地址**: 北京市海淀区新街口外大街19号

## 🙏 致谢

感谢所有为这个项目做出贡献的开发者和用户。

---

**注意**: 这是一个学术研究项目，主要用于神经影像质量控制。如果您在商业环境中使用，请确保遵守相关许可证条款。
