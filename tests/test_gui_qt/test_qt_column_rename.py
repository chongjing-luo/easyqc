"""Qt integration regressions for source-column renaming."""

import pandas as pd
from PySide6.QtCore import Qt

from core.configuration_service import ConfigurationService
from core.project_service import ProjectService
from core.table_service import TableService
from core.table_view_service import TableViewService
from gui_qt.qc_list_import_page import QtQcListImportPage
from gui_qt.table_workspace import QtTableWorkspace
from models.table_view_state import FilterCondition, FilterExpression, FilterGroup, SortRule


def config_for(tmp_path):
    config = ConfigurationService(ProjectService(tmp_path / "projects.json"), TableService())
    config.create_project("SAMPLE", tmp_path / "project")
    config.replace_subjects(pd.DataFrame({"easyqcid": ["A", "B"], "site": ["01", "02"]}))
    return config


def test_import_rename_to_identity_only_changes_draft(qtbot, tmp_path):
    config = config_for(tmp_path)
    before = config.subjects()
    page = QtQcListImportPage(config)
    qtbot.addWidget(page)
    page._replace_draft(pd.DataFrame({"relative_parent": ["x", "y"], "rest": ["01", "02"]}), reset_state=True)
    dialog = page.open_rename_column_dialog()
    assert dialog is not None
    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("rest"))
    dialog.name_edit.setText("easyqcid")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not page.task_controller.busy)
    assert page._draft.columns.tolist() == ["relative_parent", "easyqcid"]
    assert page._draft["easyqcid"].tolist() == ["01", "02"]
    pd.testing.assert_frame_equal(config.subjects(), before)


def test_import_rename_conflict_keeps_draft_and_dialog(qtbot, tmp_path):
    config = config_for(tmp_path)
    config.add_constant("ROOT", "/data")
    page = QtQcListImportPage(config)
    qtbot.addWidget(page)
    page._replace_draft(pd.DataFrame({"rest": ["01"]}), reset_state=True)
    dialog = page.open_rename_column_dialog()
    dialog.name_edit.setText("ROOT")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not page.task_controller.busy)
    assert page._draft.columns.tolist() == ["rest"]
    assert dialog.isVisible() and dialog.error_text
    dialog.reject()


def test_import_rename_to_identity_preserves_filter_and_makes_identity_visible(qtbot, tmp_path):
    page = QtQcListImportPage(config_for(tmp_path))
    qtbot.addWidget(page)
    page._replace_draft(pd.DataFrame({"parent": ["a", "b"], "rest": ["01", "02"]}), reset_state=True)
    assert page.apply_preview_filter(FilterExpression(groups=(FilterGroup("g", "all", (FilterCondition("rest", "==", "02", "c"),)),)))
    dialog = page.open_rename_column_dialog()
    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("rest"))
    dialog.name_edit.setText("easyqcid")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not page.task_controller.busy)
    assert page._preview_state.conditions[0].column == "easyqcid"
    assert page._preview_state.columns.order[0] == "easyqcid"
    assert "easyqcid" in page._preview_state.columns.pinned
    assert page._preview_result.matched_total == 1


def test_import_rename_preserves_current_filter_and_sort(qtbot, tmp_path):
    page = QtQcListImportPage(config_for(tmp_path))
    qtbot.addWidget(page)
    page._replace_draft(pd.DataFrame({"rest": ["01", "02"]}), reset_state=True)
    assert page.apply_preview_filter(FilterExpression(groups=(FilterGroup("g", "all", (FilterCondition("rest", "==", "02", "c"),)),)))
    assert page.apply_preview_sort((SortRule("rest", False),))
    dialog = page.open_rename_column_dialog()
    dialog.name_edit.setText("session")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not page.task_controller.busy)
    assert page._preview_state.conditions[0].column == "session"
    assert page._preview_state.sort_rules == (SortRule("session", False),)
    assert page._preview_result.matched_total == 1


def test_master_rename_uses_worker_and_preserves_view_on_refresh(qtbot, tmp_path):
    config = config_for(tmp_path)
    source = config.subjects()
    state = TableViewService(source).default_state().with_conditions((FilterCondition("site", "==", "02"),))
    workspace = QtTableWorkspace(source, initial_state=state,
        rename_column_callback=lambda old, new: config.rename_subject_column(old, new, notify=False),
        rename_source_columns=lambda: tuple(config.subjects().columns),
        on_data_mutation_committed=lambda: workspace.replace_service(TableViewService(config.subjects()), preserve_state=True))
    qtbot.addWidget(workspace)
    dialog = workspace.open_rename_column_dialog()
    assert dialog.source_combo.findData("easyqcid") == -1
    dialog.name_edit.setText("session")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not workspace.mutation_busy)
    assert "session" in workspace.applied_state.columns.order
    assert workspace.applied_state.conditions[0].column == "session"
    assert workspace.result.matched_total == 1


def test_rename_dialog_cancel_conflict_and_long_names(qtbot):
    from gui_qt.rename_column_dialog import RenameColumnDialog
    dialog = RenameColumnDialog(("easyqcid", "site", "age"), protected_columns=("easyqcid",))
    qtbot.addWidget(dialog)
    emitted = []
    dialog.renameRequested.connect(lambda old, new: emitted.append((old, new)))
    dialog.show()
    dialog.name_edit.setText("age")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert not emitted and dialog.error_text
    dialog.name_edit.setText("session")
    qtbot.mouseClick(dialog.apply_button, Qt.LeftButton)
    assert emitted == [("site", "session")]
    assert not dialog.apply_button.isEnabled()
    dialog.set_error("synthetic failure")
    assert dialog.apply_button.isEnabled()
    dialog.reject()
