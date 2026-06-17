"""Tests for core.shorthand_filter — convert short expressions to operation dicts."""

import pytest

from core.shorthand_filter import parse_shorthand, ShorthandParseError


def test_filter_expression_becomes_filter_rows() -> None:
    ops = parse_shorthand(filter_expr="age > 30")
    assert len(ops) == 1
    assert ops[0] == {
        "operation": "filter_rows",
        "conditions": [{"expression": "age > 30"}],
        "logic": "and",
    }


def test_derive_assignment_becomes_derive_column() -> None:
    ops = parse_shorthand(derive_expr="total = a + b")
    assert ops[0] == {
        "operation": "derive_column",
        "name": "total",
        "expression": "a + b",
    }


def test_sort_with_desc() -> None:
    ops = parse_shorthand(sort_expr="score desc")
    assert ops[0] == {
        "operation": "sort_rows",
        "sort_keys": [{"column": "score", "ascending": False}],
    }


def test_sort_default_ascending() -> None:
    ops = parse_shorthand(sort_expr="score")
    assert ops[0]["sort_keys"][0]["ascending"] is True


def test_select_columns_comma_separated() -> None:
    ops = parse_shorthand(select_expr="ezqcid, age, score")
    assert ops[0] == {
        "operation": "select_columns",
        "columns": ["ezqcid", "age", "score"],
    }


def test_drop_columns() -> None:
    ops = parse_shorthand(drop_expr="motion, age")
    assert ops[0] == {"operation": "drop_columns", "columns": ["motion", "age"]}


def test_rename_arrow_syntax() -> None:
    ops = parse_shorthand(rename_expr="motion -> motion_fd")
    assert ops[0] == {
        "operation": "rename_columns",
        "mapping": {"motion": "motion_fd"},
    }


def test_rename_multiple() -> None:
    ops = parse_shorthand(rename_expr="a -> x, b -> y")
    assert ops[0]["mapping"] == {"a": "x", "b": "y"}


def test_all_empty_returns_empty_list() -> None:
    assert parse_shorthand() == []


def test_multiple_fields_combined_in_order() -> None:
    """filter → derive → sort → select → drop → rename execution order."""
    ops = parse_shorthand(
        filter_expr="age > 30",
        derive_expr="pass = score >= 3",
        sort_expr="score desc",
        select_expr="ezqcid, score",
    )
    assert [op["operation"] for op in ops] == [
        "filter_rows",
        "derive_column",
        "sort_rows",
        "select_columns",
    ]


def test_whitespace_only_treated_as_empty() -> None:
    assert parse_shorthand(filter_expr="   ") == []


def test_derive_without_equals_raises() -> None:
    with pytest.raises(ShorthandParseError):
        parse_shorthand(derive_expr="no equals sign")


def test_rename_without_arrow_raises() -> None:
    with pytest.raises(ShorthandParseError):
        parse_shorthand(rename_expr="no_arrow_here")


def test_shorthand_to_string_roundtrip() -> None:
    """serialize back to a string for select_filter persistence."""
    from core.shorthand_filter import shorthand_to_string
    s = shorthand_to_string(filter_expr="age > 30", sort_expr="score desc")
    assert "filter:" in s
    assert "sort:" in s
