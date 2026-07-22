from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QScrollArea, QSplitter, QToolBar, QWidget

from core.qc_workflow_service import QcWorkflowService
from gui_qt.qc_workspace import QtQcWorkspace


class _FakeExecutor:
    def __init__(self) -> None:
        self.started = []
        self.close_calls = 0

    def render_command_plan(self, template, variables):
        return template.format(**variables), {0: template.format(**variables)}

    def start_commands(self, commands, control=False, cwd=None):
        self.started.append((dict(commands), control))
        return [object()]

    def close_current_processes(self):
        self.close_calls += 1


def _module(*, rater="rater1", watch_mode=False):
    return {
        "name": "AnatQC",
        "label": "Anatomical quality",
        "rater": rater,
        "ezqcid": None,
        "watch_mode": watch_mode,
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


def _workflow(tmp_path: Path, *, module=None, executor=None, subjects=None):
    return QcWorkflowService(
        module or _module(),
        subjects
        if subjects is not None
        else pd.DataFrame(
            {"ezqcid": ["SUB001", "SUB002"], "image": ["one.nii", "two.nii"]}
        ),
        rating_dir=tmp_path / "ratings",
        code_executor=executor or _FakeExecutor(),
    )


def test_qt_qc_editor_saves_full_draft_then_advances(qtbot, tmp_path) -> None:
    workflow = _workflow(tmp_path)
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)
    workspace.show()

    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    qtbot.mouseClick(workspace.tag_boxes["1"], Qt.LeftButton)
    workspace.notes_edit.setPlainText("Looks good")
    qtbot.mouseClick(workspace.save_next_button, Qt.LeftButton)

    assert workflow.current_ezqcid == "SUB002"
    assert list((tmp_path / "ratings").glob("AnatQC._.SUB001*.json"))
    assert "SUB002" in workspace.subject_label.text()
    assert workspace.error_text == ""


def test_qt_failed_save_stays_on_current_subject_and_shows_error(qtbot, tmp_path, monkeypatch) -> None:
    workflow = _workflow(tmp_path)
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)
    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)

    def fail(_delta):
        raise OSError("disk unavailable")

    monkeypatch.setattr(workflow, "save_and_move", fail)
    qtbot.mouseClick(workspace.save_next_button, Qt.LeftButton)

    assert workflow.current_ezqcid == "SUB001"
    assert "disk unavailable" in workspace.error_text


def test_qt_watch_mode_disables_edits_and_save_but_keeps_viewer(qtbot, tmp_path) -> None:
    executor = _FakeExecutor()
    workflow = _workflow(
        tmp_path,
        module=_module(rater=None),
        executor=executor,
    )
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)

    assert not workspace.score_buttons["1"]["Good"].isEnabled()
    assert not workspace.tag_boxes["1"].isEnabled()
    assert workspace.notes_edit.isReadOnly()
    assert not workspace.save_button.isEnabled()
    assert workspace.viewer_button.isEnabled()
    assert "Read-only" in workspace.watch_badge.text()

    qtbot.mouseClick(workspace.viewer_button, Qt.LeftButton)
    assert executor.started


def test_qt_qc_workspace_resizes_with_long_text_and_keyboard_actions(
    qtbot,
    tmp_path,
) -> None:
    executor = _FakeExecutor()
    module = _module(rater=None)
    module["label"] = "解剖质量控制模块_长中文标签_" + "质量" * 24
    module["scores"]["1"]["label"] = "图像质量评分_" + "非常长" * 18
    module["tags"]["1"]["label"] = "需要人工复核的长标签_" + "复核" * 18
    subjects = pd.DataFrame(
        {
            "ezqcid": ["受试者_" + "一" * 20, "受试者_" + "二" * 20],
            "image": [
                "/含 空格/中文路径/" + "深层目录/" * 12 + "one.nii",
                "/含 空格/中文路径/" + "深层目录/" * 12 + "two.nii",
            ],
        }
    )
    workspace = QtQcWorkspace(
        _workflow(
            tmp_path,
            module=module,
            executor=executor,
            subjects=subjects,
        )
    )
    qtbot.addWidget(workspace)
    workspace.resize(640, 480)
    workspace.show()
    workspace.activateWindow()

    splitter = workspace.findChild(QSplitter, "qcSplitter")
    editor_scroll = workspace.findChild(QScrollArea, "qcEditorScroll")
    action_toolbar = workspace.findChild(QToolBar, "qcActionToolbar")
    assert splitter is workspace.qc_splitter
    assert not splitter.isCollapsible(0)
    assert not splitter.isCollapsible(1)
    assert editor_scroll is workspace.editor_scroll
    assert editor_scroll.widgetResizable()
    assert action_toolbar is workspace.action_toolbar
    assert workspace.subject_list.maximumWidth() == QWidget().maximumWidth()
    assert workspace.watch_badge.maximumWidth() == QWidget().maximumWidth()
    assert workspace.module_label.wordWrap()
    assert workspace.subject_label.wordWrap()
    assert workspace.module_label.toolTip() == workspace.module_label.text()
    assert workspace.subject_label.toolTip() == workspace.subject_label.text()
    assert workspace.subject_list.item(0).toolTip().endswith(subjects.iloc[0]["ezqcid"])

    actions = (
        workspace.viewer_action,
        workspace.previous_action,
        workspace.next_action,
        workspace.discard_action,
        workspace.save_action,
        workspace.save_next_action,
    )
    assert all(action.shortcut().toString() for action in actions)
    assert not workspace.save_action.isEnabled()
    assert not workspace.save_next_action.isEnabled()
    before = list((tmp_path / "ratings").glob("*.json"))
    workspace.notes_edit.setFocus()
    qtbot.waitUntil(workspace.notes_edit.hasFocus)
    qtbot.keyClick(
        workspace.notes_edit,
        Qt.Key.Key_S,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert list((tmp_path / "ratings").glob("*.json")) == before
    qtbot.keyClick(
        workspace.notes_edit,
        Qt.Key.Key_V,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert executor.started

    expected_minimum = workspace._subject_list_accessible_width()
    assert workspace.subject_list.minimumWidth() == expected_minimum
    font = workspace.font()
    font.setPointSize(font.pointSize() + 4)
    workspace.setFont(font)
    QCoreApplication.sendEvent(workspace, QEvent(QEvent.Type.FontChange))
    assert (
        workspace.subject_list.minimumWidth()
        == workspace._subject_list_accessible_width()
    )


def test_qt_schema_drift_shows_reused_legacy_score_then_restores_editing(
    qtbot,
    tmp_path,
) -> None:
    target = tmp_path / "ratings"
    target.mkdir()
    saved = _module()
    saved.update({"ezqcid": "SUB001", "rater": "rater1"})
    saved["scores"]["1"]["num_"] = "Reject,Accept"
    saved["scores"]["1"]["value"] = "Accept"
    (target / "AnatQC._.SUB001._.rater1._.Accept._.False.json").write_text(
        json.dumps(saved), encoding="utf-8"
    )

    workflow = _workflow(tmp_path)
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)
    workspace.show()

    legacy_button = workspace._legacy_score_buttons["1"]
    assert legacy_button.isVisible()
    assert legacy_button.isChecked()
    assert not legacy_button.isEnabled()
    assert legacy_button.text() == "Accept (saved legacy value; not in current schema)"
    assert "Quality" in legacy_button.accessibleName()
    assert "Accept" in legacy_button.accessibleName()
    assert not workspace.score_buttons["1"][None].isChecked()
    assert "schema" in workspace.watch_badge.text().lower()
    assert not workspace.score_buttons["1"]["Good"].isEnabled()
    assert not workspace.tag_boxes["1"].isEnabled()
    assert workspace.notes_edit.isReadOnly()
    assert not workspace.save_button.isEnabled()
    assert not workspace.save_next_button.isEnabled()
    assert workspace.viewer_button.isEnabled()
    assert workspace.next_button.isEnabled()

    qtbot.mouseClick(workspace.next_button, Qt.LeftButton)

    assert workspace._legacy_score_buttons["1"] is legacy_button
    assert legacy_button.isHidden()
    assert workspace.score_buttons["1"][None].isChecked()
    assert workspace.score_buttons["1"]["Good"].isEnabled()
    assert workspace.tag_boxes["1"].isEnabled()
    assert not workspace.notes_edit.isReadOnly()
    assert workspace.save_button.isEnabled()
    assert workspace.viewer_button.isEnabled()
    assert "Rating mode" in workspace.watch_badge.text()


def test_qt_module_watch_survives_schema_case_navigation(qtbot, tmp_path) -> None:
    target = tmp_path / "ratings"
    target.mkdir()
    saved = _module(watch_mode=True)
    saved.update({"ezqcid": "SUB001", "rater": "rater1"})
    saved["scores"]["1"]["num_"] = "Reject,Accept"
    saved["scores"]["1"]["value"] = "Accept"
    (target / "AnatQC._.SUB001._.rater1._.Accept._.False.json").write_text(
        json.dumps(saved), encoding="utf-8"
    )

    workflow = _workflow(tmp_path, module=_module(watch_mode=True))
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)
    workspace.show()

    legacy_button = workspace._legacy_score_buttons["1"]
    assert legacy_button.isVisible()
    assert "watch mode" in workspace.watch_badge.text().lower()
    assert "schema" in workspace.watch_badge.text().lower()

    qtbot.mouseClick(workspace.next_button, Qt.LeftButton)

    assert workspace._legacy_score_buttons["1"] is legacy_button
    assert legacy_button.isHidden()
    assert workspace.score_buttons["1"][None].isChecked()
    assert "watch mode" in workspace.watch_badge.text().lower()
    assert "schema" not in workspace.watch_badge.text().lower()
    assert not workspace.score_buttons["1"]["Good"].isEnabled()
    assert not workspace.tag_boxes["1"].isEnabled()
    assert workspace.notes_edit.isReadOnly()
    assert not workspace.save_button.isEnabled()
    assert workspace.viewer_button.isEnabled()


def test_qt_qc_workspace_close_cleans_viewer_processes(qtbot, tmp_path) -> None:
    executor = _FakeExecutor()
    workflow = _workflow(tmp_path, executor=executor)
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)

    workspace.close()

    assert executor.close_calls == 1


def test_qt_qc_discard_restores_saved_state_and_emits_clean_draft(qtbot, tmp_path) -> None:
    workflow = _workflow(tmp_path)
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)
    states = []
    workspace.draftStateChanged.connect(states.append)

    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    assert workflow.dirty
    assert workspace.discard_button.isEnabled()
    qtbot.mouseClick(workspace.discard_button, Qt.LeftButton)

    assert not workflow.dirty
    assert workflow.current_module.scores["1"].value is None
    assert states[-1] is False
