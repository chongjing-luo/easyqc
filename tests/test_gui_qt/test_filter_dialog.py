"""Behavioral contract for the transactional grouped Qt Filter dialog."""

from __future__ import annotations

import pandas as pd
import pytest
from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QDoubleValidator, QValidator
from PySide6.QtWidgets import QDateTimeEdit, QDialogButtonBox, QPlainTextEdit

from core.table_view_service import TableViewService
import gui_qt.filter_panel as filter_panel_module
from gui_qt.filter_dialog import FilterDialog
from models.table_view_state import FilterCondition, FilterExpression, FilterGroup


def _profiles():
    source = pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003"],
            "site": ["North,East", "南区", "West"],
            "age": [29.5, 31.0, 42.0],
            "passed": [True, False, True],
            "visit": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
        }
    )
    return TableViewService(source).profiles


def _expression() -> FilterExpression:
    return FilterExpression(
        group_join="any",
        groups=(
            FilterGroup(
                group_id="demographic",
                join="all",
                conditions=(
                    FilterCondition("age", ">=", 30, "minimum-age"),
                    FilterCondition("passed", "==", True, "passed-only", enabled=False),
                ),
            ),
            FilterGroup(
                group_id="site-choice",
                join="any",
                conditions=(
                    FilterCondition("site", "in", ("North,East", "南区"), "sites"),
                ),
            ),
        ),
    )


def test_dialog_round_trips_grouped_ids_joins_enabled_state_and_typed_editors(qtbot):
    expression = _expression()
    dialog = FilterDialog(_profiles(), expression)
    qtbot.addWidget(dialog)

    assert dialog.editor.expression() == expression
    assert dialog.editor.top_join_combo.currentData() == "any"
    assert [group.group_id for group in dialog.editor.group_editors] == [
        "demographic",
        "site-choice",
    ]

    number_row = dialog.editor.group_editors[0].condition_rows[0]
    assert isinstance(number_row.literal_edit.validator(), QDoubleValidator)
    assert number_row.enabled_checkbox.isChecked()

    boolean_row = dialog.editor.group_editors[0].condition_rows[1]
    assert boolean_row.value_control_kind == "choice"
    assert not boolean_row.enabled_checkbox.isChecked()

    membership_row = dialog.editor.group_editors[1].condition_rows[0]
    assert membership_row.value_control_kind == "multi"
    assert isinstance(membership_row.membership_edit, QPlainTextEdit)
    membership_row.membership_edit.setPlainText("North,East\n南区")
    assert membership_row.condition().value == ("North,East", "南区")

    membership_row.set_column("visit")
    membership_row.set_operator(">")
    assert membership_row.value_control_kind == "datetime"
    membership_row.set_operator("between")
    assert membership_row.value_control_kind == "datetime_range"
    assert isinstance(membership_row.datetime_range_start, QDateTimeEdit)
    assert isinstance(membership_row.datetime_range_end, QDateTimeEdit)
    membership_row.set_operator("isna")
    assert membership_row.value_control_kind == "none"
    assert not hasattr(dialog, "json_editor")


def test_add_remove_reset_and_cancel_change_only_the_dialog_draft(qtbot):
    applied = _expression()
    dialog = FilterDialog(_profiles(), applied)
    qtbot.addWidget(dialog)
    requested: list[FilterExpression] = []
    dialog.applyRequested.connect(requested.append)
    dialog.show()

    added = dialog.editor.add_group()
    added.join_combo.setCurrentIndex(added.join_combo.findData("any"))
    added.add_condition(FilterCondition("ezqcid", "contains", "003", "subject"))
    assert len(dialog.editor.expression().groups) == 3
    dialog.editor.remove_group(added)
    assert dialog.editor.expression() == applied

    reset = dialog.button_box.button(QDialogButtonBox.StandardButton.Reset)
    qtbot.mouseClick(reset, Qt.MouseButton.LeftButton)
    assert dialog.editor.expression() == FilterExpression()
    assert applied == _expression()
    assert requested == []

    cancel = dialog.button_box.button(QDialogButtonBox.StandardButton.Cancel)
    qtbot.mouseClick(cancel, Qt.MouseButton.LeftButton)
    assert requested == []
    assert dialog.result() == dialog.DialogCode.Rejected


def test_add_buttons_do_not_treat_qt_clicked_state_as_draft_objects(qtbot):
    dialog = FilterDialog(_profiles(), _expression())
    qtbot.addWidget(dialog)
    first_group = dialog.editor.group_editors[0]

    qtbot.mouseClick(
        first_group.add_condition_button,
        Qt.MouseButton.LeftButton,
    )
    qtbot.mouseClick(
        dialog.editor.add_group_button,
        Qt.MouseButton.LeftButton,
    )

    assert len(first_group.condition_rows) == 3
    assert len(dialog.editor.group_editors) == 3

    qtbot.mouseClick(
        first_group.condition_rows[-1].remove_button,
        Qt.MouseButton.LeftButton,
    )
    qtbot.mouseClick(
        dialog.editor.group_editors[-1].remove_group_button,
        Qt.MouseButton.LeftButton,
    )

    assert len(first_group.condition_rows) == 2
    assert len(dialog.editor.group_editors) == 2


def test_unavailable_finite_choice_round_trips_without_silent_replacement(qtbot):
    applied = FilterExpression(
        "all",
        (
            FilterGroup(
                "retired-site",
                "all",
                (FilterCondition("site", "==", "Retired", "site-retired"),),
            ),
        ),
    )
    dialog = FilterDialog(_profiles(), applied)
    qtbot.addWidget(dialog)

    assert dialog.editor.expression() == applied


def test_apply_emits_one_complete_draft_and_waits_for_controller_completion(qtbot):
    dialog = FilterDialog(_profiles(), _expression())
    qtbot.addWidget(dialog)
    requested: list[FilterExpression] = []
    dialog.applyRequested.connect(requested.append)
    dialog.show()
    apply_button = dialog.button_box.button(QDialogButtonBox.StandardButton.Apply)

    qtbot.mouseClick(apply_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(apply_button, Qt.MouseButton.LeftButton)

    assert requested == [_expression()]
    assert not apply_button.isEnabled()
    assert dialog.isVisible()
    dialog.complete_apply()
    assert dialog.result() == dialog.DialogCode.Accepted


def test_error_is_visible_and_accessibly_bound_to_exact_group_and_condition(qtbot):
    dialog = FilterDialog(_profiles(), _expression())
    qtbot.addWidget(dialog)
    target_group = dialog.editor.group_editors[0]
    target_row = target_group.condition_rows[1]

    dialog.set_error(
        "passed-only is invalid",
        group_id="demographic",
        condition_id="passed-only",
    )

    assert target_row.error_text == "passed-only is invalid"
    assert target_row.error_label.isVisibleTo(dialog)
    assert "passed-only" in target_row.error_label.accessibleName()
    assert target_group.condition_rows[0].error_text == ""
    assert dialog.editor.error_text == ""

    dialog.set_error("group is invalid", group_id="site-choice")
    group = dialog.editor.group_editors[1]
    assert group.error_text == "group is invalid"
    assert "site-choice" in group.error_label.accessibleName()


def test_standard_controls_have_accessible_names_and_keyboard_focus(qtbot):
    dialog = FilterDialog(_profiles(), _expression())
    qtbot.addWidget(dialog)
    dialog.show()
    group = dialog.editor.group_editors[0]
    row = group.condition_rows[0]

    controls = (
        dialog.editor.top_join_combo,
        dialog.editor.add_group_button,
        group.join_combo,
        group.add_condition_button,
        row.enabled_checkbox,
        row.column_combo,
        row.operator_combo,
        row.remove_button,
    )
    assert all(control.accessibleName().strip() for control in controls)
    assert all(control.focusPolicy() & Qt.FocusPolicy.TabFocus for control in controls)
    assert isinstance(dialog.button_box, QDialogButtonBox)
    assert dialog.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)


def test_error_labels_force_plain_text_for_untrusted_values(qtbot):
    dialog = FilterDialog(_profiles(), _expression())
    qtbot.addWidget(dialog)
    group = dialog.editor.group_editors[0]
    row = group.condition_rows[0]

    assert row.error_label.textFormat() == Qt.TextFormat.PlainText
    assert group.error_label.textFormat() == Qt.TextFormat.PlainText
    assert dialog.editor.error_label.textFormat() == Qt.TextFormat.PlainText


def test_total_condition_bound_disables_every_add_condition_button(qtbot, monkeypatch):
    monkeypatch.setattr(filter_panel_module, "MAX_FILTER_CONDITIONS", 3)
    dialog = FilterDialog(_profiles(), _expression())
    qtbot.addWidget(dialog)

    assert sum(
        len(group.condition_rows) for group in dialog.editor.group_editors
    ) == 3
    assert all(
        not group.add_condition_button.isEnabled()
        for group in dialog.editor.group_editors
    )
    assert not dialog.editor.add_group_button.isEnabled()
    with pytest.raises(ValueError, match="at most 3 conditions"):
        dialog.editor.add_group()


def test_numeric_validators_use_the_decimal_format_expected_by_core(qtbot):
    previous_locale = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.German))
    try:
        dialog = FilterDialog(_profiles(), _expression())
    finally:
        QLocale.setDefault(previous_locale)
    qtbot.addWidget(dialog)
    number_row = dialog.editor.group_editors[0].condition_rows[0]

    validator = number_row.literal_edit.validator()
    assert validator.locale().language() == QLocale.Language.C
    assert validator.validate("1,000", 0)[0] == QValidator.State.Invalid
    number_row.set_operator("between")
    assert number_row.range_start.validator().locale().language() == QLocale.Language.C
    assert number_row.range_end.validator().locale().language() == QLocale.Language.C


def test_value_stack_uses_current_editor_size_instead_of_multiline_maximum(qtbot):
    dialog = FilterDialog(_profiles(), _expression())
    qtbot.addWidget(dialog)
    number_row = dialog.editor.group_editors[0].condition_rows[0]
    membership_row = dialog.editor.group_editors[1].condition_rows[0]

    assert number_row.value_control_kind == "literal"
    assert (
        number_row.value_stack.sizeHint().height()
        <= number_row.literal_edit.sizeHint().height() + 4
    )
    assert membership_row.value_control_kind == "multi"
    assert (
        membership_row.value_stack.sizeHint().height()
        >= membership_row.membership_edit.minimumSizeHint().height()
    )


def test_multiline_value_keeps_column_and_operator_controls_top_aligned(qtbot):
    dialog = FilterDialog(_profiles(), _expression())
    qtbot.addWidget(dialog)
    membership_row = dialog.editor.group_editors[1].condition_rows[0]
    layout = membership_row.layout()

    assert layout.itemAtPosition(1, 1).alignment() & Qt.AlignmentFlag.AlignTop
    assert layout.itemAtPosition(1, 2).alignment() & Qt.AlignmentFlag.AlignTop
