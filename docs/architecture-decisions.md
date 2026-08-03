# EasyQC 架构决策摘要

> 本文件是当前产品决策的入口摘要。历史讨论保留在工作区
> `dev/project_improvement/`；仍具规范效力的独立 ADR 保留在工作区
> `docs/decisions/`。当前代码和测试优先于历史计划文本。

---

## 当前结论

| ADR | 决策 | 当前状态 | 结果 |
|-----|------|----------|------|
| 001 | GUI 框架 | 已采纳（已完成） | PySide6/Qt Widgets 是唯一 GUI；旧 GUI 由 Git 标签保留，不在产品树保留回滚代码 |
| 002 | 状态管理 | 已采纳 | `core/` services + `models/` + Qt presentation；无 GUI 状态兼容适配器 |
| 003 | 用户代码执行 | 已被 ADR-013 取代 | 原 basename allowlist 与强制 `shell=False` 不再是当前产品边界 |
| 004 | 存储后端 | 已采纳 | 继续 CSV/JSON，不引入数据库 |
| 005 | 源码布局 | 已采纳 | `easyqc_back/` 为只读参照，`easyqc/` 为主线 |
| 006 | 表格处理 | 已采纳（已演进） | 用户使用类型感知 Filter/Sort/列工具和受限 EasyQC Formula；Core 使用结构化 JSON operations，不提供 SQL 或任意代码执行界面 |
| 007 | 冻结包定位 | 已采纳 | Ubuntu PyInstaller 证据保留为未来可选路线，不再是主发布路线 |
| 008 | 托管运行时方向 | 已采纳（发布未完成） | 托管安装器与精确锁基础设施已实现，但当前可交付使用路径仍是源码目录 + Python 3.10+ 的项目内 `.venv`；不得描述为已发布跨平台安装器 |
| 009 | Qt 显示策略 | 已采纳 | 使用标准 Qt 控件、系统字体/调色板和布局；不强制 Fusion、全局 QSS 或像素一致 |
| 010 | 表格承载基线 | 已采纳 | 16GB，通常 ≤100,000×约 300；pandas 优先，只有基准失败才考虑 Polars/Parquet |
| 011 | 平台验证 | 已采纳（证据积累中） | 四行 offscreen CI + Ubuntu 本机证据；Windows 11 与 macOS 13+ arm64 的原生 UI、辅助功能和真实查看器仍需验收 |
| 013 | 查看器命令执行 | 已采纳 | 不设命令名称黑白名单；默认 `shell=False`，用户可在导航设置中持久选择 `shell=True` |
| 014 | 安装级模板 | 已采纳 | 常量和模块模板跟随当前 EasyQC 安装；只在显式复制时进入项目，项目副本可编辑且不与模板同步 |

---

## 关键约束

- PySide6/Qt Widgets 是唯一 GUI，默认入口和 CLI 直达 QC 都使用同一套 Qt
  presentation + Core services。
- 日常入口是 `easyqc/`，不是 `easyqc_back/`。
- `easyqc_back/` 只用于只读历史对照，不参与产品运行或测试依赖。
- 项目数据继续使用 CSV/JSON 文件，保持人类可读和易备份。
- 表格处理不恢复 SQL 执行引擎；内部使用类型化操作，用户通过 Filter/Sort/
  列显示/Formula 标准控件操作，不编辑 JSON。
- 当前发布使用项目内 `.venv`，从仓库根运行 `python easyqc.py`；Docker 暂不
  使用，PyInstaller 只保留为可选历史路线，托管运行时不能在发布矩阵完成前
  写成当前安装事实。
- Qt 使用系统样式、字体、调色板、布局和尺寸策略；不追求跨平台像素一致，
  但必须保证关键控件不重叠、不消失且键盘可达。
- 16GB 是目标工作站基线；通常不超过 100,000 行 × 约 300 列。
- 外部查看器命令统一通过 `CodeExecutor` 处理；不设命令名称黑白名单，
  默认直接执行，用户可在导航设置中明确启用系统 Shell。
- 安装级常量/模块模板是 copy-only 起点，不自动进入、继承或同步到项目。
- 当前项目设置和评分只读取 `schema_version: 3` 以及 `easyqc`/`easyqcid`
  命名；运行时不兼容旧 `ezqc`/`ezqcid` 数据。
- 每个 `(module_name, rater, easyqcid)` 只保留一个最新评分快照；评分事实与
  可重建结果投影分离。
- 自动化测试是提交前基本门槛，真实 GUI 点击仍需人工 smoke test。

---

## 当前仍需补充的跨平台证据

- Qt 的 Table、QC、配置、后台任务和验证工具已经实现；托管运行时基础设施
  不是已经发布的跨平台产品。
- 100,000×300 混合类型基准已通过本机阈值，但该结果不代表 Windows/macOS
  原生性能。
- Ubuntu 图形桌面、Windows 11 和 macOS 13+ arm64 的真实查看器/人工 UI
  验证仍需随发布环境补充；这不改变 Qt-only 产品边界。

---

## 相关文档

- 产品与工程范围：工作区 `docs/PROJECT_SPEC.md`
- 当前产品架构索引：工作区 `dev/easyqc/PROJECT_INDEX.md`
- ADR-013：工作区 `docs/decisions/ADR-013-user-selectable-viewer-shell-execution.md`
- ADR-014：工作区 `docs/decisions/ADR-014-installation-scoped-copy-only-templates.md`
- 用户迁移指南：`MIGRATION.md`
- 旧重构计划：工作区 `dev/project_improvement/`（仅历史参考）
