import pandas as pd
import pytest

from core.expression_parser import ExpressionError
from core.table_transform import TableTransformEngine, TableTransformError, legacy_select_filter_to_operations


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB002", "SUB003"],
            "age": [29, 31, 27],
            "sex": ["F", "M", "F"],
            "score": [3, 2, 5],
            "motion": [0.12, 0.30, 0.08],
        }
    )


def test_select_columns_supports_reorder_and_include_rest() -> None:
    result = TableTransformEngine().select_columns(_df(), ["sex", "easyqcid"], include_rest=True)

    assert list(result.columns) == ["sex", "easyqcid", "age", "score", "motion"]


def test_filter_rows_supports_structured_conditions() -> None:
    result = TableTransformEngine().filter_rows(
        _df(),
        [
            {"column": "age", "operator": ">=", "value": 29},
            {"column": "sex", "operator": "in", "value": ["F"]},
        ],
    )

    assert result["easyqcid"].tolist() == ["SUB001"]


@pytest.mark.parametrize("operator", ("contains", "startswith", "endswith"))
def test_text_filters_never_turn_missing_values_into_nan_text(
    operator: str,
) -> None:
    source = pd.DataFrame(
        {
            "easyqcid": ["MISSING1", "MISSING2", "MISSING3", "TEXT"],
            "label": pd.Series([pd.NA, None, float("nan"), "nan"], dtype=object),
        }
    )

    result = TableTransformEngine().filter_rows(
        source,
        [{"column": "label", "operator": operator, "value": "nan"}],
    )

    assert result["easyqcid"].tolist() == ["TEXT"]


def test_filter_rows_supports_expression_condition() -> None:
    result = TableTransformEngine().filter_rows(
        _df(),
        [{"expression": "(age >= 29) and (sex in ['F', 'M']) and (motion < 0.2)"}],
    )

    assert result["easyqcid"].tolist() == ["SUB001"]


def test_sort_rows_supports_multiple_keys() -> None:
    result = TableTransformEngine().sort_rows(
        _df(),
        [
            {"column": "sex", "ascending": True},
            {"column": "score", "ascending": False},
        ],
    )

    assert result["easyqcid"].tolist() == ["SUB003", "SUB001", "SUB002"]


def test_derive_column_uses_restricted_expression_parser() -> None:
    result = TableTransformEngine().derive_column(_df(), "qc_pass", "(score >= 3) and (motion < 0.2)")

    assert result["qc_pass"].tolist() == [True, False, True]


def test_rename_and_drop_columns_validate_column_names() -> None:
    engine = TableTransformEngine()

    renamed = engine.rename_columns(_df(), {"motion": "motion_fd"})
    assert "motion_fd" in renamed.columns
    assert "motion" not in renamed.columns

    dropped = engine.drop_columns(renamed, ["motion_fd"])
    assert "motion_fd" not in dropped.columns

    with pytest.raises(TableTransformError):
        engine.rename_columns(_df(), {"missing": "new_name"})
    with pytest.raises(TableTransformError):
        engine.drop_columns(_df(), ["missing"])


def test_column_operations_reject_duplicate_output_names() -> None:
    engine = TableTransformEngine()
    duplicate_source = pd.DataFrame([[1, 2]], columns=["value", "value"])

    with pytest.raises(TableTransformError, match="重复列"):
        engine.select_columns(_df(), ["easyqcid", "easyqcid"])
    with pytest.raises(TableTransformError, match="重复列"):
        engine.select_columns(duplicate_source, ["value"])
    with pytest.raises(TableTransformError, match="重复列"):
        engine.rename_columns(_df(), {"age": "sex"})


def test_expression_parser_rejects_arbitrary_python() -> None:
    with pytest.raises(ExpressionError):
        TableTransformEngine().derive_column(_df(), "bad", "__import__('os').system('echo unsafe')")


def test_merge_tables_validates_how_and_keys() -> None:
    left = _df()[["easyqcid", "age"]]
    right = pd.DataFrame({"easyqcid": ["SUB001", "SUB003"], "group": ["A", "B"]})

    result = TableTransformEngine().merge_tables(left, right, on=["easyqcid"], how="left")

    assert result.loc[0, "group"] == "A"
    assert pd.isna(result.loc[1, "group"])
    assert result.loc[2, "group"] == "B"
    with pytest.raises(TableTransformError):
        TableTransformEngine().merge_tables(left, right, on=["easyqcid"], how="cross")


def test_merge_tables_requires_explicit_many_to_many_authorization() -> None:
    left = pd.DataFrame(
        {"easyqcid": ["SUB001", "SUB001"], "visit": [1, 2]}
    )
    right = pd.DataFrame(
        {"easyqcid": ["SUB001", "SUB001"], "site": ["A", "B"]}
    )
    engine = TableTransformEngine()

    with pytest.raises(TableTransformError, match="多对多"):
        engine.merge_tables(left, right, on=["easyqcid"])

    result = engine.merge_tables(
        left,
        right,
        on=["easyqcid"],
        relationship="many_to_many",
    )

    assert len(result) == 4


def test_merge_tables_enforces_declared_relationship_and_unique_columns() -> None:
    left = pd.DataFrame(
        {"easyqcid": ["SUB001", "SUB002"], "site": ["A", "B"]}
    )
    duplicate_right = pd.DataFrame(
        {"easyqcid": ["SUB001", "SUB001"], "group": [1, 2]}
    )
    colliding_left = left.assign(site_x=["old-A", "old-B"])
    overlapping_right = pd.DataFrame(
        {"easyqcid": ["SUB001", "SUB002"], "site": ["C", "D"]}
    )
    engine = TableTransformEngine()

    with pytest.raises(TableTransformError, match="one_to_one"):
        engine.merge_tables(
            left,
            duplicate_right,
            on=["easyqcid"],
            relationship="one_to_one",
        )
    with pytest.raises(TableTransformError, match="重复列"):
        engine.merge_tables(colliding_left, overlapping_right, on=["easyqcid"])


def test_aggregate_flattens_columns_and_allows_whitelisted_functions() -> None:
    result = TableTransformEngine().aggregate(
        _df(),
        group_by=["sex"],
        metrics={"score": ["mean", "max"], "easyqcid": ["count"]},
    )

    assert list(result.columns) == ["sex", "score_mean", "score_max", "easyqcid_count"]
    assert result.loc[result["sex"] == "F", "score_max"].iloc[0] == 5
    with pytest.raises(TableTransformError):
        TableTransformEngine().aggregate(_df(), group_by=["sex"], metrics={"score": ["std"]})


def test_apply_runs_structured_operations_in_order() -> None:
    result = TableTransformEngine().apply(
        _df(),
        [
            {"operation": "derive_column", "name": "qc_pass", "expression": "score >= 3"},
            {"operation": "filter_rows", "conditions": [{"column": "qc_pass", "operator": "==", "value": True}]},
            {"operation": "sort_rows", "sort_keys": [{"column": "score", "ascending": False}]},
            {"operation": "select_columns", "columns": ["easyqcid", "qc_pass"]},
        ],
    )

    assert result.to_dict("records") == [
        {"easyqcid": "SUB003", "qc_pass": True},
        {"easyqcid": "SUB001", "qc_pass": True},
    ]


def test_apply_supports_merge_operation() -> None:
    right = pd.DataFrame({"easyqcid": ["SUB001", "SUB003"], "site": ["A", "B"]})

    result = TableTransformEngine().apply(
        _df()[["easyqcid", "age"]],
        [
            {
                "operation": "merge_tables",
                "right": right,
                "on": ["easyqcid"],
                "how": "left",
                "relationship": "one_to_one",
            }
        ],
    )

    assert result.loc[0, "site"] == "A"
    assert pd.isna(result.loc[1, "site"])
    assert result.loc[2, "site"] == "B"


def test_limit_output_rejects_rows_or_columns_instead_of_truncating() -> None:
    source = _df()

    with pytest.raises(TableTransformError, match="行数.*超过限制"):
        TableTransformEngine(max_rows=2).apply(source, [])
    with pytest.raises(TableTransformError, match="列数.*超过限制"):
        TableTransformEngine(max_columns=3).apply(source, [])

    assert source.shape == (3, 5)


def test_legacy_select_filter_converts_simple_where_conditions() -> None:
    operations = legacy_select_filter_to_operations("SELECT * FROM df WHERE sex = 'F' and score >= 3")

    assert operations == [
        {
            "operation": "filter_rows",
            "conditions": [
                {"column": "sex", "operator": "==", "value": "F"},
                {"column": "score", "operator": ">=", "value": 3},
            ],
        }
    ]
    result = TableTransformEngine().apply(_df(), operations)
    assert result["easyqcid"].tolist() == ["SUB001", "SUB003"]


def test_legacy_select_filter_converts_select_all_to_noop() -> None:
    assert legacy_select_filter_to_operations("SELECT * FROM df") == []


def test_legacy_select_filter_rejects_complex_sql_shapes() -> None:
    with pytest.raises(TableTransformError):
        legacy_select_filter_to_operations("SELECT * FROM df; SELECT * FROM df")
    with pytest.raises(TableTransformError):
        legacy_select_filter_to_operations("SELECT easyqcid FROM df")
    with pytest.raises(TableTransformError):
        legacy_select_filter_to_operations("SELECT * FROM df WHERE sex = 'F' OR score >= 3")
