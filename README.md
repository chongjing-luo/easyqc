# EasyQC — A Configurable Workstation for Manual Visual Quality Control of MRI Data

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-234%20passed-brightgreen.svg)](tests/)

EasyQC 是一个可配置的 MRI 人工视觉质量控制工作台。它将"打开图像 → 记录评分 → 追踪进度 → 聚合结果"的完整人工 QC 链条整合为可追踪、可复用、项目化的软件工作流。

**EasyQC 不替代** MRIQC 等自动 QC 工具，也不重新实现 NIfTI 渲染引擎。它提供的是工作流层——将专业交互式查看器（FreeSurfer freeview、HCP wb_view、FSLeyes、MRIcroGL）连接到结构化评分、进度追踪和结果聚合。

---

## 系统要求

| 要求 | 说明 |
|---|---|
| **Python** | 3.10 或更高版本 |
| **操作系统** | Linux、macOS、Windows |
| **内存** | 建议 4GB 以上 |
| **外部查看器**（可选） | FreeSurfer freeview、HCP wb_view、FSLeyes、MRIcroGL、ITK-SNAP 等 |

---

## 安装

### 方式一：安装脚本（推荐 Linux/macOS）

```bash
git clone https://github.com/chongjing-luo/easyqc.git
cd easyqc
chmod +x setup.sh
./setup.sh
```

脚本会自动：检测 Python 版本 → 创建 `.venv` 虚拟环境 → 安装依赖 → 生成 `start.sh` 启动脚本。

```bash
./setup.sh -force   # 强制重建环境
./setup.sh -check   # 仅检查环境（不安装）
```

### 方式二：手动安装（所有平台）

```bash
# 创建虚拟环境
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate      # Linux / macOS
.venv\Scripts\activate         # Windows

# 安装依赖
pip install -r requirements.txt
```

### Windows 特别说明

- 推荐使用 [Python 官方安装包](https://www.python.org/downloads/)（勾选 "Add Python to PATH"）
- tkinter 随官方 Python 一起安装，无需额外操作
- 启动方式：`python easyqc.py`

---

## 启动

### GUI 模式（完整交互操作）

```bash
./start.sh                   # Linux / macOS
python easyqc.py             # 所有平台
```

### CLI 模式（直接打开指定 QC 页面）

```bash
./start.sh <project> <module> <rater> <ezqcid>
# 例：./start.sh CCNPPKE FreeSurferQC rater1 sub-001
```

CLI 模式绕过主窗口，适合：
- 从聚合结果表复查被标记的受试者
- 脚本驱动的批量复核
- 已知项目/模块/评分者/受试者时快速进入评分界面

### 旧版快照

`easyqc_back/` 只作为旧版参照、characterization tests 和兼容性对比来源，不作为日常启动目标。新功能、bug 修复和日常运行都应进入当前 `easyqc/` 主线目录。

---

## 快速上手：典型 QC 工作流

### 1. 创建项目

启动 GUI → 点击 **新建项目** → 输入项目名称并选择输出目录。

EasyQC 会自动创建项目目录结构：

```text
easyqc_<project>/
├── settings_<project>.json    # 项目配置（模块、常量、变量）
├── Table/
│   ├── ezqc_all.csv           # 受试者主表
│   ├── ezqc_qctable.csv       # 聚合后的 QC 结果宽表
│   └── ezqc_<module>.csv      # 各模块筛选后的子表
└── RatingFiles/
    └── <module>/
        └── <rater>/
            └── <module>._.<ezqcid>._.<rater>._.<score1>._.<tag1>.json
```

### 2. 构建受试者主表

受试者主表 (`ezqc_all.csv`) 至少包含一列 `ezqcid`（受试者标识符）。可通过以下方式导入：

- **目录扫描**：自动从预处理输出目录中提取受试者 ID
- **文件导入**：CSV / Excel / TXT / list 文件
- **手动输入**：直接粘贴或输入

主表可包含任意自定义列（批次、模态、预处理版本、自动 QC 指标、分组标签、文件路径等），这些变量可用于命令模板替换和受试者筛选。

### 3. 定义项目常量

在 **设置变量** 对话框中定义项目常量（key-value 对），例如：

| 常量名 | 示例值 | 用途 |
|---|---|---|
| `SUBJECTS_DIR` | `/data/CCNPPKE/Freesurfer` | FreeSurfer 输出目录 |
| `TEMPLATE_DIR` | `/data/templates` | 模板文件目录 |
| `WB_VIEW` | `/usr/bin/wb_view` | wb_view 可执行文件路径 |

常量在命令模板中通过占位符 `$变量名` / `${变量名}` / `{变量名}` 引用。

### 4. 创建 QC 模块

每个 QC 模块代表一个检查任务。核心配置项：

| 配置项 | 说明 | 示例 |
|---|---|---|
| **模块名称** | 唯一标识符（字母/数字/下划线） | `FreeSurferQC` |
| **显示标签** | GUI 中显示的名称 | `FreeSurfer 质控` |
| **评分者** | 当前评分者标识 | `rater1` |
| **Scores** | 评分维度（见下方详细说明） | `头动: 0-4`, `颅骨剥离: 0-4` |
| **Tags** | 布尔标签（复选框） | `需要重新处理`, `需要专家复核` |
| **命令模板** | 启动外部查看器的命令 | 见下方详细说明 |
| **受试者筛选** | 限定哪些受试者进入此模块 | `batch == "baseline"` |
| **进程控制** | 切换受试者时自动关闭上一进程 | `true` / `false` |

### 5. 执行 QC 评分

打开模块评分页面后：

1. EasyQC 自动将当前受试者变量和项目常量替换到命令模板中
2. 点击 **启动** 按钮，打开外部查看器（freeview / wb_view 等）
3. 在查看器中交互式检查图像（逐层导航、对比度调整、叠加切换、多平面比较）
4. 在 EasyQC 界面中记录 Scores（程度评分）和 Tags（状态标记）
5. 点击 **下一个** 保存评分并切换到下一个受试者

每次评分保存为独立 JSON 文件，通过原子写入（先写临时文件，再 `os.replace` 重命名）防止写入中断导致的数据损坏。

### 6. 提取和聚合结果

在 GUI 中点击 **提取 QC 结果** → **聚合**：

1. 扫描所有 `RatingFiles/` 下的 JSON 文件
2. 验证文件路径（module/rater/ezqcid）与 JSON 内容的一致性
3. 展平嵌套 JSON → 透视为宽格式（列名：`<module>.<rater>.<field>`）
4. 与受试者主表合并 → 输出 `ezqc_qctable.csv`

结果表可直接导入 R / Python 进行：样本排除（按 tag 筛选）、QC 评分分布统计、多评分者一致性分析（Cohen's κ、ICC）。

---

## 配置详解

### Scores（评分维度）

每个模块可定义多个 score。每个 score 的 **num** 字段支持三种格式：

| 格式 | 输入 | 展开后可选值 | 说明 |
|---|---|---|---|
| **单个正整数** | `4` | `1,2,3,4` | 自动展开为 1 到 N |
| **范围** | `0-4` | `0,1,2,3,4` | 闭区间，start ≤ end |
| **标签列表** | `差,中,良,优` | `差,中,良,优` | 用于分类评分（不区分大小写、允许空格） |

GUI 中 score 显示为下拉选项框。Scores 表达**程度**（好/坏、通过/失败）。

### Tags（布尔标签）

每个模块可定义多个 tag。每个 tag 显示为复选框，值为 `true` / `false`。Tags 表达**状态或行动**：

- `需要重新处理`（requires reprocessing）
- `需要专家复核`（requires expert review）
- `解剖失败`（anatomical failure）
- `已完成`（checkdone）

### 命令模板（Code Template）

命令模板是 EasyQC 的核心扩展机制。模板中的占位符会在运行时替换为实际值：

```bash
freeview -v $SUBJECTS_DIR/{ezqcid}/mri/T1.mgz \
         -f $SUBJECTS_DIR/{ezqcid}/surf/lh.pial:overlay=$TEMPLATE_DIR/lh.pial \
         -f $SUBJECTS_DIR/{ezqcid}/surf/rh.pial:overlay=$TEMPLATE_DIR/rh.pial
```

**占位符语法**：

| 语法 | 示例 | 替换来源 |
|---|---|---|
| `$变量名` | `$SUBJECTS_DIR` | 项目常量 |
| `${变量名}` | `${ezqcid}` | 受试者主表行 |
| `{变量名}` | `{ezqcid}` | 同上 |

**多个命令模板**：一个模块可配置多条命令模板（如不同的叠加设置、对比度、视角），评分时通过下拉菜单选择执行哪个模板。

**安全机制**：
- 命令可执行文件必须在白名单内（默认：`freeview`, `wb_view`, `fslview`, `mricron`, `itksnap`, `mricroGL`, `MRIcroGL`, `open`, `python`, `python3`）
- 所有命令以 `shell=False` 执行
- 拒绝 shell 控制操作符（`;`, `&&`, `||`, `|`）

### 受试者筛选（Subject Filter）

筛选规则限定哪些受试者进入当前模块。支持两种方式：

1. **结构化筛选条件**：在 GUI 中直接配置比较条件（`column operator value`），如 `batch == baseline`
2. **兼容旧版 SELECT 语法**：`SELECT * FROM df WHERE batch = 'baseline'`（向后兼容，仅支持简单 AND 条件）

不设置筛选规则时，主表中所有受试者均进入模块。

### 观察模式（Watch Mode）

启用后，评分页面为**只读**——可以查看已有评分但不能写入新文件。适用于：
- 主要研究者（PI）审核评分者的工作
- 教学演示
- 质量审计

---

## 表格操作

EasyQC 内置结构化表格操作引擎，支持 8 种内置 JSON 结构化操作，无需编写代码：

| 操作 | 说明 | 示例 |
|---|---|---|
| `select_columns` | 选择并重排列 | 选取 `ezqcid, batch, age` |
| `filter_rows` | 按条件筛选 | `batch == "baseline" AND age >= 9` |
| `sort_rows` | 排序 | 按 `age` 降序 |
| `derive_column` | 派生新列 | `pass_flag = (score1 >= 2) AND not tag1` |
| `rename_columns` | 重命名列 | `batch` → `acquisition_batch` |
| `drop_columns` | 删除列 | 删除中间变量列 |
| `merge_tables` | 合并表格 | 与外部 CSV 按 `ezqcid` 合并 |
| `aggregate` | 分组聚合 | 按 `batch` 分组统计 `score1` 均值 |

所有操作在 GUI 中组合为操作序列，一次执行。派生列表达式通过安全解析器验证：
- **白名单运算符**：`+`, `-`, `*`, `/`, `==`, `!=`, `>`, `>=`, `<`, `<=`, `and`, `or`, `not`
- **白名单函数**：`abs`, `round`, `isna`, `notna`, `fillna`, `contains`, `startswith`, `endswith`, `isin`
- **禁止**：`eval()`, `exec()`, `lambda`, `import`, 任意属性访问

---

## 项目结构

```
easyqc/
├── easyqc.py                   # 主入口（GUI + CLI）
├── setup.sh                    # 安装脚本
├── start.sh                    # 启动脚本（setup.sh 自动生成）
├── requirements.txt            # Python 依赖
├── projects.json               # 项目注册表
│
├── core/                       # 核心服务层（不依赖 GUI）
│   ├── project_service.py      # 项目 CRUD + 模块管理 + 观察者通知
│   ├── rating_service.py       # 评分 JSON 扫描/验证/保存/聚合/透视
│   ├── table_service.py        # CSV 表格加载/保存（原子写入）
│   ├── code_executor.py        # 受控外部命令执行（白名单 + shell=False）
│   ├── table_transform.py      # 结构化表格操作引擎（8 种操作）
│   ├── expression_parser.py    # 安全表达式解析器（AST 白名单）
│   └── cli_service.py          # CLI 模式启动流程
│
├── models/                     # 数据模型（纯 dataclass，零依赖）
│   ├── project.py              # Project / ProjectRegistry
│   ├── qcmodule.py             # QCModule / Score / Tag
│   └── rating.py               # Rating（序列化/反序列化）
│
├── gui/                        # 图形界面
│   ├── app.py                  # 应用入口（组装 services + 启动主窗口）
│   ├── main_window.py          # 主窗口（项目管理、模块列表、菜单）
│   ├── qc_page.py              # QC 评分页运行时上下文
│   ├── gui_qcpage.py           # QC 评分页 GUI（启动命令、记录评分）
│   ├── table_view.py           # 表格浏览与操作
│   ├── gui_table.py            # 表格显示组件
│   ├── state_adapter.py        # GUI 状态适配层（兼容旧 GUI → 新 models）
│   ├── dialog_main.py          # 对话框（设置变量、命令模板、筛选等）
│   ├── dialogs.py              # 新对话框组件
│   └── widgets.py              # 通用 GUI 组件
│
├── utils/                      # 工具模块
│   ├── data_manager.py         # 数据管理（主表构建、导入）
│   ├── projects_manager.py     # 项目管理器（旧版兼容层）
│   ├── file_utils.py           # 文件操作（原子写入、JSON 安全读写）
│   ├── validators.py           # 输入验证（score 解析、名称校验）
│   └── logger.py               # 统一日志系统
│
├── tests/                      # pytest 自动化测试（234 个测试函数）
│   ├── test_core/              # 核心服务测试
│   ├── test_models/            # 数据模型测试
│   ├── test_gui/               # GUI 组件测试
│   ├── test_utils/             # 工具模块测试
│   ├── test_integration/       # 集成测试（含 devCCNP 兼容性）
│   └── test_characterization/  # 旧版兼容性特征测试
│
└── logs/                       # 日志文件
```

---

## 测试

```bash
# 运行全部测试
.venv/bin/python -m pytest

# 运行特定模块测试
.venv/bin/python -m pytest tests/test_core/

# 带详细输出
.venv/bin/python -m pytest -v
```

测试覆盖：核心服务（项目 CRUD、评分聚合、命令执行、表格转换）、数据模型序列化/反序列化、输入验证、GUI 状态适配、旧版数据兼容性。

---

## 许可证

MIT License — 详见 [LICENSE](LICENSE)。

## 引用

如果在研究中使用了 EasyQC，请引用：

> Luo C. EasyQC: A configurable workstation for manual visual quality control of MRI data. GitHub repository. https://github.com/chongjing-luo/easyqc.

## 联系方式

- **作者**: chongjing.luo@mail.bnu.edu.cn
- **机构**: 北京师范大学认知神经科学与学习国家重点实验室
