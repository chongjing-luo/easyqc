from __future__ import annotations

from PySide6.QtCore import QSettings, Qt

from core.code_executor import CodeExecutor
from core.template_service import TemplateService
from gui_qt.cross_project_settings_page import QtCrossProjectSettingsPage
from gui_qt.i18n import LanguageController
from models.qcmodule import QCModule, Score, Tag


def _page(qtbot, tmp_path):
    settings = QSettings(
        str(tmp_path / "language.ini"),
        QSettings.IniFormat,
    )
    language = LanguageController(settings=settings, language="zh_CN")
    templates = TemplateService(tmp_path / "install")
    executor = CodeExecutor()
    page = QtCrossProjectSettingsPage(templates, executor, language)
    qtbot.addWidget(page)
    page.resize(980, 720)
    page.show()
    return page, templates, executor, language


def test_cross_project_page_constant_crud_execution_and_language(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    page, templates, executor, language = _page(qtbot, tmp_path)

    assert page.tabs.count() == 3
    assert [page.tabs.tabText(index) for index in range(3)] == [
        "常量模板",
        "质控模块模板",
        "命令执行",
    ]
    page.constant_name.setText("DATA_ROOT")
    page.constant_value.setText("/data/images")
    qtbot.mouseClick(page.save_constant_button, Qt.LeftButton)

    assert templates.constants() == {"DATA_ROOT": "/data/images"}
    assert page.constants_table.rowCount() == 1
    page.constant_search.setText("missing")
    assert page.constants_table.isRowHidden(0)
    page.constant_search.clear()
    assert not page.constants_table.isRowHidden(0)
    page._edit_constant_row(0, 0)
    page.constant_value.setText("/edited")
    qtbot.mouseClick(page.save_constant_button, Qt.LeftButton)
    assert templates.constants() == {"DATA_ROOT": "/edited"}

    page._reset_constant_form()
    page.constant_name.setText("DATA_ROOT")
    page.constant_value.setText("/duplicate")
    qtbot.mouseClick(page.save_constant_button, Qt.LeftButton)
    assert page.constant_error_label.isVisible()
    assert "DATA_ROOT" in page.constant_error_label.text()
    assert templates.constants() == {"DATA_ROOT": "/edited"}

    page.tabs.setCurrentWidget(page.execution_tab)
    assert page.direct_radio.isChecked()
    page.shell_radio.click()
    qtbot.mouseClick(page.save_execution_button, Qt.LeftButton)
    assert templates.shell_enabled() is True
    assert executor.shell_enabled is True
    assert page.execution_status_label.isVisible()

    page.direct_radio.click()
    monkeypatch.setattr(
        templates,
        "set_shell_enabled",
        lambda _enabled: (_ for _ in ()).throw(OSError("settings write failed")),
    )
    qtbot.mouseClick(page.save_execution_button, Qt.LeftButton)
    assert executor.shell_enabled is True
    assert "settings write failed" in page.execution_error_label.text()

    language.set_language("en")
    assert [page.tabs.tabText(index) for index in range(3)] == [
        "Constant templates",
        "QC module templates",
        "Command execution",
    ]
    assert page.save_constant_button.text() == "Add template"
    assert page.save_execution_button.text() == "Save execution mode"


def test_constant_delete_action_requires_a_visible_selection(
    qtbot,
    tmp_path,
) -> None:
    page, templates, _executor, _language = _page(qtbot, tmp_path)

    assert page.constants_table.rowCount() == 0
    assert page.delete_constant_button.isEnabled() is False

    templates.set_constant("DATA_ROOT", "/data")
    page.refresh_constants()
    assert page.constants_table.rowCount() == 1
    assert page.delete_constant_button.isEnabled() is False

    page.constants_table.selectRow(0)
    assert page.delete_constant_button.isEnabled() is True

    page.constant_search.setText("not-visible")
    assert page.constants_table.isRowHidden(0) is True
    assert page.delete_constant_button.isEnabled() is False

    page.constant_search.clear()
    assert page.constants_table.isRowHidden(0) is False
    assert page.delete_constant_button.isEnabled() is True


def test_cross_project_page_module_editor_preserves_hidden_payload(
    qtbot,
    tmp_path,
) -> None:
    page, templates, _executor, _language = _page(qtbot, tmp_path)
    module = QCModule(
        name="AnatQC",
        label="Anatomical template",
        rater="rater_a",
        scores={
            "1": Score("1", "Quality", "Poor,Good", "Poor,Good"),
            "2": Score("2", "Artifact", "None,Present", "None,Present"),
        },
        tags={
            "1": Tag("1", "Motion"),
            "2": Tag("2", "Motion"),
            "3": Tag("3", "质控模块"),
        },
        code="freeview {image}",
        control=True,
        qc_filter={"groups": [{"id": "hidden-filter"}]},
        button={"help": "SOP"},
    )
    original = templates.add_module(module, display_order=10)
    page.refresh_modules(original.module_id)
    page.tabs.setCurrentWidget(page.modules_tab)

    assert page.module_list.count() == 1
    page.module_list.setCurrentRow(0)
    assert page.module_editor.module_name.text() == "AnatQC"
    assert page.module_editor.tag_editor.tags() == (
        "Motion",
        "Motion",
        "质控模块",
    )
    scores_layout = page.module_editor.scores_group.layout()
    assert scores_layout.itemAt(0).widget() is page.module_editor.score_table
    action_layout = scores_layout.itemAt(1).layout()
    assert [action_layout.itemAt(index).widget() for index in range(4)] == [
        page.module_editor.add_score_button,
        page.module_editor.remove_score_button,
        page.module_editor.move_score_up_button,
        page.module_editor.move_score_down_button,
    ]
    assert not page.module_editor.move_score_up_button.isEnabled()
    assert page.module_editor.move_score_down_button.isEnabled()

    page.module_editor.score_table.setCurrentCell(1, 1)
    qtbot.mouseClick(page.module_editor.move_score_up_button, Qt.LeftButton)
    assert page.module_editor.score_table.currentRow() == 0
    assert page.module_editor.score_table.currentColumn() == 1
    candidate = page.module_editor.candidate()
    assert list(candidate.scores) == ["1", "2"]
    assert [score.label for score in candidate.scores.values()] == [
        "Artifact",
        "Quality",
    ]
    stored_score_labels = [
        score.label
        for score in templates.module(original.module_id).module.scores.values()
    ]
    assert stored_score_labels == [
        "Quality",
        "Artifact",
    ]
    two_rows_height = page.module_editor.score_table.height()
    page.module_editor.clear()
    assert page.module_editor.score_table.rowCount() == 1
    assert page.module_editor.score_table.height() < two_rows_height
    page.module_editor.load_module(module)
    page.module_editor.module_label.setText("Edited label")
    page.module_editor.tag_editor.set_tags(("One", "One", "Two"))
    qtbot.mouseClick(page.module_editor.save_button, Qt.LeftButton)

    saved = templates.module(original.module_id)
    assert saved.module.label == "Edited label"
    assert tuple(tag.label for tag in saved.module.tags.values()) == (
        "One",
        "One",
        "Two",
    )
    assert saved.module.qc_filter == {"groups": [{"id": "hidden-filter"}]}
    assert saved.module.button == {"help": "SOP"}
    assert saved.module.code == "freeview {image}"
    assert saved.module.control is True
    assert page.module_error_label.text() == ""

    broken = templates.module_repository.root / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")
    page.refresh_modules(original.module_id)
    assert page.module_list.count() == 1
    assert "broken.json" in page.module_error_label.text()
    broken.unlink()

    page.delete_module_template(original.module_id)
    assert templates.modules().records == ()
    assert page.module_list.count() == 0
    assert page.module_empty_label.isVisible()


def test_cross_project_score_move_actions_retranslate_and_refresh_state(
    qtbot,
    tmp_path,
) -> None:
    page, _templates, _executor, language = _page(qtbot, tmp_path)
    editor = page.module_editor

    assert editor.move_score_up_button.text() == "上移评分项"
    assert editor.move_score_down_button.text() == "下移评分项"
    assert editor.move_score_up_button.accessibleName() == "上移评分项"
    assert editor.move_score_up_button.toolTip() == "上移评分项"
    assert not editor.move_score_up_button.isEnabled()
    assert not editor.move_score_down_button.isEnabled()

    qtbot.mouseClick(editor.add_score_button, Qt.LeftButton)
    assert editor.move_score_up_button.isEnabled()
    assert not editor.move_score_down_button.isEnabled()
    qtbot.mouseClick(editor.remove_score_button, Qt.LeftButton)
    assert not editor.move_score_up_button.isEnabled()
    assert not editor.move_score_down_button.isEnabled()
    qtbot.mouseClick(editor.add_score_button, Qt.LeftButton)
    editor.discard()
    assert not editor.move_score_up_button.isEnabled()
    assert not editor.move_score_down_button.isEnabled()

    language.set_language("en")
    assert editor.move_score_up_button.text() == "Move score up"
    assert editor.move_score_down_button.text() == "Move score down"
    assert editor.move_score_down_button.accessibleName() == "Move score down"
    assert editor.move_score_down_button.toolTip() == "Move score down"
