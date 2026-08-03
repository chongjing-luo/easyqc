# EasyQC 参考与故障排查

## 1. 标识符速查

| 字段 | 规则 | 额外限制 |
|---|---|---|
| `module_name` | `^[A-Za-z0-9_]{1,32}$` | 大小写不敏感唯一；拒绝 Windows 设备名 |
| `rater` | `^[A-Za-z0-9_]{1,32}$` | 拒绝 Windows 设备名和内部保留身份 |
| `easyqcid` | `^[A-Za-z0-9_.-]{1,128}$` | 不能是 `.` 或 `..`；总名单中大小写不敏感唯一 |

`module_name` 和 `rater` 禁止短横线，是因为评分文件使用前两个短横线分隔身份；`easyqcid` 可以包含额外短横线，解析器只分割前两次。

规范评分文件名还必须小于等于 240 UTF-8 bytes，规范绝对路径不得超过 240 UTF-16 code units。这些预算为常见文件系统组件上限和经典 Windows 路径限制留出余量。

## 2. 关键路径速查

```text
EasyQC 安装根/
├── projects.json                 当前安装的项目登记与 last_project
├── constant_templates.json       安装级常量模板
├── app_settings.json             viewer shell 模式等安装设置
└── modules/                      安装级模块模板

easyqc_<project>/
├── settings_<project>.json       schema_version: 3
├── modules/<uuid>.json           每个项目模块一个文件
├── Table/easyqc_all.csv          权威质控总名单
└── RatingFiles/<module>/<rater>/
    └── <module>-<rater>-<easyqcid>.json
```

项目设置和 rating JSON 当前只接受 `schema_version: 3`。模块文件和模板文件拥有各自内部 schema；不要因为它们的版本数字不同就手工改成 3。

## 3. 占位符速查

```text
${name}   推荐；缺失时失败
{name}    缺失时失败
$name     可能保留给 Shell 环境变量
```

上下文来源是当前行普通列加项目常量，展开一次。安装级常量模板不直接参与运行时解析，只有复制后的项目常量才参与。

## 4. Formula 速查

列：`[column name]`；字符串：`"text"`；连接：`&`；比较：`= <> < <= > >=`；逻辑：`AND OR NOT`；可选前导 `=`。

函数：`ABS`、`BLANK`、`COALESCE`、`EXTENSION`、`FIND`、`IF`、`IFERROR`、`ISBLANK`、`LEFT`、`LEN`、`LOWER`、`MID`、`PARENTPATH`、`PATHNAME`、`RIGHT`、`ROUND`、`STEM`、`SUBSTITUTE`、`TEXTAFTER`、`TEXTBEFORE`、`TRIM`、`UPPER`、`VALUE`。

完整说明见[新增列与 EasyQC Formula](06-derived-columns-and-easyqc-formula.md)。

## 5. 安装后 PySide6 可 import，但窗口打不开

在 Ubuntu/Debian 先运行：

```bash
./setup.sh --check
```

若报告 xcb 动态库缺失：

```bash
sudo apt install libxcb-cursor0
./setup.sh --check
```

不要把 `libxcb-cursor0` 写进 requirements：它是 apt 管理的系统库。若仍失败，查看 `ldd` 报告中具体 `=> not found` 项，并按当前发行版提供的包名安装；不要通过删除 Qt 插件绕过检查。

## 6. macOS 安装时提示缺少 SciPy

当前 EasyQC 不依赖 SciPy。若旧 `setup.sh` 验证脚本仍检查 `import scipy`，说明使用的是旧分支或旧下载包。应拉取当前分支/版本并重新运行安装，而不是为满足过时检查盲目增加大型依赖。

检查当前 `requirements.txt` 和 `setup.sh` 是否来自同一提交。移动仓库后还应重建 `.venv`，避免激活脚本继续指向旧绝对路径。

## 7. Excel 无法导入

症状通常是 pandas 报告缺少 `openpyxl`、`xlrd` 或不支持文件格式。处理顺序：

1. 确认扩展名与实际格式一致；
2. 在当前 `.venv` 安装匹配的 pandas Excel engine；
3. 重启 EasyQC 后重试；
4. 若不能增加依赖，先在可信表格软件中导出 UTF-8 CSV。

EasyQC 不应把 Excel engine 失败转换为空名单。

## 8. 导入名单失败

### 8.1 缺少 `easyqcid`

导入草稿允许暂时没有该列。使用“新增列”从名称提取或生成固定值，再写入总名单。写入时每行都必须有合法、唯一身份。

### 8.2 重复或大小写冲突

`SUB01` 与 `sub01` 即使字符串不完全相同也冲突。应确定哪一个是真实身份，或分配新的稳定 ID。不要通过在文件中暂时改大小写规避检查。

### 8.3 重复列名

导入源的字段必须能形成唯一列。先在源文件中重命名，或只选择一组明确字段；不要依赖 pandas 自动增加后缀，因为后续合并含义会不清楚。

### 8.4 追加字段不一致

“追加行”要求相同字段集合。若本次数据带来新字段，应改用“按 `easyqcid` 合并列”；若目的是重建 schema，则使用替换并认真检查预览。

## 9. 常量或新增列被拒绝

项目常量与总名单列不能重名。先判断该值是否随行变化：

- 所有行相同：保留为项目常量，重命名名单列；
- 每行不同：保留为名单列，重命名/删除常量。

Formula 目标列也不能覆盖已有列。先验证旧列是否可以明确删除，再创建新列。评分 JSON 不因普通列删除而删除。

## 10. 模块无法保存或启动

检查：

- `module_name` 与 rater 是否满足标识规则；
- 是否与已有模块形成 case-only 冲突；
- 模块名是否已被历史评分占用；
- 模块筛选是否引用已删除列；
- rater 是否为空；
- score 选项是否有效且无重复；
- 项目设置或模块文件是否为当前支持格式。

已有评分的模块不能改内部名。需要更友好的文字时改 label；真正的新任务应使用新 module name。

## 11. 查看器没有启动

按以下顺序定位：

1. 检查错误消息是否指出 `${name}` / `{name}` 缺失；
2. 在当前行确认路径列值，在常量页确认可执行文件或根路径；
3. 确认命令在同一操作系统用户和终端环境下可运行；
4. `shell=False` 时不要使用管道、重定向或 `&&`；
5. 只有确实需要 Shell 语法时才显式启用 `shell=True`；
6. 检查引号和含空格参数；
7. 查看当天日志中的展开/启动错误。

没有命令名称黑白名单不表示命令一定成功或安全。EasyQC 按当前用户权限启动进程，是 process controller，不是 sandbox。

## 12. 结果页拒绝刷新

错误通常来自评分扫描。报告会指出具体路径和类别：

- malformed JSON；
- unsupported schema；
- wrong depth；
- symlink；
- identity mismatch；
- duplicate identity；
- casefold identity collision。

正确恢复流程：

1. 停止对该项目的写入；
2. 复制整个项目作为取证备份；
3. 对照目录、文件名和正文三元身份；
4. 从可信备份恢复明确损坏文件，或在确认内容后修复；
5. 重新扫描/刷新。

不要直接删除所有报错 JSON 来“让结果生成”，否则可能永久丢失人工评分，也会把数据缺失伪装成成功。

## 13. 并发写入失败

同一项目已被另一个 EasyQC 进程写入时，文件锁会非阻塞失败。关闭重复写入者，确认没有遗留的真实运行进程，然后刷新并重试。不要删除锁相关文件来绕过一个仍在工作的进程。

若项目位于网络或同步文件系统，移动到本地受支持文件系统进行写入；EasyQC 不提供分布式锁和多机事务。

## 14. 日志在哪里

若设置绝对环境变量 `EASYQC_LOG_DIR`，日志写到该目录；否则使用 platformdirs 选择当前用户日志目录。当天文件名是：

```text
easyqc_YYYYMMDD.log
```

文件日志失败时控制台仍保留日志，GUI 启动后显示一次退化警告。排查报告应包含：

- 操作系统和 Python 版本；
- EasyQC commit；
- 完整错误和 traceback；
- 去除隐私数据后的相关日志；
- 最小复现步骤；
- 是否使用 `shell=True`、网络盘或外部查看器。

## 15. 项目备份与恢复

评分或设置问题出现后，先备份整个项目目录。恢复时必须保持目录结构；只恢复一个 CSV 不能恢复模块与评分，只恢复 `projects.json` 不能恢复项目内容。

建议使用“关闭写入 → 全目录复制 → 校验文件 → 导入副本”的流程。若只需要重新登记，使用“取消登记/导入项目”，不要搬动或删除原项目。

## 16. 不应采用的“修复”

- 不要把旧 `ezqcid` 字段混入当前 schema-v3 项目；
- 不要手工把旧 schema 数字改成 3；
- 不要删除坏评分后继续发表不完整结果；
- 不要绕过 `easyqcid` 唯一性或 casefold 检查；
- 不要在两个进程/两台机器同时写同一项目；
- 不要把 Formula 改成 `eval` 来获得“灵活性”；
- 不要把 `shell=True` 描述成安全沙箱。

## 17. 相关文档

- [安装与项目管理](04-installation-and-project-management.md)
- [质控名单与表格工作区](05-qc-list-and-table-workspace.md)
- [常量、模块与外部查看器](07-constants-modules-and-viewers.md)
- [评分、复查与结果](08-qc-rating-review-and-results.md)
