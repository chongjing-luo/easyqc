"""Multi-window QC semantics: coexistence, isolation, refocus, dirty gates."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from core.app_services import build_app_services
from core.qc_workflow_service import QcWorkflowService
from gui_qt.application import build_product_window
from tests.test_gui_qt.test_qt_product_shell import (
    _add_project,
    _module_payload,
    _start_module_qc,
    _window,
)


def test_two_module_windows_coexist_with_isolated_executors(qtbot, tmp_path):
    window, _services = _window(qtbot, tmp_path, second_module=True)

    first = _start_module_qc(window, qtbot)              # AnatQC
    second = _start_module_qc(window, qtbot, "FuncQC")   # FuncQC

    assert window.qc_controllers == [first, second]
    assert first.isVisible() and second.isVisible()
    # dedicated executors: navigation/closing one never kills the other's viewers
    assert (
        first.workflow._code_executor
        is not second.workflow._code_executor
    )


def test_closing_one_window_keeps_other_running(qtbot, tmp_path):
    window, _services = _window(qtbot, tmp_path, second_module=True)
    first = _start_module_qc(window, qtbot)
    second = _start_module_qc(window, qtbot, "FuncQC")

    second.close()
    qtbot.waitUntil(lambda: second not in window.qc_controllers)

    assert window.qc_controllers == [first]
    assert window.qc_controller is first          # slot falls back
    assert window.active_workflow is first.workflow
    assert first.isVisible()


def test_readonly_record_opens_beside_writable_session(qtbot, tmp_path):
    """A running (even dirty) writable session never blocks read-only records."""

    # seed one saved rating BEFORE the window so the context snapshot (and the
    # row record menu) knows about it
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE")
    project = services.configuration_service.current_project
    seed = QcWorkflowService(
        _module_payload(),
        services.configuration_service.subjects(),
        rating_dir=project.rating_dir / "AnatQC" / "rater1",
        code_executor=services.code_executor,
    )
    seed.set_score("1", "Good")
    seed.save()
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    writable = _start_module_qc(window, qtbot)

    index = window.table_workspace.table_model.index(0, 0)
    window.table_workspace._open_row_context_menu(
        window.table_workspace.pinned_view,
        window.table_workspace.pinned_view.visualRect(index).center(),
    )
    menu = window.table_workspace.active_row_context_menu
    key = next(iter(menu.record_actions))
    menu.record_actions[key].trigger()

    assert len(window.qc_controllers) == 2
    record = window.qc_controller
    assert record is not writable
    assert record.workflow.initial_read_only
    assert writable.isVisible()


def test_same_module_rater_also_multi_opens(qtbot, tmp_path):
    """Same (module, rater) may open several windows; last write wins."""

    window, _services = _window(qtbot, tmp_path)
    first = _start_module_qc(window, qtbot)

    ok = window.start_qc_module("AnatQC")
    assert ok
    qtbot.waitUntil(lambda: len(window.qc_controllers) == 2, timeout=3000)
    qtbot.waitUntil(lambda: not window.qc_filter_task_controller.busy, timeout=3000)
    assert len(window.qc_controllers) == 2
    assert first in window.qc_controllers
    assert first.isVisible()


def test_dirty_session_no_longer_blocks_other_module_launch(
    qtbot, tmp_path, monkeypatch
):
    # teardown closes the main window while a draft is dirty -> auto-confirm
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    window, _services = _window(qtbot, tmp_path, second_module=True)
    first = _start_module_qc(window, qtbot)

    qtbot.mouseClick(window.qc_workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    assert window.active_workflow.dirty

    # opening a DIFFERENT module while one draft is dirty is now allowed
    second = _start_module_qc(window, qtbot, "FuncQC")
    assert second is not first
    assert len(window.qc_controllers) == 2
    assert first.workflow.dirty          # untouched
