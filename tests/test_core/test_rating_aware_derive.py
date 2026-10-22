"""Rating-aware master-list derive: formulas may reference result columns."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.app_services import build_app_services
from core.configuration_service import ConfigurationError
from core.project_context_service import ProjectContextError
from core.qc_workflow_service import QcWorkflowService
from models.derived_formula import DerivedColumnFormula


def _module_payload(*, name: str = "AnatQC", rater: str = "rater1") -> dict:
    return {
        "name": name,
        "label": f"{name} label",
        "rater": rater,
        "easyqcid": None,
        "watch_mode": False,
        "tags": {"1": {"label": "Motion", "value": False}},
        "scores": {
            "1": {
                "label": "Quality",
                "num": "Poor,Good",
                "num_": "Poor,Good",
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


def _subjects() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB002"],
            "site": ["A", "B"],
        }
    )


def _rated_services(tmp_path: Path, *, score: str = "Good"):
    """Build services with one project and one saved rating for SUB001 only."""
    registry = tmp_path / "projects.json"
    services = build_app_services(registry)
    configuration = services.configuration_service
    configuration.create_project("SAMPLE", tmp_path)
    configuration.replace_subjects(_subjects())
    configuration.save_module(_module_payload(), original_name="example")
    project = configuration.current_project
    workflow = QcWorkflowService(
        _module_payload(),
        _subjects(),
        rating_dir=project.rating_dir / "AnatQC" / "rater1",
        code_executor=services.code_executor,
    )
    workflow.set_score("1", score)
    workflow.save()
    snapshot = services.project_context_service.open_initial()
    assert snapshot.context_revision >= 1
    assert "AnatQC.rater1.score1" in {
        profile.name for profile in snapshot.table_view_service.profiles
    }
    return services, snapshot


def test_derive_from_rating_column_writes_master_and_blanks_unrated(tmp_path):
    services, snapshot = _rated_services(tmp_path)
    request = DerivedColumnFormula(
        "qc_pass",
        '=IF(ISBLANK([AnatQC.rater1.score1]), BLANK(), '
        'IF([AnatQC.rater1.score1] = "Good", "pass", "check"))',
    )

    name = services.project_context_service.derive_rating_aware_subject_column(
        request,
        expected_context_revision=snapshot.context_revision,
    )

    subjects = services.configuration_service.subjects()
    assert name == "qc_pass"
    assert "qc_pass" in subjects.columns
    assert subjects.loc[subjects["easyqcid"] == "SUB001", "qc_pass"].iloc[0] == "pass"
    unrated = subjects.loc[subjects["easyqcid"] == "SUB002", "qc_pass"].iloc[0]
    assert pd.isna(unrated) or unrated is None or unrated == ""


def test_plain_master_formula_via_rating_aware_path(tmp_path):
    services, snapshot = _rated_services(tmp_path)
    request = DerivedColumnFormula("site2", "=LOWER([site])")

    name = services.project_context_service.derive_rating_aware_subject_column(
        request,
        expected_context_revision=snapshot.context_revision,
    )

    subjects = services.configuration_service.subjects()
    assert name == "site2"
    assert tuple(subjects["site2"]) == ("a", "b")


def test_dot_in_column_name_rejected(tmp_path):
    services, snapshot = _rated_services(tmp_path)
    request = DerivedColumnFormula("qc.pass", "=LOWER([site])")

    with pytest.raises(ConfigurationError, match="点"):
        services.project_context_service.derive_rating_aware_subject_column(
            request,
            expected_context_revision=snapshot.context_revision,
        )


def test_existing_column_requires_explicit_overwrite_then_replaces(tmp_path):
    services, snapshot = _rated_services(tmp_path)
    context = services.project_context_service
    first = DerivedColumnFormula("flag", "=LOWER([site])")
    context.derive_rating_aware_subject_column(
        first,
        expected_context_revision=snapshot.context_revision,
    )

    stale = DerivedColumnFormula("flag", '=IF(ISBLANK([AnatQC.rater1.score1]), BLANK(), "rated")')
    with pytest.raises(ConfigurationError, match="列已存在"):
        context.derive_rating_aware_subject_column(
            stale,
            expected_context_revision=snapshot.context_revision,
        )

    refreshed = context.open_initial()
    context.derive_rating_aware_subject_column(
        stale,
        expected_context_revision=refreshed.context_revision,
        overwrite=True,
    )
    subjects = services.configuration_service.subjects()
    assert subjects.loc[subjects["easyqcid"] == "SUB001", "flag"].iloc[0] == "rated"
    unrated = subjects.loc[subjects["easyqcid"] == "SUB002", "flag"].iloc[0]
    assert pd.isna(unrated) or unrated is None or unrated == ""


def test_unknown_reference_rejected(tmp_path):
    services, snapshot = _rated_services(tmp_path)
    request = DerivedColumnFormula("bad", "=LEN([AnatQC.rater1.score9])")

    with pytest.raises(ConfigurationError):
        services.project_context_service.derive_rating_aware_subject_column(
            request,
            expected_context_revision=snapshot.context_revision,
        )


def test_stale_context_revision_rejected_without_write(tmp_path):
    services, snapshot = _rated_services(tmp_path)
    subjects_before = services.configuration_service.subjects().copy(deep=True)
    request = DerivedColumnFormula("qc_pass", "=LOWER([site])")

    with pytest.raises(ProjectContextError):
        services.project_context_service.derive_rating_aware_subject_column(
            request,
            expected_context_revision=snapshot.context_revision + 999,
        )

    pd.testing.assert_frame_equal(
        services.configuration_service.subjects(),
        subjects_before,
    )


def test_plain_derive_still_works_without_any_ratings(tmp_path):
    """No ratings on disk must not block ordinary master-column formulas."""
    registry = tmp_path / "projects.json"
    services = build_app_services(registry)
    configuration = services.configuration_service
    configuration.create_project("SAMPLE", tmp_path)
    configuration.replace_subjects(_subjects())
    configuration.save_module(_module_payload(), original_name="example")
    snapshot = services.project_context_service.open_initial()

    request = DerivedColumnFormula("site2", "=LOWER([site])")
    name = services.project_context_service.derive_rating_aware_subject_column(
        request,
        expected_context_revision=snapshot.context_revision,
    )

    subjects = services.configuration_service.subjects()
    assert name == "site2"
    assert tuple(subjects["site2"]) == ("a", "b")

    missing = DerivedColumnFormula("bad", "=LEN([AnatQC.rater1.score1])")
    with pytest.raises(ConfigurationError):
        services.project_context_service.derive_rating_aware_subject_column(
            missing,
            expected_context_revision=snapshot.context_revision,
        )
