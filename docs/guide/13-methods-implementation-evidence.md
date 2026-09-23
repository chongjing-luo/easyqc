# EasyQC 方法描述与实现证据

## 1. 用途与审计基线

本页把论文中需要确认的软件行为映射到当前实现与测试。核对日期为 2026-09-15，针对引言的软件定位及方法中的配置、执行、保存和结果流程；不代表全文学术审稿、外部文献核验或全部平台验收。

- 软件自报版本：`easyqc.py` 中的 `1.0.0`。
- Git HEAD：`a93629ad7798dcdc812f07fdd041be3d1b7b87d7`。
- 审计开始时工作区有 2,837 条 Git 状态记录，因此本页对应当前工作区，不能仅以该 HEAD 复现。
- 工作区证据根：`ProjectEASYQC/Tmp/subtask/easyqc-methods-audit/`，其中 `baseline_sha256.json` 保存审计前源码、测试和指南文件指纹，`guide-before/` 保存本次修改前的指南。

以下链接相对于软件仓库。函数名和测试名用于定位；后续实现改变时应重新核对，不能只更新日期。

## 2. 可以确定写入方法的事实

| 主题 | 当前行为 | 主要实现与验证 |
|---|---|---|
| 软件入口 | Qt 是唯一 GUI；普通启动和四参数 CLI 都进入 Qt | [easyqc.py](../../easyqc.py) 的 `main`；[入口测试](../../tests/test_scripts/test_qt_entry.py) |
| 条目与队列 | `easyqcid` 是项目内条目标识；模块筛选作用于当前总名单，无筛选时使用全表 | [ProjectContextService](../../core/project_context_service.py) 的 `_validate_subjects`、`resolve_module_queue`；[上下文测试](../../tests/test_core/test_project_context_service.py) |
| 多任务模块 | 多个评分项与布尔标签可表达多项相关检查；模块只有一个 `notes` | [QCModule/Score/Tag](../../models/qcmodule.py)；[模型测试](../../tests/test_models/test_qcmodule_models.py) |
| 变量与常量 | 命令使用当前行字段及项目常量；重名拒绝，推荐 `${name}` 占位符 | [QcWorkflowService](../../core/qc_workflow_service.py) 的 `viewer_plan`；[工作流测试](../../tests/test_core/test_qc_workflow_service.py) |
| 多程序启动 | `MULTICMD … ;| …` 生成多个启动项；按顺序启动，可共存；直接模式默认，Shell 显式选择 | [CodeExecutor](../../core/code_executor.py) 的 `render_command_plan`、`start_commands`；[执行器测试](../../tests/test_core/test_code_executor.py) |
| 配置复用 | 模块 JSON 可导出/导入；模板显式复制为项目独立配置 | [ConfigurationService](../../core/configuration_service.py) 的 `import_module_file`、`export_module`；[模板服务](../../core/project_template_service.py)；[跨项目界面测试](../../tests/test_gui_qt/test_cross_project_settings_page.py) |
| 完整模块快照 | 评分保存要求模块快照，并写入 `schema_version: 3`；保存相同三元身份覆盖当前记录 | [RatingService](../../core/rating_service.py) 的 `_save_rating_to_path`；[Rating](../../models/rating.py)；[工作流测试](../../tests/test_core/test_qc_workflow_service.py) 的 `test_save_preserves_full_module_payload_and_schema_version` |
| 复查 | 使用保存的模块结构和筛选；筛选应用于当前总名单，调用使用当前常量；初始界面只读可解除，Core 约束仍有效 | [ProjectContextService](../../core/project_context_service.py) 的 `create_qc_record_workflow`；[上下文测试](../../tests/test_core/test_project_context_service.py) 的 `test_historical_record_workflow_uses_saved_schema_complete_queue_and_ratings` |
| 长表与宽表 | 用户长表是评分事实与当前总名单内连接；宽表以总名单左连接；内部原始展平表另有范围和字段 | [RatingService](../../core/rating_service.py) 的 `professional_long_results`、`attach_master_columns_to_long`、`merge_subjects_with_rating_wide`；[评分测试](../../tests/test_core/test_rating_service.py) |
| 查看与导出 | 结果页可切换两种模式，各自保留视图状态；导出当前模式全部匹配行及可见列 | [QtQcResultsPage](../../gui_qt/qc_results_page.py) 的 `toggle_result_mode`；[TableExportService](../../core/table_export_service.py)；[结果页测试](../../tests/test_gui_qt/test_qt_qc_results_page.py) 的 `test_results_page_export_uses_only_the_visible_long_mode` |
| 原子写入 | JSON 写入同目录临时文件，flush/fsync 后以 `os.replace` 替换目标 | [FileUtils](../../utils/file_utils.py) 的 `atomic_write`、`safe_json_save`；[评分安全测试](../../tests/test_core/test_rating_service_v3_safety.py) |

## 3. 需要同时说明的边界

1. **模块快照不等于完整研究环境。** 它包含评分定义、标签、备注、命令模板、展开命令及模块筛选等，不包含当时整份总名单、项目常量表、应用级 Shell 选择、查看器版本、脚本文件内容或窗口交互状态。即使 JSON 中记录了展开命令，也不能据此确认所有外部程序成功显示了数据。
2. **同一模块的多项检查共用记录结构。** 没有独立子任务队列、子任务身份或每项独立备注。评分项名称应表达具体检查内容，不能把配置能力扩大成任意表单或插件能力。
3. **复查依赖当前项目。** 已移出总名单的评分仍可留在磁盘；它不会出现在当前两种结果表，也不能直接通过当前行入口打开。保存的筛选失效或不再匹配该条目时，复查会报错。
4. **结果是派生视图。** 当前 Qt 主路径在内存中构建长表和宽表；需要 CSV 时由用户导出。长表不为没有评分文件的条目制造空白评分行。常规结果 CSV 不包含完整评分定义；相同 `score1` 是否可比较需结合模块快照或研究说明。
5. **配置复制不复制依赖。** 总名单、项目常量、查看器、外部脚本与应用级 Shell 选择需要在目标项目/环境核对。仅有模块 JSON 不足以重现任务。
6. **代码能力不等于效果证据。** 当前实现支持调用外部查看器，不足以证明改善评分一致性、提高准确率或优于已有软件。原生平台、具体查看器组合和用户任务仍需各自验证。

## 4. 本次验证

在源码 `.venv` 的 Python 3.10.17 下运行既有测试，使用合成夹具与临时目录；Qt 采用 `offscreen`。`tests/conftest.py` 拦截真实项目路径的读取和修改。

| 批次 | 结果 | 日志（相对 ProjectEASYQC 工作区） |
|---|---|---|
| 模型、评分、QC 工作流、项目上下文、命令、模板、导出、Qt 入口 | 212 passed，7.98 s | `Tmp/hooks/easyqc_methods_audit_core_20260915.log` |
| Qt 结果页、QC 窗口、行右键、跨项目设置 | 44 passed，4.26 s | `Tmp/hooks/easyqc_methods_audit_qt_20260915.log` |

合计 256 项为本次定向验证数量，不是当前软件全量测试总数；未重测性能、原生 Windows/macOS 或真实医学影像查看器。

复跑命令（在 `easyqc/` 目录下执行）：

```bash
.venv/bin/python -m pytest -q tests/test_models/test_qcmodule_models.py tests/test_models/test_rating_models.py tests/test_core/test_rating_service.py tests/test_core/test_rating_service_v3.py tests/test_core/test_rating_service_v3_safety.py tests/test_core/test_qc_workflow_service.py tests/test_core/test_project_context_service.py tests/test_core/test_code_executor.py tests/test_core/test_template_service.py tests/test_core/test_table_export_service.py tests/test_scripts/test_qt_entry.py
.venv/bin/python -m pytest -q tests/test_gui_qt/test_qt_qc_results_page.py tests/test_gui_qt/test_qt_qc_workspace.py tests/test_gui_qt/test_qc_row_context_menu.py tests/test_gui_qt/test_cross_project_settings_page.py
```

## 5. 写作时的使用方式

正文可直接陈述上述已确认行为；详细文件名、数据结构和命令示例适合放入补充材料。投稿版本应绑定发布版本、提交号或可下载的源码快照，并提供一份匿名/合成名单、常量、模块及查看器说明，形成读者能复跑的最小任务。

- [核心逻辑与灵活性](02-core-logic-and-flexibility.md)
- [常量、模块与外部查看器](07-constants-modules-and-viewers.md)
- [评分、复查与结果](08-qc-rating-review-and-results.md)
- [论文写作事实表](11-paper-agent-fact-sheet.md)
