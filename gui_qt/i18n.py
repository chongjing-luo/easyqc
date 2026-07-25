"""Runtime localization for the Qt preview without touching business data."""

from __future__ import annotations

import re
import weakref
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from PySide6.QtCore import (
    QEvent,
    QLibraryInfo,
    QObject,
    QSettings,
    QTimer,
    QTranslator,
    Qt,
    Signal,
)
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QTabWidget,
    QTableWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from models.formula_function import FORMULA_FUNCTION_CATALOG

DEFAULT_LANGUAGE = "zh_CN"
SUPPORTED_LANGUAGES = ("zh_CN", "en")
LANGUAGE_SETTING_KEY = "ui/language"
USER_TEXT_PROPERTIES = "_easyqc_user_text_properties"
TRANSLATABLE_TABLE_COLUMNS_PROPERTY = "_easyqc_translatable_table_columns"
_LOCALIZABLE_PROPERTY_NAMES = frozenset(
    {
        "accessibleDescription",
        "accessibleName",
        "headers",
        "items",
        "placeholder",
        "tabs",
        "text",
        "title",
        "toolTip",
        "windowTitle",
    }
)


MESSAGES: Mapping[str, Mapping[str, str]] = {
    "app.workspace": {"zh_CN": "EasyQC 工作区", "en": "EasyQC workspace"},
    "field.ezqcid": {"zh_CN": "ezqcid", "en": "ezqcid"},
    "language.label": {"zh_CN": "语言", "en": "Language"},
    "language.switch_to_english": {
        "zh_CN": "将界面切换为英文",
        "en": "Switch the interface to English",
    },
    "language.switch_to_chinese": {
        "zh_CN": "将界面切换为中文",
        "en": "Switch the interface to Chinese",
    },
    "navigation.collapse": {
        "zh_CN": "收起导航",
        "en": "Collapse navigation",
    },
    "navigation.expand": {
        "zh_CN": "展开导航",
        "en": "Expand navigation",
    },
    "nav.constants": {"zh_CN": "常量设置", "en": "Constants"},
    "nav.import": {"zh_CN": "质控名单导入", "en": "QC list import"},
    "nav.modules": {"zh_CN": "质控模块", "en": "QC modules"},
    "nav.pre_qc": {"zh_CN": "质控前名单", "en": "Pre-QC list"},
    "nav.projects": {"zh_CN": "项目选择", "en": "Project selection"},
    "nav.results": {"zh_CN": "质控结果", "en": "QC results"},
    "startup.accessible": {"zh_CN": "EasyQC 启动", "en": "EasyQC startup"},
    "startup.loading_project": {
        "zh_CN": "正在加载项目数据…",
        "en": "Loading project data…",
    },
    "startup.preparing": {
        "zh_CN": "正在准备工作区…",
        "en": "Preparing workspace…",
    },
    "startup.subtitle": {
        "zh_CN": "可靠质控，专注工作本身。",
        "en": "Reliable quality control, focused on the work.",
    },
    "status.project_loaded": {
        "zh_CN": "已加载项目：{project_name}",
        "en": "Project loaded: {project_name}",
    },
    "table.filter.remove": {
        "zh_CN": "移除筛选 {summary}",
        "en": "Remove filter {summary}",
    },
    "table.sort.ascending": {"zh_CN": "升序", "en": "Ascending"},
    "table.sort.descending": {"zh_CN": "降序", "en": "Descending"},
    "table.sort.tooltip": {
        "zh_CN": "排序优先级 {priority} · {direction}",
        "en": "Sort priority {priority} · {direction}",
    },
}


# Existing v6 widgets predate the catalog. This explicit compatibility registry
# lets them participate in runtime switching while owners are migrated to stable
# message keys. Longer phrases are applied before shorter terms.
SOURCE_TRANSLATIONS: Mapping[str, str] = {
    "EasyQC 功能导航": "EasyQC feature navigation",
    "EasyQC 当前功能页": "Current EasyQC page",
    "EasyQC 表格预览": "EasyQC table preview",
    "EasyQC 工作区": "EasyQC workspace",
    "向右滚动": "Scroll right",
    "向左滚动": "Scroll left",
    "向上滚动": "Scroll up",
    "向下滚动": "Scroll down",
    "EasyQC 质控控制器窗口": "EasyQC QC controller window",
    "EasyQC 质控控制器": "EasyQC QC controller",
    "仅查看，不修改评分": "View only; ratings cannot be changed",
    "质控标签": "QC tag",
    "质控备注": "QC notes",
    "尚未打开项目，请在“项目选择”中创建或导入项目。": (
        "No project is open. Create or import one in Project selection."
    ),
    "只读质控结果；筛选、排序和列设置作用于完整结果。": (
        "Read-only QC results. Filters, sorting and column settings apply to "
        "the complete result."
    ),
    "只读名单；筛选和排序作用于完整结果。": (
        "Read-only list. Filters and sorting apply to the complete result."
    ),
    "写入前会校验 ezqcid、重复行和字段冲突；失败不会修改原名单。": (
        "Before writing, EasyQC validates ezqcid, duplicate rows and field "
        "conflicts. A failure leaves the original list unchanged."
    ),
    "支持算术、比较以及 abs、round、isna、notna、fillna、contains、"
    "startswith、endswith、isin；不支持 Python 代码或 SQL。": (
        "Supports arithmetic, comparisons, abs, round, isna, notna, fillna, "
        "contains, startswith, endswith and isin. Python code and SQL are not supported."
    ),
    "勾选要显示的列；固定列始终位于表格最前面。": (
        "Select columns to show. Pinned columns always stay at the start of the table."
    ),
    "从上到下依次应用排序规则。": "Sort rules are applied from top to bottom.",
    "排序优先级": "Sort priority",
    "使用质控前名单中的已有列生成普通新列": (
        "Create a regular new column from existing Pre-QC list columns"
    ),
    "使用导入草稿中的已有列生成普通新列": (
        "Create a regular new column from existing import-draft columns"
    ),
    "使用已有列生成新列": "Create a new column from existing columns",
    "使用导入草稿中的已有列或固定值生成普通新列": (
        "Create a regular new column from an existing import-draft column or "
        "a fixed value"
    ),
    "使用已有列或固定值生成新列": (
        "Create a new column from an existing column or fixed value"
    ),
    "放弃当前未保存的质控修改并关闭 EasyQC？": (
        "Discard the unsaved QC changes and close EasyQC?"
    ),
    "放弃当前未保存的质控修改并关闭？": (
        "Discard the unsaved QC changes and close?"
    ),
    "请先保存或放弃当前质控修改，再打开其他名单": (
        "Save or discard the current QC changes before opening another list."
    ),
    "请先保存或放弃当前质控修改，再切换项目": (
        "Save or discard the current QC changes before switching projects."
    ),
    "请先保存或放弃当前质控修改，再刷新项目": (
        "Save or discard the current QC changes before refreshing the project."
    ),
    "请先保存或放弃当前质控修改，再刷新结果": (
        "Save or discard the current QC changes before refreshing results."
    ),
    "请先保存或放弃当前模块更改，再设置或清除质控名单筛选": (
        "Save or discard the module changes before changing its QC-list filter."
    ),
    "重新启动前关闭由 EasyQC 管理的查看器": (
        "Close viewers managed by EasyQC before relaunching"
    ),
    "查看器命令模板，例如：freeview {image} --title {ezqcid}": (
        "Viewer command template, for example: freeview {image} --title {ezqcid}"
    ),
    "写入：质控前名单": "Write to Pre-QC list",
    "写入质控前名单": "Write to Pre-QC list",
    "质控名单筛选事务正在完成，请稍候": (
        "The QC-list filter transaction is finishing. Please wait."
    ),
    "模块质控名单筛选事务正在完成，请稍候": (
        "The module QC-list filter transaction is finishing. Please wait."
    ),
    "另一项质控名单筛选任务仍在运行": (
        "Another QC-list filter task is still running."
    ),
    "另一项质控名单任务仍在运行": "Another QC-list task is still running.",
    "另一项名单导入任务仍在运行": "Another list import task is still running.",
    "另一项配置任务仍在运行": "Another configuration task is still running.",
    "另一项项目加载任务仍在运行": "Another project load is still running.",
    "项目配置任务仍在运行，请稍后重试": (
        "A project configuration task is still running. Try again shortly."
    ),
    "项目内容已改变；请先保存或放弃当前质控修改，再刷新": (
        "The project changed. Save or discard the current QC changes before refreshing."
    ),
    "外部图像查看器命令模板": "External image viewer command template",
    "外部图像查看器": "External image viewer",
    "质控名单筛选摘要": "QC-list filter summary",
    "质控名单筛选错误": "QC-list filter error",
    "质控名单导入错误": "QC list import error",
    "质控名单导入": "QC list import",
    "质控名单筛选信息仍在加载": "QC-list filter information is still loading.",
    "质控名单筛选窗口返回了无效草稿": (
        "The QC-list filter dialog returned an invalid draft."
    ),
    "质控名单筛选任务返回了无效结果": (
        "The QC-list filter task returned an invalid result."
    ),
    "质控名单筛选保存任务返回了无效结果": (
        "Saving the QC-list filter returned an invalid result."
    ),
    "质控名单筛选保存结果与所选模块不一致": (
        "The saved QC-list filter does not match the selected module."
    ),
    "质控名单筛选准备任务返回了无效结果": (
        "Preparing the QC-list filter returned an invalid result."
    ),
    "质控名单筛选准备结果已失效": (
        "The prepared QC-list filter result is no longer current."
    ),
    "质控名单筛选所属的控制器或项目上下文已变化": (
        "The controller or project context for this QC-list filter has changed."
    ),
    "质控候选工作流任务返回了无效结果": (
        "The candidate QC workflow task returned an invalid result."
    ),
    "质控候选工作流结果已失效": "The candidate QC workflow is no longer current.",
    "所选模块已变化，请重新打开质控名单筛选": (
        "The selected module changed. Reopen its QC-list filter."
    ),
    "所选记录不在当前视图中": "The selected record is not in the current view.",
    "所选记录引用已失效，请重新选择": (
        "The selected record is no longer current. Select it again."
    ),
    "已应用视图发生变化，请重新打开视图设置后再试。": (
        "The applied view changed. Reopen View settings and try again."
    ),
    "表格导出任务仍在运行": "A table export is still running.",
    "后台表格导出任务返回了无效回执": (
        "The background table export returned an invalid receipt."
    ),
    "后台表格查询返回了无效结果": (
        "The background table query returned an invalid result."
    ),
    "当前表格不能写入新增列": "This table cannot write a new column.",
    "新增列保存任务没有返回正确的列名": (
        "Saving the new column did not return the expected column name."
    ),
    "新增列名必须是不含空格或标点的有效字段名": (
        "The new column name must be a valid field name without spaces or punctuation."
    ),
    "新增列表达式不能为空": "The new-column expression cannot be empty.",
    "新增列名不能为空": "The new column name cannot be empty.",
    "新增列预览来源必须是表格": "The new-column preview source must be a table.",
    "当前结果中没有 ezqcid 列": "The current result has no ezqcid column.",
    "ezqcid 必须保持在第一列、可见且固定": (
        "ezqcid must remain first, visible and pinned."
    ),
    "ezqcid 必须保持为第一列": "ezqcid must remain the first column.",
    "固定列必须组成连续的前置列区": (
        "Pinned columns must form one continuous leading block."
    ),
    "隐藏列或固定列列表中包含重复项": (
        "The hidden or pinned column list contains duplicates."
    ),
    "列设置中包含重复列": "Column settings contain duplicate columns.",
    "列设置窗口返回了无效草稿": (
        "The column settings dialog returned an invalid draft."
    ),
    "排序窗口返回了无效草稿": "The sort dialog returned an invalid draft.",
    "筛选窗口返回了无效草稿": "The filter dialog returned an invalid draft.",
    "筛选最多包含": "A filter can contain at most",
    "每个筛选组最多包含": "Each filter group can contain at most",
    "排序规则数不能超过列数": (
        "The number of sort rules cannot exceed the number of columns."
    ),
    "每一列只能用于一条排序规则：": "Each column can be used by only one sort rule:",
    "项目目录不存在": "Project directory does not exist",
    "无法打开项目目录": "Cannot open project directory",
    "项目预览信息不可用": "Project preview information is unavailable",
    "项目打开请求未被接受": "The project-open request was not accepted.",
    "项目加载任务返回了无效快照": (
        "The project load task returned an invalid snapshot."
    ),
    "项目加载失败": "Project load failed",
    "名单写入任务返回了无效表格": "The list write task returned an invalid table.",
    "名单预览任务返回了无效结果": (
        "The list preview task returned an invalid result."
    ),
    "名单读取任务缺少导入草稿": "The list read task has no import draft.",
    "模块导入任务返回了无效模块列表": (
        "The module import task returned an invalid module list."
    ),
    "质控结果刷新请求未启动": "The QC-results refresh did not start.",
    "Qt 预览模式不能打开质控": "Qt preview mode cannot start QC.",
    "当前状态无法启动质控": "QC cannot start in the current state.",
    "当前质控控制器已关闭": "The current QC controller is closed.",
    "启动失败：当前界面无法启动质控": (
        "Start failed: this window cannot start QC."
    ),
    "启动失败：质控启动请求未被接受": (
        "Start failed: the QC start request was not accepted."
    ),
    "保存后的质控名单与候选工作流不一致": (
        "The saved QC list does not match the candidate workflow."
    ),
    "质控名单候选控制器不存在": "The candidate QC-list controller is missing.",
    "质控名单候选缺少筛选表达式": (
        "The candidate QC list has no filter expression."
    ),
    "取消登记 {name}？项目文件不会被删除。": (
        "Unregister {name}? Project files will not be deleted."
    ),
    "新建 EasyQC 项目": "Create EasyQC project",
    "选择 EasyQC 项目目录": "Select EasyQC project folder",
    "选择项目上级目录": "Select parent folder for project",
    "选择包含名单目录的文件夹": "Select the folder containing list files",
    "选择 CSV、Excel、TXT 或 LIST 文件": "Select a CSV, Excel, TXT or LIST file",
    "导入质控模块": "Import QC module",
    "导出质控模块": "Export QC module",
    "新建项目": "New project",
    "导入项目": "Import project",
    "打开项目": "Open project",
    "项目选择": "Project selection",
    "项目列表": "Projects",
    "项目信息": "Project details",
    "项目名称": "Project name",
    "项目目录": "Project folder",
    "当前打开": "Open now",
    "最近打开": "Recently opened",
    "已登记": "Registered",
    "尚未加载项目": "No project loaded",
    "刷新项目": "Refresh project",
    "打开目录": "Open folder",
    "取消登记": "Unregister",
    "取消项目登记": "Unregister project",
    "常量设置": "Constants",
    "常量名": "Constant name",
    "常量值": "Value",
    "添加常量": "Add constant",
    "删除选中常量": "Delete selected constant",
    "搜索常量名或值": "Search name or value",
    "搜索常量": "Search constants",
    "质控前名单": "Pre-QC list",
    "质控结果": "QC results",
    "质控模块": "QC modules",
    "已有质控记录": "Existing QC records",
    "没有可用的质控模块": "No QC modules are available",
    "没有已有质控记录": "No existing QC records",
    "此模块将以只读模式打开": "This module will open read-only",
    "该条目不在此模块的独立质控名单中": (
        "This item is not in the module's independent QC list"
    ),
    "当前表格不能打开质控菜单": "This table cannot open the QC menu.",
    "当前质控表格不能打开质控菜单": (
        "The current QC table cannot open the QC menu."
    ),
    "模块列表": "Modules",
    "模块编辑": "Module editor",
    "模块名称（内部 ID）": "Module name (internal ID)",
    "显示标签": "Display label",
    "质控员（留空为只读）": "Rater (leave blank for read-only)",
    "基本信息": "Basic information",
    "评分项": "Rating items",
    "选项（逗号分隔）": "Options (comma-separated)",
    "外部图像查看器": "External image viewer",
    "新建模块": "New module",
    "导入模块": "Import module",
    "导出模块": "Export module",
    "删除模块": "Delete module",
    "保存模块": "Save module",
    "放弃更改": "Discard changes",
    "启动质控": "Start QC",
    "设置筛选": "Set filter",
    "清除筛选": "Clear filter",
    "全部名单": "Entire list",
    "未选择模块": "No module selected",
    "请先保存模块": "Save the module first",
    "质控名单": "QC list",
    "筛选名单": "Filter list",
    "只读": "Read only",
    "只读模式": "Read-only mode",
    "上一个": "Previous",
    "下一个": "Next",
    "保存并下一个": "Save and next",
    "保存": "Save",
    "序号": "No.",
    "评分": "Rating",
    "未评": "Not rated",
    "标签": "Tags",
    "备注": "Notes",
    "填写简短备注…": "Add a short note…",
    "筛选": "Filter",
    "排序": "Sort",
    "列显示": "Columns",
    "新增列": "New column",
    "刷新结果": "Refresh results",
    "导出…": "Export…",
    "查找": "Find",
    "搜索 ezqcid": "Search ezqcid",
    "上一页": "Previous page",
    "下一页": "Next page",
    "每页": "Per page",
    "视图设置": "View settings",
    "关闭": "Close",
    "重置": "Reset",
    "取消": "Cancel",
    "应用": "Apply",
    "筛选行": "Filter rows",
    "排序行": "Sort rows",
    "列设置": "Column settings",
    "添加组": "Add group",
    "添加条件": "Add condition",
    "删除组": "Remove group",
    "组间关系": "Between groups",
    "组内关系": "Within group",
    "满足全部": "Match all",
    "满足任一": "Match any",
    "满足全部组": "Match all groups",
    "满足任一组": "Match any group",
    "列": "Column",
    "条件": "Condition",
    "值": "Value",
    "选项": "Options",
    "等于": "Equals",
    "不等于": "Does not equal",
    "包含": "Contains",
    "开头为": "Starts with",
    "结尾为": "Ends with",
    "为空": "Is empty",
    "不为空": "Is not empty",
    "大于": "Greater than",
    "大于或等于": "Greater than or equal",
    "小于": "Less than",
    "小于或等于": "Less than or equal",
    "属于": "Is one of",
    "不属于": "Is not one of",
    "范围": "Range",
    "从": "From",
    "到": "To",
    "添加排序": "Add sort",
    "优先级": "Priority",
    "方向": "Direction",
    "升序": "Ascending",
    "降序": "Descending",
    "上移": "Move up",
    "下移": "Move down",
    "删除": "Delete",
    "清空": "Clear",
    "搜索列": "Search columns",
    "固定": "Pin",
    "取消固定": "Unpin",
    "新列名": "New column name",
    "计算表达式": "Expression",
    "生成列": "Create column",
    "使用快捷模板或 EasyQC 公式生成新列": (
        "Create a new column with a quick template or EasyQC Formula"
    ),
    "例如：scan_key 或 QC 分组": "For example: scan_key or QC group",
    "快捷模板和高级公式共用同一套安全计算规则。公式、模板和中间结果不会保存；"
    "只写入最终普通列。": (
        "Quick templates and advanced formulas use the same safe calculation "
        "rules. Formulas, templates and intermediate results are not saved; "
        "only the final ordinary column is written."
    ),
    "新增列公式编辑器": "New-column formula editor",
    "预览新增列结果和逐行错误": (
        "Preview the new-column result and row errors"
    ),
    "新增列前二十行公式预览": (
        "First twenty rows of the new-column formula preview"
    ),
    "确认计算完整数据并写入新列": (
        "Calculate the full data and write the new column"
    ),
    "EasyQC 公式编辑器": "EasyQC Formula editor",
    "公式输入方式": "Formula input mode",
    "快捷模板": "Quick templates",
    "高级公式": "Advanced formula",
    "快捷公式模板": "Quick formula templates",
    "快捷模板类型": "Quick-template type",
    "模板": "Template",
    "快捷模板参数": "Quick-template parameters",
    "生成公式": "Generate formula",
    "用快捷模板生成公式": "Generate a formula from the quick template",
    "固定值类型": "Fixed-value type",
    "固定值内容": "Fixed-value content",
    "固定布尔值": "Fixed Boolean value",
    "固定值必须是整数": "Fixed value must be an integer",
    "固定值必须是小数": "Fixed value must be a decimal number",
    "请输入固定数值": "Enter a fixed number",
    "固定数值格式无效": "The fixed-number format is invalid",
    "类型": "Type",
    "连接两列": "Concatenate two columns",
    "连接左列": "Left column for concatenation",
    "连接分隔文本": "Concatenation separator text",
    "连接右列": "Right column for concatenation",
    "左列": "Left column",
    "中间文本": "Separator text",
    "右列": "Right column",
    "按分隔符提取": "Extract by delimiter",
    "提取来源列": "Extraction source column",
    "提取分隔符": "Extraction delimiter",
    "提取位置": "Extraction position",
    "来源列": "Source column",
    "保留": "Keep",
    "分隔符之前": "Before delimiter",
    "分隔符之后": "After delimiter",
    "条件生成": "Conditional value",
    "条件来源列": "Condition source column",
    "条件比较符": "Condition comparison operator",
    "条件比较文本": "Condition comparison text",
    "条件成立文本": "Text when true",
    "条件不成立文本": "Text when false",
    "比较": "Compare",
    "等于文本": "Text to match",
    "成立时": "When true",
    "不成立时": "When false",
    "文本清理与大小写": "Text cleanup and case",
    "文本清理来源列": "Text-cleanup source column",
    "文本清理方式": "Text-cleanup operation",
    "处理": "Operation",
    "去除首尾空白": "Trim leading and trailing whitespace",
    "转为大写": "Convert to uppercase",
    "转为小写": "Convert to lowercase",
    "去空白并转大写": "Trim and convert to uppercase",
    "去空白并转小写": "Trim and convert to lowercase",
    "数值计算": "Numeric calculation",
    "数值计算左列": "Left column for numeric calculation",
    "数值运算符": "Numeric operator",
    "数值计算右值类型": "Right-value type for numeric calculation",
    "数值计算右列": "Right column for numeric calculation",
    "数值计算固定数值": "Fixed value for numeric calculation",
    "运算": "Operator",
    "右值类型": "Right-value type",
    "固定数值": "Fixed number",
    "例如：12 或 0.5": "For example: 12 or 0.5",
    "当前公式": "Current formula",
    "公式只计算当前表格中的值；不会运行 Python、SQL、正则或文件操作。": (
        "The formula calculates only values in the current table; it cannot "
        "run Python, SQL, regular expressions or file operations."
    ),
    "当前 EasyQC 公式": "Current EasyQC Formula",
    '例如：IF([site] = "A", UPPER([filename]), [filename])': (
        'For example: IF([site] = "A", UPPER([filename]), [filename])'
    ),
    "请输入公式": "Enter a formula",
    "公式语法状态": "Formula syntax status",
    "高级公式插入工具": "Advanced formula insertion tools",
    "要插入的表格列": "Table column to insert",
    "插入列": "Insert column",
    "在光标处插入所选列": "Insert the selected column at the cursor",
    "表格列": "Table column",
    "要插入的公式函数": "Formula function to insert",
    "插入函数": "Insert function",
    "在光标处插入所选函数": "Insert the selected function at the cursor",
    "函数": "Function",
    "函数说明": "Function details",
    "公式函数签名": "Formula function signature",
    "公式函数说明": "Formula function description",
    "公式函数示例": "Formula function example",
    "VALUE 无法转换为数值": "VALUE cannot convert the value to a number",
    "IF 条件需要布尔值": "IF condition requires Boolean values",
    "除数不能为零": "The divisor cannot be zero",
    "FIND 未找到文本": "FIND cannot find the requested text",
    "公式缺少运算对象": "The formula is missing an operand",
    "公式不能为空": "The formula cannot be empty",
    "公式没有产生有效表达式": (
        "The formula did not produce a valid expression"
    ),
    "现有列（双击插入）": "Existing columns (double-click to insert)",
    "预览结果": "Preview",
    "例如：scan_key": "For example: scan_key",
    "选择已有列或固定值作为起始值，再按顺序添加转换步骤。路径操作只处理单元格文本，"
    "不会读取文件；配方和中间结果不会保存。": (
        "Choose an existing column or fixed value as the starting value, then "
        "add transformations in order. "
        "Path operations process cell text only and never read files; recipes "
        "and intermediate values are not saved."
    ),
    "新增列转换步骤编辑器": "New-column transformation editor",
    "前 20 行预览": "First 20 rows",
    "新增列前二十行预览": "First twenty rows of the new-column preview",
    "起始值": "Starting value",
    "新增列起始值": "Starting value for the new column",
    "新增列已有来源列": "Existing source column for the new column",
    "已有列": "Existing column",
    "行": "Row",
    "新增列错误": "New-column error",
    "确认生成并写入新列": "Create and write the new column",
    "取消新增列": "Cancel new column",
    "主要来源列": "Primary source column",
    "新增列主要来源列": "Primary source column for the new column",
    "添加步骤": "Add step",
    "添加所选转换步骤": "Add the selected transformation step",
    "转换步骤（从上到下执行）": "Transformation steps (run top to bottom)",
    "有序转换步骤": "Ordered transformation steps",
    "步骤设置": "Step settings",
    "文本 · 去除首尾空格": "Text · Trim whitespace",
    "文本 · 转为小写": "Text · Convert to lowercase",
    "文本 · 转为大写": "Text · Convert to uppercase",
    "文本 · 单词首字母大写": "Text · Title case",
    "文本 · 计算长度": "Text · Length",
    "文本 · 替换固定内容": "Text · Replace literal text",
    "文本 · 删除前缀": "Text · Remove prefix",
    "文本 · 删除后缀": "Text · Remove suffix",
    "提取 · 按位置截取": "Extract · Slice by position",
    "提取 · 分隔后取一段": "Extract · Split and take segment",
    "提取 · 取分隔符之前": "Extract · Text before delimiter",
    "提取 · 取分隔符之后": "Extract · Text after delimiter",
    "提取 · 取两个标记之间": "Extract · Text between markers",
    "路径文本 · 文件名": "Path text · File name",
    "路径文本 · 上级路径": "Path text · Parent path",
    "路径文本 · 扩展名": "Path text · Extension",
    "路径文本 · 去掉最后一个扩展名": "Path text · Remove last extension",
    "组合 · 在前面添加": "Combine · Prepend",
    "组合 · 在后面添加": "Combine · Append",
    "数值 · 转为数值": "Number · Convert to number",
    "数值 · 加": "Number · Add",
    "数值 · 减": "Number · Subtract",
    "数值 · 乘": "Number · Multiply",
    "数值 · 除": "Number · Divide",
    "数值 · 绝对值": "Number · Absolute value",
    "数值 · 四舍五入": "Number · Round",
    "空值 · 填充空值": "Missing · Fill missing values",
    "条件 · 根据判断生成值": "Condition · Generate a value",
    "替换为": "Replace with",
    "前缀": "Prefix",
    "后缀": "Suffix",
    "起始位置": "Start position",
    "结束位置": "End position",
    "分隔符": "Delimiter",
    "段序号（从 0 开始）": "Segment index (starting at 0)",
    "起始标记": "Start marker",
    "结束标记": "End marker",
    "添加内容": "Value to add",
    "加数": "Addend",
    "减数": "Subtrahend",
    "乘数": "Multiplier",
    "除数": "Divisor",
    "小数位数": "Decimal places",
    "填充值": "Fill value",
    "判断方式": "Condition",
    "比较对象": "Compare with",
    "条件成立": "When true",
    "条件不成立": "When false",
    "当前步骤值": "Current step value",
    "其他列": "Another column",
    "固定值": "Fixed value",
    "使用上一步的结果": "Use the previous step result",
    "文本": "Text",
    "整数": "Integer",
    "小数": "Decimal",
    "布尔值": "Boolean",
    "空值": "Blank",
    "输入固定值": "Enter a fixed value",
    "添加并选择一个步骤后，可在这里设置参数。\n"
    "不添加步骤时会直接使用起始值。": (
        "Add and select a step to configure it here.\n"
        "With no steps, the starting value is used directly."
    ),
    "遇到错误时停止": "Stop on error",
    "错误行置空": "Set error rows to blank",
    "错误行保留本步输入": "Keep this step's input for error rows",
    "错误处理": "Error handling",
    "留空表示不限": "Leave blank for no limit",
    "大于等于": "Greater than or equal",
    "小于等于": "Less than or equal",
    "以此开头": "Starts with",
    "以此结尾": "Ends with",
    "操作": "Actions",
    "刷新": "Refresh",
    "浏览…": "Browse…",
    "文件夹": "Folder",
    "文件": "File",
    "文件夹读取": "Folder reading",
    "直接下一级文件夹": "Immediate child folders",
    "模式匹配": "Pattern matching",
    "目标类型": "Target type",
    "文件夹和文件": "Folders and files",
    "匹配方式": "Match method",
    "开头是": "Starts with",
    "结尾是": "Ends with",
    "通配符": "Wildcard",
    "正则": "Regular expression",
    "匹配模式": "Match pattern",
    "输入名称匹配模式": "Enter a name pattern",
    "查找范围": "Search scope",
    "直接下一级": "Immediate children",
    "指定层级": "Exact level",
    "所有层级": "All levels",
    "层级": "Level",
    "父路径": "Parent path",
    "输出相对父路径": "Output relative parent path",
    "相对父路径字段名": "Relative-parent field name",
    "直接输入": "Direct input",
    "读取并预览": "Read and preview",
    "导入方式": "Import mode",
    "按 ezqcid 合并列": "Merge columns by ezqcid",
    "追加行": "Append rows",
    "增加空行": "Add blank row",
    "删除选中行": "Delete selected rows",
    "清空导入数据": "Clear import data",
    "写入前需包含 ezqcid": "ezqcid is required before writing",
    "导入预览": "Import preview",
    "前 10 行预览": "First 10 rows",
    "名称": "Name",
    "状态": "Status",
    "目录": "Folder",
    "错误": "Error",
    "日志记录受限": "Limited logging",
    "配置错误": "Configuration error",
    "项目操作错误": "Project action error",
    "项目状态": "Project status",
    "表格操作错误": "Table action error",
    "质控操作错误": "QC action error",
    "质控结果错误": "QC results error",
    "新增列错误": "New-column error",
    "筛选编辑错误": "Filter error",
    "排序草稿错误": "Sort draft error",
    "列设置草稿错误": "Column-settings draft error",
    "正在准备工作区…": "Preparing workspace…",
    "正在加载项目数据…": "Loading project data…",
    "正在加载项目…": "Loading project…",
    "正在加载配置…": "Loading configuration…",
    "正在刷新质控结果…": "Refreshing QC results…",
    "正在读取导入预览…": "Reading import preview…",
    "正在搜索导入预览…": "Searching import preview…",
    "正在写入质控前名单…": "Writing Pre-QC list…",
    "正在计算…": "Calculating…",
    "正在应用…": "Applying…",
    "正在等待筛选保存…": "Waiting for the filter to be saved…",
    "刷新中…": "Refreshing…",
    "导出完成": "Export complete",
    "导出失败": "Export failed",
    "导出已取消": "Export cancelled",
    "配置已加载": "Configuration loaded",
    "项目已加载": "Project loaded",
    "项目导入完成": "Project import complete",
    "质控模块导入完成": "QC module import complete",
    "质控结果已刷新": "QC results refreshed",
    "已应用筛选": "Filter applied",
    "已应用视图": "View applied",
    "未选择记录": "No record selected",
    "没有可显示的质控前名单。": "No Pre-QC list records to display.",
    "没有可显示的质控结果": "No QC results to display.",
    "请输入精确的 ezqcid": "Enter an exact ezqcid",
    "没有匹配的 ezqcid": "No matching ezqcid",
    "请选择要打开的项目": "Select a project to open.",
    "请先打开项目": "Open a project first.",
    "请先启动质控": "Start QC first.",
    "请先选择一行": "Select a row first.",
    "请先读取导入数据": "Read import data first.",
    "请先保存当前修改，再筛选名单": (
        "Save the current changes before filtering the list."
    ),
    "请先保存当前修改，再进入只读模式": (
        "Save the current changes before entering read-only mode."
    ),
    "尚未保存": "Unsaved changes",
    "质量": "Quality",
    "差,一般,好": "Poor,Fair,Good",
    "需要复核": "Needs review",
    "EasyQC 项目配置": "EasyQC project configuration",
    "已登记项目": "Registered project",
    "配置任务状态": "Configuration task status",
    "直接输入名单": "Direct-entry list",
    "为质控前名单新增列": "Add a column to the Pre-QC list",
    "为导入草稿新增列": "Add a column to the import draft",
    "仅在导入单列数据时使用": "Use only when importing one data column",
    "例如 ezqcid 或 scanner_model": "For example: ezqcid or scanner_model",
    "单列字段名": "Single-column field name",
    "可滚动质控名单导入页": "Scrollable QC list import page",
    "导入来源路径": "Import source path",
    "尚未读取导入数据": "No import data has been read",
    "搜索导入预览": "Search import preview",
    "搜索预览": "Search preview",
    "用空格、逗号或换行分隔": "Separate with spaces, commas or new lines",
    "EasyQC 表格工作区": "EasyQC table workspace",
    "表格与视图设置": "Table and view settings",
    "表格视图设置": "Table view settings",
    "表格视图设置错误": "Table view settings error",
    "筛选、排序和列设置草稿": "Filter, sort and column-setting draft",
    "可滚动表格视图草稿": "Scrollable table-view draft",
    "表格列顺序与可见性": "Table column order and visibility",
    "表格操作": "Table actions",
    "表格导出状态": "Table export status",
    "表格横向滚动": "Horizontal table scroll",
    "表格纵向滚动": "Vertical table scroll",
    "表格行数": "Table row count",
    "关闭表格视图设置": "Close table view settings",
    "应用表格视图草稿": "Apply table-view draft",
    "取消表格视图编辑": "Cancel table-view editing",
    "重置全部视图草稿": "Reset all view drafts",
    "取消导出": "Cancel export",
    "搜索表格列": "Search table columns",
    "添加筛选组": "Add filter group",
    "分组筛选编辑器": "Grouped filter editor",
    "筛选组之间的关系": "Relationship between filter groups",
    "启用": "Enabled",
    "无需填写值": "No value required",
    "每行一个值": "One value per line",
    "固定所选列": "Pin selected column",
    "取消固定所选列": "Unpin selected column",
    "上移所选列": "Move selected column up",
    "下移所选列": "Move selected column down",
    "添加排序规则": "Add sort rule",
    "清空排序规则": "Clear sort rules",
    "查找精确 ezqcid": "Find exact ezqcid",
    "项目常量": "Project constants",
    "常量操作错误": "Constant action error",
    "取消编辑": "Cancel editing",
    "质控模块列表": "QC module list",
    "质控模块编辑操作": "QC module edit actions",
    "质控模块质控员": "QC module rater",
    "当前质控模块": "Current QC module",
    "模块条目操作": "Module row actions",
    "添加评分项": "Add rating item",
    "删除评分项": "Delete rating item",
    "添加标签": "Add tag",
    "删除标签": "Delete tag",
    "设置质控名单筛选": "Set QC-list filter",
    "清除质控名单筛选": "Clear QC-list filter",
    "质控启动状态": "QC start status",
    "质控结果表格": "QC results table",
    "质控结果表格工具": "QC results table tools",
    "导出当前质控结果视图": "Export current QC-results view",
    "搜索精确 ezqcid": "Search exact ezqcid",
    "选择导入文件夹": "Select import folder",
    "选择导入文件": "Select import file",
    "名单文件 (*.csv *.xlsx *.xls *.txt *.list)": (
        "List files (*.csv *.xlsx *.xls *.txt *.list)"
    ),
    "选择项目上级目录": "Select project parent folder",
    "选择 EasyQC 项目目录": "Select EasyQC project folder",
    "新建 EasyQC 项目": "Create EasyQC project",
    "项目名称": "Project name",
    "导入质控模块": "Import QC module",
    "导出质控模块": "Export QC module",
    "JSON 文件 (*.json)": "JSON files (*.json)",
    "CSV 文件 (*.csv)": "CSV files (*.csv)",
    "导出当前表格视图": "Export current table view",
    "正在取消导出…": "Cancelling export…",
    "导出位置必须是文件路径": "The export destination must be a file path.",
    "选择列": "Choose columns",
    "选择表格列": "Choose table columns",
    "应用筛选草稿": "Apply filter draft",
    "取消筛选编辑": "Cancel filter editing",
    "重置筛选草稿": "Reset filter draft",
    "筛选表格行": "Filter table rows",
    "应用排序草稿": "Apply sort draft",
    "取消排序编辑": "Cancel sort editing",
    "清空排序草稿": "Clear sort draft",
    "排序表格行": "Sort table rows",
    "应用列设置草稿": "Apply column-settings draft",
    "取消列设置编辑": "Cancel column-settings editing",
    "恢复默认列设置": "Restore default column settings",
    "例如：age + 1": "For example: age + 1",
    "例如：age_next": "For example: age_next",
    "确认生成并写入新列": "Create and write the new column",
    "新增列前十行预览": "First ten rows of the new-column preview",
    "可用于表达式的现有列": "Existing columns available to the expression",
    "预览新增列结果": "Preview new-column result",
    "新增列计算表达式": "New-column expression",
    "取消新增列": "Cancel new column",
    "新增列状态": "New-column status",
    "EasyQC 启动": "EasyQC startup",
    "EasyQC 质控前名单": "EasyQC Pre-QC list",
    "界面语言": "Interface language",
    "质控工作台": "Quality control workspace",
    "筛选组": "Filter group",
    "排序列": "Sort column",
    "排序方向": "Sort direction",
    "数值": "Number",
    "是": "Yes",
    "否": "No",
    "编辑": "Edit",
    "启动失败": "Start failed",
    "未知错误": "Unknown error",
    "质控前名单为空": "The Pre-QC list is empty.",
    "配置任务失败": "Configuration task failed",
    "名单导入任务失败": "List import task failed",
    "筛选不可用": "Filtering unavailable",
    "只读导入预览": "Read-only import preview",
    "项目列表操作": "Project-list actions",
    "项目信息操作": "Project-detail actions",
    "模块列表操作": "Module-list actions",
    "模块编辑操作": "Module-edit actions",
    "质控模块名称": "QC module name",
    "质控模块显示标签": "QC module display label",
    "固定 ezqcid 列": "Pinned ezqcid column",
    "刷新质控结果": "Refresh QC results",
    "正在导入项目…": "Importing project…",
    "正在导入质控模块…": "Importing QC module…",
    "正在导出质控模块…": "Exporting QC module…",
    "请先保存或放弃当前质控修改": (
        "Save or discard the current QC changes first."
    ),
    "该表格操作属于已失效的项目上下文": (
        "This table action belongs to an obsolete project context."
    ),
    "兼容性错误：旧版筛选不可用": (
        "Compatibility error: the legacy filter is unavailable."
    ),
    "已清空导入草稿；质控前名单未改变": (
        "The import draft was cleared; the Pre-QC list was not changed."
    ),
    "该列名包含空格或标点，不能直接用于表达式": (
        "This column name contains spaces or punctuation and cannot be used "
        "directly in an expression."
    ),
    **{
        spec.description_zh: spec.description_en
        for spec in FORMULA_FUNCTION_CATALOG
    },
}


_SOURCE_PATTERNS = (
    (
        re.compile(r"^示例：(?P<value>.*)$"),
        "Example: {value}",
    ),
    (
        re.compile(
            r"^公式语法错误（第 (?P<line>\d+) 行，第 (?P<column>\d+) 列）$"
        ),
        "Formula syntax error (line {line}, column {column})",
    ),
    (
        re.compile(r"^未知列: (?P<value>.*)$"),
        "Unknown column: {value}",
    ),
    (
        re.compile(r"^未知函数: (?P<value>.*)$"),
        "Unknown function: {value}",
    ),
    (
        re.compile(r"^未知快捷模板: (?P<value>.*)$"),
        "Unknown quick template: {value}",
    ),
    (
        re.compile(r"^公式不能超过 (?P<count>[\d,]+) 个字符$"),
        "The formula cannot exceed {count} characters",
    ),
    (
        re.compile(r"^公式节点不能超过 (?P<count>[\d,]+) 个$"),
        "The formula cannot exceed {count} syntax nodes",
    ),
    (
        re.compile(r"^公式嵌套不能超过 (?P<count>[\d,]+) 层$"),
        "The formula cannot exceed {count} levels of nesting",
    ),
    (
        re.compile(r"^公式解析失败: (?P<value>.*)$"),
        "Formula parsing failed: {value}",
    ),
    (
        re.compile(r"^不支持的公式节点: (?P<value>.*)$"),
        "Unsupported formula node: {value}",
    ),
    (
        re.compile(r"^不支持的一元运算符: (?P<value>.*)$"),
        "Unsupported unary operator: {value}",
    ),
    (
        re.compile(r"^不支持的运算符: (?P<value>.*)$"),
        "Unsupported operator: {value}",
    ),
    (
        re.compile(r"^不支持的函数: (?P<value>.*)$"),
        "Unsupported function: {value}",
    ),
    (
        re.compile(r"^函数尚未实现: (?P<value>.*)$"),
        "Formula function is not implemented: {value}",
    ),
    (
        re.compile(r"^(?P<function>[A-Z]+) 未找到分隔符$"),
        "{function} cannot find the delimiter",
    ),
    (
        re.compile(r"^(?P<operator>.+) 需要数值$"),
        "{operator} requires numeric values",
    ),
    (
        re.compile(r"^(?P<operator>.+) 需要布尔值$"),
        "{operator} requires Boolean values",
    ),
    (
        re.compile(r"^(?P<operator>.+) 无法比较$"),
        "{operator} cannot compare these values",
    ),
    (
        re.compile(r"^质控操作：(?P<value>.*)$"),
        "QC actions: {value}",
    ),
    (re.compile(r"^已加载项目：(?P<value>.*)$"), "Project loaded: {value}"),
    (re.compile(r"^质控标签: (?P<value>.*)$"), "QC tag: {value}"),
    (re.compile(r"^(?P<label>.*): 未评$"), "{label}: Not rated"),
    (re.compile(r"^已启动质控：(?P<value>.*)$"), "QC started: {value}"),
    (re.compile(r"^已更新质控名单：(?P<value>.*)$"), "QC list updated: {value}"),
    (re.compile(r"^已生成列：(?P<value>.*)$"), "Column created: {value}"),
    (
        re.compile(r"^已生成质控前名单列：(?P<value>.*)$"),
        "Pre-QC list column created: {value}",
    ),
    (
        re.compile(r"^当前模块筛选匹配 (?P<count>[\d,]+) 条$"),
        "Current module filter matches {count} records",
    ),
    (
        re.compile(r"^全部质控名单 · (?P<count>[\d,]+) 条$"),
        "Entire QC list · {count} records",
    ),
    (re.compile(r"^已筛选：(?P<count>[\d,]+) 条$"), "Filtered: {count} records"),
    (
        re.compile(r"^筛选 \((?P<count>\d+)\)$"),
        "Filter ({count})",
    ),
    (re.compile(r"^排序 \((?P<count>\d+)\)$"), "Sort ({count})"),
    (
        re.compile(r"^列显示 \((?P<visible>\d+)/(?P<total>\d+)\)$"),
        "Columns ({visible}/{total})",
    ),
    (
        re.compile(r"^(?P<matched>[\d,]+) / (?P<total>[\d,]+) 行$"),
        "{matched} / {total} rows",
    ),
    (
        re.compile(r"^第 (?P<start>[\d,]+)–(?P<end>[\d,]+) 行$"),
        "Rows {start}–{end}",
    ),
    (
        re.compile(r"^列 (?P<visible>\d+)/(?P<total>\d+)$"),
        "Columns {visible}/{total}",
    ),
    (
        re.compile(r"^已选原始第 (?P<row>[\d,]+) 行$"),
        "Selected source row {row}",
    ),
    (
        re.compile(r"^排序优先级 (?P<priority>\d+) · (?P<direction>.*)$"),
        "Sort priority {priority} · {direction}",
    ),
    (re.compile(r"^第 (?P<index>\d+) 组$"), "Group {index}"),
    (
        re.compile(r"^已导出 (?P<rows>[\d,]+) 行 · (?P<name>.*)$"),
        "Exported {rows} rows · {name}",
    ),
    (
        re.compile(r"^正在导出 (?P<done>[\d,]+) / (?P<total>[\d,]+) 行…$"),
        "Exporting {done} / {total} rows…",
    ),
    (
        re.compile(r"^筛选条件 (?P<suffix>.+) 的列$"),
        "Column for filter condition {suffix}",
    ),
    (
        re.compile(r"^筛选条件 (?P<suffix>.+) 的判断方式$"),
        "Operator for filter condition {suffix}",
    ),
    (
        re.compile(r"^筛选条件 (?P<suffix>.+) 的值$"),
        "Value for filter condition {suffix}",
    ),
    (
        re.compile(r"^筛选条件 (?P<suffix>.+) 的多个值，每行一个$"),
        "Values for filter condition {suffix}, one per line",
    ),
    (
        re.compile(r"^筛选条件 (?P<suffix>.+) 的起始值$"),
        "Start value for filter condition {suffix}",
    ),
    (
        re.compile(r"^筛选条件 (?P<suffix>.+) 的结束值$"),
        "End value for filter condition {suffix}",
    ),
    (
        re.compile(r"^筛选条件 (?P<suffix>.+) 的日期时间$"),
        "Date and time for filter condition {suffix}",
    ),
    (
        re.compile(r"^筛选条件 (?P<suffix>.+) 的起始日期时间$"),
        "Start date and time for filter condition {suffix}",
    ),
    (
        re.compile(r"^筛选条件 (?P<suffix>.+) 的结束日期时间$"),
        "End date and time for filter condition {suffix}",
    ),
    (
        re.compile(r"^筛选条件 (?P<suffix>.+) 的选项$"),
        "Options for filter condition {suffix}",
    ),
    (
        re.compile(r"^启用筛选条件 (?P<suffix>.+)$"),
        "Enable filter condition {suffix}",
    ),
    (
        re.compile(r"^筛选组 (?P<group>.+) 的组内关系$"),
        "Relationship within filter group {group}",
    ),
    (
        re.compile(r"^筛选组 (?P<group>.+) 错误$"),
        "Filter group {group} error",
    ),
    (
        re.compile(r"^向筛选组 (?P<group>.+) 添加条件$"),
        "Add a condition to filter group {group}",
    ),
    (
        re.compile(r"^删除筛选组 (?P<group>.+)$"),
        "Delete filter group {group}",
    ),
    (
        re.compile(r"^(?P<column>[\s\S]*) {3}· 已固定$"),
        "{column}   · pinned",
    ),
    (
        re.compile(r"^取消登记 (?P<name>.+)？项目文件不会被删除。$"),
        "Unregister {name}? Project files will not be deleted.",
    ),
    (
        re.compile(r"^排序优先级 (?P<priority>\d+) 的列$"),
        "Column for sort priority {priority}",
    ),
    (
        re.compile(r"^排序优先级 (?P<priority>\d+) 的方向$"),
        "Direction for sort priority {priority}",
    ),
    (
        re.compile(r"^列设置中包含未知列：(?P<value>.*)$"),
        "Column settings contain unknown columns: {value}",
    ),
    (
        re.compile(r"^列不能同时隐藏和固定：(?P<value>.*)$"),
        "Columns cannot be both hidden and pinned: {value}",
    ),
    (
        re.compile(r"^列已存在: (?P<value>.*)$"),
        "Column already exists: {value}",
    ),
    (
        re.compile(
            r"^列名 '(?P<value>.*)' 包含空格或标点，不能直接用于表达式$"
        ),
        "Column name '{value}' contains spaces or punctuation and cannot be "
        "used directly in an expression.",
    ),
    (
        re.compile(r"^每个筛选组最多包含 (?P<count>\d+) 个条件$"),
        "Each filter group can contain at most {count} conditions.",
    ),
    (
        re.compile(r"^筛选最多包含 (?P<count>\d+) 个组$"),
        "A filter can contain at most {count} groups.",
    ),
    (
        re.compile(r"^筛选最多包含 (?P<count>\d+) 个条件$"),
        "A filter can contain at most {count} conditions.",
    ),
    (
        re.compile(r"^启动失败：(?P<value>.*)$"),
        "Start failed: {value}",
    ),
    (
        re.compile(r"^质控模块不存在: (?P<value>.*)$"),
        "QC module does not exist: {value}",
    ),
    (
        re.compile(r"^不支持的质控名单筛选阶段: (?P<value>.*)$"),
        "Unsupported QC-list filter stage: {value}",
    ),
    (
        re.compile(r"^不支持的质控名单筛选任务: (?P<value>.*)$"),
        "Unsupported QC-list filter task: {value}",
    ),
    (
        re.compile(r"^已读取 (?P<rows>[\d,]+) 条、(?P<columns>[\d,]+) 列；尚未写入$"),
        "Read {rows} records and {columns} columns; nothing has been written yet.",
    ),
    (
        re.compile(r"^预览搜索完成，匹配 (?P<count>[\d,]+) 条$"),
        "Preview search complete; matched {count} records.",
    ),
    (
        re.compile(r"^已写入质控前名单，共 (?P<count>[\d,]+) 条$"),
        "Wrote {count} records to the Pre-QC list.",
    ),
    (
        re.compile(r"^已生成导入草稿列：(?P<name>.+)；尚未写入$"),
        "Generated import-draft column {name}; nothing has been written yet.",
    ),
    (
        re.compile(r"^已增加 (?P<count>[\d,]+) 行导入草稿；尚未写入$"),
        "Added {count} import-draft row; nothing has been written yet.",
    ),
    (
        re.compile(r"^已删除 (?P<count>[\d,]+) 行导入草稿；尚未写入$"),
        "Deleted {count} import-draft rows; nothing has been written yet.",
    ),
    (
        re.compile(
            r"^匹配 (?P<matched>[\d,]+) · 新增 (?P<added>[\d,]+) · "
            r"冲突 (?P<conflicts>[\d,]+)$"
        ),
        "Matched {matched} · new {added} · conflicts {conflicts}",
    ),
    (
        re.compile(r"^(?P<value>.*)（旧评分值，不在当前选项中）$"),
        "{value} (legacy rating value; not in the current options)",
    ),
    (
        re.compile(
            r"^(?P<label>.*): (?P<value>.*)，旧评分值，不在当前选项中$"
        ),
        "{label}: {value}, legacy rating value; not in the current options",
    ),
    (
        re.compile(r"^移除筛选 (?P<value>.*)$"),
        "Remove filter {value}",
    ),
    (
        re.compile(r"^删除筛选条件 (?P<value>.*)$"),
        "Delete filter condition {value}",
    ),
    (
        re.compile(r"^筛选条件 (?P<value>.*) 错误$"),
        "Filter condition {value} error",
    ),
    (
        re.compile(r"^项目选择，当前项目 (?P<value>.*)$"),
        "Project selection, current project {value}",
    ),
    (
        re.compile(r"^项目目录不存在: (?P<value>.*)$"),
        "Project folder does not exist: {value}",
    ),
    (
        re.compile(r"^无法打开项目目录: (?P<value>.*)$"),
        "Cannot open project folder: {value}",
    ),
    (
        re.compile(r"^项目预览信息不可用：(?P<value>.*)$"),
        "Project preview information is unavailable: {value}",
    ),
    (
        re.compile(r"^上移排序优先级 (?P<priority>\d+)$"),
        "Move sort priority {priority} up",
    ),
    (
        re.compile(r"^下移排序优先级 (?P<priority>\d+)$"),
        "Move sort priority {priority} down",
    ),
    (
        re.compile(r"^删除排序优先级 (?P<priority>\d+)$"),
        "Delete sort priority {priority}",
    ),
    (
        re.compile(r"^没有匹配的 ezqcid：(?P<value>.*)$"),
        "No matching ezqcid: {value}",
    ),
    (
        re.compile(r"^筛选 \((?P<shortcut>[A-Za-z0-9+]+)\)$"),
        "Filter ({shortcut})",
    ),
    (
        re.compile(r"^排序 \((?P<shortcut>[A-Za-z0-9+]+)\)$"),
        "Sort ({shortcut})",
    ),
    (
        re.compile(r"^列显示 \((?P<shortcut>[A-Za-z0-9+]+)\)$"),
        "Columns ({shortcut})",
    ),
    (
        re.compile(r"^新增列 \((?P<shortcut>[A-Za-z0-9+]+)\)$"),
        "New column ({shortcut})",
    ),
    (
        re.compile(r"^查找 \((?P<shortcut>[A-Za-z0-9+]+)\)$"),
        "Find ({shortcut})",
    ),
    (
        re.compile(r"^导出… \((?P<shortcut>[A-Za-z0-9+]+)\)$"),
        "Export… ({shortcut})",
    ),
    (
        re.compile(r"^取消导出 \((?P<shortcut>[A-Za-z0-9+]+)\)$"),
        "Cancel export ({shortcut})",
    ),
)

_HAN_RE = re.compile(r"[\u3400-\u9fff]")
_FORMULA_VALID_RE = re.compile(
    r"^公式有效 · 引用 (?P<count>\d+) 列$"
)
_FORMULA_ROW_ERROR_RE = re.compile(
    r"^公式有 (?P<count>\d+) 行无法处理（(?P<examples>.*)）$"
)
_FORMULA_ROW_EXAMPLE_RE = re.compile(
    r"^索引 (?P<index>[^:]+): (?P<message>.*)$"
)


def _format_message(template: str, values: Mapping[str, Any]) -> str:
    """Format strictly so missing or unexpected variables fail during development."""

    expected = {
        field_name
        for _literal, field_name, _format_spec, _conversion in __import__(
            "string"
        ).Formatter().parse(template)
        if field_name
    }
    received = set(values)
    if expected != received:
        missing = sorted(expected - received)
        unexpected = sorted(received - expected)
        raise KeyError(
            f"Translation arguments mismatch; missing={missing}, unexpected={unexpected}"
        )
    return template.format(**values)


def protect_user_text(obj: QObject, *properties: str) -> None:
    """Mark Qt presentation properties whose values are owned by project data."""

    if not isinstance(obj, QObject):
        raise TypeError("User-text protection requires a QObject")
    requested = {str(name) for name in properties}
    unsupported = sorted(requested - _LOCALIZABLE_PROPERTY_NAMES)
    if unsupported:
        raise ValueError(f"Unsupported user-text properties: {unsupported}")
    existing = obj.property(USER_TEXT_PROPERTIES)
    retained = (
        {str(name) for name in existing}
        if isinstance(existing, (list, tuple, set, frozenset))
        else ({existing} if isinstance(existing, str) and existing else set())
    )
    obj.setProperty(USER_TEXT_PROPERTIES, tuple(sorted(retained | requested)))


def set_translatable_table_columns(
    table: QTableWidget,
    *columns: int,
) -> None:
    """Mark table columns whose cell text is UI diagnostics, not user data."""

    if not isinstance(table, QTableWidget):
        raise TypeError("Translatable table columns require QTableWidget")
    normalized = tuple(int(column) for column in columns)
    if len(set(normalized)) != len(normalized):
        raise ValueError("Translatable table columns cannot contain duplicates")
    if any(
        column < 0 or column >= table.columnCount()
        for column in normalized
    ):
        raise ValueError("Translatable table column is outside the table")
    table.setProperty(
        TRANSLATABLE_TABLE_COLUMNS_PROPERTY,
        normalized,
    )


class LanguageController(QObject):
    """Own the effective Qt language, persistence and presentation bindings."""

    languageChanged = Signal(str)

    def __init__(
        self,
        *,
        settings: QSettings | None = None,
        language: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or QSettings()
        self._roots: list[weakref.ReferenceType[QWidget]] = []
        self._application_ref: weakref.ReferenceType[QApplication] | None = None
        self._qt_translator: QTranslator | None = None
        self._localizing = False
        if language is None:
            stored = str(
                self._settings.value(LANGUAGE_SETTING_KEY, DEFAULT_LANGUAGE)
            )
            self._language = (
                stored if stored in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE
            )
            if stored not in SUPPORTED_LANGUAGES:
                self._settings.setValue(LANGUAGE_SETTING_KEY, self._language)
        else:
            self._validate_language(language)
            self._language = language

    @property
    def language(self) -> str:
        return self._language

    @property
    def supported_languages(self) -> tuple[str, ...]:
        return SUPPORTED_LANGUAGES

    def language_display_name(self, language: str) -> str:
        self._validate_language(language)
        return {"zh_CN": "中文", "en": "English"}[language]

    def tr(self, key: str, **values: Any) -> str:
        try:
            localized = MESSAGES[key][self._language]
        except KeyError as exc:
            raise KeyError(f"Unknown EasyQC translation key: {key}") from exc
        return _format_message(localized, values)

    def set_language(self, language: str) -> None:
        self._validate_language(language)
        if language == self._language:
            return
        self._language = language
        self._settings.setValue(LANGUAGE_SETTING_KEY, language)
        self._settings.sync()
        self._apply_qt_translation()
        self.languageChanged.emit(language)
        self._localize_registered_roots()

    def register_root(self, root: QWidget) -> None:
        if not isinstance(root, QWidget):
            raise TypeError("Localized root must be a QWidget")
        live_roots = [reference() for reference in self._roots]
        if root not in live_roots:
            self._roots.append(weakref.ref(root))
        root.setProperty("easyqcLocalizable", True)
        self.localize_widget_tree(root)

    def unregister_root(self, root: QWidget) -> None:
        self._roots = [
            reference
            for reference in self._roots
            if reference() is not None and reference() is not root
        ]

    def translate_source(self, source: str) -> str:
        """Translate one existing v6 presentation string without touching data."""

        if self._language == "zh_CN" or not source or not _HAN_RE.search(source):
            return source
        valid_match = _FORMULA_VALID_RE.fullmatch(source)
        if valid_match is not None:
            count = int(valid_match.group("count"))
            noun = "column" if count == 1 else "columns"
            return f"Formula valid · {count} {noun} referenced"
        row_error_match = _FORMULA_ROW_ERROR_RE.fullmatch(source)
        if row_error_match is not None:
            count = int(row_error_match.group("count"))
            translated_examples: list[str] = []
            for example in row_error_match.group("examples").split("; "):
                example_match = _FORMULA_ROW_EXAMPLE_RE.fullmatch(example)
                if example_match is None:
                    return source
                translated_examples.append(
                    "index "
                    f"{example_match.group('index')}: "
                    f"{self.translate_source(example_match.group('message'))}"
                )
            noun = "row" if count == 1 else "rows"
            return (
                f"Formula cannot process {count} {noun} "
                f"({'; '.join(translated_examples)})"
            )
        exact = SOURCE_TRANSLATIONS.get(source)
        if exact is not None:
            return exact
        for pattern, template in _SOURCE_PATTERNS:
            match = pattern.fullmatch(source)
            if match:
                return template.format(**match.groupdict())
        # Unknown mixed text may contain project-defined identifiers that happen
        # to equal interface vocabulary. Partial replacement would corrupt that
        # business text, so only catalogued whole strings and structured
        # patterns are translated.
        return source

    def install_application(self, app: QApplication) -> None:
        """Localize newly shown EasyQC dialogs and dynamic labels."""

        if not isinstance(app, QApplication):
            raise TypeError("LanguageController requires a QApplication")
        previous = getattr(app, "_easyqc_language_controller", None)
        if previous is self:
            return
        if isinstance(previous, LanguageController):
            app.removeEventFilter(previous)
        app.installEventFilter(self)
        app._easyqc_language_controller = self
        self._application_ref = weakref.ref(app)
        self._apply_qt_translation()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if self._localizing or self._language == "zh_CN":
            return False
        event_type = event.type()
        if isinstance(watched, QWidget) and self._is_easyqc_widget(watched):
            if event_type == QEvent.Type.Paint and isinstance(
                watched,
                (
                    QAbstractButton,
                    QComboBox,
                    QGroupBox,
                    QLabel,
                    QLineEdit,
                    QPlainTextEdit,
                    QTextEdit,
                ),
            ):
                self._localizing = True
                try:
                    self._localize_object(watched)
                finally:
                    self._localizing = False
            elif event_type == QEvent.Type.Show and watched.isWindow():
                reference = weakref.ref(watched.window())

                def localize_shown_root() -> None:
                    root = reference()
                    if root is not None:
                        self.localize_widget_tree(root)

                QTimer.singleShot(0, localize_shown_root)
        return False

    def localize_widget_tree(self, root: QWidget) -> None:
        """Retranslate supported Qt presentation properties in-place."""

        if self._localizing:
            return
        self._localizing = True
        try:
            self._localize_object(root)
            for child in root.findChildren(QObject):
                self._localize_object(child)
        finally:
            self._localizing = False

    def _localize_registered_roots(self) -> None:
        live: list[weakref.ReferenceType[QWidget]] = []
        for reference in self._roots:
            root = reference()
            if root is None:
                continue
            live.append(reference)
            self.localize_widget_tree(root)
        self._roots = live

    def _localize_object(self, obj: QObject) -> None:
        if isinstance(obj, QWidget):
            self._localize_property(
                obj,
                "accessibleDescription",
                obj.accessibleDescription,
                obj.setAccessibleDescription,
            )
            self._localize_property(
                obj,
                "accessibleName",
                obj.accessibleName,
                obj.setAccessibleName,
            )
            self._localize_property(obj, "toolTip", obj.toolTip, obj.setToolTip)
            self._localize_property(
                obj,
                "windowTitle",
                obj.windowTitle,
                obj.setWindowTitle,
            )
        if isinstance(obj, QLabel):
            self._localize_property(obj, "text", obj.text, obj.setText)
        elif isinstance(obj, QAbstractButton):
            self._localize_property(obj, "text", obj.text, obj.setText)
        if isinstance(obj, QGroupBox):
            self._localize_property(obj, "title", obj.title, obj.setTitle)
        if isinstance(obj, QLineEdit):
            self._localize_property(
                obj,
                "placeholder",
                obj.placeholderText,
                obj.setPlaceholderText,
            )
        if isinstance(obj, (QTextEdit, QPlainTextEdit)):
            self._localize_property(
                obj,
                "placeholder",
                obj.placeholderText,
                obj.setPlaceholderText,
            )
        if isinstance(obj, QAction):
            self._localize_property(obj, "text", obj.text, obj.setText)
            self._localize_property(obj, "toolTip", obj.toolTip, obj.setToolTip)
        if isinstance(obj, QComboBox):
            self._localize_combo(obj)
        if isinstance(obj, QListWidget):
            self._localize_list(obj)
        if isinstance(obj, QTreeWidget):
            self._localize_tree(obj)
        if isinstance(obj, QTabWidget):
            self._localize_tabs(obj)
        if isinstance(obj, QTableWidget):
            self._localize_table_headers(obj)

    def _localize_property(self, obj, name, getter, setter) -> None:
        if self._is_user_text_property(obj, name):
            return
        current = getter()
        if not isinstance(current, str) or not current:
            return
        sources = getattr(obj, "_easyqc_i18n_sources", None)
        if sources is None:
            sources = {}
            setattr(obj, "_easyqc_i18n_sources", sources)
        source, last = sources.get(name, (current, None))
        if last is not None and current != last:
            source = current
        localized = self.translate_source(source)
        if localized != current:
            setter(localized)
        sources[name] = (source, localized)

    def _localize_combo(self, combo: QComboBox) -> None:
        if self._is_user_text_property(combo, "items"):
            return
        sources = getattr(combo, "_easyqc_i18n_item_sources", {})
        updated: dict[int, tuple[str, str]] = {}
        for index in range(combo.count()):
            current = combo.itemText(index)
            source, last = sources.get(index, (current, None))
            if last is not None and current != last:
                source = current
            payload = combo.itemData(index, Qt.ItemDataRole.UserRole)
            localized = (
                source
                if self._matches_user_payload(source, payload)
                else self.translate_source(source)
            )
            if localized != current:
                combo.setItemText(index, localized)
            updated[index] = (source, localized)
        combo._easyqc_i18n_item_sources = updated

    def _localize_list(self, widget: QListWidget) -> None:
        if self._is_user_text_property(widget, "items"):
            return
        for index in range(widget.count()):
            self._localize_flat_item(widget.item(index))

    def _localize_tree(self, widget: QTreeWidget) -> None:
        if self._is_user_text_property(widget, "items"):
            return

        def visit(item) -> None:
            self._localize_tree_item(item, columns=item.columnCount())
            for child_index in range(item.childCount()):
                visit(item.child(child_index))

        for index in range(widget.topLevelItemCount()):
            visit(widget.topLevelItem(index))
        header = widget.headerItem()
        if header is not None:
            self._localize_tree_item(header, columns=header.columnCount())

    def _localize_flat_item(self, item) -> None:
        if item is None:
            return
        role = int(Qt.ItemDataRole.UserRole) + 197
        sources = item.data(role)
        if not isinstance(sources, dict):
            sources = {}
        current = item.text()
        source, last = sources.get(0, (current, None))
        if last is not None and current != last:
            source = current
        payload = item.data(Qt.ItemDataRole.UserRole)
        localized = (
            source
            if self._matches_user_payload(source, payload)
            else self.translate_source(source)
        )
        if localized != current:
            item.setText(localized)
        updated_sources = {0: (source, localized)}
        accessible_current = item.data(Qt.ItemDataRole.AccessibleTextRole)
        if isinstance(accessible_current, str) and accessible_current:
            accessible_source, accessible_last = sources.get(
                "accessible",
                (accessible_current, None),
            )
            if (
                accessible_last is not None
                and accessible_current != accessible_last
            ):
                accessible_source = accessible_current
            accessible_localized = (
                accessible_source
                if self._matches_user_payload(accessible_source, payload)
                else self.translate_source(accessible_source)
            )
            if accessible_localized != accessible_current:
                item.setData(
                    Qt.ItemDataRole.AccessibleTextRole,
                    accessible_localized,
                )
            updated_sources["accessible"] = (
                accessible_source,
                accessible_localized,
            )
        item.setData(role, updated_sources)

    def _localize_tree_item(
        self,
        item: QTreeWidgetItem,
        *,
        columns: int,
    ) -> None:
        role = int(Qt.ItemDataRole.UserRole) + 197
        sources = item.data(0, role)
        if not isinstance(sources, dict):
            sources = {}
        updated = {}
        for column in range(columns):
            current = item.text(column)
            source, last = sources.get(column, (current, None))
            if last is not None and current != last:
                source = current
            localized = self.translate_source(source)
            if localized != current:
                item.setText(column, localized)
            updated[column] = (source, localized)
        item.setData(0, role, updated)

    def _localize_tabs(self, tabs: QTabWidget) -> None:
        if self._is_user_text_property(tabs, "tabs"):
            return
        sources = getattr(tabs, "_easyqc_i18n_tab_sources", {})
        updated = {}
        for index in range(tabs.count()):
            current = tabs.tabText(index)
            source, last = sources.get(index, (current, None))
            if last is not None and current != last:
                source = current
            localized = self.translate_source(source)
            if localized != current:
                tabs.setTabText(index, localized)
            updated[index] = (source, localized)
        tabs._easyqc_i18n_tab_sources = updated

    def _localize_table_headers(self, table: QTableWidget) -> None:
        if self._is_user_text_property(table, "headers"):
            return
        translatable_columns = table.property(
            TRANSLATABLE_TABLE_COLUMNS_PROPERTY
        )
        translatable = (
            {
                int(column)
                for column in translatable_columns
                if isinstance(column, int)
            }
            if isinstance(translatable_columns, (list, tuple))
            else set()
        )
        for index in range(table.columnCount()):
            item = table.horizontalHeaderItem(index)
            if item is not None:
                self._localize_flat_item(item)
            if index in translatable:
                for row in range(table.rowCount()):
                    self._localize_flat_item(table.item(row, index))
        for index in range(table.rowCount()):
            item = table.verticalHeaderItem(index)
            if item is not None:
                self._localize_flat_item(item)

    @staticmethod
    def _is_user_text_property(obj: QObject, name: str) -> bool:
        retained = obj.property(USER_TEXT_PROPERTIES)
        if isinstance(retained, str):
            return retained == name
        return isinstance(retained, (list, tuple, set, frozenset)) and name in retained

    @staticmethod
    def _matches_user_payload(source: str, payload: Any) -> bool:
        return payload is not None and source == str(payload)

    @staticmethod
    def _validate_language(language: str) -> None:
        if language not in SUPPORTED_LANGUAGES:
            raise ValueError(f"Unsupported EasyQC UI language: {language!r}")

    def _apply_qt_translation(self) -> None:
        """Align standard Qt dialog buttons with the EasyQC language."""

        application = (
            self._application_ref()
            if self._application_ref is not None
            else None
        )
        if application is None:
            return
        if self._qt_translator is not None:
            application.removeTranslator(self._qt_translator)
            self._qt_translator.deleteLater()
            self._qt_translator = None
        translations = Path(
            QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        )
        translator = QTranslator(self)
        if translator.load(str(translations / f"qtbase_{self._language}.qm")):
            application.installTranslator(translator)
            self._qt_translator = translator
        else:
            translator.deleteLater()

    @staticmethod
    def _is_easyqc_widget(widget: QWidget) -> bool:
        current: QWidget | None = widget
        while current is not None:
            if bool(current.property("easyqcLocalizable")):
                return True
            parent = current.parentWidget()
            current = parent
        return False


def get_or_create_language_controller(
    app: QApplication | None = None,
) -> LanguageController:
    """Return the process-local controller backed by the normal QSettings scope."""

    application = app or QApplication.instance()
    if not isinstance(application, QApplication):
        raise RuntimeError("A QApplication is required before localization")
    existing = getattr(application, "_easyqc_language_controller", None)
    if isinstance(existing, LanguageController):
        return existing
    controller = LanguageController()
    controller.install_application(application)
    return controller


def translate_ui_text(source: str) -> str:
    """Translate native-dialog arguments through the active controller."""

    application = QApplication.instance()
    controller = (
        getattr(application, "_easyqc_language_controller", None)
        if isinstance(application, QApplication)
        else None
    )
    return (
        controller.translate_source(source)
        if isinstance(controller, LanguageController)
        else source
    )


__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGE_SETTING_KEY",
    "LanguageController",
    "MESSAGES",
    "SOURCE_TRANSLATIONS",
    "SUPPORTED_LANGUAGES",
    "TRANSLATABLE_TABLE_COLUMNS_PROPERTY",
    "USER_TEXT_PROPERTIES",
    "get_or_create_language_controller",
    "protect_user_text",
    "set_translatable_table_columns",
    "translate_ui_text",
]
