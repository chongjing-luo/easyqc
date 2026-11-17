"""Actual Qt layout and editing-state regressions for command cards."""
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from gui_qt.i18n import LanguageController
from gui_qt.module_command_editor import ModuleCommandGroupsEditor
from gui_qt.theme import apply_easyqc_theme
from models.qcmodule import QCModule


def make_editor(qtbot, tmp_path, language="zh_CN", width=768):
    controller = LanguageController(
        settings=QSettings(str(tmp_path / "language.ini"), QSettings.IniFormat),
        language=language,
    )
    apply_easyqc_theme(QApplication.instance())
    editor = ModuleCommandGroupsEditor(controller)
    qtbot.addWidget(editor)
    editor.set_groups([
        ('freeview -v "${data_dir}/${easyqcid}/mri/T1.mgz"', "direct"),
        ('TEMP_SCENE="${data_dir}/${easyqcid}/qc.scene"\n'
         'sed "s/template/${easyqcid}/g" "${scene_template}" > "$TEMP_SCENE"\n'
         'wb_view -scene-load-hd "$TEMP_SCENE" 1', "shell"),
    ])
    editor.resize(width, 640)
    editor.show()
    qtbot.wait(30)
    return editor


@pytest.mark.parametrize("language", ["zh_CN", "en"])
@pytest.mark.parametrize("width", [320, 768])
def test_cards_fit_requested_width_and_keep_all_header_controls(qtbot, tmp_path, language, width):
    editor = make_editor(qtbot, tmp_path, language, width)
    # 字体度量随平台不同（Windows 下单选对文案更宽）：组件永远可以缩到
    # 自身最小宽度，但绝不小于它——用 Qt 契约表达，而非绝对像素。
    assert editor.width() == max(width, editor.minimumSizeHint().width())
    for card in editor.cards:
        controls = [card.index_label, card.direct_radio, card.shell_radio,
                    card.up_button, card.down_button, card.remove_button]
        rects = []
        for control in controls:
            rect = control.rect().translated(control.mapTo(card, control.rect().topLeft()))
            assert card.rect().contains(rect)
            assert control.width() >= control.minimumSizeHint().width()
            assert all(not rect.intersects(other) for other in rects)
            rects.append(rect)
        assert card.code_edit.viewport().height() >= card.code_edit.fontMetrics().lineSpacing() * 3
        assert card.code_edit.accessibleName()


def test_add_move_remove_keep_existing_editor_undo_cursor_and_verbatim_draft(qtbot, tmp_path):
    editor = make_editor(qtbot, tmp_path)
    card = editor.cards[0]
    card.code_edit.setPlainText("  original text  \n")
    cursor = card.code_edit.textCursor()
    cursor.setPosition(5)
    card.code_edit.setTextCursor(cursor)
    card.code_edit.insertPlainText("XYZ")
    expected = card.code_edit.toPlainText()
    position = card.code_edit.textCursor().position()
    editor.add_group()
    assert editor.cards[0] is card
    assert card.code_edit.toPlainText() == expected
    assert card.code_edit.textCursor().position() == position
    editor._move_card(card, 1)
    assert editor.cards[1] is card
    editor._remove_card(editor.cards[0])
    assert editor.cards[0] is card
    assert card.code_edit.document().isUndoAvailable()
    card.code_edit.undo()
    assert card.code_edit.toPlainText() == "  original text  \n"


def test_delete_last_group_focuses_add_and_retranslation_preserves_input(qtbot, tmp_path):
    editor = make_editor(qtbot, tmp_path)
    card = editor.cards[1]
    text = card.code_edit.toPlainText()
    editor.language.set_language("en")
    assert card.code_edit.toPlainText() == text
    assert card.shell_radio.isChecked()
    assert editor.cards[0].direct_radio.isChecked()
    while editor.cards:
        editor._remove_card(editor.cards[0])
    assert editor.empty_label.isVisible()
    assert editor.add_button.hasFocus()
    qtbot.keyClick(editor.add_button, Qt.Key_Space)
    assert len(editor.cards) == 1
    assert editor.cards[0].code_edit.hasFocus()


def test_long_script_grows_to_readable_height_with_scrollbars(qtbot, tmp_path):
    editor = make_editor(qtbot, tmp_path)
    edit = editor.cards[0].code_edit
    initial = edit.height()
    edit.setPlainText("\n".join("echo " + "x" * 200 for _ in range(30)))
    qtbot.waitUntil(lambda: edit.height() > initial)
    assert edit.horizontalScrollBar().maximum() > 0
    assert edit.verticalScrollBar().maximum() > 0


def test_legacy_split_does_not_reinterpret_keyword_in_literal_path(qtbot, tmp_path):
    editor = make_editor(qtbot, tmp_path)
    code = 'MULTICMD viewer "/data/MULTICMD/image.nii";|echo ready'
    editor.set_module(QCModule(name="Anat", label="Anat", code=code))
    assert editor.groups() == ((code, "direct"),)


def test_capture_command_editor_review(qtbot, tmp_path):
    """Opt-in review evidence, otherwise normal tests write only tmp_path."""
    import os
    output = Path(os.environ.get("EASYQC_COMMAND_REVIEW_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    for language in ("zh_CN", "en"):
        for width in (320, 768):
            editor = make_editor(qtbot, tmp_path, language, width)
            assert editor.grab().save(str(output / f"commands_{language}_{width}.png"))
            editor.close()


@pytest.mark.parametrize("language", ["zh_CN", "en"])
@pytest.mark.parametrize("width", [980, 1280])
def test_integrated_forms_keep_commands_inside_scroll_viewport(qtbot, tmp_path, language, width):
    import os
    from core.configuration_service import ConfigurationService
    from core.project_service import ProjectService
    from core.table_service import TableService
    from gui_qt.project_config_workspace import QtProjectConfigWorkspace
    from gui_qt.module_template_editor import QtModuleTemplateEditor
    from PySide6.QtWidgets import QScrollArea

    output = Path(os.environ.get("EASYQC_COMMAND_REVIEW_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    sample = make_editor(qtbot, tmp_path, language)
    groups = sample.groups()
    controller = sample.language
    sample.close()
    configuration = ConfigurationService(ProjectService(tmp_path / "projects.json"), TableService())
    configuration.create_project("SAMPLE", tmp_path)
    workspace = QtProjectConfigWorkspace(configuration, language=controller)
    qtbot.addWidget(workspace)
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy)
    qtbot.waitUntil(lambda: not workspace.module_filter_task_controller.busy)
    workspace.tabs.setCurrentWidget(workspace.modules_tab)
    workspace.command_editor.set_groups(groups)
    workspace.resize(width, 800)
    controller.register_root(workspace)
    workspace.show()
    workspace.module_splitter.setSizes([240, width - 240])
    workspace.module_editor_scroll.ensureWidgetVisible(workspace.command_editor)
    qtbot.wait(40)
    # 平台字体不同，内容最小宽度不同：必要时放大到"内容恰好放下"的宽度
    # 再断言无横向滚动（上限保证常见桌面分辨率内必须放得下）。
    content = workspace.module_editor_scroll.widget()
    needed = content.minimumSizeHint().width() + (
        workspace.width() - workspace.module_editor_scroll.viewport().width()
    ) + 40
    if needed > width:
        assert needed <= 1680, f"表单最小宽度超出常规桌面: {needed}"
        workspace.resize(needed, 800)
        workspace.module_splitter.setSizes([240, needed - 240])
        workspace.module_editor_scroll.ensureWidgetVisible(
            workspace.command_editor
        )
    qtbot.waitUntil(
        lambda: (
            workspace.module_editor_scroll.horizontalScrollBar().maximum()
            == 0
        ),
        timeout=3000,
    )
    assert workspace.grab().save(
        str(output / f"project_{language}_{width}.png")
    )
    workspace.close()

    scroll = QScrollArea()
    qtbot.addWidget(scroll)
    form = QtModuleTemplateEditor(controller)
    form.command_editor.set_groups(groups)
    form.retranslate_ui()
    controller.register_root(form)
    scroll.setWidgetResizable(True)
    scroll.setWidget(form)
    scroll.resize(width - 280, 800)
    scroll.show()
    scroll.ensureWidgetVisible(form.command_editor)
    qtbot.wait(40)
    form_needed = form.minimumSizeHint().width() + (
        scroll.width() - scroll.viewport().width()
    ) + 8
    if form_needed > scroll.width():
        assert form_needed <= 1680, f"模板表单最小宽度超出常规桌面: {form_needed}"
        scroll.resize(form_needed, 800)
        scroll.ensureWidgetVisible(form.command_editor)
    qtbot.waitUntil(
        lambda: scroll.horizontalScrollBar().maximum() == 0,
        timeout=3000,
    )
    assert scroll.grab().save(str(output / f"template_{language}_{width}.png"))
    scroll.close()


def test_registered_root_language_switch_after_reordering_preserves_card_numbers(qtbot, tmp_path):
    editor = make_editor(qtbot, tmp_path)
    editor.language.register_root(editor)
    editor.language.set_language("en")
    editor._move_card(editor.cards[1], -1)
    text = editor.groups()
    editor.language.set_language("zh_CN")
    editor.language.set_language("en")
    assert [card.index_label.text() for card in editor.cards] == ["Command group 1", "Command group 2"]
    assert editor.groups() == text


@pytest.mark.parametrize("code", ["echo first; echo second", "echo first\necho second", "cd /tmp; MULTICMD viewer a;|viewer b"])
def test_english_warning_body_is_translated(qtbot, tmp_path, monkeypatch, code):
    from gui_qt.module_command_editor import confirm_command_group_syntax
    from PySide6.QtWidgets import QMessageBox
    editor = make_editor(qtbot, tmp_path, "en")
    messages = []
    monkeypatch.setattr(QMessageBox, "question", lambda parent, title, message: messages.append(message) or QMessageBox.No)
    assert confirm_command_group_syntax(editor, editor.language, ((code, "direct"),)) is None
    assert len(messages) == 1
    assert not any("\u4e00" <= char <= "\u9fff" for char in messages[0])
    assert "Shell" in messages[0]
