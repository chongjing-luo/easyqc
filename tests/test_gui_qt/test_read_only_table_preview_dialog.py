from __future__ import annotations

from threading import Event

import pandas as pd

from core.table_view_service import TableViewService
from gui_qt.i18n import LanguageController
from gui_qt.read_only_table_preview_dialog import ReadOnlyTablePreviewDialog
from gui_qt.table_workspace import QtTableWorkspace
from models.table_view_state import (
    FilterCondition,
    FilterExpression,
    FilterGroup,
)


def _expression() -> FilterExpression:
    return FilterExpression(
        groups=(
            FilterGroup(
                "group-1",
                "all",
                (
                    FilterCondition(
                        "site",
                        "==",
                        "A",
                        "condition-1",
                    ),
                ),
            ),
        ),
    )


def _prepared_service() -> TableViewService:
    return TableViewService(
        pd.DataFrame(
            {
                "easyqcid": ["SUB001", "SUB002", "SUB003"],
                "site": ["A", "B", "A"],
            }
        )
    )


def test_prepared_workspace_and_dialog_reuse_exact_service_and_state(qtbot) -> None:
    service = _prepared_service()
    state = service.default_state(page_size=25).with_filter(_expression())

    workspace = QtTableWorkspace.from_prepared_service(service, state)
    qtbot.addWidget(workspace)
    assert workspace.service is service
    assert workspace.applied_state.effective_filter == _expression()
    assert workspace.result.matched_total == 2

    dialog = ReadOnlyTablePreviewDialog(
        service,
        state,
        "筛选质控名单：Module_User_Text",
    )
    qtbot.addWidget(dialog)
    assert dialog.table_workspace.service is service
    assert dialog.table_workspace.applied_state.effective_filter == _expression()
    assert dialog.table_workspace.derive_action is None
    assert dialog.table_workspace.delete_rows_action is None
    assert dialog.table_workspace.delete_columns_action is None
    assert dialog.table_workspace.on_open_qc is None
    assert dialog.table_workspace.row_context_provider is None
    assert dialog.table_workspace.on_open_qc_module is None
    assert dialog.table_workspace.on_open_qc_record is None


def test_preview_close_cancels_table_workers_and_export(qtbot, monkeypatch) -> None:
    service = _prepared_service()
    dialog = ReadOnlyTablePreviewDialog(
        service,
        service.default_state(page_size=25),
        "质控总名单：example",
    )
    qtbot.addWidget(dialog)
    workspace = dialog.table_workspace
    cancelled: list[str] = []
    monkeypatch.setattr(
        workspace.task_controller,
        "cancel",
        lambda: cancelled.append("table"),
    )
    monkeypatch.setattr(
        workspace.mutation_task_controller,
        "cancel",
        lambda: cancelled.append("mutation"),
    )
    monkeypatch.setattr(
        workspace.export_task_controller,
        "cancel",
        lambda: cancelled.append("export"),
    )
    export_cancel = Event()
    workspace._export_cancel_event = export_cancel

    dialog.show()
    assert dialog.close()

    assert cancelled == ["table", "mutation", "export"]
    assert export_cancel.is_set()


def test_preview_title_switches_language_without_translating_module_name(qtbot) -> None:
    language = LanguageController(language="zh_CN")
    service = _prepared_service()
    dialog = ReadOnlyTablePreviewDialog(
        service,
        service.default_state(page_size=25),
        "筛选质控名单：Module_User_Text",
        language=language,
    )
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "筛选质控名单：Module_User_Text"
    language.set_language("en")
    assert dialog.windowTitle() == "Filtered QC list — Module_User_Text"
    language.set_language("zh_CN")
    assert dialog.windowTitle() == "筛选质控名单：Module_User_Text"
