from __future__ import annotations

from dataclasses import replace
from threading import Event

import pandas as pd
import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit

from core.configuration_service import ConfigurationService
from core.event_bus import EventType
from core.project_service import ProjectService
from core.table_service import TableService
from models.table_view_state import FilterCondition, FilterExpression, FilterGroup


def _page(qtbot, tmp_path):
    from gui_qt.qc_list_import_page import QtQcListImportPage

    configuration = ConfigurationService(
        ProjectService(tmp_path / "projects.json"),
        TableService(),
    )
    configuration.create_project("SAMPLE", tmp_path)
    current = pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002"],
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


@pytest.mark.parametrize("source_mode", ["folder", "file", "text"])
def test_each_source_builds_a_preview_draft_without_persistence(
    qtbot,
    tmp_path,
    source_mode,
) -> None:
    page, configuration, current = _page(qtbot, tmp_path)
    table_path = configuration.current_project.table_dir / "ezqc_all.csv"
    before_bytes = table_path.read_bytes()

    if source_mode == "folder":
        source = tmp_path / "folders"
        (source / "SUB004").mkdir(parents=True)
        (source / "SUB003").mkdir()
        qtbot.mouseClick(page.folder_mode_button, Qt.LeftButton)
        page.source_path_edit.setText(str(source))
        page.single_column_name.setText("ezqcid")
        expected = ["SUB003", "SUB004"]
    elif source_mode == "file":
        source = tmp_path / "list.csv"
        pd.DataFrame(
            {"ezqcid": ["SUB003", "SUB004"], "site": ["C", "D"]}
        ).to_csv(source, index=False)
        qtbot.mouseClick(page.file_mode_button, Qt.LeftButton)
        page.source_path_edit.setText(str(source))
        expected = ["SUB003", "SUB004"]
    else:
        qtbot.mouseClick(page.text_mode_button, Qt.LeftButton)
        page.direct_text_edit.setPlainText("SUB003, SUB004")
        page.single_column_name.setText("ezqcid")
        expected = ["SUB003", "SUB004"]

    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)

    assert page.draft["ezqcid"].tolist() == expected
    assert page.preview_model.rowCount() == 2
    pd.testing.assert_frame_equal(configuration.subjects(), current)
    assert table_path.read_bytes() == before_bytes


def test_failed_parse_preserves_last_draft_and_active_list(qtbot, tmp_path) -> None:
    page, configuration, current = _page(qtbot, tmp_path)
    table_path = configuration.current_project.table_dir / "ezqc_all.csv"
    qtbot.mouseClick(page.text_mode_button, Qt.LeftButton)
    page.direct_text_edit.setPlainText("SUB003 SUB004")
    page.single_column_name.setText("ezqcid")
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


def test_preview_search_filter_and_columns_do_not_change_draft(qtbot, tmp_path) -> None:
    page, configuration, _current = _page(qtbot, tmp_path)
    source = tmp_path / "preview.csv"
    pd.DataFrame(
        {
            "ezqcid": ["SUB003", "SUB004", "SUB005"],
            "site": ["A", "B", "A"],
            "scanner_model": ["Prisma", "Skyra", "Prisma"],
        }
    ).to_csv(source, index=False)
    page.source_path_edit.setText(str(source))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    original = page.draft

    page.preview_search.setText("Prisma")
    page.preview_search.returnPressed.emit()
    _wait(page, qtbot)
    assert page.preview_model.rowCount() == 2
    page.preview_search.clear()
    page.preview_search.returnPressed.emit()
    _wait(page, qtbot)

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
    assert tuple(page.preview_model.snapshot().columns) == ("ezqcid", "site")
    pd.testing.assert_frame_equal(page.draft, original)
    pd.testing.assert_frame_equal(configuration.subjects(), _current)
    assert page.filter_button.text().startswith("筛选")
    assert page.columns_button.text().startswith("列")


def test_merge_columns_applies_once_and_emits_list_change(qtbot, tmp_path) -> None:
    page, configuration, _current = _page(qtbot, tmp_path)
    source = tmp_path / "columns.csv"
    pd.DataFrame(
        {"ezqcid": [" SUB001 ", "SUB003"], "batch": ["X", "Y"]}
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

    merged = configuration.subjects().set_index("ezqcid")
    assert list(merged.index) == ["SUB001", "SUB002", "SUB003"]
    assert merged.loc["SUB001", "batch"] == "X"
    assert len(events) == 1
    assert not page.error_text


def test_append_rows_and_clear_draft_are_explicit(qtbot, tmp_path) -> None:
    page, configuration, _current = _page(qtbot, tmp_path)
    source = tmp_path / "rows.csv"
    pd.DataFrame({"ezqcid": ["SUB003"], "site": ["C"]}).to_csv(
        source,
        index=False,
    )
    page.source_path_edit.setText(str(source))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    qtbot.mouseClick(page.append_rows_radio, Qt.LeftButton)

    qtbot.mouseClick(page.apply_button, Qt.LeftButton)
    _wait(page, qtbot)
    assert configuration.subjects()["ezqcid"].tolist() == [
        "SUB001",
        "SUB002",
        "SUB003",
    ]
    qtbot.mouseClick(page.clear_button, Qt.LeftButton)
    assert page.draft.empty
    assert configuration.subjects()["ezqcid"].tolist()[-1] == "SUB003"


def test_failed_apply_preserves_disk_active_list_and_draft(qtbot, tmp_path) -> None:
    page, configuration, current = _page(qtbot, tmp_path)
    source = tmp_path / "conflict.csv"
    pd.DataFrame({"ezqcid": ["SUB001"], "site": ["changed"]}).to_csv(
        source,
        index=False,
    )
    page.source_path_edit.setText(str(source))
    qtbot.mouseClick(page.read_preview_button, Qt.LeftButton)
    _wait(page, qtbot)
    draft = page.draft
    table_path = configuration.current_project.table_dir / "ezqc_all.csv"
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
    pd.DataFrame({"ezqcid": ["SUB003"]}).to_csv(source, index=False)
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
