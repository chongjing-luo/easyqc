# 03 配置检查任务

模块就是一个检查任务,回答三个问题:**查哪些条目(筛选)、用什么命令打开图(命令模板)、记录什么判断(评分结构)**。配好一个,复制改名就是下一个;换项目可以导出带走。

入口:`质控模块` 页。最快的起步方式是 **从模板添加**——软件自带 8 个现成模块(开目录、Freeview 多表面、MRIcroGL 蒙版叠加、wb_view 等),复制成可编辑副本改一改就能用。

## 1. 基本身份

| 字段 | 要点 |
|---|---|
| 名字 | 英文/数字/下划线,项目内唯一;显示名可中文 |
| 评分者 | **要保存评分就必须填**;留空 = 只读的观察模式(给人围观用) |
| 标签(显示名) | 界面上显示的名字,随你 |

## 2. 筛选:这个模块查哪些条目

不设筛选 = 整个名单。设了,启动模块时就从**当前名单**里挑出满足条件的条目组成队列(所以名单后来变了,重新打开模块队列自动更新)。

界面就是普通筛选器:多个条件、条件间"满足全部/任一"。例:只查静息态功能像 → 条件 `mod` `等于` `rest`;只查某批 → `wave` `等于` `2`。

筛选本身支持按列类型选操作符(文本:包含/开头/结尾;数值:大于/介于……),和名单页的筛选是同一套,上限很宽裕(16 组 256 条),正常用不到头。

> 说明:一些老模块用的是旧式一行 SQL 写法(`SELECT * FROM df WHERE ...`),软件会自动转换成新筛选;你新配的用界面就行。

## 3. 命令模板:告诉它数据在哪、用什么打开

这是配置的核心。命令是一段**模板**,启动时花括号里的名字被替换成**当前条目那一行的列值 + 项目常量**,然后执行。

### 3.1 占位符:三种写法随便用

```text
${subjects_dir}   {sublist}   $moddir     ← 同一个意思,三种写法
```

- 列名和常量名都在替换范围;**拼错或不存在的不替换、原样留在命令里**——这既是排错线索,也让 `$HOME` 这类系统变量能安全共存。
- 例:模板 `open ${data_dir}/{sublist}` + 当前行 `sublist = CCNPPEK0001_01` + 常量 `data_dir = /data/CCNP` → 执行 `open /data/CCNP/CCNPPEK0001_01`。

### 3.2 五个由简到繁的真实例子

**例 1|打开文件夹 / 打开 HTML 报告**(浏览器质控的最小配置):

```text
xdg-open ${qsiprep_dir}/{sublist}/report.html      # macOS 把 xdg-open 换成 open
```

**例 2|Freeview 看皮层重建**(多文件、多表面,续行用 `\`):

```text
freeview --layout 4 \
  -v ${subjects_dir}/${fsrecon_dir}/mri/orig/001.mgz \
     ${subjects_dir}/${fsrecon_dir}/mri/mask.nii.gz:colormap=jet:opacity=0.3 \
  -f ${subjects_dir}/${fsrecon_dir}/surf/lh.pial \
     ${subjects_dir}/${fsrecon_dir}/surf/rh.pial \
     ${subjects_dir}/${fsrecon_dir}/surf/lh.white \
     ${subjects_dir}/${fsrecon_dir}/surf/rh.white
```

**例 3|一条模板同时开两个程序**(先 MRIcroGL 看蒙版,再 Freeview 看表面):

```text
MULTICMD
MRIcroGL 命令……
;|
freeview 命令……
```

第一行写 `MULTICMD`,中间用单独一行 `;|` 分隔,两段都会执行。**普通换行不会**分成两条命令,多命令必须用这个写法。

**例 4|让查看器自动摆好视图**:MRIcroGL 支持脚本,让每条数据打开就是"1400×1000 窗口 + T1 + 半透明蒙版":

```text
TMP=$(mktemp /tmp/mgl.XXXXXX); TMP="$TMP.py"; printf 'import gl\ngl.windowposition(0,0,1400,1000)\ngl.loadimage("{subjects_dir}/{fsrecon_dir}/mri/orig/001.mgz")\ngl.overlayload("{subjects_dir}/{fsrecon_dir}/mri/mask.nii.gz")\ngl.opacity(1,30)\n' > "$TMP"; ${mricrogl_dir} "$TMP"; rm -f "$TMP"
```

这段"生成临时脚本→交给查看器→清理"的写法是模板库里现成的,复制后改路径即可。也有更规范的 `SCRIPT` 写法:首行查看器命令、后面直接贴脚本文本,软件负责落盘成临时文件并传给查看器(适合 Workbench 场景等更长的脚本)。

**例 5|wb_view 看功能—结构配准**(HCP 格式输出):

```text
wb_view \
${hcp_dir}/${subses}/MNINonLinear/Results/${moddir}/${moddir}.nii.gz \
${hcp_dir}/${subses}/MNINonLinear/T1w.nii.gz \
${hcp_dir}/${subses}/MNINonLinear/${subses}.L.pial.164k_fs_LR.surf.gii \
${hcp_dir}/${subses}/MNINonLinear/${subses}.R.pial.164k_fs_LR.surf.gii
```

### 3.3 直接 / Shell,怎么选

模块里一个二选一:

| 选 | 什么时候 |
|---|---|
| **直接(默认)** | 单纯一条命令开程序。路径按整体传给程序,不经过 shell,引号、空格、括号都最不容易出幺蛾子 |
| **Shell** | 命令里用了 shell 语法:`$()`、管道、`&&`、环境变量、例 4 的 mktemp/printf/rm、MULTICMD 前缀写法 |

判断口诀:**命令里有 `$(`、`|`、`;`、`>` 这类符号 → 选 Shell;否则用直接**。选错了保存时或启动时会直接报错提示,改一下就行。

### 3.4 两个实用开关

- **"重新启动前关闭由 EasyQC 管理的查看器"**:勾上后每换一条数据先关旧窗口再开新的,屏幕清爽;要几条数据同屏对比就不勾。
- **测试命令**:不用进 QC 窗口——名单页右键某条 → **仅执行命令**,立刻看这条的图开不开得出来;开着没问题再正式评分。命令的报错信息在 QC 窗口底部的"命令输出"面板里看(04 章 §5)。

## 4. 评分结构:记录什么判断

### 4.1 评分项(打等级)

每项 = 一个名字 + 一组可选值,三种写法:

| 你写 | 评分时的选项 |
|---|---|
| `0-4` | 0,1,2,3,4(范围) |
| `4` | 1,2,3,4(数量) |
| `差,一般,好` | 三个文字选项(类别) |

一个模块可以放多个评分项——**一个模块组织几项相关检查**是推荐用法:比如结构像模块放 `headmotion / skullstrip / fs_recon / coregistration` 四项各打 0–4,而不是拆成四个模块。

### 4.2 标签(勾选框)

独立于打分的布尔标记,标记状态而不是等级:`checkdone`(查完)、`anatfailed`(结构像失败)、`need_rerun`(要返工)。

### 4.3 备注

每条一个自由文本框,记判断依据和异常位置("颞叶皮层缺失,冠状位 45–60")。

> 说明:评分项和标签一旦有评分存档,改动要谨慎——旧评分里不存在的选项会显示为"旧评分值"且不可点;结构差异较大的旧记录打开时会是只读,想改走 05 章 §4 的"已有质控记录"。

## 5. 模块的复制、导出与共享

- **项目内**:复制模块,改名改配置,是最快的"新任务"。
- **给同事/别的项目**:`导出` 得到一个 JSON 文件,对方 `导入`。注意模块里只有配置——**不带名单列和常量**,对方项目里要有同名列(如 `sublist`)和常量(如 `subjects_dir`),命令才能原样工作;缺什么就把他的列名/常量改成一致,或改命令。
- **长期沉淀**:把打磨好的模块"存为模板"(跨项目设置页),以后每个新项目直接复制。
