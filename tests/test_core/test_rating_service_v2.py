from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from core.rating_identity import RatingIdentity, canonical_rating_path
from core.rating_service import (
    RatingIdentityConflictError,
    RatingLegacyConflictError,
    RatingScanError,
    RatingService,
)
from models.project import Project
from models.rating import Rating


def _rating(
    module_name: str = "AnatQC",
    rater: str = "rater_1",
    ezqcid: str = "SUB001-session-1",
    *,
    score: str = "Good",
) -> Rating:
    return Rating.from_legacy_dict(
        {
            "name": module_name,
            "label": "Anatomical QC",
            "rater": rater,
            "ezqcid": ezqcid,
            "scores": {
                "1": {
                    "label": "Overall",
                    "num": "Poor,Good",
                    "num_": "Poor,Good",
                    "value": score,
                }
            },
            "tags": {"1": {"label": "Review", "value": False}},
            "notes": None,
            "time": "2026-07-28 10:00:00",
            "code": "viewer {image}",
            "code_exe": {"0": "viewer image.nii.gz"},
            "interper": "shell",
            "control": True,
            "select_filter": None,
            "showing": True,
        }
    )


def _project(tmp_path: Path) -> Project:
    return Project("SYNTHETIC", tmp_path / "easyqc_SYNTHETIC")


def _legacy_path(project: Project, rating: Rating) -> Path:
    return (
        project.rating_dir
        / rating.module_name
        / rating.rater
        / (
            f"{rating.module_name}._.{rating.ezqcid}._.{rating.rater}"
            f"._.{rating.scores['1']}._.False.json"
        )
    )


def _write_legacy(project: Project, rating: Rating) -> Path:
    path = _legacy_path(project, rating)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(rating.to_legacy_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_save_uses_stable_exact_path_and_overwrites_latest_snapshot(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    first = _rating(score="Poor")
    second = _rating(score="Good")

    first_path = service.save_rating(first)
    second_path = service.save_rating(second)

    expected = canonical_rating_path(
        project.rating_dir,
        RatingIdentity("AnatQC", "rater_1", "SUB001-session-1"),
    )
    assert first_path == second_path == expected
    assert first_path.name == "AnatQC-rater_1-SUB001-session-1.json"
    assert list(first_path.parent.glob("*.json")) == [first_path]
    payload = json.loads(first_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["scores"]["1"]["value"] == "Good"


def test_score_and_tag_values_never_change_the_filename(tmp_path: Path) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    first = _rating(score="Poor")
    second = _rating(score="Good")
    second.tags["1"] = True

    assert service.save_rating(first) == service.save_rating(second)


def test_existing_canonical_body_with_another_identity_is_not_overwritten(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    requested = _rating()
    target = canonical_rating_path(
        project.rating_dir,
        RatingIdentity(
            requested.module_name,
            requested.rater,
            requested.ezqcid,
        ),
    )
    target.parent.mkdir(parents=True)
    foreign = _rating(ezqcid="OTHER")
    original = json.dumps(foreign.to_legacy_dict(), sort_keys=True)
    target.write_text(original, encoding="utf-8")

    with pytest.raises(RatingIdentityConflictError):
        RatingService(project).save_rating(requested)

    assert target.read_text(encoding="utf-8") == original


def test_legacy_record_is_readable_but_normal_save_requires_explicit_migration(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    rating = _rating()
    legacy_path = _write_legacy(project, rating)
    service = RatingService(project)

    assert service.validate_rating_file(legacy_path)
    loaded = service.load_all_ratings()
    assert [(item.module_name, item.rater, item.ezqcid) for item in loaded] == [
        ("AnatQC", "rater_1", "SUB001-session-1")
    ]

    with pytest.raises(RatingLegacyConflictError):
        service.save_rating(_rating(score="Poor"))

    assert legacy_path.exists()
    assert not canonical_rating_path(
        project.rating_dir,
        RatingIdentity("AnatQC", "rater_1", "SUB001-session-1"),
    ).exists()


def test_find_rating_files_supports_one_legacy_or_one_canonical_record(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    rating = _rating()
    legacy_path = _write_legacy(project, rating)
    target_dir = legacy_path.parent

    assert RatingService.find_rating_files_in_rater_dir(
        target_dir,
        rating.module_name,
        rating.ezqcid,
        rating.rater,
    ) == [legacy_path]

    legacy_path.unlink()
    canonical = service.save_rating(rating)
    assert RatingService.find_rating_files_in_rater_dir(
        target_dir,
        rating.module_name,
        rating.ezqcid,
        rating.rater,
    ) == [canonical]


def test_structured_scan_keeps_valid_sibling_and_reports_bad_file(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    valid = service.save_rating(_rating())
    bad = (
        project.rating_dir
        / "RestQC"
        / "rater_2"
        / "RestQC-rater_2-SUB002.json"
    )
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")

    scan = service.scan_rating_records()

    assert [record.path for record in scan.records] == [valid]
    assert [issue.path for issue in scan.errors] == [bad]
    assert scan.errors[0].code == "json_load"
    with pytest.raises(RatingScanError) as exc_info:
        service.load_all_rating_records()
    assert exc_info.value.result == scan


def test_scan_reports_directory_filename_body_mismatch(tmp_path: Path) -> None:
    project = _project(tmp_path)
    path = (
        project.rating_dir
        / "WrongModule"
        / "rater_1"
        / "AnatQC-rater_1-SUB001.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(_rating(ezqcid="SUB001").to_legacy_dict()),
        encoding="utf-8",
    )

    scan = RatingService(project).scan_rating_records()

    assert not scan.records
    assert len(scan.errors) == 1
    assert scan.errors[0].code == "path_identity_mismatch"


def test_scan_reports_new_and_legacy_duplicate_identity(tmp_path: Path) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    rating = _rating()
    canonical = service.save_rating(rating)
    legacy = _write_legacy(project, rating)

    scan = service.scan_rating_records()

    assert {record.path for record in scan.records} == {canonical, legacy}
    assert {error.code for error in scan.errors} == {"duplicate_identity"}
    with pytest.raises(RatingScanError, match="duplicate"):
        service.load_all_ratings()


def test_canonical_records_aggregate_after_save_reload(tmp_path: Path) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    service.save_rating(_rating(ezqcid="001", score="Poor"))
    service.save_rating(_rating(ezqcid="002", score="Good"))

    subjects = pd.DataFrame(
        {"ezqcid": ["001", "002"], "site": ["A", "B"]}
    )
    result = service.aggregate_to_wide(service.load_all_ratings(), subjects)

    assert list(result["ezqcid"]) == ["001", "002"]
    assert list(result["AnatQC.rater_1.score1"]) == ["Poor", "Good"]
