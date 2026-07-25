from __future__ import annotations

import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
)

from core.formula_engine import FormulaEngine
from core.formula_parser import FormulaParser
from gui_qt.formula_editor import FormulaEditorWidget
from gui_qt.formula_templates import (
    column_reference,
    formula_literal,
    render_cleanup_formula,
    render_concatenate_formula,
    render_conditional_formula,
    render_extract_formula,
    render_fixed_formula,
    render_numeric_columns_formula,
    render_numeric_fixed_formula,
)


def test_template_renderers_escape_user_text_and_share_the_formula_parser() -> None:
    formulas = (
        render_fixed_formula('A "quoted" value'),
        render_concatenate_formula("site", "_", "file]name"),
        render_extract_formula("filename", "_", position="after"),
        render_conditional_formula(
            "site",
            "=",
            "A",
            "pass",
            "review",
        ),
        render_cleanup_formula("label", operation="trim_upper"),
        render_numeric_columns_formula("age", "-", "baseline age"),
        render_numeric_fixed_formula("score", "/", 2),
    )

    assert formula_literal('A "quoted" value') == '"A ""quoted"" value"'
    assert column_reference("file]name") == "[file]]name]"
    assert formulas[1] == '[site] & "_" & [file]]name]'
    assert formulas[5] == "[age] - [baseline age]"
    for formula in formulas:
        assert FormulaParser().parse(formula).root


def test_two_column_templates_evaluate_without_a_second_executor() -> None:
    frame = pd.DataFrame(
        {
            "site": ["A", "B"],
            "filename": ["sub_01.nii.gz", "scan_02.nii.gz"],
            "age": [30, 42],
            "baseline age": [24, 36],
        },
        index=[5, 8],
    )
    engine = FormulaEngine()

    combined = engine.evaluate(
        frame,
        render_concatenate_formula("site", "_", "filename"),
    )
    numeric = engine.evaluate(
        frame,
        render_numeric_columns_formula("age", "-", "baseline age"),
    )

    assert combined.values.tolist() == [
        "A_sub_01.nii.gz",
        "B_scan_02.nii.gz",
    ]
    assert numeric.values.tolist() == [6.0, 6.0]
    assert not combined.has_errors
    assert not numeric.has_errors


@pytest.mark.parametrize(
    ("call", "message"),
    (
        (lambda: column_reference(""), "列名"),
        (
            lambda: render_extract_formula(
                "filename",
                "",
                position="before",
            ),
            "分隔符",
        ),
        (
            lambda: render_numeric_fixed_formula("score", "+", "bad"),
            "数值",
        ),
        (
            lambda: render_cleanup_formula("label", operation="unknown"),
            "清理",
        ),
    ),
)
def test_template_renderers_fail_loud_on_incomplete_values(call, message) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        call()


def test_editor_preserves_one_formula_across_modes_and_reports_status(qtbot) -> None:
    widget = FormulaEditorWidget(("site", "评分", "file]name"))
    qtbot.addWidget(widget)
    widget.show()

    widget.set_formula('IF([site] = "A", [评分], BLANK())')
    original = widget.formula()
    assert widget.status_label.property("state") == "valid"
    assert "有效" in widget.status_label.text()

    widget.tabs.setCurrentIndex(1)
    widget.tabs.setCurrentIndex(0)
    assert widget.formula() == original

    widget.set_formula("[missing]")
    assert widget.status_label.property("state") == "invalid"
    assert "未知列" in widget.status_label.text()

    widget.set_formula("IF(")
    assert widget.status_label.property("state") == "invalid"
    assert "语法" in widget.status_label.text()


def test_advanced_insertion_uses_exact_columns_and_shared_function_metadata(
    qtbot,
) -> None:
    widget = FormulaEditorWidget(("评分", "file]name"))
    qtbot.addWidget(widget)
    widget.show()
    widget.tabs.setCurrentIndex(1)
    widget.formula_edit.clear()

    function_index = widget.function_combo.findData("ROUND")
    widget.function_combo.setCurrentIndex(function_index)
    assert widget.function_signature.text() == "ROUND(number, digits)"
    assert widget.function_description.text()
    assert widget.function_example.text()
    assert widget.function_combo.count() == 23

    qtbot.mouseClick(widget.insert_function_button, Qt.LeftButton)
    assert widget.formula() == "ROUND()"

    column_index = widget.column_combo.findData("file]name")
    widget.column_combo.setCurrentIndex(column_index)
    qtbot.mouseClick(widget.insert_column_button, Qt.LeftButton)
    assert widget.formula() == "ROUND([file]]name])"


def test_numeric_quick_template_generates_visible_two_column_formula(qtbot) -> None:
    widget = FormulaEditorWidget(("age", "baseline age", "site"))
    qtbot.addWidget(widget)
    widget.show()
    panel = widget.quick_panel
    panel.set_template("numeric")
    panel.numeric_left_combo.setCurrentIndex(
        panel.numeric_left_combo.findData("age")
    )
    panel.numeric_operator_combo.setCurrentIndex(
        panel.numeric_operator_combo.findData("-")
    )
    panel.numeric_right_kind_combo.setCurrentIndex(
        panel.numeric_right_kind_combo.findData("column")
    )
    panel.numeric_right_column_combo.setCurrentIndex(
        panel.numeric_right_column_combo.findData("baseline age")
    )

    qtbot.mouseClick(panel.generate_button, Qt.LeftButton)

    assert widget.formula() == "[age] - [baseline age]"
    assert widget.status_label.property("state") == "valid"


def test_formula_editor_controls_have_visible_labels_and_accessible_names(
    qtbot,
) -> None:
    widget = FormulaEditorWidget(("site", "评分"))
    qtbot.addWidget(widget)
    widget.show()

    assert isinstance(widget.formula_edit, QPlainTextEdit)
    assert widget.formula_edit.accessibleName()
    assert widget.formula_edit.lineWrapMode() == QPlainTextEdit.WidgetWidth
    for control_type in (QAbstractButton, QComboBox, QLineEdit):
        for control in widget.findChildren(control_type):
            assert control.accessibleName(), (
                type(control).__name__,
                control.objectName(),
                control.text() if isinstance(control, QAbstractButton) else "",
            )
    assert widget.focusProxy() is widget.formula_edit
