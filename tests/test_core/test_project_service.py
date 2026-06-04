import json

import pytest

from core.project_service import ProjectService


def test_project_service_creates_project_registry_and_default_settings(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")

    project = service.create("SAMPLE", tmp_path)

    assert project.path == tmp_path / "easyqc_SAMPLE"
    assert project.settings_path.exists()
    assert json.loads((tmp_path / "projects.json").read_text(encoding="utf-8")) == {
        "projects": {"SAMPLE": str(tmp_path / "easyqc_SAMPLE")},
        "last_project": "SAMPLE",
    }
    assert service.list_all() == ["SAMPLE"]
    assert service.get_modules()["1"].name == "example"


def test_project_service_loads_existing_project_without_recursion(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    loaded_service = ProjectService(tmp_path / "projects.json")

    project = loaded_service.load("SAMPLE")

    assert project.name == "SAMPLE"
    assert loaded_service.current_project == project
    assert loaded_service.settings["qcmodule"]["1"]["name"] == "example"


def test_project_service_reloads_registry_without_recreating_service(tmp_path) -> None:
    registry_path = tmp_path / "projects.json"
    stale_service = ProjectService(registry_path)
    writer_service = ProjectService(registry_path)
    writer_service.create("SAMPLE", tmp_path)

    assert stale_service.list_all() == []

    stale_service.reload_registry()
    project = stale_service.load("SAMPLE")

    assert stale_service.list_all() == ["SAMPLE"]
    assert project.path == tmp_path / "easyqc_SAMPLE"

    writer_service.remove("SAMPLE")
    stale_service.reload_registry()

    assert stale_service.current_project is None
    assert stale_service.settings == {}


def test_project_service_rejects_duplicate_and_invalid_names(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)

    with pytest.raises(ValueError):
        service.create("SAMPLE", tmp_path)
    with pytest.raises(ValueError):
        service.create("../bad", tmp_path)


def test_project_service_add_update_remove_module(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)

    module = service.add_module("t1_qc", "T1 QC", index=1)
    service.update_module("t1_qc", label="T1 Quality", rater="rater1", control=True)
    modules = service.get_modules()

    assert module.name == "t1_qc"
    assert modules["1"].name == "t1_qc"
    assert modules["1"].label == "T1 Quality"
    assert modules["1"].rater == "rater1"
    assert modules["2"].name == "example"

    service.remove_module("example")
    assert list(service.get_modules()) == ["1"]
    assert service.get_modules()["1"].name == "t1_qc"


def test_project_service_prevents_removing_last_module(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)

    with pytest.raises(ValueError):
        service.remove_module("example")


def test_project_service_observer_receives_events(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    events = []
    service.add_observer(events.append)

    service.create("SAMPLE", tmp_path)
    service.add_module("t1_qc", "T1 QC")

    assert events == ["project_changed", "modules_changed"]


def test_project_service_remove_only_updates_registry(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    project = service.create("SAMPLE", tmp_path)

    service.remove("SAMPLE")

    assert service.list_all() == []
    assert project.path.exists()
    assert json.loads((tmp_path / "projects.json").read_text(encoding="utf-8")) == {
        "projects": {},
        "last_project": None,
    }
