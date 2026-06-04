# EasyQC 迁移指南

> 适用范围：从重构前 EasyQC 迁移到当前 `easyqc/` 主线版本。`easyqc_back/` 只作为旧版参照，不作为日常运行入口。

---

## 1. 迁移原则

1. **继续使用原项目目录**：项目数据仍然是 `settings_{project}.json`、`Table/*.csv` 和 `RatingFiles/**/*.json`，不迁移到数据库。
2. **新版入口固定为 `easyqc/`**：日常启动请使用当前目录下的 `python easyqc.py` 或 `./start.sh`。
3. **旧版只读参照**：仓库根目录的 `easyqc_back/` 用于查旧逻辑、做特征测试和对比输出，不用于真实项目评分。
4. **先备份真实项目数据**：对重要项目，迁移前先复制整个 `easyqc_<PROJECT>/` 目录。
5. **用真实 GUI smoke test 验收**：自动测试不能替代人工点击主窗口、表格窗口、右键菜单和 QC 页面关闭流程。

---

## 2. 启动新版

推荐方式：

```bash
cd easyqc
source .venv/bin/activate
python easyqc.py
```

如果已经完成安装，也可以：

```bash
cd easyqc
./start.sh
```

CLI 直达 QC 页面：

```bash
cd easyqc
python easyqc.py <project> <module> <rater> <ezqcid>
```

示例：

```bash
python easyqc.py CCNPPEKI AnatRestAll rf CCNPPEK0001_01_rest01
```

---

## 3. 项目数据兼容性

当前版本保持旧项目的核心文件布局：

```text
easyqc_<PROJECT>/
├── settings_<PROJECT>.json
├── Table/
│   ├── ezqc_all.csv
│   ├── ezqc_qctable.csv
│   └── ezqc_qctable_filter.csv
└── RatingFiles/
    └── <module>/<rater>/*.json
```

已确认或已有测试覆盖的兼容面：

- 项目新建、导入、移除。
- 变量导入和合并，包括路径导入。
- 评分保存、旧评分文件清理和观察模式。
- 旧评分 JSON 的读取、验证和聚合。
- 真实 `easyqc_CCNPPEKI` 项目的 `ezqc_qctable.csv` 快照重建对比。
- 合成多模块、多评分者、多受试者评分聚合。
- CLI 参数解析和直达 QC 页面。
- Freeview、MRIcroGL、wb_view 等外部查看器命令。

仍需补充验证的场景：

- 真实大表格 GUI 点击和滚动。
- 真实项目中的多个 QC 模块、多评分者 GUI 压力场景。
- macOS / Windows 跨平台真实点击。
- 旧版/新版 `settings_*.json`、评分 JSON、`ezqc_qctable.csv` 的逐项输出对比。

---

## 4. 表格转换变化

新版不再依赖外部 SQL 查询引擎。主线表格处理改为 JSON 结构化操作，由 `TableTransformEngine` 执行。

支持的主要操作：

- 选列和重排列。
- 行筛选。
- 排序。
- 新增或更新变量列。
- 删除列和重命名列。
- 表格合并。
- 分组聚合。

兼容策略：

- 推荐新规则使用 JSON。
- 简单旧文本 `SELECT * FROM df WHERE ...` 会被窄范围转换为结构化筛选。
- 复杂 SQL 不兼容，包括 `JOIN`、`GROUP BY`、子查询、分号多语句、任意非 `SELECT * FROM df` 查询。
- 不恢复 SQL 执行引擎，也不重新引入相关依赖。

---

## 5. 迁移检查清单

迁移一个真实项目时，建议按此顺序检查：

1. 复制备份原项目目录，例如 `easyqc_CCNPPEKI_backup_YYYYMMDD/`。
2. 启动新版 GUI，确认主窗口可打开、可关闭。
3. 导入或切换到目标项目。
4. 打开 `ezqc_all` 和聚合表格，确认行列数量合理。
5. 新建一个临时变量，分别测试路径、文件或文本导入。
6. 打开一个已评分受试者，确认旧评分能恢复。
7. 修改一项评分或备注，关闭后重新打开，确认保存和旧文件清理正常。
8. 打开表格右键菜单和 QC 页面右键菜单。
9. 测试常用外部查看器命令。
10. 提取 QC 结果，检查 `ezqc_qctable.csv`。

详细人工测试表见仓库根目录的 `Manual_Test_Checklist.md`。

---

## 6. 不建议做的事

- 不要从 `easyqc_back/` 启动真实项目。
- 不要在 `easyqc_back/` 中修复 bug 或新增功能。
- 不要手工编辑评分 JSON 文件名，评分读取会校验文件名、目录和 JSON 内容是否一致。
- 不要把复杂 SQL 当作新版表格处理入口；请改写为 JSON 结构化规则。

---

## 7. 回退策略

如果迁移中发现严重问题：

1. 停止使用当前真实项目目录继续测试。
2. 保留出错前后的 `settings_*.json`、`Table/*.csv`、`RatingFiles/**/*.json`。
3. 用备份项目目录恢复业务工作。
4. 将复现步骤记录到 `Manual_Test_Checklist.md` 或 issue/日志中。

注意：`easyqc_back/` 是参照实现，不建议直接对真实项目继续评分；需要对比旧行为时，应使用复制出的临时项目目录。
