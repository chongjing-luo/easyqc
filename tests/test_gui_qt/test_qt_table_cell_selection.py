"""RI-13a: all three table surfaces share cell copy without changing QC identity."""

import pytest
from PySide6.QtCore import QItemSelectionModel, QSettings, Qt
from PySide6.QtWidgets import QAbstractItemView, QApplication

from gui_qt.qc_workspace import QtQcWorkspace
from gui_qt.i18n import LanguageController
from gui_qt.table_workspace import QtTableWorkspace
from gui_qt.table_clipboard import selected_cells_tsv
from tests.test_gui_qt.test_qc_row_context_menu import _context
from tests.test_gui_qt.test_qt_qc_list_import_page import _page
from tests.test_gui_qt.test_qt_qc_workspace import _workflow
from tests.test_gui_qt.test_qt_table_workspace import _source


@pytest.mark.parametrize("kind", ["table", "queue"])
def test_multicell_menu_never_resolves_qc_context(qtbot, tmp_path, kind):
    workspace = QtTableWorkspace(_source()) if kind == "table" else QtQcWorkspace(_workflow(tmp_path))
    qtbot.addWidget(workspace)
    workspace.resize(1000, 700)
    workspace.show()
    view = workspace.table_view if kind == "table" else workspace.queue_table
    model = view.model()
    assert view.selectionBehavior() == QAbstractItemView.SelectItems
    assert view.selectionMode() == QAbstractItemView.ExtendedSelection
    resolved = []
    workspace.set_row_context_actions(
        lambda identity: resolved.append(identity) or _context(identity),
        lambda *_args: None,
        lambda *_args: None,
    )
    view.selectionModel().clearSelection()
    for row in (0, 1):
        view.selectionModel().select(model.index(row, 1), QItemSelectionModel.Select)
    point = view.visualRect(model.index(0, 1)).center()
    if kind == "table":
        workspace._open_row_context_menu(view, point)
    else:
        workspace.workflow.set_notes("unsaved draft")
        active = workspace.workflow.current_easyqcid
        workspace._open_queue_context_menu(point)
        assert workspace.workflow.current_easyqcid == active
        assert workspace.workflow.dirty
    assert resolved == []
    assert len(view.selectionModel().selectedIndexes()) == 2
    assert [action.text() for action in workspace.active_row_context_menu.actions()] == ["复制"]


def test_table_restore_selects_one_cell_and_copy_shortcut(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.show()
    assert workspace.select_source_position(1)
    assert len(workspace.table_view.selectionModel().selectedIndexes()) == 1
    view = workspace.pinned_view
    view.setFocus()
    clipboard = QApplication.clipboard()
    previous = clipboard.text()
    try:
        qtbot.keyClick(view, Qt.Key_C, Qt.ControlModifier)
        assert clipboard.text() == "SUB002"
    finally:
        clipboard.setText(previous)


def test_import_preview_is_cell_copy_only(qtbot, tmp_path):
    page, _configuration, _current = _page(qtbot, tmp_path)
    assert page.preview_table.selectionBehavior() == QAbstractItemView.SelectItems
    assert page.preview_table.selectionMode() == QAbstractItemView.ExtendedSelection
    menu = page.create_preview_context_menu(after_position=None)
    assert [action.text() for action in menu.actions()] == ["复制"]


def test_single_cell_without_provider_still_offers_copy(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.show()
    index = workspace.table_model.index(0, 1)
    workspace._open_row_context_menu(workspace.table_view, workspace.table_view.visualRect(index).center())
    assert [action.text() for action in workspace.active_row_context_menu.actions()] == ["复制"]


def test_mouse_gestures_select_cells_and_right_click_preserves_or_replaces(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.resize(1000, 700)
    workspace.show()
    view = workspace.table_view
    model = workspace.table_model
    first = view.visualRect(model.index(0, 1)).center()
    second = view.visualRect(model.index(1, 2)).center()
    qtbot.mouseClick(view.viewport(), Qt.LeftButton, pos=first)
    qtbot.mouseClick(view.viewport(), Qt.LeftButton, Qt.ShiftModifier, pos=second)
    assert len(view.selectionModel().selectedIndexes()) == 4
    qtbot.mouseClick(view.viewport(), Qt.RightButton, pos=first)
    workspace._open_row_context_menu(view, first)
    assert len(view.selectionModel().selectedIndexes()) == 4
    assert len(workspace.active_row_context_menu.actions()) == 1
    workspace.active_row_context_menu.close()
    outside = view.visualRect(model.index(3, 1)).center()
    qtbot.mouseClick(view.viewport(), Qt.RightButton, pos=outside)
    workspace._open_row_context_menu(view, outside)
    assert view.selectionModel().selectedIndexes() == [model.index(3, 1)]


def test_workspace_dispatches_execution_modes_with_clicked_identity(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.show()
    called = []
    workspace.set_row_context_actions(
        _context, lambda *args: called.append(("open", args)), lambda *_args: None,
        on_execute=lambda *args: called.append(("execute", args)),
        on_open_execute=lambda *args: called.append(("both", args)),
    )
    index = workspace.table_model.index(1, 1)
    workspace._open_row_context_menu(workspace.table_view, workspace.table_view.visualRect(index).center())
    menu = workspace.active_row_context_menu
    menu.execute_actions["AnatQC"].trigger()
    menu.open_execute_actions["AnatQC"].trigger()
    menu.module_actions["AnatQC"].trigger()
    assert [mode for mode, _args in called] == ["execute", "both", "open"]
    assert all(args[0] == "SUB002" and args[1].module_name == "AnatQC" for _mode, args in called)


def test_import_blank_row_button_preserves_old_menu_route(qtbot, tmp_path):
    page, _configuration, current = _page(qtbot, tmp_path)
    page._replace_draft(current.copy(), reset_state=True)
    page.preview_table.setCurrentIndex(page.preview_model.index(0, 1))
    qtbot.mouseClick(page.add_row_button, Qt.LeftButton)
    assert len(page.draft) == 3
    assert page.draft.iloc[2]["easyqcid"] == "SUB002"
    assert len(page.preview_table.selectionModel().selectedIndexes()) == 1


def test_copy_only_menu_uses_the_workspaces_language_controller(qtbot, tmp_path):
    language = LanguageController(settings=QSettings(str(tmp_path / "language.ini"), QSettings.IniFormat))
    language.set_language("en")
    workspace = QtTableWorkspace(_source(), language=language)
    qtbot.addWidget(workspace)
    workspace.show()
    index = workspace.table_model.index(0, 1)
    workspace._open_row_context_menu(workspace.table_view, workspace.table_view.visualRect(index).center())
    assert workspace.active_row_context_menu.actions()[0].text() == "Copy"
    language.set_language("zh_CN")
    assert workspace.active_row_context_menu.actions()[0].text() == "复制"


def test_ctrl_selection_across_frozen_and_main_views_copies_one_rectangle(qtbot):
    workspace = QtTableWorkspace(_source())
    qtbot.addWidget(workspace)
    workspace.resize(1000, 700)
    workspace.show()
    frozen, main = workspace.pinned_view, workspace.table_view
    model = workspace.table_model
    qtbot.mouseClick(frozen.viewport(), Qt.LeftButton, pos=frozen.visualRect(model.index(0, 0)).center())
    point = main.visualRect(model.index(1, 1)).center()
    qtbot.mouseClick(main.viewport(), Qt.LeftButton, Qt.ControlModifier, pos=point)
    assert len(main.selectionModel().selectedIndexes()) == 2
    workspace._open_row_context_menu(main, point)
    assert len(workspace.active_row_context_menu.actions()) == 1
    assert selected_cells_tsv((frozen, main)) == "SUB001\t\n\tB"
