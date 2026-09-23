"""Independent RI-13 GUI review probes; synthetic projects and fake viewers."""

from pathlib import Path
from threading import Event, get_ident

import pytest
from PySide6.QtCore import Qt

from core.qc_workflow_service import QcWorkflowService
from gui_qt.i18n import LanguageController
from gui_qt.result_association_dialog import ResultAssociationDialog
from gui_qt.theme import apply_easyqc_theme
from models.result_association import ResultAssociationRule
from tests.test_gui_qt.test_association_actions import _Output
from tests.test_gui_qt.test_qt_product_shell import _window
from tests.test_gui_qt.test_result_association_dialog import _subjects, _modules, _ratings, _rule


def _open_populated_dialog(window):
    window.association_action.trigger()
    dialog = window.association_dialog
    dialog.add_button.click()
    dialog.name_edit.setText("Synthetic association")
    dialog.source_rater_combo.setEditText("rater1")
    for row in range(dialog.fields_table.rowCount()):
        if dialog.fields_table.item(row, 0).data(Qt.UserRole) == "score1":
            dialog.fields_table.item(row, 0).setCheckState(Qt.Checked)
            dialog.fields_table.item(row, 1).setText("linked.score")
            break
    return dialog


def test_apply_context_install_failure_keeps_dialog_editable(qtbot, tmp_path, monkeypatch):
    window, _services = _window(qtbot, tmp_path)
    dialog = _open_populated_dialog(window)

    def fail_install(*_args, **_kwargs):
        raise RuntimeError("synthetic context installation failure")

    monkeypatch.setattr(window, "_apply_context", fail_install)
    try:
        dialog.apply_button.click()
        qtbot.waitUntil(lambda: not window.context_task_controller.busy)
        assert dialog.apply_button.isEnabled(), "Failed context installation leaves Apply permanently disabled"
        assert dialog.error_text
        assert dialog.name_edit.text() == "Synthetic association"
    finally:
        dialog.set_error("review cleanup")
        dialog.reject()


def test_committed_rules_can_retry_after_transient_prepare_failure(qtbot, tmp_path, monkeypatch):
    window, services = _window(qtbot, tmp_path)
    dialog = _open_populated_dialog(window)
    original = services.project_context_service.prepare_initial
    calls = []

    def fail_prepare_once():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("synthetic prepare failure after settings commit")
        return original()

    monkeypatch.setattr(services.project_context_service, "prepare_initial", fail_prepare_once)
    try:
        dialog.apply_button.click()
        qtbot.waitUntil(lambda: not window.context_task_controller.busy)
        assert services.configuration_service.result_associations()
        assert dialog.error_text
        dialog.apply_button.click()
        qtbot.waitUntil(lambda: not window.context_task_controller.busy)
        assert window.association_dialog is None, dialog.error_text
        assert "linked.score" in {p.name for p in window.current_context.table_view_service.profiles}
    finally:
        if window.association_dialog is not None:
            dialog.set_error("review cleanup")
            dialog.reject()


@pytest.mark.parametrize("combined", (False, True))
def test_linked_module_opens_exact_source_rater_read_only_by_default(qtbot, tmp_path, monkeypatch, combined):
    window, services = _window(qtbot, tmp_path)
    rule = ResultAssociationRule(
        "source", "Source", "AnatQC", "source_rater", "site", "site", (("notes", "linked.notes"),),
    )
    services.configuration_service.save_result_associations((rule,), notify=False)
    window.refresh_context()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy)
    snapshot = window.current_context
    context = services.project_context_service.qc_row_context(snapshot, "SUB001")
    entry = context.linked_modules[0]
    monkeypatch.setattr(QcWorkflowService, "launch_viewer", lambda workflow: [])
    method = window._open_execute_qc_row_module if combined else window._open_qc_row_module
    method("SUB001", entry, snapshot.context_revision)
    qtbot.waitUntil(lambda: window.qc_workspace is not None and not window.qc_filter_task_controller.busy)
    assert window.active_workflow.current_easyqcid == entry.easyqcid
    assert window.active_workflow.current_module.rater == "source_rater"
    assert window.qc_workspace.read_only_box.isChecked(), "Following an association enables editing another rater's records"


def test_combined_action_dispatches_viewer_startup_off_gui_thread(qtbot, tmp_path, monkeypatch):
    window, services = _window(qtbot, tmp_path)
    gui_thread = get_ident()
    threads = []
    monkeypatch.setattr(QcWorkflowService, "launch_viewer", lambda workflow: threads.append(get_ident()))
    snapshot = window.current_context
    context = services.project_context_service.qc_row_context(snapshot, "SUB002")
    window._open_execute_qc_row_module("SUB002", context.modules[0], snapshot.context_revision)
    qtbot.waitUntil(lambda: bool(threads))
    assert threads[0] != gui_thread, "CodeExecutor's per-command 350ms startup waits run in the Qt thread"


def test_command_only_preserves_unsaved_qc_draft_and_uses_exact_source(qtbot, tmp_path, monkeypatch):
    from dataclasses import replace

    window, services = _window(qtbot, tmp_path)
    snapshot = window.current_context
    context = services.project_context_service.qc_row_context(snapshot, "SUB001")
    window._open_qc_row_module("SUB001", context.modules[0], snapshot.context_revision)
    qtbot.waitUntil(lambda: window.active_workflow is not None and not window.qc_filter_task_controller.busy)
    workflow = window.active_workflow
    workflow.set_notes("unsaved synthetic draft")
    executor = _Output()
    services.project_context_service._command_executor = executor
    calls = []
    monkeypatch.setattr(services.project_context_service, "launch_row_command", lambda *args, **kwargs: calls.append((args, kwargs)))
    entry = replace(context.modules[0], easyqcid="SUB002", rater="source_rater")
    window._execute_qc_row_module("SUB001", entry, snapshot.context_revision)
    qtbot.waitUntil(lambda: bool(calls) and not window.row_command_controller.busy)
    assert calls[0][0][1] == "SUB002"
    assert calls[0][1]["rater_override"] == "source_rater"
    assert window.active_workflow is workflow
    assert workflow.dirty
    assert workflow.current_module.notes == "unsaved synthetic draft"
    workflow.discard_changes()


def test_close_waits_for_real_apply_without_discarding_draft(qtbot, tmp_path, monkeypatch):
    window, services = _window(qtbot, tmp_path)
    dialog = _open_populated_dialog(window)
    entered, release = Event(), Event()
    original = services.configuration_service.save_result_associations

    def delayed_save(*args, **kwargs):
        entered.set()
        if not release.wait(timeout=5):
            raise RuntimeError("synthetic test did not release apply")
        return original(*args, **kwargs)

    monkeypatch.setattr(services.configuration_service, "save_result_associations", delayed_save)
    try:
        dialog.apply_button.click()
        qtbot.waitUntil(entered.is_set)
        assert not dialog.close()
        assert not window.close()
        assert window.isVisible() and dialog.isVisible()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window.context_task_controller.busy)
    assert window.association_dialog is None
    assert services.configuration_service.result_associations()


@pytest.mark.parametrize(("route", "fail_start"), (
    ("record", False), ("table", False), ("queue", False), ("refresh", False), ("record", True),
))
def test_combined_worker_blocks_navigation_refresh_and_window_close(qtbot, tmp_path, monkeypatch, route, fail_start):
    window, services = _window(qtbot, tmp_path)
    seed = services.project_context_service.create_qc_workflow(
        window.current_context, module_name="AnatQC", initial_easyqcid="SUB003",
    )
    seed.set_notes("synthetic saved record")
    seed.save()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy)
    seed.close()
    snapshot = window.current_context
    record = services.project_context_service.qc_row_context(snapshot, "SUB003").records[0]
    module = services.project_context_service.qc_row_context(snapshot, "SUB001").modules[0]
    entered, release = Event(), Event()
    launches = []

    def delayed_start(workflow):
        launches.append(workflow.current_easyqcid)
        entered.set()
        if len(launches) == 1 and not release.wait(timeout=5):
            raise RuntimeError("synthetic viewer was not released")
        if fail_start:
            raise RuntimeError("synthetic combined startup failed")
        return []

    monkeypatch.setattr(QcWorkflowService, "launch_viewer", delayed_start)
    window._open_execute_qc_row_module("SUB001", module, snapshot.context_revision)
    try:
        qtbot.waitUntil(entered.is_set)
        controller = window.qc_controller
        assert controller.workspace.filter_busy
        assert not controller.command_output_panel.timer.isActive()
        assert not controller.command_output_panel.clear_button.isEnabled()
        assert not controller.close()
        assert not window.close()
        if route == "record":
            window.table_workspace.on_open_qc_record(record)
        elif route == "table":
            window.table_workspace.on_open_qc("SUB003")
        elif route == "queue":
            index = controller.workspace.queue_model.index(2, 0)
            controller.workspace.queue_table.doubleClicked.emit(index)
        else:
            assert not window.refresh_context()
        assert window.qc_controller is controller, f"{route} replaced the workflow during viewer startup"
        assert controller.workflow.current_easyqcid == "SUB001", f"{route} changed identity during viewer startup"
        assert launches == ["SUB001"], f"{route} launched a second viewer during startup"
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window.qc_filter_task_controller.busy)
        qtbot.waitUntil(lambda: not window.context_task_controller.busy)
    assert not controller.workspace.filter_busy
    assert controller.command_output_panel.timer.isActive()
    assert controller.command_output_panel.clear_button.isEnabled()
    if fail_start:
        assert "synthetic combined startup failed" in window.shell_error_label.text()


@pytest.mark.parametrize("fail_start", (False, True))
def test_command_only_disables_manual_output_query_during_startup(qtbot, tmp_path, monkeypatch, fail_start):
    window, services = _window(qtbot, tmp_path)
    entered, release = Event(), Event()
    services.project_context_service._command_executor = _Output()

    def delayed_command(*_args, **_kwargs):
        entered.set()
        if not release.wait(timeout=5):
            raise RuntimeError("synthetic command was not released")
        if fail_start:
            raise RuntimeError("synthetic command startup failed")
        return []

    monkeypatch.setattr(services.project_context_service, "launch_row_command", delayed_command)
    snapshot = window.current_context
    entry = services.project_context_service.qc_row_context(snapshot, "SUB001").modules[0]
    window._execute_qc_row_module("SUB001", entry, snapshot.context_revision)
    try:
        qtbot.waitUntil(entered.is_set)
        panel = window.row_command_output_panel
        assert not panel.timer.isActive()
        assert not panel.clear_button.isEnabled()
        assert panel.copy_button.isEnabled()
        window.command_output_window.close()
    finally:
        release.set()
        qtbot.waitUntil(lambda: not window.row_command_controller.busy)
    assert panel.timer.isActive()
    assert panel.clear_button.isEnabled()


@pytest.mark.parametrize(("language", "suffix"), (("zh_CN", "zh"), ("en", "en")))
def test_capture_synthetic_dialog_at_800_by_650(qtbot, qapp, tmp_path, language, suffix):
    apply_easyqc_theme(qapp)
    dialog = ResultAssociationDialog(_subjects(), _modules(), _ratings(), (_rule(),),
                                     language=LanguageController(language=language))
    qtbot.addWidget(dialog)
    dialog.resize(800, 650)
    dialog.show()
    qapp.processEvents()
    assert dialog.size().width() == 800 and dialog.size().height() == 650
    destination = tmp_path / f"RI13_association_{suffix}.png"
    assert dialog.grab().save(str(destination))
