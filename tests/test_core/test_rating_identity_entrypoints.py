from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core.cli_service import QCPageLaunchError, resolve_qcpage_launch
from core.project_context_service import ProjectContextError, ProjectContextService
from core.qc_workflow_service import (
    QcIdentityError,
    QcReadOnlyError,
    QcWorkflowService,
)


def _module(
    *,
    name: str = "AnatQC",
    rater: str | None = "rater_1",
) -> dict:
    return {
        "name": name,
        "label": "Anatomical QC",
        "rater": rater,
        "ezqcid": None,
        "watch_mode": False,
        "scores": {
            "1": {
                "label": "Overall",
                "num": "Poor,Good",
                "num_": "Poor,Good",
                "value": None,
            }
        },
        "tags": {"1": {"label": "Review", "value": False}},
        "notes": None,
        "time": None,
        "code": "",
        "code_exe": None,
        "interper": "shell",
        "control": False,
        "select_filter": None,
        "showing": True,
    }


@pytest.mark.parametrize(
    ("module_name", "rater", "ezqcid"),
    [
        ("Anat-QC", "rater_1", "SUB001"),
        ("AnatQC", "rater-one", "SUB001"),
        ("AnatQC", "rater_1", "SUB/001"),
        ("AnatQC", "__observation_no_rater__", "SUB001"),
    ],
)
def test_qc_workflow_rejects_invalid_persistent_identity_before_writing(
    tmp_path: Path,
    module_name: str,
    rater: str,
    ezqcid: str,
) -> None:
    rating_dir = (
        tmp_path
        / "RatingFiles"
        / module_name
        / rater
    )

    with pytest.raises(QcIdentityError):
        QcWorkflowService(
            _module(name=module_name, rater=rater),
            pd.DataFrame({"ezqcid": [ezqcid]}),
            rating_dir=rating_dir,
        )

    assert not rating_dir.exists()


def test_qc_workflow_rejects_case_only_ezqcid_collision(tmp_path: Path) -> None:
    with pytest.raises(QcIdentityError, match="case"):
        QcWorkflowService(
            _module(),
            pd.DataFrame({"ezqcid": ["SUB001", "sub001"]}),
            rating_dir=tmp_path / "RatingFiles" / "AnatQC" / "rater_1",
        )


@pytest.mark.parametrize(
    ("module_name", "rater", "ezqcid", "field"),
    [
        (" AnatQC", "rater_1", "SUB001", "module_name"),
        ("AnatQC", " rater_1", "SUB001", "rater"),
        ("AnatQC", "rater_1", " SUB001", "ezqcid"),
        ("AnatQC", "rater_1", 1, "ezqcid"),
    ],
)
def test_qc_workflow_does_not_clean_or_stringify_persistent_identities(
    tmp_path: Path,
    module_name,
    rater,
    ezqcid,
    field: str,
) -> None:
    with pytest.raises(QcIdentityError, match=field):
        QcWorkflowService(
            _module(name=module_name, rater=rater),
            pd.DataFrame({"ezqcid": [ezqcid]}),
            rating_dir=tmp_path / "RatingFiles",
        )


@pytest.mark.parametrize("rater", [None, ""])
def test_qc_workflow_keeps_blank_rater_as_read_only(
    tmp_path: Path,
    rater: str | None,
) -> None:
    workflow = QcWorkflowService(
        _module(rater=rater),
        pd.DataFrame({"ezqcid": ["SUB001"]}),
        rating_dir=tmp_path / "RatingFiles",
    )

    assert workflow.watch_mode
    assert "rater" in workflow.read_only_reason.lower()
    with pytest.raises(QcReadOnlyError):
        workflow.save()
    assert not (tmp_path / "RatingFiles").exists()


def test_project_context_rejects_invalid_and_case_colliding_ezqcids() -> None:
    with pytest.raises(ProjectContextError, match="ezqcid"):
        ProjectContextService._validate_subjects(
            pd.DataFrame({"ezqcid": [" SUB001"]}),
            {},
        )

    with pytest.raises(ProjectContextError, match="case"):
        ProjectContextService._validate_subjects(
            pd.DataFrame({"ezqcid": ["SUB001", "sub001"]}),
            {},
        )


def test_qc_workflow_accepts_numeric_leading_internal_ids(
    tmp_path: Path,
) -> None:
    workflow = QcWorkflowService(
        _module(name="1Anat", rater="2rater"),
        pd.DataFrame({"ezqcid": ["001-session.1"]}),
        rating_dir=tmp_path / "RatingFiles" / "1Anat" / "2rater",
    )

    assert workflow.current_ezqcid == "001-session.1"
    assert workflow.current_module.name == "1Anat"
    assert workflow.current_module.rater == "2rater"


@pytest.mark.parametrize(
    ("module_name", "rater", "ezqcid", "field"),
    [
        ("Anat-QC", "rater_1", "SUB001", "module_name"),
        ("AnatQC", "rater-one", "SUB001", "rater"),
        ("AnatQC", "rater_1", "SUB 001", "ezqcid"),
        ("AnatQC", "rater_1", 1, "ezqcid"),
    ],
)
def test_cli_rejects_invalid_identity_before_project_resolution(
    tmp_path: Path,
    module_name: str,
    rater: str,
    ezqcid: str,
    field: str,
) -> None:
    registry = tmp_path / "projects.json"

    with pytest.raises(QCPageLaunchError, match=field):
        resolve_qcpage_launch(
            "missing",
            module_name,
            rater,
            ezqcid,
            registry,
        )
    assert not registry.exists()
