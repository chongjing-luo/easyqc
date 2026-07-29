from __future__ import annotations

from dataclasses import replace
from threading import Event

import pandas as pd
import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QLabel,
    QLineEdit,
    QMessageBox,
)

from core.configuration_service import ConfigurationService
from core.event_bus import EventType
from core.project_service import ProjectService
from core.table_service import TableService
from gui_qt.i18n import get_or_create_language_controller
from models.table_view_state import (
    FilterCondition,
    FilterExpression,
    FilterGroup,
    SortRule,
)


def _page(qtbot, tmp_path):
    from gui_qt.qc_list_import_page import QtQcListImportPage

    configuration = ConfigurationService(
        ProjectService(tmp_path / "projects.json"),
        TableService(),
    )
    configuration.create_project("SAMPLE", tmp_path)
    current = pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB002"],
            "site": ["A", "B"],
        }
    )
    configuration.replace_subjects(current)
    page = QtQcListImportPage(configuration)
    qtbot.addWidget(page)
    page.refresh_current(current)
    page.resize(900, 640)
    page.show()
    return page, configuration, current


def _wait(page, qtbot):
    qtbot.waitUntil(lambda: not page.task_controller.busy, timeout=3000)


def _site_filter(*values: str) -> FilterExpression:
    return FilterExpression(
        groups=(
            FilterGroup(
                group_id="delete-sites",
                join="any",
                conditions=tuple(
                    FilterCondition(
                        "site",
                        "==",
                        value,
                        f"delete-site-{index}",
                    )
                    for index, value in enumerate(values)
                ),
            ),
        )
    )


def _accept_questions(monkeypatch) -> None:
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.Yes,
    )


@pytest.mark.parametrize("source_mode", ["folder", "file", "text"])
def test_each_source_builds_a_preview_draft_without_persistence(
    qtbot,
    tmp_path,
    source_mode,
) -> None:
    page, configuration, current = _page(qtbot, tmp_path)
    table_path = configuration.current_project.table_dir / "easyqc_all.csv"
    before_bytes = table_path.read_bytes()

    if source_mode == "folder":
        source = tmp_path / "folders"
        (source / "SUB004").mkdir(parents=True)
        (source / "SUB003").mkdir()
        qtbot.mouseClick(page.folder_mode_button, Qt.LeftButton)
        page.source_path_edit.setText(str(source))
        page.single_column_name.setText("easyqcid")
        expected = ["SUB003", "SUB004"]
    elif source_mode == "file":
        source = tmp_path / "list.csv"
        pd.DataFrame(
            {"easyqcid": ["SUB003", "SUB004"], "site": ["C", "D"]}
        ).to_csv(source, index=False)
        qtbot.mouseClick(page.file_mode_button, Qt.LeftButton)
        page.source_path_edit.setText(str(source))
        expected = ["SUB003", "SUB004"]
    else:
        qtbot.mouseClick(page.text_mode_button, Qt.LeftButton)
        page.direct_text_edit.setPlainText("SUB003, SUB004")
        page.single_column_name.setText("easyqcid")
        expected = ["SUB003", "SUB004"]

    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)

    assert page.draft["easyqcid"].tolist() == expected
    assert page.preview_model.rowCount() == 2
    pd.testing.assert_frame_equal(configuration.subjects(), current)
    assert table_path.read_bytes() == before_bytes


def test_failed_parse_preserves_last_draft_and_active_list(qtbot, tmp_path) -> None:
    page, configuration, current = _page(qtbot, tmp_path)
    table_path = configuration.current_project.table_dir / "easyqc_all.csv"
    qtbot.mouseClick(page.text_mode_button, Qt.LeftButton)
    page.direct_text_edit.setPlainText("SUB003 SUB004")
    page.single_column_name.setText("easyqcid")
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    before_draft = page.draft
    before_bytes = table_path.read_bytes()

    qtbot.mouseClick(page.file_mode_button, Qt.LeftButton)
    page.source_path_edit.setText(str(tmp_path / "missing.csv"))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)

    pd.testing.assert_frame_equal(page.draft, before_draft)
    pd.testing.assert_frame_equal(configuration.subjects(), current)
    assert table_path.read_bytes() == before_bytes
    assert page.error_text


def test_preview_search_filter_sort_and_columns_do_not_change_draft(
    qtbot,
    tmp_path,
) -> None:
    page, configuration, _current = _page(qtbot, tmp_path)
    source = tmp_path / "preview.csv"
    pd.DataFrame(
        {
            "easyqcid": ["SUB003", "SUB004", "SUB005"],
            "site": ["A", "B", "A"],
            "scanner_model": ["Prisma", "Skyra", "Prisma"],
        }
    ).to_csv(source, index=False)
    page.source_path_edit.setText(str(source))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    original = page.draft

    for button, attribute in (
        (page.filter_button, "filter_dialog"),
        (page.sort_button, "sort_dialog"),
        (page.columns_button, "columns_dialog"),
    ):
        qtbot.mouseClick(button, Qt.LeftButton)
        dialog = getattr(page, attribute)
        assert dialog is not None and dialog.isVisible()
        dialog.reject()
        qtbot.waitUntil(lambda name=attribute: getattr(page, name) is None)

    page.preview_search.setText("Prisma")
    page.preview_search.returnPressed.emit()
    _wait(page, qtbot)
    assert page.preview_model.rowCount() == 2
    page.preview_search.clear()
    page.preview_search.returnPressed.emit()
    _wait(page, qtbot)

    assert page.apply_preview_sort((SortRule("site", ascending=False),))
    assert page.preview_model.snapshot()["easyqcid"].tolist() == [
        "SUB004",
        "SUB003",
        "SUB005",
    ]

    expression = FilterExpression(
        groups=(
            FilterGroup(
                group_id="site-a",
                join="all",
                conditions=(FilterCondition("site", "==", "A", "site-a"),),
            ),
        )
    )
    assert page.apply_preview_filter(expression)
    assert page.preview_model.rowCount() == 2
    columns = replace(
        page.preview_columns,
        hidden=("scanner_model",),
    )
    assert page.apply_preview_columns(columns)
    assert tuple(page.preview_model.snapshot().columns) == ("easyqcid", "site")
    pd.testing.assert_frame_equal(page.draft, original)
    pd.testing.assert_frame_equal(configuration.subjects(), _current)
    assert page.filter_button.text().startswith("筛选")
    assert page.sort_button.text().startswith("排序")
    assert page.columns_button.text().startswith("列显示")


def test_derived_column_updates_only_latest_import_draft(
    qtbot,
    tmp_path,
) -> None:
    page, configuration, current = _page(qtbot, tmp_path)
    source = tmp_path / "draft-columns.csv"
    pd.DataFrame(
        {
            "easyqcid": ["NEW001", "NEW002", "NEW003"],
            "batch": ["X", "Y", "Z"],
        }
    ).to_csv(source, index=False)
    page.source_path_edit.setText(str(source))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    table_path = configuration.current_project.table_dir / "easyqc_all.csv"
    before_bytes = table_path.read_bytes()
    events = []
    configuration.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        lambda event: events.append(event),
    )

    action_roots = [
        button.text().split(" (", 1)[0]
        for button in (
            page.filter_button,
            page.sort_button,
            page.columns_button,
            page.derive_button,
        )
    ]
    assert action_roots == ["筛选", "排序", "列显示", "新增列"]

    qtbot.mouseClick(page.derive_button, Qt.LeftButton)
    dialog = page.derived_column_dialog
    assert dialog is not None and dialog.isVisible()
    assert [
        dialog.editor.column_combo.itemData(index)
        for index in range(dialog.editor.column_combo.count())
    ] == ["easyqcid", "batch"]

    dialog.name_edit.setText("batch_copy")
    dialog.editor.set_formula("[batch]")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(
        lambda: (
            "batch_copy" in page.draft.columns
            and page.status_label.text()
            == "已生成导入草稿列：batch_copy；尚未写入"
        ),
        timeout=3000,
    )

    assert page.draft["batch_copy"].tolist() == ["X", "Y", "Z"]
    pd.testing.assert_frame_equal(configuration.subjects(), current)
    assert table_path.read_bytes() == before_bytes
    assert events == []


def test_stale_formula_worker_result_cannot_replace_newer_import_draft(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    from core.table_transform import TableTransformEngine

    page, configuration, current = _page(qtbot, tmp_path)
    page._install_draft(
        pd.DataFrame(
            {
                "easyqcid": ["OLD001", "OLD002"],
                "batch": ["A", "B"],
            }
        )
    )
    real_derive = TableTransformEngine.derive_column_from_formula
    started = Event()
    release = Event()

    def delayed_derive(engine, source, request):
        started.set()
        release.wait(2)
        return real_derive(engine, source, request)

    monkeypatch.setattr(
        TableTransformEngine,
        "derive_column_from_formula",
        delayed_derive,
    )

    qtbot.mouseClick(page.derive_button, Qt.LeftButton)
    dialog = page.derived_column_dialog
    assert dialog is not None
    dialog.name_edit.setText("batch_copy")
    dialog.editor.set_formula("[batch]")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(started.is_set, timeout=2000)

    newer = pd.DataFrame(
        {
            "easyqcid": ["NEW001"],
            "batch": ["Z"],
        }
    )
    page._install_draft(newer)
    release.set()
    qtbot.waitUntil(
        lambda: "导入草稿已变化" in page.error_text,
        timeout=3000,
    )

    pd.testing.assert_frame_equal(page.draft, newer)
    pd.testing.assert_frame_equal(configuration.subjects(), current)


def test_import_preview_context_actions_edit_correct_draft_rows(
    qtbot,
    tmp_path,
) -> None:
    page, configuration, current = _page(qtbot, tmp_path)
    source = tmp_path / "draft-rows.csv"
    pd.DataFrame(
        {
            "easyqcid": ["ROW_A", "ROW_B", "ROW_C", "ROW_D"],
            "site": ["A", "B", "C", "D"],
        }
    ).to_csv(source, index=False)
    page.source_path_edit.setText(str(source))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    before_bytes = (
        configuration.current_project.table_dir / "easyqc_all.csv"
    ).read_bytes()

    assert page.preview_table.selectionMode() == QAbstractItemView.SingleSelection
    assert page.preview_table.contextMenuPolicy() == Qt.CustomContextMenu

    page.preview_search.setText("ROW_D")
    page.preview_search.returnPressed.emit()
    _wait(page, qtbot)
    assert page.draft_position_for_preview_row(0) == 3
    page.preview_table.clearSelection()
    assert page.delete_draft_rows_by_filter(_site_filter("D"))
    assert page.draft["easyqcid"].tolist() == ["ROW_A", "ROW_B", "ROW_C"]

    assert page.apply_preview_sort((SortRule("site", ascending=False),))
    assert page.draft_position_for_preview_row(0) == 2
    assert page.draft_position_for_preview_row(2) == 0
    page.preview_table.clearSelection()
    assert page.delete_draft_rows_by_filter(_site_filter("A", "C"))
    assert page.draft["easyqcid"].tolist() == ["ROW_B"]
    assert page.draft.columns.tolist() == ["easyqcid", "site"]

    assert page.insert_blank_draft_row(after_position=0)
    assert page.draft.columns.tolist() == ["easyqcid", "site"]
    assert len(page.draft) == 2
    assert pd.isna(page.draft.iloc[1]["easyqcid"])
    assert page.insert_blank_draft_row(after_position=None)
    assert len(page.draft) == 3
    assert pd.isna(page.draft.iloc[-1]["site"])

    menu = page.create_preview_context_menu(after_position=None)
    assert [action.text() for action in menu.actions()] == [
        "增加空行",
        "按条件删除行…",
    ]
    language = get_or_create_language_controller()
    language.set_language("en")
    english_menu = page.create_preview_context_menu(after_position=None)
    assert [action.text() for action in english_menu.actions()] == [
        "Add blank row",
        "Delete rows by condition…",
    ]
    language.set_language("zh_CN")
    pd.testing.assert_frame_equal(configuration.subjects(), current)
    assert (
        configuration.current_project.table_dir / "easyqc_all.csv"
    ).read_bytes() == before_bytes


def test_import_row_edits_feed_the_next_derived_column(qtbot, tmp_path) -> None:
    page, configuration, current = _page(qtbot, tmp_path)
    page._install_draft(
        pd.DataFrame(
            {
                "easyqcid": ["NEW001", "NEW002", "NEW003"],
                "batch": ["X", "Y", "Z"],
            }
        )
    )
    assert page.delete_draft_rows_by_filter(
        FilterExpression(
            groups=(
                FilterGroup(
                    group_id="delete-y",
                    join="all",
                    conditions=(
                        FilterCondition(
                            "batch",
                            "==",
                            "Y",
                            "delete-y-condition",
                        ),
                    ),
                ),
            )
        )
    )

    qtbot.mouseClick(page.derive_button, Qt.LeftButton)
    dialog = page.derived_column_dialog
    assert dialog is not None
    dialog.name_edit.setText("batch_copy")
    dialog.editor.set_formula("[batch]")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: "batch_copy" in page.draft.columns, timeout=3000)

    assert page.draft["easyqcid"].tolist() == ["NEW001", "NEW003"]
    assert page.draft["batch_copy"].tolist() == ["X", "Z"]
    pd.testing.assert_frame_equal(configuration.subjects(), current)


def test_merge_columns_applies_once_and_emits_list_change(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    _accept_questions(monkeypatch)
    page, configuration, _current = _page(qtbot, tmp_path)
    source = tmp_path / "columns.csv"
    pd.DataFrame(
        {"easyqcid": ["SUB001", "SUB003"], "batch": ["X", "Y"]}
    ).to_csv(source, index=False)
    page.source_path_edit.setText(str(source))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    events = []
    configuration.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        lambda event: events.append(event),
    )

    qtbot.mouseClick(page.apply_button, Qt.LeftButton)
    _wait(page, qtbot)

    merged = configuration.subjects().set_index("easyqcid")
    assert list(merged.index) == ["SUB001", "SUB002", "SUB003"]
    assert merged.loc["SUB001", "batch"] == "X"
    assert len(events) == 1
    assert not page.error_text


def test_append_rows_and_clear_draft_are_explicit(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    _accept_questions(monkeypatch)
    page, configuration, _current = _page(qtbot, tmp_path)
    source = tmp_path / "rows.csv"
    pd.DataFrame({"easyqcid": ["SUB003"], "site": ["C"]}).to_csv(
        source,
        index=False,
    )
    page.source_path_edit.setText(str(source))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    page.write_mode_combo.setCurrentIndex(
        page.write_mode_combo.findData("append")
    )
    assert page.conflict_policy_combo.currentData() == "deduplicate"

    qtbot.mouseClick(page.apply_button, Qt.LeftButton)
    _wait(page, qtbot)
    assert configuration.subjects()["easyqcid"].tolist() == [
        "SUB001",
        "SUB002",
        "SUB003",
    ]
    qtbot.mouseClick(page.clear_button, Qt.LeftButton)
    assert page.draft.empty
    assert configuration.subjects()["easyqcid"].tolist()[-1] == "SUB003"


def test_failed_apply_preserves_disk_active_list_and_draft(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    _accept_questions(monkeypatch)
    page, configuration, current = _page(qtbot, tmp_path)
    source = tmp_path / "conflict.csv"
    pd.DataFrame({"easyqcid": ["SUB001"], "other": ["changed"]}).to_csv(
        source,
        index=False,
    )
    page.source_path_edit.setText(str(source))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    page.write_mode_combo.setCurrentIndex(
        page.write_mode_combo.findData("append")
    )
    draft = page.draft
    table_path = configuration.current_project.table_dir / "easyqc_all.csv"
    before_bytes = table_path.read_bytes()

    qtbot.mouseClick(page.apply_button, Qt.LeftButton)
    _wait(page, qtbot)

    pd.testing.assert_frame_equal(configuration.subjects(), current)
    pd.testing.assert_frame_equal(page.draft, draft)
    assert table_path.read_bytes() == before_bytes
    assert page.error_text


def test_preview_load_is_background_responsive_and_visible_language_is_neutral(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    page, configuration, _current = _page(qtbot, tmp_path)
    source = tmp_path / "slow.csv"
    pd.DataFrame({"easyqcid": ["SUB003"]}).to_csv(source, index=False)
    real_reader = configuration.draft_from_file
    started = Event()
    release = Event()

    def delayed_reader(path, single_column_name=None):
        started.set()
        release.wait(2)
        return real_reader(path, single_column_name)

    monkeypatch.setattr(configuration, "draft_from_file", delayed_reader)
    page.source_path_edit.setText(str(source))
    event_loop_progress = []
    QTimer.singleShot(0, lambda: event_loop_progress.append(True))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    qtbot.waitUntil(started.is_set, timeout=2000)
    qtbot.waitUntil(lambda: bool(event_loop_progress), timeout=2000)
    assert page.task_controller.busy
    assert not page.read_preview_button.isEnabled()

    release.set()
    _wait(page, qtbot)
    assert page.preview_model.rowCount() == 1
    visible_text = " ".join(
        [
            *(widget.text() for widget in page.findChildren(QLabel)),
            *(widget.text() for widget in page.findChildren(QAbstractButton)),
            *(widget.placeholderText() for widget in page.findChildren(QLineEdit)),
            page.accessibleName(),
        ]
    ).casefold()
    assert "受试者" not in visible_text
    assert "被试" not in visible_text
    assert "变量设置" not in visible_text
    assert "subject" not in visible_text


def test_reduced_width_keeps_import_and_apply_actions_reachable(qtbot, tmp_path) -> None:
    page, _configuration, _current = _page(qtbot, tmp_path)

    page.resize(640, 520)
    qtbot.waitUntil(lambda: page.width() == 640)

    assert page.minimumSizeHint().width() <= 640
    assert page.read_preview_button.isVisible()
    assert page.clear_button.isVisible()
    assert page.apply_button.isVisible()
    assert page.preview_table.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded


def test_import_draft_can_generate_missing_easyqcid_then_merge(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    _accept_questions(monkeypatch)
    page, configuration, _current = _page(qtbot, tmp_path)
    page._install_draft(
        pd.DataFrame(
            {
                "raw_id": ["SUB003", "SUB004"],
                "batch": ["C", "D"],
            }
        )
    )

    qtbot.mouseClick(page.derive_button, Qt.LeftButton)
    dialog = page.derived_column_dialog
    assert dialog is not None
    dialog.name_edit.setText("easyqcid")
    dialog.editor.set_formula("TRIM([raw_id])")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: "easyqcid" in page.draft.columns, timeout=3000)

    assert page.draft["easyqcid"].tolist() == ["SUB003", "SUB004"]
    qtbot.mouseClick(page.apply_button, Qt.LeftButton)
    _wait(page, qtbot)
    assert configuration.subjects()["easyqcid"].tolist() == [
        "SUB001",
        "SUB002",
        "SUB003",
        "SUB004",
    ]


def _pattern_tree(tmp_path):
    root = tmp_path / "folder-pattern-ui"
    (root / "siteA" / "sub01").mkdir(parents=True)
    (root / "siteB" / "sub02").mkdir(parents=True)
    (root / "siteA" / "sub01" / "scan_T1.nii.gz").write_text(
        "a",
        encoding="utf-8",
    )
    (root / "siteB" / "sub02" / "scan_T2.nii.gz").write_text(
        "b",
        encoding="utf-8",
    )
    return root


def test_folder_pattern_controls_create_two_or_one_column_draft(
    qtbot,
    tmp_path,
) -> None:
    page, _configuration, _current = _page(qtbot, tmp_path)
    root = _pattern_tree(tmp_path)
    qtbot.mouseClick(page.folder_mode_button, Qt.LeftButton)

    assert page.folder_default_button.isChecked()
    assert not page.folder_match_panel.isVisible()
    qtbot.mouseClick(page.folder_match_button, Qt.LeftButton)
    assert page.folder_match_panel.isVisible()

    page.source_path_edit.setText(str(root))
    page.single_column_name.setText("matched_item")
    page.parent_column_name_edit.setText("relative_parent")
    page.folder_target_combo.setCurrentIndex(
        page.folder_target_combo.findData("file")
    )
    page.folder_match_kind_combo.setCurrentIndex(
        page.folder_match_kind_combo.findData("ends_with")
    )
    page.folder_pattern_edit.setText(".nii.gz")
    page.folder_scope_combo.setCurrentIndex(
        page.folder_scope_combo.findData("exact")
    )
    assert page.folder_exact_depth_spin.isEnabled()
    page.folder_exact_depth_spin.setValue(3)
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)

    assert page.draft.to_dict("records") == [
        {"relative_parent": "siteA/sub01", "matched_item": "scan_T1.nii.gz"},
        {"relative_parent": "siteB/sub02", "matched_item": "scan_T2.nii.gz"},
    ]

    page.include_parent_column_checkbox.setChecked(False)
    assert not page.parent_column_name_edit.isEnabled()
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    assert not page.parent_column_name_edit.isEnabled()
    assert page.draft.to_dict("records") == [
        {"matched_item": "scan_T1.nii.gz"},
        {"matched_item": "scan_T2.nii.gz"},
    ]


def test_folder_pattern_controls_translate_without_losing_request_state(
    qtbot,
    tmp_path,
) -> None:
    page, _configuration, _current = _page(qtbot, tmp_path)
    language = get_or_create_language_controller()
    language.register_root(page)
    qtbot.mouseClick(page.folder_mode_button, Qt.LeftButton)
    qtbot.mouseClick(page.folder_match_button, Qt.LeftButton)
    page.folder_pattern_edit.setText("scan_*")
    page.folder_scope_combo.setCurrentIndex(
        page.folder_scope_combo.findData("all")
    )
    page.include_parent_column_checkbox.setChecked(False)

    language.set_language("en")
    try:
        assert page.folder_match_button.text() == "Pattern matching"
        assert page.folder_target_combo.itemText(0) == "Folder"
        assert page.folder_match_kind_combo.itemText(3) == "Wildcard"
        assert page.folder_scope_combo.currentText() == "All levels"
        assert page.folder_pattern_edit.text() == "scan_*"
        assert not page.include_parent_column_checkbox.isChecked()
        assert not page.parent_column_name_edit.isEnabled()
        presentation = [
            *(widget.text() for widget in page.findChildren(QLabel)),
            *(widget.text() for widget in page.findChildren(QAbstractButton)),
            *(
                combo.itemText(index)
                for combo in page.findChildren(QComboBox)
                for index in range(combo.count())
            ),
            *(
                widget.placeholderText()
                for widget in page.findChildren(QLineEdit)
            ),
        ]
        assert not {
            text
            for text in presentation
            if any("\u3400" <= char <= "\u9fff" for char in text)
        }
    finally:
        language.set_language("zh_CN")


def test_failed_folder_pattern_preserves_previous_import_draft(
    qtbot,
    tmp_path,
) -> None:
    page, _configuration, _current = _page(qtbot, tmp_path)
    root = _pattern_tree(tmp_path)
    previous = pd.DataFrame({"raw_id": ["KEEP001"]})
    page._install_draft(previous)
    qtbot.mouseClick(page.folder_mode_button, Qt.LeftButton)
    qtbot.mouseClick(page.folder_match_button, Qt.LeftButton)
    page.source_path_edit.setText(str(root))
    page.single_column_name.setText("matched_item")
    page.folder_target_combo.setCurrentIndex(
        page.folder_target_combo.findData("file")
    )
    page.folder_match_kind_combo.setCurrentIndex(
        page.folder_match_kind_combo.findData("regex")
    )
    page.folder_pattern_edit.setText("(")

    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)

    pd.testing.assert_frame_equal(page.draft, previous)
    assert "正则" in page.error_text


def test_import_draft_column_deletion_is_discoverable_and_never_writes_project_data(
    qtbot,
    tmp_path,
) -> None:
    page, configuration, current = _page(qtbot, tmp_path)
    page._install_draft(
        pd.DataFrame(
            {
                "easyqcid": ["NEW001", "NEW002"],
                "site": ["A", "B"],
                "path": ["/a/one.nii", "/b/two.nii"],
            }
        )
    )
    table_path = configuration.current_project.table_dir / "easyqc_all.csv"
    table_before = table_path.read_bytes()
    rating_path = (
        configuration.current_project.path
        / "RatingFiles"
        / "AnatQC"
        / "rater1"
        / "rating.json"
    )
    rating_path.parent.mkdir(parents=True)
    rating_path.write_bytes(b'{"easyqcid":"NEW001","score":"Good"}')
    rating_before = rating_path.read_bytes()

    assert page.delete_rows_button.text() == "删除行"
    assert page.delete_column_button.text() == "删除列"
    page.preview_table.clearSelection()
    assert page.delete_draft_columns(("site",))
    assert page.draft.columns.tolist() == ["easyqcid", "path"]

    page.preview_table.clearSelection()
    assert page.delete_draft_columns(("easyqcid",))
    assert page.draft.columns.tolist() == ["path"]
    assert "easyqcid" in page.stats_label.text()
    assert table_path.read_bytes() == table_before
    assert rating_path.read_bytes() == rating_before
    pd.testing.assert_frame_equal(configuration.subjects(), current)


def test_import_delete_controls_follow_busy_state_and_language(
    qtbot,
    tmp_path,
) -> None:
    page, _configuration, _current = _page(qtbot, tmp_path)
    page._install_draft(pd.DataFrame({"easyqcid": ["NEW001"], "site": ["A"]}))
    language = get_or_create_language_controller()
    try:
        page.preview_table.clearSelection()
        assert page.delete_rows_button.isEnabled()
        assert page.delete_column_button.isEnabled()

        page._set_busy(True)
        assert not page.delete_rows_button.isEnabled()
        assert not page.delete_column_button.isEnabled()
        page._set_busy(False)

        language.set_language("en")
        language.localize_widget_tree(page)
        assert page.delete_rows_button.text() == "Delete rows"
        assert page.delete_column_button.text() == "Delete columns"
    finally:
        language.set_language("zh_CN")


def test_import_row_deletion_confirmation_names_scope_and_retained_ratings(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    page, _configuration, _current = _page(qtbot, tmp_path)
    page._install_draft(
        pd.DataFrame({"easyqcid": ["NEW001", "NEW002"], "site": ["A", "B"]})
    )
    page.preview_table.clearSelection()
    prompts = []

    def cancel(_parent, title, text, _buttons, _default):
        prompts.append((title, text))
        return QMessageBox.No

    monkeypatch.setattr(QMessageBox, "question", cancel)

    dialog = page.open_delete_rows_dialog()
    assert dialog is not None
    dialog.editor.set_expression(_site_filter("A"))
    qtbot.mouseClick(dialog.delete_button, Qt.LeftButton)
    assert page.draft["easyqcid"].tolist() == ["NEW001", "NEW002"]
    assert prompts
    assert prompts[0][0] == "确认删除"
    assert "1" in prompts[0][1]
    assert "评分记录" in prompts[0][1]


def test_import_write_modes_expose_contextual_conflict_policies(
    qtbot,
    tmp_path,
) -> None:
    page, _configuration, _current = _page(qtbot, tmp_path)
    page._install_draft(
        pd.DataFrame(
            {"easyqcid": ["SUB001", "SUB003"], "site": ["changed", "C"]}
        )
    )

    assert page.write_mode_combo.currentData() == "merge_columns"
    assert [
        page.conflict_policy_combo.itemData(index)
        for index in range(page.conflict_policy_combo.count())
    ] == ["preserve", "update"]
    assert "重复列" in page.stats_label.text()

    page.write_mode_combo.setCurrentIndex(
        page.write_mode_combo.findData("append")
    )
    assert [
        page.conflict_policy_combo.itemData(index)
        for index in range(page.conflict_policy_combo.count())
    ] == ["deduplicate", "replace"]
    assert page.conflict_policy_combo.isVisible()

    page.write_mode_combo.setCurrentIndex(
        page.write_mode_combo.findData("replace")
    )
    assert page.current_import_policy() == ("replace", None)
    assert not page.conflict_policy_combo.isVisible()
    assert "替换" in page.stats_label.text()


def test_import_append_replace_and_full_replace_follow_selected_policy(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    _accept_questions(monkeypatch)
    page, configuration, _current = _page(qtbot, tmp_path)
    page._install_draft(
        pd.DataFrame(
            {"easyqcid": ["SUB002", "SUB003"], "site": ["B2", "C"]}
        )
    )
    page.write_mode_combo.setCurrentIndex(
        page.write_mode_combo.findData("append")
    )
    page.conflict_policy_combo.setCurrentIndex(
        page.conflict_policy_combo.findData("replace")
    )

    qtbot.mouseClick(page.apply_button, Qt.LeftButton)
    _wait(page, qtbot)
    assert configuration.subjects().to_dict("records") == [
        {"easyqcid": "SUB001", "site": "A"},
        {"easyqcid": "SUB002", "site": "B2"},
        {"easyqcid": "SUB003", "site": "C"},
    ]

    page._install_draft(
        pd.DataFrame({"easyqcid": ["NEW001"], "batch": ["X"]})
    )
    page.write_mode_combo.setCurrentIndex(
        page.write_mode_combo.findData("replace")
    )
    qtbot.mouseClick(page.apply_button, Qt.LeftButton)
    _wait(page, qtbot)
    assert configuration.subjects().to_dict("records") == [
        {"easyqcid": "NEW001", "batch": "X"}
    ]
