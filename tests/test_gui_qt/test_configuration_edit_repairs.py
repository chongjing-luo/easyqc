"""RI-12 regressions: editable names and complete module draft persistence.

All projects, template catalogs, and settings live under pytest's tmp_path.
The only injected failure is at the settings-write boundary.
"""

from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QTableWidgetItem

from core.configuration_service import ConfigurationService
from core.project_service import ProjectService
from core.project_template_service import ProjectTemplateService
from core.table_service import TableService
from core.template_service import TemplateService
from gui_qt.cross_project_settings_page import QtCrossProjectSettingsPage
from gui_qt.i18n import LanguageController
from gui_qt.project_config_workspace import QtProjectConfigWorkspace


@pytest.fixture
def editable_workspace(qtbot, tmp_path):
    configuration = ConfigurationService(
        ProjectService(tmp_path / "projects.json"), TableService()
    )
    configuration.create_project("SAMPLE", tmp_path)
    configuration.replace_subjects(
        pd.DataFrame({"easyqcid": ["01", "02"], "site": ["A", "B"]})
    )
    workspace = QtProjectConfigWorkspace(configuration)
    qtbot.addWidget(workspace)
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=3000)
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy, timeout=3000
    )
    yield workspace, configuration
    qtbot.waitUntil(lambda: not workspace.io_task_controller.busy, timeout=3000)
    qtbot.waitUntil(
        lambda: not workspace.module_filter_task_controller.busy, timeout=3000
    )


def _reopen(tmp_path):
    configuration = ConfigurationService(
        ProjectService(tmp_path / "projects.json"), TableService()
    )
    configuration.load_project("SAMPLE")
    return configuration


def _fill_constant(workspace, name, value):
    workspace.constant_name.setText(name)
    workspace.constant_value.setText(value)


def _add_constant(qtbot, workspace, name, value):
    _fill_constant(workspace, name, value)
    qtbot.mouseClick(workspace.save_constant_button, Qt.LeftButton)


def test_project_constant_name_is_editable_by_keyboard(
    qtbot, editable_workspace
):
    workspace, _ = editable_workspace
    _add_constant(qtbot, workspace, "DATA_ROOT", "/original")
    workspace._edit_constant_name("DATA_ROOT")

    assert not workspace.constant_name.isReadOnly()
    workspace.constant_name.selectAll()
    qtbot.keyClicks(workspace.constant_name, "IMAGE_ROOT")
    assert workspace.constant_name.text() == "IMAGE_ROOT"


def test_project_constant_rename_keeps_commands_unchanged(
    qtbot, tmp_path, editable_workspace
):
    workspace, configuration = editable_workspace
    _add_constant(qtbot, workspace, "DATA_ROOT", "/original")
    module = configuration.modules()[0]
    module.code = 'viewer "${DATA_ROOT}/{site}/$DATA_ROOT"'
    configuration.save_module(module, original_name=module.name)
    before_module = deepcopy(configuration.modules()[0].to_legacy_dict())
    workspace._edit_constant_name("DATA_ROOT")
    _fill_constant(workspace, "IMAGE_ROOT", "/renamed")

    qtbot.mouseClick(workspace.save_constant_button, Qt.LeftButton)

    reopened = _reopen(tmp_path)
    assert reopened.constants().get("IMAGE_ROOT") == "/renamed"
    assert "DATA_ROOT" not in reopened.constants()
    assert reopened.modules()[0].to_legacy_dict() == before_module


def test_adding_existing_constant_does_not_overwrite(
    qtbot, tmp_path, editable_workspace
):
    workspace, _ = editable_workspace
    _add_constant(qtbot, workspace, "DATA_ROOT", "/original")
    _add_constant(qtbot, workspace, "DATA_ROOT", "/unwanted-replacement")

    assert _reopen(tmp_path).constants()["DATA_ROOT"] == "/original"
    assert workspace.constant_error_label.text()
    assert workspace.constant_name.text() == "DATA_ROOT"
    assert workspace.constant_value.text() == "/unwanted-replacement"


@pytest.mark.parametrize("new_name", ["OTHER_ROOT", "site", "", "bad-name"])
def test_constant_rename_conflict_or_invalid_name_preserves_state_and_draft(
    qtbot, tmp_path, editable_workspace, new_name
):
    workspace, configuration = editable_workspace
    _add_constant(qtbot, workspace, "DATA_ROOT", "/original")
    _add_constant(qtbot, workspace, "OTHER_ROOT", "/other")
    before = deepcopy(configuration.constants())
    workspace._edit_constant_name("DATA_ROOT")
    _fill_constant(workspace, new_name, "/edited-draft")

    qtbot.mouseClick(workspace.save_constant_button, Qt.LeftButton)

    assert _reopen(tmp_path).constants() == before
    assert workspace.constant_error_label.text()
    assert workspace.constant_name.text() == new_name
    assert workspace.constant_value.text() == "/edited-draft"


def test_template_constant_rename_is_editable_and_project_copy_is_detached(
    qtbot, tmp_path, editable_workspace
):
    _workspace, configuration = editable_workspace
    templates = TemplateService(tmp_path / "installation")
    templates.set_constant("DATA_ROOT", "/template")
    ProjectTemplateService(templates).copy_constant("DATA_ROOT", configuration)
    language = LanguageController(
        settings=QSettings(str(tmp_path / "template-language.ini"), QSettings.IniFormat),
        language="zh_CN",
    )
    page = QtCrossProjectSettingsPage(templates, language)
    qtbot.addWidget(page)
    row = next(
        row for row in range(page.constants_table.rowCount())
        if page.constants_table.item(row, 0).text() == "DATA_ROOT"
    )
    page._edit_constant_row(row, 0)

    assert not page.constant_name.isReadOnly()
    page.constant_name.selectAll()
    qtbot.keyClicks(page.constant_name, "IMAGE_ROOT")
    page.constant_value.setText("/new-template")
    qtbot.mouseClick(page.save_constant_button, Qt.LeftButton)

    assert TemplateService(tmp_path / "installation").constants() == {
        "IMAGE_ROOT": "/new-template"
    }
    assert _reopen(tmp_path).constants()["DATA_ROOT"] == "/template"
    assert "IMAGE_ROOT" not in _reopen(tmp_path).constants()


def test_deleted_constant_is_not_recreated_by_stale_edit(
    qtbot, tmp_path, editable_workspace
):
    workspace, configuration = editable_workspace
    _add_constant(qtbot, workspace, "DATA_ROOT", "/original")
    workspace._edit_constant_name("DATA_ROOT")
    configuration.delete_constant("DATA_ROOT")
    _fill_constant(workspace, "IMAGE_ROOT", "/stale-draft")

    qtbot.mouseClick(workspace.save_constant_button, Qt.LeftButton)

    assert "IMAGE_ROOT" not in _reopen(tmp_path).constants()
    assert workspace.constant_error_label.text()
    assert workspace.constant_name.text() == "IMAGE_ROOT"
    assert workspace.constant_value.text() == "/stale-draft"


_COMMAND = 'echo "${site}";\nprintf "%s\\n" "${easyqcid}"'


def _fill_module_draft(workspace, *, command_first):
    if command_first:
        workspace.module_code.setPlainText(_COMMAND)
    workspace.module_name.setText("DraftQC")
    workspace.module_label.setText("Draft complete module")
    workspace.module_rater.setText("reviewer_2")
    workspace.module_control.setChecked(True)
    workspace.score_table.setRowCount(2)
    for row, (label, options) in enumerate(
        (("Visual quality", "Reject,Accept"), ("Motion", "Absent,Present"))
    ):
        workspace.score_table.setItem(row, 0, QTableWidgetItem(label))
        workspace.score_table.setItem(row, 1, QTableWidgetItem(options))
    workspace.tag_editor.set_tags(("Needs review", "DraftOnlyTag"))
    if not command_first:
        workspace.module_code.setPlainText(_COMMAND)


def _assert_module_complete(module):
    assert module.code == _COMMAND
    assert module.name == "DraftQC"
    assert module.label == "Draft complete module"
    assert module.rater == "reviewer_2"
    assert module.control is True
    assert [(score.label, score.num_) for score in module.scores.values()] == [
        ("Visual quality", "Reject,Accept"), ("Motion", "Absent,Present")
    ]
    assert [tag.label for tag in module.tags.values()] == [
        "Needs review", "DraftOnlyTag"
    ]


@pytest.mark.parametrize("command_first", [True, False])
def test_new_module_persists_complete_draft_in_either_input_order(
    qtbot, tmp_path, editable_workspace, command_first
):
    workspace, _ = editable_workspace
    workspace._prepare_new_module()
    _fill_module_draft(workspace, command_first=command_first)

    qtbot.mouseClick(workspace.save_module_button, Qt.LeftButton)

    saved = next(module for module in _reopen(tmp_path).modules() if module.name == "DraftQC")
    _assert_module_complete(saved)
    assert not workspace.error_text


def test_failed_new_module_commit_leaves_no_shell_and_keeps_complete_draft(
    qtbot, tmp_path, editable_workspace, monkeypatch
):
    workspace, configuration = editable_workspace
    before = [module.to_legacy_dict() for module in configuration.modules()]
    workspace._prepare_new_module()
    _fill_module_draft(workspace, command_first=True)
    draft = workspace._module_form_signature()
    commit = configuration.project_service.commit_settings

    def reject_requested_module(candidate, *args, **kwargs):
        # Reject this requested payload at its storage boundary. A partial
        # default module must not have been persisted before that failure.
        for payload in candidate.get("qcmodule", {}).values():
            if "DraftOnlyTag" in str(payload):
                raise OSError("synthetic settings write failure")
        return commit(candidate, *args, **kwargs)

    monkeypatch.setattr(
        configuration.project_service, "commit_settings", reject_requested_module
    )

    qtbot.mouseClick(workspace.save_module_button, Qt.LeftButton)

    assert "synthetic settings write failure" in workspace.error_text
    assert [module.to_legacy_dict() for module in _reopen(tmp_path).modules()] == before
    assert workspace._module_form_signature() == draft


def test_existing_module_edit_preserves_complete_draft(
    qtbot, tmp_path, editable_workspace
):
    workspace, configuration = editable_workspace
    workspace._load_module_form(configuration.modules()[0])
    _fill_module_draft(workspace, command_first=True)

    qtbot.mouseClick(workspace.save_module_button, Qt.LeftButton)

    saved = next(module for module in _reopen(tmp_path).modules() if module.name == "DraftQC")
    _assert_module_complete(saved)
    assert not workspace.error_text
