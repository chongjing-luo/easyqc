# EasyQC — A Configurable Workstation for Manual Visual Quality Control of MRI Data

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

EasyQC 是一个可配置的 MRI 人工视觉质量控制工作台。它将"打开图像 → 记录评分 → 追踪进度 → 聚合结果"的完整人工 QC 链条整合为可追踪、可复用、项目化的软件工作流。

**EasyQC 不替代** MRIQC 等自动 QC 工具，也不重新实现 NIfTI 渲染引擎。它提供的是工作流层——将专业交互式查看器（FreeSurfer freeview、HCP wb_view、FSLeyes、MRIcroGL）连接到结构化评分、进度追踪和结果聚合。

---

## 系统要求

| 要求 | 说明 |
|---|---|
| **Python** | 原生安装包无需预装；源码安装需要 3.10 或更高版本 |
| **操作系统** | Linux、macOS、Windows |
| **内存** | 建议 16GB 以上；100,000 行 × 300 列仅是修订绑定的合成名单/Formula 证据 |
| **常规规模** | 质控总名单通常不超过约 100,000 行；该约束不等同于评分文件扫描、聚合或迁移基准 |
| **外部查看器**（可选） | FreeSurfer freeview、HCP wb_view、FSLeyes、MRIcroGL、ITK-SNAP 等 |

---

## 安装

EasyQC 现在有一套原生安装包构建流程，同一源码分别在原生 runner 上生成：

| 系统 | 文件 | 安装 |
|---|---|---|
| Ubuntu 22.04/24.04 x86_64 | `EasyQC-<version>-linux-x86_64.deb` | `sudo apt install ./EasyQC-...deb` |
| Windows 11 x86_64 | `EasyQC-<version>-windows-x86_64-setup.exe` | 双击或静默安装 |
| macOS 13+ arm64 | `EasyQC-<version>-macos-arm64.dmg` | 打开 DMG，将 `EasyQC.app` 拖入 Applications |

每个产物目录同时包含 `artifact-manifest.json` 和 `SHA256SUMS`。原生包内置
EasyQC 的私有 Python/Qt 运行时，用户无需另装 Python。这些包由
`.github/workflows/native-installers.yml` 在对应操作系统上构建；不能在 Linux
上交叉生成并声称 Windows/macOS 已验证。

原生包的可变安装状态不会写入只读程序目录：`projects.json`、模板和命令设置
使用每用户、按 EasyQC 版本隔离的数据目录，真实项目仍位于用户选择的路径。
因此卸载应用不会删除项目名单或评分文件。

当前第一轮构建是**未签名测试包**。Windows 可能显示 SmartScreen 提示，macOS
可能显示 Gatekeeper 提示；在配置真实证书、公证并保留 CI 证据前，不应将其
描述为已签名公开发行版。详细构建、验证和卸载说明见
[原生安装包](docs/guide/12-native-installers.md)。

源码检出和项目内虚拟环境仍受支持，适合开发、审计或无法使用原生包的环境。
长期的 `easyqc-install` 私有 Python 环境也继续保留；原生包是便利发布层，
不是第二套 GUI 或第二套业务实现。

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
- 启动方式：`python easyqc.py`

### 项目布局说明（flat layout）

EasyQC 采用 **flat layout**：`easyqc/` 目录本身**不是**可安装的 Python 包（根目录没有 `__init__.py`），而是包含一个同名启动脚本 `easyqc.py`。该脚本在运行时把项目根注入 `sys.path`，并以扁平方式导入兄弟目录（`from core.X`、`from utils.X`、`from models.X`）。

- **运行方式**：始终在项目根目录执行 `python easyqc.py`（或 `./start.sh`，后者会自动 `cd` 到正确目录）。不要从其他目录直接 `import easyqc`。
- **不支持 `pip install`**：本项目不打包为可安装包。如需在新机器部署，使用上面的安装脚本或手动创建虚拟环境 + `pip install -r requirements.txt`。
- **测试配置**：`pytest.ini` 的 `pythonpath = .` 同样依赖 flat layout（pytest 从项目根发现 `core/`/`utils/`/`models/`）。
- **正式分发**：`easyqc-install` 管理的私有 Python 环境仍是可维护运行时方向；
  PyInstaller 原生包作为无 Python 前置要求的便利发布层，由三平台原生 CI
  从同一 revision 构建，不形成第二套产品代码。

这一布局是有意的工程取舍：避免 `easyqc/` 目录与 `easyqc.py` 脚本同名引发的打包冲突，保持运行入口最简。重构为标准 src-layout 包属于未来可选改进，当前 flat layout 已稳定且有测试守卫（`tests/test_scripts/test_startup_scripts.py::test_flat_layout_imports_work_from_easyqc_root`）。

---

## 启动

### GUI 模式（完整交互操作）

```bash
./start.sh                   # Linux / macOS
python easyqc.py             # 所有平台
```

PySide6/Qt Widgets 是 EasyQC 的唯一图形界面；默认入口无需 GUI 选择参数。
应用包含共享 Core 服务和不直接修改源表的专业 Table 工作区：类型感知的可视化
Filter Builder、多列排序、列显示/重排、固定 `easyqcid`、完整结果计数、分页、
精确查找和安全的 QC 身份校验。筛选与排序界面不显示或要求编辑 JSON。Qt 的
Table、QC 与项目配置现已通过同一个共享 Core 上下文接通真实项目，耗时
query/load/export 已移出 GUI 线程。GUI 读取现有 JSON/CSV 权威事实，不会另建
数据库。

Qt GUI 的七个导航页依次为：跨项目设置、项目选择、质控名单导入、
质控前名单、常量设置、质控模块和质控结果。首个页面集中管理当前 EasyQC
安装的常量模板、质控模块模板和命令执行模式；它不会把模板隐式注入项目。

### CLI 模式（直接打开指定 QC 页面）

```bash
./start.sh <project> <module> <rater> <easyqcid>
# 例：./start.sh CCNPPKE FreeSurferQC rater1 sub-001
```

CLI 模式绕过主窗口，适合：
- 从聚合结果表复查被标记的质控条目
- 脚本驱动的批量复核
- 已知项目/模块/质控员/质控条目时快速进入评分界面

### 旧版快照

`easyqc_back/` 只作为旧版参照、characterization tests 和兼容性对比来源，不作为日常启动目标。新功能、bug 修复和日常运行都应进入当前 `easyqc/` 主线目录。

---

## 快速上手：典型 QC 工作流

### 1. 创建项目

启动 GUI → 点击 **新建项目** → 输入项目名称并选择输出目录。

EasyQC 会自动创建项目目录结构：

```text
easyqc_<project>/
├── settings_<project>.json    # schema-v3 项目常量、变量和模块快照
├── modules/                   # 项目模块权威文件（每个模块一个 JSON）
├── Table/
│   ├── easyqc_all.csv           # 质控总名单
│   ├── easyqc_qctable.csv       # 聚合后的 QC 结果宽表
│   └── easyqc_<module>.csv      # 各模块筛选后的子表
└── RatingFiles/
    └── <module>/
        └── <rater>/
            └── <module_name>-<rater>-<easyqcid>.json
```

### 2. 构建质控总名单

质控总名单 (`easyqc_all.csv`) 至少包含一列 `easyqcid`（通用质控条目标识符）。可通过以下方式导入：

- **目录扫描**：从预处理输出目录中提取质控条目 ID
- **文件导入**：CSV / Excel / TXT / list 文件
- **手动输入**：直接粘贴或输入

总名单可包含任意自定义列（批次、模态、预处理版本、自动 QC 指标、分组标签、文件路径等），这些变量可用于命令模板替换和模块名单筛选。

### 3. 定义项目常量

在 **常量设置** 中定义项目常量（key-value 对），例如：

| 常量名 | 示例值 | 用途 |
|---|---|---|
| `SUBJECTS_DIR` | `/data/CCNPPKE/Freesurfer` | FreeSurfer 输出目录 |
| `TEMPLATE_DIR` | `/data/templates` | 模板文件目录 |
| `WB_VIEW` | `/usr/bin/wb_view` | wb_view 可执行文件路径 |

常量在命令模板中通过占位符 `$变量名` / `${变量名}` / `{变量名}` 引用。

Qt GUI 还可在 **跨项目设置 → 常量模板** 中保存常用起点，再在
**常量设置 → 从模板添加** 中复制到当前项目。复制前可以修改名称和值；
复制成功后它就是普通的项目常量，与原模板完全脱离。模板不会自动应用、
同步或参与 View 命令的变量解析。

### 4. 创建 QC 模块

每个 QC 模块代表一个检查任务。核心配置项：

| 配置项 | 说明 | 示例 |
|---|---|---|
| **模块名称** | 内部唯一标识符（1–32 个 ASCII 字母、数字或下划线） | `FreeSurferQC` |
| **显示标签** | GUI 中显示的名称 | `FreeSurfer 质控` |
| **评分者** | 当前评分者标识 | `rater1` |
| **Scores** | 评分维度（见下方详细说明） | `头动: 0-4`, `颅骨剥离: 0-4` |
| **Tags** | 布尔标签（复选框） | `需要重新处理`, `需要专家复核` |
| **命令模板** | 启动外部查看器的命令 | 见下方详细说明 |
| **名单筛选** | 限定哪些质控条目进入此模块 | `batch == "baseline"` |
| **进程控制** | 切换质控条目时自动关闭上一进程 | `true` / `false` |

常用模块可在 **跨项目设置 → 质控模块模板** 中创建、导入、编辑、导出和
删除，再从项目的 **质控模块 → 从模板添加** 复制。每次复制都会生成新的
项目模块 ID；项目副本可独立编辑，后续模板修改不会回写项目。

### 5. 执行 QC 评分

打开模块评分页面后：

1. EasyQC 自动将当前质控条目的行变量和项目常量替换到命令模板中
2. 点击 **启动** 按钮，打开外部查看器（freeview / wb_view 等）
3. 在查看器中交互式检查图像（逐层导航、对比度调整、叠加切换、多平面比较）
4. 在 EasyQC 界面中记录 Scores（程度评分）和 Tags（状态标记）
5. 点击 **保存并下一个** 保存评分并切换到下一个质控条目

每次保存都会更新该记录、模块和评分者三元组的完整 JSON 快照；同一三元组
始终写回同一路径，不建立保存事件历史。

#### 5.1 评分身份与覆盖语义

新格式把稳定身份直接写入目录和文件名：

```text
RatingFiles/<module_name>/<rater>/
  <module_name>-<rater>-<easyqcid>.json
```

`module_name` 和 `rater` 不允许短横线，因此移除末尾一个 `.json` 后，
文件名可用 `split("-", 2)` 无歧义还原；`easyqcid` 内仍可包含更多短横线。
三个内部 ID 的规范约束为：

```regex
module_name = ^[A-Za-z0-9_]{1,32}$
rater       = ^[A-Za-z0-9_]{1,32}$
easyqcid    = ^[A-Za-z0-9_.-]{1,128}$
```

模块名和评分者只能使用 ASCII 字母、数字及下划线；`easyqcid` 还允许点和
短横线。目录组件还拒绝 Windows 保留设备名，`easyqcid` 拒绝 `.` 和 `..`。
模块名在项目内、同模块评分者身份和质控总名单中的 `easyqcid` 分别按
case-insensitive 规则保持唯一，原始大小写仍保留。CSV/Excel 导入必须在
类型推断前把 `easyqcid` 作为文本读取，不能在前导零已经丢失后再转字符串。
新保存写入完整的当前模块快照，并标记 `schema_version: 3`。
同一 `(module_name, rater, easyqcid)` 再次编辑时，EasyQC 在项目写锁内创建
唯一临时文件，执行 flush/`fsync` 后用 `os.replace` 原子覆盖旧快照。

目录 `<module_name>/<rater>`、文件名中的三个字段和 JSON 正文的
`name`/`rater`/`easyqcid` 是有意保留的安全冗余。扫描会交叉核对三者；
损坏 JSON、目录或正文错位、重复三元组以及仅大小写不同的身份冲突都会
保留具体路径并明确失败，聚合不会静默跳过后继续生成一个看似完整的结果。
删除质控名单中的行或删除模块配置不会删除既有评分文件。评分仍存在时，
模块内部名不能被普通重命名或复用于另一个逻辑模块；这类变更需要显式迁移。
同理，已有评分所引用的 rater 或 `easyqcid` 不能在原位置重新指代另一人或
另一条记录。

当前程序只接受 settings 与 rating 的 `schema_version: 3`、
`easyqcid` 和 `easyqc_*.csv`。旧 `ezqc`/`ezqcid` 项目、旧评分文件名和
schema 0/1/2 评分不会被自动读取或迁移；需要继续使用旧项目时，应启动迁移前
备份版本。这个明确断代避免了双格式分支长期污染保存、扫描和聚合逻辑。

单次 QC 会话中，当前记录通过确定性规范路径定位。常规容量目标是质控总名单
通常不超过约 100,000 行；另有真实创建并扫描 100,000 个 schema-v3 JSON 的
Ubuntu 基准，对扫描、验证、展平、透视、评分字典与名单合并全链路设置
60 秒和 4 GiB 的 fail-loud 门槛。

### 6. 提取和聚合结果

在 GUI 中点击 **提取 QC 结果** → **聚合**：

1. 扫描所有 `RatingFiles/` 下的 JSON 文件
2. 验证文件路径（module/rater/easyqcid）与 JSON 内容的一致性
3. 展平嵌套 JSON → 透视为宽格式（列名：`<module>.<rater>.<field>`）
4. 与质控总名单左连接 → 得到可筛选、排序和导出的结果投影

结构化扫描结果同时包含有效记录和每一个带路径的错误。正式聚合只有在错误
集合为空时才继续；损坏、错位、重复或仅大小写冲突的记录不会被静默排除。
修复或显式迁移后应重新扫描并重建宽表。

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

GUI 中 score 显示为含“未评”的矩形可按下按钮。Scores 表达**程度**（好/坏、通过/失败）。

### Tags（布尔标签）

每个模块可定义多个 tag。每个 tag 显示为复选框，值为 `true` / `false`。Tags 表达**状态或行动**：

- `需要重新处理`（requires reprocessing）
- `需要专家复核`（requires expert review）
- `解剖失败`（anatomical failure）
- `已完成`（checkdone）

### 命令模板（Code Template）

命令模板是 EasyQC 的核心扩展机制。模板中的占位符会在运行时替换为实际值：

```bash
freeview -v $SUBJECTS_DIR/{easyqcid}/mri/T1.mgz \
         -f $SUBJECTS_DIR/{easyqcid}/surf/lh.pial:overlay=$TEMPLATE_DIR/lh.pial \
         -f $SUBJECTS_DIR/{easyqcid}/surf/rh.pial:overlay=$TEMPLATE_DIR/rh.pial
```

**占位符语法**：

| 语法 | 示例 | 替换来源 |
|---|---|---|
| `$变量名` | `$SUBJECTS_DIR` | 项目常量 |
| `${变量名}` | `${easyqcid}` | 质控总名单当前行 |
| `{变量名}` | `{easyqcid}` | 同上 |

每个模块保存一个命令模板；需要有序启动多个命令时，使用受支持的
`MULTICMD` 形式表达一个命令计划。

**执行边界**：

- 不设置命令名称白名单或黑名单；
- 新安装默认直接执行（`shell=False`）；
- 用户可在 **跨项目设置 → 命令执行** 中明确选择 `shell=True`，以使用
  管道、重定向、变量展开或命令串联；
- 该执行器负责命令解析、启动错误和进程生命周期，不是权限控制或沙箱。
  两种模式都以当前操作系统用户权限运行；Shell 模式还会让操作系统解释
  整条命令及替换后的值，因此命令模板应由本地用户审核。

### 质控名单筛选（QC-list Filter）

筛选规则限定哪些记录进入当前模块。用户在 GUI 中配置结构化比较条件
（`column operator value`），例如 `batch == baseline`；界面不要求编写
JSON、SQL 或 Python。

不设置筛选规则时，质控总名单中的所有记录均进入模块。

### 观察模式（Watch Mode）

启用后，评分页面为**只读**——可以查看已有评分但不能写入新文件。适用于：
- 主要研究者（PI）审核评分者的工作
- 教学演示
- 质量审计

---

## 表格操作

EasyQC 的统一表格工作区直接提供结构化筛选、多列排序、列显示和新增列。
用户无需查看、粘贴或编辑 JSON。视觉表格界面通过结构化 Core 契约调用
底层 `TableTransformEngine`；Core 还支持 8 种类型化操作：

| 操作 | 说明 | 示例 |
|---|---|---|
| `select_columns` | 选择并重排列 | 选取 `easyqcid, batch, age` |
| `filter_rows` | 按条件筛选 | `batch == "baseline" AND age >= 9` |
| `sort_rows` | 排序 | 按 `age` 降序 |
| `derive_column` | 派生新列 | `pass_flag = (score1 >= 2) AND not tag1` |
| `rename_columns` | 重命名列 | `batch` → `acquisition_batch` |
| `drop_columns` | 删除列 | 删除中间变量列 |
| `merge_tables` | 合并表格 | 与外部 CSV 按 `easyqcid` 合并 |
| `aggregate` | 分组聚合 | 按 `batch` 分组统计 `score1` 均值 |

这些 Core 操作不是面向用户的 JSON 编辑格式。交互式新增列使用
**EasyQC Formula**：普通用户可通过快捷模板生成公式，高级用户也可直接输入
一个表达式。精确列名写成 `[列名]`，例如：

```text
IF([site] = "A", UPPER(TEXTBEFORE([filename], "_")), "OTHER")
[parent path] & "/" & [filename]
ROUND(([age] - [baseline_age]) / 12, 1)
"fixed value"
```

- **运算符**：算术、比较、`AND`/`OR`/`NOT` 与文本连接 `&`；
- **23 个封闭函数**：控制/空值、文本提取、数值和路径文本函数；
- **安全边界**：不是完整 VBA，不接受 Python、SQL、正则、语句、对象、循环、
  用户函数、文件、网络、Shell、`eval` 或 `exec`；
- **保存语义**：预览成功后只生成普通的新列；公式、AST、快捷模板状态和
  中间结果均不保存；
- **数据范围**：导入页使用当前完整导入草稿；质控前名单和质控结果使用
  权威质控总名单。只有尚无 `easyqcid` 的导入草稿可以创建该列，已有列不能覆盖。

---

## 项目结构

```
easyqc/
├── easyqc.py                   # 主入口（GUI + CLI）
├── setup.sh                    # 安装脚本
├── start.sh                    # 启动脚本（setup.sh 自动生成）
├── requirements.txt            # Python 依赖
├── projects.json               # 项目注册表
├── constant_templates.json      # 当前安装的常量模板
├── app_settings.json            # 当前安装的命令执行模式
├── modules/                     # 当前安装的模块模板（每个模块一个 JSON）
│
├── core/                       # 核心服务层（不依赖 GUI）
│   ├── project_service.py      # 项目 CRUD + 模块管理 + 观察者通知
│   ├── module_repository.py     # 每模块一个独立 schema JSON 的加载与原子发布
│   ├── template_service.py      # 安装级模板与命令设置
│   ├── project_template_service.py # 复制模板为项目自有配置
│   ├── rating_identity.py      # 稳定评分身份、文件名和可移植路径契约
│   ├── rating_service.py       # schema-v3 扫描、规范保存、验证、聚合和透视
│   ├── rating_write_lock.py    # 项目级跨线程/跨进程评分写锁
│   ├── table_service.py        # CSV 表格加载/保存（原子写入）
│   ├── code_executor.py        # 用户可选 direct/Shell 的外部进程控制
│   ├── table_transform.py      # 结构化表格操作引擎（8 种操作）
│   ├── expression_parser.py    # 安全表达式解析器（AST 白名单）
│   ├── formula_parser.py       # EasyQC Formula 封闭语法与 AST
│   ├── formula_engine.py       # 逐列向量计算与逐行错误语义
│   └── cli_service.py          # CLI 模式启动流程
│
├── models/                     # 数据模型（纯 dataclass，零依赖）
│   ├── project.py              # Project / ProjectRegistry
│   ├── qcmodule.py             # QCModule / Score / Tag
│   └── rating.py               # Rating（序列化/反序列化）
│
├── gui_qt/                     # 唯一的 PySide6/Qt Widgets 图形界面
│   ├── application.py          # QApplication、启动页、产品/CLI-QC 入口
│   ├── main_window.py          # 七页导航与共享应用上下文
│   ├── table_workspace.py      # 虚拟化表格、筛选/排序/列/新增列
│   ├── qc_workspace.py         # 评分、标签、备注、查看器与名单导航
│   ├── cross_project_settings_page.py # 模板库和命令执行设置
│   └── template_copy_dialogs.py # 从模板添加到项目
│
├── utils/                      # 工具模块
│   ├── data_manager.py         # 数据管理（主表构建、导入）
│   ├── file_utils.py           # 文件操作（原子写入、JSON 安全读写）
│   ├── validators.py           # 输入验证（score 解析、名称校验）
│   └── logger.py               # 统一日志系统
│
├── easyqc.spec                 # PyInstaller 打包配置
├── build.py                    # 一键打包脚本 (Linux/macOS/Windows)
├── tests/                      # pytest 自动化测试
│   ├── test_core/              # 核心服务测试
│   ├── test_models/            # 数据模型测试
│   ├── test_gui_qt/            # Qt GUI 组件与工作流测试
│   ├── test_utils/             # 工具模块测试
│   └── test_integration/       # 跨服务与入口集成测试
│
└── docs/                       # 当前用户、维护者与论文写作资料
```

---

## 测试

```bash
# 完整/发布测试：无 GUI 依赖与 Qt 测试分别运行在独立进程
.venv/bin/python scripts/run_test_matrix.py

# 单进程 pytest 仅用于本地诊断
.venv/bin/python -m pytest

# 运行特定模块测试
.venv/bin/python -m pytest tests/test_core/

# 带详细输出
.venv/bin/python -m pytest -v
```

测试矩阵会完整执行两个分组；任一分组失败都会返回非零状态，但不会阻止后续
分组运行。Qt 分组显式使用 offscreen 平台并单独加载 pytest-qt，避免第三方
插件污染无 GUI 依赖的 Core 测试进程。

测试覆盖：核心服务（项目 CRUD、评分聚合、命令执行、表格转换）、schema-v3
数据模型序列化/反序列化、输入验证、Qt 工作流以及默认/CLI 入口。

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
运行 `--help`、offscreen 以及 Linux native xcb Qt 事件循环 smoke；
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
- **GUI 入口**：PySide6/Qt Widgets 是唯一支持的图形界面

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
