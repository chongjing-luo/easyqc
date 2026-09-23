"""Independent RI-13 integration counterexamples using synthetic projects only."""

import pytest

from core.project_context_service import ProjectContextError
from tests.test_core.test_association_context import _Commands, _project, _rule
from tests.test_core.test_project_context_service import _filter_expression, _module_payload


@pytest.mark.parametrize("change", ["module_filter", "deleted_subject"])
def test_command_only_rejects_snapshot_invalidated_inside_the_same_project(tmp_path, change):
    services = _project(tmp_path)
    config = services.configuration_service
    context = services.project_context_service
    stale = context.snapshot()
    executor = _Commands()
    context._command_executor = executor
    if change == "module_filter":
        config.save_module_filter("RestQC", _filter_expression("kind", "==", "anat"))
    else:
        config.delete_subject_rows(("S01_rest02",), notify=False)
    context.snapshot()

    with pytest.raises(ProjectContextError, match="stale|失效|名单|current"):
        context.launch_row_command(stale, "S01_rest02", "RestQC")
    assert executor.commands == []


def test_association_source_module_navigation_defaults_to_read_only(tmp_path):
    services = _project(tmp_path)
    services.configuration_service.save_result_associations([_rule()], notify=False)
    snapshot = services.project_context_service.snapshot()

    menu = services.project_context_service.qc_row_context(snapshot, "S01_anat")

    assert menu.linked_modules
    assert all(entry.read_only for entry in menu.linked_modules)


def test_association_output_cannot_claim_a_configured_but_unrated_result_field(tmp_path):
    services = _project(tmp_path)
    config = services.configuration_service
    config.add_module("OtherQC", "Other QC")
    config.save_module(_module_payload(name="OtherQC"), original_name="OtherQC")
    rule = _rule()
    rule["fields"] = [["score1", "OtherQC.rater1.score1"]]
    before = config.current_project.settings_path.read_bytes()

    with pytest.raises(ValueError, match="重名|reserved|collision"):
        config.save_result_associations([rule], notify=False)

    assert config.current_project.settings_path.read_bytes() == before


def test_failed_independent_command_does_not_close_previous_uncontrolled_commands(tmp_path):
    services = _project(tmp_path)
    module = _module_payload(name="RestQC")
    module["control"] = False
    services.configuration_service.save_module(module, original_name="RestQC")
    context = services.project_context_service
    snapshot = context.snapshot()

    class FailingSecondLaunch(_Commands):
        def start_commands(self, commands, **kwargs):
            if self.commands:
                raise RuntimeError("synthetic launch failure")
            return super().start_commands(commands, **kwargs)

    executor = FailingSecondLaunch()
    context._command_executor = executor
    context.launch_row_command(snapshot, "S01_rest01", "RestQC")
    assert executor.commands[0][1]["control"] is False

    with pytest.raises(RuntimeError, match="synthetic launch failure"):
        context.launch_row_command(snapshot, "S01_rest02", "RestQC")

    assert not executor.closed
