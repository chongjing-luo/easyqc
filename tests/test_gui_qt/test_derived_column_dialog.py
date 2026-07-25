from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPlainTextEdit

from gui_qt.derived_column_dialog import DerivedColumnDialog
from models.column_recipe import ColumnRecipe, RecipeStep, RecipeValue


def _source() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003"],
            "age": [29, 31, 27],
            "score": [2, 4, 3],
        }
    )


def test_derived_column_dialog_previews_ordered_recipe_without_writing(qtbot):
    commits = []
    dialog = DerivedColumnDialog(
        _source(),
        commits.append,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText("age_next")
    dialog.editor.set_source_column("age")
    dialog.editor.set_steps(
        (
            RecipeStep.create(
                "add",
                value=RecipeValue.literal(1),
            ),
        )
    )
    qtbot.mouseClick(dialog.preview_button, Qt.LeftButton)

    assert commits == []
    assert dialog.preview_table.rowCount() == 3
    assert dialog.preview_table.columnCount() == 3
    assert dialog.preview_table.horizontalHeaderItem(1).text() == "起始值"
    assert dialog.preview_table.horizontalHeaderItem(2).text() == "age_next"
    assert dialog.preview_table.item(0, 2).text() == "30"
    assert dialog.error_label.text() == ""


def test_derived_column_dialog_has_no_code_editor_and_commits_recipe_once(qtbot):
    commits = []

    def persist(recipe):
        commits.append(recipe)
        return recipe.name

    dialog = DerivedColumnDialog(_source(), persist)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText("age_next")
    dialog.editor.set_source_column("age")
    dialog.editor.set_steps(
        (
            RecipeStep.create(
                "add",
                value=RecipeValue.literal(1),
            ),
        )
    )

    assert dialog.findChildren(QPlainTextEdit) == []
    assert "__import__" not in " ".join(
        dialog.editor.operation_combo.itemText(index)
        for index in range(dialog.editor.operation_combo.count())
    )
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: dialog.result() == QDialog.Accepted, timeout=3000)

    assert len(commits) == 1
    assert isinstance(commits[0], ColumnRecipe)
    assert commits[0].name == "age_next"
    assert commits[0].source_column == "age"
    assert dialog.committed_column == "age_next"


def test_derived_column_preview_renders_missing_values_as_blank(qtbot):
    source = _source()
    source.loc[1, "age"] = pd.NA
    dialog = DerivedColumnDialog(source, lambda recipe: recipe.name)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("age_copy")
    dialog.editor.set_source_column("age")

    assert dialog.preview()
    assert dialog.preview_table.item(1, 2).text() == ""


def test_derived_column_dialog_shows_step_specific_validation_error(qtbot):
    dialog = DerivedColumnDialog(_source(), lambda recipe: recipe.name)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("bad_split")
    dialog.editor.set_source_column("age")
    dialog.editor.set_steps(
        (RecipeStep.create("split_take", delimiter="", index=0),)
    )

    assert not dialog.preview()
    assert "delimiter" in dialog.error_label.text()


def test_derived_column_dialog_previews_and_commits_fixed_start(qtbot):
    commits = []

    def persist(recipe):
        commits.append(recipe)
        return recipe.name

    dialog = DerivedColumnDialog(_source(), persist)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("batch")
    dialog.editor.set_initial_value(RecipeValue.literal("A"))

    assert dialog.preview()
    assert dialog.preview_table.item(0, 1).text() == "A"
    assert dialog.preview_table.item(0, 2).text() == "A"
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: dialog.result() == QDialog.Accepted, timeout=3000)
    assert commits[0].initial_value == RecipeValue.literal("A")


def test_derived_column_dialog_can_preview_missing_ezqcid_target(qtbot):
    source = pd.DataFrame({"raw_id": ["SUB001", "SUB002"]})
    dialog = DerivedColumnDialog(source, lambda recipe: recipe.name)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("ezqcid")
    dialog.editor.set_source_column("raw_id")

    assert dialog.preview()
    assert dialog.preview_table.horizontalHeaderItem(0).text() == "行"
    assert dialog.preview_table.horizontalHeaderItem(2).text() == "ezqcid"
    assert dialog.preview_table.item(0, 2).text() == "SUB001"
