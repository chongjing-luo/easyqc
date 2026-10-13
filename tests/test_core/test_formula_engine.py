from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

import core.formula_engine as formula_engine_module
from core.formula_engine import FormulaEngine
from core.formula_parser import FormulaValidationError


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "a": [10, 5, pd.NA, 8],
            "b": [2, 0, 4, 2],
            "flag": [True, False, True, False],
            "other": [False, True, True, False],
            "raw": ["4", "bad", pd.NA, "2.5"],
            "fallback": ["x", "y", "z", "w"],
        },
        index=[3, 5, 8, 13],
    )


def test_arithmetic_precedence_unary_and_comparisons_are_vectorized() -> None:
    engine = FormulaEngine()
    frame = _frame()

    arithmetic = engine.evaluate(frame, "-[a] + [b] * 3")
    comparison = engine.evaluate(frame, "([a] >= 8) AND NOT [other]")

    assert arithmetic.values.tolist()[:2] == [-4, -5]
    assert pd.isna(arithmetic.values.iloc[2])
    assert arithmetic.values.iloc[3] == -2
    assert not arithmetic.has_errors
    assert comparison.values.tolist() == [True, False, False, True]
    assert not comparison.has_errors


@pytest.mark.parametrize(
    ("operator", "expected"),
    (
        ("=", [False, False, False, False]),
        ("<>", [True, True, True, True]),
        ("<", [False, False, False, False]),
        ("<=", [False, False, False, False]),
        (">", [True, True, False, True]),
        (">=", [True, True, False, True]),
    ),
)
def test_each_comparison_operator_preserves_index(
    operator: str,
    expected: list[bool],
) -> None:
    result = FormulaEngine().evaluate(_frame(), f"[a] {operator} [b]")

    assert result.values.fillna(False).tolist() == expected
    assert result.values.index.tolist() == [3, 5, 8, 13]
    assert not result.has_errors


def test_mixed_comparison_marks_only_the_incomparable_row() -> None:
    frame = pd.DataFrame(
        {
            "left": pd.Series(
                [2, "z", "bad"],
                index=[11, 17, 23],
                dtype=object,
            ),
            "right": pd.Series(
                [1, "a", 0],
                index=[11, 17, 23],
                dtype=object,
            ),
        },
        index=[11, 17, 23],
    )

    result = FormulaEngine().evaluate(frame, "[left] > [right]")

    assert result.values.tolist() == [True, True, False]
    assert result.values.index.tolist() == [11, 17, 23]
    assert pd.isna(result.errors.loc[11])
    assert pd.isna(result.errors.loc[17])
    assert result.errors.loc[23] == "> 无法比较"


def test_division_by_zero_is_a_row_error_not_a_python_exception() -> None:
    result = FormulaEngine().evaluate(_frame(), "[a] / [b]")

    assert result.values.iloc[0] == 5
    assert pd.isna(result.values.iloc[1])
    assert pd.isna(result.values.iloc[2])
    assert result.values.iloc[3] == 4
    assert pd.isna(result.errors.iloc[0])
    assert "除数不能为零" in result.errors.iloc[1]
    assert pd.isna(result.errors.iloc[2])
    assert pd.isna(result.errors.iloc[3])


def test_arithmetic_does_not_silently_coerce_text_values() -> None:
    result = FormulaEngine().evaluate(_frame(), "[raw] + 1")

    assert result.errors.tolist() == [
        "+ 需要数值",
        "+ 需要数值",
        pd.NA,
        "+ 需要数值",
    ]
    assert result.values.isna().all()


def test_if_keeps_errors_only_from_the_selected_branch() -> None:
    result = FormulaEngine().evaluate(
        _frame(),
        'IF([b] <> 0, [a] / [b], "zero")',
    )

    assert result.values.iloc[0] == 5
    assert result.values.iloc[1] == "zero"
    assert pd.isna(result.values.iloc[2])
    assert result.values.iloc[3] == 4
    assert not result.has_errors


def test_iferror_consumes_only_rows_with_expression_errors() -> None:
    result = FormulaEngine().evaluate(
        _frame(),
        "IFERROR(VALUE([raw]), [fallback])",
    )

    assert result.values.tolist() == [4.0, "y", pd.NA, 2.5]
    assert not result.has_errors


def test_blank_isblank_and_coalesce_have_explicit_null_semantics() -> None:
    frame = _frame()
    frame["optional"] = [pd.NA, "present", pd.NA, ""]

    blank = FormulaEngine().evaluate(frame, "BLANK()")
    isblank = FormulaEngine().evaluate(frame, "ISBLANK([optional])")
    coalesced = FormulaEngine().evaluate(
        frame,
        'COALESCE([optional], [fallback], "last")',
    )

    assert blank.values.isna().all()
    assert isblank.values.tolist() == [True, False, True, False]
    assert coalesced.values.tolist() == ["x", "present", "z", ""]
    assert not blank.has_errors
    assert not isblank.has_errors
    assert not coalesced.has_errors


def test_coalesce_does_not_treat_row_errors_as_blanks() -> None:
    result = FormulaEngine().evaluate(
        _frame(),
        "COALESCE(VALUE([raw]), [fallback])",
    )

    assert result.values.tolist() == [4.0, pd.NA, "z", 2.5]
    assert pd.isna(result.errors.iloc[0])
    assert result.errors.iloc[1] == "VALUE 无法转换为数值"
    assert pd.isna(result.errors.iloc[2])
    assert pd.isna(result.errors.iloc[3])


def test_logical_input_type_errors_remain_row_aligned() -> None:
    result = FormulaEngine().evaluate(_frame(), '[raw] AND TRUE')

    assert result.errors.notna().all()
    assert set(result.errors.dropna()) == {"AND 需要布尔值"}


def test_wrong_function_arity_fails_before_producing_values() -> None:
    with pytest.raises(FormulaValidationError, match="IF.*3"):
        FormulaEngine().evaluate(_frame(), "IF(TRUE, 1)")


def test_formula_evaluation_never_mutates_the_source() -> None:
    frame = _frame()
    original = frame.copy(deep=True)

    result = FormulaEngine().evaluate(frame, "[a] + 1")

    pd.testing.assert_frame_equal(frame, original)
    assert result.values.index.equals(frame.index)
    assert result.errors.index.equals(frame.index)


def test_evaluator_has_no_per_row_or_dynamic_code_execution_path() -> None:
    source = inspect.getsource(formula_engine_module)

    for forbidden in (
        ".apply(",
        ".iterrows(",
        ".itertuples(",
        "np.vectorize",
        "eval(",
        "exec(",
    ):
        assert forbidden not in source


def test_random_formula_is_reproducible_prefix_stable_and_locally_seeded() -> None:
    engine = FormulaEngine()
    preview = pd.DataFrame(index=[3, 5, 8, 13, 21])
    full = pd.DataFrame(index=range(100_000))
    expected = [
        0.4963776898696063,
        0.45833075236414145,
        0.13071530424120104,
        0.8753057054212088,
        0.9207502768943099,
    ]
    np.random.seed(314159)
    expected_legacy_values = np.random.random(4)
    np.random.seed(314159)

    preview_result = engine.evaluate(preview, "RANDOM(20260806)")
    full_result = engine.evaluate(full, "RANDOM(20260806)")
    actual_legacy_values = np.random.random(4)

    assert preview_result.values.tolist() == pytest.approx(expected)
    assert full_result.values.iloc[:5].tolist() == pytest.approx(expected)
    assert preview_result.values.index.tolist() == [3, 5, 8, 13, 21]
    assert preview_result.values.dtype == np.dtype("float64")
    assert preview_result.values.between(0.0, 1.0, inclusive="left").all()
    assert not preview_result.has_errors
    assert not full_result.has_errors
    np.testing.assert_array_equal(actual_legacy_values, expected_legacy_values)


@pytest.mark.parametrize(
    "formula",
    (
        "RANDOM([a])",
        "RANDOM(1.5)",
        "RANDOM(-1)",
        "RANDOM(TRUE)",
        'RANDOM("1")',
        "RANDOM(4294967296)",
        "RANDOM()",
        "RANDOM(1, 2)",
    ),
)
def test_random_formula_rejects_invalid_seed_literals(formula: str) -> None:
    with pytest.raises(FormulaValidationError, match="RANDOM"):
        FormulaEngine().evaluate(pd.DataFrame({"a": [1, 2]}), formula)


# ---- TEXT: value -> text with whole-number decimal dropping ----

def test_text_drops_decimal_point_for_whole_numbers() -> None:
    frame = _frame()
    frame["num"] = [3.0, 2.5, pd.NA, 40.0]

    result = FormulaEngine().evaluate(frame, "TEXT([num])")

    assert result.values.tolist() == ["3", "2.5", pd.NA, "40"]
    assert not result.has_errors


def test_text_passes_text_through_unchanged() -> None:
    result = FormulaEngine().evaluate(_frame(), "TEXT([fallback])")

    for produced, original in zip(result.values, _frame()["fallback"]):
        if pd.isna(original):
            assert pd.isna(produced)
        else:
            assert produced == str(original)
    assert not result.has_errors


def test_text_propagates_row_errors_as_blanks() -> None:
    result = FormulaEngine().evaluate(
        _frame(),
        "TEXT(VALUE([raw]))",
    )

    assert result.values.iloc[1] is pd.NA or pd.isna(result.values.iloc[1])
    assert result.errors.iloc[1] == "VALUE 无法转换为数值"


def _raters_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "s1": [3.0, 2.0, pd.NA, pd.NA],
            "s2": [3.0, pd.NA, 4.0, pd.NA],
            "s3": [3.0, 1.0, pd.NA, pd.NA],
            "label": ["a", "b", pd.NA, "d"],
        },
        index=[3, 5, 8, 13],
    )


@pytest.mark.parametrize(
    ("formula", "expected"),
    (
        ("SUM([s1], [s2], [s3])", [9.0, 3.0, 4.0, pd.NA]),
        ("AVERAGE([s1], [s2], [s3])", [3.0, 1.5, 4.0, pd.NA]),
        ("MIN([s1], [s2], [s3])", [3.0, 1.0, 4.0, pd.NA]),
        ("MAX([s1], [s2], [s3])", [3.0, 2.0, 4.0, pd.NA]),
        ("MEDIAN([s1], [s2], [s3])", [3.0, 1.5, 4.0, pd.NA]),
        ("MIN([s1])", [3.0, 2.0, pd.NA, pd.NA]),
        ("COUNT([s1], [s2], [s3], [label])", [4, 3, 1, 1]),
        ("COUNT([s1], [s2], [s3])", [3, 2, 1, 0]),
    ),
)
def test_aggregates_skip_blanks_and_stay_blank_when_all_blank(
    formula: str,
    expected: list[object],
) -> None:
    result = FormulaEngine().evaluate(_raters_frame(), formula)

    assert result.values.tolist() == expected
    assert result.values.index.tolist() == [3, 5, 8, 13]
    assert not result.has_errors


def test_stdev_is_sample_based_and_blank_below_two_values() -> None:
    result = FormulaEngine().evaluate(_raters_frame(), "STDEV([s1], [s2], [s3])")

    assert result.values.iloc[0] == 0.0
    assert result.values.iloc[1] == pytest.approx(0.7071067811865476)
    assert result.values.iloc[2:] .isna().all()
    assert not result.has_errors


def test_aggregates_mark_non_numeric_rows_without_touching_other_rows() -> None:
    result = FormulaEngine().evaluate(_raters_frame(), "SUM([s1], [label])")

    assert result.values.isna().all()
    assert result.errors.notna().tolist() == [True, True, False, True]
    assert (result.errors.dropna() == "SUM 需要数值").all()


def test_contains_reports_false_on_miss_and_keeps_blank_rows_blank() -> None:
    frame = pd.DataFrame({"id": ["sub_01_rest", "sub_02_anat", pd.NA, ""]})

    hit = FormulaEngine().evaluate(frame, 'CONTAINS("rest", [id])')
    miss = FormulaEngine().evaluate(frame, 'CONTAINS("zzz", [id])')

    assert hit.values.tolist() == [True, False, pd.NA, False]
    assert not hit.has_errors
    assert miss.values.tolist() == [False, False, pd.NA, False]
    assert not miss.has_errors


def test_splitpart_counts_from_both_ends_and_errors_out_of_range() -> None:
    frame = pd.DataFrame(
        {"id": ["CCNPCKG0001_01_rest01", "CCNPCKG0002_02_rest02", pd.NA, "short"]}
    )

    head = FormulaEngine().evaluate(frame, 'SPLITPART([id], "_", 1)')
    tail = FormulaEngine().evaluate(frame, 'SPLITPART([id], "_", -1)')

    assert head.values.tolist() == [
        "CCNPCKG0001",
        "CCNPCKG0002",
        pd.NA,
        "short",
    ]
    assert not head.has_errors
    assert tail.values.tolist() == ["rest01", "rest02", pd.NA, "short"]
    assert not tail.has_errors

    beyond = FormulaEngine().evaluate(frame, 'SPLITPART([id], "_", 5)')
    assert beyond.values.isna().all()
    assert "SPLITPART 段序号超出范围" in beyond.errors.iloc[0]
    assert beyond.errors.isna().iloc[2]


@pytest.mark.parametrize(
    "formula",
    (
        'SPLITPART([id], "_", 0)',
        'SPLITPART([id], "", 1)',
        'SPLITPART([id], [sep], 1)',
    ),
)
def test_splitpart_rejects_invalid_literal_parameters(formula: str) -> None:
    frame = pd.DataFrame({"id": ["a_b"], "sep": ["_"]})

    with pytest.raises(FormulaValidationError):
        FormulaEngine().evaluate(frame, formula)


def test_between_uses_closed_interval_and_keeps_blanks_blank() -> None:
    result = FormulaEngine().evaluate(_raters_frame(), "BETWEEN([s1], 1, 2)")

    assert result.values.tolist() == [False, True, pd.NA, pd.NA]
    assert not result.has_errors


def test_mod_follows_divisor_sign_and_errors_on_zero_divisor() -> None:
    frame = pd.DataFrame({"n": [7.0, -7.0, pd.NA], "d": [3.0, -3.0, 2.0]})

    result = FormulaEngine().evaluate(frame, "MOD([n], [d])")

    assert result.values.tolist() == [1.0, -1.0, pd.NA]
    assert not result.has_errors

    zero = FormulaEngine().evaluate(frame, "MOD([n], 0)")
    assert zero.values.isna().all()
    assert (zero.errors.dropna() == "除数不能为零").all()


def test_int_truncates_toward_zero() -> None:
    frame = pd.DataFrame({"run": [1.9, -1.9, pd.NA]})

    result = FormulaEngine().evaluate(frame, "INT([run])")

    assert result.values.tolist() == [1.0, -1.0, pd.NA]
    assert not result.has_errors


def test_ifs_returns_first_true_pair_and_falls_back_to_default() -> None:
    result = FormulaEngine().evaluate(
        _raters_frame(),
        'IFS([s1] >= 3, "pass", [s1] >= 1, "review", "fail")',
    )

    assert result.values.tolist() == ["pass", "review", "fail", "fail"]
    assert not result.has_errors


def test_ifs_condition_errors_only_block_rows_still_undecided() -> None:
    frame = pd.DataFrame(
        {
            "flag": [True, False, False],
            "mixed": pd.Series(["a", 1, "c"], dtype=object),
        }
    )
    # Row 0 resolves at the first condition, so the later mixed-type numeric
    # comparison must not touch it; row 1 stays numeric (False, falls to the
    # default) and row 2 cannot be compared and fails loud.
    result = FormulaEngine().evaluate(
        frame,
        'IFS([flag], "done", [mixed] >= 3, "never", "default")',
    )

    assert result.values.iloc[0] == "done"
    assert result.values.iloc[1] == "default"
    assert pd.isna(result.values.iloc[2])
    assert result.errors.notna().tolist() == [False, False, True]


@pytest.mark.parametrize(
    "formula",
    (
        'IFS([flag], "a", [flag], "b")',
        'IFS([flag], "a", [flag], "b", [flag], "c")',
    ),
)
def test_ifs_rejects_arguments_without_a_trailing_default(formula: str) -> None:
    frame = pd.DataFrame({"flag": [True]})

    with pytest.raises(FormulaValidationError, match="成对再加一个默认值"):
        FormulaEngine().evaluate(frame, formula)
