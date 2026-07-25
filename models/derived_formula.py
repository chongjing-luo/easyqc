"""Immutable request contract for one ephemeral EasyQC Formula column."""

from __future__ import annotations

from dataclasses import dataclass


MAX_FORMULA_LENGTH = 4_096


@dataclass(frozen=True, slots=True)
class DerivedColumnFormula:
    """Request one ordinary column from one restricted formula expression."""

    name: str
    expression: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("新增列名必须是文本")
        if not isinstance(self.expression, str):
            raise TypeError("公式必须是文本")
        normalized_name = self.name.strip()
        normalized_expression = self.expression.strip()
        if not normalized_name:
            raise ValueError("新增列名不能为空")
        if normalized_name != self.name:
            raise ValueError("新增列名不能包含首尾空格")
        if not normalized_expression:
            raise ValueError("公式不能为空")
        if len(normalized_expression) > MAX_FORMULA_LENGTH:
            raise ValueError(f"公式不能超过 {MAX_FORMULA_LENGTH:,} 个字符")
        object.__setattr__(self, "expression", normalized_expression)


__all__ = ["DerivedColumnFormula", "MAX_FORMULA_LENGTH"]
