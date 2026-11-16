"""Command-group save paths use one canonical format and visible group warnings."""

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from core.configuration_service import ConfigurationService
from core.project_service import ProjectService
from core.project_template_service import ProjectTemplateService
from core.table_service import TableService
from core.template_service import TemplateService
from gui_qt.cross_project_settings_page import QtCrossProjectSettingsPage
from gui_qt.i18n import LanguageController
from gui_qt.project_config_workspace import QtProjectConfigWorkspace


def _workspace(qtbot, tmp_path):
    configuration = ConfigurationService(
        ProjectService(tmp_path / "projects.json"), TableService()
    )
    configuration.create_project("SAMPLE", tmp_path)
    templates = TemplateService(tmp_path / "install")
    language = LanguageController(
        settings=QSettings(str(tmp_path / "language.ini"), QSettings.IniFormat),
        language="zh_CN",
    )
    workspace = QtProjectConfigWorkspace(
        configuration,
        templates=templates,
        project_templates=ProjectTemplateService(templates),
        language=language,
    )
    qtbot.addWidget(workspace)
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy)
    qtbot.waitUntil(lambda: not workspace.module_filter_task_controller.busy)
    workspace.module_name.setText("Reviewed")
    return workspace, configuration, templates


@pytest.mark.parametrize("groups,code,mode,count", [
    ([], None, "direct", 0),
    ([("viewer image", "shell")], "viewer image", "shell", 0),
    ([("viewer image", "direct"), ("echo ready", "shell")], None, "direct", 2),
])
def test_workspace_save_as_template_normalizes_zero_one_many_groups(
    qtbot, tmp_path, monkeypatch, groups, code, mode, count,
):
    workspace, _, templates = _workspace(qtbot, tmp_path)
    workspace.command_editor.set_groups(groups)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)

    workspace._save_module_as_template()
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy)

    records = templates.modules().records
    assert len(records) == 1
    payload = records[0].module.to_legacy_dict()
    assert payload["code"] == code
    assert payload["interper"] == mode
    assert len(payload.get("commands", {})) == count
    if count == 0:
        assert "commands" not in payload


@pytest.mark.parametrize("action", [
    "_save_module_form", "_save_module_form_as_new", "_save_module_as_template",
])
@pytest.mark.parametrize("language", ["zh_CN", "en"])
def test_workspace_saves_warn_once_with_visible_numbers_and_cancel_without_write(
    qtbot, tmp_path, monkeypatch, action, language,
):
    workspace, configuration, templates = _workspace(qtbot, tmp_path)
    workspace.language.set_language(language)
    workspace.command_editor.set_groups([
        ("", "direct"), ("viewer image | report", "direct"),
        ("echo ready; echo done", "direct"),
    ])
    before = [module.to_legacy_dict() for module in configuration.modules()]
    warnings = []

    def answer(_parent, title, message, *args, **kwargs):
        if "Shell" in title:
            warnings.append(message)
            return QMessageBox.No
        return QMessageBox.Yes

    monkeypatch.setattr(QMessageBox, "question", answer)
    getattr(workspace, action)()
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy)

    assert len(warnings) == 1
    prefix = "命令组" if language == "zh_CN" else "Command group"
    assert f"{prefix} 2" in warnings[0]
    assert f"{prefix} 3" in warnings[0]
    assert f"{prefix} 1" not in warnings[0]
    assert [module.to_legacy_dict() for module in configuration.modules()] == before
    assert not templates.modules().records


@pytest.mark.parametrize("answer", [QMessageBox.No, QMessageBox.Yes])
def test_template_editor_warns_once_before_persisting_direct_shell_syntax(
    qtbot, tmp_path, monkeypatch, answer,
):
    templates = TemplateService(tmp_path / "install")
    language = LanguageController(
        settings=QSettings(str(tmp_path / "language.ini"), QSettings.IniFormat),
        language="en",
    )
    page = QtCrossProjectSettingsPage(templates, language)
    qtbot.addWidget(page)
    page.module_editor.module_name.setText("Reviewed")
    page.module_editor.command_editor.set_groups([
        ("", "direct"), ("viewer image | report", "direct"),
        ("echo ready; echo done", "direct"),
    ])
    warnings = []

    def respond(_parent, title, message, *args, **kwargs):
        warnings.append((title, message))
        return answer

    monkeypatch.setattr(QMessageBox, "question", respond)
    page.module_editor.save_button.click()

    assert len(warnings) == 1
    assert "Shell" in warnings[0][0]
    assert "Command group 2" in warnings[0][1]
    assert "Command group 3" in warnings[0][1]
    assert "Command group 1" not in warnings[0][1]
    records = templates.modules().records
    assert len(records) == (1 if answer == QMessageBox.Yes else 0)
    if records:
        assert [group.code for group in records[0].module.commands.values()] == [
            "viewer image | report", "echo ready; echo done",
        ]


def test_both_editors_show_the_translated_per_group_hint(qtbot, tmp_path):
    workspace, _, templates = _workspace(qtbot, tmp_path)
    page = QtCrossProjectSettingsPage(templates, workspace.language)
    qtbot.addWidget(page)
    for language in ("zh_CN", "en"):
        workspace.language.set_language(language)
        workspace.retranslate_ui()
        expected = workspace.language.tr("cross.command_groups_hint")
        assert expected != "cross.command_groups_hint"
        assert workspace.module_command_hint.text() == expected
        assert page.module_editor.module_command_hint.text() == expected
