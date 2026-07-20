"""Complete-data filtering/sorting for the read-only table workspace.

Rendering limits are deliberately absent from ``apply_state``. A row window is
created only after the complete ordered position result and counts exist.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd
from pandas.api import types as ptypes

from models.table_view_state import (
    ColumnKind,
    ColumnProfile,
    ColumnViewState,
    FilterCondition,
    RowWindow,
    TableViewResult,
    TableViewState,
)


class TableViewError(ValueError):
    """Raised when a workspace state cannot be applied safely."""


class QcIdentityError(TableViewError):
    """Raised when a selected row cannot identify exactly one QC object."""


_VALUELESS_OPERATORS = {"isna", "notna"}
_COMMON_OPERATORS = {"==", "!=", "isna", "notna", "in", "not_in"}
_OPERATORS_BY_KIND = {
    ColumnKind.TEXT: _COMMON_OPERATORS | {"contains", "startswith", "endswith"},
    ColumnKind.NUMBER: _COMMON_OPERATORS | {">", ">=", "<", "<=", "between"},
    ColumnKind.BOOLEAN: {"==", "!=", "isna", "notna"},
    ColumnKind.DATETIME: _COMMON_OPERATORS | {">", ">=", "<", "<=", "between"},
}


class TableViewService:
    """Own a stable source frame and derive validated view results from it."""

    def __init__(self, source: pd.DataFrame, distinct_value_limit: int = 100) -> None:
        if not isinstance(source, pd.DataFrame):
            raise TypeError("表格工作区输入必须是 pandas DataFrame")
        if source.columns.has_duplicates:
            duplicates = source.columns[source.columns.duplicated()].tolist()
            raise TableViewError(f"表格包含重复列名: {duplicates}")
        self._source = source.copy(deep=True)
        self._distinct_value_limit = distinct_value_limit
        self._profiles = self._profile_columns()
        self._profile_by_name = {profile.name: profile for profile in self._profiles}
        self._identity_positions = self._build_identity_positions()
        self._identity_counts = {
            identity: 1 if isinstance(positions, int) else len(positions)
            for identity, positions in self._identity_positions.items()
        }

    @property
    def profiles(self) -> tuple[ColumnProfile, ...]:
        return self._profiles

    @property
    def source_total(self) -> int:
        return len(self._source)

    def default_state(self, page_size: int = 200) -> TableViewState:
        order = tuple(str(column) for column in self._source.columns)
        pinned = ("ezqcid",) if "ezqcid" in order else ()
        return TableViewState(
            columns=ColumnViewState(order=order, pinned=pinned),
            page_size=page_size,
        )

    def apply_state(self, state: TableViewState) -> TableViewResult:
        """Return the complete applied row order without materializing result columns."""

        normalized = self.validate_state(state)
        source_positions = np.arange(len(self._source), dtype=np.int64)

        if normalized.conditions:
            mask = pd.Series(True, index=self._source.index, dtype=bool)
            for condition in normalized.conditions:
                if condition.enabled:
                    mask &= self._condition_mask(self._source, condition)
            source_positions = source_positions[mask.to_numpy(dtype=bool)]

        if normalized.sort_rules and len(source_positions):
            helper = self._helper_column_name()
            sort_columns = [rule.column for rule in normalized.sort_rules]
            sort_frame = (
                self._source.loc[:, sort_columns]
                .iloc[source_positions]
                .copy()
            )
            sort_frame[helper] = source_positions
            sort_frame = sort_frame.sort_values(
                by=[*sort_columns, helper],
                ascending=[
                    *(rule.ascending for rule in normalized.sort_rules),
                    True,
                ],
                na_position="last",
                kind="mergesort",
            )
            source_positions = sort_frame[helper].to_numpy(dtype=np.int64, copy=True)

        return TableViewResult(
            source_positions=source_positions,
            source_total=len(self._source),
            matched_total=len(source_positions),
            state=normalized,
        )

    def get_window(
        self,
        result: TableViewResult,
        offset: int,
        limit: int | None = None,
        columns: tuple[str, ...] | None = None,
    ) -> RowWindow:
        """Materialize one bounded row and column projection from the source snapshot."""

        self._validate_result(result)
        actual_limit = result.state.page_size if limit is None else limit
        if actual_limit <= 0:
            raise TableViewError("显示窗口行数必须大于 0")
        selected_columns = (
            tuple(str(column) for column in self._source.columns)
            if columns is None
            else tuple(columns)
        )
        if len(set(selected_columns)) != len(selected_columns):
            raise TableViewError("显示列不能重复")
        for column in selected_columns:
            self._require_column(column)
        maximum_offset = max(0, result.matched_total - 1)
        actual_offset = min(max(0, int(offset)), maximum_offset) if result.matched_total else 0
        stop = actual_offset + actual_limit
        window_positions = result.source_positions[actual_offset:stop]
        return RowWindow(
            dataframe=(
                self._source.loc[:, list(selected_columns)]
                .iloc[window_positions]
                .copy()
                .reset_index(drop=True)
            ),
            source_positions=tuple(int(value) for value in window_positions),
            offset=actual_offset,
            limit=actual_limit,
            matched_total=result.matched_total,
        )

    def find_result_position(
        self,
        result: TableViewResult,
        source_position: int,
    ) -> int | None:
        """Return the applied-result position for one exact source position."""

        self._validate_result(result)
        matches = np.flatnonzero(result.source_positions == int(source_position))
        return int(matches[0]) if len(matches) else None

    def find_identity(
        self,
        result: TableViewResult,
        identity: str,
        id_column: str = "ezqcid",
    ) -> int | None:
        """Return the first applied-result position matching one normalized identity."""

        self._validate_result(result)
        if id_column not in self._source.columns:
            raise QcIdentityError(f"表格缺少 QC 身份列 '{id_column}'")
        query = self._normalize_identity(identity)
        if not query or not result.matched_total:
            return None
        if id_column != "ezqcid":
            values = self._source[id_column].iloc[result.source_positions].map(
                self._normalize_identity
            )
            matches = np.flatnonzero(values.eq(query).to_numpy(dtype=bool))
            return int(matches[0]) if len(matches) else None
        indexed = self._identity_positions.get(query)
        if indexed is None:
            return None
        source_positions = (indexed,) if isinstance(indexed, int) else indexed
        matches = (
            self.find_result_position(result, source_position)
            for source_position in source_positions
        )
        return next((position for position in matches if position is not None), None)

    def validate_state(self, state: TableViewState) -> TableViewState:
        columns = tuple(str(column) for column in self._source.columns)
        if set(state.columns.order) != set(columns) or len(state.columns.order) != len(columns):
            raise TableViewError("列布局必须且只能包含源表的全部列")
        if len(set(state.columns.hidden)) != len(state.columns.hidden):
            raise TableViewError("隐藏列不能重复")
        if len(set(state.columns.pinned)) != len(state.columns.pinned):
            raise TableViewError("固定列不能重复")
        unknown_hidden = sorted(set(state.columns.hidden) - set(columns))
        if unknown_hidden:
            raise TableViewError(f"隐藏列不存在: {unknown_hidden}")
        unknown_pinned = sorted(set(state.columns.pinned) - set(columns))
        if unknown_pinned:
            raise TableViewError(f"固定列不存在: {unknown_pinned}")
        hidden_pinned = sorted(set(state.columns.hidden) & set(state.columns.pinned))
        if hidden_pinned:
            raise TableViewError(f"固定列不能隐藏: {hidden_pinned}")
        if "ezqcid" in columns:
            if "ezqcid" not in state.columns.pinned:
                raise TableViewError("ezqcid 必须保持固定")
            if "ezqcid" in state.columns.hidden:
                raise TableViewError("ezqcid 不能隐藏")
            if not state.columns.order or state.columns.order[0] != "ezqcid":
                raise TableViewError("ezqcid 必须保持为第一列")
        if state.density not in {"compact", "comfortable"}:
            raise TableViewError(f"不支持的表格密度: {state.density}")
        if state.page_size <= 0:
            raise TableViewError("每页行数必须大于 0")

        conditions = tuple(self._normalize_condition(condition) for condition in state.conditions)
        sort_columns: list[str] = []
        for rule in state.sort_rules:
            self._require_column(rule.column)
            sort_columns.append(rule.column)
        if len(set(sort_columns)) != len(sort_columns):
            raise TableViewError("多列排序不能重复使用同一列")
        return replace(state, conditions=conditions)

    def validate_qc_identity(
        self,
        result: TableViewResult,
        result_position: int,
        id_column: str = "ezqcid",
    ) -> str:
        self._validate_result(result)
        if id_column not in self._source.columns:
            raise QcIdentityError(f"表格缺少 QC 身份列 '{id_column}'")
        if result_position < 0 or result_position >= result.matched_total:
            raise QcIdentityError("所选记录位置已失效，请重新选择")
        source_position = int(result.source_positions[result_position])
        identity = self._normalize_identity(
            self._source[id_column].iloc[source_position]
        )
        if not identity:
            raise QcIdentityError(f"所选记录的 {id_column} 为空，无法打开 QC")
        identity_count = (
            self._identity_counts.get(identity, 0)
            if id_column == "ezqcid"
            else sum(
                self._normalize_identity(value) == identity
                for value in self._source[id_column]
            )
        )
        if identity_count != 1:
            raise QcIdentityError(f"{id_column} '{identity}' 在源表中不唯一，无法安全打开 QC")
        return identity

    def _validate_result(self, result: TableViewResult) -> None:
        if not isinstance(result, TableViewResult):
            raise TypeError("result must be a TableViewResult")
        if result.source_total != len(self._source):
            raise TableViewError("表格结果与当前源表不匹配，请重新应用视图")

    def _profile_columns(self) -> tuple[ColumnProfile, ...]:
        profiles: list[ColumnProfile] = []
        for column in self._source.columns:
            series = self._source[column]
            kind = self._column_kind(str(column), series)
            distinct_count = int(series.nunique(dropna=True))
            values: tuple[Any, ...] = ()
            if distinct_count <= self._distinct_value_limit:
                raw_values = series.dropna().drop_duplicates().tolist()
                values = tuple(sorted(raw_values, key=lambda item: str(item).casefold()))
            profiles.append(
                ColumnProfile(
                    name=str(column),
                    kind=kind,
                    nullable=bool(series.isna().any()),
                    distinct_count=distinct_count,
                    values=values,
                )
            )
        return tuple(profiles)

    @staticmethod
    def _column_kind(column: str, series: pd.Series) -> ColumnKind:
        if column == "ezqcid":
            return ColumnKind.TEXT
        if ptypes.is_bool_dtype(series.dtype):
            return ColumnKind.BOOLEAN
        if ptypes.is_numeric_dtype(series.dtype):
            return ColumnKind.NUMBER
        if ptypes.is_datetime64_any_dtype(series.dtype):
            return ColumnKind.DATETIME
        return ColumnKind.TEXT

    def _normalize_condition(self, condition: FilterCondition) -> FilterCondition:
        self._require_column(condition.column)
        profile = self._profile_by_name[condition.column]
        operator = condition.operator.strip().lower()
        if operator not in _OPERATORS_BY_KIND[profile.kind]:
            raise TableViewError(
                f"列 '{condition.column}' ({profile.kind.value}) 不支持筛选操作符 '{condition.operator}'"
            )
        value = None if operator in _VALUELESS_OPERATORS else self._normalize_value(profile, operator, condition.value)
        return replace(condition, operator=operator, value=value)

    def _normalize_value(self, profile: ColumnProfile, operator: str, value: Any) -> Any:
        if operator in {"in", "not_in"}:
            values = [part.strip() for part in value.split(",")] if isinstance(value, str) else list(value or ())
            if not values:
                raise TableViewError(f"筛选列 '{profile.name}' 的值列表不能为空")
            return tuple(self._normalize_scalar(profile, item) for item in values)
        if operator == "between":
            values = [part.strip() for part in value.split(",")] if isinstance(value, str) else list(value or ())
            if len(values) != 2:
                raise TableViewError(f"筛选列 '{profile.name}' 的区间必须包含两个值")
            return tuple(self._normalize_scalar(profile, item) for item in values)
        if value is None or (isinstance(value, str) and not value.strip()):
            raise TableViewError(f"筛选列 '{profile.name}' 的值不能为空")
        return self._normalize_scalar(profile, value)

    @staticmethod
    def _normalize_scalar(profile: ColumnProfile, value: Any) -> Any:
        if profile.kind == ColumnKind.TEXT:
            return str(value)
        if profile.kind == ColumnKind.NUMBER:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise TableViewError(f"列 '{profile.name}' 需要数值，收到: {value!r}") from exc
            return int(number) if number.is_integer() else number
        if profile.kind == ColumnKind.BOOLEAN:
            if isinstance(value, bool):
                return value
            normalized = str(value).strip().lower()
            if normalized in {"true", "1", "yes", "y", "是"}:
                return True
            if normalized in {"false", "0", "no", "n", "否"}:
                return False
            raise TableViewError(f"列 '{profile.name}' 需要 true/false，收到: {value!r}")
        try:
            return pd.to_datetime(value, errors="raise")
        except (TypeError, ValueError) as exc:
            raise TableViewError(f"列 '{profile.name}' 需要日期/时间，收到: {value!r}") from exc

    def _condition_mask(self, frame: pd.DataFrame, condition: FilterCondition) -> pd.Series:
        series = frame[condition.column]
        operator = condition.operator
        value = condition.value
        if operator == "isna":
            return series.isna()
        if operator == "notna":
            return series.notna()
        if operator == "==":
            return series.eq(value).fillna(False)
        if operator == "!=":
            return (series.notna() & series.ne(value)).fillna(False)
        if operator == ">":
            return series.gt(value).fillna(False)
        if operator == ">=":
            return series.ge(value).fillna(False)
        if operator == "<":
            return series.lt(value).fillna(False)
        if operator == "<=":
            return series.le(value).fillna(False)
        if operator == "between":
            return series.between(value[0], value[1], inclusive="both").fillna(False)
        if operator == "in":
            return series.isin(value).fillna(False)
        if operator == "not_in":
            return (series.notna() & ~series.isin(value)).fillna(False)

        strings = series.astype("string")
        needle = str(value)
        if operator == "contains":
            return strings.str.contains(needle, case=False, regex=False, na=False)
        if operator == "startswith":
            return strings.str.lower().str.startswith(needle.casefold(), na=False)
        if operator == "endswith":
            return strings.str.lower().str.endswith(needle.casefold(), na=False)
        raise TableViewError(f"不支持的筛选操作符: {operator}")

    def _require_column(self, column: str) -> None:
        if column not in self._source.columns:
            raise TableViewError(f"列不存在: {column}")

    def _helper_column_name(self) -> str:
        name = "__easyqc_source_position__"
        while name in self._source.columns:
            name = f"_{name}"
        return name

    def _build_identity_positions(self) -> dict[str, int | tuple[int, ...]]:
        if "ezqcid" not in self._source.columns:
            return {}
        identities = [self._normalize_identity(value) for value in self._source["ezqcid"]]
        collected: dict[str, list[int]] = {}
        for source_position, identity in enumerate(identities):
            if identity:
                collected.setdefault(identity, []).append(source_position)
        return {
            identity: values[0] if len(values) == 1 else tuple(values)
            for identity, values in collected.items()
        }

    @staticmethod
    def _normalize_identity(value: Any) -> str:
        if pd.isna(value):
            return ""
        return str(value).strip()


__all__ = ["QcIdentityError", "TableViewError", "TableViewService"]
