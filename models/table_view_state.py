"""Typed, GUI-independent state for the read-only table workspace."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
import math
from typing import Any

import numpy as np
import pandas as pd


MAX_FILTER_GROUPS = 16
MAX_FILTER_CONDITIONS_PER_GROUP = 64
MAX_FILTER_CONDITIONS = 256
FILTER_EXPRESSION_SCHEMA_VERSION = 1


class ColumnKind(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"


class TableViewStateContractError(ValueError):
    """Raised when an internal table-state payload violates its schema."""


@dataclass(frozen=True)
class FilterCondition:
    column: str
    operator: str
    value: Any = None
    condition_id: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class FilterGroup:
    group_id: str
    join: str
    conditions: tuple[FilterCondition, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "conditions", tuple(self.conditions))


@dataclass(frozen=True)
class FilterExpression:
    group_join: str = "all"
    groups: tuple[FilterGroup, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "groups", tuple(self.groups))


@dataclass(frozen=True)
class SortRule:
    column: str
    ascending: bool = True


@dataclass(frozen=True)
class ColumnViewState:
    order: tuple[str, ...]
    hidden: tuple[str, ...] = ()
    widths: tuple[tuple[str, int], ...] = ()
    pinned: tuple[str, ...] = ()

    @property
    def visible_columns(self) -> tuple[str, ...]:
        hidden = set(self.hidden)
        return tuple(column for column in self.order if column not in hidden)

    def width_for(self, column: str, default: int = 120) -> int:
        return dict(self.widths).get(column, default)

    def with_width(self, column: str, width: int) -> "ColumnViewState":
        widths = dict(self.widths)
        widths[column] = width
        return replace(self, widths=tuple((name, widths[name]) for name in self.order if name in widths))


@dataclass(frozen=True)
class TableViewState:
    columns: ColumnViewState
    conditions: tuple[FilterCondition, ...] = ()
    sort_rules: tuple[SortRule, ...] = ()
    density: str = "compact"
    page_size: int = 200
    revision: int = 0
    schema_version: int = 2
    filter: FilterExpression | None = None

    def __post_init__(self) -> None:
        if self.schema_version != 2:
            raise TableViewStateContractError(
                "TableViewState schema_version must be 2"
            )
        object.__setattr__(self, "conditions", tuple(self.conditions))
        object.__setattr__(self, "sort_rules", tuple(self.sort_rules))
        if self.filter is not None:
            if not isinstance(self.filter, FilterExpression):
                raise TableViewStateContractError(
                    "TableViewState filter must be a FilterExpression"
                )
            flattened = tuple(
                condition
                for group in self.filter.groups
                for condition in group.conditions
            )
            if self.conditions and self.conditions != flattened:
                raise TableViewStateContractError(
                    "flat conditions conflict with grouped filter"
                )
            object.__setattr__(self, "conditions", flattened)

    def with_conditions(self, conditions: tuple[FilterCondition, ...]) -> "TableViewState":
        return replace(self, conditions=tuple(conditions), filter=None)

    def with_filter(self, expression: FilterExpression) -> "TableViewState":
        return replace(self, conditions=(), filter=expression)

    def with_sort_rules(self, sort_rules: tuple[SortRule, ...]) -> "TableViewState":
        return replace(self, sort_rules=tuple(sort_rules))

    def next_revision(self) -> "TableViewState":
        return replace(self, revision=self.revision + 1)

    @property
    def effective_filter(self) -> FilterExpression:
        if self.filter is not None:
            return self.filter
        if not self.conditions:
            return FilterExpression()
        conditions = _normalize_legacy_condition_ids(self.conditions)
        return FilterExpression(
            group_join="all",
            groups=(
                FilterGroup(
                    group_id="legacy-flat",
                    join="all",
                    conditions=conditions,
                ),
            ),
        )

    def to_json_object(self) -> dict[str, object]:
        """Return the strict internal V2 payload; this is never a GUI editor."""

        expression = self.effective_filter
        _validate_filter_bounds(expression)
        return {
            "schema_version": 2,
            "columns": {
                "order": list(self.columns.order),
                "hidden": list(self.columns.hidden),
                "widths": [list(item) for item in self.columns.widths],
                "pinned": list(self.columns.pinned),
            },
            "filter": _filter_expression_to_json_body(expression),
            "sort_rules": [
                {"column": rule.column, "ascending": rule.ascending}
                for rule in self.sort_rules
            ],
            "density": self.density,
            "page_size": self.page_size,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    kind: ColumnKind
    nullable: bool
    distinct_count: int
    values: tuple[Any, ...] = ()


@dataclass(frozen=True)
class TableViewResult:
    source_positions: np.ndarray
    source_total: int
    matched_total: int
    state: TableViewState

    def __post_init__(self) -> None:
        if self.source_total < 0:
            raise ValueError("source_total must be greater than or equal to zero")
        positions = np.asarray(self.source_positions)
        if positions.ndim != 1:
            raise ValueError("source_positions must be a one-dimensional array")
        positions = np.array(positions, dtype=np.int64, order="C", copy=True)
        if len(positions) != self.matched_total:
            raise ValueError("matched_total must equal the number of source positions")
        if self.matched_total < 0:
            raise ValueError("matched_total must be greater than or equal to zero")
        if len(positions) and (
            int(positions.min()) < 0 or int(positions.max()) >= self.source_total
        ):
            raise ValueError("source position is outside source_total")
        if len(np.unique(positions)) != len(positions):
            raise ValueError("source positions must not contain duplicates")
        positions.setflags(write=False)
        object.__setattr__(self, "source_positions", positions)


@dataclass
class RowWindow:
    dataframe: pd.DataFrame
    source_positions: tuple[int, ...]
    offset: int
    limit: int
    matched_total: int


def normalize_table_view_state_payload(value: object) -> TableViewState:
    """Normalize one strict internal V1/V2 mapping into ``TableViewState`` V2."""

    record = _record(value, "TableViewState")
    version = _integer(record.get("schema_version"), "schema_version", minimum=1)
    common = {
        "schema_version",
        "columns",
        "sort_rules",
        "density",
        "page_size",
        "revision",
    }
    if version == 1:
        _require_fields(record, common | {"conditions"}, "TableViewStateV1")
        conditions = _normalize_legacy_condition_ids(
            tuple(
                _condition_from_json_object(
                    item,
                    f"conditions[{index}]",
                    allow_empty_id=True,
                )
                for index, item in enumerate(
                    _items(
                        record["conditions"],
                        "conditions",
                        maximum=MAX_FILTER_CONDITIONS,
                    )
                )
            )
        )
        expression = (
            FilterExpression(
                group_join="all",
                groups=(FilterGroup("legacy-flat", "all", conditions),),
            )
            if conditions
            else FilterExpression()
        )
    elif version == 2:
        _require_fields(record, common | {"filter"}, "TableViewStateV2")
        expression = _filter_from_json_object(record["filter"])
    else:
        raise TableViewStateContractError(
            f"unsupported schema_version: {version}"
        )

    return TableViewState(
        columns=_columns_from_json_object(record["columns"]),
        sort_rules=tuple(
            _sort_rule_from_json_object(item, index)
            for index, item in enumerate(_items(record["sort_rules"], "sort_rules"))
        ),
        density=_text(record["density"], "density"),
        page_size=_integer(record["page_size"], "page_size", minimum=1),
        revision=_integer(record["revision"], "revision", minimum=0),
        filter=expression,
    )


def filter_expression_from_json_object(
    value: object | None,
) -> FilterExpression:
    """Read one standalone schema-version-1 filter without side effects.

    ``None`` is the backward-compatible representation of a missing module
    filter. Any non-null payload must satisfy the complete strict schema.
    """

    if value is None:
        return FilterExpression()
    record = _record(value, "filter")
    _require_fields(
        record,
        {"schema_version", "group_join", "groups"},
        "filter",
    )
    version = _integer(
        record["schema_version"],
        "filter.schema_version",
        minimum=1,
    )
    if version != FILTER_EXPRESSION_SCHEMA_VERSION:
        raise TableViewStateContractError(
            f"unsupported filter schema_version: {version}"
        )
    expression = _filter_from_json_object(
        {
            "group_join": record["group_join"],
            "groups": record["groups"],
        }
    )
    _validate_filter_expression_contract(expression)
    return expression if expression.groups else FilterExpression()


def filter_expression_to_json_object(
    expression: FilterExpression,
) -> dict[str, object]:
    """Serialize one standalone filter as the strict schema-version-1 object."""

    _validate_filter_expression_contract(expression)
    if not expression.groups:
        expression = FilterExpression()
    return {
        "schema_version": FILTER_EXPRESSION_SCHEMA_VERSION,
        **_filter_expression_to_json_body(expression),
    }


def _columns_from_json_object(value: object) -> ColumnViewState:
    record = _record(value, "columns")
    _require_fields(record, {"order", "hidden", "widths", "pinned"}, "columns")
    widths: list[tuple[str, int]] = []
    for index, item in enumerate(_items(record["widths"], "columns.widths")):
        pair = _items(item, f"columns.widths[{index}]")
        if len(pair) != 2:
            raise TableViewStateContractError(
                f"columns.widths[{index}] must contain column and width"
            )
        widths.append(
            (
                _text(pair[0], f"columns.widths[{index}].column"),
                _integer(pair[1], f"columns.widths[{index}].width", minimum=1),
            )
        )
    return ColumnViewState(
        order=tuple(
            _text(item, f"columns.order[{index}]")
            for index, item in enumerate(_items(record["order"], "columns.order"))
        ),
        hidden=tuple(
            _text(item, f"columns.hidden[{index}]")
            for index, item in enumerate(_items(record["hidden"], "columns.hidden"))
        ),
        widths=tuple(widths),
        pinned=tuple(
            _text(item, f"columns.pinned[{index}]")
            for index, item in enumerate(_items(record["pinned"], "columns.pinned"))
        ),
    )


def _filter_from_json_object(value: object) -> FilterExpression:
    record = _record(value, "filter")
    _require_fields(record, {"group_join", "groups"}, "filter")
    groups: list[FilterGroup] = []
    total_conditions = 0
    for index, item in enumerate(
        _items(
            record["groups"],
            "filter.groups",
            maximum=MAX_FILTER_GROUPS,
        )
    ):
        label = f"filter.groups[{index}]"
        group = _record(item, label)
        _require_fields(group, {"group_id", "join", "conditions"}, label)
        condition_items = _items(
            group["conditions"],
            f"{label}.conditions",
            maximum=MAX_FILTER_CONDITIONS_PER_GROUP,
        )
        total_conditions += len(condition_items)
        if total_conditions > MAX_FILTER_CONDITIONS:
            raise TableViewStateContractError(
                f"filter contains at most {MAX_FILTER_CONDITIONS} conditions"
            )
        groups.append(
            FilterGroup(
                group_id=_text(group["group_id"], f"{label}.group_id"),
                join=_text(group["join"], f"{label}.join"),
                conditions=tuple(
                    _condition_from_json_object(condition, f"{label}.conditions[{condition_index}]")
                    for condition_index, condition in enumerate(condition_items)
                ),
            )
        )
    return FilterExpression(
        group_join=_text(record["group_join"], "filter.group_join"),
        groups=tuple(groups),
    )


def _condition_from_json_object(
    value: object,
    label: str,
    *,
    allow_empty_id: bool = False,
) -> FilterCondition:
    record = _record(value, label)
    _require_fields(
        record,
        {"column", "operator", "value", "condition_id", "enabled"},
        label,
    )
    return FilterCondition(
        column=_text(record["column"], f"{label}.column"),
        operator=_text(record["operator"], f"{label}.operator"),
        value=_json_input_value(record["value"], f"{label}.value"),
        condition_id=_text(
            record["condition_id"],
            f"{label}.condition_id",
            allow_empty=allow_empty_id,
        ),
        enabled=_boolean(record["enabled"], f"{label}.enabled"),
    )


def _normalize_legacy_condition_ids(
    conditions: tuple[FilterCondition, ...]
) -> tuple[FilterCondition, ...]:
    normalized: list[FilterCondition] = []
    seen: set[str] = set()
    for index, condition in enumerate(conditions):
        condition_id = str(condition.condition_id).strip()
        if not condition_id:
            condition_id = f"legacy-condition-{index + 1:04d}"
        if condition_id in seen:
            raise TableViewStateContractError(
                f"legacy condition_id is duplicated: {condition_id}"
            )
        seen.add(condition_id)
        normalized.append(replace(condition, condition_id=condition_id))
    return tuple(normalized)


def _condition_to_json_object(condition: FilterCondition) -> dict[str, object]:
    return {
        "column": condition.column,
        "operator": condition.operator,
        "value": _json_output_value(condition.value, "condition.value"),
        "condition_id": condition.condition_id,
        "enabled": condition.enabled,
    }


def _filter_expression_to_json_body(
    expression: FilterExpression,
) -> dict[str, object]:
    return {
        "group_join": expression.group_join,
        "groups": [
            {
                "group_id": group.group_id,
                "join": group.join,
                "conditions": [
                    _condition_to_json_object(condition)
                    for condition in group.conditions
                ],
            }
            for group in expression.groups
        ],
    }


def _sort_rule_from_json_object(value: object, index: int) -> SortRule:
    label = f"sort_rules[{index}]"
    record = _record(value, label)
    _require_fields(record, {"column", "ascending"}, label)
    return SortRule(
        column=_text(record["column"], f"{label}.column"),
        ascending=_boolean(record["ascending"], f"{label}.ascending"),
    )


def _record(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) for key in value
    ):
        raise TableViewStateContractError(f"{label} must be an object")
    return dict(value)


def _require_fields(
    record: Mapping[str, object], expected: set[str], label: str
) -> None:
    missing = sorted(expected - set(record))
    unknown = sorted(set(record) - expected)
    if missing:
        raise TableViewStateContractError(f"{label} missing fields: {missing}")
    if unknown:
        raise TableViewStateContractError(f"{label} unexpected fields: {unknown}")


def _items(
    value: object,
    label: str,
    *,
    maximum: int | None = None,
) -> list[object]:
    if not isinstance(value, list):
        raise TableViewStateContractError(f"{label} must be a list")
    if maximum is not None and len(value) > maximum:
        raise TableViewStateContractError(
            f"{label} contains at most {maximum} items"
        )
    return value


def _validate_filter_bounds(expression: FilterExpression) -> None:
    if len(expression.groups) > MAX_FILTER_GROUPS:
        raise TableViewStateContractError(
            f"filter.groups contains at most {MAX_FILTER_GROUPS} items"
        )
    total_conditions = 0
    for group in expression.groups:
        if len(group.conditions) > MAX_FILTER_CONDITIONS_PER_GROUP:
            raise TableViewStateContractError(
                "filter group conditions contains at most "
                f"{MAX_FILTER_CONDITIONS_PER_GROUP} items"
            )
        total_conditions += len(group.conditions)
    if total_conditions > MAX_FILTER_CONDITIONS:
        raise TableViewStateContractError(
            f"filter contains at most {MAX_FILTER_CONDITIONS} conditions"
        )


def _validate_filter_expression_contract(
    expression: FilterExpression,
) -> None:
    if not isinstance(expression, FilterExpression):
        raise TableViewStateContractError(
            "filter must be a FilterExpression"
        )
    _validate_filter_bounds(expression)
    if expression.group_join not in {"all", "any"}:
        raise TableViewStateContractError(
            "filter.group_join must be 'all' or 'any'"
        )

    seen_groups: set[str] = set()
    seen_conditions: set[str] = set()
    for group_index, group in enumerate(expression.groups):
        label = f"filter.groups[{group_index}]"
        if not isinstance(group, FilterGroup):
            raise TableViewStateContractError(
                f"{label} must be a FilterGroup"
            )
        if not isinstance(group.group_id, str) or not group.group_id.strip():
            raise TableViewStateContractError(
                f"{label}.group_id must be a nonblank string"
            )
        if group.group_id in seen_groups:
            raise TableViewStateContractError(
                f"{label}.group_id is duplicated: {group.group_id}"
            )
        seen_groups.add(group.group_id)
        if group.join not in {"all", "any"}:
            raise TableViewStateContractError(
                f"{label}.join must be 'all' or 'any'"
            )

        for condition_index, condition in enumerate(group.conditions):
            condition_label = f"{label}.conditions[{condition_index}]"
            if not isinstance(condition, FilterCondition):
                raise TableViewStateContractError(
                    f"{condition_label} must be a FilterCondition"
                )
            _text(condition.column, f"{condition_label}.column")
            _text(condition.operator, f"{condition_label}.operator")
            _text(condition.condition_id, f"{condition_label}.condition_id")
            if condition.condition_id in seen_conditions:
                raise TableViewStateContractError(
                    f"{condition_label}.condition_id is duplicated: "
                    f"{condition.condition_id}"
                )
            seen_conditions.add(condition.condition_id)
            _boolean(condition.enabled, f"{condition_label}.enabled")
            _json_output_value(
                condition.value,
                f"{condition_label}.value",
            )


def _text(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise TableViewStateContractError(f"{label} must be a nonblank string")
    return value


def _integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise TableViewStateContractError(
            f"{label} must be an integer greater than or equal to {minimum}"
        )
    return value


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise TableViewStateContractError(f"{label} must be a boolean")
    return value


def _json_input_value(value: object, label: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TableViewStateContractError(f"{label} must be finite")
        return value
    if isinstance(value, list):
        return tuple(
            _json_input_value(item, f"{label}[{index}]")
            for index, item in enumerate(value)
        )
    raise TableViewStateContractError(
        f"{label} must contain only JSON scalar or list values"
    )


def _json_output_value(value: object, label: str) -> object:
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TableViewStateContractError(f"{label} must be finite")
        return value
    if isinstance(value, (tuple, list)):
        return [
            _json_output_value(item, f"{label}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TableViewStateContractError(
        f"{label} cannot be represented as an internal JSON value"
    )


__all__ = [
    "ColumnKind",
    "ColumnProfile",
    "ColumnViewState",
    "FILTER_EXPRESSION_SCHEMA_VERSION",
    "FilterCondition",
    "FilterExpression",
    "FilterGroup",
    "MAX_FILTER_CONDITIONS",
    "MAX_FILTER_CONDITIONS_PER_GROUP",
    "MAX_FILTER_GROUPS",
    "RowWindow",
    "SortRule",
    "TableViewResult",
    "TableViewState",
    "TableViewStateContractError",
    "filter_expression_from_json_object",
    "filter_expression_to_json_object",
    "normalize_table_view_state_payload",
]
