"""Tests for core.assignment — grouping column + balanced rater assignment."""

import pandas as pd
import pytest

from core.assignment import add_group_column, assign_raters


# ---- add_group_column ----

def test_group_column_groups_consecutive_rows_by_size() -> None:
    df = pd.DataFrame({"ezqcid": [f"S{i:03d}" for i in range(10)]})

    out = add_group_column(df, size=3)

    assert out["group"].tolist() == [1, 1, 1, 2, 2, 2, 3, 3, 3, 4]


def test_group_column_respects_sorted_order() -> None:
    """Group numbers follow the CURRENT row order (caller sorts first)."""
    df = pd.DataFrame({"ezqcid": ["S003", "S001", "S002"]})

    out = add_group_column(df, size=1)

    assert out["group"].tolist() == [1, 2, 3]
    assert out["ezqcid"].tolist() == ["S003", "S001", "S002"]


def test_group_column_custom_name_and_size() -> None:
    df = pd.DataFrame({"x": range(5)})

    out = add_group_column(df, size=2, column="batch_group")

    assert "group" not in out.columns
    assert out["batch_group"].tolist() == [1, 1, 2, 2, 3]


def test_group_column_invalid_size_raises() -> None:
    with pytest.raises(ValueError):
        add_group_column(pd.DataFrame({"x": [1]}), size=0)


def test_group_column_does_not_mutate_input() -> None:
    df = pd.DataFrame({"x": range(5)})
    add_group_column(df, size=2)
    assert "group" not in df.columns


# ---- assign_raters ----

def test_assign_raters_expands_rows_per_image() -> None:
    df = pd.DataFrame({"ezqcid": ["S1", "S2", "S3"]})

    out = assign_raters(df, raters=["甲", "乙"], per_image=1)

    assert len(out) == 3
    assert set(out["rater"]) == {"甲", "乙"}
    assert out["ezqcid"].tolist() == ["S1", "S2", "S3"]


def test_assign_raters_two_per_image_doubles_rows() -> None:
    df = pd.DataFrame({"ezqcid": [f"S{i}" for i in range(100)]})

    out = assign_raters(df, raters=["甲", "乙", "丙", "丁"], per_image=2)

    assert len(out) == 200
    # each image appears exactly twice
    counts = out["ezqcid"].value_counts()
    assert (counts == 2).all()
    # each image's two rows have two DIFFERENT raters
    for ezqcid, grp in out.groupby("ezqcid"):
        assert grp["rater"].nunique() == 2


def test_assign_raters_balanced_totals() -> None:
    """6000 images / 4 raters / 2 per image -> each rater gets 3000 ± 2."""
    df = pd.DataFrame({"ezqcid": [f"S{i}" for i in range(6000)]})

    out = assign_raters(df, raters=["甲", "乙", "丙", "丁"], per_image=2)

    counts = out["rater"].value_counts()
    assert set(counts.values) <= {3000} or (counts.max() - counts.min()) <= 2


def test_assign_raters_seed_reproducible() -> None:
    df = pd.DataFrame({"ezqcid": [f"S{i}" for i in range(50)]})

    a = assign_raters(df, raters=["甲", "乙", "丙"], per_image=2, seed=42)
    b = assign_raters(df, raters=["甲", "乙", "丙"], per_image=2, seed=42)

    assert a["rater"].tolist() == b["rater"].tolist()


def test_assign_raters_seedless_runs_differ_eventually() -> None:
    df = pd.DataFrame({"ezqcid": [f"S{i}" for i in range(200)]})

    a = assign_raters(df, raters=["甲", "乙", "丙", "丁"], per_image=2)
    b = assign_raters(df, raters=["甲", "乙", "丙", "丁"], per_image=2)

    # random without seed: extremely unlikely identical orderings
    assert a["rater"].tolist() != b["rater"].tolist()


def test_assign_raters_per_image_exceeds_raters_raises() -> None:
    df = pd.DataFrame({"ezqcid": ["S1"]})
    with pytest.raises(ValueError):
        assign_raters(df, raters=["甲", "乙"], per_image=3)


def test_assign_raters_empty_raters_raises() -> None:
    df = pd.DataFrame({"ezqcid": ["S1"]})
    with pytest.raises(ValueError):
        assign_raters(df, raters=[], per_image=1)


def test_assign_raters_custom_column_name_preserves_other_columns() -> None:
    df = pd.DataFrame({"ezqcid": ["S1", "S2"], "age": [10, 20]})

    out = assign_raters(df, raters=["甲", "乙"], per_image=1, column="reviewer")

    assert "rater" not in out.columns
    assert set(out["reviewer"]) == {"甲", "乙"}
    assert out["age"].tolist() == [10, 20] or set(out["age"]) == {10, 20}


def test_assign_raters_does_not_mutate_input() -> None:
    df = pd.DataFrame({"ezqcid": ["S1", "S2"]})
    assign_raters(df, raters=["甲", "乙"], per_image=2)
    assert "rater" not in df.columns
    assert len(df) == 2


# ---- group orders: sequential / reverse / random ----

def test_group_reverse_numbers_from_last_row() -> None:
    df = pd.DataFrame({"x": range(5)})

    out = add_group_column(df, size=3, order="reverse")

    assert out["group"].tolist() == [2, 2, 1, 1, 1]


def test_group_random_balanced_and_reproducible() -> None:
    df = pd.DataFrame({"x": range(12)})

    a = add_group_column(df, size=3, order="random", seed=5)
    b = add_group_column(df, size=3, order="random", seed=5)

    assert a["group"].tolist() == b["group"].tolist()
    # every group has exactly `size` members
    counts = a["group"].value_counts().to_dict()
    assert counts == {1: 3, 2: 3, 3: 3, 4: 3}


def test_group_random_covers_all_groups() -> None:
    df = pd.DataFrame({"x": range(30)})

    out = add_group_column(df, size=10, order="random")

    assert set(out["group"]) == {1, 2, 3}
    assert out["group"].value_counts().tolist() == [10, 10, 10]


def test_group_invalid_order_raises() -> None:
    with pytest.raises(ValueError):
        add_group_column(pd.DataFrame({"x": [1]}), size=1, order="bogus")
