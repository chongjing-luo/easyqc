"""Transactional Columns dialog behavior tests."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox

from gui_qt.columns_dialog import ColumnsDialog
from models.table_view_state import ColumnViewState


DEFAULT = ColumnViewState(
    order=("ezqcid", "site", "age", "passed"),
    pinned=("ezqcid",),
)


def _item(editor, column: str):
    for row in range(editor.list_widget.count()):
        item = editor.list_widget.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == column:
            return item
    raise AssertionError(f"missing column item: {column}")


def test_column_search_filters_without_mutating_the_draft(qtbot):
    dialog = ColumnsDialog(DEFAULT, DEFAULT)
    qtbot.addWidget(dialog)
    dialog.show()
    before = dialog.editor.state()

    dialog.editor.search_edit.setText("AGE")
    visible = [
        str(dialog.editor.list_widget.item(row).data(Qt.ItemDataRole.UserRole))
        for row in range(dialog.editor.list_widget.count())
        if not dialog.editor.list_widget.item(row).isHidden()
    ]

    assert visible == ["age"]
    assert dialog.editor.state() == before
    assert dialog.editor.search_edit.accessibleName() == "Search table columns"


def test_column_visibility_pin_unpin_and_move_keep_one_leading_block(qtbot):
    dialog = ColumnsDialog(DEFAULT, DEFAULT)
    qtbot.addWidget(dialog)
    dialog.show()
    editor = dialog.editor

    site = _item(editor, "site")
    site.setCheckState(Qt.CheckState.Unchecked)
    editor.list_widget.setCurrentItem(site)
    qtbot.mouseClick(editor.pin_button, Qt.MouseButton.LeftButton)
    assert editor.state().pinned == ("ezqcid", "site")
    assert "site" not in editor.state().hidden

    age = _item(editor, "age")
    editor.list_widget.setCurrentItem(age)
    qtbot.mouseClick(editor.pin_button, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(editor.move_up_button, Qt.MouseButton.LeftButton)
    assert editor.state().order == ("ezqcid", "age", "site", "passed")
    assert editor.state().pinned == ("ezqcid", "age", "site")

    age = _item(editor, "age")
    editor.list_widget.setCurrentItem(age)
    qtbot.mouseClick(editor.unpin_button, Qt.MouseButton.LeftButton)
    assert editor.state().order == ("ezqcid", "site", "age", "passed")
    assert editor.state().pinned == ("ezqcid", "site")
    pinned_count = len(editor.state().pinned)
    assert editor.state().order[:pinned_count] == editor.state().pinned

    passed = _item(editor, "passed")
    passed.setCheckState(Qt.CheckState.Unchecked)
    assert editor.state().hidden == ("passed",)


def test_ezqcid_controls_cannot_hide_unpin_or_move_identity(qtbot):
    dialog = ColumnsDialog(DEFAULT, DEFAULT)
    qtbot.addWidget(dialog)
    dialog.show()
    editor = dialog.editor
    identity = _item(editor, "ezqcid")
    editor.list_widget.setCurrentItem(identity)

    assert not bool(identity.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    assert not editor.move_up_button.isEnabled()
    assert not editor.move_down_button.isEnabled()
    assert not editor.unpin_button.isEnabled()
    assert not editor.move_selected(1)
    assert not editor.unpin_selected()
    assert editor.state().order[0] == "ezqcid"
    assert "ezqcid" not in editor.state().hidden
    assert editor.state().pinned[0] == "ezqcid"


def test_columns_reset_cancel_close_and_apply_are_transactional(qtbot):
    applied = ColumnViewState(
        order=("ezqcid", "age", "site", "passed"),
        hidden=("passed",),
        pinned=("ezqcid", "age"),
    )
    dialog = ColumnsDialog(applied, DEFAULT)
    qtbot.addWidget(dialog)
    dialog.show()
    emitted: list[ColumnViewState] = []
    dialog.applyRequested.connect(emitted.append)

    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Reset),
        Qt.MouseButton.LeftButton,
    )
    assert dialog.editor.state() == DEFAULT
    assert emitted == []
    qtbot.mouseClick(
        dialog.button_box.button(QDialogButtonBox.StandardButton.Cancel),
        Qt.MouseButton.LeftButton,
    )
    assert emitted == []

    close_dialog = ColumnsDialog(applied, DEFAULT)
    qtbot.addWidget(close_dialog)
    close_dialog.show()
    close_emitted: list[ColumnViewState] = []
    close_dialog.applyRequested.connect(close_emitted.append)
    close_dialog.editor.set_state(DEFAULT)
    close_dialog.close()
    assert close_emitted == []

    apply_dialog = ColumnsDialog(applied, DEFAULT)
    qtbot.addWidget(apply_dialog)
    apply_dialog.show()
    accepted: list[ColumnViewState] = []
    apply_dialog.applyRequested.connect(accepted.append)
    qtbot.mouseClick(
        apply_dialog.button_box.button(QDialogButtonBox.StandardButton.Apply),
        Qt.MouseButton.LeftButton,
    )
    assert accepted == [applied]
    assert not apply_dialog.apply_button.isEnabled()
    assert apply_dialog.apply_button.accessibleName() == "Apply column draft"
    assert apply_dialog.cancel_button.accessibleName() == "Cancel column editing"
    assert apply_dialog.reset_button.accessibleName() == "Restore default columns"
    apply_dialog.set_error("<b>unsafe</b>")
    assert apply_dialog.editor.error_label.textFormat() == Qt.TextFormat.PlainText
    assert apply_dialog.editor.error_text == "<b>unsafe</b>"


def test_search_has_a_visible_label_and_invalid_state_is_atomic(qtbot):
    dialog = ColumnsDialog(DEFAULT, DEFAULT)
    qtbot.addWidget(dialog)
    dialog.show()
    before = dialog.editor.state()

    assert dialog.editor.search_label.text() == "Search columns"
    assert dialog.editor.search_label.buddy() is dialog.editor.search_edit
    with pytest.raises(ValueError, match="hidden and pinned"):
        dialog.editor.set_state(
            ColumnViewState(
                order=DEFAULT.order,
                hidden=("site",),
                pinned=("ezqcid", "site"),
            )
        )
    assert dialog.editor.state() == before
    with pytest.raises(ValueError, match="contiguous leading block"):
        dialog.editor.set_state(
            ColumnViewState(
                order=DEFAULT.order,
                pinned=("ezqcid", "age"),
            )
        )
    assert dialog.editor.state() == before
