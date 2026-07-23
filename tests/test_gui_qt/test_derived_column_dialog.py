from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from gui_qt.derived_column_dialog import DerivedColumnDialog


def _source() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003"],
            "age": [29, 31, 27],
            "score": [2, 4, 3],
        }
    )


def test_derived_column_dialog_inserts_column_and_previews_without_writing(qtbot):
    commits = []
    dialog = DerivedColumnDialog(
        _source(),
        lambda name, expression: commits.append((name, expression)),
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText("age_next")
    dialog.expression_edit.setPlainText("age")
    age_item = dialog.columns_list.findItems("age", Qt.MatchExactly)[0]

    dialog.columns_list.itemDoubleClicked.emit(age_item)
    dialog.expression_edit.setPlainText("age + 1")
    qtbot.mouseClick(dialog.preview_button, Qt.LeftButton)

    assert commits == []
    assert dialog.preview_table.rowCount() == 3
    assert dialog.preview_table.columnCount() == 2
    assert dialog.preview_table.horizontalHeaderItem(1).text() == "age_next"
    assert dialog.preview_table.item(0, 1).text() == "30"
    assert dialog.error_label.text() == ""


def test_derived_column_dialog_rejects_unsafe_preview_and_commits_once(qtbot):
    commits = []

    def persist(name, expression):
        commits.append((name, expression))
        return name

    dialog = DerivedColumnDialog(_source(), persist)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText("unsafe")
    dialog.expression_edit.setPlainText("__import__('os')")
    qtbot.mouseClick(dialog.preview_button, Qt.LeftButton)
    assert "白名单" in dialog.error_label.text()
    assert commits == []

    dialog.name_edit.setText("age_next")
    dialog.expression_edit.setPlainText("age + 1")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: dialog.result() == QDialog.Accepted, timeout=3000)

    assert commits == [("age_next", "age + 1")]
    assert dialog.committed_column == "age_next"


def test_derived_column_preview_renders_missing_values_as_blank(qtbot):
    source = _source()
    source.loc[1, "age"] = pd.NA
    dialog = DerivedColumnDialog(source, lambda name, _expression: name)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("age_copy")
    dialog.expression_edit.setPlainText("age")

    assert dialog.preview()
    assert dialog.preview_table.item(1, 1).text() == ""
