"""Synthetic project integration for RI-13; never opens real viewers."""
from copy import deepcopy

import pandas as pd
import pytest

from core.app_services import build_app_services
from core.project_context_service import ProjectContextError
from tests.test_core.test_project_context_service import _module_payload, _filter_expression
from models.table_view_state import filter_expression_to_json_object


def _project(tmp_path):
    services = build_app_services(tmp_path / "projects.json")
    config = services.configuration_service
    config.create_project("Synthetic", tmp_path)
    config.replace_subjects(pd.DataFrame({
        "easyqcid": ["S01_anat", "S01_rest01", "S01_rest02"],
        "subses": ["01", "01", "01"], "kind": ["anat", "func", "func"],
        "image": ["/synthetic/anat", "/synthetic/rest01", "/synthetic/rest02"],
    }))
    config.save_module(_module_payload(name="RestQC"), original_name="example")
    context = services.project_context_service.open_initial()
    for identity, score in (("S01_anat", "Fair"), ("S01_rest01", "Good"), ("S01_rest02", "Poor")):
        workflow = services.project_context_service.create_qc_workflow(
            context, module_name="RestQC", initial_easyqcid=identity,
        )
        workflow.set_score("1", score)
        workflow.save()
    return services


def _rule():
    return dict(rule_id="rest_to_anat", name="Rest scores", source_module="RestQC",
                source_rater="rater1", source_key="subses", target_key="subses",
                fields=[["score1", "linked.rest"]],
                source_filter=filter_expression_to_json_object(_filter_expression("kind", "==", "func")),
                target_filter=filter_expression_to_json_object(_filter_expression("kind", "==", "anat")))


def _frame(service):
    result = service.apply_state(service.default_state(page_size=100))
    return service.get_window(result, 0, 100).dataframe


def test_association_settings_reload_and_projection_preserve_original_rating_files(tmp_path):
    services = _project(tmp_path)
    project = services.configuration_service.current_project
    before = {str(p): p.read_bytes() for p in (project.path / "RatingFiles").rglob("*.json")}
    services.configuration_service.save_result_associations([_rule()], notify=False)
    snapshot = services.project_context_service.snapshot()
    wide = _frame(snapshot.table_view_service)
    long = _frame(snapshot.long_results_table_view_service)
    assert len(wide) == 3 and len(long) == 3
    assert "S01_rest01: Good" in wide.loc[0, "linked.rest"]
    assert "S01_rest02: Poor" in wide.loc[0, "linked.rest"]
    assert long.loc[long.easyqcid.eq("S01_anat"), "linked.rest"].iloc[0] == wide.loc[0, "linked.rest"]
    assert "linked.rest" not in snapshot.subjects
    assert snapshot.association_rules[0].rule_id == "rest_to_anat"
    context = services.project_context_service.qc_row_context(snapshot, "S01_anat")
    assert {entry.easyqcid for entry in context.linked_records} == {"S01_rest01", "S01_rest02"}
    assert {entry.easyqcid for entry in context.records} == {"S01_anat"}
    assert {entry.easyqcid for entry in context.linked_modules} == {"S01_rest01", "S01_rest02"}
    record = context.linked_records[0]
    workflow = services.project_context_service.create_qc_record_workflow(
        snapshot, easyqcid=record.easyqcid, module_name=record.module_name, rater=record.rater,
    )
    # Saved records open with a user-reversible read-only presentation, not
    # the immutable Core watch-mode guard (the existing workflow contract).
    assert workflow.initial_read_only
    assert workflow.current_easyqcid == record.easyqcid
    after = {str(p): p.read_bytes() for p in (project.path / "RatingFiles").rglob("*.json")}
    assert after == before
    reloaded = build_app_services(tmp_path / "projects.json").project_context_service.open_initial()
    assert _frame(reloaded.table_view_service).equals(wide)


@pytest.mark.parametrize("bad_change", [
    {"target_key": "missing"}, {"fields": [["score1", "subses"]]},
    {"fields": [["score1", "RestQC.rater1.score1"]]}, {"source_module": "Unknown"},
])
def test_invalid_association_never_changes_persisted_settings(tmp_path, bad_change):
    services = _project(tmp_path)
    config = services.configuration_service
    before = config.current_project.settings_path.read_bytes()
    rule = {**_rule(), **bad_change}
    with pytest.raises(ValueError):
        config.save_result_associations([rule], notify=False)
    assert config.current_project.settings_path.read_bytes() == before


def test_stale_association_settings_do_not_overwrite_newer_changes(tmp_path):
    services = _project(tmp_path)
    config = services.configuration_service
    token = config.capture_settings_state()
    config.add_constant("root", "/changed")
    with pytest.raises(Exception, match="changed|失效"):
        config.save_result_associations([_rule()], expected_state=token, notify=False)
    assert "result_associations" not in services.project_service.settings


def test_existing_association_prevents_deleting_its_key_before_list_write(tmp_path):
    services = _project(tmp_path)
    config = services.configuration_service
    config.save_result_associations([_rule()], notify=False)
    before = config.subjects()
    with pytest.raises(ValueError, match="关联|association"):
        config.delete_subject_columns(("subses",), notify=False)
    pd.testing.assert_frame_equal(config.subjects(), before)


def test_self_association_does_not_duplicate_local_record_actions(tmp_path):
    services = _project(tmp_path)
    config = services.configuration_service
    rule = _rule()
    rule["source_filter"] = rule["target_filter"] = None
    config.save_result_associations([rule], notify=False)
    snapshot = services.project_context_service.snapshot()
    context = services.project_context_service.qc_row_context(snapshot, "S01_anat")
    keys = [record.key for record in context.records + context.linked_records]
    assert len(keys) == len(set(keys))


def test_projection_updates_from_original_rating_and_rules_can_be_removed(tmp_path):
    services = _project(tmp_path)
    config = services.configuration_service
    rule = _rule()
    reverse = deepcopy(rule)
    reverse.update(rule_id="anat_to_rest", name="Anat scores")
    reverse["source_filter"], reverse["target_filter"] = rule["target_filter"], rule["source_filter"]
    reverse["fields"] = [["score1", "linked.anat"]]
    config.save_result_associations([rule, reverse], notify=False)
    context = services.project_context_service
    before = context.snapshot()
    workflow = context.create_qc_workflow(before, module_name="RestQC", initial_easyqcid="S01_rest01")
    workflow.set_score("1", "Poor")
    workflow.save()
    after = _frame(context.snapshot().table_view_service)
    assert "S01_rest01: Poor" in after.loc[0, "linked.rest"]
    assert after.loc[1, "linked.anat"] == "S01_anat: Fair"
    assert "linked.anat" not in config.subjects()
    config.save_result_associations([], notify=False)
    config.delete_subject_columns(("subses",), notify=False)
    final = _frame(context.snapshot().table_view_service)
    assert "linked.rest" not in final and "linked.anat" not in final


@pytest.mark.parametrize("output", ["master.score1", "master.master.notes"])
def test_outputs_cannot_occupy_long_table_reserved_namespace(tmp_path, output):
    services = _project(tmp_path)
    config = services.configuration_service
    subjects = config.subjects()
    subjects["score1"] = "ordinary metadata"
    config.replace_subjects(subjects, notify=False)
    rule = _rule()
    rule["fields"] = [["score1", output]]
    before = config.current_project.settings_path.read_bytes()
    with pytest.raises(ValueError, match="reserved|重名"):
        config.save_result_associations([rule], notify=False)
    assert config.current_project.settings_path.read_bytes() == before


class _Commands:
    def __init__(self):
        self.commands = []
        self.closed = False

    def render_command_plan(self, template, variables):
        command = template.replace("{image}", variables["image"])
        return command, {0: command}

    def start_commands(self, commands, **kwargs):
        self.commands.append((commands, kwargs))
        return []

    def close_current_processes(self):
        self.closed = True


def test_direct_command_uses_source_variables_without_shared_qc_executor_or_rating_write(tmp_path):
    services = _project(tmp_path)
    context_service = services.project_context_service
    snapshot = context_service.snapshot()
    executor = _Commands()
    context_service._command_executor = executor
    project = services.configuration_service.current_project
    before = {str(p): p.read_bytes() for p in (project.path / "RatingFiles").rglob("*.json")}
    context_service.launch_row_command(snapshot, "S01_rest02", "RestQC", rater_override="rater1")
    assert executor.commands[0][0] == {0: "freeview /synthetic/rest02"}
    assert executor.commands[0][1]["output_contexts"][0].easyqcid == "S01_rest02"
    assert executor.commands[0][1]["shell"] is True
    assert not executor.closed
    assert services.code_executor.current_processes == []
    assert {str(p): p.read_bytes() for p in (project.path / "RatingFiles").rglob("*.json")} == before


def test_direct_command_rejects_stale_project_and_module_filter(tmp_path):
    services = _project(tmp_path)
    svc = services.project_context_service
    executor = _Commands()
    svc._command_executor = executor
    services.configuration_service.save_module_filter("RestQC", _filter_expression("kind", "==", "func"))
    snapshot = svc.snapshot()
    with pytest.raises((ValueError, ProjectContextError)):
        svc.launch_row_command(snapshot, "S01_anat", "RestQC")
    services.configuration_service.create_project("Other", tmp_path)
    svc.snapshot()
    with pytest.raises(ProjectContextError):
        svc.launch_row_command(snapshot, "S01_rest02", "RestQC")
    assert not executor.commands
