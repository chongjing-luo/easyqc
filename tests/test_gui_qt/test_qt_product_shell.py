from __future__ import annotations

from threading import Event

import pandas as pd
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QMessageBox

from core.app_services import build_app_services
from core.project_context_service import ProjectContextError
from gui_qt.application import build_product_window
from models.table_view_state import SortRule



def _module_payload(*, name="AnatQC", rater="rater1"):
    return {
        "name": name,
        "label": f"{name} label",
        "rater": rater,
        "ezqcid": None,
        "watch_mode": False,
        "tags": {"1": {"label": "Motion", "value": False}},
        "scores": {
            "1": {
                "label": "Quality",
                "num": "Poor,Fair,Good",
                "num_": "Poor,Fair,Good",
                "value": None,
            }
        },
        "code": "freeview {image}",
        "interper": "shell",
        "control": True,
        "select_filter": None,
        "showing": True,
        "code_exe": None,
        "time": None,
        "notes": None,
        "button": {},
    }


def _add_project(services, tmp_path, name, *, prefix="", second_module=False):
    configuration = services.configuration_service
    configuration.create_project(name, tmp_path)
    configuration.replace_subjects(
        pd.DataFrame(
            {
                "ezqcid": ["SUB001", "SUB002", "SUB003"],
                "site": ["A", "B", "C"],
                "image": [f"/{prefix}one.nii", f"/{prefix}two.nii", f"/{prefix}three.nii"],
            }
        )
    )
    configuration.save_module(_module_payload(), original_name="example")
    if second_module:
        configuration.add_module("FuncQC", "Functional QC")
        configuration.save_module(
            _module_payload(name="FuncQC", rater="rater2"),
            original_name="FuncQC",
        )


def _window(qtbot, tmp_path, *, second_module=False):
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE", second_module=second_module)
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    return window, services


def test_empty_product_shell_keeps_configuration_available_without_writes(qtbot, tmp_path) -> None:
    registry = tmp_path / "projects.json"
    services = build_app_services(registry)
    window = build_product_window(services)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    assert window.current_context.project_name == ""
    assert window.workspace_tabs.count() == 3
    assert window.workspace_tabs.isTabEnabled(window.config_tab_index)
    assert not window.workspace_tabs.isTabEnabled(window.qc_tab_index)
    assert "No project" in window.shell_empty_label.text()
    assert not registry.exists()


def test_product_shell_loads_last_project_and_three_shared_workspaces(qtbot, tmp_path) -> None:
    window, services = _window(qtbot, tmp_path)

    assert window.current_context.project_name == "SAMPLE"
    assert window.project_combo.currentText() == "SAMPLE"
    assert window.module_combo.currentData() == "AnatQC"
    assert window.table_workspace.result.matched_total == 3
    assert window.qc_workspace is not None
    assert window.qc_workspace.workflow.current_module.name == "AnatQC"
    assert window.config_workspace.configuration is services.configuration_service
    assert [window.workspace_tabs.tabText(i) for i in range(3)] == [
        "Table",
        "QC",
        "Project configuration",
    ]


def test_table_open_qc_uses_applied_sort_queue_and_selected_identity(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path)
    table = window.table_workspace
    assert table.apply_sort_rules((SortRule("site", ascending=False),))
    assert table.select_source_position(2)

    assert table.open_selected_qc()

    assert window.workspace_tabs.currentIndex() == window.qc_tab_index
    assert window.qc_workspace.workflow.subject_ids == ("SUB003", "SUB002", "SUB001")
    assert window.qc_workspace.workflow.current_ezqcid == "SUB003"


def test_stale_table_callback_is_rejected_after_same_id_project_switch(qtbot, tmp_path) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "ALPHA", prefix="alpha/")
    _add_project(services, tmp_path, "BETA", prefix="beta/")
    window = build_product_window(services)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    assert window.load_project("ALPHA")
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    stale_callback = window.table_workspace.on_open_qc

    assert window.load_project("BETA")
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    current_workflow = window.qc_workspace.workflow
    stale_callback("SUB001")

    assert window.current_context.project_name == "BETA"
    assert window.qc_workspace.workflow is current_workflow
    assert "stale" in window.shell_error_label.text().lower()


def test_failed_qc_replacement_keeps_old_workflow_then_success_closes_it(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, services = _window(qtbot, tmp_path, second_module=True)
    previous = window.qc_workspace.workflow
    close_calls = []
    monkeypatch.setattr(
        services.code_executor,
        "close_current_processes",
        lambda *args, **kwargs: close_calls.append(True),
    )
    real_factory = services.project_context_service.create_qc_workflow

    def fail_func(*args, **kwargs):
        if kwargs.get("module_name") == "FuncQC":
            raise ProjectContextError("replacement failed")
        return real_factory(*args, **kwargs)

    monkeypatch.setattr(services.project_context_service, "create_qc_workflow", fail_func)
    window.module_combo.setCurrentIndex(window.module_combo.findData("FuncQC"))

    assert window.qc_workspace.workflow is previous
    assert close_calls == []
    assert "replacement failed" in window.shell_error_label.text()

    monkeypatch.setattr(services.project_context_service, "create_qc_workflow", real_factory)
    window.module_combo.setCurrentIndex(window.module_combo.findData("FuncQC"))
    assert window.qc_workspace.workflow is not previous
    assert close_calls == [True]


def test_dirty_qc_disables_context_replacement_and_close_requires_confirmation(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    qtbot.mouseClick(window.qc_workspace.score_buttons["1"]["Good"], Qt.LeftButton)

    assert window.qc_workspace.workflow.dirty
    assert not window.project_combo.isEnabled()
    assert not window.module_combo.isEnabled()
    assert not window.config_workspace.isEnabled()
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No)
    assert not window.close()
    assert window.isVisible()

    qtbot.mouseClick(window.qc_workspace.discard_button, Qt.LeftButton)
    assert not window.qc_workspace.workflow.dirty
    assert window.project_combo.isEnabled()
    assert window.config_workspace.isEnabled()


def test_rating_save_refreshes_table_and_preserves_view_state_and_qc_session(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    table = window.table_workspace
    assert table.apply_sort_rules((SortRule("site", ascending=False),))
    assert table.find_identity_exact("SUB001")
    assert table.open_selected_qc()
    qc = window.qc_workspace
    qtbot.mouseClick(qc.score_buttons["1"]["Good"], Qt.LeftButton)
    qtbot.mouseClick(qc.save_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)

    assert window.qc_workspace is qc
    assert window.table_workspace.applied_state.sort_rules == (
        SortRule("site", ascending=False),
    )
    result_frame = window.table_workspace.service.get_window(
        window.table_workspace.result,
        0,
        max(1, window.table_workspace.result.matched_total),
    ).dataframe
    assert "AnatQC.rater1.score1" in result_frame.columns
    row = result_frame.set_index("ezqcid").loc["SUB001"]
    assert row["AnatQC.rater1.score1"] == "Good"
    assert window.table_workspace.find_identity_exact("SUB001")


def test_initial_project_materialization_runs_without_blocking_qt(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE")
    started = Event()
    release = Event()
    real_prepare = services.project_context_service.prepare_initial

    def delayed_prepare():
        started.set()
        release.wait(2)
        return real_prepare()

    monkeypatch.setattr(
        services.project_context_service,
        "prepare_initial",
        delayed_prepare,
    )
    event_loop_progress = []
    window = build_product_window(services)
    qtbot.addWidget(window)
    QTimer.singleShot(0, lambda: event_loop_progress.append(True))
    qtbot.waitUntil(started.is_set, timeout=2000)
    qtbot.waitUntil(lambda: bool(event_loop_progress), timeout=2000)

    assert window.context_task_controller.busy
    assert "Loading" in window.shell_status_label.text()
    release.set()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy, timeout=3000)
    assert window.current_context.project_name == "SAMPLE"
