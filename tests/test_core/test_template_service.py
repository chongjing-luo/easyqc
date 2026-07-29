from __future__ import annotations

from copy import deepcopy
import json

import pandas as pd
import pytest

from core.configuration_service import ConfigurationService
from core.module_repository import ModuleRecord, ModuleRepository
from core.project_service import ProjectService
from core.project_template_service import ProjectTemplateService
from core.qc_workflow_service import QcWorkflowService
from core.table_service import TableService
from core.template_service import TemplateService, TemplateServiceError
from models.qcmodule import QCModule


def _module(name: str, label: str | None = None) -> QCModule:
    return QCModule(
        name=name,
        label=label or name,
        code="viewer {image_path}",
    )


def test_installation_template_state_is_isolated_by_application_root(
    tmp_path,
) -> None:
    first = TemplateService(tmp_path / "install-a")
    second = TemplateService(tmp_path / "install-b")

    first.set_constant("DATA_ROOT", "/first")
    first.set_shell_enabled(True)
    first_module = first.add_module(_module("AnatQC"), display_order=10)

    second.set_constant("DATA_ROOT", "/second")
    second.set_shell_enabled(False)
    second_module = second.add_module(_module("FuncQC"), display_order=10)

    assert first.constants() == {"DATA_ROOT": "/first"}
    assert second.constants() == {"DATA_ROOT": "/second"}
    assert first.shell_enabled() is True
    assert second.shell_enabled() is False
    assert [record.module.name for record in first.modules().records] == ["AnatQC"]
    assert [record.module.name for record in second.modules().records] == ["FuncQC"]
    assert first_module.module_id != second_module.module_id


def test_module_repository_round_trips_one_file_per_module(tmp_path) -> None:
    repository = ModuleRepository(tmp_path / "modules", scope="template")
    record = ModuleRecord.create(
        _module("AnatQC", "Anatomical quality"),
        scope="template",
        display_order=20,
    )

    repository.save(record)

    assert [path.name for path in repository.root.glob("*.json")] == [
        f"{record.module_id}.json"
    ]
    loaded = repository.load(record.module_id)
    assert loaded.module_id == record.module_id
    assert loaded.scope == "template"
    assert loaded.display_order == 20
    assert loaded.module.to_legacy_dict() == record.module.to_legacy_dict()


def test_constant_template_addition_obeys_first_name_rule(tmp_path) -> None:
    templates = TemplateService(tmp_path / "install")
    templates.set_constant("DATA_ROOT", "/first")

    with pytest.raises(TemplateServiceError, match="DATA_ROOT"):
        templates.set_constant("DATA_ROOT", "/second")

    assert templates.constants() == {"DATA_ROOT": "/first"}


def test_module_catalog_keeps_valid_siblings_visible_when_one_file_is_bad(
    tmp_path,
) -> None:
    repository = ModuleRepository(tmp_path / "modules", scope="template")
    valid = ModuleRecord.create(
        _module("ValidQC"),
        scope="template",
        display_order=1,
    )
    repository.save(valid)
    broken = repository.root / "broken.json"
    broken.write_text("{not-json", encoding="utf-8")

    snapshot = repository.snapshot()

    assert [record.module.name for record in snapshot.records] == ["ValidQC"]
    assert len(snapshot.errors) == 1
    assert snapshot.errors[0].path == broken
    assert "broken.json" in snapshot.errors[0].message


def test_copying_template_module_creates_detached_editable_project_record(
    tmp_path,
) -> None:
    templates = TemplateService(tmp_path / "install")
    template_record = templates.add_module(
        _module("AnatQC", "Template label"),
        display_order=1,
    )
    projects = ModuleRepository(
        tmp_path / "easyqc_SAMPLE" / "modules",
        scope="project",
    )
    copier = ProjectTemplateService(templates)
    candidate = deepcopy(template_record.module)
    candidate.label = "Project label"
    candidate.rater = "rater_a"

    copied = copier.copy_module(
        template_record.module_id,
        projects,
        candidate=candidate,
    )
    template_record.module.label = "Mutated detached object"

    assert copied.module_id != template_record.module_id
    assert copied.scope == "project"
    assert copied.module.label == "Project label"
    assert copied.module.rater == "rater_a"
    assert projects.load(copied.module_id).module.label == "Project label"
    assert templates.module(template_record.module_id).module.label == "Template label"


def test_copying_template_module_obeys_destination_first_name_rule(
    tmp_path,
) -> None:
    templates = TemplateService(tmp_path / "install")
    source = templates.add_module(_module("AnatQC"), display_order=1)
    projects = ModuleRepository(tmp_path / "project" / "modules", scope="project")
    projects.save(
        ModuleRecord.create(
            _module("AnatQC", "Existing wins"),
            scope="project",
            display_order=1,
        )
    )

    with pytest.raises(TemplateServiceError, match="AnatQC"):
        ProjectTemplateService(templates).copy_module(
            source.module_id,
            projects,
        )

    assert [item.module.label for item in projects.snapshot().records] == [
        "Existing wins"
    ]


def test_template_module_import_export_creates_detached_record(tmp_path) -> None:
    source = TemplateService(tmp_path / "source")
    module = _module("AnatQC", "Anatomical template")
    module.rater = "template_rater"
    module.qc_filter = {"groups": [{"id": "kept"}]}
    module.button = {"help": "SOP"}
    original = source.add_module(module, display_order=10)
    exported_path = tmp_path / "exports" / "anat-template.json"

    source.export_module(original.module_id, exported_path)
    destination = TemplateService(tmp_path / "destination")
    imported = destination.import_module(exported_path)

    assert imported.module_id != original.module_id
    assert imported.scope == "template"
    assert imported.display_order == 10
    assert imported.module.to_legacy_dict() == original.module.to_legacy_dict()


def test_legacy_module_migration_publishes_only_a_complete_directory(
    tmp_path,
) -> None:
    repository = ModuleRepository(tmp_path / "project" / "modules", scope="project")
    legacy = {
        "1": _module("AnatQC").to_legacy_dict(),
        "2": _module("FuncQC").to_legacy_dict(),
    }

    records = repository.initialize_settings(legacy)

    assert [record.module.name for record in records] == ["AnatQC", "FuncQC"]
    assert len(list(repository.root.glob("*.json"))) == 2
    for record in records:
        payload = json.loads(
            (repository.root / f"{record.module_id}.json").read_text(
                encoding="utf-8"
            )
        )
        assert payload["schema_version"] == 1
        assert payload["scope"] == "project"

    # Existing per-module storage is authoritative and migration is idempotent.
    assert repository.initialize_settings({"1": _module("Ignored").to_legacy_dict()}) == records


def test_template_to_project_to_view_command_first_throughput(tmp_path) -> None:
    installation = tmp_path / "install"
    templates = TemplateService(installation)
    templates.set_constant("DATA_ROOT", "/images")
    module = _module("AnatQC")
    module.rater = "rater_a"
    module.code = "viewer {DATA_ROOT}/{image_file}"
    source = templates.add_module(module, display_order=10)

    project_service = ProjectService(installation / "projects.json")
    configuration = ConfigurationService(project_service, TableService())
    configuration.create_project("SAMPLE", tmp_path)
    configuration.replace_subjects(
        pd.DataFrame(
            {"easyqcid": ["ROW001"], "image_file": ["row001.nii.gz"]}
        ),
        notify=False,
    )
    project_modules = ModuleRepository(
        project_service.current_project.path / "modules",
        scope="project",
    )
    copier = ProjectTemplateService(templates)

    copied_constant = copier.copy_constant(
        "DATA_ROOT",
        configuration,
    )
    copied_module = copier.copy_module(source.module_id, project_modules)
    workflow = QcWorkflowService(
        copied_module.module,
        configuration.subjects(),
        rating_dir=None,
        constants=configuration.constants(),
    )

    assert copied_constant == "DATA_ROOT"
    assert configuration.constants() == {"DATA_ROOT": "/images"}
    assert workflow.viewer_plan().rendered_template == "viewer /images/row001.nii.gz"
    assert templates.constants() == {"DATA_ROOT": "/images"}


def test_template_constant_can_match_list_but_copy_to_project_is_blocked(
    tmp_path,
) -> None:
    installation = tmp_path / "install"
    templates = TemplateService(installation)
    templates.set_constant("site", "template-default")
    project_service = ProjectService(installation / "projects.json")
    configuration = ConfigurationService(project_service, TableService())
    configuration.create_project("SAMPLE", tmp_path)
    configuration.replace_subjects(
        pd.DataFrame({"easyqcid": ["ROW001"], "site": ["project-row"]}),
        notify=False,
    )

    with pytest.raises(TemplateServiceError, match="site"):
        ProjectTemplateService(templates).copy_constant(
            "site",
            configuration,
        )

    assert templates.constants() == {"site": "template-default"}
    assert configuration.constants() == {}


def test_copy_module_template_uses_project_configuration_transaction(
    tmp_path,
) -> None:
    installation = tmp_path / "install"
    templates = TemplateService(installation)
    template_module = _module("AnatQC", "Template label")
    template_module.button = {"help": "SOP"}
    source = templates.add_module(template_module, display_order=10)
    project_service = ProjectService(installation / "projects.json")
    configuration = ConfigurationService(project_service, TableService())
    configuration.create_project("SAMPLE", tmp_path)
    candidate = deepcopy(source.module)
    candidate.name = "ProjectAnatQC"
    candidate.label = "Project label"

    copied_name = ProjectTemplateService(
        templates
    ).copy_module_to_project(
        source.module_id,
        configuration,
        candidate=candidate,
    )

    assert copied_name == "ProjectAnatQC"
    copied = next(
        module
        for module in configuration.modules()
        if module.name == "ProjectAnatQC"
    )
    assert copied.label == "Project label"
    assert copied.button == {"help": "SOP"}
    project_records = ModuleRepository(
        project_service.current_project.path / "modules",
        scope="project",
    ).snapshot()
    assert any(
        record.module.name == "ProjectAnatQC"
        for record in project_records.records
    )
    assert any(
        payload["name"] == "ProjectAnatQC"
        for payload in project_service.settings["qcmodule"].values()
    )
    assert templates.module(source.module_id).module.label == "Template label"

    edited_template = deepcopy(source.module)
    edited_template.label = "Changed template"
    templates.save_module(
        ModuleRecord.create(
            edited_template,
            scope="template",
            display_order=source.display_order,
            module_id=source.module_id,
        )
    )
    with pytest.raises(TemplateServiceError, match="ProjectAnatQC"):
        ProjectTemplateService(templates).copy_module_to_project(
            source.module_id,
            configuration,
            candidate=candidate,
        )
    assert next(
        module
        for module in configuration.modules()
        if module.name == "ProjectAnatQC"
    ).label == "Project label"
