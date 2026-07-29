"""Normalize one QC module's persisted row-filter contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd

from core.table_transform import (
    TableTransformError,
    legacy_select_filter_to_operations,
)
from core.table_view_service import TableViewError, TableViewService
from models.table_view_state import (
    FilterCondition,
    FilterExpression,
    FilterGroup,
    filter_expression_from_json_object,
)


class ModuleFilterCompatibilityError(ValueError):
    """Raised when a legacy module filter cannot be converted safely."""


def normalize_module_filter(module: Mapping[str, Any]) -> FilterExpression:
    """Return the typed row filter for one module payload.

    The function reads only ``qc_filter`` and ``select_filter`` and has no side
    effects. A non-null structured filter is authoritative. Missing filters and
    blank legacy values mean the complete list; unsupported legacy syntax
    raises ``ModuleFilterCompatibilityError``.
    """

    if not isinstance(module, Mapping):
        raise TypeError("module must be a mapping")

    structured = module.get("qc_filter")
    if structured is not None:
        return filter_expression_from_json_object(structured)

    legacy = module.get("select_filter")
    if legacy is None or (isinstance(legacy, str) and not legacy.strip()):
        return FilterExpression()
    if not isinstance(legacy, str):
        raise ModuleFilterCompatibilityError(
            "legacy select_filter must be a string or null"
        )

    try:
        operations = legacy_select_filter_to_operations(legacy)
    except TableTransformError as exc:
        raise ModuleFilterCompatibilityError(
            f"unsupported legacy select_filter: {exc}"
        ) from exc
    if operations is None:
        raise ModuleFilterCompatibilityError(
            "unsupported legacy select_filter: expected a bounded "
            "SELECT * FROM df query"
        )
    if not operations:
        return FilterExpression()

    operation = operations[0]
    conditions = tuple(
        FilterCondition(
            column=condition["column"],
            operator=condition["operator"],
            value=condition["value"],
            condition_id=f"legacy-select-filter-{index + 1:04d}",
            enabled=True,
        )
        for index, condition in enumerate(operation["conditions"])
    )
    return FilterExpression(
        group_join="all",
        groups=(
            FilterGroup(
                group_id="legacy-select-filter",
                join="all",
                conditions=conditions,
            ),
        ),
    )


def resolve_module_filter_identities(
    subjects: pd.DataFrame,
    expression: FilterExpression,
) -> tuple[str, ...]:
    """Evaluate one row filter and return matched identities in source order.

    The complete subject frame and one typed expression are the only inputs.
    The function writes nothing, applies no sort/column/pagination state, and
    propagates table/filter/identity contract errors without an empty fallback.
    """

    if not isinstance(subjects, pd.DataFrame):
        raise TypeError("module filter subjects must be a pandas DataFrame")
    if subjects.columns.has_duplicates:
        duplicates = subjects.columns[subjects.columns.duplicated()].tolist()
        raise TableViewError(f"表格包含重复列名: {duplicates}")

    projected_columns = _module_filter_columns(subjects, expression)
    service = TableViewService(subjects.loc[:, projected_columns])
    state = service.default_state(
        page_size=max(1, service.source_total)
    ).with_filter(expression)
    result = service.apply_state(state)
    return service.validate_qc_identities(result)


def _module_filter_columns(
    subjects: pd.DataFrame,
    expression: FilterExpression,
) -> tuple[str, ...]:
    """Return available identity/filter columns without validating the rule."""

    if not isinstance(expression, FilterExpression):
        return tuple(subjects.columns)

    requested = ["easyqcid"]
    for group in expression.groups:
        if not isinstance(group, FilterGroup):
            return tuple(subjects.columns)
        for condition in group.conditions:
            if not isinstance(condition, FilterCondition):
                return tuple(subjects.columns)
            if (
                isinstance(condition.column, str)
                and condition.column not in requested
            ):
                requested.append(condition.column)
    return tuple(column for column in requested if column in subjects.columns)


__all__ = [
    "ModuleFilterCompatibilityError",
    "normalize_module_filter",
    "resolve_module_filter_identities",
]
