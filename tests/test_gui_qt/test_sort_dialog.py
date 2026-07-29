"""Transactional Sort dialog behavior tests."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QLabel

from gui_qt.sort_dialog import SortDialog
from models.table_view_state import SortRule


COLUMNS = ("easyqcid", "site", "age", "passed")


def test_sort_editor_real_buttons_preserve_ordered_priority(qtbot):
    dialog = SortDialog(
        COLUMNS,
        (SortRule("site", True), SortRule("age", False)),
    )
    qtbot.addWidget(dialog)
    dialog.show()

    second = dialog.editor.rule_rows[1]
    qtbot.mouseClick(second.move_up_button, Qt.MouseButton.LeftButton)
    assert dialog.editor.rules() == (
        SortRule("age", False),
        SortRule("site", True),
    )
    assert [row.priority_label.text() for row in dialog.editor.rule_rows] == [
        "1",
        "2",
    ]

    qtbot.mouseClick(
        dialog.editor.rule_rows[1].remove_button,
        Qt.MouseButton.LeftButton,
    )
    qtbot.mouseClick(dialog.editor.add_button, Qt.MouseButton.LeftButton)
    assert dialog.editor.rules() == (
        SortRule("age", False),
        SortRule("easyqcid", True),
    )


def test_duplicate_sort_apply_stays_open_and_emits_nothing(qtbot):
    dialog = SortDialog(COLUMNS, ())
    qtbot.addWidget(dialog)
    dialog.show()
    emitted: list[tuple[SortRule, ...]] = []
    dialog.applyRequested.connect(emitted.append)

    qtbot.mouseClick(dialog.editor.add_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(dialog.editor.add_button, Qt.MouseButton.LeftButton)
    for row in dialog.editor.rule_rows:
        row.column_combo.setCurrentIndex(row.column_combo.findData("site"))
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )

    assert dialog.isVisible()
    assert emitted == []
    assert "只能用于一条排序规则" in dialog.editor.error_text
    assert dialog.editor.error_label.textFormat() == Qt.TextFormat.PlainText


def test_sort_reset_cancel_close_and_apply_are_transactional(qtbot):
    applied = (SortRule("site", True),)
    dialog = SortDialog(COLUMNS, applied)
    qtbot.addWidget(dialog)
    dialog.show()
    emitted: list[tuple[SortRule, ...]] = []
    dialog.applyRequested.connect(emitted.append)

    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Reset),
        Qt.MouseButton.LeftButton,
    )
    assert dialog.editor.rules() == ()
    assert emitted == []
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Cancel),
        Qt.MouseButton.LeftButton,
    )
    assert emitted == []

    close_dialog = SortDialog(COLUMNS, applied)
    qtbot.addWidget(close_dialog)
    close_dialog.show()
    close_emitted: list[tuple[SortRule, ...]] = []
    close_dialog.applyRequested.connect(close_emitted.append)
    close_dialog.editor.set_rules((SortRule("age", False),))
    close_dialog.close()
    assert close_emitted == []

    apply_dialog = SortDialog(COLUMNS, applied)
    qtbot.addWidget(apply_dialog)
    apply_dialog.show()
    accepted: list[tuple[SortRule, ...]] = []
    apply_dialog.applyRequested.connect(accepted.append)
    apply_dialog.editor.set_rules((SortRule("age", False),))
    qtbot.mouseClick(
        apply_dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )
    assert accepted == [(SortRule("age", False),)]
    assert not apply_dialog.apply_button.isEnabled()
    assert apply_dialog.apply_button.accessibleName() == "应用排序草稿"
    assert apply_dialog.cancel_button.accessibleName() == "取消排序编辑"
    assert apply_dialog.reset_button.accessibleName() == "清空排序草稿"


def test_sort_fields_have_visible_labels_and_invalid_set_is_atomic(qtbot):
    applied = (SortRule("site", True),)
    dialog = SortDialog(COLUMNS, applied)
    qtbot.addWidget(dialog)
    dialog.show()

    labels = {label.text() for label in dialog.editor.findChildren(QLabel)}
    assert {"优先级", "列", "方向"} <= labels
    with pytest.raises(ValueError, match="Unknown sort column"):
        dialog.editor.set_rules((SortRule("missing", False),))
    assert dialog.editor.rules() == applied
