"""Independent RI-12 Qt review: dialog lifecycle during background rename."""

from threading import Event

import pandas as pd
import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QDialogButtonBox

from core.configuration_service import ConfigurationService
from core.project_service import ProjectService
from core.table_service import TableService
from core.table_transform import TableTransformEngine
from core.table_view_service import TableViewService
from core.template_service import TemplateService
from gui_qt.cross_project_settings_page import QtCrossProjectSettingsPage
from gui_qt.i18n import LanguageController
from gui_qt.qc_list_import_page import QtQcListImportPage
from gui_qt.table_workspace import QtTableWorkspace


def _configuration(tmp_path):
    configuration = ConfigurationService(
        ProjectService(tmp_path / "projects.json"), TableService()
    )
    configuration.create_project("SAMPLE", tmp_path)
    configuration.replace_subjects(
        pd.DataFrame({"easyqcid": ["A", "B"], "site": ["01", "02"]})
    )
    return configuration


def test_stale_template_constant_rename_does_not_recreate_deleted_source(
    qtbot, tmp_path
):
    templates = TemplateService(tmp_path / "installation")
    templates.set_constant("ROOT", "/original")
    language = LanguageController(
        settings=QSettings(str(tmp_path / "language.ini"), QSettings.IniFormat),
        language="zh_CN",
    )
    page = QtCrossProjectSettingsPage(templates, language)
    qtbot.addWidget(page)
    page._edit_constant_row(0, 0)
    page.constant_name.setText("RENAMED_ROOT")
    page.constant_value.setText("/unsaved")
    templates.delete_constant("ROOT")

    qtbot.mouseClick(page.save_constant_button, Qt.LeftButton)

    assert TemplateService(tmp_path / "installation").constants() == {}
    assert page.constant_error_label.text()
    assert page.constant_name.text() == "RENAMED_ROOT"
    assert page.constant_value.text() == "/unsaved"


@pytest.mark.parametrize("write_fails", [False, True])
def test_master_rename_cancel_while_worker_runs_has_safe_completion(
    qtbot, tmp_path, write_fails
):
    configuration = _configuration(tmp_path)
    started, release = Event(), Event()

    def rename(old, new):
        started.set()
        assert release.wait(3), "test did not release the synthetic worker"
        if write_fails:
            raise OSError("synthetic rename failure")
        return configuration.rename_subject_column(old, new, notify=False)

    workspace = QtTableWorkspace(
        configuration.subjects(),
        rename_column_callback=rename,
        rename_source_columns=lambda: tuple(configuration.subjects().columns),
        on_data_mutation_committed=lambda: workspace.replace_service(
            TableViewService(configuration.subjects()), preserve_state=True
        ),
    )
    qtbot.addWidget(workspace)
    dialog = workspace.open_rename_column_dialog()
    dialog.name_edit.setText("session")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    try:
        qtbot.waitUntil(started.is_set)
        qtbot.mouseClick(dialog.button_box.button(QDialogButtonBox.Cancel), Qt.LeftButton)
        qtbot.wait(10)  # Allow deferred widget deletion before worker delivery.
    finally:
        release.set()
    qtbot.waitUntil(lambda: not workspace.mutation_busy)

    # Either keep a submitted transaction open, or implement real cancellation;
    # never leave persisted data and the displayed service out of sync.
    assert tuple(configuration.subjects().columns) == workspace.applied_state.columns.order
    assert "deleted" not in workspace.error_text.lower()
    if write_fails:
        assert "synthetic rename failure" in workspace.error_text


@pytest.mark.parametrize("write_fails", [False, True])
def test_import_rename_cancel_while_worker_runs_does_not_access_deleted_dialog(
    qtbot, tmp_path, monkeypatch, write_fails
):
    configuration = _configuration(tmp_path)
    page = QtQcListImportPage(configuration)
    qtbot.addWidget(page)
    original = pd.DataFrame({"rest": ["01", "02"]})
    page._replace_draft(original, reset_state=True)
    started, release = Event(), Event()
    real_rename = TableTransformEngine.rename_columns

    def rename(engine, frame, names):
        started.set()
        assert release.wait(3), "test did not release the synthetic worker"
        if write_fails:
            raise OSError("synthetic draft rename failure")
        return real_rename(engine, frame, names)

    monkeypatch.setattr(TableTransformEngine, "rename_columns", rename)
    dialog = page.open_rename_column_dialog()
    dialog.name_edit.setText("session")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    try:
        qtbot.waitUntil(started.is_set)
        qtbot.mouseClick(dialog.button_box.button(QDialogButtonBox.Cancel), Qt.LeftButton)
        qtbot.wait(10)
    finally:
        release.set()
    qtbot.waitUntil(lambda: not page.task_controller.busy)

    assert "deleted" not in page.error_text.lower()
    assert page._pending is None
    assert page._pending_rename is None
    if write_fails:
        pd.testing.assert_frame_equal(page._draft, original)
        assert "synthetic draft rename failure" in page.error_text
