from __future__ import annotations

import pandas as pd
import pytest

from core.formula_parser import FormulaError, FormulaParser
from models.derived_formula import DerivedColumnFormula


def test_formula_request_is_trimmed_immutable_and_bounded() -> None:
    request = DerivedColumnFormula(
        name="scan_key",
        expression='  IF([site] = "A", "yes", "no")  ',
    )

    assert request.name == "scan_key"
    assert request.expression == 'IF([site] = "A", "yes", "no")'
    with pytest.raises(AttributeError):
        request.expression = '"changed"'  # type: ignore[misc]
    with pytest.raises(ValueError, match="4,096"):
        DerivedColumnFormula(name="too_long", expression="x" * 4097)


def test_parser_normalizes_first_throughput_formula_to_closed_ast() -> None:
    parsed = FormulaParser().parse(
        '=IF([site] = "A", UPPER(TEXTBEFORE([filename], "_")), '
        '[site] & "_" & [filename])'
    )

    assert parsed.root.kind == "call"
    assert parsed.root.value == "IF"
    assert parsed.referenced_columns == ("site", "filename")
    assert parsed.node_count <= 256
    assert parsed.depth <= 32


@pytest.mark.parametrize(
    "expression",
    (
        'Shell("rm -rf x")',
        "For i = 1 To 10",
        'CreateObject("WScript.Shell")',
        "[site].upper()",
        "x = 1",
    ),
)
def test_parser_rejects_programming_language_and_object_syntax(
    expression: str,
) -> None:
    with pytest.raises(FormulaError):
        FormulaParser().parse(expression)


def test_parser_preserves_unicode_space_and_bracket_column_names() -> None:
    parsed = FormulaParser().parse('[评分 列] & [a]]b]')

    assert parsed.referenced_columns == ("评分 列", "a]b")


def test_first_throughput_materializes_one_vectorized_column() -> None:
    from core.table_transform import TableTransformEngine

    source = pd.DataFrame(
        {
            "site": ["A", "B", "A", "B"],
            "filename": [
                "sub_001.nii.gz",
                "scan_002.nii.gz",
                "person_003.nii.gz",
                "image_004.nii.gz",
            ],
        },
        index=[11, 13, 17, 19],
    )
    request = DerivedColumnFormula(
        name="scan_key",
        expression=(
            'IF([site] = "A", UPPER(TEXTBEFORE([filename], "_")), '
            '[site] & "_" & [filename])'
        ),
    )

    result = TableTransformEngine().derive_column_from_formula(source, request)

    assert result is not source
    assert source.columns.tolist() == ["site", "filename"]
    assert result.index.tolist() == [11, 13, 17, 19]
    assert result["scan_key"].tolist() == [
        "SUB",
        "B_scan_002.nii.gz",
        "PERSON",
        "B_image_004.nii.gz",
    ]


def test_first_throughput_rejects_unknown_column_without_mutating_source() -> None:
    from core.table_transform import TableTransformEngine, TableTransformError

    source = pd.DataFrame({"site": ["A"]})
    request = DerivedColumnFormula(name="new", expression="[missing]")

    with pytest.raises(TableTransformError, match="未知列"):
        TableTransformEngine().derive_column_from_formula(source, request)
    assert source.columns.tolist() == ["site"]
