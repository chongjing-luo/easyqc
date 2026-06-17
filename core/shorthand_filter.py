"""Shorthand filter parser — convert short one-line expressions to operation dicts.

Lets users write simple expressions instead of verbose JSON:
  filter:  "age > 30 and sex == 'F'"     → filter_rows (expression condition)
  sort:    "score desc"                   → sort_rows
  select:  "ezqcid, age, score"           → select_columns
  derive:  "total = a + b"                → derive_column
  drop:    "motion, age"                  → drop_columns
  rename:  "motion -> motion_fd"          → rename_columns

The output is a list of operation dicts consumed by TableTransformEngine.apply.
The engine and ExpressionParser are NOT modified — this module only produces
the same operation dicts the JSON path would, just from friendlier syntax.

Layer: core. No tkinter, no GUI dependency.
"""

from __future__ import annotations


class ShorthandParseError(ValueError):
    """Raised when a shorthand expression is malformed."""


def _clean(s: str | None) -> str:
    """Return stripped string, or '' if None/whitespace-only."""
    return (s or "").strip()


def parse_shorthand(
    filter_expr: str | None = None,
    sort_expr: str | None = None,
    select_expr: str | None = None,
    derive_expr: str | None = None,
    drop_expr: str | None = None,
    rename_expr: str | None = None,
) -> list[dict]:
    """Convert shorthand expressions to an ordered list of operation dicts.

    Order: filter → derive → sort → select → drop → rename
    (filter first narrows rows; derive adds cols needed by sort/select;
    sort before select so selection sees sorted order; drop/rename last.)
    Empty/whitespace inputs are skipped.
    """
    operations: list[dict] = []

    filt = _clean(filter_expr)
    if filt:
        operations.append({
            "operation": "filter_rows",
            "conditions": [{"expression": filt}],
            "logic": "and",
        })

    der = _clean(derive_expr)
    if der:
        if "=" not in der:
            raise ShorthandParseError(
                f"derive 表达式必须含 '='(格式: 新列名 = 表达式): {der!r}"
            )
        name, _, expr = der.partition("=")
        name = name.strip()
        expr = expr.strip()
        if not name or not expr:
            raise ShorthandParseError(f"derive 表达式不完整: {der!r}")
        operations.append({
            "operation": "derive_column",
            "name": name,
            "expression": expr,
        })

    srt = _clean(sort_expr)
    if srt:
        parts = srt.split()
        column = parts[0]
        ascending = True
        if len(parts) >= 2:
            ascending = parts[1].lower() not in ("desc", "descending", "降序")
        operations.append({
            "operation": "sort_rows",
            "sort_keys": [{"column": column, "ascending": ascending}],
        })

    sel = _clean(select_expr)
    if sel:
        columns = [c.strip() for c in sel.split(",") if c.strip()]
        if columns:
            operations.append({"operation": "select_columns", "columns": columns})

    drp = _clean(drop_expr)
    if drp:
        columns = [c.strip() for c in drp.split(",") if c.strip()]
        if columns:
            operations.append({"operation": "drop_columns", "columns": columns})

    rnm = _clean(rename_expr)
    if rnm:
        mapping: dict[str, str] = {}
        for pair in rnm.split(","):
            pair = pair.strip()
            if not pair:
                continue
            if "->" not in pair:
                raise ShorthandParseError(
                    f"rename 表达式必须含 '->'(格式: 旧名 -> 新名): {pair!r}"
                )
            old, _, new = pair.partition("->")
            old = old.strip()
            new = new.strip()
            if not old or not new:
                raise ShorthandParseError(f"rename 表达式不完整: {pair!r}")
            mapping[old] = new
        if mapping:
            operations.append({"operation": "rename_columns", "mapping": mapping})

    return operations


def shorthand_to_string(
    filter_expr: str | None = None,
    sort_expr: str | None = None,
    select_expr: str | None = None,
    derive_expr: str | None = None,
    drop_expr: str | None = None,
    rename_expr: str | None = None,
) -> str:
    """Serialize shorthand fields to a compact string for select_filter persistence.

    Format: "filter: ...; sort: ...; derive: ..."
    Reverse: parse_shorthand_string() below.
    """
    parts = []
    if _clean(filter_expr):
        parts.append(f"filter: {filter_expr}")
    if _clean(derive_expr):
        parts.append(f"derive: {derive_expr}")
    if _clean(sort_expr):
        parts.append(f"sort: {sort_expr}")
    if _clean(select_expr):
        parts.append(f"select: {select_expr}")
    if _clean(drop_expr):
        parts.append(f"drop: {drop_expr}")
    if _clean(rename_expr):
        parts.append(f"rename: {rename_expr}")
    return "; ".join(parts)


def parse_shorthand_string(s: str) -> dict[str, str]:
    """Parse a persisted shorthand string back to individual field values.

    Inverse of shorthand_to_string. Returns a dict with keys
    filter/sort/select/derive/drop/rename (only non-empty).
    """
    result: dict[str, str] = {}
    if not s or not s.strip():
        return result
    for part in s.split(";"):
        part = part.strip()
        if ":" not in part:
            continue
        key, _, value = part.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key in ("filter", "sort", "select", "derive", "drop", "rename") and value:
            result[key] = value
    return result


__all__ = [
    "parse_shorthand",
    "shorthand_to_string",
    "parse_shorthand_string",
    "ShorthandParseError",
]
