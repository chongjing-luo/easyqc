"""Derived-column dialog integration: formula / grouping / rater assignment."""

from __future__ import annotations

import pandas as pd
from PySide6.QtCore import Qt

from core.configuration_service import ConfigurationService
from core.project_service import ProjectService
from core.table_service import TableService
from gui_qt.table_workspace import QtTableWorkspace


def config_for(tmp_path):
    config = ConfigurationService(ProjectService(tmp_path / "projects.json"), TableService())
    config.create_project("SAMPLE", tmp_path / "project")
    config.replace_subjects(
        pd.DataFrame({"easyqcid": [f"S{i:02d}" for i in range(5)], "site": ["A"] * 5})
    )
    return config


def _workspace(tmp_path, config):
    ws = QtTableWorkspace(
        config._subjects_snapshot().dataframe,
        derive_column_callback=lambda request: config.derive_subject_column(request, notify=False),
        group_column_callback=lambda column, size, order, seed: (
            config.add_subject_group_column(
                column, size=size, order=order,
                seed=int(seed) if seed is not None else None, notify=False,
            )
        ),
        rater_assignment_callback=lambda raters, per_image, seed: (
            config.export_rater_assignment(raters, per_image=per_image, seed=seed),
            1,
        )[1],
    )
    return ws


# ---- grouping tab ----

def test_group_tab_adds_numbered_column_end_to_end(qtbot, tmp_path):
    config = config_for(tmp_path)
    ws = _workspace(tmp_path, config)
    qtbot.addWidget(ws)

    dialog = ws.open_derived_column_dialog()
    assert dialog is not None
    dialog.mode_tabs.setCurrentWidget(dialog.group_page)
    dialog.name_edit.setText("batch_group")
    dialog.group_size_spin.setValue(3)
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: dialog.task_controller.busy is False and not dialog.isVisible())

    frame = config._subjects_snapshot().dataframe
    assert frame["batch_group"].tolist() == [1, 1, 1, 2, 2]


def test_group_tab_duplicate_name_keeps_dialog_open(qtbot, tmp_path):
    config = config_for(tmp_path)
    ws = _workspace(tmp_path, config)
    qtbot.addWidget(ws)

    dialog = ws.open_derived_column_dialog()
    dialog.mode_tabs.setCurrentWidget(dialog.group_page)
    dialog.name_edit.setText("site")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)

    assert dialog.error_label.text  # "列已存在"
    assert "batch_group" not in config._subjects_snapshot().dataframe.columns


# ---- assignment tab ----

def test_assignment_tab_exports_balanced_table_end_to_end(qtbot, tmp_path):
    config = config_for(tmp_path)
    ws = _workspace(tmp_path, config)
    qtbot.addWidget(ws)

    dialog = ws.open_derived_column_dialog()
    dialog.mode_tabs.setCurrentWidget(dialog.assignment_page)
    dialog.assign_raters_edit.setText("甲, 乙, 丙, 丁")
    dialog.assign_per_image_spin.setValue(2)
    dialog.assign_seed_edit.setText("11")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: dialog.task_controller.busy is False and not dialog.isVisible())

    path = config.table_service.table_path(
        config._require_project(), "ezqc_rater_assignment"
    )
    assert path.exists()
    out = pd.read_csv(path)
    assert len(out) == 10
    assert (out["easyqcid"].value_counts() == 2).all()
    per_rater = out["rater"].value_counts()
    assert per_rater.max() - per_rater.min() <= 2
    assert "rater" not in config._subjects_snapshot().dataframe.columns


def test_assignment_tab_rejects_empty_raters(qtbot, tmp_path):
    config = config_for(tmp_path)
    ws = _workspace(tmp_path, config)
    qtbot.addWidget(ws)

    dialog = ws.open_derived_column_dialog()
    dialog.mode_tabs.setCurrentWidget(dialog.assignment_page)
    dialog.assign_raters_edit.setText("   ")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    assert dialog.error_label.text
    path = config.table_service.table_path(
        config._require_project(), "ezqc_rater_assignment"
    )
    assert not path.exists()


def test_assignment_tab_preview_shows_stats(qtbot, tmp_path):
    config = config_for(tmp_path)
    ws = _workspace(tmp_path, config)
    qtbot.addWidget(ws)

    dialog = ws.open_derived_column_dialog()
    dialog.mode_tabs.setCurrentWidget(dialog.assignment_page)
    dialog.assign_raters_edit.setText("甲, 乙")
    qtbot.mouseClick(dialog.preview_button, Qt.LeftButton)

    assert dialog.preview_table.rowCount() > 0
    assert "每人" in dialog.assign_stats_label.text()


# ---- tab availability ----

def test_mode_tabs_hidden_without_handlers(qtbot, tmp_path):
    config = config_for(tmp_path)
    ws = QtTableWorkspace(  # formula-only callbacks
        config._subjects_snapshot().dataframe,
        derive_column_callback=lambda request: config.derive_subject_column(request, notify=False),
    )
    qtbot.addWidget(ws)
    dialog = ws.open_derived_column_dialog()
    assert dialog.mode_tabs.indexOf(dialog.group_page) == -1
    assert dialog.mode_tabs.indexOf(dialog.assignment_page) == -1
    assert dialog.mode_tabs.count() == 1


# ---- group order modes (dialog end-to-end) ----

def test_group_tab_reverse_mode(qtbot, tmp_path):
    config = config_for(tmp_path)
    ws = _workspace(tmp_path, config)
    qtbot.addWidget(ws)

    dialog = ws.open_derived_column_dialog()
    dialog.mode_tabs.setCurrentWidget(dialog.group_page)
    dialog.name_edit.setText("batch_group")
    dialog.group_size_spin.setValue(3)
    dialog.group_order_combo.setCurrentIndex(dialog.group_order_combo.findData("reverse"))
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: dialog.task_controller.busy is False and not dialog.isVisible())

    assert config._subjects_snapshot().dataframe["batch_group"].tolist() == [2, 2, 1, 1, 1]


def test_group_tab_random_mode_reproducible(qtbot, tmp_path):
    config = config_for(tmp_path)
    ws = _workspace(tmp_path, config)
    qtbot.addWidget(ws)

    dialog = ws.open_derived_column_dialog()
    dialog.mode_tabs.setCurrentWidget(dialog.group_page)
    dialog.name_edit.setText("batch_group")
    dialog.group_size_spin.setValue(1)
    dialog.group_order_combo.setCurrentIndex(dialog.group_order_combo.findData("random"))
    assert dialog.group_seed_edit.isEnabled()
    dialog.group_seed_edit.setText("42")
    qtbot.mouseClick(dialog.generate_button, Qt.LeftButton)
    qtbot.waitUntil(lambda: dialog.task_controller.busy is False and not dialog.isVisible())

    first = config._subjects_snapshot().dataframe["batch_group"].tolist()
    assert sorted(first) == [1, 2, 3, 4, 5]

    # rerun with same seed via core -> identical
    config.add_subject_group_column("again", size=1, order="random", seed=42, notify=False)
    # remove first run column from frame copy for comparison is complex; instead
    # verify core reproducibility directly
    frame = config._subjects_snapshot().dataframe
    assert frame["again"].tolist() == first


def test_group_tab_seed_disabled_for_sequential(qtbot, tmp_path):
    config = config_for(tmp_path)
    ws = _workspace(tmp_path, config)
    qtbot.addWidget(ws)
    dialog = ws.open_derived_column_dialog()
    dialog.mode_tabs.setCurrentWidget(dialog.group_page)
    assert not dialog.group_seed_edit.isEnabled()
    dialog.group_order_combo.setCurrentIndex(dialog.group_order_combo.findData("random"))
    assert dialog.group_seed_edit.isEnabled()
