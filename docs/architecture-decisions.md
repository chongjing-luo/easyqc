# EasyQC 架构决策摘要

> 本文件归档已经稳定的重构决策。完整讨论记录保留在 `Tmp/project_improvement/04-decisions/`。

---

## 当前结论

| ADR | 决策 | 当前状态 | 结果 |
|-----|------|----------|------|
| 001 | GUI 框架 | 已采纳（已更新） | PySide6/Qt Widgets 为目标；tkinter 在发布门禁完成前保持默认和回滚能力 |
| 002 | 状态管理 | 部分采纳，迁移中 | 引入 `core/` service、`models/` 和 `LegacyGUIStateAdapter`，仍保留少量 legacy fallback |
| 003 | 用户代码执行 | 已采纳 | 使用受控 `CodeExecutor`，避免恢复任意 `shell=True` 路径 |
| 004 | 存储后端 | 已采纳 | 继续 CSV/JSON，不引入数据库 |
| 005 | 源码布局 | 已采纳 | `easyqc_back/` 为只读参照，`easyqc/` 为主线 |
| 006 | 表格处理 | 已采纳 | 使用 `TableTransformEngine`，只保留简单旧 `SELECT * FROM df WHERE ...` 兼容转换 |
| 007 | 冻结包定位 | 已采纳 | Ubuntu PyInstaller 证据保留为未来可选路线，不再是主发布路线 |
| 008 | 主分发方式 | 已采纳 | `uv 0.11.29` + 私有 CPython 3.13.13；精确载荷、用户/系统作用域、并排更新和回滚 |
| 009 | Qt 显示策略 | 已采纳 | 使用标准 Qt 控件、系统字体/调色板和布局；不强制 Fusion、全局 QSS 或像素一致 |
| 010 | 表格承载基线 | 已采纳 | 16GB，通常 ≤100,000×约 300；pandas 优先，只有基准失败才考虑 Polars/Parquet |
| 011 | 平台验证 | 已采纳 | 四行固定 CI + Ubuntu 主机 + Windows 11 VM + 真实/远程 macOS 13+ arm64 |

---

## 关键约束

- Qt 是目标 GUI；在真实 CI、原生安装/UI 和明确切换审批完成前，tkinter
  继续作为默认入口和回滚适配器。
- 日常入口是 `easyqc/`，不是 `easyqc_back/`。
- `easyqc_back/` 只用于查旧逻辑、构建 characterization tests 和对比 legacy 输出。
- 项目数据继续使用 CSV/JSON 文件，保持人类可读和易备份。
- 表格处理不恢复 SQL 执行引擎；内部使用类型化操作，用户通过 Filter/Sort/
  Columns 标准控件操作，不编辑 JSON。
- 正式分发使用受控私有 Python 环境；Docker 暂不使用，PyInstaller 只保留为
  可选历史路线。
- Qt 使用系统样式、字体、调色板、布局和尺寸策略；不追求跨平台像素一致，
  但必须保证关键控件不重叠、不消失且键盘可达。
- 16GB 是目标工作站基线；通常不超过 100,000 行 × 约 300 列。
- 外部查看器命令通过受控执行器处理；兼容旧模板时也不能重新打开任意 shell 执行。
- 自动化测试是提交前基本门槛，真实 GUI 点击仍需人工 smoke test。

---

## 当前仍未完全收敛的边界

- Qt 的 Table、QC、配置、后台任务、托管运行时和验证工具已经实现；完整回归
  当前为 1,020 passed、4 个声明的受保护 fixture skip。
- 100,000×300 混合类型基准已通过本机阈值，但该结果不代表 Windows/macOS
  原生性能。
- 四个 GitHub-hosted 任务尚未真实运行；Ubuntu 图形桌面、Windows 11 VM 和
  macOS 13+ arm64 原生 UI/安装证据仍待补充。
- Qt 默认切换尚未批准；tkinter 删除尚未开始。

---

## 相关文档

- 产品与工程范围：工作区 `docs/PROJECT_SPEC.md`
- 当前 Qt 迁移索引：工作区 `dev/qt-migration/PROJECT_INDEX.md`
- 用户迁移指南：`MIGRATION.md`
- 旧重构计划：工作区 `dev/project_improvement/`（仅历史参考）
