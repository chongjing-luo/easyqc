from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QSettings

from gui_qt.i18n import LanguageController
from gui_qt.qc_row_context_menu import QcRowContextMenu
from gui_qt.qc_workspace import QtQcWorkspace
from gui_qt.table_workspace import QtTableWorkspace
from models.qc_row_context import (
    QcModuleMenuEntry,
    QcRecordMenuEntry,
    QcRowContext,
)

from tests.test_gui_qt.test_qt_qc_workspace import _workflow
from tests.test_gui_qt.test_qt_table_workspace import _source


def _context(identity: str) -> QcRowContext:
    return QcRowContext(
        easyqcid=identity,
        modules=(
            QcModuleMenuEntry(
                module_name="AnatQC",
                label="Anatomical quality",
                enabled=True,
            ),
            QcModuleMenuEntry(
                module_name="FuncQC",
                label="Functional quality",
                enabled=False,
                disabled_reason="该条目不在此模块的独立质控名单中",
            ),
        ),
        records=(
            QcRecordMenuEntry(
                easyqcid=identity,
                module_name="AnatQC",
                module_label="Saved anatomical quality",
                rater="rater1",
                recorded_at=datetime(2026, 7, 25, 1, 2, 3),
            ),
        ),
    )


def test_qc_row_context_detaches_mutable_input_sequences() -> None:
    modules = [
        QcModuleMenuEntry(
            module_name="AnatQC",
            label="Anatomical quality",
            enabled=True,
        )
    ]
    records = [
        QcRecordMenuEntry(
            easyqcid="SUB001",
            module_name="AnatQC",
            module_label="Anatomical quality",
            rater="rater1",
        )
    ]

    context = QcRowContext(
        easyqcid="SUB001",
        modules=modules,
        records=records,
    )
    modules.clear()
    records.clear()

    assert isinstance(context.modules, tuple)
    assert isinstance(context.records, tuple)
    assert len(context.modules) == 1
    assert len(context.records) == 1


def test_shared_qc_row_menu_renders_disabled_modules_and_typed_record_actions(
    qtbot,
) -> None:
    context = _context("SUB001")
    opened_modules = []
    opened_records = []
    menu = QcRowContextMenu(
        context,
        on_module=opened_modules.append,
        on_record=opened_records.append,
    )
    qtbot.addWidget(menu)

    assert menu.modules_menu.title() == "质控模块"
    assert menu.records_menu.title() == "已有质控记录"
    assert menu.module_actions["AnatQC"].isEnabled()
    assert not menu.module_actions["FuncQC"].isEnabled()
    assert (
        menu.module_actions["FuncQC"].toolTip()
        == "该条目不在此模块的独立质控名单中"
    )
    assert menu.record_actions[("SUB001", "AnatQC", "rater1")].text() == (
        "Saved anatomical quality · rater1"
    )

    menu.module_actions["AnatQC"].trigger()
    menu.record_actions[("SUB001", "AnatQC", "rater1")].trigger()

    assert opened_modules == [context.modules[0]]
    assert opened_records == [context.records[0]]


def test_shared_qc_row_menu_has_explanatory_disabled_empty_actions(qtbot) -> None:
    menu = QcRowContextMenu(
        QcRowContext(easyqcid="SUB001", modules=(), records=()),
        on_module=lambda _entry: None,
        on_record=lambda _entry: None,
    )
    qtbot.addWidget(menu)

    assert menu.modules_menu.actions()[0].text() == "没有可用的质控模块"
    assert not menu.modules_menu.actions()[0].isEnabled()
    assert menu.records_menu.actions()[0].text() == "没有已有质控记录"
    assert not menu.records_menu.actions()[0].isEnabled()


def test_pinned_and_unpinned_tables_resolve_the_same_right_clicked_identity(
    qtbot,
) -> None:
    identities = []
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.show()
    workspace.set_row_context_actions(
        lambda identity: identities.append(identity) or _context(identity),
        lambda _identity, _entry: None,
        lambda _entry: None,
    )

    main_index = workspace.table_model.index(1, 1)
    pinned_index = workspace.table_model.index(1, 0)
    workspace._open_row_context_menu(
        workspace.table_view,
        workspace.table_view.visualRect(main_index).center(),
    )
    assert workspace.active_row_context_menu is not None
    workspace.active_row_context_menu.close()
    workspace._open_row_context_menu(
        workspace.pinned_view,
        workspace.pinned_view.visualRect(pinned_index).center(),
    )

    assert identities == ["SUB002", "SUB002"]
    assert workspace.table_view.selectionModel().selectedRows()[0].row() == 1


def test_qc_queue_uses_the_shared_context_menu_for_its_clicked_identity(
    qtbot,
    tmp_path,
) -> None:
    workspace = QtQcWorkspace(_workflow(tmp_path))
    qtbot.addWidget(workspace)
    workspace.show()
    identities = []
    workspace.set_row_context_actions(
        lambda identity: identities.append(identity) or _context(identity),
        lambda _identity, _entry: None,
        lambda _entry: None,
    )
    index = workspace.queue_model.index(1, 1)

    workspace._open_queue_context_menu(
        workspace.queue_table.visualRect(index).center(),
    )

    assert identities == ["SUB002"]
    assert workspace.active_row_context_menu is not None
    assert workspace.queue_table.currentIndex().row() == 1


def test_qc_row_menu_switches_shell_text_to_english_without_translating_data(
    qtbot,
    tmp_path,
) -> None:
    controller = LanguageController(
        settings=QSettings(str(tmp_path / "language.ini"), QSettings.IniFormat)
    )
    menu = QcRowContextMenu(
        _context("SUB001"),
        on_module=lambda _entry: None,
        on_record=lambda _entry: None,
    )
    qtbot.addWidget(menu)
    controller.register_root(menu)
    menu.show()

    controller.set_language("en")

    assert [action.text() for action in menu.actions()] == [
        "QC modules",
        "Existing QC records",
    ]
    assert menu.module_actions["AnatQC"].text() == "Anatomical quality"
    assert menu.module_actions["FuncQC"].toolTip() == (
        "This item is not in the module's independent QC list"
    )
