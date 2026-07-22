# EasyQC — A Configurable Workstation for Manual Visual Quality Control of MRI Data

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-1020%20passed%20%7C%204%20skipped-brightgreen.svg)](tests/)

EasyQC 是一个可配置的 MRI 人工视觉质量控制工作台。它将"打开图像 → 记录评分 → 追踪进度 → 聚合结果"的完整人工 QC 链条整合为可追踪、可复用、项目化的软件工作流。

**EasyQC 不替代** MRIQC 等自动 QC 工具，也不重新实现 NIfTI 渲染引擎。它提供的是工作流层——将专业交互式查看器（FreeSurfer freeview、HCP wb_view、FSLeyes、MRIcroGL）连接到结构化评分、进度追踪和结果聚合。

---

## 系统要求

| 要求 | 说明 |
|---|---|
| **Python** | 3.10 或更高版本 |
| **操作系统** | Linux、macOS、Windows |
| **内存** | 16GB 以上（正式基线；通常不超过 100,000 行 × 约 300 列） |
| **外部查看器**（可选） | FreeSurfer freeview、HCP wb_view、FSLeyes、MRIcroGL、ITK-SNAP 等 |

---

## 安装

当前源码检出仍使用下面的开发/迁移期安装方式。正式分发的主路线已经确定为：
由 `easyqc-install` 创建并维护一个固定版本、与系统 Python 隔离的私有环境，
支持在线/离线载荷、安装后验证、并排更新和一键回滚。该安装器的核心、CLI 与
启动器已经实现，但 Windows/macOS/Ubuntu 原生发布矩阵尚未全部完成，因此本
README 不把它描述成已经发布的跨平台安装包。

### 方式一：安装脚本（推荐 Linux/macOS）

```bash
git clone https://github.com/chongjing-luo/easyqc.git
cd easyqc
chmod +x setup.sh
./setup.sh
```

Linux 上的 Qt xcb 平台还需要系统运行库。Ubuntu/Debian 请先执行：

```bash
sudo apt install libxcb-cursor0
./setup.sh --check
```

安装脚本会对实际 `libqxcb.so` 运行 `ldd`；存在任何 `=> not found` 时会
明确失败，而不是把“PySide6 可以 import”误报为 GUI 已可用。

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

### 项目布局说明（flat layout）

EasyQC 采用 **flat layout**：`easyqc/` 目录本身**不是**可安装的 Python 包（根目录没有 `__init__.py`），而是包含一个同名启动脚本 `easyqc.py`。该脚本在运行时把项目根注入 `sys.path`，并以扁平方式导入兄弟目录（`from core.X`、`from utils.X`、`from models.X`）。

- **运行方式**：始终在项目根目录执行 `python easyqc.py`（或 `./start.sh`，后者会自动 `cd` 到正确目录）。不要从其他目录直接 `import easyqc`。
- **不支持 `pip install`**：本项目不打包为可安装包。如需在新机器部署，使用上面的安装脚本或手动创建虚拟环境 + `pip install -r requirements.txt`。
- **测试配置**：`pytest.ini` 的 `pythonpath = .` 同样依赖 flat layout（pytest 从项目根发现 `core/`/`utils/`/`models/`）。
- **正式分发**：主路线是 `easyqc-install` 管理的私有 Python 环境；仓库中的
  PyInstaller 方案仅保留为可选历史/诊断路线，不是当前发布阻塞项。

这一布局是有意的工程取舍：避免 `easyqc/` 目录与 `easyqc.py` 脚本同名引发的打包冲突，保持运行入口最简。重构为标准 src-layout 包属于未来可选改进，当前 flat layout 已稳定且有测试守卫（`tests/test_scripts/test_startup_scripts.py::test_flat_layout_imports_work_from_easyqc_root`）。

---

## 启动

### GUI 模式（完整交互操作）

```bash
./start.sh                   # Linux / macOS
python easyqc.py             # 所有平台
```

当前稳定入口仍是 tkinter。PySide6/Qt 迁移采用显式预览入口，避免在工作流
尚未完成迁移时影响真实项目：

```bash
python easyqc.py --ui qt-preview   # Qt Table/QC/配置迁移预览
python easyqc.py --ui tk           # 显式使用当前稳定 GUI（迁移期回退）
```

Qt Preview 已包含共享 Core 服务和不直接修改源表的专业 Table 工作区：类型感知的可视化
Filter Builder、多列排序、列显示/重排、固定 `ezqcid`、完整结果计数、分页、
精确查找和安全的 QC 身份校验。筛选与排序界面不显示或要求编辑 JSON。Qt 的
Table、QC 与项目配置现已通过同一个共享 Core 上下文接通真实项目，耗时
query/load/export 已移出 GUI 线程；但四个平台的真实 CI、三平台原生安装/UI
与人工可访问性门禁尚未完成，因此仍需显式选择 Preview，默认入口继续使用 tkinter。
两种 GUI 读取同一套现有 JSON/CSV 事实，Qt 层不会另建权威数据库。

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

EasyQC 内置结构化表格操作引擎，支持 8 种类型化操作。用户通过 GUI 选择列、
操作符和值，无需查看、粘贴或编辑 JSON，也无需编写代码：

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

底层仍使用可验证的结构化 Core 契约执行操作；它不是面向用户的编辑格式。
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
├── easyqc.spec                 # PyInstaller 打包配置
├── build.py                    # 一键打包脚本 (Linux/macOS/Windows)
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
# 完整/发布测试：非 GUI、tkinter、Qt 分别运行在独立进程
.venv/bin/python scripts/run_test_matrix.py

# 单进程 pytest 仅用于本地诊断（无显示器 Linux 需提供虚拟 X）
xvfb-run -a .venv/bin/python -m pytest

# 运行特定模块测试
.venv/bin/python -m pytest tests/test_core/

# 带详细输出
.venv/bin/python -m pytest -v
```

测试矩阵会完整执行三个分组；任一分组失败都会返回非零状态，但不会阻止后续
分组运行。无显示器的 Linux 环境只为 tkinter 分组调用 `xvfb-run`，Qt 分组
显式使用 offscreen 平台，从而避免迁移期在同一 Python 进程混用两个 GUI
runtime。普通 `pytest` 保留为诊断手段，不作为双 GUI 迁移期的完整发布证据。

测试覆盖：核心服务（项目 CRUD、评分聚合、命令执行、表格转换）、数据模型序列化/反序列化、输入验证、GUI 状态适配、旧版数据兼容性。

---

## 可选历史路线：冻结为自带 Python 的可执行目录

这一节记录已经验证过的 PyInstaller 诊断/可选路线，便于复现既有证据；它不
是当前主分发方案，也未取得 Windows/macOS 原生发布结论。EasyQC 可以通过
PyInstaller 打包为自带 Python、PySide6、pandas、NumPy 的
`onedir` 目录。用户无需另装 Python 包；Ubuntu 22.04 x86_64 基线产物还会
显式携带经过固定哈希验证的 `libxcb-cursor0` 运行库。其他系统依赖仍由最终
产物的 `ldd` 与原生 smoke 门禁判定，不能据此扩展为“所有 Linux”兼容声明。

### 打包流程

```bash
# 1. 安装依赖
pip install -r requirements.txt
pip install pyinstaller

# 2a. Linux x86_64：调用方先取得官方 Ubuntu Jammy deb，然后显式传入
python build.py --linux-cursor-deb /path/to/libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb
python build.py --clean --linux-cursor-deb /path/to/libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb

# 2b. macOS / Windows：在对应原生平台打包
python build.py
python build.py --version 1.0.0
```

`build.py` 不下载、不安装这个 deb，也不使用 `sudo`。Linux 构建会先校验固定
大小、deb SHA-256、Package/Version/Architecture，再解包到受控 build
sysroot；PyInstaller 只能接收验证后的 `libxcb-cursor.so.0`。产物还必须包含
`THIRD_PARTY_LICENSES/xcb-util-cursor.txt` 与确定性的 provenance 记录。

> PyInstaller 只能为**当前平台**打包。下面的命令不是正式发布流程；任何未来
> 冻结包都必须在对应原生平台重新构建、完成许可证清单并通过独立发布门禁。

### 输出

| 平台 | 产物 | 大小 |
|---|---|---|
| **Linux** | `dist/EasyQC-v1.0.0-linux-x86_64/EasyQC` | 533.219 MiB（当前 diagnostic 实测，非发布阈值） |
| **macOS** | `dist/EasyQC-v1.0.0-macos-arm64/EasyQC.app` | 待原生构建记录 |
| **Windows** | `dist/EasyQC-v1.0.0-windows-AMD64/EasyQC.exe` | 待原生构建记录 |

已验证的 Ubuntu 诊断产物可在兼容环境中运行，但不能据此承诺其他系统或未来
发行版。`build.py` 会验证最终 cursor 哈希、MIT/X notice、provenance、精确的
platformdirs 4.10.1 metadata/notice 与 `libqxcb.so` 的产物内 `ldd` 闭包，再
运行 `--help`、offscreen 以及 Linux native xcb Qt Preview 事件循环 smoke；
运行时会主动移除外部 `LD_LIBRARY_PATH`。打包 smoke 前后完整 artifact
manifest 必须一致；出现 `_internal/logs/` 或任何其他候选产物变化都会直接
失败，不会通过事后删除伪装成干净产物。Windows/macOS 不能由 Linux 交叉构建
或代验。

### 技术说明

- **打包模式**：目录模式 (`COLLECT`)，启动速度快，方便调试。如需单文件可改用 `--onefile`
- **GUI 应用**：`console=False`，Windows 下双击启动不显示命令行窗口
- **外部查看器**：需用户单独安装（FreeSurfer freeview、wb_view 等），打包文件不包含它们
- **项目文件**：`projects.json`、项目目录、日志等运行时数据不打包在内，由用户运行时动态创建
- **运行时日志**：默认写入操作系统的用户日志目录；文件日志不可用时继续 QC，并由当前 GUI 显示一次明确警告
- **迁移期默认入口**：仍为 tkinter；Qt 完成 Table/QC/配置和三平台验证后才切换默认值

---

## 许可证

EasyQC 源码使用 MIT License，详见 [LICENSE](LICENSE)。分发包中的
PySide6/Qt、PyInstaller 等第三方组件保留各自许可证；公开发布前必须附带
第三方许可证清单并完成 LGPLv3 合规复核。Linux 基线包已为
`xcb-util-cursor` 附带完整 MIT/X notice 和固定来源记录；这不替代整个包的
第三方许可证总清单。

## 引用

如果在研究中使用了 EasyQC，请引用：

> Luo C. EasyQC: A configurable workstation for manual visual quality control of MRI data. GitHub repository. https://github.com/chongjing-luo/easyqc.

## 联系方式

- **作者**: chongjing.luo@mail.bnu.edu.cn
- **机构**: 北京师范大学认知神经科学与学习国家重点实验室
