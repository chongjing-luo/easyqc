from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from core.app_services import build_app_services
from core.project_context_service import ProjectContextError
from core.qc_workflow_service import QcWorkflowService
from core.event_bus import EventType
from models.table_view_state import (
    FilterCondition,
    FilterExpression,
    FilterGroup,
)
from utils.file_utils import FileUtils


def _module_payload(*, name: str = "AnatQC", rater: str | None = "rater1") -> dict:
    return {
        "name": name,
        "label": f"{name} label",
        "rater": rater,
        "ezqcid": None,
        "watch_mode": False,
        "tags": {"1": {"label": "Motion", "value": False}},
        "scores": {
            "1": {
                "label": "Quality",
                "num": "Poor,Fair,Good",
                "num_": "Poor,Fair,Good",
                "value": None,
            }
        },
        "code": "freeview {image}",
        "interper": "shell",
        "control": True,
        "select_filter": None,
        "showing": True,
        "code_exe": None,
        "time": None,
        "notes": None,
        "button": {},
    }


def _subjects(prefix: str = "") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003"],
            "site": ["A", "B", "C"],
            "image": [f"/{prefix}one.nii", f"/{prefix}two.nii", f"/{prefix}three.nii"],
        }
    )


def _filter_expression(
    column: str,
    operator: str,
    value,
) -> FilterExpression:
    return FilterExpression(
        group_join="all",
        groups=(
            FilterGroup(
                group_id="module-filter",
                join="all",
                conditions=(
                    FilterCondition(
                        column,
                        operator,
                        value,
                        "module-filter-condition",
                        True,
                    ),
                ),
            ),
        ),
    )


def _add_project(services, tmp_path, name: str, *, prefix: str = "", second_module=False):
    configuration = services.configuration_service
    configuration.create_project(name, tmp_path)
    configuration.replace_subjects(_subjects(prefix))
    configuration.save_module(_module_payload(), original_name="example")
    if second_module:
        configuration.add_module("FuncQC", "Functional QC")
        configuration.save_module(
            _module_payload(name="FuncQC", rater="rater2"),
            original_name="FuncQC",
        )
    return configuration.current_project


def test_empty_context_is_detached_and_does_not_create_registry(tmp_path) -> None:
    registry = tmp_path / "projects.json"
    services = build_app_services(registry)

    snapshot = services.project_context_service.open_initial()

    assert snapshot.project_name == ""
    assert snapshot.project_path is None
    assert snapshot.context_revision == 0
    assert snapshot.project_names == ()
    assert snapshot.subjects.empty
    assert snapshot.table_view_service.source_total == 0
    assert not registry.exists()


def test_last_project_snapshot_prepares_subjects_ratings_and_table_service(tmp_path) -> None:
    registry = tmp_path / "projects.json"
    services = build_app_services(registry)
    project = _add_project(services, tmp_path, "SAMPLE")
    workflow = QcWorkflowService(
        _module_payload(),
        _subjects(),
        rating_dir=project.rating_dir / "AnatQC" / "rater1",
        code_executor=services.code_executor,
    )
    workflow.set_score("1", "Good")
    workflow.save()

    reopened = build_app_services(registry)
    snapshot = reopened.project_context_service.open_initial()

    assert snapshot.project_name == "SAMPLE"
    assert snapshot.project_path == project.path.resolve()
    assert snapshot.context_revision == 1
    assert tuple(snapshot.subjects["ezqcid"]) == ("SUB001", "SUB002", "SUB003")
    assert [module.name for module in snapshot.modules] == ["AnatQC"]
    assert "AnatQC.rater1.score1" in {
        profile.name for profile in snapshot.table_view_service.profiles
    }
    assert "AnatQC.rater1.button" not in {
        profile.name for profile in snapshot.table_view_service.profiles
    }
    result = snapshot.table_view_service.apply_state(
        snapshot.table_view_service.default_state()
    )
    window = snapshot.table_view_service.get_window(result, 0, 1)
    assert window.dataframe.loc[0, "AnatQC.rater1.score1"] == "Good"


def test_qc_factory_uses_snapshot_order_directory_and_emits_rating_event(tmp_path) -> None:
    services = build_app_services(tmp_path / "projects.json")
    project = _add_project(services, tmp_path, "SAMPLE")
    snapshot = services.project_context_service.snapshot()
    received = []
    services.event_bus.subscribe(EventType.RATING_SAVED, received.append)

    workflow = services.project_context_service.create_qc_workflow(
        snapshot,
        module_name="AnatQC",
        initial_ezqcid="SUB003",
        navigation_ids=("SUB003", "SUB001"),
    )

    assert workflow.subject_ids == ("SUB003", "SUB001")
    assert workflow.current_ezqcid == "SUB003"
    workflow.set_score("1", "Fair")
    saved = workflow.save()
    assert saved.parent == project.rating_dir / "AnatQC" / "rater1"
    assert len(received) == 1
    assert received[0].data == {
        "context_revision": snapshot.context_revision,
        "project_name": "SAMPLE",
        "project_path": str(project.path.resolve()),
        "module_name": "AnatQC",
        "rater": "rater1",
        "ezqcid": "SUB003",
        "path": str(saved),
    }


def test_qc_factory_projects_existing_score_and_tag_summaries_for_exact_queue(
    tmp_path,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    project = _add_project(services, tmp_path, "SAMPLE")
    seed = QcWorkflowService(
        _module_payload(),
        _subjects(),
        rating_dir=project.rating_dir / "AnatQC" / "rater1",
        code_executor=services.code_executor,
    )
    seed.set_score("1", "Good")
    seed.set_tag("1", True)
    seed.save()
    snapshot = services.project_context_service.snapshot()

    workflow = services.project_context_service.create_qc_workflow(
        snapshot,
        module_name="AnatQC",
        initial_ezqcid="SUB002",
        navigation_ids=("SUB002", "SUB001"),
    )

    assert workflow.queue_summary("SUB002") == ("", "")
    assert workflow.queue_summary("SUB001") == ("Good", "Motion")


@pytest.mark.parametrize(
    "navigation_ids, message",
    [
        (("SUB001", "SUB001"), "duplicate"),
        (("SUB001", "FOREIGN"), "not in"),
        ((), "at least one"),
    ],
)
def test_qc_factory_rejects_unsafe_navigation_queue(
    tmp_path,
    navigation_ids,
    message,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE")
    snapshot = services.project_context_service.snapshot()

    with pytest.raises(ProjectContextError, match=message):
        services.project_context_service.create_qc_workflow(
            snapshot,
            module_name="AnatQC",
            initial_ezqcid="SUB001",
            navigation_ids=navigation_ids,
        )


def test_qc_factory_rejects_snapshot_after_project_switch_even_for_same_id(tmp_path) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "ALPHA", prefix="alpha/")
    alpha = services.project_context_service.snapshot()
    _add_project(services, tmp_path, "BETA", prefix="beta/")
    beta = services.project_context_service.snapshot()

    assert beta.context_revision > alpha.context_revision
    with pytest.raises(ProjectContextError, match="stale"):
        services.project_context_service.create_qc_workflow(
            alpha,
            module_name="AnatQC",
            initial_ezqcid="SUB001",
            navigation_ids=("SUB001",),
        )


def test_failed_project_load_preserves_current_project_and_settings(tmp_path) -> None:
    services = build_app_services(tmp_path / "projects.json")
    configuration = services.configuration_service
    alpha_project = _add_project(services, tmp_path, "ALPHA")
    alpha_settings = deepcopy(dict(services.project_service.settings))
    beta_project = _add_project(services, tmp_path, "BETA")
    services.project_service.load("ALPHA")
    beta_project.settings_path.write_text("{broken", encoding="utf-8")

    with pytest.raises(Exception):
        services.project_service.load("BETA")

    assert services.project_service.current_project == alpha_project
    assert dict(services.project_service.settings) == alpha_settings
    assert configuration.current_project == alpha_project


def test_context_resolves_empty_and_grouped_module_queues_in_source_order(
    tmp_path,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE")
    empty_snapshot = services.project_context_service.snapshot()

    assert services.project_context_service.resolve_module_queue(
        empty_snapshot,
        "AnatQC",
    ) == ("SUB001", "SUB002", "SUB003")

    grouped = FilterExpression(
        group_join="any",
        groups=(
            FilterGroup(
                group_id="site-a",
                join="all",
                conditions=(
                    FilterCondition("site", "==", "A", "site-a-condition"),
                ),
            ),
            FilterGroup(
                group_id="site-c",
                join="all",
                conditions=(
                    FilterCondition("site", "==", "C", "site-c-condition"),
                ),
            ),
        ),
    )
    saved_matches = services.configuration_service.save_module_filter(
        "AnatQC",
        grouped,
    )
    filtered_snapshot = services.project_context_service.snapshot()

    assert saved_matches == ("SUB001", "SUB003")
    assert services.project_context_service.resolve_module_queue(
        filtered_snapshot,
        "AnatQC",
    ) == ("SUB001", "SUB003")


def test_qc_factory_uses_module_queue_by_default_and_preserves_explicit_queue(
    tmp_path,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE")
    services.configuration_service.save_module_filter(
        "AnatQC",
        _filter_expression("site", "==", "C"),
    )
    snapshot = services.project_context_service.snapshot()

    filtered = services.project_context_service.create_qc_workflow(
        snapshot,
        module_name="AnatQC",
        initial_ezqcid="SUB003",
    )
    transitional = services.project_context_service.create_qc_workflow(
        snapshot,
        module_name="AnatQC",
        initial_ezqcid="SUB001",
        navigation_ids=("SUB001", "SUB002"),
    )

    assert filtered.subject_ids == ("SUB003",)
    assert transitional.subject_ids == ("SUB001", "SUB002")


def test_zero_match_filter_can_save_and_resolve_but_default_launch_rejects(
    tmp_path,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    _add_project(services, tmp_path, "SAMPLE")

    assert services.configuration_service.save_module_filter(
        "AnatQC",
        _filter_expression("site", "==", "missing"),
    ) == ()
    snapshot = services.project_context_service.snapshot()
    assert services.project_context_service.resolve_module_queue(
        snapshot,
        "AnatQC",
    ) == ()

    with pytest.raises(ProjectContextError, match="matches no subjects"):
        services.project_context_service.create_qc_workflow(
            snapshot,
            module_name="AnatQC",
            initial_ezqcid="SUB001",
        )


def test_failed_filter_save_preserves_detached_context(
    monkeypatch,
    tmp_path,
) -> None:
    services = build_app_services(tmp_path / "projects.json")
    project = _add_project(services, tmp_path, "SAMPLE")
    snapshot = services.project_context_service.snapshot()
    before_settings = deepcopy(dict(services.project_service.settings))
    before_bytes = project.settings_path.read_bytes()
    real_save = FileUtils.safe_json_save

    def fail_settings(path, data, indent=4):
        if path == project.settings_path:
            raise OSError("module filter replace failed")
        return real_save(path, data, indent)

    monkeypatch.setattr(FileUtils, "safe_json_save", fail_settings)

    with pytest.raises(OSError, match="module filter replace failed"):
        services.configuration_service.save_module_filter(
            "AnatQC",
            _filter_expression("site", "==", "A"),
        )

    assert dict(services.project_service.settings) == before_settings
    assert project.settings_path.read_bytes() == before_bytes
    assert snapshot.modules[0].qc_filter is None
    assert services.project_context_service.resolve_module_queue(
        snapshot,
        "AnatQC",
    ) == ("SUB001", "SUB002", "SUB003")
