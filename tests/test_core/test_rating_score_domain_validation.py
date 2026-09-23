"""Schema-v3 persisted-rating score-domain contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.rating_identity import RatingIdentity, canonical_rating_path
from core.rating_service import RatingScanError, RatingService
from models.project import Project
from models.rating import Rating


def _project(tmp_path: Path) -> Project:
    return Project("SYNTHETIC", tmp_path / "easyqc_SYNTHETIC")


def _payload(
    score_value: object,
    allowed_values: str | list[object] = "Poor,Good",
) -> dict[str, object]:
    return {
        "schema_version": 3,
        "name": "AnatQC",
        "label": "Anatomical QC",
        "rater": "rater_1",
        "easyqcid": "SUB001",
        "scores": {
            "1": {
                "label": "Overall",
                "num": "Poor,Good",
                "num_": allowed_values,
                "value": score_value,
            }
        },
        "tags": {"1": {"label": "Review", "value": False}},
        "notes": None,
        "time": "2026-09-05 08:00:00",
        "code": "viewer {image}",
        "code_exe": {"0": "viewer image.nii.gz"},
        "interper": "shell",
        "control": True,
        "select_filter": None,
        "showing": True,
    }


def _rating(
    score_value: object,
    allowed_values: str | list[object] = "Poor,Good",
) -> Rating:
    return Rating.from_legacy_dict(_payload(score_value, allowed_values))


def test_load_and_scan_reject_a_schema_v3_score_outside_its_saved_domain(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    path = canonical_rating_path(
        project.rating_dir,
        RatingIdentity("AnatQC", "rater_1", "SUB001"),
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(_payload("Unexpected"), ensure_ascii=False),
        encoding="utf-8",
    )
    service = RatingService(project)

    scan = service.scan_rating_records()

    assert scan.records == []
    assert len(scan.errors) == 1
    assert scan.errors[0].path == path
    assert not service.validate_rating_file(path)
    with pytest.raises(RatingScanError):
        service.load_rating(path)
    with pytest.raises(RatingScanError):
        service.load_all_rating_records()


def test_save_rejects_a_score_outside_its_saved_domain_without_overwriting(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    target = service.save_rating(_rating("Good"))
    original_bytes = target.read_bytes()

    with pytest.raises(ValueError, match="score.*1"):
        service.save_rating(_rating("Unexpected"))

    assert target.read_bytes() == original_bytes


@pytest.mark.parametrize(
    ("allowed_values", "score_value"),
    [
        ("Poor,Good", None),
        ("Poor,Good", "Good"),
        ("1,2,3", 2),
        (["低", "中", "高"], "中"),
    ],
)
def test_schema_v3_save_and_load_accept_values_from_the_saved_score_domain(
    tmp_path: Path,
    allowed_values: str | list[object],
    score_value: object,
) -> None:
    service = RatingService(_project(tmp_path))

    path = service.save_rating(_rating(score_value, allowed_values))

    assert service.load_rating(path).scores["1"] == score_value
