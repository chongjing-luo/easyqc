# EasyQC 新增列与 EasyQC Formula

## 1. 为什么不是 SQL、Python 或完整 VBA

新增列需要同时覆盖数值计算、文本清理、路径拆分、多列组合、条件判断和固定值。SQL 对非技术用户过重，Python 会把任意代码执行引入普通表格操作，完整 VBA 又需要庞大语言运行时和对象模型。

EasyQC Formula 选择中间路线：一个受限的 Excel/VBA 风格表达式。它熟悉、可组合，但不是 VBA 本身：

- 没有语句、赋值、循环或对象；
- 没有 Python、SQL、正则表达式或用户函数；
- 没有文件、网络、进程或 GUI 访问；
- 不使用 `eval` 或 `exec`；
- 只允许登记过的操作符和函数。

因此安全边界不是“屏蔽几个危险词”，而是解析器根本无法表示任意代码。

## 2. 最小用法

在表格工作区点击“新增列”：

1. 输入不存在的新列名；
2. 编写一个 Formula；
3. 查看前 20 行预览、引用输入、结果和逐行错误；
4. 解决所有未处理错误；
5. 提交完整数据计算；
6. 最终值成为普通列。

示例：

```text
IF([site] = "A", UPPER(TEXTBEFORE([filename], "_")), "OTHER")
```

可选的开头 `=` 会被接受，因此 Excel 用户也可以写：

```text
=ROUND(([age] - [baseline_age]) / 12, 1)
```

## 3. 基本语法

### 3.1 列引用

列名写在方括号中：

```text
[filename]
[parent path]
[baseline_age]
```

若列名本身含 `]`，使用 `]]` 转义。Formula 只读取当前页面允许的源列；引用不存在或不在该数据域的列会在预览前失败。

### 3.2 字面量

文本使用双引号：

```text
"fixed value"
```

文本中的双引号写成两个双引号。数字可直接书写。空值应通过 `BLANK()` 表示，而不是依赖特殊 Python 值。

### 3.3 操作符

| 类别 | 语法 |
|---|---|
| 算术 | `+`、`-`、`*`、`/` |
| 比较 | `=`、`<>`、`<`、`<=`、`>`、`>=` |
| 逻辑 | `AND`、`OR`、`NOT` |
| 文本连接 | `&` |

使用括号明确优先级。例如：

```text
([age] >= 18) AND ([site] <> "excluded")
```

## 4. 多列与固定值

Formula 不限制只选一个源变量。直接在同一表达式中引用多个列：

```text
[parent path] & "/" & [filename]
```

```text
ROUND(([age] - [baseline_age]) / 12, 1)
```

不依赖任何源列时，写一个常量表达式：

```text
"baseline"
```

它会为每一行产生相同值。导入源没有 `easyqcid` 时，也可以先从名称提取，或在确定只有一行时生成固定身份；最终写入总名单仍必须通过唯一性校验。

## 5. 23 个允许函数

| 函数 | 典型用途 |
|---|---|
| `ABS(value)` | 绝对值 |
| `BLANK()` | 明确空值 |
| `COALESCE(a, b, ...)` | 取第一个非空值 |
| `EXTENSION(path)` | 文件扩展名 |
| `FIND(text, within)` | 查找文本位置 |
| `IF(condition, yes, no)` | 条件分支 |
| `IFERROR(value, fallback)` | 显式处理逐行错误 |
| `ISBLANK(value)` | 判断空值 |
| `LEFT(text, count)` | 左侧字符 |
| `LEN(text)` | 文本长度 |
| `LOWER(text)` | 小写 |
| `MID(text, start, count)` | 中间字符 |
| `PARENTPATH(path)` | 父路径 |
| `PATHNAME(path)` | 路径末端名称 |
| `RIGHT(text, count)` | 右侧字符 |
| `ROUND(value, digits)` | 四舍五入 |
| `STEM(path)` | 去除扩展名的名称 |
| `SUBSTITUTE(text, old, new)` | 文本替换 |
| `TEXTAFTER(text, delimiter)` | 分隔符之后文本 |
| `TEXTBEFORE(text, delimiter)` | 分隔符之前文本 |
| `TRIM(text)` | 清除首尾和多余空白 |
| `UPPER(text)` | 大写 |
| `VALUE(text)` | 文本转数值 |

函数名不区分大小写，但文档建议使用大写，以便与列和文本区分。函数参数数量和类型由注册表校验；未知函数不会退化为 Python 调用。

## 6. 常用配方

### 6.1 从文件名提取站点与身份

```text
UPPER(TEXTBEFORE([filename], "_"))
```

### 6.2 构造完整路径

```text
[root] & "/" & [relative_parent] & "/" & [filename]
```

若中间列可能为空，可用条件避免多余分隔符：

```text
IF(ISBLANK([relative_parent]), [root] & "/" & [filename], [root] & "/" & [relative_parent] & "/" & [filename])
```

### 6.3 文件名、stem 与后缀

```text
PATHNAME([image_path])
STEM([image_path])
EXTENSION([image_path])
```

### 6.4 清理标签

```text
UPPER(TRIM(SUBSTITUTE([raw_label], "-", "_")))
```

### 6.5 数值换算

```text
ROUND(([age_months] / 12), 1)
```

### 6.6 缺失值回退

```text
COALESCE([preferred_path], [fallback_path], "missing")
```

### 6.7 捕获显式计算错误

```text
IFERROR(VALUE([age_text]), BLANK())
```

`IFERROR` 表示用户明确接受一个回退规则。系统不会自动把错误替换为空值。

## 7. 预览和提交

预览默认展示前 20 行，并把每行的引用输入、计算结果和错误并列显示。它解决两个问题：

- 用户能看到表达式如何解释真实列值；
- 一行失败不会只给出模糊的“公式无效”。

预览不是从当前筛选出来的 20 行物化结果。提交时在该页面的完整数据域重新计算，并核对源状态未过期。存在未由 `IFERROR` 处理的逐行错误时，整个新增列提交被阻止；原表和目标列保持不变。

## 8. Formula 的保存语义

EasyQC 只保存计算后的普通值：

```text
Formula/AST/模板状态  --不保存-->
最终列值              --保存-->
```

这意味着：

- 以后修改源列不会自动重算派生列；
- 项目不需要长期兼容旧公式解释器；
- 结果 CSV 可被其他软件直接读取；
- 若需要复现公式，用户应在项目方法文档中另行记录表达式。

现有目标列不能被覆盖。若要改变派生列，应先通过明确的列删除流程删除旧列，再创建新列；这避免一次 Formula 误操作静默改写原始数据。

## 9. 三个页面的数据边界

### 9.1 质控名单导入

输入是刚刚导入的完整草稿，而不是当前项目总名单；提交结果留在草稿，直到执行导入写入。

### 9.2 质控前名单

输入是总名单普通列；提交结果持久化到 `easyqc_all.csv`。

### 9.3 质控结果

界面可以从总名单普通列新增列并写回总名单，但不会提供 `<module>.<rater>.<field>` 等评分派生列作为 Formula 输入。否则一次结果刷新就可能改变公式输入，从而形成循环或不可追溯依赖。

## 10. 资源与安全限制

单个表达式限制为：

- 最长 4,096 个字符；
- 最多 256 个 AST 节点；
- 最大嵌套深度 32。

这些限制同时控制错误复杂度和资源消耗。Formula 不是通用编程语言，也不是查看器命令：它不能启动 Shell，即使“跨项目设置”允许查看器使用 `shell=True`，也不会扩大 Formula 权限。

## 11. 失败行为

| 情况 | 行为 |
|---|---|
| 目标列已存在 | 提交前拒绝，不覆盖 |
| 引用列不存在 | 解析/绑定失败 |
| 未知函数或参数错误 | 明确指出函数合同问题 |
| 某些行转换失败 | 在预览中标记；未处理则阻止提交 |
| 表在后台发生变化 | 旧任务结果被判为过期，不写回 |
| 表达式超长/过深/节点过多 | 在执行完整数据前拒绝 |

## 12. 相关文档

- [质控名单与表格工作区](05-qc-list-and-table-workspace.md)
- [常量、模块与外部查看器](07-constants-modules-and-viewers.md)
- [可靠性、性能与平台](09-reliability-performance-and-platforms.md)
