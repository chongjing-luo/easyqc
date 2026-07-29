import pytest

from core.module_filter import (
    ModuleFilterCompatibilityError,
    normalize_module_filter,
)
from models.table_view_state import (
    FilterCondition,
    FilterExpression,
    FilterGroup,
    TableViewStateContractError,
    filter_expression_from_json_object,
    filter_expression_to_json_object,
)


def _grouped_expression() -> FilterExpression:
    return FilterExpression(
        group_join="any",
        groups=(
            FilterGroup(
                group_id="site-or-score",
                join="any",
                conditions=(
                    FilterCondition("site", "==", "A", "site-a", True),
                    FilterCondition("score", ">=", 3, "score-3", False),
                ),
            ),
            FilterGroup(
                group_id="visit",
                join="all",
                conditions=(
                    FilterCondition(
                        "visit",
                        "in",
                        ("baseline", "follow-up"),
                        "visit-list",
                        True,
                    ),
                ),
            ),
        ),
    )


def test_missing_and_canonical_empty_module_filters_normalize_to_empty_expression() -> None:
    assert normalize_module_filter({"name": "module"}) == FilterExpression()
    assert normalize_module_filter(
        {
            "name": "module",
            "qc_filter": {
                "schema_version": 1,
                "group_join": "any",
                "groups": [],
            },
        }
    ) == FilterExpression()
    assert filter_expression_to_json_object(
        FilterExpression(group_join="any")
    ) == {
        "schema_version": 1,
        "group_join": "all",
        "groups": [],
    }


def test_grouped_filter_payload_round_trips_without_loss() -> None:
    expression = _grouped_expression()

    payload = filter_expression_to_json_object(expression)

    assert payload["schema_version"] == 1
    assert payload["groups"][1]["conditions"][0]["value"] == [
        "baseline",
        "follow-up",
    ]
    assert filter_expression_from_json_object(payload) == expression
    assert normalize_module_filter({"qc_filter": payload}) == expression


def test_structured_filter_is_authoritative_over_legacy_select_filter() -> None:
    expression = normalize_module_filter(
        {
            "qc_filter": {
                "schema_version": 1,
                "group_join": "all",
                "groups": [],
            },
            "select_filter": "SELECT easyqcid FROM df",
        }
    )

    assert expression == FilterExpression()


def test_supported_legacy_select_filter_converts_to_typed_expression() -> None:
    expression = normalize_module_filter(
        {
            "select_filter": (
                "SELECT * FROM df WHERE site = 'A' and score >= 3"
            )
        }
    )

    assert expression == FilterExpression(
        group_join="all",
        groups=(
            FilterGroup(
                group_id="legacy-select-filter",
                join="all",
                conditions=(
                    FilterCondition(
                        "site",
                        "==",
                        "A",
                        "legacy-select-filter-0001",
                        True,
                    ),
                    FilterCondition(
                        "score",
                        ">=",
                        3,
                        "legacy-select-filter-0002",
                        True,
                    ),
                ),
            ),
        ),
    )


@pytest.mark.parametrize(
    "legacy_filter",
    [
        "SELECT easyqcid FROM df",
        "SELECT * FROM df WHERE site = 'A' OR score >= 3",
        '{"operations": [{"operation": "derive_column"}]}',
    ],
)
def test_unsupported_legacy_filter_fails_loudly(legacy_filter: str) -> None:
    with pytest.raises(ModuleFilterCompatibilityError, match="legacy"):
        normalize_module_filter({"select_filter": legacy_filter})


def test_standalone_filter_payload_rejects_wrong_schema_and_duplicate_ids() -> None:
    payload = filter_expression_to_json_object(_grouped_expression())
    payload["schema_version"] = 2

    with pytest.raises(TableViewStateContractError, match="schema_version"):
        filter_expression_from_json_object(payload)

    duplicate_ids = filter_expression_to_json_object(_grouped_expression())
    duplicate_ids["groups"][1]["conditions"][0]["condition_id"] = "site-a"

    with pytest.raises(TableViewStateContractError, match="condition_id"):
        filter_expression_from_json_object(duplicate_ids)
