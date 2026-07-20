from dataclasses import replace

import numpy as np
import pytest

from models.table_view_state import (
    ColumnViewState,
    FilterCondition,
    SortRule,
    TableViewResult,
    TableViewState,
)


def test_column_state_reports_visible_columns_in_saved_order() -> None:
    columns = ColumnViewState(
        order=("ezqcid", "age", "site", "score"),
        hidden=("site",),
        pinned=("ezqcid",),
    )

    assert columns.visible_columns == ("ezqcid", "age", "score")


def test_table_view_state_is_replaced_without_mutating_applied_state() -> None:
    applied = TableViewState(
        columns=ColumnViewState(order=("ezqcid", "age")),
    )
    draft = replace(
        applied,
        conditions=(FilterCondition(column="age", operator=">=", value="30"),),
    )

    assert applied.conditions == ()
    assert draft.conditions[0].column == "age"


def test_sort_rules_are_ordered_for_future_multi_sort_support() -> None:
    state = TableViewState(
        columns=ColumnViewState(order=("ezqcid", "site", "score")),
        sort_rules=(
            SortRule(column="site", ascending=True),
            SortRule(column="score", ascending=False),
        ),
    )

    assert [rule.column for rule in state.sort_rules] == ["site", "score"]


def test_table_result_owns_only_read_only_contiguous_int64_positions() -> None:
    state = TableViewState(columns=ColumnViewState(order=("ezqcid",)))
    caller_positions = np.array([4, 1, 9], dtype=np.int64)

    result = TableViewResult(
        source_positions=caller_positions,
        source_total=10,
        matched_total=3,
        state=state,
    )

    assert not hasattr(result, "dataframe")
    assert result.source_positions.tolist() == [4, 1, 9]
    assert result.source_positions.dtype == np.dtype(np.int64)
    assert result.source_positions.flags.c_contiguous
    assert not result.source_positions.flags.writeable
    with pytest.raises(ValueError, match="read-only"):
        result.source_positions[0] = 7
    caller_positions[0] = 7
    assert caller_positions.tolist() == [7, 1, 9]
    assert result.source_positions.tolist() == [4, 1, 9]


def test_table_result_rejects_position_count_and_range_mismatches() -> None:
    state = TableViewState(columns=ColumnViewState(order=("ezqcid",)))

    with pytest.raises(ValueError, match="matched_total"):
        TableViewResult(
            source_positions=(0, 1),
            source_total=2,
            matched_total=1,
            state=state,
        )
    with pytest.raises(ValueError, match="source position"):
        TableViewResult(
            source_positions=(0, 2),
            source_total=2,
            matched_total=2,
            state=state,
        )
