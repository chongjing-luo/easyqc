from __future__ import annotations

import re
from functools import reduce
from typing import Any

import pandas as pd

from core.expression_parser import ExpressionError, ExpressionParser
from utils.logger import log_warning


class TableTransformError(ValueError):
    """Raised when a table transform operation is invalid."""


_LEGACY_SELECT_RE = re.compile(
    r"^\s*select\s+\*\s+from\s+df(?:\s+where\s+(?P<where>.+))?\s*$",
    re.IGNORECASE,
)
_LEGACY_CONDITION_RE = re.compile(
    r"^\s*(?P<column>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<operator>>=|<=|!=|<>|=|>|<)\s*"
    r"(?P<value>'[^']*'|\"[^\"]*\"|true|false|null|none|-?\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)


def legacy_select_filter_to_operations(query: str) -> list[dict[str, Any]] | None:
    """Convert a narrow legacy ``SELECT * FROM df`` filter into structured operations.

    This is a compatibility parser, not a SQL execution engine. It intentionally
    supports only simple single-table filters that old EasyQC projects stored in
    ``select_filter`` fields.
    """

    query = query.strip()
    if not query.lower().startswith("select"):
        return None
    if ";" in query:
        raise TableTransformError("旧 SELECT 筛选只支持单条语句，不支持分号或多语句")

    match = _LEGACY_SELECT_RE.match(query)
    if not match:
        raise TableTransformError("旧 SELECT 筛选只支持 SELECT * FROM df 和简单 WHERE 条件")

    where_clause = match.group("where")
    if not where_clause:
        return []
    if re.search(r"\b(or|join|group|order|limit|union|having|select|from)\b", where_clause, re.IGNORECASE):
        raise TableTransformError("旧 SELECT 筛选只支持 AND 连接的简单比较条件")

    conditions = []
    for raw_condition in re.split(r"\s+and\s+", where_clause, flags=re.IGNORECASE):
        condition_match = _LEGACY_CONDITION_RE.match(raw_condition)
        if not condition_match:
            raise TableTransformError(f"不支持的旧 SELECT 条件: {raw_condition}")

        operator = condition_match.group("operator")
        conditions.append(
            {
                "column": condition_match.group("column"),
                "operator": "==" if operator == "=" else ("!=" if operator == "<>" else operator),
                "value": _legacy_literal_to_value(condition_match.group("value")),
            }
        )

    return [{"operation": "filter_rows", "conditions": conditions}]


def _legacy_literal_to_value(value: str) -> Any:
    value = value.strip()
    if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
        return value[1:-1]

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    if "." in value:
        return float(value)
    return int(value)


class TableTransformEngine:
    ALLOWED_MERGE_HOW = {"left", "right", "inner", "outer"}
    ALLOWED_AGGREGATIONS = {"count", "mean", "sum", "min", "max"}

    def __init__(
        self,
        expression_parser: ExpressionParser | None = None,
        max_rows: int | None = None,
        max_columns: int | None = None,
    ) -> None:
        self.expression_parser = expression_parser or ExpressionParser()
        self.max_rows = max_rows
        self.max_columns = max_columns

    def apply(self, df: pd.DataFrame, operations: list[dict[str, Any]]) -> pd.DataFrame:
        result = df.copy()
        for operation in operations:
            op_type = operation.get("operation") or operation.get("type")
            if op_type == "select_columns":
                result = self.select_columns(
                    result,
                    operation.get("columns", []),
                    include_rest=operation.get("include_rest", False),
                )
            elif op_type == "filter_rows":
                result = self.filter_rows(
                    result,
                    operation.get("conditions", []),
                    logic=operation.get("logic", "and"),
                )
            elif op_type == "sort_rows":
                result = self.sort_rows(result, operation.get("sort_keys", []))
            elif op_type == "derive_column":
                result = self.derive_column(result, operation["name"], operation["expression"])
            elif op_type == "rename_columns":
                result = self.rename_columns(result, operation.get("mapping", {}))
            elif op_type == "drop_columns":
                result = self.drop_columns(result, operation.get("columns", []))
            elif op_type == "merge_tables":
                result = self.merge_tables(
                    result,
                    operation["right"],
                    on=operation.get("on", []),
                    how=operation.get("how", "left"),
                )
            elif op_type == "aggregate":
                result = self.aggregate(
                    result,
                    group_by=operation.get("group_by", []),
                    metrics=operation.get("metrics", {}),
                )
            else:
                raise TableTransformError(f"不支持的表格转换操作: {op_type}")
            result = self.limit_output(result)
        return self.limit_output(result)

    def limit_output(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df
        if self.max_rows is not None and len(result) > self.max_rows:
            log_warning(f"表格行数超过限制 {self.max_rows}，已截断输出", "TableTransformEngine")
            result = result.iloc[: self.max_rows, :]
        if self.max_columns is not None and len(result.columns) > self.max_columns:
            log_warning(f"表格列数超过限制 {self.max_columns}，已截断输出", "TableTransformEngine")
            result = result.iloc[:, : self.max_columns]
        return result.copy()

    def select_columns(
        self,
        df: pd.DataFrame,
        columns: list[str],
        include_rest: bool = False,
    ) -> pd.DataFrame:
        self._require_columns(df, columns)
        selected = list(columns)
        if include_rest:
            selected.extend([column for column in df.columns if column not in selected])
        return df.loc[:, selected].copy()

    def filter_rows(
        self,
        df: pd.DataFrame,
        conditions: list[dict[str, Any]],
        logic: str = "and",
    ) -> pd.DataFrame:
        if logic not in {"and", "or"}:
            raise TableTransformError(f"不支持的筛选逻辑: {logic}")
        if not conditions:
            return df.copy()

        masks = [self._condition_to_mask(df, condition) for condition in conditions]
        combiner = (lambda left, right: left & right) if logic == "and" else (lambda left, right: left | right)
        mask = reduce(combiner, masks)
        return df.loc[mask].copy()

    def sort_rows(self, df: pd.DataFrame, sort_keys: list[dict[str, Any]]) -> pd.DataFrame:
        if not sort_keys:
            return df.copy()
        columns = [item["column"] for item in sort_keys]
        self._require_columns(df, columns)
        ascending = [bool(item.get("ascending", True)) for item in sort_keys]
        return df.sort_values(by=columns, ascending=ascending).reset_index(drop=True)

    def derive_column(self, df: pd.DataFrame, name: str, expression: str) -> pd.DataFrame:
        if not name:
            raise TableTransformError("派生列名不能为空")
        result = df.copy()
        result[name] = self.expression_parser.evaluate(expression, result)
        return result

    def rename_columns(self, df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
        self._require_columns(df, list(mapping.keys()))
        if any(not new_name for new_name in mapping.values()):
            raise TableTransformError("新列名不能为空")
        return df.rename(columns=mapping).copy()

    def drop_columns(self, df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
        self._require_columns(df, columns)
        return df.drop(columns=columns).copy()

    def merge_tables(
        self,
        left: pd.DataFrame,
        right: pd.DataFrame,
        on: list[str],
        how: str = "left",
    ) -> pd.DataFrame:
        if how not in self.ALLOWED_MERGE_HOW:
            raise TableTransformError(f"不支持的合并方式: {how}")
        self._require_columns(left, on)
        self._require_columns(right, on)
        return pd.merge(left, right, on=on, how=how)

    def aggregate(
        self,
        df: pd.DataFrame,
        group_by: list[str],
        metrics: dict[str, list[str]],
    ) -> pd.DataFrame:
        self._require_columns(df, group_by)
        self._require_columns(df, list(metrics.keys()))

        for functions in metrics.values():
            unknown = [func for func in functions if func not in self.ALLOWED_AGGREGATIONS]
            if unknown:
                raise TableTransformError(f"不支持的聚合函数: {unknown}")

        result = df.groupby(group_by, dropna=False).agg(metrics).reset_index()
        result.columns = [
            column if isinstance(column, str) else "_".join(str(part) for part in column if part)
            for column in result.columns
        ]
        return result

    def _condition_to_mask(self, df: pd.DataFrame, condition: dict[str, Any]) -> pd.Series:
        if "expression" in condition:
            mask = self.expression_parser.evaluate(condition["expression"], df)
            if mask.dtype != bool:
                raise TableTransformError("筛选表达式必须返回布尔结果")
            return mask

        column = condition.get("column")
        operator = condition.get("operator", condition.get("op"))
        value = condition.get("value")

        if not column or not operator:
            raise TableTransformError("筛选条件必须包含 column 和 operator")
        self._require_columns(df, [column])

        series = df[column]
        if operator in {"==", "eq"}:
            return series == value
        if operator in {"!=", "ne"}:
            return series != value
        if operator in {">", "gt"}:
            return series > value
        if operator in {">=", "ge"}:
            return series >= value
        if operator in {"<", "lt"}:
            return series < value
        if operator in {"<=", "le"}:
            return series <= value
        if operator == "in":
            return series.isin(value)
        if operator in {"not_in", "not in"}:
            return ~series.isin(value)
        if operator == "contains":
            return series.astype(str).str.contains(str(value), regex=False, na=False)
        if operator == "startswith":
            return series.astype(str).str.startswith(str(value), na=False)
        if operator == "endswith":
            return series.astype(str).str.endswith(str(value), na=False)
        if operator == "isna":
            return series.isna()
        if operator == "notna":
            return series.notna()

        raise TableTransformError(f"不支持的筛选操作符: {operator}")

    def _require_columns(self, df: pd.DataFrame, columns: list[str]) -> None:
        missing = [column for column in columns if column not in df.columns]
        if missing:
            raise TableTransformError(f"列不存在: {missing}")


__all__ = [
    "ExpressionError",
    "ExpressionParser",
    "TableTransformEngine",
    "TableTransformError",
    "legacy_select_filter_to_operations",
]
