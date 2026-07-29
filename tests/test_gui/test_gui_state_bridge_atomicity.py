from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from core.configuration_service import ConfigurationError
from core.project_service import ProjectService
from core.session_state import SessionState
from core.table_service import TableService
from gui.state_bridge import GUIStateBridge
from utils.file_utils import FileUtils


def _bridge(tmp_path: Path) -> tuple[GUIStateBridge, ProjectService]:
    projects = ProjectService(tmp_path / "projects.json")
    projects.create("SAMPLE", tmp_path)
    return (
        GUIStateBridge(
            projects,
            session_state=SessionState(),
            table_service=TableService(),
        ),
        projects,
    )


def _fail_settings_write(monkeypatch, project_service: ProjectService) -> None:
    project = project_service.current_project
    assert project is not None
    original = FileUtils.safe_json_save

    def fail(path, data):
        if Path(path) == project.settings_path:
            raise OSError("synthetic settings write failure")
        return original(path, data)

    monkeypatch.setattr(FileUtils, "safe_json_save", fail)


@pytest.mark.parametrize(
    "operation",
    [
        lambda bridge: bridge.add_module("AnatQC", "Anatomical", "2"),
        lambda bridge: bridge.modify_module("1", "RenamedQC", "Renamed", "1"),
        lambda bridge: bridge.insert_module(
            "2",
            bridge.project_service.default_module(
                "ImportedQC",
                "Imported",
            ).to_legacy_dict(),
        ),
    ],
    ids=["add", "modify", "import"],
)
def test_tk_module_write_failure_does_not_pollute_memory_or_disk(
    operation,
    monkeypatch,
    tmp_path,
) -> None:
    bridge, projects = _bridge(tmp_path)
    before_memory = deepcopy(dict(projects.settings))
    project = projects.current_project
    assert project is not None
    before_disk = FileUtils.safe_json_load(project.settings_path)
    _fail_settings_write(monkeypatch, projects)

    with pytest.raises(OSError, match="synthetic"):
        operation(bridge)

    assert dict(projects.settings) == before_memory
    assert FileUtils.safe_json_load(project.settings_path) == before_disk


def test_tk_module_delete_is_atomic_and_persistent(monkeypatch, tmp_path) -> None:
    bridge, projects = _bridge(tmp_path)
    bridge.add_module("AnatQC", "Anatomical", "2")
    before_memory = deepcopy(dict(projects.settings))
    project = projects.current_project
    assert project is not None
    before_disk = FileUtils.safe_json_load(project.settings_path)
    _fail_settings_write(monkeypatch, projects)

    with pytest.raises(OSError, match="synthetic"):
        bridge.delete_module("2")

    assert dict(projects.settings) == before_memory
    assert FileUtils.safe_json_load(project.settings_path) == before_disk


def test_tk_module_modify_commits_rename_and_position_once(tmp_path) -> None:
    bridge, projects = _bridge(tmp_path)
    bridge.add_module("AnatQC", "Anatomical", "2")

    bridge.modify_module("2", "RenamedQC", "Renamed", "1")

    assert [
        module["name"]
        for module in projects.settings["qcmodule"].values()
    ] == ["RenamedQC", "example"]
    reloaded = ProjectService(tmp_path / "projects.json")
    reloaded.load("SAMPLE")
    assert [
        module["name"]
        for module in reloaded.settings["qcmodule"].values()
    ] == ["RenamedQC", "example"]


def test_tk_master_list_rejects_invalid_candidate_without_memory_mutation(
    tmp_path,
) -> None:
    bridge, _ = _bridge(tmp_path)
    original = pd.DataFrame({"easyqcid": ["001", "NA"], "site": ["A", "B"]})
    bridge.set_all_variable_table(original)

    with pytest.raises(ConfigurationError, match="case"):
        bridge.set_all_variable_table(
            pd.DataFrame({"easyqcid": ["SUB001", "sub001"]})
        )

    pd.testing.assert_frame_equal(bridge.all_variable_table(), original)


def test_tk_row_merge_rejects_duplicate_identity_without_memory_mutation(
    tmp_path,
) -> None:
    bridge, _ = _bridge(tmp_path)
    original = pd.DataFrame({"easyqcid": ["SUB001"], "site": ["A"]})
    bridge.set_all_variable_table(original)

    with pytest.raises(ConfigurationError, match="重复 easyqcid"):
        bridge.merge_all_variables_as_rows(
            pd.DataFrame({"easyqcid": ["SUB001"], "site": ["B"]})
        )

    pd.testing.assert_frame_equal(bridge.all_variable_table(), original)
