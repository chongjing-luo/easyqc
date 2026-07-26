from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt

from core.table_view_service import TableViewService
from gui_qt.delete_columns_dialog import DeleteColumnsDialog
from gui_qt.delete_rows_dialog import DeleteRowsDialog
from models.table_view_state import (
    FilterCondition,
    FilterExpression,
    FilterGroup,
)


def _expression() -> FilterExpression:
    return FilterExpression(
        groups=(
            FilterGroup(
                group_id="site-a",
                join="all",
                conditions=(
                    FilterCondition(
                        "site",
                        "==",
                        "A",
                        "site-a-condition",
                    ),
                ),
            ),
        )
    )


def test_delete_rows_dialog_rejects_empty_filter_and_emits_complete_draft(
    qtbot,
) -> None:
    profiles = TableViewService(
        pd.DataFrame(
            {"ezqcid": ["SUB001", "SUB002"], "site": ["A", "B"]}
        )
    ).profiles
    dialog = DeleteRowsDialog(profiles, FilterExpression())
    qtbot.addWidget(dialog)
    emitted = []
    dialog.deleteRequested.connect(emitted.append)
    dialog.show()

    qtbot.mouseClick(dialog.delete_button, Qt.LeftButton)

    assert emitted == []
    assert "条件" in dialog.error_text
    assert dialog.isVisible()

    dialog.editor.set_expression(_expression())
    qtbot.mouseClick(dialog.delete_button, Qt.LeftButton)

    assert emitted == [_expression()]
    assert not dialog.delete_button.isEnabled()
    dialog.set_error("没有匹配行")
    assert dialog.delete_button.isEnabled()
    assert "没有匹配行" in dialog.error_text

    qtbot.mouseClick(dialog.delete_button, Qt.LeftButton)
    dialog.complete_delete()
    assert not dialog.isVisible()


def test_delete_columns_dialog_search_preserves_checks_and_protects_identity(
    qtbot,
) -> None:
    dialog = DeleteColumnsDialog(
        ("ezqcid", "site", "age", "image_path"),
        protected_columns=("ezqcid",),
    )
    qtbot.addWidget(dialog)
    emitted = []
    dialog.deleteRequested.connect(emitted.append)
    dialog.show()

    identity = dialog.item_for_column("ezqcid")
    assert not bool(identity.flags() & Qt.ItemIsEnabled)
    assert "受保护" in identity.text()

    qtbot.mouseClick(dialog.delete_button, Qt.LeftButton)
    assert emitted == []
    assert "至少" in dialog.error_text

    site = dialog.item_for_column("site")
    age = dialog.item_for_column("age")
    site.setCheckState(Qt.Checked)
    age.setCheckState(Qt.Checked)
    dialog.search_edit.setText("age")
    assert dialog.item_for_column("site").isHidden()
    assert dialog.selected_columns() == ("site", "age")

    qtbot.mouseClick(dialog.delete_button, Qt.LeftButton)

    assert emitted == [("site", "age")]
    assert not dialog.delete_button.isEnabled()
    dialog.complete_delete()
    assert not dialog.isVisible()


def test_delete_columns_dialog_reset_and_cancel_are_transactional(qtbot) -> None:
    dialog = DeleteColumnsDialog(("ezqcid", "site", "age"))
    qtbot.addWidget(dialog)
    emitted = []
    dialog.deleteRequested.connect(emitted.append)
    dialog.show()
    dialog.item_for_column("ezqcid").setCheckState(Qt.Checked)
    dialog.item_for_column("age").setCheckState(Qt.Checked)

    qtbot.mouseClick(dialog.reset_button, Qt.LeftButton)
    assert dialog.selected_columns() == ()

    dialog.item_for_column("site").setCheckState(Qt.Checked)
    qtbot.mouseClick(dialog.cancel_button, Qt.LeftButton)

    assert emitted == []
    assert not dialog.isVisible()
