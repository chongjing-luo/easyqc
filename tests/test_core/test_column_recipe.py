from __future__ import annotations

import pandas as pd
import pytest

from core.table_transform import TableTransformEngine, TableTransformError
from models.column_recipe import ColumnRecipe, RecipeStep, RecipeValue


def _step(
    operation: str,
    *,
    on_error: str = "fail",
    **parameters: object,
) -> RecipeStep:
    return RecipeStep.create(
        operation,
        on_error=on_error,
        **parameters,
    )


def test_recipe_chains_path_text_and_another_column_without_code() -> None:
    source = pd.DataFrame(
        {
            "ezqcid": ["ROW1", "ROW2"],
            "image_path": [
                "/data/siteA/SUB001_T1.nii.gz",
                r"C:\data\siteB\SUB002_T2.nii.gz",
            ],
            "site": ["A", "B"],
        }
    )
    recipe = ColumnRecipe(
        name="scan_key",
        source_column="image_path",
        steps=(
            _step("path_name"),
            _step("remove_suffix", suffix=".nii.gz"),
            _step("split_take", delimiter="_", index=0),
            _step("prepend", value=RecipeValue.literal("scan-")),
            _step("append", value=RecipeValue.literal("_")),
            _step("append", value=RecipeValue.column("site")),
        ),
    )

    result = TableTransformEngine().derive_column_from_recipe(source, recipe)

    assert result["scan_key"].tolist() == ["scan-SUB001_A", "scan-SUB002_B"]
    assert "scan_key" not in source.columns


def test_recipe_supports_general_text_cleanup_and_boundary_extraction() -> None:
    source = pd.DataFrame(
        {
            "ezqcid": ["ROW1", "ROW2"],
            "label": ["  id[alpha]-visit  ", " id[beta]-visit "],
        }
    )
    recipe = ColumnRecipe(
        name="group_code",
        source_column="label",
        steps=(
            _step("trim"),
            _step("between", start="[", end="]"),
            _step("upper"),
        ),
    )

    result = TableTransformEngine().derive_column_from_recipe(source, recipe)

    assert result["group_code"].tolist() == ["ALPHA", "BETA"]


def test_recipe_supports_numeric_columns_and_structured_conditions() -> None:
    source = pd.DataFrame(
        {
            "ezqcid": ["ROW1", "ROW2", "ROW3"],
            "score_text": ["1.25", "2.75", "4"],
            "scale": [2, 2, 0.5],
            "motion": [0.1, 0.25, pd.NA],
        }
    )
    scaled_recipe = ColumnRecipe(
        name="scaled_score",
        source_column="score_text",
        steps=(
            _step("to_number"),
            _step("multiply", value=RecipeValue.column("scale")),
            _step("round", digits=1),
        ),
    )
    status_recipe = ColumnRecipe(
        name="qc_status",
        source_column="motion",
        steps=(
            _step(
                "conditional",
                operator="gt",
                compare_to=RecipeValue.literal(0.2),
                when_true=RecipeValue.literal("需要复核"),
                when_false=RecipeValue.literal("通过"),
            ),
        ),
    )

    engine = TableTransformEngine()
    scaled = engine.derive_column_from_recipe(source, scaled_recipe)
    status = engine.derive_column_from_recipe(source, status_recipe)

    assert scaled["scaled_score"].tolist() == [2.5, 5.5, 2.0]
    assert status["qc_status"].tolist() == ["通过", "需要复核", "通过"]


def test_recipe_error_policy_is_explicit_and_step_specific() -> None:
    source = pd.DataFrame(
        {
            "ezqcid": ["ROW1", "ROW2"],
            "label": ["site_SUB001", "SUB002"],
        }
    )
    failing = ColumnRecipe(
        name="identifier",
        source_column="label",
        steps=(_step("split_take", delimiter="_", index=1),),
    )

    with pytest.raises(TableTransformError, match=r"第 1 步.*1 行"):
        TableTransformEngine().derive_column_from_recipe(source, failing)

    blank = ColumnRecipe(
        name="identifier",
        source_column="label",
        steps=(
            _step(
                "split_take",
                delimiter="_",
                index=1,
                on_error="blank",
            ),
        ),
    )
    keep = ColumnRecipe(
        name="identifier",
        source_column="label",
        steps=(
            _step(
                "split_take",
                delimiter="_",
                index=1,
                on_error="keep",
            ),
        ),
    )

    blank_result = TableTransformEngine().derive_column_from_recipe(source, blank)
    keep_result = TableTransformEngine().derive_column_from_recipe(source, keep)

    assert blank_result["identifier"].iloc[0] == "SUB001"
    assert pd.isna(blank_result["identifier"].iloc[1])
    assert keep_result["identifier"].tolist() == ["SUB001", "SUB002"]


def test_conditional_error_policy_handles_only_incompatible_rows() -> None:
    source = pd.DataFrame(
        {
            "ezqcid": ["ROW1", "ROW2"],
            "mixed": pd.Series([3, "bad"], dtype=object),
        }
    )
    step_parameters = {
        "operator": "gt",
        "compare_to": RecipeValue.literal(2),
        "when_true": RecipeValue.literal("high"),
        "when_false": RecipeValue.literal("low"),
    }
    engine = TableTransformEngine()

    with pytest.raises(TableTransformError, match=r"第 1 步.*1 行"):
        engine.derive_column_from_recipe(
            source,
            ColumnRecipe(
                name="status",
                source_column="mixed",
                steps=(_step("conditional", **step_parameters),),
            ),
        )

    blanked = engine.derive_column_from_recipe(
        source,
        ColumnRecipe(
            name="status",
            source_column="mixed",
            steps=(
                _step(
                    "conditional",
                    on_error="blank",
                    **step_parameters,
                ),
            ),
        ),
    )
    kept = engine.derive_column_from_recipe(
        source,
        ColumnRecipe(
            name="status",
            source_column="mixed",
            steps=(
                _step(
                    "conditional",
                    on_error="keep",
                    **step_parameters,
                ),
            ),
        ),
    )

    assert blanked["status"].iloc[0] == "high"
    assert pd.isna(blanked["status"].iloc[1])
    assert kept["status"].tolist() == ["high", "bad"]


@pytest.mark.parametrize(
    ("recipe", "match"),
    [
        (
            ColumnRecipe(
                name="ezqcid",
                source_column="label",
                steps=(),
            ),
            "ezqcid",
        ),
        (
            ColumnRecipe(
                name="new_value",
                source_column="missing",
                steps=(),
            ),
            "未知来源列",
        ),
        (
            ColumnRecipe(
                name="new_value",
                source_column="label",
                steps=(_step("python", code="__import__('os')"),),
            ),
            "不支持的新增列操作",
        ),
    ],
)
def test_recipe_rejects_key_overwrite_unknown_columns_and_code_like_operations(
    recipe: ColumnRecipe,
    match: str,
) -> None:
    source = pd.DataFrame({"ezqcid": ["ROW1"], "label": ["alpha"]})

    with pytest.raises(TableTransformError, match=match):
        TableTransformEngine().derive_column_from_recipe(source, recipe)


def test_recipe_value_rejects_callable_literals() -> None:
    with pytest.raises(TypeError, match="标量"):
        RecipeValue.literal(lambda value: value)


def test_recipe_covers_text_slice_replace_and_length_operations() -> None:
    source = pd.DataFrame(
        {
            "ezqcid": ["ROW1", "ROW2"],
            "label": ["  Prefix-Alpha-END  ", "  Prefix-Beta-END  "],
        }
    )
    recipe = ColumnRecipe(
        name="label_length",
        source_column="label",
        steps=(
            _step("trim"),
            _step("remove_prefix", prefix="Prefix-"),
            _step("replace_literal", old="-END", new=""),
            _step("slice", start=0, end=5),
            _step("lower"),
            _step("length"),
        ),
    )

    result = TableTransformEngine().derive_column_from_recipe(source, recipe)

    assert result["label_length"].tolist() == [5, 4]


def test_recipe_covers_literal_boundaries_and_path_text_conveniences() -> None:
    source = pd.DataFrame(
        {
            "ezqcid": ["ROW1", "ROW2"],
            "path": ["/data/site/A/file.nii.gz", r"C:\data\site\B\scan.txt"],
            "token": ["site=A;visit=01", "site=B;visit=02"],
        }
    )
    engine = TableTransformEngine()

    parent = engine.derive_column_from_recipe(
        source,
        ColumnRecipe(
            name="parent",
            source_column="path",
            steps=(_step("path_parent"),),
        ),
    )
    suffix = engine.derive_column_from_recipe(
        source,
        ColumnRecipe(
            name="suffix",
            source_column="path",
            steps=(_step("path_suffix"),),
        ),
    )
    stem = engine.derive_column_from_recipe(
        source,
        ColumnRecipe(
            name="stem",
            source_column="path",
            steps=(_step("path_stem"),),
        ),
    )
    before = engine.derive_column_from_recipe(
        source,
        ColumnRecipe(
            name="site_pair",
            source_column="token",
            steps=(_step("before", delimiter=";"),),
        ),
    )
    after = engine.derive_column_from_recipe(
        source,
        ColumnRecipe(
            name="visit",
            source_column="token",
            steps=(_step("after", delimiter="visit="),),
        ),
    )

    assert parent["parent"].tolist() == ["/data/site/A", "C:/data/site/B"]
    assert suffix["suffix"].tolist() == [".gz", ".txt"]
    assert stem["stem"].tolist() == ["file.nii", "scan"]
    assert before["site_pair"].tolist() == ["site=A", "site=B"]
    assert after["visit"].tolist() == ["01", "02"]


def test_recipe_covers_arithmetic_missing_fill_and_text_conditions() -> None:
    source = pd.DataFrame(
        {
            "ezqcid": ["ROW1", "ROW2", "ROW3"],
            "value": [-2, 4, pd.NA],
            "fallback": [10, 20, 30],
            "label": ["QC-pass", "review", pd.NA],
        }
    )
    engine = TableTransformEngine()

    numeric = engine.derive_column_from_recipe(
        source,
        ColumnRecipe(
            name="adjusted",
            source_column="value",
            steps=(
                _step("fill_missing", value=RecipeValue.column("fallback")),
                _step("absolute"),
                _step("add", value=RecipeValue.literal(2)),
                _step("subtract", value=RecipeValue.literal(1)),
                _step("divide", value=RecipeValue.literal(2)),
            ),
        ),
    )
    status = engine.derive_column_from_recipe(
        source,
        ColumnRecipe(
            name="status",
            source_column="label",
            steps=(
                _step(
                    "conditional",
                    operator="starts_with",
                    compare_to=RecipeValue.literal("QC-"),
                    when_true=RecipeValue.literal("pass"),
                    when_false=RecipeValue.literal("check"),
                ),
            ),
        ),
    )

    assert numeric["adjusted"].tolist() == [1.5, 2.5, 15.5]
    assert status["status"].tolist() == ["pass", "check", "check"]


@pytest.mark.parametrize(
    "step",
    [
        RecipeStep.create("trim", unexpected="value"),
        RecipeStep.create("append"),
    ],
)
def test_recipe_rejects_unknown_and_missing_step_parameters(step: RecipeStep) -> None:
    source = pd.DataFrame({"ezqcid": ["ROW1"], "label": ["alpha"]})
    recipe = ColumnRecipe(
        name="new_value",
        source_column="label",
        steps=(step,),
    )

    with pytest.raises(TableTransformError, match=r"第 1 步"):
        TableTransformEngine().derive_column_from_recipe(source, recipe)


def test_recipe_rejects_nested_values_and_unknown_column_references() -> None:
    with pytest.raises(TypeError, match="标量"):
        RecipeStep.create("append", value={"column": "label"})

    source = pd.DataFrame({"ezqcid": ["ROW1"], "label": ["alpha"]})
    recipe = ColumnRecipe(
        name="new_value",
        source_column="label",
        steps=(_step("append", value=RecipeValue.column("unknown")),),
    )

    with pytest.raises(TableTransformError, match="未知引用列"):
        TableTransformEngine().derive_column_from_recipe(source, recipe)


def test_recipe_division_by_zero_obeys_explicit_error_policy() -> None:
    source = pd.DataFrame(
        {"ezqcid": ["ROW1", "ROW2"], "value": [4, 8], "divisor": [2, 0]}
    )
    failing = ColumnRecipe(
        name="quotient",
        source_column="value",
        steps=(_step("divide", value=RecipeValue.column("divisor")),),
    )
    blank = ColumnRecipe(
        name="quotient",
        source_column="value",
        steps=(
            _step(
                "divide",
                value=RecipeValue.column("divisor"),
                on_error="blank",
            ),
        ),
    )

    with pytest.raises(TableTransformError, match=r"第 1 步.*1 行"):
        TableTransformEngine().derive_column_from_recipe(source, failing)

    result = TableTransformEngine().derive_column_from_recipe(source, blank)
    assert result["quotient"].iloc[0] == 2
    assert pd.isna(result["quotient"].iloc[1])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("site-A", ["site-A", "site-A"]),
        (7, [7, 7]),
        (2.5, [2.5, 2.5]),
        (True, [True, True]),
    ],
)
def test_recipe_supports_typed_fixed_initial_values(value, expected) -> None:
    source = pd.DataFrame({"ezqcid": ["ROW1", "ROW2"]})
    recipe = ColumnRecipe(
        name="fixed_value",
        initial_value=RecipeValue.literal(value),
        steps=(),
    )

    result = TableTransformEngine().derive_column_from_recipe(source, recipe)

    assert result["fixed_value"].tolist() == expected
    assert recipe.source_column is None


def test_recipe_supports_blank_fixed_start_and_later_steps() -> None:
    source = pd.DataFrame({"ezqcid": ["ROW1", "ROW2"]})
    blank = ColumnRecipe(
        name="blank_value",
        initial_value=RecipeValue.literal(None),
        steps=(),
    )
    transformed = ColumnRecipe(
        name="constant_label",
        initial_value=RecipeValue.literal("  qc  "),
        steps=(_step("trim"), _step("upper")),
    )
    engine = TableTransformEngine()

    blank_result = engine.derive_column_from_recipe(source, blank)
    transformed_result = engine.derive_column_from_recipe(source, transformed)

    assert blank_result["blank_value"].isna().all()
    assert transformed_result["constant_label"].tolist() == ["QC", "QC"]


def test_recipe_initial_value_rejects_current_step_reference() -> None:
    with pytest.raises(ValueError, match="起始值"):
        ColumnRecipe(
            name="invalid",
            initial_value=RecipeValue.current(),
            steps=(),
        )


def test_recipe_can_create_missing_identity_but_never_replace_existing_identity() -> None:
    source_without_identity = pd.DataFrame(
        {"raw_id": ["SUB001", "SUB002"], "site": ["A", "B"]}
    )
    recipe = ColumnRecipe(
        name="ezqcid",
        source_column="raw_id",
        steps=(_step("trim"),),
    )
    engine = TableTransformEngine()

    created = engine.derive_column_from_recipe(source_without_identity, recipe)

    assert created["ezqcid"].tolist() == ["SUB001", "SUB002"]
    with pytest.raises(TableTransformError, match="已存在"):
        engine.derive_column_from_recipe(created, recipe)
