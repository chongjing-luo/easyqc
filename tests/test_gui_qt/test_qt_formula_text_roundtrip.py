"""GUI import previews and committed project tables agree on text (D012)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from core.app_services import build_app_services
from gui_qt.application import build_product_window


def test_import_formula_keeps_leading_zero_after_commit_and_window_reload(
    qtbot, tmp_path, monkeypatch,
):
    services = build_app_services(tmp_path / "projects.json")
    services.configuration_service.create_project("TEXT", tmp_path)
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy)
    window.navigation.setCurrentRow(window.qc_list_import_page_index)
    page = window.config_workspace.subjects_tab
    qtbot.mouseClick(page.text_mode_button, Qt.LeftButton)
    page.single_column_name.setText("source")
    page.direct_text_edit.setPlainText("scan02\nscan01")
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not page.task_controller.busy)

    for name, expression in (("easyqcid", "[source]"), ("code", "RIGHT([source], 2)")):
        qtbot.mouseClick(page.derive_button, Qt.LeftButton)
        dialog = page.derived_column_dialog
        assert dialog is not None
        dialog.name_edit.setText(name)
        dialog.editor.set_formula(expression)
        qtbot.mouseClick(dialog.preview_button, Qt.LeftButton)
        if name == "code":
            assert dialog.preview_table.item(0, 2).text() == "02"
            assert dialog.preview_table.item(1, 2).text() == "01"
        qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
        qtbot.waitUntil(lambda: name in page.draft.columns)
        qtbot.waitUntil(lambda: not dialog.task_controller.busy and not dialog.isVisible())

    assert page.draft["code"].tolist() == ["02", "01"]
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
    qtbot.mouseClick(page.apply_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not page.task_controller.busy and not window.context_task_controller.busy)
    assert not page.error_text
    assert window.current_context.subjects["code"].tolist() == ["02", "01"]
    window.close()

    reopened_services = build_app_services(tmp_path / "projects.json")
    reopened = build_product_window(reopened_services)
    qtbot.addWidget(reopened)
    reopened.show()
    qtbot.waitUntil(lambda: not reopened.context_task_controller.busy)
    assert not reopened.shell_error_label.text()
    assert reopened.current_context.subjects["code"].tolist() == ["02", "01"]
