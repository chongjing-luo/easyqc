# EasyQC 文档中心

EasyQC 是一个离线、可配置的人工视觉质量控制（manual visual quality control）工作台：它把质控名单、项目变量、可复用检查模块、外部专业查看器、人工评分和结果聚合连接成一条可追踪的本地工作流。

本目录描述当前 Qt-only、schema-v3 产品。它既是用户手册，也是维护者和论文写作 Agent 的事实入口；它不是论文稿，也不会把尚未完成的平台验证写成产品结论。

## 从哪里开始

| 读者 | 建议阅读路径 |
|---|---|
| 第一次使用 | [项目概览](guide/01-project-overview.md) → [安装与项目管理](guide/04-installation-and-project-management.md) → [名单与表格](guide/05-qc-list-and-table-workspace.md) → [模块与查看器](guide/07-constants-modules-and-viewers.md) → [评分与结果](guide/08-qc-rating-review-and-results.md) |
| 配置复杂工作流 | [核心逻辑与灵活性](guide/02-core-logic-and-flexibility.md) → [新增列与 EasyQC Formula](guide/06-derived-columns-and-easyqc-formula.md) → [模块与查看器](guide/07-constants-modules-and-viewers.md) |
| 维护或审计代码 | [架构与数据流](guide/03-architecture-and-data-flow.md) → [可靠性、性能与平台](guide/09-reliability-performance-and-platforms.md) → [架构决策](architecture-decisions.md) |
| 准备论文 | [项目概览](guide/01-project-overview.md) → [核心逻辑与灵活性](guide/02-core-logic-and-flexibility.md) → [论文 Agent 事实表](guide/11-paper-agent-fact-sheet.md) |
| 遇到错误 | [参考与故障排查](guide/10-reference-and-troubleshooting.md) |

## 十分钟理解 EasyQC

EasyQC 的最小模型由五类对象组成：

```text
项目 Project
├── 质控总名单 Master QC list
│   └── 每行是一个通用质控条目，以 easyqcid 唯一标识
├── 项目常量 Project constants
├── 质控模块 QC modules
│   ├── 自己的名单筛选规则
│   ├── 自己的质控员、评分项、标签和备注
│   └── 自己的外部查看器命令模板
├── 当前评分快照 Rating snapshots
│   └── 每个 (module_name, rater, easyqcid) 最多一个当前 JSON
└── 可重建结果 Derived results
    └── 评分 JSON + 质控总名单 → 宽格式结果表
```

最重要的理解是：一行不必代表一个“受试者”。它可以代表一次扫描、一个访视、一个处理产物、一幅图像、一批实验输出，或任何需要人工检查的单位。`easyqcid` 标识的是质控条目，而不是强制的数据学实体类型。

一个项目只有一份总名单，但每个模块可以保存独立筛选规则。因此，同一份数据可以形成“结构像检查”“功能像检查”“分割结果检查”等不同队列；不设置模块筛选时，该模块使用全部名单。打开 QC 时，当前行的列值与项目常量合并为查看器命令上下文。

## 完整使用流程

1. 在“项目选择”创建项目，或登记一个现有 schema-v3 项目。
2. 在“质控名单导入”读取目录、文件或直接输入内容，在独立草稿中清理、派生列并预览冲突。
3. 把草稿按“替换”“追加行”或“按 `easyqcid` 合并列”的明确策略写入质控总名单。
4. 在“常量设置”保存不随行变化的路径或参数；常量名不得与名单列名重叠。
5. 在“质控模块”配置模块标识、质控员、评分项、标签、名单筛选和查看器命令；也可从安装级模板复制一个可编辑副本。
6. 从模块启动质控，或在名单/结果/QC 队列表格中右键选择适用模块。
7. 双击 QC 队列中的条目可切换记录并启动查看器；在 EasyQC 中填写评分、标签和备注。
8. 保存时写入该模块、质控员和条目的完整当前快照；再次编辑同一身份时原子覆盖。
9. 在“质控结果”刷新，从评分 JSON 重建结果投影，再筛选、排序、控制列和导出 CSV。

## 七个导航页

| 页面 | 主要职责 |
|---|---|
| 跨项目设置 | 管理本次 EasyQC 安装中的常量模板、模块模板和查看器命令执行方式 |
| 项目选择 | 创建、导入、切换和移除项目登记 |
| 质控名单导入 | 构建独立导入草稿，预览后显式写入总名单 |
| 质控前名单 | 查看和整理总名单；筛选/排序只是视图，删除和新增列是显式数据变更 |
| 常量设置 | 管理项目内、对所有行共享的变量 |
| 质控模块 | 管理检查任务、独立队列、质控员、评分结构和查看器命令 |
| 质控结果 | 从当前评分事实得到宽表视图，并刷新、筛选和导出 |

左侧导航可收起。底部语言按钮只显示目标语言：中文界面显示 `English`，英文界面显示“中文”；切换不会重建当前业务状态。

## 文档地图

| 文档 | 唯一负责的主题 |
|---|---|
| [01 项目概览](guide/01-project-overview.md) | 问题、定位、用户、适用范围与非目标 |
| [02 核心逻辑与灵活性](guide/02-core-logic-and-flexibility.md) | 产品心智模型、可配置性和关键设计巧思 |
| [03 架构与数据流](guide/03-architecture-and-data-flow.md) | 分层、服务图、权威数据、启动与写回边界 |
| [04 安装与项目管理](guide/04-installation-and-project-management.md) | 源码安装、启动、项目生命周期、目录结构和 schema 边界 |
| [05 名单与表格工作区](guide/05-qc-list-and-table-workspace.md) | 导入、合并、筛选、排序、列、分页、删除和右键菜单 |
| [06 新增列与 Formula](guide/06-derived-columns-and-easyqc-formula.md) | 安全公式语法、函数目录、示例、预览和失败行为 |
| [07 常量、模块与查看器](guide/07-constants-modules-and-viewers.md) | 上下文合成、模块配置、模板复制和外部进程控制 |
| [08 评分、复查与结果](guide/08-qc-rating-review-and-results.md) | QC 操作、只读/编辑、快照覆盖、历史记录和结果重建 |
| [09 可靠性、性能与平台](guide/09-reliability-performance-and-platforms.md) | 原子写入、并发、基准、Qt 跨平台策略和证据边界 |
| [10 参考与故障排查](guide/10-reference-and-troubleshooting.md) | 标识符、文件格式、常见错误、日志、备份和恢复 |
| [11 论文 Agent 事实表](guide/11-paper-agent-fact-sheet.md) | 可安全引用的贡献、方法事实、证据、限制和术语 |
| [架构决策](architecture-decisions.md) | 已接受的长期工程取舍 |

## 事实状态怎么读

本套文档使用四种状态，不能互相替代：

- **已实现（implemented）**：当前代码存在该行为。
- **已测试（tested）**：当前自动化测试覆盖了相应合同；不等于真实用户或真实查看器验证。
- **已演示/已测量（demonstrated/measured）**：在明确环境和数据上获得了保留证据；不能无条件外推。
- **计划中（planned）**：已有方向或基础设施，但不能描述成当前可交付能力。

当前产品只接受 `easyqc` / `easyqcid` 和 `schema_version: 3` 的项目设置及评分记录。旧 `ezqc` / `ezqcid`、旧评分命名和 schema 0/1/2 不在运行时兼容范围内。

## 当前边界

- 唯一 GUI：PySide6 / Qt Widgets。
- 本地持久化：JSON 和 CSV；无数据库、云端、遥测或网络 API。
- 人工质控：EasyQC 不做自动/AI 评分，也不替代 MRIQC。
- 图像显示：由 Freeview、FSLeyes、MRIcroGL、wb_view、ITK-SNAP 等外部查看器完成。
- 查看器命令：默认 `shell=False`，用户可显式切换为 `shell=True`；无命令名称黑白名单。EasyQC 是进程控制器，不是安全沙箱。
- 常规容量目标：约不超过 100,000 行、约 300 列，建议 16 GiB 内存；详见[性能证据边界](guide/09-reliability-performance-and-platforms.md)。
- 平台：Qt 和自动化矩阵面向 Ubuntu、Windows、macOS；当前原生人工 UI 与真实查看器的完整证据仍以 Ubuntu 为主，其他平台不得写成已完成验证。

## 名称约定

| 中文 | 推荐英文 | 含义 |
|---|---|---|
| 质控条目 | QC record / QC item | 总名单的一行，不预设是受试者 |
| 质控总名单 | master QC list | 项目唯一的权威名单 `easyqc_all.csv` |
| 模块名单/队列 | module-specific QC queue | 一个模块筛选总名单得到的有序条目集合 |
| 项目常量 | project constant | 对项目所有行共享的命令变量 |
| 行变量 | row variable | 当前名单行的一列值 |
| 评分快照 | rating snapshot | 一个三元身份的完整当前评分 JSON |
| 结果投影 | result projection | 从总名单和评分事实重建的表格视图 |

维护者应以当前代码和测试为第一事实来源，以工作区 `docs/PROJECT_SPEC.md` 为长期产品范围；`docs/superpowers/` 是设计历史，不是用户行为的最高权威。
