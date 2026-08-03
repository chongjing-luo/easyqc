# EasyQC 安装与项目管理

## 1. 当前交付形态

EasyQC 当前以源码目录加独立 Python 虚拟环境运行，而不是 PyPI 包、Docker 镜像或每个平台各自维护的一套 GUI。产品只有一套 PySide6/Qt Widgets 代码；Linux、Windows 和 macOS 复用同一业务实现，并让 Qt 与操作系统负责字体、DPI 和原生窗口外观。

最低运行要求是 Python 3.10。基础 Python 依赖来自仓库根目录的 `requirements.txt`：NumPy、pandas、Lark、platformdirs、PySide6-Essentials 及其传递依赖。SciPy 不是 EasyQC 的运行依赖，也不应作为安装成功条件。

> `requirements.txt` 只能声明 Python 包。Ubuntu 的 `libxcb-cursor0` 属于操作系统动态库，必须由系统包管理器安装，不能写成 pip requirement。

## 2. Linux 与 macOS 安装

在 EasyQC 仓库根目录运行：

```bash
chmod +x setup.sh
./setup.sh
```

脚本会：

1. 检查 Python 3.10 或更高版本；
2. 在当前仓库创建 `.venv`；
3. 安装 `requirements.txt`；
4. 验证 NumPy、pandas、Lark 和 Qt Widgets；
5. 生成从正确工作目录启动程序的 `start.sh`。

常用检查命令：

```bash
./setup.sh --check   # 只验证，不安装
./setup.sh --force   # 重建当前仓库的 .venv
```

`.venv` 含有绝对路径信息。复制或移动整个仓库后，应重新运行 `./setup.sh --force`，不要假设旧环境仍然可用。

### 2.1 Ubuntu 的 Qt xcb 运行库

若 Ubuntu/Debian 缺少 Qt xcb 依赖，先安装：

```bash
sudo apt install libxcb-cursor0
./setup.sh --check
```

安装脚本会定位 PySide6 自带的 `libqxcb.so`，再使用 `ldd` 检查真实动态依赖。只验证 `import PySide6` 不足以证明图形窗口能够启动，因此任何 `=> not found` 都会明确使检查失败。

## 3. Windows 手动安装

Windows 当前使用标准虚拟环境流程；`setup.sh` 面向 Bash 环境，不是 Windows 安装器。

```powershell
py -3.10 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python easyqc.py
```

也可以使用更高版本的受支持 Python。应从仓库根目录启动，因为 EasyQC 采用 flat layout：`easyqc.py`、`core/`、`models/`、`gui_qt/` 和 `utils/` 是并列路径，当前项目不是 `pip install easyqc` 后从任意目录导入的标准包。

## 4. 可选文件格式依赖

CSV、TXT/LIST 和直接输入不需要额外引擎。Excel 导入调用 `pandas.read_excel`，因此还需要与文件类型匹配的 pandas Excel engine，例如 `.xlsx` 通常使用 `openpyxl`。基础 requirements 没有强制安装这些可选引擎；缺少时导入会显示原始失败原因，而不是悄悄返回空表。

## 5. 启动方式

完整主界面：

```bash
python easyqc.py
```

Linux/macOS 安装脚本生成启动器后也可使用：

```bash
./start.sh
```

已知全部身份时，可直接进入同一个 Qt QC 窗口：

```bash
python easyqc.py <project> <module> <rater> <easyqcid>
```

四个参数必须同时存在且有效。CLI 路径使用与主界面相同的 Core、模块队列、查看器和评分服务，不是简化版数据通道。

启动时先出现 520×252 的无确定进度加载窗口。它在当前活动屏幕的可用区域居中，至少显示约 500 ms；完整上下文准备成功后才显示主窗口。若上次项目损坏，程序报告错误，不会用空项目掩盖失败。

## 6. 创建项目

在“项目选择”点击“新建项目”，填写项目名并选择父目录。若所选目录名不是 `easyqc_<项目名>`，程序会在其中创建该名称的子目录。目标必须为空或由 EasyQC 新建，避免把项目文件写进已有资料目录。

典型项目结构是：

```text
easyqc_<project>/
├── settings_<project>.json
├── modules/
│   └── <module-uuid>.json
├── Table/
│   └── easyqc_all.csv
└── RatingFiles/
    └── <module_name>/<rater>/
        └── <module_name>-<rater>-<easyqcid>.json
```

- `settings_<project>.json` 保存 schema-v3 项目设置和模块映射。
- `modules/` 每个文件保存一个项目模块；文件名使用内部 UUID，模块业务名在正文中。
- `Table/easyqc_all.csv` 是质控总名单。
- `RatingFiles/` 保存每个三元身份的当前评分快照。

项目创建后会登记到当前 EasyQC 安装根目录的 `projects.json`。模板和命令执行设置也跟随当前安装目录，而不是写入操作系统级全局共享区。因此两个独立 EasyQC 检出不会自动污染彼此的项目登记或模板。

## 7. 导入现有项目

“导入项目”只是把已有项目目录登记到当前安装。候选目录必须：

- 恰好包含一个 `settings_*.json`；
- 设置正文是 JSON object；
- 项目设置使用 `schema_version: 3`；
- 模块目录、模块映射和其他必需合同可被严格读取。

候选项目先完整准备，成功后才切换当前项目。若存在两个 settings 文件、JSON 损坏或 schema 不支持，当前项目、内存状态和 `last_project` 都保持不变。

EasyQC 有意不提供旧 `ezqc` / `ezqcid` 或 schema 0/1/2 的运行时兼容层。旧项目必须在独立备份上使用明确迁移工具转换后再导入；不能通过手工改一个版本号伪装成新格式。

## 8. 切换、恢复与取消登记

每次成功创建、导入或切换项目后，`projects.json` 的 `last_project` 被原子更新。下次启动会准备该项目的设置、总名单、模块和评分；只有整套候选上下文有效才激活。

“取消登记”只从本次安装的项目列表中移除路径：

- 不删除项目目录；
- 不删除评分文件；
- 不清空用户的 CSV/JSON；
- 以后仍可通过“导入项目”重新登记。

这一命名刻意避免把“从最近项目列表移除”误解为“删除磁盘数据”。

## 9. 模板与项目所有权

“跨项目设置”中的常量和模块是安装级模板。它们只在用户执行“从模板添加”时复制到当前项目：

1. 复制前可以调整候选内容；
2. 项目级名称冲突会阻止写入；
3. 成功后副本归项目所有并可编辑；
4. 原模板与项目副本不再同步。

这种 copy-only 设计使项目可复现、可整体搬迁，也避免升级模板时无意改变已经开始的质控任务。

## 10. 备份与迁移建议

当前项目的完整可恢复单元是整个 `easyqc_<project>/` 目录，而不是单独的结果 CSV。备份时应：

1. 关闭该项目的 EasyQC 写入窗口；
2. 复制整个项目目录到新位置或版本化存储；
3. 保留 `settings_*.json`、`modules/`、`Table/` 和 `RatingFiles/` 的相对结构；
4. 在副本上验证文件数、大小或校验和；
5. 需要使用时，通过“导入项目”登记副本。

`projects.json` 只是登记表，不替代项目备份。仅复制它不能恢复实际名单或评分。EasyQC 的“同一身份覆盖最新评分”也不是事件历史；若需要保留每次编辑，应使用目录快照、Git/LFS 或受管理的外部备份策略。

## 11. 安装和项目管理的失败原则

- Python 包安装失败时保留 pip 错误，不把环境标记为可用。
- Qt 插件动态库缺失时安装检查失败。
- 项目候选不完整时不切换当前项目。
- schema 不匹配时拒绝读取，不做猜测性升级。
- 项目名称或路径冲突时不覆盖已有目录。
- 取消登记不承担文件删除职责。

## 12. 相关文档

- [架构与数据流](03-architecture-and-data-flow.md)
- [名单与表格工作区](05-qc-list-and-table-workspace.md)
- [可靠性、性能与平台](09-reliability-performance-and-platforms.md)
- [参考与故障排查](10-reference-and-troubleshooting.md)
