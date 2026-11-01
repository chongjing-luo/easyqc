from __future__ import annotations

from PySide6.QtCore import QSettings

from gui_qt.i18n import LanguageController
from gui_qt.module_command_editor import ModuleCommandGroupsEditor
from models.qcmodule import CommandGroup, QCModule


def _language(tmp_path) -> LanguageController:
    return LanguageController(
        settings=QSettings(
            str(tmp_path / "language.ini"), QSettings.IniFormat
        )
    )


def test_editor_adds_moves_removes_and_renumbers_groups(qtbot, tmp_path):
    editor = ModuleCommandGroupsEditor(_language(tmp_path))
    qtbot.addWidget(editor)

    assert editor.groups() == (("", "direct"),)
    editor.cards[0].code_edit.setPlainText("first cmd")
    editor.add_group()
    editor.cards[1].code_edit.setPlainText("second cmd")
    editor.cards[1].shell_radio.click()
    editor.add_group()
    editor.cards[2].code_edit.setPlainText("third cmd")
    assert editor.groups() == (
        ("first cmd", "direct"),
        ("second cmd", "shell"),
        ("third cmd", "direct"),
    )
    assert [card.index_label.text() for card in editor.cards] == [
        "命令组 1",
        "命令组 2",
        "命令组 3",
    ]
    assert not editor.cards[0].up_button.isEnabled()
    assert not editor.cards[2].down_button.isEnabled()
    assert editor.cards[1].up_button.isEnabled()
    assert editor.cards[1].down_button.isEnabled()

    editor._move_card(editor.cards[2], -1)
    assert editor.groups() == (
        ("first cmd", "direct"),
        ("third cmd", "direct"),
        ("second cmd", "shell"),
    )

    editor._remove_card(editor.cards[0])
    assert editor.groups() == (
        ("third cmd", "direct"),
        ("second cmd", "shell"),
    )
    assert [card.index_label.text() for card in editor.cards] == [
        "命令组 1",
        "命令组 2",
    ]

    # 空白组在 payload 里丢弃，但 groups() 原样返回
    editor.add_group()
    assert editor.groups()[-1] == ("", "direct")
    payload = editor.command_groups_payload()
    assert [(group.code, group.interper) for group in payload.values()] == [
        ("third cmd", "direct"),
        ("second cmd", "shell"),
    ]


def test_editor_splits_legacy_multicmd_but_keeps_prefix_whole(
    qtbot, tmp_path,
):
    editor = ModuleCommandGroupsEditor(_language(tmp_path))
    qtbot.addWidget(editor)

    editor.set_module(
        QCModule(
            name="A",
            label="A",
            code="MULTICMD\nfreeview {image}\n;|\nTMP=$(mktemp)",
            interper="shell",
        )
    )
    assert editor.groups() == (
        ("freeview {image}", "shell"),
        ("TMP=$(mktemp)", "shell"),
    )

    editor.set_module(
        QCModule(
            name="B",
            label="B",
            code="cd /data; MULTICMD a;|b",
            interper="shell",
        )
    )
    assert editor.groups() == (("cd /data; MULTICMD a;|b", "shell"),)

    editor.set_module(
        QCModule(
            name="C",
            label="C",
            commands={
                "1": CommandGroup("1", "viewer", "direct"),
                "2": CommandGroup("2", "echo | cat", "shell"),
            },
        )
    )
    assert editor.groups() == (
        ("viewer", "direct"),
        ("echo | cat", "shell"),
    )

    # 无命令模块与空文本都给一张空白直接组卡片
    editor.set_module(QCModule(name="D", label="D"))
    assert editor.groups() == (("", "direct"),)


def test_editor_switches_language_without_losing_drafts(qtbot, tmp_path):
    language = _language(tmp_path)
    editor = ModuleCommandGroupsEditor(language)
    qtbot.addWidget(editor)
    editor.cards[0].code_edit.setPlainText("viewer {image}")
    editor.add_group()

    language.set_language("en")

    assert editor.cards[0].index_label.text() == "Command group 1"
    assert editor.cards[1].index_label.text() == "Command group 2"
    assert editor.cards[0].direct_radio.text() == (
        "Direct execution (shell=False)"
    )
    assert editor.add_button.text() == "Add command group"
    assert editor.groups() == (
        ("viewer {image}", "direct"),
        ("", "direct"),
    )
