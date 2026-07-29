from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path

import pytest

import core.module_repository as module_repository_module
from core.configuration_service import ConfigurationError, ConfigurationService
from core.module_repository import (
    ModuleRecord,
    ModuleRepository,
    ModuleRepositoryError,
)
from core.project_service import ProjectService
from core.project_template_service import ProjectTemplateService
from core.rating_identity import RatingIdentityError
from core.table_service import TableService
from core.template_service import TemplateService, TemplateServiceError
from models.qcmodule import QCModule
from utils.file_utils import FileUtils


def _module(name: str, *, rater: str | None = None) -> QCModule:
    return QCModule(name=name, label=name, rater=rater)


def _configuration(tmp_path) -> tuple[ConfigurationService, ProjectService]:
    project_service = ProjectService(tmp_path / "projects.json")
    configuration = ConfigurationService(project_service, TableService())
    configuration.create_project("SAMPLE", tmp_path)
    return configuration, project_service


def test_module_record_uses_portable_module_and_optional_rater_validation() -> None:
    accepted = ModuleRecord.create(
        _module("1_QC", rater="rater_1"),
        scope="project",
        display_order=10,
    )

    assert accepted.module.name == "1_QC"
    assert accepted.module.rater == "rater_1"

    for invalid_name in ("bad-name", "非ASCII", "a" * 33):
        with pytest.raises(RatingIdentityError, match="module_name"):
            ModuleRecord.create(
                _module(invalid_name),
                scope="project",
                display_order=10,
            )

    for invalid_rater in ("bad-rater", "评分者", "r" * 33):
        with pytest.raises(RatingIdentityError, match="rater"):
            ModuleRecord.create(
                _module("ValidQC", rater=invalid_rater),
                scope="project",
                display_order=10,
            )

    for readonly_rater in (None, ""):
        record = ModuleRecord.create(
            _module("ReadonlyQC", rater=readonly_rater),
            scope="project",
            display_order=10,
        )
        assert record.module.rater == readonly_rater


def test_module_repository_load_rejects_invalid_identity_from_json(tmp_path) -> None:
    repository = ModuleRepository(tmp_path / "modules", scope="project")
    valid = ModuleRecord.create(
        _module("ValidQC"),
        scope="project",
        display_order=10,
    )
    payload = valid.to_json_object()
    payload["module"]["name"] = "bad-name"
    repository.root.mkdir(parents=True)
    path = repository.root / f"{valid.module_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModuleRepositoryError, match="module_name"):
        repository.load(valid.module_id)


def test_project_module_repository_enforces_case_insensitive_uniqueness(
    tmp_path,
) -> None:
    repository = ModuleRepository(
        tmp_path / "easyqc_SAMPLE" / "modules",
        scope="project",
    )
    first = ModuleRecord.create(
        _module("AnatQC"),
        scope="project",
        display_order=10,
    )
    repository.save(first)
    before = {
        path.name: path.read_bytes()
        for path in repository.root.glob("*.json")
    }

    with pytest.raises(ModuleRepositoryError, match="already exists"):
        repository.save(
            ModuleRecord.create(
                _module("anatqc"),
                scope="project",
                display_order=20,
            )
        )

    assert {
        path.name: path.read_bytes()
        for path in repository.root.glob("*.json")
    } == before


def test_project_repository_blocks_rated_module_rename_and_name_reuse(
    tmp_path,
) -> None:
    project_root = tmp_path / "easyqc_SAMPLE"
    repository = ModuleRepository(project_root / "modules", scope="project")
    original = repository.save(
        ModuleRecord.create(
            _module("AnatQC"),
            scope="project",
            display_order=10,
        )
    )
    rating = (
        project_root
        / "RatingFiles"
        / "AnatQC"
        / "rater_1"
        / "AnatQC-rater_1-ROW001.json"
    )
    rating.parent.mkdir(parents=True)
    rating.write_text("{}", encoding="utf-8")
    original_path = repository.root / f"{original.module_id}.json"
    original_bytes = original_path.read_bytes()
    renamed_module = deepcopy(original.module)
    renamed_module.name = "RenamedQC"

    with pytest.raises(ModuleRepositoryError, match="rating"):
        repository.save(
            ModuleRecord.create(
                renamed_module,
                scope="project",
                display_order=original.display_order,
                module_id=original.module_id,
            )
        )

    assert original_path.read_bytes() == original_bytes
    repository.delete(original.module_id)

    with pytest.raises(ModuleRepositoryError, match="rating"):
        repository.save(
            ModuleRecord.create(
                _module("anatqc"),
                scope="project",
                display_order=10,
            )
        )

    assert list(repository.root.glob("*.json")) == []
    assert rating.exists()


def test_configuration_module_lifecycle_validates_before_project_write(
    tmp_path,
) -> None:
    configuration, projects = _configuration(tmp_path)
    configuration.add_module("1_QC", "Numeric prefix")
    configuration.add_module("AnatQC", "Anatomical")
    settings_path = projects.current_project.settings_path
    before = settings_path.read_bytes()

    with pytest.raises(ConfigurationError, match="already exists"):
        configuration.add_module("anatqc", "Case collision")
    with pytest.raises(ConfigurationError, match="rater"):
        configuration.import_module_payload(
            _module("ImportedQC", rater="bad-rater").to_legacy_dict()
        )
    with pytest.raises(ConfigurationError, match="module_name"):
        configuration.import_module_payload(
            _module("bad-name").to_legacy_dict()
        )

    assert settings_path.read_bytes() == before
    assert [module.name for module in configuration.modules()] == [
        "example",
        "1_QC",
        "AnatQC",
    ]


def test_configuration_blocks_rated_rename_then_deleted_name_reuse(
    tmp_path,
) -> None:
    configuration, projects = _configuration(tmp_path)
    configuration.add_module("AnatQC", "Anatomical")
    rating = (
        projects.current_project.rating_dir
        / "AnatQC"
        / "rater_1"
        / "AnatQC-rater_1-ROW001.json"
    )
    rating.parent.mkdir(parents=True)
    rating.write_text("{}", encoding="utf-8")
    candidate = deepcopy(
        next(module for module in configuration.modules() if module.name == "AnatQC")
    )
    candidate.name = "RenamedQC"

    with pytest.raises(ConfigurationError, match="rating"):
        configuration.save_module(candidate, original_name="AnatQC")

    assert any(module.name == "AnatQC" for module in configuration.modules())
    configuration.remove_module("AnatQC")

    with pytest.raises(ConfigurationError, match="rating"):
        configuration.add_module("anatqc", "Reused")

    assert all(module.name.casefold() != "anatqc" for module in configuration.modules())
    assert rating.exists()


def test_template_copy_obeys_project_case_insensitive_name_rule(tmp_path) -> None:
    templates = TemplateService(tmp_path / "install")
    source = templates.add_module(_module("AnatQC"), display_order=10)
    destination = ModuleRepository(
        tmp_path / "easyqc_SAMPLE" / "modules",
        scope="project",
    )
    destination.save(
        ModuleRecord.create(
            _module("anatqc"),
            scope="project",
            display_order=10,
        )
    )

    with pytest.raises(TemplateServiceError, match="AnatQC"):
        ProjectTemplateService(templates).copy_module(
            source.module_id,
            destination,
        )

    assert [record.module.name for record in destination.snapshot().records] == [
        "anatqc"
    ]


def test_project_repository_conflict_check_and_write_share_rating_lock(
    tmp_path,
    monkeypatch,
) -> None:
    project_root = tmp_path / "easyqc_SAMPLE"
    repository = ModuleRepository(project_root / "modules", scope="project")
    lock_depth = 0
    lock_targets = []

    @contextmanager
    def observed_rating_lock(target):
        nonlocal lock_depth
        lock_targets.append(target)
        lock_depth += 1
        try:
            yield project_root / "RatingFiles" / ".easyqc-rating-write.lock"
        finally:
            lock_depth -= 1

    monkeypatch.setattr(
        module_repository_module,
        "rating_write_lock",
        observed_rating_lock,
    )
    original_snapshot = repository.snapshot
    original_write = repository._write_record

    def guarded_snapshot():
        assert lock_depth > 0
        return original_snapshot()

    def guarded_write(record):
        assert lock_depth > 0
        return original_write(record)

    monkeypatch.setattr(repository, "snapshot", guarded_snapshot)
    monkeypatch.setattr(repository, "_write_record", guarded_write)

    repository.save(
        ModuleRecord.create(
            _module("AnatQC"),
            scope="project",
            display_order=10,
        )
    )
    before = {
        path.name: path.read_bytes()
        for path in repository.root.glob("*.json")
    }

    with pytest.raises(ModuleRepositoryError, match="already exists"):
        repository.save(
            ModuleRecord.create(
                _module("anatqc"),
                scope="project",
                display_order=20,
            )
        )

    assert lock_depth == 0
    assert lock_targets
    assert all(Path(target).parent.name == "RatingFiles" for target in lock_targets)
    assert {
        path.name: path.read_bytes()
        for path in repository.root.glob("*.json")
    } == before


def test_module_rename_check_and_delete_settings_write_share_rating_lock(
    tmp_path,
    monkeypatch,
) -> None:
    configuration, projects = _configuration(tmp_path)
    configuration.add_module("AnatQC", "Anatomical")
    project = projects.current_project
    assert project is not None
    rating = (
        project.rating_dir
        / "AnatQC"
        / "rater_1"
        / "AnatQC-rater_1-ROW001.json"
    )
    rating.parent.mkdir(parents=True)
    rating.write_text("{}", encoding="utf-8")
    settings_path = project.settings_path
    before_rename = settings_path.read_bytes()
    lock_depth = 0
    rating_checks = []
    settings_writes = []

    @contextmanager
    def observed_rating_lock(target):
        nonlocal lock_depth
        lock_depth += 1
        try:
            yield project.rating_dir / ".easyqc-rating-write.lock"
        finally:
            lock_depth -= 1

    monkeypatch.setattr(
        module_repository_module,
        "rating_write_lock",
        observed_rating_lock,
    )
    original_rating_check = ModuleRepository._rated_module_directory
    original_json_save = FileUtils.safe_json_save

    def guarded_rating_check(repository, module_name):
        assert lock_depth > 0
        rating_checks.append(module_name)
        return original_rating_check(repository, module_name)

    def guarded_json_save(path, payload, *args, **kwargs):
        if Path(path) == settings_path:
            assert lock_depth > 0
            settings_writes.append(Path(path))
        return original_json_save(path, payload, *args, **kwargs)

    monkeypatch.setattr(
        ModuleRepository,
        "_rated_module_directory",
        guarded_rating_check,
    )
    monkeypatch.setattr(FileUtils, "safe_json_save", guarded_json_save)
    renamed = deepcopy(
        next(module for module in configuration.modules() if module.name == "AnatQC")
    )
    renamed.name = "RenamedQC"

    with pytest.raises(ConfigurationError, match="rating"):
        configuration.save_module(renamed, original_name="AnatQC")

    assert settings_path.read_bytes() == before_rename
    assert "AnatQC" in rating_checks
    assert settings_writes == []

    configuration.remove_module("AnatQC")

    assert settings_writes == [settings_path]
    assert rating.exists()
    assert [module.name for module in configuration.modules()] == ["example"]


def test_stale_project_process_cannot_replace_new_casefold_module(
    tmp_path,
) -> None:
    registry_path = tmp_path / "projects.json"
    creator = ProjectService(registry_path)
    creator.create("SAMPLE", tmp_path)
    first_projects = ProjectService(registry_path)
    second_projects = ProjectService(registry_path)
    first_projects.load("SAMPLE")
    second_projects.load("SAMPLE")
    first = ConfigurationService(first_projects, TableService())
    second = ConfigurationService(second_projects, TableService())

    first.add_module("AnatQC", "First writer")
    project = first_projects.current_project
    assert project is not None
    settings_before = project.settings_path.read_bytes()
    modules_before = {
        path.name: path.read_bytes()
        for path in sorted((project.path / "modules").glob("*.json"))
    }

    with pytest.raises(ConfigurationError, match="changed|exists"):
        second.import_module_payload(
            _module("anatqc").to_legacy_dict(),
        )

    assert project.settings_path.read_bytes() == settings_before
    assert {
        path.name: path.read_bytes()
        for path in sorted((project.path / "modules").glob("*.json"))
    } == modules_before
    verifier = ProjectService(registry_path)
    verifier.load("SAMPLE")
    assert [module.name for module in verifier.get_modules().values()] == [
        "example",
        "AnatQC",
    ]
