from dataclasses import replace

import pandas as pd
import pytest

from core.table_view_service import QcIdentityError, TableViewError, TableViewService
from models.table_view_state import ColumnViewState, FilterCondition, SortRule


def _source() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003", "SUB004"],
            "age": [29, 31, 27, 31],
            "site": ["A", "B", "A", None],
            "passed": [True, False, True, False],
            "score": [3.0, 2.0, 5.0, None],
        }
    )


def test_apply_filters_complete_source_before_taking_render_window() -> None:
    source = pd.DataFrame(
        {
            "ezqcid": [f"SUB{i:05d}" for i in range(10_001)],
            "marker": ["target" if i == 9_000 else "other" for i in range(10_001)],
        }
    )
    service = TableViewService(source)
    state = service.default_state().with_conditions(
        (FilterCondition(column="marker", operator="==", value="target"),)
    )

    result = service.apply_state(state)
    window = service.get_window(result, offset=0, limit=200)

    assert result.source_total == 10_001
    assert result.matched_total == 1
    assert window.dataframe["ezqcid"].tolist() == ["SUB09000"]


def test_type_aware_filters_and_missing_operators() -> None:
    service = TableViewService(_source())
    state = service.default_state().with_conditions(
        (
            FilterCondition(column="age", operator=">=", value="30"),
            FilterCondition(column="passed", operator="==", value="false"),
            FilterCondition(column="site", operator="notna"),
        )
    )

    result = service.apply_state(state)

    assert result.dataframe["ezqcid"].tolist() == ["SUB002"]


def test_between_contains_membership_and_missing_are_supported() -> None:
    service = TableViewService(_source())

    between = service.apply_state(
        service.default_state().with_conditions(
            (FilterCondition(column="age", operator="between", value=(28, 31)),)
        )
    )
    contains = service.apply_state(
        service.default_state().with_conditions(
            (FilterCondition(column="ezqcid", operator="contains", value="003"),)
        )
    )
    membership = service.apply_state(
        service.default_state().with_conditions(
            (FilterCondition(column="site", operator="in", value=("A",)),)
        )
    )
    missing = service.apply_state(
        service.default_state().with_conditions(
            (FilterCondition(column="score", operator="isna"),)
        )
    )

    assert between.dataframe["ezqcid"].tolist() == ["SUB001", "SUB002", "SUB004"]
    assert contains.dataframe["ezqcid"].tolist() == ["SUB003"]
    assert membership.dataframe["ezqcid"].tolist() == ["SUB001", "SUB003"]
    assert missing.dataframe["ezqcid"].tolist() == ["SUB004"]


def test_sort_is_stable_and_window_preserves_source_positions() -> None:
    service = TableViewService(_source())
    state = service.default_state().with_sort_rules(
        (SortRule(column="age", ascending=True),)
    )

    result = service.apply_state(state)
    window = service.get_window(result, offset=1, limit=2)

    assert result.dataframe["ezqcid"].tolist() == ["SUB003", "SUB001", "SUB002", "SUB004"]
    assert window.dataframe["ezqcid"].tolist() == ["SUB001", "SUB002"]
    assert window.source_positions == (0, 1)


def test_invalid_column_or_operator_fails_loud_without_partial_result() -> None:
    service = TableViewService(_source())

    with pytest.raises(TableViewError, match="列不存在"):
        service.apply_state(
            service.default_state().with_conditions(
                (FilterCondition(column="missing", operator="==", value=1),)
            )
        )
    with pytest.raises(TableViewError, match="不支持"):
        service.apply_state(
            service.default_state().with_conditions(
                (FilterCondition(column="age", operator="contains", value="3"),)
            )
        )


def test_qc_identity_requires_nonblank_unique_ezqcid() -> None:
    valid = TableViewService(_source())
    valid_result = valid.apply_state(valid.default_state())
    assert valid.validate_qc_identity(valid_result, 1) == "SUB002"

    duplicate_source = pd.DataFrame({"ezqcid": ["SUB001", "SUB001"]})
    duplicate_service = TableViewService(duplicate_source)
    duplicate_result = duplicate_service.apply_state(duplicate_service.default_state())
    with pytest.raises(QcIdentityError, match="不唯一"):
        duplicate_service.validate_qc_identity(duplicate_result, 0)

    blank_source = pd.DataFrame({"ezqcid": ["  "]})
    blank_service = TableViewService(blank_source)
    blank_result = blank_service.apply_state(blank_service.default_state())
    with pytest.raises(QcIdentityError, match="为空"):
        blank_service.validate_qc_identity(blank_result, 0)


def test_source_dataframe_is_not_mutated_by_view_operations() -> None:
    source = _source()
    original = source.copy(deep=True)
    service = TableViewService(source)
    service.apply_state(
        service.default_state()
        .with_conditions((FilterCondition(column="age", operator=">", value=28),))
        .with_sort_rules((SortRule(column="score", ascending=False),))
    )

    pd.testing.assert_frame_equal(source, original)


def test_ezqcid_layout_must_remain_visible_first_and_pinned() -> None:
    service = TableViewService(_source())
    default = service.default_state()

    for columns in (
        replace(default.columns, hidden=("ezqcid",)),
        replace(default.columns, pinned=()),
        ColumnViewState(
            order=("age", "ezqcid", "site", "passed", "score"),
            pinned=("ezqcid",),
        ),
    ):
        with pytest.raises(TableViewError, match="ezqcid"):
            service.apply_state(replace(default, columns=columns))
