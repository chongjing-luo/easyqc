# EasyQC 论文写作 Agent 事实表

## 1. 本文件的用途

本文件是论文材料事实源，不是论文初稿。写作 Agent 应先区分四类陈述：

- **implemented**：当前代码实现；
- **tested**：当前自动化测试覆盖合同；
- **measured/demonstrated**：特定修订与环境有保留证据；
- **planned/unverified**：设计目标或待完成原生验证。

不得把后一类改写为已实现，也不得把 offscreen CI 改写成真实临床/研究现场验证。

## 2. 推荐的一句话定位

中文：

> EasyQC 是一个离线、可配置的人工视觉质量控制工作台，将通用质控名单、模块独立队列、外部专业查看器、结构化人工判断与可重建结果连接为本地可追踪工作流。

英文建议：

> EasyQC is an offline, configurable workstation that connects generic QC records, module-specific queues, external domain viewers, structured human ratings, and rebuildable result projections into a traceable local workflow for manual visual quality control.

避免称为 MRI renderer、automatic QC classifier、database platform、cloud collaboration service 或 immutable audit-log system。

## 3. 研究问题与软件缺口

人工视觉 QC 常由文件浏览器、查看器命令、电子表格和备注拼接完成。问题不只是打开图像，而是：

- 待检查记录与评分行容易错位；
- 不同任务重复维护名单副本；
- 查看器命令和项目路径依赖个人环境；
- 模块和质控员身份容易在汇总时丢失；
- 当前结果与原始评分事实之间缺少可重建关系；
- 任意脚本虽灵活，却把安全和可维护性成本交给普通用户。

EasyQC 的贡献应围绕“可配置工作流编排与可靠本地记录”展开，而不是宣称发明新的影像质量指标。

## 4. 可作为论文贡献的设计点

### 4.1 通用 QC record 抽象

总名单一行不被硬编码为 participant，可表示 scan、visit、processing output、image、folder 或其他检查单位。`easyqcid` 提供稳定、可移植、大小写安全的身份。

### 4.2 单一总名单与模块独立队列

每个模块保存结构化筛选规则，而不是维护独立权威 CSV。多个任务共享一套身份和行变量，同时保持队列、rater、评分结构和查看器命令独立。

### 4.3 模块化的选择—显示—判断—责任人组合

一个 module 统一：record selection、viewer invocation、rating schema 和 rater ownership。这是项目适配的主要扩展单元。

### 4.4 复用专业查看器

EasyQC 不实现 NIfTI/MRI rendering，而是把 Freeview、FSLeyes、MRIcroGL、wb_view、ITK-SNAP 等工具作为外部进程。贡献是稳定传递上下文和记录判断，不是替代专业交互显示。

### 4.5 受限 Formula

EasyQC Formula 用封闭单表达式模型覆盖数值、文本、路径、条件和缺失值变换。与 SQL/Python/full VBA 相比，它降低普通表格转换的学习和任意代码执行风险，同时保留多列与固定值能力。

### 4.6 自解释当前评分快照

每个 rating JSON 保存完整模块快照。目录、文件名与正文冗余声明 `(module_name, rater, easyqcid)`，使混放、误移和身份错配可以被检测。

### 4.7 评分事实与结果投影分离

评分 JSON 是当前事实；宽结果由严格扫描、展平、透视和对总名单左连接得到。名单清理不静默删除评分，结果损坏可从事实重建。

### 4.8 copy-only 模板

安装级常量/模块模板只在显式复制时进入项目，之后项目独立。它实现跨项目复用，又避免隐式继承和版本间状态污染。

### 4.9 fail-loud 本地可靠性

原子写入、CAS 修订、项目级锁、严格 schema/身份扫描和过期后台任务拒绝，共同避免静默部分成功。

## 5. Methods 可使用的当前事实

| 主题 | 当前事实 |
|---|---|
| GUI | PySide6 / Qt Widgets；唯一 GUI |
| Python | 3.10+；当前源码目录 + `.venv` 运行 |
| 持久化 | 本地 JSON/CSV；无数据库 |
| 项目设置/评分 schema | `schema_version: 3` only；无运行时旧格式兼容 |
| 主名单 | `Table/easyqc_all.csv`；`easyqcid` 唯一且 casefold 唯一 |
| 模块队列 | 总名单 + 每模块独立结构化筛选 |
| 执行上下文 | 当前行普通列 + 项目常量 |
| 模块 | rater、scores、Boolean tags、notes、viewer command、queue filter |
| Formula | Lark 解析的封闭 Excel/VBA-style 单表达式；23 函数；不使用 eval/exec |
| 查看器执行 | `CodeExecutor`；默认 `shell=False`；用户可显式 `shell=True`；无命令名黑白名单 |
| 评分身份 | 一份当前快照 / `(module_name, rater, easyqcid)` |
| 评分路径 | `RatingFiles/<module>/<rater>/<module>-<rater>-<easyqcid>.json` |
| 结果 | rating JSON → validate → flatten → wide pivot → left join master list |
| 写入 | 同目录临时文件、flush/fsync、`os.replace`；表格含 SHA-256 CAS |
| 并发 | 进程内锁 + 本机非阻塞文件锁；不是分布式事务 |
| i18n | 运行时中/英文切换；用户业务文本不翻译 |

## 6. Claim—evidence—limitation 矩阵

| 可写 claim | 证据类型与位置 | 必须同时写出的限制 |
|---|---|---|
| EasyQC supports configurable module-specific QC queues over one master list | Core + tests: `core/configuration_service.py`, `core/project_context_service.py`, `tests/test_core/test_qc_workflow_service.py` | 不代表跨机器协同队列 |
| EasyQC Formula avoids arbitrary code execution | parser/engine + tests: `core/formula_parser.py`, `core/formula_engine.py`, `tests/test_core/test_formula_parser.py`, `tests/test_core/test_formula_functions.py` | viewer command 是独立的真实进程执行边界 |
| Ratings are isolated by module, rater and record | identity/service + tests: `core/rating_identity.py`, `core/rating_service.py`, `tests/test_core/test_rating_identity.py`, `tests/test_core/test_rating_service_v3_safety.py` | 同一三元组只保留最新快照，不是事件日志 |
| Result tables are rebuildable from rating facts | `core/rating_service.py`, `tests/test_core/test_rating_service.py` | 左连接当前总名单；已删除名单行不会出现在当前投影 |
| Local writes resist partial publication and stale overwrite | `utils/file_utils.py`, `core/table_service.py`, rating lock tests | 不是网络文件系统/数据库的分布式事务 |
| The UI uses one cross-platform Qt implementation | `gui_qt/`, `.github/workflows/qt-platform.yml`, Qt tests | offscreen CI 不等于 Windows/macOS 原生人工验证 |
| 100k-scale synthetic workloads met declared gates | archived benchmark evidence，见第 7 节 | 不能外推到任意数据/磁盘/平台/viewer |

## 7. 可报告的性能数据

所有数字必须绑定证据目录和工作负载：

1. **评分聚合**：100,000 个真实 schema-v3 JSON，扫描、校验、展平、透视和合并为 30.628661 s，峰值 1555.043 MiB；门槛 60 s / 4096 MiB。证据：`dev/archive/easyqcid-schema-v3-audit-remediation/`。
2. **表格查询**：100,000×300 合成名单，query p95 119.919 ms，Qt application delay 89.883 ms，峰值 859.859 MiB。证据：`dev/archive/module-qc-filter-usability/`。
3. **Formula**：100,000×300 合成名单，0.895 s，峰值 698.375 MiB；门槛 10 s / 4096 MiB。证据：`dev/archive/easyqc-formula/`。

建议论文写法：

> In revision-bound synthetic benchmarks on the recorded Ubuntu environment, the tested workloads met their preregistered time and memory gates [...]. These measurements do not include external-viewer startup or native GUI rendering and are not assumed to transfer unchanged across storage devices or operating systems.

不要写“EasyQC can always process 100,000 records in under one second”。只有 Formula 的特定合成表达式接近该数量级；100,000 个评分文件聚合约 30.6 s。

## 8. 当前平台陈述边界

安全陈述：

- 单一 Qt codebase 目标覆盖 Ubuntu、Windows 和 macOS；
- hosted CI 包含 Ubuntu 22.04/24.04、Windows Server 2022 和 macOS 15 arm64 的 offscreen 行；
- 布局使用系统字体、DPI、size policy 和标准控件；
- Ubuntu 有当前最强本机证据。

不安全陈述：

- “Windows 11 与 macOS 已完成所有原生 UI 验收”；
- “所有神经影像查看器在三个系统上均已验证”；
- “不同平台 GUI 像素一致”；
- “虚拟机测试等同于真实 Mac arm64”。

## 9. 安全与权限的准确措辞

Formula 可称为 restricted expression language，不能称为通用脚本执行。viewer command 则是用户配置的外部进程：

- 默认 direct execution (`shell=False`)；
- 可显式 OS Shell execution (`shell=True`)；
- 不采用 executable allowlist/denylist；
- 使用当前 OS user 权限；
- EasyQC is a process controller, not a sandbox。

论文不能把 Formula 的安全边界套用到 viewer command，也不能声称 `shell=False` 使任意可执行程序安全。

## 10. 主要限制

- 只保存每个三元身份的最新评分，不内建不可变编辑历史；
- 无数据库、服务器、账户系统和多机协同事务；
- 不执行自动 QC 或 AI 判别；
- 不实现医学图像渲染，依赖外部查看器及其安装；
- 旧 `ezqc` / `ezqcid` 与 schema 0/1/2 不由当前运行时读取；
- Formula 不支持正则、循环、自定义函数或评分结果列输入；
- Excel 导入需要额外 pandas engine；
- Windows/macOS 原生 UI、辅助功能和真实 viewer 组合仍需扩大验证；
- 性能证据为合成、环境绑定，不代表临床规模普适结论。

## 11. 建议论文图表

### 11.1 主框架图

采用五层结构，而不是代码类图：

```text
Project management
        ↓
Master QC list + project constants
        ↓
Independent configurable QC modules
        ↓
Module queue → external viewer → human rating
        ↓
Validated rating snapshots → rebuildable results/export
```

横向强调灵活性：任意 QC record、多个 module/rater、多个 external viewers；纵向强调从配置到结果的可追踪关系。

### 11.2 数据与身份图

展示总名单行变量 + 常量如何进入 viewer command，以及 `(module, rater, easyqcid)` 如何同时映射目录、文件名和 JSON 正文。

### 11.3 可靠性图或表

列出 atomic publication、CAS、write lock、strict scan、rebuildable projection 及其保护的失败类型。

### 11.4 性能表

分开报告 list query、Formula 和 100k rating aggregation，不把三个工作负载混成一个“100k performance”结论。

## 12. 中英术语表

| 中文 | 推荐英文 | 避免 |
|---|---|---|
| 质控条目 | QC record / QC item | subject（除非真实语义确为受试者） |
| 质控总名单 | master QC list | subject list |
| 模块独立队列 | module-specific QC queue | copied master table |
| 项目常量 | project constant | global variable（易与安装全局混淆） |
| 行变量 | row variable | runtime code |
| 质控模块 | QC module | plugin（当前不是代码插件系统） |
| 质控员 | rater | user account（当前无账户系统） |
| 评分快照 | rating snapshot | immutable event |
| 结果投影 | result projection | source-of-truth result database |
| 外部查看器 | external domain viewer | built-in renderer |
| 受限表达式 | restricted expression language | VBA interpreter |
| 原子发布 | atomic file publication | full database transaction |
| 取消登记 | unregister project | delete project |

## 13. 代码和测试入口

| 主题 | 主要实现 | 主要测试 |
|---|---|---|
| 项目与 schema | `core/project_service.py`, `core/project_context_service.py` | `tests/test_core/test_project_service.py`, `tests/test_core/test_project_context_service.py` |
| 名单导入/删除/合并 | `core/configuration_service.py`, `core/table_service.py` | `tests/test_core/test_configuration_service.py`, `tests/test_core/test_qc_list_identity_validation.py` |
| 表格视图 | `core/table_view_service.py`, `gui_qt/table_workspace.py` | `tests/test_core/test_table_view_service.py`, `tests/test_gui_qt/test_qt_table_workspace.py` |
| Formula | `core/formula_parser.py`, `core/formula_engine.py`, `core/table_transform.py` | `tests/test_core/test_formula_parser.py`, `tests/test_core/test_formula_engine.py`, `tests/test_core/test_formula_functions.py`, `tests/test_gui_qt/test_formula_editor.py` |
| 模块/模板 | `core/module_repository.py`, `core/template_service.py`, `core/project_template_service.py` | `tests/test_core/test_module_identity_lifecycle.py`, `tests/test_gui_qt/test_cross_project_settings_page.py` |
| 查看器 | `core/code_executor.py` | `tests/test_core/test_code_executor.py` |
| QC 会话 | `core/qc_workflow_service.py`, `gui_qt/qc_workspace.py` | `tests/test_core/test_qc_workflow_service.py`, `tests/test_gui_qt/test_qt_qc_workspace.py` |
| 评分身份/聚合 | `core/rating_identity.py`, `core/rating_service.py` | `tests/test_core/test_rating_identity.py`, `tests/test_core/test_rating_service_v3_safety.py` |
| 结果/导出 | `gui_qt/qc_results_page.py`, `core/table_export_service.py` | `tests/test_gui_qt/test_qt_qc_results_page.py`, `tests/test_core/test_table_export_service.py` |
| 平台/i18n | `.github/workflows/qt-platform.yml`, `gui_qt/i18n.py` | `tests/test_scripts/test_ci_platform_workflow.py`, `tests/test_gui_qt/test_i18n.py` |

## 14. 写作 Agent 的最终检查

在生成论文文字前逐项确认：

- 使用 `easyqc` / `easyqcid` / schema v3；
- 没有恢复 tkinter 或 `--ui qt-preview`；
- 没有声称数据库、云端、AI QC 或内建 viewer；
- 把模板写成 explicit copy-only；
- 把历史记录写成 toggleable initial read-only，而非一律强制只读；
- 把同一三元组写成 latest snapshot overwrite；
- 给每个性能数字附工作负载、环境与限制；
- 区分 offscreen tests 与 native validation；
- Formula 与 viewer command 的安全边界没有混淆；
- 任何未来计划都标记 planned/unverified。

## 15. 相关文档

- [项目概览](01-project-overview.md)
- [核心逻辑与灵活性](02-core-logic-and-flexibility.md)
- [架构与数据流](03-architecture-and-data-flow.md)
- [可靠性、性能与平台](09-reliability-performance-and-platforms.md)
