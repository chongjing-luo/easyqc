"""Immutable public function metadata for EasyQC Formula."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Mapping


@dataclass(frozen=True, slots=True)
class FormulaFunctionSpec:
    """Describe one closed formula function for Core and GUI consumers."""

    name: str
    category: str
    min_args: int
    max_args: int
    signature: str
    description_zh: str
    description_en: str
    example: str

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.upper():
            raise ValueError("公式函数名必须是大写 ASCII 文本")
        if not 0 <= self.min_args <= self.max_args <= 16:
            raise ValueError(f"函数 {self.name} 的参数范围无效")
        for field_name in (
            "category",
            "signature",
            "description_zh",
            "description_en",
            "example",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"函数 {self.name} 的 {field_name} 不能为空")


def _spec(
    name: str,
    category: str,
    arity: int | tuple[int, int],
    signature: str,
    description_zh: str,
    description_en: str,
    example: str,
) -> FormulaFunctionSpec:
    minimum, maximum = arity if isinstance(arity, tuple) else (arity, arity)
    return FormulaFunctionSpec(
        name=name,
        category=category,
        min_args=minimum,
        max_args=maximum,
        signature=signature,
        description_zh=description_zh,
        description_en=description_en,
        example=example,
    )


FORMULA_FUNCTION_CATALOG: Final = (
    _spec(
        "ABS",
        "numeric",
        1,
        "ABS(number)",
        "返回数值的绝对值。",
        "Return the absolute value of a number.",
        "ABS([number])",
    ),
    _spec(
        "BLANK",
        "control",
        0,
        "BLANK()",
        "产生一个空值。",
        "Produce a blank value.",
        "BLANK()",
    ),
    _spec(
        "COALESCE",
        "control",
        (1, 16),
        "COALESCE(value, ...)",
        "返回第一个非空候选值；错误不视为空值。",
        "Return the first nonblank candidate; errors are not blanks.",
        'COALESCE([value], "unknown")',
    ),
    _spec(
        "EXTENSION",
        "path",
        1,
        "EXTENSION(path)",
        "提取路径末项的最后一个扩展名，不访问文件系统。",
        "Extract the final suffix from path text without filesystem access.",
        "EXTENSION([path])",
    ),
    _spec(
        "FIND",
        "text",
        2,
        "FIND(find_text, within_text)",
        "查找固定文本并返回从 1 开始的位置。",
        "Find fixed text and return its one-based position.",
        'FIND("_", [filename])',
    ),
    _spec(
        "IF",
        "control",
        3,
        "IF(condition, when_true, when_false)",
        "根据逐行布尔条件选择一个值。",
        "Choose a value from a row-wise boolean condition.",
        'IF([score] >= 3, "pass", "review")',
    ),
    _spec(
        "IFERROR",
        "control",
        2,
        "IFERROR(expression, fallback)",
        "只在表达式产生逐行错误时使用备用值。",
        "Use a fallback only where the expression has a row error.",
        "IFERROR(VALUE([raw]), BLANK())",
    ),
    _spec(
        "ISBLANK",
        "control",
        1,
        "ISBLANK(value)",
        "判断值是否为空。",
        "Test whether a value is blank.",
        "ISBLANK([value])",
    ),
    _spec(
        "LEFT",
        "text",
        2,
        "LEFT(text, count)",
        "从左侧提取固定数量的字符。",
        "Take a fixed number of characters from the left.",
        "LEFT([filename], 3)",
    ),
    _spec(
        "LEN",
        "text",
        1,
        "LEN(text)",
        "返回文本的 Unicode 字符数。",
        "Return the Unicode character count of text.",
        "LEN([filename])",
    ),
    _spec(
        "LOWER",
        "text",
        1,
        "LOWER(text)",
        "将文本转换为小写。",
        "Convert text to lowercase.",
        "LOWER([label])",
    ),
    _spec(
        "MID",
        "text",
        3,
        "MID(text, start, count)",
        "从从 1 开始的位置提取固定数量的字符。",
        "Take characters from a one-based start position.",
        "MID([filename], 5, 3)",
    ),
    _spec(
        "PARENTPATH",
        "path",
        1,
        "PARENTPATH(path)",
        "提取路径文本的父路径，同时支持两种分隔符。",
        "Extract parent path text using either path separator.",
        "PARENTPATH([path])",
    ),
    _spec(
        "PATHNAME",
        "path",
        1,
        "PATHNAME(path)",
        "提取路径文本的最后一项。",
        "Extract the final component from path text.",
        "PATHNAME([path])",
    ),
    _spec(
        "RIGHT",
        "text",
        2,
        "RIGHT(text, count)",
        "从右侧提取固定数量的字符。",
        "Take a fixed number of characters from the right.",
        "RIGHT([filename], 4)",
    ),
    _spec(
        "ROUND",
        "numeric",
        2,
        "ROUND(number, digits)",
        "将数值舍入到固定的小数位数。",
        "Round a number to a fixed number of digits.",
        "ROUND([number], 2)",
    ),
    _spec(
        "STEM",
        "path",
        1,
        "STEM(path)",
        "去掉路径末项的最后一个扩展名。",
        "Remove the final suffix from the path name.",
        "STEM([path])",
    ),
    _spec(
        "SUBSTITUTE",
        "text",
        3,
        "SUBSTITUTE(text, old_text, new_text)",
        "按字面值替换全部固定文本，不使用正则表达式。",
        "Replace all fixed text literally without regular expressions.",
        'SUBSTITUTE([label], "-", "_")',
    ),
    _spec(
        "TEXTAFTER",
        "text",
        2,
        "TEXTAFTER(text, delimiter)",
        "提取第一个固定分隔符之后的文本。",
        "Return text after the first fixed delimiter.",
        'TEXTAFTER([filename], "_")',
    ),
    _spec(
        "TEXTBEFORE",
        "text",
        2,
        "TEXTBEFORE(text, delimiter)",
        "提取第一个固定分隔符之前的文本。",
        "Return text before the first fixed delimiter.",
        'TEXTBEFORE([filename], "_")',
    ),
    _spec(
        "TRIM",
        "text",
        1,
        "TRIM(text)",
        "移除文本首尾空白。",
        "Remove leading and trailing whitespace from text.",
        "TRIM([label])",
    ),
    _spec(
        "UPPER",
        "text",
        1,
        "UPPER(text)",
        "将文本转换为大写。",
        "Convert text to uppercase.",
        "UPPER([label])",
    ),
    _spec(
        "VALUE",
        "numeric",
        1,
        "VALUE(value)",
        "使用固定小数点规则将文本转换为数值。",
        "Convert text to a number using locale-stable decimal syntax.",
        "VALUE([raw])",
    ),
)

FORMULA_FUNCTION_NAMES: Final = frozenset(
    spec.name for spec in FORMULA_FUNCTION_CATALOG
)
FORMULA_FUNCTION_ARITY: Final[Mapping[str, tuple[int, int]]] = MappingProxyType(
    {
        spec.name: (spec.min_args, spec.max_args)
        for spec in FORMULA_FUNCTION_CATALOG
    }
)


__all__ = [
    "FORMULA_FUNCTION_ARITY",
    "FORMULA_FUNCTION_CATALOG",
    "FORMULA_FUNCTION_NAMES",
    "FormulaFunctionSpec",
]
