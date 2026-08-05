from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QInputDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
)

from gui_qt.i18n import LanguageController
from gui_qt.module_tag_editor import (
    ModuleTagEditor,
    normalize_stored_tag_labels,
    sync_score_table_height,
)


def _language(tmp_path) -> LanguageController:
    return LanguageController(
        settings=QSettings(
            str(tmp_path / "module-tags-language.ini"),
            QSettings.IniFormat,
        ),
        language="zh_CN",
    )


def _tag_text_buttons(editor: ModuleTagEditor) -> list[QPushButton]:
    return editor.findChildren(QPushButton, "moduleTagText")


def _tag_remove_buttons(editor: ModuleTagEditor) -> list[QToolButton]:
    return editor.findChildren(QToolButton, "moduleTagRemove")


def test_tag_drafts_preserve_duplicate_order_and_edit_remove_exact_chip(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    editor = ModuleTagEditor(_language(tmp_path))
    qtbot.addWidget(editor)
    editor.set_tags(("重复", "重复", "第三项"))

    assert editor.tags() == ("重复", "重复", "第三项")
    assert [button.text() for button in _tag_text_buttons(editor)] == [
        "重复",
        "重复",
        "第三项",
    ]

    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *_args, **_kwargs: ("第二项已编辑", True)),
    )
    qtbot.mouseClick(_tag_text_buttons(editor)[1], Qt.LeftButton)
    assert editor.tags() == ("重复", "第二项已编辑", "第三项")

    qtbot.mouseClick(_tag_remove_buttons(editor)[0], Qt.LeftButton)
    assert editor.tags() == ("第二项已编辑", "第三项")


def test_add_rejects_blank_visibly_then_appends_nonblank_without_autosave(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    editor = ModuleTagEditor(_language(tmp_path))
    qtbot.addWidget(editor)
    editor.set_tags(("既有标签",))
    answers = iter((("   ", True), ("新增标签", True)))
    monkeypatch.setattr(
        QInputDialog,
        "getText",
        staticmethod(lambda *_args, **_kwargs: next(answers)),
    )

    qtbot.mouseClick(editor.add_button, Qt.LeftButton)
    assert editor.tags() == ("既有标签",)
    assert editor.error_label.isVisibleTo(editor)
    assert editor.error_label.text() == "标签不能为空"

    qtbot.mouseClick(editor.add_button, Qt.LeftButton)
    assert editor.tags() == ("既有标签", "新增标签")
    assert not editor.error_label.isVisibleTo(editor)


def test_set_tags_validates_entire_input_before_replacing_draft(qtbot, tmp_path) -> None:
    editor = ModuleTagEditor(_language(tmp_path))
    qtbot.addWidget(editor)
    editor.set_tags(("保留一", "保留二"))

    with pytest.raises(ValueError, match="标签不能为空"):
        editor.set_tags(("新值", "  "))
    assert editor.tags() == ("保留一", "保留二")

    with pytest.raises(TypeError, match="strings"):
        editor.set_tags(("新值", 3))
    assert editor.tags() == ("保留一", "保留二")

    with pytest.raises(TypeError, match="iterable"):
        editor.set_tags("不是标签序列")
    assert editor.tags() == ("保留一", "保留二")


def test_only_exact_single_empty_storage_sentinel_becomes_zero_tags(
    qtbot,
    tmp_path,
) -> None:
    editor = ModuleTagEditor(_language(tmp_path))
    qtbot.addWidget(editor)

    assert normalize_stored_tag_labels((None,)) == ()
    assert normalize_stored_tag_labels(("",)) == ()
    editor.set_tags(normalize_stored_tag_labels((None,)))
    assert editor.tags() == ()

    with pytest.raises(ValueError, match="标签不能为空"):
        editor.set_tags(normalize_stored_tag_labels(("有效", "")))
    with pytest.raises(ValueError, match="标签不能为空"):
        editor.set_tags(normalize_stored_tag_labels((None, "有效")))
    with pytest.raises(ValueError, match="标签不能为空"):
        editor.set_tags(normalize_stored_tag_labels(("   ",)))


def test_language_switch_translates_chrome_without_rewriting_tag_text(
    qtbot,
    tmp_path,
) -> None:
    language = _language(tmp_path)
    editor = ModuleTagEditor(language)
    qtbot.addWidget(editor)
    language.register_root(editor)
    editor.set_tags(("质控模块", "质控模块"))

    assert editor.add_button.text() == "添加标签"
    language.set_language("en")

    assert editor.add_button.text() == "Add tag"
    assert editor.tags() == ("质控模块", "质控模块")
    assert [button.text() for button in _tag_text_buttons(editor)] == [
        "质控模块",
        "质控模块",
    ]
    assert _tag_remove_buttons(editor)[0].accessibleName() == "Remove tag 质控模块"


def test_score_table_height_uses_header_rows_and_frame_and_tracks_row_count(
    qtbot,
) -> None:
    table = QTableWidget(1, 2)
    qtbot.addWidget(table)
    table.setItem(0, 0, QTableWidgetItem("Quality"))
    table.setItem(0, 1, QTableWidgetItem("Poor,Fair,Good"))
    table.show()

    one_row_height = sync_score_table_height(table)
    expected = (
        table.horizontalHeader().height()
        + sum(table.rowHeight(row) for row in range(table.rowCount()))
        + table.frameWidth() * 2
    )
    assert one_row_height == expected
    assert table.minimumHeight() == one_row_height
    assert table.maximumHeight() == one_row_height
    assert table.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff

    table.insertRow(1)
    table.setItem(1, 0, QTableWidgetItem("Artifact"))
    table.setItem(1, 1, QTableWidgetItem("None,Mild,Severe"))
    two_row_height = sync_score_table_height(table)
    assert two_row_height > one_row_height

    table.removeRow(1)
    assert sync_score_table_height(table) == one_row_height
