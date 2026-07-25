"""Pure quick-template renderers for EasyQC Formula."""

from __future__ import annotations

import math


def column_reference(column: str) -> str:
    """Return one exact escaped bracketed column reference."""

    if not isinstance(column, str):
        raise TypeError("列名必须是文本")
    if not column:
        raise ValueError("列名不能为空")
    if "\n" in column or "\r" in column:
        raise ValueError("列名不能包含换行")
    return f"[{column.replace(']', ']]')}]"


def formula_literal(value: object) -> str:
    """Return one locale-stable scalar formula literal."""

    if value is None:
        return "BLANK()"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return f'"{value.replace(chr(34), chr(34) * 2)}"'
    if type(value) is int:
        return str(value)
    if type(value) is float and math.isfinite(value):
        return repr(value)
    raise TypeError("固定值必须是文本、数值、布尔值或空值")


def render_fixed_formula(value: object) -> str:
    """Render a constant formula."""

    return formula_literal(value)


def render_concatenate_formula(
    left_column: str,
    separator: str,
    right_column: str,
) -> str:
    """Render two-column text concatenation."""

    if not isinstance(separator, str):
        raise TypeError("连接分隔文本必须是文本")
    return (
        f"{column_reference(left_column)} & {formula_literal(separator)} & "
        f"{column_reference(right_column)}"
    )


def render_extract_formula(
    column: str,
    delimiter: str,
    *,
    position: str,
) -> str:
    """Render extraction before or after one fixed delimiter."""

    if not isinstance(delimiter, str):
        raise TypeError("分隔符必须是文本")
    if not delimiter:
        raise ValueError("分隔符不能为空")
    functions = {"before": "TEXTBEFORE", "after": "TEXTAFTER"}
    function = functions.get(position)
    if function is None:
        raise ValueError("提取位置必须是 before 或 after")
    return (
        f"{function}({column_reference(column)}, "
        f"{formula_literal(delimiter)})"
    )


def render_conditional_formula(
    column: str,
    operator: str,
    compare_value: object,
    when_true: object,
    when_false: object,
) -> str:
    """Render one simple column-to-literal conditional formula."""

    if operator not in {"=", "<>", "<", "<=", ">", ">="}:
        raise ValueError("条件比较符无效")
    return (
        f"IF({column_reference(column)} {operator} "
        f"{formula_literal(compare_value)}, {formula_literal(when_true)}, "
        f"{formula_literal(when_false)})"
    )


def render_cleanup_formula(column: str, *, operation: str) -> str:
    """Render one common text cleanup/case formula."""

    reference = column_reference(column)
    formulas = {
        "trim": f"TRIM({reference})",
        "upper": f"UPPER({reference})",
        "lower": f"LOWER({reference})",
        "trim_upper": f"UPPER(TRIM({reference}))",
        "trim_lower": f"LOWER(TRIM({reference}))",
    }
    try:
        return formulas[operation]
    except KeyError as exc:
        raise ValueError("文本清理方式无效") from exc


def _numeric_operator(operator: str) -> str:
    if operator not in {"+", "-", "*", "/"}:
        raise ValueError("数值运算符无效")
    return operator


def render_numeric_columns_formula(
    left_column: str,
    operator: str,
    right_column: str,
) -> str:
    """Render arithmetic between two existing columns."""

    return (
        f"{column_reference(left_column)} {_numeric_operator(operator)} "
        f"{column_reference(right_column)}"
    )


def render_numeric_fixed_formula(
    left_column: str,
    operator: str,
    value: int | float,
) -> str:
    """Render arithmetic between one column and one fixed number."""

    if type(value) not in {int, float} or (
        type(value) is float and not math.isfinite(value)
    ):
        raise TypeError("固定数值必须是有限整数或小数")
    return (
        f"{column_reference(left_column)} {_numeric_operator(operator)} "
        f"{formula_literal(value)}"
    )


__all__ = [
    "column_reference",
    "formula_literal",
    "render_cleanup_formula",
    "render_concatenate_formula",
    "render_conditional_formula",
    "render_extract_formula",
    "render_fixed_formula",
    "render_numeric_columns_formula",
    "render_numeric_fixed_formula",
]
