"""Tests for models.subject_table — P3-C / F-IMP-5 easyqcid identity guard."""

import pandas as pd
import pytest

from models.subject_table import SubjectTable


def test_subject_table_from_dataframe_requires_easyqcid_column() -> None:
    """F-IMP-5: easyqcid is THE join key. A subjects frame without it must fail
    loud at load, not silently at the first merge."""
    df = pd.DataFrame({"subid": ["S1", "S2"]})
    with pytest.raises(ValueError) as exc:
        SubjectTable.from_dataframe(df)
    assert "easyqcid" in str(exc.value)


@pytest.mark.parametrize(
    "identities",
    [
        ["S1", None],
        ["S1", ""],
        ["S1", " "],
    ],
)
def test_subject_table_rejects_any_missing_or_blank_easyqcid(identities) -> None:
    df = pd.DataFrame({"easyqcid": identities, "site": ["a", "b"]})
    with pytest.raises(ValueError, match="easyqcid"):
        SubjectTable.from_dataframe(df)


def test_subject_table_rejects_non_string_easyqcid_instead_of_coercing() -> None:
    df = pd.DataFrame({"easyqcid": pd.array([1, 2, 3], dtype="int64")})
    with pytest.raises(ValueError, match="字符串"):
        SubjectTable.from_dataframe(df)


def test_subject_table_rejects_duplicate_easyqcid() -> None:
    df = pd.DataFrame({"easyqcid": ["S1", "S1", "S2"]})
    with pytest.raises(ValueError, match="重复"):
        SubjectTable.from_dataframe(df)


def test_subject_table_rejects_case_only_easyqcid_collision() -> None:
    df = pd.DataFrame({"easyqcid": ["Sub01", "sub01"]})
    with pytest.raises(ValueError, match="case-only"):
        SubjectTable.from_dataframe(df)


def test_subject_table_from_csv_round_trip(tmp_path) -> None:
    csv = tmp_path / "easyqc_all.csv"
    csv.write_text("easyqcid,site\nS1,A\nS2,B\n", encoding="utf-8")
    table = SubjectTable.from_csv(csv)
    assert list(table.dataframe["easyqcid"]) == ["S1", "S2"]
    assert list(table.dataframe["site"]) == ["A", "B"]


def test_subject_table_from_csv_preserves_leading_zero_easyqcid(tmp_path) -> None:
    csv = tmp_path / "easyqc_all.csv"
    csv.write_text(
        "easyqcid,visit\n001,1\n01-A,2\n",
        encoding="utf-8",
    )

    table = SubjectTable.from_csv(csv)

    assert table.dataframe["easyqcid"].tolist() == ["001", "01-A"]
    assert table.dataframe["visit"].tolist() == [1, 2]


def test_subject_table_dataframe_property_returns_string_typed_copy() -> None:
    df = pd.DataFrame({"easyqcid": ["S1"], "site": ["A"]})
    table = SubjectTable.from_dataframe(df)
    assert table.dataframe["easyqcid"].dtype == object
