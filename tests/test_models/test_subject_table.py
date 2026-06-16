"""Tests for models.subject_table — P3-C / F-IMP-5 ezqcid identity guard."""

import pandas as pd
import pytest

from models.subject_table import SubjectTable


def test_subject_table_from_dataframe_requires_ezqcid_column() -> None:
    """F-IMP-5: ezqcid is THE join key. A subjects frame without it must fail
    loud at load, not silently at the first merge."""
    df = pd.DataFrame({"subid": ["S1", "S2"]})
    with pytest.raises(ValueError) as exc:
        SubjectTable.from_dataframe(df)
    assert "ezqcid" in str(exc.value)


def test_subject_table_rejects_all_nan_ezqcid() -> None:
    """F-IMP-5: an all-empty ezqcid column is useless for joins."""
    df = pd.DataFrame({"ezqcid": [None, None], "site": ["a", "b"]})
    with pytest.raises(ValueError):
        SubjectTable.from_dataframe(df)


def test_subject_table_coerces_ezqcid_to_string() -> None:
    """F-IMP-5 / AC-9: numeric-looking IDs must be string (stable join)."""
    df = pd.DataFrame({"ezqcid": pd.array([1, 2, 3], dtype="int64")})
    table = SubjectTable.from_dataframe(df)
    assert all(isinstance(v, str) for v in table.dataframe["ezqcid"])
    assert list(table.dataframe["ezqcid"]) == ["1", "2", "3"]


def test_subject_table_warns_on_duplicate_ezqcid_but_does_not_raise() -> None:
    """F-IMP-5: duplicate ezqcid is warned (real data may have legitimate
    duplicate rows), not fatal — but it must be visible."""
    df = pd.DataFrame({"ezqcid": ["S1", "S1", "S2"]})
    # should NOT raise
    table = SubjectTable.from_dataframe(df)
    assert len(table.dataframe) == 3


def test_subject_table_from_csv_round_trip(tmp_path) -> None:
    csv = tmp_path / "ezqc_all.csv"
    csv.write_text("ezqcid,site\nS1,A\nS2,B\n", encoding="utf-8")
    table = SubjectTable.from_csv(csv)
    assert list(table.dataframe["ezqcid"]) == ["S1", "S2"]
    assert list(table.dataframe["site"]) == ["A", "B"]


def test_subject_table_dataframe_property_returns_string_typed_copy() -> None:
    df = pd.DataFrame({"ezqcid": ["S1"], "site": ["A"]})
    table = SubjectTable.from_dataframe(df)
    assert table.dataframe["ezqcid"].dtype == object
