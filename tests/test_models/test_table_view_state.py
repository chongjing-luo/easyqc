from dataclasses import replace

from models.table_view_state import (
    ColumnViewState,
    FilterCondition,
    SortRule,
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
