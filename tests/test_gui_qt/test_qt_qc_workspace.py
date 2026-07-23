from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from shiboken6 import isValid
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableView,
    QWidget,
)

from core.qc_workflow_service import QcWorkflowService
from gui_qt import qc_workspace as qc_workspace_module
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


def _workflow(
    tmp_path: Path,
    *,
    module=None,
    executor=None,
    subjects=None,
    initial_ezqcid=None,
):
    return QcWorkflowService(
        module or _module(),
        subjects
        if subjects is not None
        else pd.DataFrame(
            {"ezqcid": ["SUB001", "SUB002"], "image": ["one.nii", "two.nii"]}
        ),
        rating_dir=tmp_path / "ratings",
        code_executor=executor or _FakeExecutor(),
        initial_ezqcid=initial_ezqcid,
    )


def _controller_class():
    controller_class = getattr(qc_workspace_module, "QtQcControllerWindow", None)
    assert controller_class is not None, "compact top-level QC controller is missing"
    return controller_class


def test_compact_qc_controller_is_top_level_with_exact_table_and_action_order(
    qtbot,
    tmp_path,
) -> None:
    controller = _controller_class()(_workflow(tmp_path))
    qtbot.addWidget(controller)
    controller.resize(640, 760)
    controller.show()
    workspace = controller.workspace

    assert isinstance(controller, QMainWindow)
    assert controller.isWindow()
    assert controller.parent() is None
    assert controller.windowTitle() == "EasyQC"
    assert controller.centralWidget() is workspace
    assert isinstance(workspace.queue_table, QTableView)
    assert workspace.queue_table.model() is workspace.queue_model
    assert [
        workspace.queue_model.headerData(column, Qt.Horizontal, Qt.DisplayRole)
        for column in range(workspace.queue_model.columnCount())
    ] == ["序号", "ezqcid", "评分", "标签"]
    assert workspace.queue_table.editTriggers() == QAbstractItemView.NoEditTriggers
    assert workspace.queue_table.selectionBehavior() == QAbstractItemView.SelectRows
    assert workspace.queue_table.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOn
    assert [control.text() for control in workspace.action_controls] == [
        "只读",
        "筛选名单",
        "上一个",
        "下一个",
        "保存",
        "保存并下一个",
    ]
    assert isinstance(workspace.action_controls[0], QCheckBox)
    assert all(
        isinstance(control, QPushButton) for control in workspace.action_controls[1:]
    )
    for removed_name in (
        "module_label",
        "subject_label",
        "viewer_button",
        "watch_badge",
        "discard_button",
    ):
        assert not hasattr(workspace, removed_name)


def test_qc_queue_model_keeps_100k_prepared_queue_rows_virtual_without_local_filter(
    qtbot,
    tmp_path,
) -> None:
    identities = [f"QC-{index:06d}" for index in range(100_000)]
    workflow = _workflow(
        tmp_path,
        initial_ezqcid=identities[-1],
        subjects=pd.DataFrame(
            {
                "ezqcid": identities,
                "image": [f"image-{index}.nii" for index in range(100_000)],
            }
        ),
    )
    model_class = getattr(qc_workspace_module, "QtQcQueueModel", None)
    assert model_class is not None
    model = model_class(workflow)

    assert model.rowCount() == 100_000
    assert not hasattr(model, "_visible_positions")
    assert model.data(model.index(99_999, 1), Qt.DisplayRole) == "QC-099999"
    assert model.identity_at(99_999) == "QC-099999"
    assert not hasattr(model, "set_filter")
    model.visible_row_for = lambda _identity: (_ for _ in ()).throw(
        AssertionError("current-row hot path used linear fallback")
    )
    assert model.current_visible_row() == 99_999
    assert model.neighbor_identity("QC-099999", -1) == "QC-099998"
    model.refresh_identity("QC-099999")


def test_queue_double_click_and_previous_stay_inside_prepared_workflow_queue(
    qtbot,
    tmp_path,
) -> None:
    executor = _FakeExecutor()
    subjects = pd.DataFrame(
        {
            "ezqcid": ["B-001", "B-002"],
            "image": ["b1.nii", "b2.nii"],
        }
    )
    controller = _controller_class()(
        _workflow(tmp_path, executor=executor, subjects=subjects)
    )
    qtbot.addWidget(controller)
    controller.show()
    workspace = controller.workspace

    workspace.queue_table.doubleClicked.emit(workspace.queue_model.index(1, 1))

    assert workspace.workflow.current_ezqcid == "B-002"
    assert executor.started[-1][0] == {0: "freeview b2.nii"}

    assert workspace.queue_model.rowCount() == 2
    assert [
        workspace.queue_model.data(
            workspace.queue_model.index(row, 1),
            Qt.DisplayRole,
        )
        for row in range(2)
    ] == ["B-001", "B-002"]
    qtbot.mouseClick(workspace.previous_button, Qt.LeftButton)
    assert workspace.workflow.current_ezqcid == "B-001"
    assert executor.started[-1][0] == {0: "freeview b1.nii"}


def test_filter_action_requests_structured_transaction_and_dirty_draft_blocks_it(
    qtbot,
    tmp_path,
) -> None:
    controller = _controller_class()(_workflow(tmp_path))
    qtbot.addWidget(controller)
    controller.show()
    workspace = controller.workspace
    requests = []
    workspace.filterRequested.connect(lambda: requests.append(True))

    qtbot.mouseClick(workspace.filter_button, Qt.LeftButton)

    assert requests == [True]
    assert not hasattr(workspace, "set_queue_filter")

    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    qtbot.mouseClick(workspace.filter_button, Qt.LeftButton)

    assert requests == [True]
    assert "请先保存当前修改" in workspace.error_text


def test_dirty_draft_rejects_structured_filter_request_without_changing_queue(
    qtbot,
    tmp_path,
) -> None:
    workspace = QtQcWorkspace(_workflow(tmp_path))
    qtbot.addWidget(workspace)
    requests = []
    workspace.filterRequested.connect(lambda: requests.append(True))
    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    qtbot.mouseClick(workspace.filter_button, Qt.LeftButton)

    assert requests == []
    assert workspace.queue_model.rowCount() == 2
    assert workspace.workflow.current_ezqcid == "SUB001"
    assert workspace.workflow.dirty
    assert "请先保存" in workspace.error_text


def test_prepared_queue_save_next_stays_inside_workflow_identities(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    executor = _FakeExecutor()
    subjects = pd.DataFrame(
        {
            "ezqcid": ["B-001", "B-002"],
            "image": ["b1.nii", "b2.nii"],
        }
    )
    controller = _controller_class()(
        _workflow(tmp_path, executor=executor, subjects=subjects)
    )
    qtbot.addWidget(controller)
    workspace = controller.workspace
    assert workspace.workflow.current_ezqcid == "B-001"
    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    qtbot.mouseClick(workspace.save_next_button, Qt.LeftButton)

    assert workspace.workflow.current_ezqcid == "B-002"
    assert list((tmp_path / "ratings").glob("AnatQC._.B-001*.json"))
    assert executor.started[-1][0] == {0: "freeview b2.nii"}

    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    monkeypatch.setattr(
        workspace.workflow,
        "save",
        lambda: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    qtbot.mouseClick(workspace.save_button, Qt.LeftButton)
    assert workspace.workflow.current_ezqcid == "B-002"
    assert "disk unavailable" in workspace.error_text


def test_manual_and_core_read_only_use_the_single_top_control_but_keep_viewer(
    qtbot,
    tmp_path,
) -> None:
    executor = _FakeExecutor()
    controller = _controller_class()(_workflow(tmp_path, executor=executor))
    qtbot.addWidget(controller)
    controller.show()
    workspace = controller.workspace

    workspace.read_only_box.click()
    assert workspace.read_only_box.isChecked()
    assert not workspace.score_buttons["1"]["Good"].isEnabled()
    assert not workspace.tag_boxes["1"].isEnabled()
    assert workspace.notes_edit.isReadOnly()
    assert not workspace.save_button.isEnabled()
    workspace.queue_table.doubleClicked.emit(workspace.queue_model.index(0, 1))
    assert executor.started

    workspace.read_only_box.click()
    assert not workspace.read_only_box.isChecked()
    assert workspace.score_buttons["1"]["Good"].isEnabled()

    forced = _controller_class()(_workflow(tmp_path, module=_module(rater=None)))
    qtbot.addWidget(forced)
    assert forced.workspace.read_only_box.isChecked()
    assert not forced.workspace.read_only_box.isEnabled()
    assert len(
        [
            box
            for box in forced.workspace.findChildren(QCheckBox)
            if box.text() == "只读"
        ]
    ) == 1


def test_dirty_controller_close_rejects_without_cleanup_then_accepts_once(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    executor = _FakeExecutor()
    controller = _controller_class()(_workflow(tmp_path, executor=executor))
    qtbot.addWidget(controller)
    controller.show()
    qtbot.mouseClick(
        controller.workspace.score_buttons["1"]["Good"],
        Qt.LeftButton,
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.No,
    )

    assert not controller.close()
    assert controller.isVisible()
    assert controller.workspace.workflow.dirty
    assert executor.close_calls == 0

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *args, **kwargs: QMessageBox.Yes,
    )
    assert controller.close()
    assert not isValid(controller) or not controller.isVisible()
    assert executor.close_calls == 1


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
    assert workspace.queue_model.current_visible_row() == 1
    assert workspace.queue_table.currentIndex().row() == 1
    assert workspace.error_text == ""


def test_qt_failed_save_stays_on_current_subject_and_shows_error(qtbot, tmp_path, monkeypatch) -> None:
    workflow = _workflow(tmp_path)
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)
    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)

    def fail():
        raise OSError("disk unavailable")

    monkeypatch.setattr(workflow, "save", fail)
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
    assert workspace.read_only_box.isChecked()
    assert not workspace.read_only_box.isEnabled()
    assert "rater" in workspace.read_only_box.toolTip().lower()

    workspace.queue_table.doubleClicked.emit(workspace.queue_model.index(0, 1))
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
    controller = _controller_class()(
        _workflow(
            tmp_path,
            module=module,
            executor=executor,
            subjects=subjects,
        )
    )
    qtbot.addWidget(controller)
    controller.resize(640, 560)
    controller.show()
    controller.activateWindow()
    workspace = controller.workspace

    editor_scroll = workspace.findChild(QScrollArea, "qcEditorScroll")
    assert editor_scroll is workspace.editor_scroll
    assert editor_scroll.widgetResizable()
    assert workspace.queue_table.maximumWidth() == QWidget().maximumWidth()
    assert workspace.queue_table.model().rowCount() == 2
    assert workspace.queue_table.model().data(
        workspace.queue_table.model().index(0, 1),
        Qt.DisplayRole,
    ) == subjects.iloc[0]["ezqcid"]

    actions = (
        workspace.previous_action,
        workspace.next_action,
        workspace.save_action,
        workspace.save_next_action,
        workspace.filter_action,
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
    workspace.queue_table.doubleClicked.emit(workspace.queue_model.index(0, 1))
    assert executor.started
    assert all(control.isVisible() for control in workspace.action_controls)
    assert controller.minimumSizeHint().width() <= 640


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
    assert legacy_button.text() == "Accept（旧评分值，不在当前选项中）"
    assert "Quality" in legacy_button.accessibleName()
    assert "Accept" in legacy_button.accessibleName()
    assert not workspace.score_buttons["1"][None].isChecked()
    assert "schema" in workspace.read_only_box.toolTip().lower()
    assert not workspace.score_buttons["1"]["Good"].isEnabled()
    assert not workspace.tag_boxes["1"].isEnabled()
    assert workspace.notes_edit.isReadOnly()
    assert not workspace.save_button.isEnabled()
    assert not workspace.save_next_button.isEnabled()
    assert workspace.queue_table.isEnabled()
    assert workspace.next_button.isEnabled()

    qtbot.mouseClick(workspace.next_button, Qt.LeftButton)

    assert workspace._legacy_score_buttons["1"] is legacy_button
    assert legacy_button.isHidden()
    assert workspace.score_buttons["1"][None].isChecked()
    assert workspace.score_buttons["1"]["Good"].isEnabled()
    assert workspace.tag_boxes["1"].isEnabled()
    assert not workspace.notes_edit.isReadOnly()
    assert workspace.save_button.isEnabled()
    assert workspace.queue_table.isEnabled()
    assert not workspace.read_only_box.isChecked()


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
    assert "watch mode" in workspace.read_only_box.toolTip().lower()
    assert "schema" in workspace.read_only_box.toolTip().lower()

    qtbot.mouseClick(workspace.next_button, Qt.LeftButton)

    assert workspace._legacy_score_buttons["1"] is legacy_button
    assert legacy_button.isHidden()
    assert workspace.score_buttons["1"][None].isChecked()
    assert "watch mode" in workspace.read_only_box.toolTip().lower()
    assert "schema" not in workspace.read_only_box.toolTip().lower()
    assert not workspace.score_buttons["1"]["Good"].isEnabled()
    assert not workspace.tag_boxes["1"].isEnabled()
    assert workspace.notes_edit.isReadOnly()
    assert not workspace.save_button.isEnabled()
    assert workspace.queue_table.isEnabled()


def test_qt_qc_workspace_close_cleans_viewer_processes(qtbot, tmp_path) -> None:
    executor = _FakeExecutor()
    workflow = _workflow(tmp_path, executor=executor)
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)

    workspace.close()

    assert executor.close_calls == 1


def test_qt_qc_save_emits_clean_draft_without_a_visible_discard_control(
    qtbot,
    tmp_path,
) -> None:
    workflow = _workflow(tmp_path)
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)
    states = []
    workspace.draftStateChanged.connect(states.append)

    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    assert workflow.dirty
    assert not hasattr(workspace, "discard_button")
    qtbot.mouseClick(workspace.save_button, Qt.LeftButton)

    assert not workflow.dirty
    assert workflow.current_module.scores["1"].value == "Good"
    assert states[-1] is False
