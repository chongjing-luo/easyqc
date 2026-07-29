from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from shiboken6 import isValid
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableView,
    QWidget,
)

from core.qc_workflow_service import QcWorkflowService
from gui_qt import qc_workspace as qc_workspace_module
from gui_qt.i18n import LanguageController
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
        "easyqcid": None,
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


def _rating_dir(tmp_path: Path) -> Path:
    return tmp_path / "RatingFiles" / "AnatQC" / "rater1"


def _workflow(
    tmp_path: Path,
    *,
    module=None,
    executor=None,
    subjects=None,
    initial_easyqcid=None,
    initial_read_only=False,
):
    return QcWorkflowService(
        module or _module(),
        subjects
        if subjects is not None
        else pd.DataFrame(
            {"easyqcid": ["SUB001", "SUB002"], "image": ["one.nii", "two.nii"]}
        ),
        rating_dir=_rating_dir(tmp_path),
        code_executor=executor or _FakeExecutor(),
        initial_easyqcid=initial_easyqcid,
        initial_read_only=initial_read_only,
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
    assert controller.windowTitle() == "EasyQC · AnatQC · rater1"
    assert controller.centralWidget() is workspace
    assert isinstance(workspace.queue_table, QTableView)
    assert workspace.queue_table.model() is workspace.queue_model
    assert [
        workspace.queue_model.headerData(column, Qt.Horizontal, Qt.DisplayRole)
        for column in range(workspace.queue_model.columnCount())
    ] == ["序号", "easyqcid", "评分", "标签"]
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


def test_score_choices_are_native_exclusive_rectangular_push_buttons(
    qtbot,
    tmp_path,
) -> None:
    workspace = QtQcWorkspace(_workflow(tmp_path))
    qtbot.addWidget(workspace)
    workspace.show()

    buttons = workspace.score_buttons["1"]
    legacy_button = workspace._legacy_score_buttons["1"]
    group = workspace._score_groups["1"]

    assert tuple(buttons) == (None, "Poor", "Fair", "Good")
    assert all(isinstance(button, QPushButton) for button in buttons.values())
    assert isinstance(legacy_button, QPushButton)
    assert all(button.isCheckable() for button in (*buttons.values(), legacy_button))
    assert group.exclusive()
    assert set(group.buttons()) == {*buttons.values(), legacy_button}
    assert group.checkedButton() is buttons[None]
    assert [value for value, button in buttons.items() if button.isChecked()] == [None]
    assert workspace.findChildren(QRadioButton) == []
    assert all(
        button.styleSheet() == "" and button.icon().isNull()
        for button in (*buttons.values(), legacy_button)
    )


def test_score_choice_mouse_and_space_activation_update_exactly_one_draft(
    qtbot,
    tmp_path,
) -> None:
    workflow = _workflow(tmp_path)
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)
    workspace.show()
    workspace.activateWindow()
    states = []
    workspace.draftStateChanged.connect(states.append)
    buttons = workspace.score_buttons["1"]

    qtbot.mousePress(buttons["Good"], Qt.LeftButton)
    assert buttons["Good"].isDown()
    qtbot.mouseRelease(buttons["Good"], Qt.LeftButton)

    assert not buttons["Good"].isDown()
    assert workflow.current_module.scores["1"].value == "Good"
    assert [value for value, button in buttons.items() if button.isChecked()] == [
        "Good"
    ]
    assert states[-1] is True

    buttons["Fair"].setFocus()
    qtbot.waitUntil(buttons["Fair"].hasFocus)
    qtbot.keyClick(buttons["Fair"], Qt.Key.Key_Space)

    assert buttons["Fair"].hasFocus()
    assert buttons["Fair"].accessibleName() == "Quality: Fair"
    assert workflow.current_module.scores["1"].value == "Fair"
    assert [value for value, button in buttons.items() if button.isChecked()] == [
        "Fair"
    ]
    assert not buttons["Good"].isChecked()
    assert states[-1] is True


def test_qc_controller_switches_language_without_losing_unsaved_rating(
    qtbot,
    tmp_path,
) -> None:
    language = LanguageController(
        settings=QSettings(str(tmp_path / "qc-language.ini"), QSettings.IniFormat)
    )
    controller = _controller_class()(_workflow(tmp_path), language=language)
    qtbot.addWidget(controller)
    controller.show()
    workspace = controller.workspace
    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    workspace.notes_edit.setPlainText("keep this draft")

    language.set_language("en")

    try:
        assert [
            workspace.queue_model.headerData(column, Qt.Horizontal, Qt.DisplayRole)
            for column in range(workspace.queue_model.columnCount())
        ] == ["No.", "easyqcid", "Rating", "Tags"]
        assert [control.text() for control in workspace.action_controls] == [
            "Read only",
            "Filter list",
            "Previous",
            "Next",
            "Save",
            "Save and next",
        ]
        assert workspace.workflow.current_module.scores["1"].value == "Good"
        assert workspace.notes_edit.toPlainText() == "keep this draft"
        assert workspace.workflow.dirty
        assert workspace.score_buttons["1"]["Good"].isChecked()
        accessibility_texts = {
            text
            for widget in (controller, *controller.findChildren(QWidget))
            for text in (widget.accessibleName(), widget.toolTip())
            if text
        }
        assert {
            text
            for text in accessibility_texts
            if any("\u3400" <= char <= "\u9fff" for char in text)
        } == set()
    finally:
        workspace.workflow.discard_changes()


def test_qc_language_switch_preserves_user_defined_chinese_rating_and_tag_text(
    qtbot,
    tmp_path,
) -> None:
    module = _module()
    module["scores"]["1"]["label"] = "质控模块"
    module["scores"]["1"]["num"] = "常量设置,质控结果"
    module["scores"]["1"]["num_"] = "常量设置,质控结果"
    module["tags"]["1"]["label"] = "项目选择"
    language = LanguageController(
        settings=QSettings(str(tmp_path / "qc-user-text-language.ini"), QSettings.IniFormat)
    )
    controller = _controller_class()(
        _workflow(tmp_path, module=module),
        language=language,
    )
    qtbot.addWidget(controller)
    controller.show()
    workspace = controller.workspace

    language.set_language("en")

    score_button = workspace.score_buttons["1"]["常量设置"]
    assert score_button.parentWidget().title() == "质控模块"
    assert score_button.text() == "常量设置"
    assert workspace.score_buttons["1"]["质控结果"].text() == "质控结果"
    assert workspace.tag_boxes["1"].text() == "项目选择"
    assert workspace.save_next_button.text() == "Save and next"


def test_qc_queue_model_keeps_100k_prepared_queue_rows_virtual_without_local_filter(
    qtbot,
    tmp_path,
) -> None:
    identities = [f"QC-{index:06d}" for index in range(100_000)]
    workflow = _workflow(
        tmp_path,
        initial_easyqcid=identities[-1],
        subjects=pd.DataFrame(
            {
                "easyqcid": identities,
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
            "easyqcid": ["B-001", "B-002"],
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

    assert workspace.workflow.current_easyqcid == "B-002"
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
    assert workspace.workflow.current_easyqcid == "B-001"
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
    assert workspace.workflow.current_easyqcid == "SUB001"
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
            "easyqcid": ["B-001", "B-002"],
            "image": ["b1.nii", "b2.nii"],
        }
    )
    controller = _controller_class()(
        _workflow(tmp_path, executor=executor, subjects=subjects)
    )
    qtbot.addWidget(controller)
    workspace = controller.workspace
    assert workspace.workflow.current_easyqcid == "B-001"
    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    qtbot.mouseClick(workspace.save_next_button, Qt.LeftButton)

    assert workspace.workflow.current_easyqcid == "B-002"
    assert list(_rating_dir(tmp_path).glob("AnatQC-rater1-B-001.json"))
    assert executor.started[-1][0] == {0: "freeview b2.nii"}

    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    monkeypatch.setattr(
        workspace.workflow,
        "save",
        lambda: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    qtbot.mouseClick(workspace.save_button, Qt.LeftButton)
    assert workspace.workflow.current_easyqcid == "B-002"
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

    workspace.set_filter_busy(True)
    assert not workspace.score_buttons["1"]["Good"].isEnabled()
    assert workspace.queue_table.isEnabled()
    assert not workspace.next_button.isEnabled()
    workspace.set_filter_busy(False)
    assert workspace.score_buttons["1"]["Good"].isEnabled()
    assert workspace.next_button.isEnabled()

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


def test_initial_history_read_only_can_be_cleared_then_edited_and_saved(
    qtbot,
    tmp_path,
) -> None:
    controller = _controller_class()(
        _workflow(tmp_path, initial_read_only=True)
    )
    qtbot.addWidget(controller)
    controller.show()
    workspace = controller.workspace

    assert workspace.read_only_box.isChecked()
    assert workspace.read_only_box.isEnabled()
    assert not workspace.workflow.watch_mode
    assert not workspace.score_buttons["1"]["Good"].isEnabled()
    assert not workspace.tag_boxes["1"].isEnabled()
    assert workspace.notes_edit.isReadOnly()
    assert not workspace.save_button.isEnabled()

    workspace.read_only_box.click()
    qtbot.mouseClick(workspace.score_buttons["1"]["Good"], Qt.LeftButton)
    qtbot.mouseClick(workspace.tag_boxes["1"], Qt.LeftButton)
    workspace.notes_edit.setPlainText("edited historical note")
    qtbot.mouseClick(workspace.save_button, Qt.LeftButton)

    assert not workspace.read_only_box.isChecked()
    assert workspace.score_buttons["1"]["Good"].isEnabled()
    assert workspace.workflow.current_module.scores["1"].value == "Good"
    assert workspace.workflow.current_module.tags["1"].value is True
    assert workspace.workflow.current_module.notes == "edited historical note"
    assert list(_rating_dir(tmp_path).glob("AnatQC-rater1-SUB001.json"))


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

    assert workflow.current_easyqcid == "SUB002"
    assert list(_rating_dir(tmp_path).glob("AnatQC-rater1-SUB001.json"))
    assert workspace.queue_model.current_visible_row() == 1
    assert workspace.queue_table.currentIndex().row() == 1
    assert workspace.score_buttons["1"][None].isChecked()
    assert sum(
        button.isChecked() for button in workspace.score_buttons["1"].values()
    ) == 1
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

    assert workflow.current_easyqcid == "SUB001"
    assert workflow.dirty
    assert workspace.score_buttons["1"]["Good"].isChecked()
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
            "easyqcid": ["SUBJECT_" + "A" * 20, "SUBJECT_" + "B" * 20],
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
    ) == subjects.iloc[0]["easyqcid"]

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
    before = list(_rating_dir(tmp_path).glob("*.json"))
    workspace.notes_edit.setFocus()
    qtbot.waitUntil(workspace.notes_edit.hasFocus)
    qtbot.keyClick(
        workspace.notes_edit,
        Qt.Key.Key_S,
        Qt.KeyboardModifier.ControlModifier,
    )
    assert list(_rating_dir(tmp_path).glob("*.json")) == before
    workspace.queue_table.doubleClicked.emit(workspace.queue_model.index(0, 1))
    assert executor.started
    assert all(control.isVisible() for control in workspace.action_controls)
    assert controller.minimumSizeHint().width() <= 640


def test_qc_layout_reserves_eight_rows_and_gives_added_height_to_queue(
    qtbot,
    tmp_path,
) -> None:
    subjects = pd.DataFrame(
        {
            "easyqcid": [f"SUB{index:03d}" for index in range(12)],
            "image": [f"{index}.nii" for index in range(12)],
        }
    )
    controller = _controller_class()(
        _workflow(tmp_path, subjects=subjects)
    )
    qtbot.addWidget(controller)
    controller.show()
    workspace = controller.workspace
    qtbot.wait(0)

    row_height = workspace.queue_table.verticalHeader().defaultSectionSize()
    required_table_height = (
        workspace.queue_table.horizontalHeader().height()
        + row_height * 8
        + 2 * workspace.queue_table.frameWidth()
    )
    assert workspace.queue_table.minimumHeight() >= required_table_height

    action_layout = workspace.action_column.layout()
    occupied_height = sum(
        control.height() for control in workspace.action_controls
    ) + action_layout.spacing() * (len(workspace.action_controls) - 1)
    assert all(
        control.minimumHeight() >= row_height
        for control in workspace.action_controls
    )
    assert abs(occupied_height - workspace.action_column.contentsRect().height()) <= 2
    assert abs(workspace.action_column.height() - workspace.queue_table.height()) <= 2

    initial_table_height = workspace.queue_table.height()
    initial_editor_height = workspace.editor_scroll.height()
    controller.resize(controller.width(), controller.height() + 160)
    qtbot.wait(0)

    assert workspace.queue_table.height() > initial_table_height
    assert workspace.queue_table.height() - initial_table_height >= (
        workspace.editor_scroll.height() - initial_editor_height
    )


def test_score_growth_raises_height_and_tags_use_one_scrollable_row(
    qtbot,
    tmp_path,
) -> None:
    simple = _controller_class()(_workflow(tmp_path / "simple"))
    qtbot.addWidget(simple)
    simple.show()

    rich_module = _module()
    rich_module["scores"] = {
        str(index): {
            "label": f"Score {index}",
            "num": "Poor,Fair,Good",
            "num_": "Poor,Fair,Good",
            "value": None,
        }
        for index in range(1, 7)
    }
    rich_module["tags"] = {
        str(index): {"label": f"Tag {index}", "value": False}
        for index in range(1, 13)
    }
    rich = _controller_class()(
        _workflow(tmp_path / "rich", module=rich_module)
    )
    qtbot.addWidget(rich)
    rich.show()
    qtbot.wait(0)

    assert (
        rich.workspace.editor_scroll.widget().sizeHint().height()
        > simple.workspace.editor_scroll.widget().sizeHint().height()
    )
    assert rich.height() > simple.height()
    assert rich.height() <= rich.screen().availableGeometry().height()
    tag_centers = {
        checkbox.mapTo(
            rich.workspace.tags_scroll.widget(),
            checkbox.rect().center(),
        ).y()
        for checkbox in rich.workspace.tag_boxes.values()
    }
    assert tag_centers == {next(iter(tag_centers))}
    assert (
        rich.workspace.tags_scroll.verticalScrollBarPolicy()
        == Qt.ScrollBarAlwaysOff
    )
    assert (
        rich.workspace.tags_scroll.horizontalScrollBarPolicy()
        == Qt.ScrollBarAsNeeded
    )
    assert (
        rich.workspace.tags_scroll.widget().minimumWidth()
        > rich.workspace.tags_scroll.viewport().width()
    )

    rich.resize(rich.width(), 560)
    qtbot.wait(0)
    notes_group = rich.workspace.findChild(QGroupBox, "notesGroup")
    assert notes_group is not None
    assert rich.workspace.editor_scroll.verticalScrollBar().maximum() > 0
    rich.workspace.editor_scroll.ensureWidgetVisible(notes_group)
    assert rich.workspace.editor_scroll.verticalScrollBar().value() > 0


def test_qt_schema_drift_shows_reused_legacy_score_then_restores_editing(
    qtbot,
    tmp_path,
) -> None:
    target = _rating_dir(tmp_path)
    target.mkdir(parents=True)
    saved = _module()
    saved.update({"easyqcid": "SUB001", "rater": "rater1"})
    saved["schema_version"] = 3
    saved["scores"]["1"]["num_"] = "Reject,Accept"
    saved["scores"]["1"]["value"] = "Accept"
    (target / "AnatQC-rater1-SUB001.json").write_text(
        json.dumps(saved), encoding="utf-8"
    )

    workflow = _workflow(tmp_path)
    workspace = QtQcWorkspace(workflow)
    qtbot.addWidget(workspace)
    workspace.show()

    legacy_button = workspace._legacy_score_buttons["1"]
    assert isinstance(legacy_button, QPushButton)
    assert legacy_button.isCheckable()
    assert legacy_button.isVisible()
    assert legacy_button.isChecked()
    assert workspace._score_groups["1"].checkedButton() is legacy_button
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
    assert workspace._score_groups["1"].checkedButton() is (
        workspace.score_buttons["1"][None]
    )
    assert workspace.score_buttons["1"]["Good"].isEnabled()
    assert workspace.tag_boxes["1"].isEnabled()
    assert not workspace.notes_edit.isReadOnly()
    assert workspace.save_button.isEnabled()
    assert workspace.queue_table.isEnabled()
    assert not workspace.read_only_box.isChecked()


def test_qt_module_watch_survives_schema_case_navigation(qtbot, tmp_path) -> None:
    target = _rating_dir(tmp_path)
    target.mkdir(parents=True)
    saved = _module(watch_mode=True)
    saved.update({"easyqcid": "SUB001", "rater": "rater1"})
    saved["schema_version"] = 3
    saved["scores"]["1"]["num_"] = "Reject,Accept"
    saved["scores"]["1"]["value"] = "Accept"
    (target / "AnatQC-rater1-SUB001.json").write_text(
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
    assert workspace.score_buttons["1"]["Good"].isChecked()
    assert sum(
        button.isChecked() for button in workspace.score_buttons["1"].values()
    ) == 1
    assert states[-1] is False
