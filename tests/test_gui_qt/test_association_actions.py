"""Actual Qt integration of association controls and explicit viewer actions."""
from core.command_output import CommandOutputBatch, CommandOutputStatus
from core.qc_workflow_service import QcWorkflowService
from tests.test_gui_qt.test_qt_product_shell import _window


class _Output:
    def __init__(self):
        self.closed = False

    def command_output_since(self, after_sequence):
        return CommandOutputBatch((), 0, False, CommandOutputStatus("test", False, None))

    def close(self):
        self.closed = True


def test_command_only_keeps_qc_closed_and_output_close_keeps_executor(qtbot, tmp_path, monkeypatch):
    window, services = _window(qtbot, tmp_path)
    executor = _Output()
    services.project_context_service._command_executor = executor
    launched = []
    monkeypatch.setattr(services.project_context_service, "launch_row_command", lambda *args, **kwargs: launched.append((args, kwargs)))
    context = services.project_context_service.qc_row_context(window.current_context, "SUB002")
    window._execute_qc_row_module("SUB002", context.modules[0], window.current_context.context_revision)
    qtbot.waitUntil(lambda: bool(launched) and not window.row_command_controller.busy)
    assert window.qc_controller is None
    assert launched[0][0][1] == "SUB002"
    assert window.command_output_window.isVisible()
    window.command_output_window.close()
    assert not executor.closed
    window.close()
    assert executor.closed


def test_open_module_alone_does_not_execute_but_combined_action_executes_once(qtbot, tmp_path, monkeypatch):
    window, services = _window(qtbot, tmp_path)
    launched = []
    monkeypatch.setattr(QcWorkflowService, "launch_viewer", lambda workflow: launched.append(workflow.current_easyqcid))
    context = services.project_context_service.qc_row_context(window.current_context, "SUB002")
    window._open_qc_row_module("SUB002", context.modules[0], window.current_context.context_revision)
    qtbot.waitUntil(lambda: window.qc_controller is not None and not window.qc_filter_task_controller.busy)
    assert launched == []
    window._open_execute_qc_row_module("SUB003", context.modules[0], window.current_context.context_revision)
    qtbot.waitUntil(lambda: bool(launched))
    assert launched == ["SUB003"]
    assert window.active_workflow.current_easyqcid == "SUB003"


def test_association_entrypoints_exist_and_dialog_applies_rules(qtbot, tmp_path):
    from models.result_association import ResultAssociationRule

    window, services = _window(qtbot, tmp_path)
    assert window.association_action in window.table_workspace.action_toolbar.actions()
    assert window.results_association_action in window.results_workspace.action_toolbar.actions()
    window.association_action.trigger()
    dialog = window.association_dialog
    assert dialog.isVisible()
    rule = ResultAssociationRule("example", "Related", "AnatQC", "rater1", "site", "site", (("score1", "linked.score"),))
    dialog.applyRequested.emit((rule,))
    qtbot.waitUntil(lambda: not window.context_task_controller.busy)
    assert services.configuration_service.result_associations()[0].name == "Related"
    assert "linked.score" in {p.name for p in window.current_context.table_view_service.profiles}


def test_stale_command_action_never_dispatches(qtbot, tmp_path, monkeypatch):
    window, services = _window(qtbot, tmp_path)
    launched = []
    monkeypatch.setattr(services.project_context_service, "launch_row_command", lambda *a, **kw: launched.append(a))
    context = services.project_context_service.qc_row_context(window.current_context, "SUB002")
    window._execute_qc_row_module("SUB002", context.modules[0], window.current_context.context_revision - 1)
    assert not launched
    assert window.shell_error_label.text()
