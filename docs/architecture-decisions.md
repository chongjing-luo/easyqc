# EasyQC 架构决策摘要

> 本文件归档已经稳定的重构决策。完整讨论记录保留在 `Tmp/project_improvement/04-decisions/`。

---

## 当前结论

| ADR | 决策 | 当前状态 | 结果 |
|-----|------|----------|------|
| 001 | GUI 框架 | 已采纳（已完成） | PySide6/Qt Widgets 是唯一 GUI；旧 GUI 由 Git 标签保留，不在产品树保留回滚代码 |
| 002 | 状态管理 | 已采纳 | `core/` services + `models/` + Qt presentation；无 GUI 状态兼容适配器 |
| 003 | 用户代码执行 | 已被 ADR-013 取代 | 原 basename allowlist 与强制 `shell=False` 不再是当前产品边界 |
| 004 | 存储后端 | 已采纳 | 继续 CSV/JSON，不引入数据库 |
| 005 | 源码布局 | 已采纳 | `easyqc_back/` 为只读参照，`easyqc/` 为主线 |
| 006 | 表格处理 | 已采纳 | 使用 `TableTransformEngine`，只保留简单旧 `SELECT * FROM df WHERE ...` 兼容转换 |
| 007 | 冻结包定位 | 已采纳 | Ubuntu PyInstaller 证据保留为未来可选路线，不再是主发布路线 |
| 008 | 主分发方式 | 已采纳 | `uv 0.11.29` + 私有 CPython 3.13.13；精确载荷、用户/系统作用域、并排更新和回滚 |
| 009 | Qt 显示策略 | 已采纳 | 使用标准 Qt 控件、系统字体/调色板和布局；不强制 Fusion、全局 QSS 或像素一致 |
| 010 | 表格承载基线 | 已采纳 | 16GB，通常 ≤100,000×约 300；pandas 优先，只有基准失败才考虑 Polars/Parquet |
| 011 | 平台验证 | 已采纳 | 四行固定 CI + Ubuntu 主机 + Windows 11 VM + 真实/远程 macOS 13+ arm64 |
| 013 | 查看器命令执行 | 已采纳 | 不设命令名称黑白名单；默认 `shell=False`，用户可在导航设置中持久选择 `shell=True` |

---

## 关键约束

- PySide6/Qt Widgets 是唯一 GUI，默认入口和 CLI 直达 QC 都使用同一套 Qt
  presentation + Core services。
- 日常入口是 `easyqc/`，不是 `easyqc_back/`。
- `easyqc_back/` 只用于只读历史对照，不参与产品运行或测试依赖。
- 项目数据继续使用 CSV/JSON 文件，保持人类可读和易备份。
- 表格处理不恢复 SQL 执行引擎；内部使用类型化操作，用户通过 Filter/Sort/
  Columns 标准控件操作，不编辑 JSON。
- 正式分发使用受控私有 Python 环境；Docker 暂不使用，PyInstaller 只保留为
  可选历史路线。
- Qt 使用系统样式、字体、调色板、布局和尺寸策略；不追求跨平台像素一致，
  但必须保证关键控件不重叠、不消失且键盘可达。
- 16GB 是目标工作站基线；通常不超过 100,000 行 × 约 300 列。
- 外部查看器命令统一通过 `CodeExecutor` 处理；不设命令名称黑白名单，
  默认直接执行，用户可在导航设置中明确启用系统 Shell。
- 自动化测试是提交前基本门槛，真实 GUI 点击仍需人工 smoke test。

---

## 当前仍需补充的跨平台证据

- Qt 的 Table、QC、配置、后台任务、托管运行时和验证工具已经实现。
- 100,000×300 混合类型基准已通过本机阈值，但该结果不代表 Windows/macOS
  原生性能。
- Ubuntu 图形桌面、Windows 11 和 macOS 13+ arm64 的真实查看器/人工 UI
  验证仍需随发布环境补充；这不改变 Qt-only 产品边界。

---

## 相关文档

- 产品与工程范围：工作区 `docs/PROJECT_SPEC.md`
- 当前 Qt 迁移索引：工作区 `dev/qt-migration/PROJECT_INDEX.md`
- 用户迁移指南：`MIGRATION.md`
- 旧重构计划：工作区 `dev/project_improvement/`（仅历史参考）
