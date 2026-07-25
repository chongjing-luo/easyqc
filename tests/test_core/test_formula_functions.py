from __future__ import annotations

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from core.formula_engine import FormulaEngine
from core.formula_parser import (
    ALLOWED_FUNCTION_NAMES,
    FormulaParser,
    FormulaValidationError,
)
from models.formula_function import (
    FORMULA_FUNCTION_CATALOG,
    FormulaFunctionSpec,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "text": ["  Alpha  ", "βeta_02", pd.NA, "无分隔"],
            "path": [
                "/data/A/file.nii.gz",
                r"C:\data\B\scan.txt",
                "plain",
                pd.NA,
            ],
            "number": [-1.24, 2.56, pd.NA, 4.04],
            "raw number": ["4", "bad", pd.NA, "2.5"],
            "flag": [True, False, True, False],
            "fallback": ["A", "B", "C", "D"],
            "count": [1, 2, 3, 4],
            "delimiter": ["_", "_", "_", "_"],
        },
        index=[11, 14, 20, 25],
    )


def _assert_values(actual: pd.Series, expected: list[object]) -> None:
    assert actual.index.tolist() == [11, 14, 20, 25]
    for value, expected_value in zip(actual.tolist(), expected):
        if pd.isna(expected_value):
            assert pd.isna(value)
        else:
            assert value == expected_value


def test_function_catalog_is_complete_immutable_bilingual_and_parseable() -> None:
    catalog = FormulaEngine.function_catalog()
    names = tuple(spec.name for spec in catalog)

    assert catalog is FORMULA_FUNCTION_CATALOG
    assert len(names) == len(set(names))
    assert frozenset(names) == ALLOWED_FUNCTION_NAMES
    assert names == tuple(sorted(names))
    for spec in catalog:
        assert isinstance(spec, FormulaFunctionSpec)
        assert spec.category
        assert spec.signature.startswith(f"{spec.name}(")
        assert spec.description_zh
        assert spec.description_en
        assert FormulaParser().parse(spec.example).root
    with pytest.raises(FrozenInstanceError):
        catalog[0].signature = "changed"  # type: ignore[misc]


def test_every_catalog_entry_has_a_vector_evaluator_handler() -> None:
    frame = pd.DataFrame(
        {
            "value": ["present"],
            "filename": ["sub_001.nii.gz"],
            "score": [4],
            "raw": ["2.5"],
            "number": [2.56],
            "path": [r"C:\data\sub_001.nii.gz"],
            "label": [" QC-pass "],
        },
        index=[37],
    )
    engine = FormulaEngine()

    for spec in FORMULA_FUNCTION_CATALOG:
        populated = engine.evaluate(frame, spec.example)
        empty = engine.evaluate(frame.iloc[0:0], spec.example)
        assert populated.values.index.tolist() == [37], spec.name
        assert not populated.has_errors, spec.name
        assert empty.values.empty, spec.name
        assert empty.errors.empty, spec.name


def test_text_cleanup_case_and_length_are_unicode_safe() -> None:
    engine = FormulaEngine()
    frame = _frame()

    trimmed = engine.evaluate(frame, "TRIM([text])")
    upper = engine.evaluate(frame, "UPPER(TRIM([text]))")
    lower = engine.evaluate(frame, "LOWER(TRIM([text]))")
    length = engine.evaluate(frame, "LEN(TRIM([text]))")

    _assert_values(trimmed.values, ["Alpha", "βeta_02", pd.NA, "无分隔"])
    _assert_values(upper.values, ["ALPHA", "ΒETA_02", pd.NA, "无分隔"])
    _assert_values(lower.values, ["alpha", "βeta_02", pd.NA, "无分隔"])
    _assert_values(length.values, [5, 7, pd.NA, 3])
    assert not any(
        result.has_errors for result in (trimmed, upper, lower, length)
    )


def test_left_right_and_mid_use_fixed_excel_style_positions() -> None:
    engine = FormulaEngine()
    frame = _frame()

    left = engine.evaluate(frame, "LEFT(TRIM([text]), 2)")
    right = engine.evaluate(frame, "RIGHT(TRIM([text]), 2)")
    middle = engine.evaluate(frame, "MID(TRIM([text]), 2, 3)")

    _assert_values(left.values, ["Al", "βe", pd.NA, "无分"])
    _assert_values(right.values, ["ha", "02", pd.NA, "分隔"])
    _assert_values(middle.values, ["lph", "eta", pd.NA, "分隔"])
    assert not left.has_errors
    assert not right.has_errors
    assert not middle.has_errors


def test_find_textbefore_and_textafter_report_missing_delimiters_by_row() -> None:
    engine = FormulaEngine()
    frame = _frame()

    found = engine.evaluate(frame, 'FIND("_", [text])')
    before = engine.evaluate(frame, 'TEXTBEFORE([text], "_")')
    after = engine.evaluate(frame, 'TEXTAFTER([text], "_")')

    _assert_values(found.values, [pd.NA, 5, pd.NA, pd.NA])
    _assert_values(before.values, [pd.NA, "βeta", pd.NA, pd.NA])
    _assert_values(after.values, [pd.NA, "02", pd.NA, pd.NA])
    for result in (found, before, after):
        assert result.errors.notna().tolist() == [True, False, False, True]


def test_substitute_is_literal_not_regex_and_preserves_blank() -> None:
    result = FormulaEngine().evaluate(
        _frame(),
        'SUBSTITUTE(TRIM([text]), "_", ".")',
    )

    _assert_values(result.values, ["Alpha", "βeta.02", pd.NA, "无分隔"])
    assert not result.has_errors


def test_value_abs_and_round_keep_numeric_row_errors_explicit() -> None:
    engine = FormulaEngine()
    frame = _frame()

    converted = engine.evaluate(frame, "VALUE([raw number])")
    absolute = engine.evaluate(frame, "ABS([number])")
    rounded = engine.evaluate(frame, "ROUND([number], 1)")

    _assert_values(converted.values, [4.0, pd.NA, pd.NA, 2.5])
    assert converted.errors.notna().tolist() == [False, True, False, False]
    _assert_values(absolute.values, [1.24, 2.56, pd.NA, 4.04])
    _assert_values(rounded.values, [-1.2, 2.6, pd.NA, 4.0])
    assert not absolute.has_errors
    assert not rounded.has_errors


def test_path_functions_are_cross_platform_text_operations_only() -> None:
    engine = FormulaEngine()
    frame = _frame()

    name = engine.evaluate(frame, "PATHNAME([path])")
    parent = engine.evaluate(frame, "PARENTPATH([path])")
    extension = engine.evaluate(frame, "EXTENSION([path])")
    stem = engine.evaluate(frame, "STEM([path])")

    _assert_values(name.values, ["file.nii.gz", "scan.txt", "plain", pd.NA])
    _assert_values(parent.values, ["/data/A", "C:/data/B", "", pd.NA])
    _assert_values(extension.values, [".gz", ".txt", "", pd.NA])
    _assert_values(stem.values, ["file.nii", "scan", "plain", pd.NA])
    assert not any(
        result.has_errors for result in (name, parent, extension, stem)
    )


@pytest.mark.parametrize(
    ("formula", "message"),
    (
        ("LEFT([text], [count])", "固定整数"),
        ("LEFT([text], -1)", "不能小于 0"),
        ("MID([text], 0, 2)", "不能小于 1"),
        ('FIND("", [text])', "不能为空"),
        ("TEXTAFTER([text], [delimiter])", "固定文本"),
        ("ROUND([number], 1.5)", "固定整数"),
    ),
)
def test_scalar_parameters_fail_structurally(
    formula: str,
    message: str,
) -> None:
    with pytest.raises(FormulaValidationError, match=message):
        FormulaEngine().evaluate(_frame(), formula)


def test_catalog_functions_preserve_an_empty_non_default_index() -> None:
    frame = _frame().iloc[0:0]

    result = FormulaEngine().evaluate(
        frame,
        'PATHNAME(SUBSTITUTE(UPPER([text]), "_", "-"))',
    )

    assert result.values.empty
    assert result.errors.empty
    assert result.values.index.equals(frame.index)
    assert result.errors.index.equals(frame.index)
