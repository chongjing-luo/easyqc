"""Failure-atomicity contract for project create/remove lifecycle operations."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from core.event_bus import Event, EventType
from core.module_repository import ModuleRepository, ModuleRepositoryError
from core.project_service import ProjectService
from utils.file_utils import FileUtils


def _tree_snapshot(root: Path) -> dict[str, bytes | None]:
    """Capture directory entries and file bytes without following symlinks."""

    return {
        path.relative_to(root).as_posix(): None if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


def _collect_service_events(service: ProjectService) -> list[Event]:
    received: list[Event] = []
    for event_type in EventType:
        service.event_bus.subscribe(event_type, received.append)
    return received


def _inject_create_failure(
    monkeypatch: pytest.MonkeyPatch,
    service: ProjectService,
    boundary: str,
) -> None:
    if boundary == "registry":
        def fail_registry_write() -> None:
            raise OSError("registry publication failed")

        monkeypatch.setattr(service, "_save_registry", fail_registry_write)
        return

    if boundary == "settings":
        real_save = FileUtils.safe_json_save

        def fail_settings_write(path, payload, *args, **kwargs) -> None:
            if Path(path).name == "settings_BETA.json":
                raise OSError("settings publication failed")
            real_save(path, payload, *args, **kwargs)

        monkeypatch.setattr(FileUtils, "safe_json_save", fail_settings_write)
        return

    if boundary == "module":
        def fail_module_write(self, modules) -> None:
            raise ModuleRepositoryError("module publication failed")

        monkeypatch.setattr(
            ModuleRepository,
            "_replace_settings_unlocked",
            fail_module_write,
        )
        return

    raise AssertionError(f"unknown failure boundary: {boundary}")


@pytest.mark.parametrize(
    ("boundary", "expected_exception"),
    [
        pytest.param("registry", OSError, id="registry-write"),
        pytest.param("settings", OSError, id="settings-write"),
        pytest.param("module", ValueError, id="module-write"),
    ],
)
def test_create_failure_restores_previous_state_and_removes_new_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
    expected_exception: type[Exception],
) -> None:
    registry_path = tmp_path / "projects.json"
    service = ProjectService(registry_path)
    previous_project = service.create("ALPHA", tmp_path)
    previous_settings = deepcopy(dict(service.settings))
    previous_registry = deepcopy(service.registry.to_legacy_dict())
    previous_registry_bytes = registry_path.read_bytes()
    previous_project_tree = _tree_snapshot(previous_project.path)
    received = _collect_service_events(service)
    failed_target = tmp_path / "easyqc_BETA"
    _inject_create_failure(monkeypatch, service, boundary)

    with pytest.raises(expected_exception, match=f"{boundary} publication failed"):
        service.create("BETA", failed_target)

    assert service.current_project is previous_project
    assert dict(service.settings) == previous_settings
    assert service.registry.to_legacy_dict() == previous_registry
    assert registry_path.read_bytes() == previous_registry_bytes
    assert _tree_snapshot(previous_project.path) == previous_project_tree
    assert "BETA" not in service.list_all()
    assert not failed_target.exists()
    assert received == []


def test_create_failure_preserves_caller_owned_empty_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "projects.json"
    service = ProjectService(registry_path)
    target = tmp_path / "easyqc_SAMPLE"
    target.mkdir()
    received = _collect_service_events(service)
    _inject_create_failure(monkeypatch, service, "registry")

    with pytest.raises(OSError, match="registry publication failed"):
        service.create("SAMPLE", target)

    assert target.is_dir()
    assert list(target.iterdir()) == []
    assert service.current_project is None
    assert dict(service.settings) == {}
    assert service.registry.to_legacy_dict() == {
        "projects": {},
        "last_project": None,
    }
    assert not registry_path.exists()
    assert received == []


@pytest.mark.parametrize("foreign_parent", [".", "Table", "modules"])
@pytest.mark.parametrize("boundary", ["registry", "settings"])
def test_create_rollback_preserves_unexpected_files_and_reports_incomplete_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    foreign_parent: str,
    boundary: str,
) -> None:
    service = ProjectService(tmp_path / "projects.json")
    target = tmp_path / "easyqc_SAMPLE"
    foreign_file = target / foreign_parent / "user-notes.txt"
    received = _collect_service_events(service)

    def add_foreign_file_and_fail() -> None:
        foreign_file.parent.mkdir(parents=True, exist_ok=True)
        foreign_file.write_text("must survive rollback", encoding="utf-8")
        raise OSError(f"{boundary} publication failed")

    if boundary == "registry":
        monkeypatch.setattr(service, "_save_registry", add_foreign_file_and_fail)
    else:
        real_save = FileUtils.safe_json_save

        def fail_settings_write(path, payload, *args, **kwargs) -> None:
            if Path(path) == target / "settings_SAMPLE.json":
                add_foreign_file_and_fail()
            real_save(path, payload, *args, **kwargs)

        monkeypatch.setattr(FileUtils, "safe_json_save", fail_settings_write)

    with pytest.raises(RuntimeError, match="rollback incomplete"):
        service.create("SAMPLE", target)

    assert foreign_file.read_text(encoding="utf-8") == "must survive rollback"
    assert service.current_project is None
    assert dict(service.settings) == {}
    assert service.registry.projects == {}
    assert not service.registry_path.exists()
    assert received == []


@pytest.mark.parametrize("operation", ["create", "initial_create", "remove"])
def test_registry_post_publication_error_restores_durable_and_memory_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    service = ProjectService(tmp_path / "projects.json")
    if operation != "initial_create":
        service.create("ALPHA", tmp_path)
    previous_current = service.current_project
    previous_settings = deepcopy(dict(service.settings))
    previous_registry = deepcopy(service.registry.to_legacy_dict())
    previous_tree = _tree_snapshot(tmp_path)
    received = _collect_service_events(service)
    real_save = service._save_registry

    def publish_then_fail() -> None:
        real_save()
        raise OSError("failure after registry publication")

    monkeypatch.setattr(service, "_save_registry", publish_then_fail)

    with pytest.raises(OSError, match="after registry publication"):
        if operation == "remove":
            service.remove("ALPHA")
        else:
            service.create("BETA", tmp_path)

    assert service.current_project is previous_current
    assert dict(service.settings) == previous_settings
    assert service.registry.to_legacy_dict() == previous_registry
    assert _tree_snapshot(tmp_path) == previous_tree
    assert received == []


def test_remove_registry_failure_restores_state_and_emits_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path = tmp_path / "projects.json"
    service = ProjectService(registry_path)
    service.create("ALPHA", tmp_path)
    current_project = service.create("BETA", tmp_path)
    previous_settings = deepcopy(dict(service.settings))
    previous_registry = deepcopy(service.registry.to_legacy_dict())
    previous_registry_bytes = registry_path.read_bytes()
    previous_project_tree = _tree_snapshot(current_project.path)
    received = _collect_service_events(service)

    def fail_registry_write() -> None:
        raise OSError("registry publication failed")

    monkeypatch.setattr(service, "_save_registry", fail_registry_write)

    with pytest.raises(OSError, match="registry publication failed"):
        service.remove("BETA")

    assert service.current_project is current_project
    assert dict(service.settings) == previous_settings
    assert service.registry.to_legacy_dict() == previous_registry
    assert registry_path.read_bytes() == previous_registry_bytes
    assert _tree_snapshot(current_project.path) == previous_project_tree
    assert service.list_all() == ["ALPHA", "BETA"]
    assert received == []


def test_successful_create_and_remove_publish_complete_lifecycle_state(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "projects.json"
    service = ProjectService(registry_path)
    received = _collect_service_events(service)

    project = service.create("SAMPLE", tmp_path)

    repository = ModuleRepository(project.path / "modules", scope="project")
    snapshot = repository.snapshot()
    assert project.settings_path.is_file()
    assert snapshot.errors == ()
    assert [record.module.name for record in snapshot.records] == ["example"]
    assert json.loads(registry_path.read_text(encoding="utf-8")) == {
        "projects": {"SAMPLE": str(project.path)},
        "last_project": "SAMPLE",
    }
    assert [event.type for event in received] == [
        EventType.SETTINGS_SAVED,
        EventType.PROJECT_CHANGED,
    ]

    service.remove("SAMPLE")

    assert service.current_project is None
    assert dict(service.settings) == {}
    assert service.list_all() == []
    assert project.path.is_dir()
    assert json.loads(registry_path.read_text(encoding="utf-8")) == {
        "projects": {},
        "last_project": None,
    }
    assert [event.type for event in received] == [
        EventType.SETTINGS_SAVED,
        EventType.PROJECT_CHANGED,
        EventType.PROJECT_CHANGED,
    ]
