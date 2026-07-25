from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
)

from gui_qt.derived_column_dialog import DerivedColumnDialog
from gui_qt.formula_editor import FormulaEditorWidget
from models.derived_formula import DerivedColumnFormula


def _source() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003"],
            "age": [29, 31, 27],
            "score": [2, 4, 3],
        },
        index=[7, 9, 12],
    )


def test_dialog_previews_only_referenced_inputs_result_and_error(qtbot) -> None:
    commits = []
    dialog = DerivedColumnDialog(_source(), commits.append)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText("age next")
    dialog.editor.set_formula("[age] + 1")

    qtbot.mouseClick(dialog.preview_button, Qt.LeftButton)

    assert commits == []
    assert dialog.preview_table.rowCount() == 3
    assert [
        dialog.preview_table.horizontalHeaderItem(index).text()
        for index in range(dialog.preview_table.columnCount())
    ] == ["ezqcid", "age", "age next", "错误"]
    assert dialog.preview_table.item(0, 2).text() == "30.0"
    assert dialog.preview_table.item(0, 3).text() == ""
    assert dialog.error_label.text() == ""


def test_dialog_hosts_formula_editor_and_commits_formula_request_once(
    qtbot,
) -> None:
    commits = []

    def persist(formula_request):
        commits.append(formula_request)
        return formula_request.name

    dialog = DerivedColumnDialog(_source(), persist)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText("age_next")
    dialog.editor.set_formula("ROUND([age] / 2, 1)")

    assert isinstance(dialog.editor, FormulaEditorWidget)
    assert dialog.findChildren(QPlainTextEdit)
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: dialog.result() == QDialog.Accepted, timeout=3000)

    assert commits == [
        DerivedColumnFormula("age_next", "ROUND([age] / 2, 1)")
    ]
    assert dialog.committed_column == "age_next"


def test_preview_renders_missing_values_as_blank(qtbot) -> None:
    source = _source()
    source.loc[9, "age"] = pd.NA
    dialog = DerivedColumnDialog(source, lambda request: request.name)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("age_copy")
    dialog.editor.set_formula("[age]")

    assert dialog.preview()
    assert dialog.preview_table.item(1, 2).text() == ""
    assert dialog.preview_table.item(1, 3).text() == ""


def test_row_errors_are_visible_in_preview_and_block_commit(qtbot) -> None:
    source = pd.DataFrame(
        {
            "ezqcid": ["A", "B", "C"],
            "raw": ["4", "bad", pd.NA],
        },
        index=[1, 2, 3],
    )
    commits = []
    dialog = DerivedColumnDialog(source, commits.append)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText("number")
    dialog.editor.set_formula("VALUE([raw])")

    assert dialog.preview()
    assert dialog.preview_table.item(1, 3).text() == "VALUE 无法转换为数值"
    assert "1 行" in dialog.error_label.text()

    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    assert commits == []
    assert dialog.result() != QDialog.Accepted
    assert dialog.isVisible()


def test_dialog_previews_and_commits_fixed_formula(qtbot) -> None:
    commits = []

    def persist(formula_request):
        commits.append(formula_request)
        return formula_request.name

    dialog = DerivedColumnDialog(_source(), persist)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("batch")
    dialog.editor.set_formula('"A"')

    assert dialog.preview()
    assert dialog.preview_table.item(0, 1).text() == "A"
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: dialog.result() == QDialog.Accepted, timeout=3000)
    assert commits[0] == DerivedColumnFormula("batch", '"A"')


def test_dialog_can_preview_missing_ezqcid_target(qtbot) -> None:
    source = pd.DataFrame({"raw_id": [" SUB001 ", "SUB002"]})
    dialog = DerivedColumnDialog(source, lambda request: request.name)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("ezqcid")
    dialog.editor.set_formula("TRIM([raw_id])")

    assert dialog.preview()
    assert dialog.preview_table.horizontalHeaderItem(0).text() == "行"
    assert dialog.preview_table.horizontalHeaderItem(1).text() == "raw_id"
    assert dialog.preview_table.horizontalHeaderItem(2).text() == "ezqcid"
    assert dialog.preview_table.item(0, 2).text() == "SUB001"


def test_formula_validation_error_clears_when_draft_becomes_valid(qtbot) -> None:
    dialog = DerivedColumnDialog(_source(), lambda request: request.name)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("copy")

    dialog.editor.set_formula("[missing]")
    assert "未知列" in dialog.error_label.text()

    dialog.editor.set_formula("[age]")
    assert dialog.error_label.text() == ""
    assert not dialog.error_label.isVisible()


def test_full_source_worker_error_keeps_dialog_open(qtbot) -> None:
    def fail_persist(_request):
        raise ValueError("完整名单第 21 行无法处理")

    dialog = DerivedColumnDialog(_source(), fail_persist)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText("age_next")
    dialog.editor.set_formula("[age] + 1")

    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: not dialog.task_controller.busy,
        timeout=3000,
    )

    assert dialog.result() != QDialog.Accepted
    assert dialog.isVisible()
    assert dialog.error_label.text() == "完整名单第 21 行无法处理"


def test_dialog_rejects_existing_target_without_calling_worker(qtbot) -> None:
    commits = []
    dialog = DerivedColumnDialog(_source(), commits.append)
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.name_edit.setText("age")
    dialog.editor.set_formula("[score]")

    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)

    assert commits == []
    assert "已存在" in dialog.error_label.text()


def test_dialog_remains_reachable_and_accessible_at_640_pixels(qtbot) -> None:
    dialog = DerivedColumnDialog(_source(), lambda request: request.name)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("age next")
    dialog.editor.set_formula("[age] + 1")
    assert dialog.preview()

    dialog.resize(640, 680)
    dialog.show()
    qtbot.waitUntil(lambda: dialog.width() == 640)

    assert dialog.minimumSizeHint().width() <= 640
    assert dialog.name_edit.isVisibleTo(dialog)
    assert dialog.editor.formula_edit.isVisibleTo(dialog)
    assert dialog.preview_table.isVisibleTo(dialog)
    assert dialog.preview_button.isVisibleTo(dialog)
    assert dialog.generate_button.isVisibleTo(dialog)
    assert dialog.cancel_button.isVisibleTo(dialog)
    assert dialog.preview_button.minimumHeight() >= 34
    assert dialog.editor.quick_panel.generate_button.minimumHeight() >= 34
    assert dialog.generate_button.minimumHeight() >= 34
    assert dialog.cancel_button.minimumHeight() >= 34
    for control_type in (QComboBox, QLineEdit, QPlainTextEdit, QPushButton):
        for control in dialog.findChildren(control_type):
            assert control.accessibleName().strip(), type(control).__name__
