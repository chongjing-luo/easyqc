from __future__ import annotations

import operator as scalar_operator
import re
from functools import reduce
from typing import Any, Callable

import pandas as pd

from core.expression_parser import ExpressionError, ExpressionParser
from core.formula_engine import FormulaEngine
from core.formula_parser import FormulaError
from models.column_recipe import ColumnRecipe, RecipeStep, RecipeValue
from models.derived_formula import DerivedColumnFormula
from utils.logger import log_warning


class TableTransformError(ValueError):
    """Raised when a table transform operation is invalid."""


_RecipeOperation = Callable[
    [pd.DataFrame, pd.Series, RecipeStep],
    tuple[pd.Series, pd.Series],
]


def _string_series(series: pd.Series) -> pd.Series:
    """Convert non-missing values to strings without turning nulls into text."""

    return series.astype("string")


def _false_mask(index: pd.Index) -> pd.Series:
    return pd.Series(False, index=index, dtype=bool)


def _broadcast(value: object, index: pd.Index) -> pd.Series:
    return pd.Series([value] * len(index), index=index, dtype=object)


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
    RECIPE_PARAMETER_SCHEMA = {
        "trim": (frozenset(), frozenset()),
        "lower": (frozenset(), frozenset()),
        "upper": (frozenset(), frozenset()),
        "title": (frozenset(), frozenset()),
        "length": (frozenset(), frozenset()),
        "replace_literal": (frozenset({"old", "new"}), frozenset()),
        "remove_prefix": (frozenset({"prefix"}), frozenset()),
        "remove_suffix": (frozenset({"suffix"}), frozenset()),
        "slice": (frozenset(), frozenset({"start", "end"})),
        "split_take": (frozenset({"delimiter", "index"}), frozenset()),
        "before": (frozenset({"delimiter"}), frozenset()),
        "after": (frozenset({"delimiter"}), frozenset()),
        "between": (frozenset({"start", "end"}), frozenset()),
        "path_name": (frozenset(), frozenset()),
        "path_parent": (frozenset(), frozenset()),
        "path_suffix": (frozenset(), frozenset()),
        "path_stem": (frozenset(), frozenset()),
        "prepend": (frozenset({"value"}), frozenset()),
        "append": (frozenset({"value"}), frozenset()),
        "to_number": (frozenset(), frozenset()),
        "add": (frozenset({"value"}), frozenset()),
        "subtract": (frozenset({"value"}), frozenset()),
        "multiply": (frozenset({"value"}), frozenset()),
        "divide": (frozenset({"value"}), frozenset()),
        "absolute": (frozenset(), frozenset()),
        "round": (frozenset(), frozenset({"digits"})),
        "fill_missing": (frozenset({"value"}), frozenset()),
        "conditional": (
            frozenset({"operator", "when_true", "when_false"}),
            frozenset({"compare_to"}),
        ),
    }

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

    def derive_column_from_formula(
        self,
        df: pd.DataFrame,
        request: DerivedColumnFormula,
    ) -> pd.DataFrame:
        """Return a detached table with one strict EasyQC Formula column."""

        if not isinstance(df, pd.DataFrame):
            raise TableTransformError("新增列来源必须是表格")
        if not isinstance(request, DerivedColumnFormula):
            raise TableTransformError("新增列请求必须是 DerivedColumnFormula")
        if request.name in df.columns:
            raise TableTransformError(f"新列已存在: {request.name}")
        try:
            evaluation = FormulaEngine().evaluate(df, request.expression)
            evaluation.raise_for_errors()
        except FormulaError as exc:
            raise TableTransformError(str(exc)) from exc
        if (
            len(evaluation.values) != len(df)
            or not evaluation.values.index.equals(df.index)
        ):
            raise TableTransformError("公式结果与原表行数或顺序不一致")
        result = df.copy()
        result[request.name] = evaluation.values
        return result

    def derive_column_from_recipe(
        self,
        df: pd.DataFrame,
        recipe: ColumnRecipe,
    ) -> pd.DataFrame:
        """Return a detached table with one safe, row-aligned derived column.

        The operation has no side effects. Structural or row-level failures are
        reported as ``TableTransformError`` and never execute user-provided
        Python, SQL, regular expressions, shell commands, or file access.
        """

        if not isinstance(df, pd.DataFrame):
            raise TableTransformError("新增列来源必须是表格")
        if not isinstance(recipe, ColumnRecipe):
            raise TableTransformError("新增列请求必须是 ColumnRecipe")
        if recipe.name in df.columns:
            raise TableTransformError(f"新列已存在: {recipe.name}")

        current = self.recipe_initial_values(df, recipe)
        original_index = df.index.copy()
        for step_number, step in enumerate(recipe.steps, start=1):
            self._validate_recipe_step(step, step_number)
            step_input = current.copy()
            try:
                transformed, invalid = self._execute_recipe_step(df, current, step)
            except TableTransformError:
                raise
            except Exception as exc:
                raise TableTransformError(
                    f"第 {step_number} 步“{step.operation}”执行失败: {exc}"
                ) from exc

            if not isinstance(transformed, pd.Series) or len(transformed) != len(df):
                raise TableTransformError(
                    f"第 {step_number} 步“{step.operation}”没有返回逐行结果"
                )
            if not transformed.index.equals(original_index):
                raise TableTransformError(
                    f"第 {step_number} 步“{step.operation}”改变了行索引"
                )
            invalid = pd.Series(invalid, index=original_index).fillna(True).astype(bool)
            invalid_count = int(invalid.sum())
            if invalid_count:
                if step.on_error == "fail":
                    raise TableTransformError(
                        f"第 {step_number} 步“{step.operation}”有 "
                        f"{invalid_count} 行无法处理"
                    )
                transformed = transformed.copy()
                if step.on_error == "blank":
                    transformed.loc[invalid] = pd.NA
                else:
                    transformed.loc[invalid] = step_input.loc[invalid]
            current = transformed

        if len(current) != len(df) or not current.index.equals(original_index):
            raise TableTransformError("新增列结果与原表行数或顺序不一致")
        result = df.copy()
        result[recipe.name] = current
        return result

    def recipe_initial_values(
        self,
        df: pd.DataFrame,
        recipe: ColumnRecipe,
    ) -> pd.Series:
        """Resolve one safe recipe start into a detached, row-aligned Series."""

        if not isinstance(df, pd.DataFrame):
            raise TableTransformError("新增列来源必须是表格")
        if not isinstance(recipe, ColumnRecipe):
            raise TableTransformError("新增列请求必须是 ColumnRecipe")

        value = recipe.initial_value
        if value.kind == "column":
            column = str(value.value)
            if column not in df.columns:
                raise TableTransformError(f"未知来源列: {column}")
            return df[column].copy()
        if value.kind == "literal":
            return _broadcast(value.value, df.index)
        raise TableTransformError("新增列起始值只能选择已有列或固定值")

    def _validate_recipe_step(self, step: RecipeStep, step_number: int) -> None:
        if not isinstance(step, RecipeStep):
            raise TableTransformError(f"第 {step_number} 步不是有效的转换步骤")
        schema = self.RECIPE_PARAMETER_SCHEMA.get(step.operation)
        if schema is None:
            raise TableTransformError(f"不支持的新增列操作: {step.operation}")
        required, optional = schema
        supplied = set(step.parameters)
        missing = sorted(required - supplied)
        unknown = sorted(supplied - required - optional)
        if missing:
            raise TableTransformError(
                f"第 {step_number} 步“{step.operation}”缺少参数: {missing}"
            )
        if unknown:
            raise TableTransformError(
                f"第 {step_number} 步“{step.operation}”包含未知参数: {unknown}"
            )

    def _execute_recipe_step(
        self,
        df: pd.DataFrame,
        current: pd.Series,
        step: RecipeStep,
    ) -> tuple[pd.Series, pd.Series]:
        operation = getattr(self, f"_recipe_{step.operation}", None)
        if operation is None:
            raise TableTransformError(f"不支持的新增列操作: {step.operation}")
        return operation(df, current, step)

    def _recipe_trim(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        return _string_series(current).str.strip(), _false_mask(current.index)

    def _recipe_lower(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        return _string_series(current).str.lower(), _false_mask(current.index)

    def _recipe_upper(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        return _string_series(current).str.upper(), _false_mask(current.index)

    def _recipe_title(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        return _string_series(current).str.title(), _false_mask(current.index)

    def _recipe_length(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        return _string_series(current).str.len(), _false_mask(current.index)

    def _recipe_replace_literal(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df
        old = self._text_parameter(step, "old", allow_empty=False)
        new = self._text_parameter(step, "new", allow_empty=True)
        return (
            _string_series(current).str.replace(old, new, regex=False),
            _false_mask(current.index),
        )

    def _recipe_remove_prefix(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df
        prefix = self._text_parameter(step, "prefix", allow_empty=False)
        values = _string_series(current)
        matches = values.str.startswith(prefix, na=False)
        return values.where(~matches, values.str.slice(start=len(prefix))), _false_mask(current.index)

    def _recipe_remove_suffix(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df
        suffix = self._text_parameter(step, "suffix", allow_empty=False)
        values = _string_series(current)
        matches = values.str.endswith(suffix, na=False)
        return values.where(~matches, values.str.slice(stop=-len(suffix))), _false_mask(current.index)

    def _recipe_slice(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df
        start = self._optional_int_parameter(step, "start")
        end = self._optional_int_parameter(step, "end")
        return _string_series(current).str.slice(start=start, stop=end), _false_mask(current.index)

    def _recipe_split_take(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df
        delimiter = self._text_parameter(step, "delimiter", allow_empty=False)
        index = self._int_parameter(step, "index")
        values = _string_series(current)
        transformed = values.str.split(delimiter, regex=False).str[index]
        invalid = values.notna() & transformed.isna()
        return transformed, invalid

    def _recipe_before(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df
        delimiter = self._text_parameter(step, "delimiter", allow_empty=False)
        values = _string_series(current)
        present = values.str.contains(delimiter, regex=False, na=False)
        transformed = values.str.split(delimiter, n=1, regex=False).str[0]
        return transformed, values.notna() & ~present

    def _recipe_after(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df
        delimiter = self._text_parameter(step, "delimiter", allow_empty=False)
        values = _string_series(current)
        present = values.str.contains(delimiter, regex=False, na=False)
        transformed = values.str.split(delimiter, n=1, regex=False).str[1]
        return transformed, values.notna() & ~present

    def _recipe_between(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df
        start = self._text_parameter(step, "start", allow_empty=False)
        end = self._text_parameter(step, "end", allow_empty=False)
        values = _string_series(current)
        after_start = values.str.split(start, n=1, regex=False).str[1]
        transformed = after_start.str.split(end, n=1, regex=False).str[0]
        start_present = values.str.contains(start, regex=False, na=False)
        end_present = after_start.str.contains(end, regex=False, na=False)
        invalid = values.notna() & ~(start_present & end_present)
        return transformed, invalid

    def _recipe_path_name(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        normalized = _string_series(current).str.replace("\\", "/", regex=False)
        return normalized.str.rsplit("/", n=1).str[-1], _false_mask(current.index)

    def _recipe_path_parent(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        normalized = _string_series(current).str.replace("\\", "/", regex=False)
        has_parent = normalized.str.contains("/", regex=False, na=False)
        parent = normalized.str.rsplit("/", n=1).str[0].where(has_parent, "")
        return parent, _false_mask(current.index)

    def _recipe_path_suffix(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        name, _ = self._recipe_path_name(
            pd.DataFrame(index=current.index),
            current,
            RecipeStep.create("path_name"),
        )
        parts = name.str.rpartition(".")
        has_suffix = parts[0].ne("") & parts[1].eq(".") & parts[2].ne("")
        suffix = ("." + parts[2]).where(has_suffix, "")
        return suffix, _false_mask(current.index)

    def _recipe_path_stem(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        name, _ = self._recipe_path_name(
            pd.DataFrame(index=current.index),
            current,
            RecipeStep.create("path_name"),
        )
        parts = name.str.rpartition(".")
        has_suffix = parts[0].ne("") & parts[1].eq(".") & parts[2].ne("")
        return name.where(~has_suffix, parts[0]), _false_mask(current.index)

    def _recipe_prepend(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        operand = self._resolve_recipe_value(df, current, step, "value")
        return _string_series(operand) + _string_series(current), _false_mask(current.index)

    def _recipe_append(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        operand = self._resolve_recipe_value(df, current, step, "value")
        return _string_series(current) + _string_series(operand), _false_mask(current.index)

    def _recipe_to_number(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        transformed = pd.to_numeric(current, errors="coerce")
        return transformed, current.notna() & transformed.isna()

    def _recipe_add(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        return self._numeric_binary(df, current, step, lambda left, right: left + right)

    def _recipe_subtract(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        return self._numeric_binary(df, current, step, lambda left, right: left - right)

    def _recipe_multiply(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        return self._numeric_binary(df, current, step, lambda left, right: left * right)

    def _recipe_divide(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        return self._numeric_binary(
            df,
            current,
            step,
            lambda left, right: left / right,
            reject_zero=True,
        )

    def _recipe_absolute(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df, step
        numeric = pd.to_numeric(current, errors="coerce")
        return numeric.abs(), current.notna() & numeric.isna()

    def _recipe_round(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        del df
        digits = self._optional_int_parameter(step, "digits")
        if digits is None:
            digits = 0
        numeric = pd.to_numeric(current, errors="coerce")
        return numeric.round(digits), current.notna() & numeric.isna()

    def _recipe_fill_missing(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        value = self._resolve_recipe_value(df, current, step, "value")
        return current.where(current.notna(), value), _false_mask(current.index)

    def _recipe_conditional(
        self, df: pd.DataFrame, current: pd.Series, step: RecipeStep
    ) -> tuple[pd.Series, pd.Series]:
        operator = self._text_parameter(step, "operator", allow_empty=False)
        allowed = {
            "eq",
            "ne",
            "gt",
            "ge",
            "lt",
            "le",
            "contains",
            "starts_with",
            "ends_with",
            "is_missing",
            "not_missing",
        }
        if operator not in allowed:
            raise TableTransformError(f"不支持的条件操作符: {operator}")
        invalid = _false_mask(current.index)
        if operator in {"is_missing", "not_missing"}:
            if "compare_to" in step.parameters:
                raise TableTransformError(f"条件 {operator} 不接受 compare_to")
            mask = current.isna()
            if operator == "not_missing":
                mask = ~mask
        else:
            if "compare_to" not in step.parameters:
                raise TableTransformError(f"条件 {operator} 缺少 compare_to")
            compare_to = self._resolve_recipe_value(df, current, step, "compare_to")
            mask, invalid = self._conditional_mask(current, compare_to, operator)
        mask = pd.Series(mask, index=current.index).fillna(False).astype(bool)
        when_true = self._resolve_recipe_value(df, current, step, "when_true")
        when_false = self._resolve_recipe_value(df, current, step, "when_false")
        return when_true.where(mask, when_false), invalid

    def _numeric_binary(
        self,
        df: pd.DataFrame,
        current: pd.Series,
        step: RecipeStep,
        operation: Callable[[pd.Series, pd.Series], pd.Series],
        *,
        reject_zero: bool = False,
    ) -> tuple[pd.Series, pd.Series]:
        operand = self._resolve_recipe_value(df, current, step, "value")
        left = pd.to_numeric(current, errors="coerce")
        right = pd.to_numeric(operand, errors="coerce")
        invalid = (current.notna() & left.isna()) | (operand.notna() & right.isna())
        if reject_zero:
            invalid |= right.eq(0) & left.notna()
            right = right.mask(right.eq(0))
        return operation(left, right), invalid

    def _conditional_mask(
        self,
        current: pd.Series,
        compare_to: pd.Series,
        operator: str,
    ) -> tuple[pd.Series, pd.Series]:
        comparisons = {
            "eq": pd.Series.eq,
            "ne": pd.Series.ne,
            "gt": pd.Series.gt,
            "ge": pd.Series.ge,
            "lt": pd.Series.lt,
            "le": pd.Series.le,
        }
        if operator in comparisons:
            valid = current.notna() & compare_to.notna()
            result = _false_mask(current.index)
            invalid = _false_mask(current.index)
            positions = valid.to_numpy().nonzero()[0]
            if len(positions):
                try:
                    compared = comparisons[operator](
                        current.iloc[positions],
                        compare_to.iloc[positions],
                    )
                    result.iloc[positions] = compared.to_numpy(dtype=bool)
                except (ArithmeticError, TypeError, ValueError):
                    scalar_comparisons = {
                        "eq": scalar_operator.eq,
                        "ne": scalar_operator.ne,
                        "gt": scalar_operator.gt,
                        "ge": scalar_operator.ge,
                        "lt": scalar_operator.lt,
                        "le": scalar_operator.le,
                    }
                    compare = scalar_comparisons[operator]
                    for position in positions:
                        try:
                            result.iloc[position] = bool(
                                compare(
                                    current.iloc[position],
                                    compare_to.iloc[position],
                                )
                            )
                        except (ArithmeticError, TypeError, ValueError):
                            invalid.iloc[position] = True
            return result, invalid
        left = _string_series(current)
        right = _string_series(compare_to)
        if operator == "contains":
            return (
                pd.Series(
                    [
                        False if pd.isna(lval) or pd.isna(rval) else rval in lval
                        for lval, rval in zip(left.array, right.array)
                    ],
                    index=current.index,
                ),
                _false_mask(current.index),
            )
        if operator == "starts_with":
            return (
                pd.Series(
                    [
                        False
                        if pd.isna(lval) or pd.isna(rval)
                        else lval.startswith(rval)
                        for lval, rval in zip(left.array, right.array)
                    ],
                    index=current.index,
                ),
                _false_mask(current.index),
            )
        return (
            pd.Series(
                [
                    False if pd.isna(lval) or pd.isna(rval) else lval.endswith(rval)
                    for lval, rval in zip(left.array, right.array)
                ],
                index=current.index,
            ),
            _false_mask(current.index),
        )

    def _resolve_recipe_value(
        self,
        df: pd.DataFrame,
        current: pd.Series,
        step: RecipeStep,
        parameter: str,
    ) -> pd.Series:
        value = step.parameters.get(parameter)
        if not isinstance(value, RecipeValue):
            raise TableTransformError(f"参数 {parameter} 必须选择当前值、列或固定值")
        if value.kind == "current":
            return current.copy()
        if value.kind == "column":
            column = str(value.value)
            if column not in df.columns:
                raise TableTransformError(f"未知引用列: {column}")
            return df[column].copy()
        return _broadcast(value.value, current.index)

    def _text_parameter(
        self,
        step: RecipeStep,
        name: str,
        *,
        allow_empty: bool,
    ) -> str:
        value = step.parameters.get(name)
        if not isinstance(value, str):
            raise TableTransformError(f"参数 {name} 必须是文本")
        if not allow_empty and not value:
            raise TableTransformError(f"参数 {name} 不能为空")
        return value

    def _int_parameter(self, step: RecipeStep, name: str) -> int:
        value = step.parameters.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TableTransformError(f"参数 {name} 必须是整数")
        return value

    def _optional_int_parameter(self, step: RecipeStep, name: str) -> int | None:
        if name not in step.parameters or step.parameters[name] is None:
            return None
        return self._int_parameter(step, name)

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
