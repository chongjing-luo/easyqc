"""Full-window first-import and actual file-chooser regressions."""

import pandas as pd
import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog, QFileSystemModel, QLineEdit, QMessageBox

from core.app_services import build_app_services
from gui_qt.application import build_product_window
from gui_qt.i18n import get_or_create_language_controller
from gui_qt.qc_list_import_page import QtQcListImportPage


@pytest.mark.parametrize("source_mode", ["folder", "file", "text"])
def test_first_import_generates_identity_and_commits_from_full_window(
    qtbot, tmp_path, monkeypatch, source_mode
):
    services = build_app_services(tmp_path / "projects.json")
    configuration = services.configuration_service
    configuration.create_project("EMPTY", tmp_path)
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy)
    assert window.current_context.has_project, window.shell_error_label.text()
    window.navigation.setCurrentRow(window.qc_list_import_page_index)
    page = window.config_workspace.subjects_tab
    if source_mode == "folder":
        source = tmp_path / "scans"
        for identity in ("0001", "0002"):
            (source / identity / "rest").mkdir(parents=True)
        qtbot.mouseClick(page.folder_mode_button, Qt.LeftButton)
        qtbot.mouseClick(page.folder_match_button, Qt.LeftButton)
        page.source_path_edit.setText(str(source))
        page.single_column_name.setText("rest")
        page.parent_column_name_edit.setText("relative_parent")
        page.folder_pattern_edit.setText("rest")
        page.folder_scope_combo.setCurrentIndex(page.folder_scope_combo.findData("exact"))
        page.folder_exact_depth_spin.setValue(2)
        expression = "[relative_parent]"
        expected = ["0001", "0002"]
    elif source_mode == "file":
        source = tmp_path / "scans.csv"
        pd.DataFrame({"raw_id": ["S001", "S002"], "image": ["a", "b"]}).to_csv(source, index=False)
        qtbot.mouseClick(page.file_mode_button, Qt.LeftButton)
        page.source_path_edit.setText(str(source))
        expression = "[raw_id]"
        expected = ["S001", "S002"]
    else:
        qtbot.mouseClick(page.text_mode_button, Qt.LeftButton)
        page.single_column_name.setText("raw_id")
        page.direct_text_edit.setPlainText("0001\n0002")
        expression = "[raw_id]"
        expected = ["0001", "0002"]
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not page.task_controller.busy)
    assert len(page.draft) == 2, page.error_text
    assert "easyqcid" not in page.draft.columns
    assert page.derive_button.isEnabled()
    qtbot.mouseClick(page.derive_button, Qt.LeftButton)
    dialog = page.derived_column_dialog
    assert dialog is not None
    dialog.name_edit.setText("easyqcid")
    dialog.editor.set_formula(expression)
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: "easyqcid" in page.draft.columns)
    qtbot.waitUntil(lambda: not dialog.task_controller.busy and not dialog.isVisible())
    assert page.apply_button.isEnabled()
    assert page.draft["easyqcid"].tolist() == expected
    assert configuration.subjects().empty
    monkeypatch.setattr(QMessageBox, "question", lambda *_args, **_kwargs: QMessageBox.Yes)
    qtbot.mouseClick(page.apply_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not page.task_controller.busy and not window.context_task_controller.busy)
    assert not page.error_text
    assert configuration.subjects()["easyqcid"].tolist() == expected
    assert window.current_context.subjects["easyqcid"].tolist() == expected
    assert not window.shell_error_label.text()


@pytest.mark.parametrize("language_code", ["zh_CN", "en"])
def test_file_browse_shows_files_after_folder_mode_and_has_all_files_filter(
    qtbot, tmp_path, monkeypatch, qapp, language_code
):
    monkeypatch.chdir(tmp_path)
    services = build_app_services(tmp_path / "projects.json")
    services.configuration_service.create_project("SAMPLE", tmp_path)
    page = QtQcListImportPage(services.configuration_service)
    qtbot.addWidget(page)
    page.show()
    language = get_or_create_language_controller()
    language.register_root(page)
    language.set_language(language_code)
    source = tmp_path / "inputs"
    source.mkdir()
    (source / "list.csv").write_text("easyqcid\nS001\n", encoding="utf-8")
    (source / "list.CSV").write_text("easyqcid\nS002\n", encoding="utf-8")
    (source / "unsupported.data").write_text("not a supported list", encoding="utf-8")
    failures = []
    observed_modes = []
    previous = qapp.testAttribute(Qt.AA_DontUseNativeDialogs)
    qapp.setAttribute(Qt.AA_DontUseNativeDialogs, True)

    def inspect_picker(mode, accept):
        picker = QApplication.activeModalWidget()
        try:
            assert isinstance(picker, QFileDialog)
            observed_modes.append(picker.fileMode())
            if mode == "folder":
                assert picker.fileMode() == QFileDialog.Directory
                return
            assert picker.fileMode() == QFileDialog.ExistingFile
            picker.setDirectory(str(source))
            model = picker.findChild(QFileSystemModel)

            def names():
                root = model.index(str(source))
                return {model.index(row, 0, root).data() for row in range(model.rowCount(root))}

            qtbot.waitUntil(lambda: "list.csv" in names())
            all_filters = [item for item in picker.nameFilters() if item.endswith("(*)")]
            assert all_filters, picker.nameFilters()
            assert all_filters == ["All files (*)" if language_code == "en" else "所有文件 (*)"]
            assert picker.selectedNameFilter() == all_filters[0]
            picker.selectNameFilter(all_filters[0])
            qtbot.waitUntil(lambda: "unsupported.data" in names() and "list.CSV" in names())
            if accept:
                picker.findChild(QLineEdit, "fileNameEdit").setText("list.CSV")
                picker.accept()
                assert picker.result() == QFileDialog.Accepted, picker.selectedFiles()
        except Exception as exc:
            failures.append(exc)
        finally:
            if isinstance(picker, QFileDialog) and picker.isVisible():
                picker.reject()

    try:
        page.source_path_edit.setText("unchanged-on-cancel")
        for mode, accept in (("folder", False), ("file", False), ("folder", False), ("file", True)):
            qtbot.mouseClick(getattr(page, f"{mode}_mode_button"), Qt.LeftButton)
            QTimer.singleShot(0, lambda mode=mode, accept=accept: inspect_picker(mode, accept))
            qtbot.mouseClick(page.browse_button, Qt.LeftButton)
            if failures:
                raise failures[0]
            if not accept:
                assert page.source_path_edit.text() == "unchanged-on-cancel"
        assert len(observed_modes) == 4
        assert page.source_path_edit.text() == str(source / "list.CSV")
        qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
        qtbot.waitUntil(lambda: not page.task_controller.busy)
        assert page.draft["easyqcid"].tolist() == ["S002"]
        before = page.draft
        page.source_path_edit.setText(str(source / "unsupported.data"))
        qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
        qtbot.waitUntil(lambda: not page.task_controller.busy)
        assert page.error_text
        pd.testing.assert_frame_equal(page.draft, before)
    finally:
        qapp.setAttribute(Qt.AA_DontUseNativeDialogs, previous)


@pytest.mark.parametrize("initial_language", ["zh_CN", "en"])
@pytest.mark.parametrize("failure_method", ["prepare_initial", "activate"])
def test_import_page_keeps_project_load_error_visible_until_recovery(
    qtbot, tmp_path, monkeypatch, initial_language, failure_method
):
    get_or_create_language_controller().set_language(initial_language)
    services = build_app_services(tmp_path / "projects.json")
    services.configuration_service.create_project("EMPTY", tmp_path)
    original_method = getattr(services.project_context_service, failure_method)

    def fail_to_load(*_args):
        raise ValueError("Synthetic project read failure")

    monkeypatch.setattr(services.project_context_service, failure_method, fail_to_load)
    window = build_product_window(services)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy)
    page = window.config_workspace.subjects_tab
    initial_prefix = "Project loading failed." if initial_language == "en" else "项目加载失败"
    assert page.context_error_label.text().startswith(initial_prefix)
    window.navigation.setCurrentRow(window.qc_list_import_page_index)
    assert page.context_error_label.isVisible()
    assert "Synthetic project read failure" in page.context_error_label.text()
    qtbot.mouseClick(page.text_mode_button, Qt.LeftButton)
    page.single_column_name.setText("raw_id")
    page.direct_text_edit.setPlainText("S001 S002")
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: not page.task_controller.busy)
    assert len(page.draft) == 2
    assert not page.derive_button.isEnabled()
    assert page.context_error_label.isVisible()
    assert "Synthetic project read failure" in page.context_error_label.text()
    window.language.set_language("en")
    assert page.context_error_label.text().startswith("Project loading failed.")
    assert "Synthetic project read failure" in page.context_error_label.text()
    window.language.set_language("zh_CN")
    assert page.context_error_label.text().startswith("项目加载失败")
    monkeypatch.setattr(services.project_context_service, failure_method, original_method)
    assert window.refresh_context()
    qtbot.waitUntil(lambda: not window.context_task_controller.busy)
    assert window.current_context.has_project
    assert not page.context_error_label.isVisible()
