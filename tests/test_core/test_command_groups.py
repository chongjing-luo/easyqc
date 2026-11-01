from copy import deepcopy
import json

import pandas as pd
import pytest

from core.app_services import build_app_services
from core.code_executor import CodeExecutor
from core.configuration_service import ConfigurationError, ConfigurationService
from core.qc_workflow_service import QcSessionError, QcWorkflowService
from core.rating_service import RatingService
from models.qcmodule import QCModule
from models.rating import Rating


def _payload(commands):
    return {"name": "AnatQC", "label": "Anatomical QC", "rater": "rater1",
            "code": None, "commands": commands, "control": True}


def _groups():
    return {"first": {"code": 'viewer "{image}"', "interper": "direct"},
            "second": {"code": "echo ready | cat", "interper": "shell"}}


class RecordingExecutor:
    def __init__(self):
        self.planner = CodeExecutor()
        self.started = []
        self.closed = False

    def render_command_plan(self, template, variables):
        return self.planner.render_command_plan(template, variables)

    def start_commands(self, commands, **kwargs):
        self.started.append((commands, kwargs))
        return list(commands.values())

    def close_current_processes(self):
        self.closed = True


def _workflow(tmp_path, groups, executor=None, image="/synthetic/image.nii"):
    return QcWorkflowService(
        _payload(groups),
        pd.DataFrame({"easyqcid": ["SUB001"], "image": [image]}),
        rating_dir=tmp_path / "RatingFiles" / "AnatQC" / "rater1",
        code_executor=executor or RecordingExecutor(),
    )


@pytest.mark.parametrize("groups,expected_code,expected_mode,expected_groups", [
    ({}, None, "direct", None),
    ({"blank": {"code": "  \n", "interper": "shell"}}, None, "direct", None),
    ({"blank": {"code": " ", "interper": "direct"},
      "one": {"code": "echo ready | cat", "interper": "shell"}},
     "echo ready | cat", "shell", None),
    (_groups(), None, "direct", _groups()),
])
def test_configuration_normalizes_zero_one_many_groups_without_mutation(
    groups, expected_code, expected_mode, expected_groups,
):
    source = _payload(groups)
    before = deepcopy(source)
    result = ConfigurationService._normalized_module_payload(source)
    assert result["code"] == expected_code
    assert result["interper"] == expected_mode
    assert result.get("commands") == expected_groups
    assert source == before
    if expected_groups is None:
        assert "commands" not in result


def test_single_group_serializes_identically_to_legacy_module():
    grouped = ConfigurationService._normalized_module_payload(
        _payload({"x": {"code": "viewer {image}", "interper": "direct"}})
    )
    legacy = _payload(None)
    legacy.pop("commands")
    legacy["code"] = "viewer {image}"
    expected = ConfigurationService._normalized_module_payload(legacy)
    assert json.dumps(grouped) == json.dumps(expected)


@pytest.mark.parametrize("commands", [[], {"x": {"code": None}},
    {"x": {"code": "", "interper": "invalid"}}])
def test_configuration_does_not_discard_invalid_groups(commands):
    with pytest.raises(ConfigurationError, match="[Cc]ommand|commands"):
        ConfigurationService._normalized_module_payload(_payload(commands))


def test_configuration_validates_each_group_before_persisting(tmp_path):
    services = build_app_services(tmp_path / "projects.json")
    service = services.configuration_service
    service.create_project("SAMPLE", tmp_path)
    before = service.current_project.settings_path.read_bytes()
    groups = _groups()
    groups["second"]["interper"] = "direct"
    with pytest.raises(ConfigurationError, match="命令组 2.*MULTICMD"):
        service.save_module(_payload(groups), original_name="example")
    assert service.current_project.settings_path.read_bytes() == before
    service.save_module(_payload(groups), original_name="example", allow_shell_separators=True)
    stored = service.modules()[0]
    assert stored.to_legacy_dict()["commands"] == groups


def test_executor_applies_each_shell_override_and_falls_back(monkeypatch):
    executor = CodeExecutor()
    calls = []
    monkeypatch.setattr(executor, "close_current_processes", lambda: calls.append("close"))
    monkeypatch.setattr(executor, "start_command", lambda command, **kw: calls.append((command, kw["shell"])))
    executor.start_commands({"a": "viewer a", "b": "echo b", "c": "echo c"},
                            shell=True, shells={"a": False, "b": True}, control=True)
    assert calls == ["close", ("viewer a", False), ("echo b", True), ("echo c", True)]


@pytest.mark.parametrize("shells", [{"a": 1}, {"a": "false"}, {"a": None}, {"unused": 0}, []])
def test_executor_rejects_invalid_modes_before_closing_or_starting(monkeypatch, shells):
    executor = CodeExecutor()
    calls = []
    monkeypatch.setattr(executor, "close_current_processes", lambda: calls.append("close"))
    monkeypatch.setattr(executor, "start_command", lambda *args, **kw: calls.append("start"))
    with pytest.raises(TypeError, match="shells"):
        executor.start_commands({"a": "viewer"}, shells=shells, control=True)
    assert calls == []


def test_group_plan_flattens_nested_multicmd_and_preserves_raw_structure(tmp_path):
    groups = _groups()
    groups["first"]["code"] = 'MULTICMD viewer "{image}";|other "literal;|quoted"'
    groups["script"] = {"code": "SCRIPT viewer\nprint('{image}')", "interper": "direct"}
    workflow = _workflow(tmp_path, groups, image="MULTICMD a;|b")
    plan = workflow.viewer_plan()
    assert plan.commands == {
        0: 'viewer "MULTICMD a;|b"', 1: 'other "literal;|quoted"',
        2: "echo ready | cat", 3: "SCRIPT viewer\nprint('MULTICMD a;|b')",
    }
    assert plan.shells == {0: False, 1: False, 2: True, 3: False}
    assert workflow.current_module.code_exe == {str(k): v for k, v in plan.commands.items()}
    assert plan.control is True


def test_group_launch_forwards_modes_contexts_and_rating_snapshot(tmp_path):
    executor = RecordingExecutor()
    workflow = _workflow(tmp_path, _groups(), executor)
    assert len(workflow.launch_viewer()) == 2
    commands, options = executor.started[0]
    assert options["shells"] == {0: False, 1: True}
    assert [context.command_index for context in options["output_contexts"].values()] == [0, 1]
    workflow.set_notes("checked")
    payload = json.loads(workflow.save().read_text(encoding="utf-8"))
    assert payload["commands"] == _groups()
    assert payload["code_exe"] == {str(key): value for key, value in commands.items()}
    assert payload["schema_version"] == 3


def test_failed_group_launch_restores_snapshot_and_cleans_up(tmp_path):
    class FailingExecutor(RecordingExecutor):
        def start_commands(self, commands, **kwargs):
            raise RuntimeError("viewer failed")

    executor = FailingExecutor()
    workflow = _workflow(tmp_path, _groups(), executor)
    assert workflow.current_module.code_exe is None
    with pytest.raises(RuntimeError, match="viewer failed"):
        workflow.launch_viewer()
    assert workflow.current_module.code_exe is None
    assert executor.closed


def test_zero_groups_keep_no_viewer_error(tmp_path):
    with pytest.raises(QcSessionError, match="no viewer"):
        _workflow(tmp_path, {}).viewer_plan()


def test_row_command_forwards_group_modes_without_saving_rating(tmp_path):
    services = build_app_services(tmp_path / "projects.json")
    configuration = services.configuration_service
    configuration.create_project("SAMPLE", tmp_path)
    configuration.replace_subjects(pd.DataFrame({"easyqcid": ["SUB001"], "image": ["/synthetic/a"]}))
    configuration.save_module(_payload(_groups()), original_name="example")
    context = services.project_context_service
    executor = RecordingExecutor()
    context._command_executor = executor
    snapshot = context.snapshot()
    context.launch_row_command(snapshot, "SUB001", "AnatQC")
    assert executor.started[0][1]["shells"] == {0: False, 1: True}
    assert executor.started[0][1]["output_contexts"][1].easyqcid == "SUB001"
    assert not executor.closed
    assert not list((configuration.current_project.path / "RatingFiles").rglob("*.json"))


def test_group_templates_stay_in_rating_json_not_derived_results(tmp_path):
    workflow = _workflow(tmp_path, _groups())
    workflow.set_notes("checked")
    payload = json.loads(workflow.save().read_text(encoding="utf-8"))
    rating = Rating.from_legacy_dict(payload)
    service = RatingService(project_or_service=None)
    long_table = service.rating_to_flat_dataframe(rating)
    assert "commands" not in long_table.columns
    wide = service.long_table_to_wide(long_table)
    merged = service.merge_subjects_with_rating_wide(wide, pd.DataFrame({"easyqcid": ["SUB001"]}))
    assert not any("commands" in column for column in merged)
    assert rating.to_legacy_dict()["commands"] == _groups()
