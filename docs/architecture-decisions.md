# EasyQC 架构决策摘要

> 本文件归档已经稳定的重构决策。完整讨论记录保留在 `Tmp/project_improvement/04-decisions/`。

---

## 当前结论

| ADR | 决策 | 当前状态 | 结果 |
|-----|------|----------|------|
| 001 | GUI 框架 | 已采纳 | 继续使用 tkinter |
| 002 | 状态管理 | 部分采纳，迁移中 | 引入 `core/` service、`models/` 和 `LegacyGUIStateAdapter`，仍保留少量 legacy fallback |
| 003 | 用户代码执行 | 已采纳 | 使用受控 `CodeExecutor`，避免恢复任意 `shell=True` 路径 |
| 004 | 存储后端 | 已采纳 | 继续 CSV/JSON，不引入数据库 |
| 005 | 源码布局 | 已采纳 | `easyqc_back/` 为只读参照，`easyqc/` 为主线 |
| 006 | 表格处理 | 已采纳 | 使用 `TableTransformEngine`，只保留简单旧 `SELECT * FROM df WHERE ...` 兼容转换 |

---

## 关键约束

- GUI 仍使用 tkinter，避免新增大型 GUI 依赖。
- 日常入口是 `easyqc/`，不是 `easyqc_back/`。
- `easyqc_back/` 只用于查旧逻辑、构建 characterization tests 和对比 legacy 输出。
- 项目数据继续使用 CSV/JSON 文件，保持人类可读和易备份。
- 表格处理不恢复 SQL 执行引擎；新规则使用 JSON 结构化操作。
- 外部查看器命令通过受控执行器处理；兼容旧模板时也不能重新打开任意 shell 执行。
- 自动化测试是提交前基本门槛，真实 GUI 点击仍需人工 smoke test。

---

## 当前仍未完全收敛的边界

- GUI 层还有少量 legacy `ProjectManager` / `dt` fallback，用于兼容旧入口和测试。
- 旧版/新版输出逐项对比尚未全部完成。
- 真实 `easyqc_CCNPPEKI` qctable 快照重建、脱敏 fixture 快照和合成多模块/多评分者聚合已有测试；真实大表格 GUI 点击和真实项目 GUI 压力场景仍需补充。
- macOS / Windows 的真实 GUI 点击验证仍待补充。

---

## 相关文档

- 用户迁移指南：`easyqc/MIGRATION.md`
- 总体计划：`Tmp/project_improvement/README.md`
- Phase 5 切换计划：`Tmp/project_improvement/03-migration-plan/phase-5-switchover.md`
- Phase 7 GUI 兼容层清理：`Tmp/project_improvement/03-migration-plan/phase-7-gui-compatibility-cleanup.md`
