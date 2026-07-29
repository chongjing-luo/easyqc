from dataclasses import replace

import numpy as np
import pytest

from models.table_view_state import (
    ColumnViewState,
    FilterCondition,
    FilterExpression,
    FilterGroup,
    MAX_FILTER_CONDITIONS_PER_GROUP,
    MAX_FILTER_GROUPS,
    SortRule,
    TableViewResult,
    TableViewState,
    TableViewStateContractError,
    normalize_table_view_state_payload,
)


def test_column_state_reports_visible_columns_in_saved_order() -> None:
    columns = ColumnViewState(
        order=("easyqcid", "age", "site", "score"),
        hidden=("site",),
        pinned=("easyqcid",),
    )

    assert columns.visible_columns == ("easyqcid", "age", "score")


def test_table_view_state_is_replaced_without_mutating_applied_state() -> None:
    applied = TableViewState(
        columns=ColumnViewState(order=("easyqcid", "age")),
    )
    draft = replace(
        applied,
        conditions=(FilterCondition(column="age", operator=">=", value="30"),),
    )

    assert applied.conditions == ()
    assert draft.conditions[0].column == "age"


def test_sort_rules_are_ordered_for_future_multi_sort_support() -> None:
    state = TableViewState(
        columns=ColumnViewState(order=("easyqcid", "site", "score")),
        sort_rules=(
            SortRule(column="site", ascending=True),
            SortRule(column="score", ascending=False),
        ),
    )

    assert [rule.column for rule in state.sort_rules] == ["site", "score"]


def test_table_result_owns_only_read_only_contiguous_int64_positions() -> None:
    state = TableViewState(columns=ColumnViewState(order=("easyqcid",)))
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
    state = TableViewState(columns=ColumnViewState(order=("easyqcid",)))

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


def _state_payload_v1() -> dict[str, object]:
    return {
        "schema_version": 1,
        "columns": {
            "order": ["easyqcid", "site", "score"],
            "hidden": [],
            "widths": [["easyqcid", 120]],
            "pinned": ["easyqcid"],
        },
        "conditions": [
            {
                "column": "site",
                "operator": "==",
                "value": "A",
                "condition_id": "site-a",
                "enabled": True,
            }
        ],
        "sort_rules": [{"column": "score", "ascending": False}],
        "density": "compact",
        "page_size": 200,
        "revision": 4,
    }


def test_flat_v1_payload_normalizes_to_strict_grouped_v2() -> None:
    state = normalize_table_view_state_payload(_state_payload_v1())

    assert state.schema_version == 2
    assert state.filter == FilterExpression(
        group_join="all",
        groups=(
            FilterGroup(
                group_id="legacy-flat",
                join="all",
                conditions=(
                    FilterCondition(
                        column="site",
                        operator="==",
                        value="A",
                        condition_id="site-a",
                        enabled=True,
                    ),
                ),
            ),
        ),
    )
    assert state.conditions == state.filter.groups[0].conditions

    emitted = state.to_json_object()
    assert emitted["schema_version"] == 2
    assert "conditions" not in emitted
    assert emitted["filter"] == {
        "group_join": "all",
        "groups": [
            {
                "group_id": "legacy-flat",
                "join": "all",
                "conditions": [
                    {
                        "column": "site",
                        "operator": "==",
                        "value": "A",
                        "condition_id": "site-a",
                        "enabled": True,
                    }
                ],
            }
        ],
    }
    assert normalize_table_view_state_payload(emitted) == state


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.update(schema_version=99), "schema_version"),
        (lambda payload: payload.update(unexpected=True), "unexpected"),
        (lambda payload: payload.update(conditions={}), "conditions"),
    ],
)
def test_state_payload_rejects_unknown_versions_fields_and_wrong_shapes(
    mutation, message: str
) -> None:
    payload = _state_payload_v1()
    mutation(payload)

    with pytest.raises(TableViewStateContractError, match=message):
        normalize_table_view_state_payload(payload)


def test_explicit_grouped_state_keeps_flat_compatibility_view() -> None:
    expression = FilterExpression(
        group_join="any",
        groups=(
            FilterGroup(
                group_id="site-or-score",
                join="any",
                conditions=(
                    FilterCondition("site", "==", "A", "site-a"),
                    FilterCondition("score", ">=", 3, "score-3"),
                ),
            ),
        ),
    )

    state = TableViewState(
        columns=ColumnViewState(
            order=("easyqcid", "site", "score"), pinned=("easyqcid",)
        ),
        filter=expression,
    )

    assert state.filter is expression
    assert tuple(condition.condition_id for condition in state.conditions) == (
        "site-a",
        "score-3",
    )


def test_legacy_flat_serialization_assigns_deterministic_v2_condition_ids() -> None:
    payload = _state_payload_v1()
    payload["conditions"][0]["condition_id"] = ""

    normalized = normalize_table_view_state_payload(payload)
    emitted = normalized.to_json_object()

    assert normalized.conditions[0].condition_id == "legacy-condition-0001"
    assert (
        emitted["filter"]["groups"][0]["conditions"][0]["condition_id"]
        == "legacy-condition-0001"
    )
    assert normalize_table_view_state_payload(emitted) == normalized


def test_v2_payload_rejects_blank_condition_identity() -> None:
    state = normalize_table_view_state_payload(_state_payload_v1())
    payload = state.to_json_object()
    payload["filter"]["groups"][0]["conditions"][0]["condition_id"] = ""

    with pytest.raises(TableViewStateContractError, match="condition_id"):
        normalize_table_view_state_payload(payload)


def test_v2_payload_bounds_group_and_condition_collections() -> None:
    state = normalize_table_view_state_payload(_state_payload_v1())
    too_many_groups = state.to_json_object()
    group = too_many_groups["filter"]["groups"][0]
    too_many_groups["filter"]["groups"] = [
        {**group, "group_id": f"group-{index}"}
        for index in range(MAX_FILTER_GROUPS + 1)
    ]

    with pytest.raises(TableViewStateContractError, match="filter.groups.*at most"):
        normalize_table_view_state_payload(too_many_groups)

    too_many_conditions = state.to_json_object()
    condition = too_many_conditions["filter"]["groups"][0]["conditions"][0]
    too_many_conditions["filter"]["groups"][0]["conditions"] = [
        {**condition, "condition_id": f"condition-{index}"}
        for index in range(MAX_FILTER_CONDITIONS_PER_GROUP + 1)
    ]

    with pytest.raises(TableViewStateContractError, match="conditions.*at most"):
        normalize_table_view_state_payload(too_many_conditions)
