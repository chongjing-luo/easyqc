# EasyQC 迁移指南

> 适用范围：从重构前 EasyQC 迁移到当前 `easyqc/` 主线版本。`easyqc_back/` 只作为旧版参照，不作为日常运行入口。

---

## 1. 迁移原则

1. **继续使用原项目目录**：项目数据仍然是 `settings_{project}.json`、`Table/*.csv` 和 `RatingFiles/**/*.json`，不迁移到数据库。
2. **新版入口固定为 `easyqc/`**：日常启动请使用当前目录下的 `python easyqc.py` 或 `./start.sh`。
3. **旧版只读参照**：仓库根目录的 `easyqc_back/` 用于查旧逻辑、做特征测试和对比输出，不用于真实项目评分。
4. **先备份真实项目数据**：迁移器必须先创建完整项目副本、注册表副本和
   SHA-256 清单；备份验真之前不得切换现场项目。
5. **用真实 GUI smoke test 验收**：自动测试不能替代人工点击主窗口、表格窗口、右键菜单和 QC 页面关闭流程。

---

## 2. 启动新版

推荐方式：

```bash
cd easyqc
source .venv/bin/activate
python easyqc.py
```

### PySide6/Qt 渐进迁移

迁移期间默认界面仍为已验证的 tkinter 实现。新的 Qt 路径必须显式选择：

```bash
python easyqc.py --ui qt-preview
```

当前 Preview 已提供“共享 Core 服务 → 完整数据筛选/多列排序 → 有界行窗口 →
QAbstractTableModel/QTableView”的专业只读 Table 工作区。Filter/Sort/Columns
均为类型化可视控件，不再以 JSON 作为用户输入界面；同时包含固定 `easyqcid`、
计数、分页、精确查找、稳定选择和 QC 身份安全门。Qt 的 Table、QC 与项目配置
已通过同一个共享 Core 上下文接通真实项目；仍保持显式 Preview，是因为完整
第三方组件清单、三平台原生包和人工可访问性门禁尚未完成，而不是缺少产品路由。
需要恢复当前生产界面时使用：

```bash
python easyqc.py --ui tk
```

该切换不转换项目文件；JSON/CSV 与完整旧版评分 payload 始终是权威事实。
Qt 在 Table、QC、配置、打包及真机验证全部通过前不会成为默认入口。

Linux 使用 Qt Preview 或构建 Qt 包前，Ubuntu/Debian 需要：

```bash
sudo apt install libxcb-cursor0
./setup.sh --check
```

当前 50,000×300 合成表的 pandas query/window 基准为 p95 122.630 ms，低于
300 ms 门禁；进程峰值 RSS 573.805 MiB，仍作为容量风险保留。Polars/Arrow
条件后端因此暂不启用。

如果已经完成安装，也可以：

```bash
cd easyqc
./start.sh
```

CLI 直达 QC 页面：

```bash
cd easyqc
python easyqc.py <project> <module> <rater> <easyqcid>
```

示例：

```bash
python easyqc.py CCNPPEKI AnatRestAll rf CCNPPEK0001_01_rest01
```

---

## 3. 严格 schema-v3 项目契约

当前版本只读取 schema v3，不再内置旧项目兼容读取器。schema v1、
`ezqcid`、`ezqc_*.csv` 和旧 `._.` 评分文件名必须先通过离线迁移器转换；
不符合契约时程序会明确报错，不会静默猜测或部分加载。

```text
easyqc_<PROJECT>/
├── settings_<PROJECT>.json
├── modules/
│   └── <module-uuid>.json
├── Table/
│   ├── easyqc_all.csv
│   ├── easyqc_qctable.csv
│   └── easyqc_qctable_filter.csv
└── RatingFiles/
    └── <module>/<rater>/<module>-<rater>-<easyqcid>.json
```

契约要点：

- `settings_<PROJECT>.json` 必须使用 `schema_version: 3`。
- 每个评分 JSON 必须使用 `schema_version: 3` 和 `easyqcid`，并保留完整模块
  payload。
- 模块仓库的文件格式有独立版本，当前仍为 `schema_version: 1`；其中模块
  payload 使用 `easyqcid`。
- `easyqc_all.csv` 的 `easyqcid` 必须非空、合法、唯一且不存在仅大小写不同
  的冲突。
- 评分文件目录、文件名和 JSON 内部的模块名、评分者、`easyqcid` 必须完全
  一致。
- 项目设置和评分采用原子写入；模块和评分的冲突会失败并显式报告。

真实 `CCNPPEKI` 项目已于 2026-07-29 完成严格 schema-v3 迁移：
2,483 条评分、7 个模块、27 个 CSV 均通过当前 Core 扫描和聚合验证。该结果
证明这一项目已迁移，不代表其他旧项目可以跳过各自的备份和验证。

仍需单独补充的验证场景包括真实大表格 GUI 点击/滚动，以及 macOS、Windows
原生界面测试；这些平台验证不改变存储契约。

---

## 4. 表格转换变化

新版不再依赖外部 SQL 查询引擎。用户界面使用类型化条件和操作控件；内部由
`TableTransformEngine` 接收结构化操作契约执行，用户不需要编辑 JSON。

支持的主要操作：

- 选列和重排列。
- 行筛选。
- 排序。
- 新增或更新变量列。
- 删除列和重命名列。
- 表格合并。
- 分组聚合。

转换策略：

- 新规则通过 GUI 构建；JSON 仅是内部兼容/传输契约，不是用户编辑界面。
- 简单旧文本 `SELECT * FROM df WHERE ...` 会被窄范围转换为结构化筛选。
- 复杂 SQL 不兼容，包括 `JOIN`、`GROUP BY`、子查询、分号多语句、任意非 `SELECT * FROM df` 查询。
- 不恢复 SQL 执行引擎，也不重新引入相关依赖。

---

## 5. 迁移一个旧项目

关闭 EasyQC 后，在当前 `easyqc/` 目录执行：

```bash
source .venv/bin/activate
python scripts/migrate_project_schema_v3.py \
  --registry "$PWD/projects.json" \
  --project <PROJECT> \
  --backup-parent "$PWD/project_backups"
```

迁移器按固定顺序执行：

1. 从注册表解析一个精确项目路径，并拒绝符号链接和意外目录结构。
2. 创建不可覆盖的完整项目/注册表备份和 SHA-256 清单。
3. 验证复制前后现场数据未发生并发变化。
4. 在同一文件系统的独立暂存目录转换设置、模块、全部 CSV 和评分。
5. 用当前 `ProjectService`、`ModuleRepository`、`RatingService` 和结果聚合
   全量验证暂存项目。
6. 仅在所有门禁通过后，以目录重命名切换现场项目。
7. 使用真实注册表再次加载、扫描并聚合现场项目。
8. 写入 `migration_report.json` 和 `ROLLBACK.md`；完整旧数据继续保存在
   带时间戳的备份目录中。

迁移完成后再进行 GUI 检查：切换目标项目，打开总名单和结果表，打开一条已有
评分确认评分/标签/备注，保存一条测试修改并重新打开，最后检查右键菜单和外部
查看器命令。

详细人工测试表见仓库根目录的 `Manual_Test_Checklist.md`。

---

## 6. 不建议做的事

- 不要从 `easyqc_back/` 启动真实项目。
- 不要在 `easyqc_back/` 中修复 bug 或新增功能。
- 不要把 schema v1 或带 `ezqcid` 的项目直接交给当前程序；先运行离线迁移。
- 不要手工编辑评分 JSON 文件名，评分读取会校验文件名、目录和 JSON 内容是否一致。
- 不要把复杂 SQL 或 JSON 当作新版筛选入口；请使用类型化可视 Filter Builder。

---

## 7. 回退策略

如果迁移中发现严重问题：

1. 关闭 EasyQC，并停止继续写入当前项目。
2. 打开本次带时间戳备份目录中的 `ROLLBACK.md`。
3. 将当前项目目录移动到一个新的诊断路径，禁止覆盖。
4. 把备份中的完整项目副本复制回注册路径；必要时同样保留并恢复
   `projects.json`。
5. 依据 `backup_manifest.json` 重新核对 SHA-256，再恢复业务使用。

注意：`easyqc_back/` 是参照实现，不建议直接对真实项目继续评分；需要对比旧行为时，应使用复制出的临时项目目录。
