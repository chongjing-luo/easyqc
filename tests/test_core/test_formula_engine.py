from __future__ import annotations

import inspect

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
