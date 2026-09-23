"""First import must not depend on an already populated Master QC list."""

import pandas as pd
import pytest

from core.app_services import build_app_services
from core.qc_workflow_service import QcWorkflowService


@pytest.mark.parametrize("stored_empty_table", [False, True])
def test_empty_project_context_is_loadable_before_first_import(
    tmp_path, stored_empty_table
):
    registry = tmp_path / "projects.json"
    services = build_app_services(registry)
    configuration = services.configuration_service
    project = configuration.create_project("EMPTY", tmp_path)
    if stored_empty_table:
        configuration.replace_subjects(pd.DataFrame(columns=["easyqcid", "image"]))
    table_path = project.table_dir / "easyqc_all.csv"
    before = table_path.read_bytes() if table_path.exists() else None

    reopened = build_app_services(registry)
    snapshot = reopened.project_context_service.open_initial()

    assert snapshot.has_project
    assert snapshot.project_name == "EMPTY"
    assert snapshot.subjects.empty
    assert "easyqcid" in snapshot.subjects.columns
    assert snapshot.long_results_table_view_service.source_total == 0
    assert snapshot.table_view_service.source_total == 0
    assert (table_path.read_bytes() if table_path.exists() else None) == before


def test_cleared_master_context_keeps_existing_rating_files(tmp_path):
    registry = tmp_path / "projects.json"
    services = build_app_services(registry)
    configuration = services.configuration_service
    project = configuration.create_project("CLEARED", tmp_path)
    configuration.replace_subjects(pd.DataFrame({"easyqcid": ["0001"]}))
    module = configuration.modules()[0].to_legacy_dict()
    module["rater"] = "tester"
    workflow = QcWorkflowService(
        module, configuration.subjects(),
        rating_dir=project.rating_dir / module["name"] / "tester",
        code_executor=services.code_executor,
    )
    workflow.save()
    ratings = {path: path.read_bytes() for path in project.rating_dir.rglob("*.json")}
    assert ratings
    configuration.replace_subjects(pd.DataFrame(columns=["easyqcid"]))

    snapshot = build_app_services(registry).project_context_service.open_initial()

    assert snapshot.has_project
    assert snapshot.subjects.empty
    assert snapshot.long_results_table_view_service.source_total == 0
    assert {path: path.read_bytes() for path in project.rating_dir.rglob("*.json")} == ratings
