from copy import deepcopy
import json
import shutil

import pytest

from core.module_repository import ModuleRepository
from core.project_service import ProjectService
from models.project import Project
from utils.file_utils import FileUtils


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


def test_project_create_publishes_one_file_per_module(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")

    project = service.create("SAMPLE", tmp_path)

    repository = ModuleRepository(project.path / "modules", scope="project")
    snapshot = repository.snapshot()
    assert snapshot.errors == ()
    assert [record.module.name for record in snapshot.records] == ["example"]
    assert len(list(repository.root.glob("*.json"))) == 1


def test_project_load_rejects_schema_v3_project_without_module_directory(
    tmp_path,
) -> None:
    registry = tmp_path / "projects.json"
    creator = ProjectService(registry)
    project = creator.create("SAMPLE", tmp_path)
    shutil.rmtree(project.path / "modules")
    settings_before = project.settings_path.read_bytes()

    loader = ProjectService(registry)
    with pytest.raises(ValueError, match="缺少模块目录"):
        loader.load("SAMPLE")

    assert loader.current_project is None
    assert project.settings_path.read_bytes() == settings_before
    assert not (project.path / "modules").exists()


def test_project_settings_commit_updates_module_files_and_legacy_snapshot(
    tmp_path,
) -> None:
    service = ProjectService(tmp_path / "projects.json")
    project = service.create("SAMPLE", tmp_path)
    candidate = deepcopy(dict(service.settings))
    candidate["qcmodule"]["1"]["label"] = "Updated label"

    service.commit_settings(candidate, notify=False)

    repository = ModuleRepository(project.path / "modules", scope="project")
    assert repository.snapshot().records[0].module.label == "Updated label"
    stored = json.loads(project.settings_path.read_text(encoding="utf-8"))
    assert stored["qcmodule"]["1"]["label"] == "Updated label"


def test_project_settings_reject_case_only_module_duplicate_before_writing(
    tmp_path,
) -> None:
    service = ProjectService(tmp_path / "projects.json")
    project = service.create("SAMPLE", tmp_path)
    candidate = deepcopy(dict(service.settings))
    duplicate = deepcopy(candidate["qcmodule"]["1"])
    duplicate["name"] = "Example"
    candidate["qcmodule"]["2"] = duplicate
    registry_before = (tmp_path / "projects.json").read_bytes()
    settings_before = project.settings_path.read_bytes()
    modules_before = {
        path.name: path.read_bytes()
        for path in sorted((project.path / "modules").glob("*.json"))
    }

    with pytest.raises(ValueError, match="模块名称不能重复"):
        service.commit_settings(candidate, notify=False)

    assert (tmp_path / "projects.json").read_bytes() == registry_before
    assert project.settings_path.read_bytes() == settings_before
    assert {
        path.name: path.read_bytes()
        for path in sorted((project.path / "modules").glob("*.json"))
    } == modules_before


def test_project_settings_failure_restores_module_files_and_memory(
    tmp_path,
    monkeypatch,
) -> None:
    service = ProjectService(tmp_path / "projects.json")
    project = service.create("SAMPLE", tmp_path)
    repository = ModuleRepository(project.path / "modules", scope="project")
    before_record = repository.snapshot().records[0]
    before_settings = deepcopy(dict(service.settings))
    before_bytes = project.settings_path.read_bytes()
    candidate = deepcopy(before_settings)
    candidate["qcmodule"]["1"]["label"] = "Must roll back"
    real_save = FileUtils.safe_json_save

    def fail_settings(path, payload, *args, **kwargs):
        if path == project.settings_path:
            raise OSError("settings publication failed")
        return real_save(path, payload, *args, **kwargs)

    monkeypatch.setattr(FileUtils, "safe_json_save", fail_settings)

    with pytest.raises(OSError, match="settings publication failed"):
        service.commit_settings(candidate, notify=False)

    restored = repository.snapshot()
    assert restored.errors == ()
    assert restored.records[0].module_id == before_record.module_id
    assert restored.records[0].module.label == before_record.module.label
    assert dict(service.settings) == before_settings
    assert project.settings_path.read_bytes() == before_bytes


def test_loading_durable_last_project_does_not_rewrite_registry(
    tmp_path,
    monkeypatch,
) -> None:
    registry = tmp_path / "projects.json"
    creator = ProjectService(registry)
    creator.create("SAMPLE", tmp_path)
    loaded_service = ProjectService(registry)

    def fail_redundant_write() -> None:
        raise AssertionError("durable last_project must not be rewritten")

    monkeypatch.setattr(loaded_service, "_save_registry", fail_redundant_write)

    project = loaded_service.load("SAMPLE")

    assert project.name == "SAMPLE"
    assert loaded_service.current_project == project


def test_switch_load_rolls_back_when_last_project_cannot_be_persisted(
    tmp_path,
    monkeypatch,
) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("ALPHA", tmp_path)
    service.create("BETA", tmp_path)
    beta = service.current_project
    beta_settings = dict(service.settings)

    def fail_registry_write() -> None:
        raise OSError("registry is read-only")

    monkeypatch.setattr(service, "_save_registry", fail_registry_write)

    with pytest.raises(OSError, match="registry is read-only"):
        service.load("ALPHA")

    assert service.current_project == beta
    assert service.registry.last_project == "BETA"
    assert dict(service.settings) == beta_settings


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


def test_project_create_rejects_nonempty_target_without_mutation(tmp_path) -> None:
    registry_path = tmp_path / "projects.json"
    target = tmp_path / "easyqc_SAMPLE"
    target.mkdir()
    sentinel = target / "keep-me.txt"
    sentinel.write_bytes(b"user data")
    service = ProjectService(registry_path)

    with pytest.raises(ValueError, match="非空"):
        service.create("SAMPLE", target)

    assert sentinel.read_bytes() == b"user data"
    assert sorted(path.name for path in target.iterdir()) == ["keep-me.txt"]
    assert not registry_path.exists()
    assert service.list_all() == []
    assert service.current_project is None


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


def test_project_service_update_module_allows_interper(tmp_path) -> None:
    """P1-A / F-MOD-4: module-config keys need a sanctioned mutation path.
    ``interper`` (viewer interpreter type) is module config, so it must be
    updatable; otherwise the GUI back-door mutates the raw dict (ADR-002)."""
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)

    service.update_module("example", interper="python")

    module = service.get_modules()["1"]
    assert module.interper == "python"


def test_project_service_update_module_rejects_runtime_keys(tmp_path) -> None:
    """P1-A / F-MOD-4: keys that are rating/runtime state (not module config)
    MUST be rejected by ``update_module`` with a ValueError naming the key, so
    callers are forced to use the correct path (RatingService for scores, the
    QC page controller for runtime state). This prevents accidental persistence
    of per-subject state into the settings schema."""
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)

    # easyqcid/code_exe/notes/time are per-subject rating runtime state
    with pytest.raises(ValueError) as exc_easyqcid:
        service.update_module("example", easyqcid="SUB001")
    assert "easyqcid" in str(exc_easyqcid.value)

    with pytest.raises(ValueError) as exc_notes:
        service.update_module("example", notes="hello")
    assert "notes" in str(exc_notes.value)

    with pytest.raises(ValueError) as exc_scores:
        service.update_module("example", scores={"1": {}})
    assert "scores" in str(exc_scores.value)

    with pytest.raises(ValueError) as exc_time:
        service.update_module("example", time="2024-01-01 00:00:00")
    assert "time" in str(exc_time.value)


def test_project_service_new_settings_uses_schema_version_key(tmp_path) -> None:
    """The seed settings use the sole supported schema-v3 contract."""
    service = ProjectService(tmp_path / "projects.json")
    settings = service.new_settings()

    assert settings["schema_version"] == 3
    assert "version" not in settings


def test_project_service_save_writes_schema_version(tmp_path) -> None:
    """P0-E / AC-11: an explicitly-saved settings file carries schema_version."""
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    service.save()

    saved = json.loads(service.current.settings_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 3


def test_project_service_rejects_settings_without_schema_v3(tmp_path) -> None:
    project_dir = tmp_path / "easyqc_OLD"
    (project_dir / "Table").mkdir(parents=True)
    (project_dir / "RatingFiles").mkdir()
    old_settings = {
        "constants": {}, "variables": {}, "var_select_filter": None,
        "select_filter": None,
        "qcmodule": {"1": {"name": "m", "label": "m", "rater": None,
                           "scores": {}, "tags": {}, "code": None,
                           "code_exe": None, "notes": None, "time": None,
                           "interper": "shell", "control": False,
                           "showing": True, "select_filter": None, "button": {}}},
    }
    settings_path = project_dir / "settings_OLD.json"
    settings_path.write_text(json.dumps(old_settings), encoding="utf-8")
    bytes_before = settings_path.read_bytes()

    registry_path = tmp_path / "projects.json"
    service = ProjectService(registry_path)
    service.registry.projects["OLD"] = Project("OLD", project_dir)
    service._save_registry()

    with pytest.raises(ValueError, match="schema_version 3"):
        service.load("OLD")

    assert service.current_project is None
    assert settings_path.read_bytes() == bytes_before


def test_project_service_save_rejects_non_v3_schema(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    settings_path = service.current.settings_path
    bytes_before = settings_path.read_bytes()
    service._settings["schema_version"] = 4

    with pytest.raises(ValueError, match="schema_version 3"):
        service.save()

    assert settings_path.read_bytes() == bytes_before


def test_project_service_observer_receives_events(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    events = []
    service.add_observer(events.append)

    service.create("SAMPLE", tmp_path)
    service.add_module("t1_qc", "T1 QC")

    assert events == ["project_changed", "modules_changed"]


def test_project_service_has_event_bus(tmp_path) -> None:
    """P1-C: ProjectService exposes a typed EventBus (AC-10)."""
    from core.event_bus import EventBus

    service = ProjectService(tmp_path / "projects.json")
    assert isinstance(service.event_bus, EventBus)


def test_project_service_injects_event_bus(tmp_path) -> None:
    """P1-C: an externally-supplied EventBus is reused (DI for GUI/testing)."""
    from core.event_bus import EventBus

    bus = EventBus()
    service = ProjectService(tmp_path / "projects.json", event_bus=bus)
    assert service.event_bus is bus


def test_project_service_emits_typed_project_changed(tmp_path) -> None:
    """P1-C: create/load/remove emit Event(PROJECT_CHANGED, source='ProjectService')."""
    from core.event_bus import Event, EventType

    service = ProjectService(tmp_path / "projects.json")
    received: list[Event] = []
    service.event_bus.subscribe(EventType.PROJECT_CHANGED, received.append)

    service.create("SAMPLE", tmp_path)

    assert len(received) == 1
    assert received[0].type is EventType.PROJECT_CHANGED
    assert received[0].source == "ProjectService"


def test_project_service_emits_typed_modules_changed(tmp_path) -> None:
    """P1-C: add_module/remove_module/update_module emit MODULES_CHANGED."""
    from core.event_bus import Event, EventType

    service = ProjectService(tmp_path / "projects.json")
    received: list[Event] = []
    service.event_bus.subscribe(EventType.MODULES_CHANGED, received.append)

    service.create("SAMPLE", tmp_path)
    received.clear()
    service.add_module("t1_qc", "T1 QC")

    assert len(received) == 1
    assert received[0].type is EventType.MODULES_CHANGED
    assert received[0].source == "ProjectService"


def test_project_service_emits_settings_saved(tmp_path) -> None:
    """P1-C: save emits SETTINGS_SAVED (new event the old string bus lacked)."""
    from core.event_bus import Event, EventType

    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    received: list[Event] = []
    service.event_bus.subscribe(EventType.SETTINGS_SAVED, received.append)

    service.save()

    assert len(received) == 1
    assert received[0].type is EventType.SETTINGS_SAVED


def test_project_service_legacy_observer_still_works(tmp_path) -> None:
    """P1-C: the deprecated add_observer API keeps working (transition bridge)
    so existing callers/tests do not break until P2 retires it."""
    service = ProjectService(tmp_path / "projects.json")
    events: list[str] = []
    service.add_observer(events.append)

    service.create("SAMPLE", tmp_path)

    # legacy string callback still fires (bridged to PROJECT_CHANGED)
    assert events == ["project_changed"]


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


# ---- P2-A1: ProjectService score/tag/constant CRUD (F-MOD-4 sanctioned paths) ----

def test_project_service_add_score_inserts_empty_score_and_reindexes(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    service.add_module("t1_qc", "T1 QC", index=1)

    service.add_score("t1_qc", index=2)  # insert at position 2

    modules = service.get_modules()
    module = next(m for m in modules.values() if m.name == "t1_qc")
    assert "1" in module.scores
    assert "2" in module.scores
    assert module.scores["2"].label is None  # empty shell


def test_project_service_delete_score_removes_and_reindexes(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    service.add_module("t1_qc", "T1 QC", index=1)
    service.add_score("t1_qc", index=1)

    modules = service.get_modules()
    module = next(m for m in modules.values() if m.name == "t1_qc")
    assert len(module.scores) == 2  # default "1" + added "1"(reindexed)

    service.delete_score("t1_qc", index=1)

    modules = service.get_modules()
    module = next(m for m in modules.values() if m.name == "t1_qc")
    assert len(module.scores) == 1


def test_project_service_add_tag_and_delete_tag(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    service.add_module("t1_qc", "T1 QC", index=1)

    service.add_tag("t1_qc", index=2)
    modules = service.get_modules()
    module = next(m for m in modules.values() if m.name == "t1_qc")
    assert "2" in module.tags

    service.delete_tag("t1_qc", index=2)
    modules = service.get_modules()
    module = next(m for m in modules.values() if m.name == "t1_qc")
    assert "2" not in module.tags


def test_project_service_update_score_fields(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    service.add_module("t1_qc", "T1 QC", index=1)

    service.update_score_fields("t1_qc", "1", label="overall", num="1-5", num_="1,2,3,4,5")

    modules = service.get_modules()
    module = next(m for m in modules.values() if m.name == "t1_qc")
    assert module.scores["1"].label == "overall"
    assert module.scores["1"].num_ == "1,2,3,4,5"


def test_project_service_update_tag_fields(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    service.add_module("t1_qc", "T1 QC", index=1)

    service.update_tag_fields("t1_qc", "1", label="needs_review")

    modules = service.get_modules()
    module = next(m for m in modules.values() if m.name == "t1_qc")
    assert module.tags["1"].label == "needs_review"


def test_project_service_set_get_rename_delete_constant(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)

    assert not service.has_constant("SUBJECTS_DIR")
    service.set_constant("SUBJECTS_DIR", "/data/fs")
    assert service.has_constant("SUBJECTS_DIR")
    assert dict(service.constant_items())["SUBJECTS_DIR"] == "/data/fs"

    service.rename_constant("SUBJECTS_DIR", "FS_DIR", "/data/fs")
    assert not service.has_constant("SUBJECTS_DIR")
    assert service.has_constant("FS_DIR")

    service.delete_constant("FS_DIR")
    assert not service.has_constant("FS_DIR")


def test_project_service_score_tag_constant_methods_emit_modules_changed(tmp_path) -> None:
    """P2-A1: the new sanctioned CRUD paths emit typed events so GUI observers
    refresh (AC-10), mirroring add_module/remove_module."""
    from core.event_bus import Event, EventType

    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    service.add_module("t1_qc", "T1 QC", index=1)
    received: list[Event] = []
    service.event_bus.subscribe(EventType.MODULES_CHANGED, received.append)
    received.clear()

    service.add_score("t1_qc", index=2)
    service.update_score_fields("t1_qc", "1", label="q")
    service.set_constant("K", "V")

    assert len(received) == 3
    assert all(e.type is EventType.MODULES_CHANGED for e in received)


# ---- P2-A gap: project lifecycle + module CRUD helpers for dialogs migration ----

def test_project_service_current_project_name(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    assert service.current_project_name() == "SAMPLE"


def test_project_service_has_project(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    assert service.has_project("SAMPLE")
    assert not service.has_project("OTHER")


def test_project_service_project_display_rows(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    rows = service.project_display_rows()
    assert ("SAMPLE",) == (rows[0][0],)  # first element is name
    assert len(rows) == 1


def test_project_service_module_name_exists(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    assert service.module_name_exists("example")
    assert not service.module_name_exists("nonexistent")


def test_project_service_module_name_exists_with_exclude(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    assert not service.module_name_exists("example", exclude_name="example")


def test_project_service_next_module_index(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    service.add_module("t1", "T1", index=2)
    assert service.next_module_index() == 3


def test_project_service_module_table_rows(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    rows = service.module_table_rows()
    assert len(rows) == 1
    assert rows[0][1] == "example"  # (index, name, label)


def test_project_service_can_delete_module(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    assert not service.can_delete_module()  # only 1 module
    service.add_module("t1", "T1", index=2)
    assert service.can_delete_module()


def test_project_service_delete_module_by_index(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    service.add_module("t1", "T1", index=2)
    service.delete_module(index=1)
    modules = service.get_modules()
    assert len(modules) == 1
    assert modules["1"].name == "t1"


def test_project_service_insert_module_with_dict(tmp_path) -> None:
    """insert_module accepts an external module dict (import path)."""
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    external = {"name": "imported", "label": "Imported", "rater": None,
                "scores": {}, "tags": {}, "code": None, "code_exe": None,
                "notes": None, "time": None, "interper": "shell",
                "control": False, "showing": True, "select_filter": None,
                "button": {}, "watch_mode": False, "easyqcid": None}
    service.insert_module(index=2, module=external)
    assert service.module_name_exists("imported")


def test_project_service_export_module(tmp_path) -> None:
    """export_module writes a sanitized copy (rater/easyqcid=None)."""
    import json
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    service.update_module("example", rater="r1")
    out = tmp_path / "exported.json"
    service.export_module("example", out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["name"] == "example"
    assert payload["rater"] is None  # sanitized
    assert payload["easyqcid"] is None


def test_project_service_module_index_by_name_returns_none_if_absent(tmp_path) -> None:
    service = ProjectService(tmp_path / "projects.json")
    service.create("SAMPLE", tmp_path)
    assert service.module_index_by_name("nonexistent") is None
    assert service.module_index_by_name("example") == "1"


def test_project_service_import_project_from_dir(tmp_path) -> None:
    """import_project_from_dir scans settings_*.json, registers, loads."""
    # create a project first, then import its dir into a fresh service
    s1 = ProjectService(tmp_path / "projects1.json")
    s1.create("SAMPLE", tmp_path)
    project_dir = s1.current_project.path

    s2 = ProjectService(tmp_path / "projects2.json")
    s2.import_project_from_dir(project_dir)
    assert s2.has_project("SAMPLE")
    assert s2.current_project_name() == "SAMPLE"


def test_project_service_import_invalid_dir_raises(tmp_path) -> None:
    import pytest
    s = ProjectService(tmp_path / "projects.json")
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        s.import_project_from_dir(empty_dir)


def test_project_service_import_rejects_ambiguous_settings_without_state_change(
    tmp_path,
) -> None:
    creator = ProjectService(tmp_path / "source-projects.json")
    source = creator.create("SOURCE", tmp_path / "source-root")
    (source.path / "settings_OTHER.json").write_bytes(
        source.settings_path.read_bytes()
    )

    service = ProjectService(tmp_path / "projects.json")
    active = service.create("ACTIVE", tmp_path / "active-root")
    registry_before = service.registry_path.read_bytes()
    settings_before = dict(service.settings)

    with pytest.raises(ValueError, match="唯一"):
        service.import_project_from_dir(source.path)

    assert service.registry_path.read_bytes() == registry_before
    assert service.current_project == active
    assert dict(service.settings) == settings_before
    assert service.list_all() == ["ACTIVE"]


def test_project_service_import_invalid_schema_rolls_back_registry_and_state(
    tmp_path,
) -> None:
    creator = ProjectService(tmp_path / "source-projects.json")
    source = creator.create("SOURCE", tmp_path / "source-root")
    invalid = json.loads(source.settings_path.read_text(encoding="utf-8"))
    invalid["schema_version"] = 2
    FileUtils.safe_json_save(source.settings_path, invalid)

    service = ProjectService(tmp_path / "projects.json")
    active = service.create("ACTIVE", tmp_path / "active-root")
    registry_before = service.registry_path.read_bytes()
    settings_before = dict(service.settings)

    with pytest.raises(ValueError, match="schema_version 3"):
        service.import_project_from_dir(source.path)

    assert service.registry_path.read_bytes() == registry_before
    assert service.current_project == active
    assert dict(service.settings) == settings_before
    assert service.list_all() == ["ACTIVE"]


def test_project_service_import_rejects_duplicate_name_without_rebinding(
    tmp_path,
) -> None:
    service = ProjectService(tmp_path / "projects.json")
    registered = service.create("SAMPLE", tmp_path / "registered-root")

    creator = ProjectService(tmp_path / "source-projects.json")
    candidate = creator.create("SAMPLE", tmp_path / "candidate-root")
    registry_before = service.registry_path.read_bytes()
    settings_before = dict(service.settings)

    with pytest.raises(ValueError, match="项目已存在"):
        service.import_project_from_dir(candidate.path)

    assert service.registry_path.read_bytes() == registry_before
    assert service.current_project == registered
    assert dict(service.settings) == settings_before
    assert service.registry.projects["SAMPLE"] == registered


def test_project_service_constants_returns_dict(tmp_path) -> None:
    s = ProjectService(tmp_path / "projects.json")
    s.create("SAMPLE", tmp_path)
    s.set_constant("K", "V")
    assert s.constants()["K"] == "V"
