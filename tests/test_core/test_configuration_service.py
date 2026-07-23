from __future__ import annotations

import json

import pandas as pd
import pytest

from core.configuration_service import ConfigurationError, ConfigurationService
from core.project_service import ProjectService
from core.table_service import TableService
from utils.file_utils import FileUtils


def _service(tmp_path):
    projects = ProjectService(tmp_path / "projects.json")
    service = ConfigurationService(projects, TableService())
    service.create_project("SAMPLE", tmp_path)
    return service, projects


def _subjects():
    return pd.DataFrame(
        {"ezqcid": ["SUB001", "SUB002"], "site": ["A", "B"], "age": [29, 31]}
    )


def test_project_and_subject_configuration_use_temporary_atomic_files(tmp_path) -> None:
    service, projects = _service(tmp_path)

    service.replace_subjects(_subjects())

    result = service.subjects()
    pd.testing.assert_frame_equal(result, _subjects())
    assert projects.current_project.name == "SAMPLE"
    assert projects.current_project.settings_path.exists()
    assert (projects.current_project.table_dir / "ezqc_all.csv").exists()


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame({"ezqcid": ["SUB001", "SUB001"]}),
        pd.DataFrame({"ezqcid": ["SUB001", " "]}),
        pd.DataFrame({"subject": ["SUB001"]}),
    ],
)
def test_subject_configuration_rejects_unsafe_identity(tmp_path, frame) -> None:
    service, _ = _service(tmp_path)

    with pytest.raises(ConfigurationError):
        service.replace_subjects(frame)


def test_subject_merge_and_constant_column_collisions_fail_loud(tmp_path) -> None:
    service, _ = _service(tmp_path)
    service.replace_subjects(_subjects())
    service.set_constant("DATA_ROOT", "/data")

    with pytest.raises(ConfigurationError, match="overlap"):
        service.merge_subjects(
            pd.DataFrame({"ezqcid": ["SUB001"], "site": ["changed"]}),
            mode="columns",
        )
    with pytest.raises(ConfigurationError, match="column"):
        service.set_constant("site", "bad")
    with pytest.raises(ConfigurationError, match="constant"):
        service.replace_subjects(
            pd.DataFrame({"ezqcid": ["SUB001"], "DATA_ROOT": ["shadow"]})
        )


def test_failed_settings_commit_restores_in_memory_and_disk(monkeypatch, tmp_path) -> None:
    service, projects = _service(tmp_path)
    before = projects.current_project.settings_path.read_text(encoding="utf-8")
    real_save = FileUtils.safe_json_save

    def fail_settings(path, data, indent=4):
        if path == projects.current_project.settings_path:
            raise OSError("settings replace failed")
        return real_save(path, data, indent)

    monkeypatch.setattr(FileUtils, "safe_json_save", fail_settings)

    with pytest.raises(OSError, match="settings replace failed"):
        service.set_constant("DATA_ROOT", "/data")
    assert "DATA_ROOT" not in service.constants()
    assert projects.current_project.settings_path.read_text(encoding="utf-8") == before


def test_module_add_move_import_collision_and_atomic_export(tmp_path) -> None:
    service, _ = _service(tmp_path)
    service.add_module("AnatQC", "Anatomical QC")
    service.add_module("FuncQC", "Functional QC")

    assert service.move_module("FuncQC", -1)
    assert [module.name for module in service.modules()][1] == "FuncQC"

    imported = {
        "name": "ImportedQC",
        "label": "Imported QC",
        "rater": "external",
        "ezqcid": "SUB999",
        "watch_mode": False,
        "scores": {"1": {"label": "Quality", "num": "A,B", "num_": "A,B", "value": "A"}},
        "tags": {"1": {"label": "Artifact", "value": True}},
        "code": "freeview {image}",
        "interper": "shell",
        "control": True,
        "select_filter": None,
        "showing": True,
        "code_exe": {"0": "old"},
        "time": "2026-01-01 00:00:00",
        "notes": "old",
        "button": {},
    }
    service.import_module_payload(imported)
    module = next(item for item in service.modules() if item.name == "ImportedQC")
    assert module.ezqcid is None
    assert module.notes is None
    assert module.scores["1"].value is None
    assert module.tags["1"].value is False

    with pytest.raises(ConfigurationError, match="already exists"):
        service.import_module_payload(imported)

    output = tmp_path / "exports" / "module.json"
    service.export_module("ImportedQC", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["rater"] is None
    assert payload["ezqcid"] is None


def test_remove_project_only_unregisters_it(tmp_path) -> None:
    service, projects = _service(tmp_path)
    project_path = projects.current_project.path

    service.remove_project("SAMPLE")

    assert service.projects() == ()
    assert project_path.exists()


def test_project_entries_are_detached_and_include_path_and_open_state(tmp_path) -> None:
    service, projects = _service(tmp_path)
    service.create_project("SECOND", tmp_path)
    service.load_project("SAMPLE")

    entries = service.project_entries()

    assert tuple(entry.name for entry in entries) == ("SAMPLE", "SECOND")
    assert entries[0].path == projects.registry.projects["SAMPLE"].path
    assert entries[0].is_current
    assert entries[0].is_most_recent
    assert not entries[1].is_current
    assert not entries[1].is_most_recent
    assert service.current_project.name == "SAMPLE"
    assert service.projects() == ("SAMPLE", "SECOND")


def test_configuration_snapshot_is_detached_from_authoritative_subjects(tmp_path) -> None:
    service, _ = _service(tmp_path)
    service.replace_subjects(_subjects())

    snapshot = service.snapshot()
    snapshot.subjects.loc[0, "site"] = "changed-only-in-snapshot"

    assert snapshot.current_project_name == "SAMPLE"
    assert snapshot.projects == ("SAMPLE",)
    assert len(snapshot.modules) == 1
    assert service.subjects().loc[0, "site"] == "A"
