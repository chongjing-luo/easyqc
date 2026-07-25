"""Immutable request contract for deterministic folder/file discovery."""

from __future__ import annotations

from dataclasses import dataclass
import keyword


_TARGET_KINDS = frozenset({"directory", "file", "both"})
_MATCH_KINDS = frozenset(
    {"starts_with", "ends_with", "contains", "wildcard", "regex"}
)
_SCOPES = frozenset({"direct", "exact", "all"})


def _field_name(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label}必须是文本")
    normalized = value.strip()
    if (
        not normalized
        or not normalized.isidentifier()
        or keyword.iskeyword(normalized)
    ):
        raise ValueError(f"{label}必须是有效字段名")
    return normalized


@dataclass(frozen=True, slots=True)
class FolderMatchRequest:
    """Describe a safe basename match and its one/two-column output."""

    target_kind: str
    match_kind: str
    pattern: str
    scope: str
    exact_depth: int
    item_column: str
    parent_column: str | None = None

    def __post_init__(self) -> None:
        if self.target_kind not in _TARGET_KINDS:
            raise ValueError(f"不支持的目标类型: {self.target_kind}")
        if self.match_kind not in _MATCH_KINDS:
            raise ValueError(f"不支持的匹配方式: {self.match_kind}")
        if not isinstance(self.pattern, str) or not self.pattern:
            raise ValueError("匹配模式不能为空")
        if self.scope not in _SCOPES:
            raise ValueError(f"不支持的查找范围: {self.scope}")
        if isinstance(self.exact_depth, bool) or not isinstance(
            self.exact_depth, int
        ):
            raise TypeError("精确层级必须是整数")
        if self.exact_depth < 1:
            raise ValueError("精确层级必须大于或等于 1")

        item_column = _field_name(self.item_column, "匹配项字段名")
        parent_column = (
            None
            if self.parent_column is None
            else _field_name(self.parent_column, "相对父路径字段名")
        )
        if parent_column == item_column:
            raise ValueError("匹配项和相对父路径字段名不能相同")
        object.__setattr__(self, "item_column", item_column)
        object.__setattr__(self, "parent_column", parent_column)


__all__ = ["FolderMatchRequest"]
