from __future__ import annotations

from PySide6.QtCore import Qt

from core.table_transform import TableTransformEngine
from gui_qt.column_recipe_editor import ColumnRecipeEditor
from models.column_recipe import RecipeStep, RecipeValue


def test_recipe_editor_adds_reorders_and_removes_typed_steps(qtbot) -> None:
    editor = ColumnRecipeEditor(("ezqcid", "path", "site"))
    qtbot.addWidget(editor)
    editor.show()
    editor.set_source_column("path")
    editor.set_steps(
        (
            RecipeStep.create("path_name"),
            RecipeStep.create(
                "append",
                value=RecipeValue.column("site"),
            ),
        )
    )

    editor.step_list.setCurrentRow(1)
    qtbot.mouseClick(editor.move_up_button, Qt.LeftButton)
    recipe = editor.recipe("scan_key")

    assert [step.operation for step in recipe.steps] == ["append", "path_name"]
    assert recipe.steps[0].parameters["value"] == RecipeValue.column("site")

    qtbot.mouseClick(editor.remove_button, Qt.LeftButton)
    assert [step.operation for step in editor.recipe("scan_key").steps] == [
        "path_name"
    ]


def test_recipe_editor_keeps_column_names_as_data_and_builds_literal_values(
    qtbot,
) -> None:
    editor = ColumnRecipeEditor(("ezqcid", "image path", "评分"))
    qtbot.addWidget(editor)
    editor.set_source_column("image path")
    editor.add_step("prepend")
    editor.parameter_editor.set_value_parameter(
        "value",
        RecipeValue.literal("scan-"),
    )

    recipe = editor.recipe("scan_key")

    assert recipe.source_column == "image path"
    assert recipe.steps[0].parameters["value"] == RecipeValue.literal("scan-")


def test_recipe_editor_exposes_complete_core_catalog_and_omits_unused_compare(
    qtbot,
) -> None:
    editor = ColumnRecipeEditor(("ezqcid", "value"))
    qtbot.addWidget(editor)
    operation_ids = {
        editor.operation_combo.itemData(index)
        for index in range(editor.operation_combo.count())
    }

    assert operation_ids == set(TableTransformEngine.RECIPE_PARAMETER_SCHEMA)

    editor.add_step("conditional")
    operator_combo = editor.parameter_editor._field_widgets["operator"]
    operator_combo.setCurrentIndex(operator_combo.findData("is_missing"))
    recipe = editor.recipe("missing_status")

    assert "compare_to" not in recipe.steps[0].parameters


def test_recipe_editor_uses_typed_fixed_value_as_shared_start(qtbot) -> None:
    editor = ColumnRecipeEditor(("ezqcid", "site"))
    qtbot.addWidget(editor)

    editor.set_initial_value(RecipeValue.literal("same-site"))
    recipe = editor.recipe("site_copy")

    assert editor.initial_value_editor.kind_combo.currentData() == "literal"
    assert recipe.initial_value == RecipeValue.literal("same-site")
    assert recipe.source_column is None


def test_recipe_editor_fixed_start_can_feed_later_steps(qtbot) -> None:
    editor = ColumnRecipeEditor(("ezqcid", "site"))
    qtbot.addWidget(editor)
    editor.set_initial_value(RecipeValue.literal(2))
    editor.set_steps(
        (
            RecipeStep.create(
                "multiply",
                value=RecipeValue.literal(3),
            ),
        )
    )

    recipe = editor.recipe("six")

    assert recipe.initial_value == RecipeValue.literal(2)
    assert recipe.steps[0].parameters["value"] == RecipeValue.literal(3)
