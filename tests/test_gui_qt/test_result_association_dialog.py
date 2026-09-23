"""RI-13c: transactional, graphical association editing using synthetic data."""

from dataclasses import replace

import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPlainTextEdit, QScrollArea

from gui_qt.filter_dialog import FilterDialog
from gui_qt.i18n import LanguageController
from gui_qt.result_association_dialog import ResultAssociationDialog
from models.qcmodule import QCModule, Score, Tag
from models.rating import Rating
from models.result_association import ResultAssociationRule
from models.table_view_state import (
    FilterCondition, FilterExpression, FilterGroup,
    filter_expression_to_json_object,
)


def _subjects():
    return pd.DataFrame({
        "easyqcid": ["anat01", "func01"], "participant": ["01", "01"],
        "kind": ["anat", "func"],
    })


def _modules():
    return (
        QCModule("AnatQC", "Anatomical", scores={"1": Score("1", "Quality", "3", "1,2,3")},
                 tags={"2": Tag("2", "Motion")}),
        QCModule("FuncQC", "Functional", scores={"2": Score("2", "Quality", "3", "1,2,3")}),
    )


def _ratings():
    return (
        Rating("AnatQC", "alice", "anat01", {"1": "3", "7": "2"}, {"2": True}),
        Rating("FuncQC", "bob", "func01", {"2": "1"}, {}),
    )


def _rule(**changes):
    return replace(ResultAssociationRule(
        "anat", "Anatomical QC", "AnatQC", "alice", "participant", "participant",
        (("score1", "anat_quality"), ("tag2", "anat_motion")),
    ), **changes)


def _dialog(qtbot, rules=None, **kwargs):
    dialog = ResultAssociationDialog(
        _subjects(), _modules(), _ratings(), (_rule(),) if rules is None else rules,
        **kwargs,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def _field_row(dialog, field):
    return next(row for row in range(dialog.fields_table.rowCount())
                if dialog.fields_table.item(row, 0).data(Qt.UserRole) == field)


def test_existing_rule_graphical_round_trip_has_no_json_editor(qtbot):
    rule = _rule()
    dialog = _dialog(qtbot, (rule,))
    requested = []
    dialog.applyRequested.connect(requested.append)

    assert dialog.rule_list.count() == 1
    assert dialog.name_edit.text() == rule.name
    assert dialog.source_module_combo.currentData() == "AnatQC"
    assert dialog.source_rater_combo.currentText() == "alice"
    assert dialog.source_key_combo.currentData() == "participant"
    assert dialog.target_key_combo.currentData() == "participant"
    assert not dialog.findChildren(QPlainTextEdit)
    assert {dialog.fields_table.item(row, 0).data(Qt.UserRole)
            for row in range(dialog.fields_table.rowCount())} == {"score1", "score7", "tag2", "notes"}

    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert requested == [(rule,)]
    assert dialog.isVisible()
    assert not dialog.apply_button.isEnabled()
    dialog.complete_apply()
    assert dialog.result() == QDialog.Accepted


def test_add_delete_and_cancel_do_not_mutate_inputs(qtbot):
    rule = _rule()
    dialog = _dialog(qtbot, (rule,))
    requested = []
    dialog.applyRequested.connect(requested.append)
    qtbot.mouseClick(dialog.add_button, Qt.LeftButton)
    assert dialog.rule_list.count() == 2
    dialog.name_edit.setText("New temporary rule")
    qtbot.mouseClick(dialog.delete_button, Qt.LeftButton)
    assert dialog.rule_list.count() == 1
    assert dialog.name_edit.text() == rule.name
    dialog.reject()
    assert not requested
    assert rule == _rule()


def test_switching_rules_retains_even_incomplete_drafts(qtbot):
    dialog = _dialog(qtbot, (_rule(), _rule(rule_id="other", name="Other", fields=(("notes", "other_notes"),))))
    dialog.name_edit.clear()
    row = _field_row(dialog, "score1")
    dialog.fields_table.item(row, 1).setText("changed_output")
    dialog.rule_list.setCurrentRow(1)
    dialog.name_edit.setText("Second edited")
    dialog.rule_list.setCurrentRow(0)
    assert dialog.name_edit.text() == ""
    assert dialog.fields_table.item(_field_row(dialog, "score1"), 1).text() == "changed_output"
    dialog.rule_list.setCurrentRow(1)
    assert dialog.name_edit.text() == "Second edited"


def test_apply_failure_unfreezes_and_preserves_full_draft(qtbot):
    dialog = _dialog(qtbot)
    requested = []
    dialog.applyRequested.connect(requested.append)
    dialog.name_edit.setText("Updated rule")
    dialog.source_rater_combo.setEditText("new-rater")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert len(requested) == 1
    assert not dialog.name_edit.isEnabled()
    dialog.reject()
    assert dialog.isVisible()
    dialog.set_error("synthetic atomic save failure")
    assert dialog.error_text == "synthetic atomic save failure"
    assert dialog.name_edit.isEnabled() and dialog.apply_button.isEnabled()
    assert dialog.name_edit.text() == "Updated rule"
    assert dialog.source_rater_combo.currentText() == "new-rater"
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert requested[0] == requested[1]
    dialog.set_error("retry later")


@pytest.mark.parametrize("side", ("source", "target"))
def test_filter_dialog_applies_to_draft_and_round_trips_without_json(qtbot, side):
    dialog = _dialog(qtbot)
    button = getattr(dialog, f"{side}_filter_button")
    qtbot.mouseClick(button, Qt.LeftButton)
    editor = dialog.findChild(FilterDialog)
    assert editor is not None and editor.isVisible()
    expression = FilterExpression(groups=(FilterGroup("kinds", "all", (
        FilterCondition("kind", "==", "anat" if side == "source" else "func", "kind"),
    )),))
    editor.editor.set_expression(expression)
    qtbot.mouseClick(editor.apply_button, Qt.LeftButton)
    assert editor.result() == QDialog.Accepted
    requested = []
    dialog.applyRequested.connect(requested.append)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert getattr(requested[0][0], f"{side}_filter") == filter_expression_to_json_object(expression)
    dialog.set_error("keep open")


def test_filter_cancel_does_not_change_rule(qtbot):
    dialog = _dialog(qtbot)
    qtbot.mouseClick(dialog.source_filter_button, Qt.LeftButton)
    editor = dialog.findChild(FilterDialog)
    editor.editor.add_group()
    editor.reject()
    requested = []
    dialog.applyRequested.connect(requested.append)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert requested == [(_rule(),)]
    dialog.set_error("keep open")


def test_switch_source_module_refreshes_fields_and_allows_typed_rater(qtbot):
    dialog = _dialog(qtbot, ())
    qtbot.mouseClick(dialog.add_button, Qt.LeftButton)
    dialog.source_module_combo.setCurrentIndex(dialog.source_module_combo.findData("FuncQC"))
    assert dialog.source_rater_combo.findText("bob") >= 0
    assert _field_row(dialog, "score2") >= 0
    dialog.source_rater_combo.setEditText("future_rater")
    row = _field_row(dialog, "score2")
    dialog.fields_table.item(row, 0).setCheckState(Qt.Checked)
    dialog.fields_table.item(row, 1).setText("functional_quality")
    requested = []
    dialog.applyRequested.connect(requested.append)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert requested[0][0].source_module == "FuncQC"
    assert requested[0][0].source_rater == "future_rater"
    assert ("score2", "functional_quality") in requested[0][0].fields
    dialog.set_error("keep open")


@pytest.mark.parametrize("invalid", ("name", "field", "conflict"))
def test_invalid_draft_has_visible_error_and_does_not_emit(qtbot, invalid):
    dialog = _dialog(qtbot)
    requested = []
    dialog.applyRequested.connect(requested.append)
    if invalid == "name":
        dialog.name_edit.clear()
    else:
        row = _field_row(dialog, "score1")
        dialog.fields_table.item(row, 1).setText("" if invalid == "field" else "easyqcid")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert dialog.error_text and dialog.error_label.isVisible()
    assert not requested
    assert dialog.apply_button.isEnabled()


def test_deleting_all_rules_emits_empty_tuple(qtbot):
    dialog = _dialog(qtbot)
    requested = []
    dialog.applyRequested.connect(requested.append)
    qtbot.mouseClick(dialog.delete_button, Qt.LeftButton)
    assert not dialog.name_edit.isEnabled()
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert requested == [()]
    dialog.set_error("keep open")


def test_missing_saved_source_choices_are_not_silently_replaced(qtbot):
    saved = _rule(source_module="Removed", source_rater="former", source_key="old_key", fields=(("score9", "old_score"),))
    dialog = _dialog(qtbot, (saved,))
    assert dialog.source_module_combo.currentData() == "Removed"
    assert dialog.source_key_combo.currentData() == "old_key"
    requested = []
    dialog.applyRequested.connect(requested.append)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert requested == [(saved,)]
    dialog.set_error("Unknown source column: old_key")


def test_bilingual_layout_fits_desktop_and_protects_user_text(qtbot):
    language = LanguageController(language="en")
    dialog = _dialog(qtbot, (_rule(name="取消"),), language=language)
    dialog.resize(800, 650)
    assert dialog.windowTitle() == "Result associations"
    assert dialog.name_edit.text() == "取消"
    assert dialog.findChild(QScrollArea) is not None
    assert dialog.minimumSizeHint().width() <= 800
    for control in (dialog.rule_list, dialog.name_edit, dialog.source_module_combo,
                    dialog.source_rater_combo, dialog.source_key_combo, dialog.target_key_combo,
                    dialog.fields_table, dialog.source_filter_button, dialog.apply_button):
        assert control.accessibleName()
        assert control.focusPolicy() != Qt.NoFocus
    language.set_language("zh_CN")
    assert dialog.windowTitle() == "结果关联"
    assert dialog.name_edit.text() == "取消"
    assert dialog.rule_list.item(0).text() == "取消"


def test_duplicate_source_fields_in_saved_rule_round_trip_without_loss(qtbot):
    rule = _rule(fields=(("score1", "first_output"), ("score1", "second_output")))
    dialog = _dialog(qtbot, (rule,))
    requested = []
    dialog.applyRequested.connect(requested.append)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert requested == [(rule,)]
    dialog.set_error("keep open")


def test_output_names_must_be_unique_across_all_rules(qtbot):
    dialog = _dialog(qtbot, (_rule(), _rule(rule_id="second")))
    requested = []
    dialog.applyRequested.connect(requested.append)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert dialog.error_text
    assert not requested


def test_invalid_filter_remains_open_without_replacing_saved_filter(qtbot):
    dialog = ResultAssociationDialog(_subjects().assign(age=[10, 20]), _modules(), _ratings(), (_rule(),))
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mouseClick(dialog.source_filter_button, Qt.LeftButton)
    editor = dialog.findChild(FilterDialog)
    editor.editor.set_expression(FilterExpression(groups=(FilterGroup("g", "all", (
        FilterCondition("age", ">", "-", "partial-number"),
    )),)))
    qtbot.mouseClick(editor.apply_button, Qt.LeftButton)
    assert editor.isVisible()
    assert editor.editor.error_text
    assert editor.apply_button.isEnabled()
    editor.reject()
    requested = []
    dialog.applyRequested.connect(requested.append)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert requested == [(_rule(),)]
    dialog.set_error("keep open")


def test_historical_module_fields_and_raters_remain_available(qtbot):
    ratings = (*_ratings(), Rating("Historical", "retired", "anat01", {"9": "1"}, {"8": False}))
    dialog = ResultAssociationDialog(_subjects(), _modules(), ratings, ())
    qtbot.addWidget(dialog)
    dialog.add_button.click()
    dialog.source_module_combo.setCurrentIndex(dialog.source_module_combo.findData("Historical"))
    assert dialog.source_rater_combo.findText("retired") >= 0
    assert _field_row(dialog, "score9") >= 0
    assert _field_row(dialog, "tag8") >= 0


def test_stale_saved_filter_shows_error_without_discarding_rule(qtbot):
    expression = FilterExpression(groups=(FilterGroup("g", "all", (
        FilterCondition("removed_column", "==", "x", "removed"),
    )),))
    rule = _rule(source_filter=filter_expression_to_json_object(expression))
    dialog = _dialog(qtbot, (rule,))
    qtbot.mouseClick(dialog.source_filter_button, Qt.LeftButton)
    assert dialog.error_text
    requested = []
    dialog.applyRequested.connect(requested.append)
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert requested == [(rule,)]
    dialog.set_error("Unknown source filter column")
