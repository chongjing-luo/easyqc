"""Strict schema-v3 rating persistence tests."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from core.rating_identity import RatingIdentity, canonical_rating_path
from core.rating_service import (
    RatingIdentityConflictError,
    RatingScanError,
    RatingService,
    RatingServiceError,
)
from models.project import Project
from models.rating import Rating


def _rating(
    module_name: str = "AnatQC",
    rater: str = "rater_1",
    easyqcid: str = "SUB001-session-1",
    *,
    score: str = "Good",
) -> Rating:
    return Rating.from_legacy_dict(
        {
            "name": module_name,
            "label": "Anatomical QC",
            "rater": rater,
            "easyqcid": easyqcid,
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


def _current_payload(rating: Rating) -> dict:
    payload = rating.to_legacy_dict()
    payload["schema_version"] = 3
    return payload


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
    assert payload["schema_version"] == 3
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
            requested.easyqcid,
        ),
    )
    target.parent.mkdir(parents=True)
    foreign = _rating(easyqcid="OTHER")
    original = json.dumps(_current_payload(foreign), sort_keys=True)
    target.write_text(original, encoding="utf-8")

    with pytest.raises(RatingIdentityConflictError):
        RatingService(project).save_rating(requested)

    assert target.read_text(encoding="utf-8") == original


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


def test_scan_rejects_file_symlink_before_loading_outside_target(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{not json", encoding="utf-8")
    link = (
        project.rating_dir
        / "AnatQC"
        / "rater_1"
        / "AnatQC-rater_1-SUB001-session-1.json"
    )
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    scan = RatingService(project).scan_rating_records()

    assert not scan.records
    assert [(issue.path, issue.code) for issue in scan.errors] == [
        (link, "symlink")
    ]


def test_scan_reports_linked_directory_without_traversing_it(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside-module"
    outside.mkdir()
    (outside / "should-not-be-read.json").write_text(
        "{not json",
        encoding="utf-8",
    )
    project.rating_dir.mkdir(parents=True)
    link = project.rating_dir / "AnatQC"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    scan = RatingService(project).scan_rating_records()

    assert not scan.records
    assert [(issue.path, issue.code) for issue in scan.errors] == [
        (link, "symlink")
    ]


def test_ratingfiles_root_symlink_is_rejected_for_scan_and_save(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    project.path.mkdir(parents=True)
    outside = tmp_path / "outside-rating-root"
    outside.mkdir()
    try:
        project.rating_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    scan = RatingService(project).scan_rating_records()

    assert [(issue.path, issue.code) for issue in scan.errors] == [
        (project.rating_dir, "symlink")
    ]
    with pytest.raises(RatingServiceError, match="symlink"):
        RatingService(project).save_rating(_rating())
    assert list(outside.iterdir()) == []


def test_save_rejects_symlinked_rater_directory_without_outside_write(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    module_dir = project.rating_dir / "AnatQC"
    module_dir.mkdir(parents=True)
    outside = tmp_path / "outside-rater"
    outside.mkdir()
    link = module_dir / "rater_1"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(RatingServiceError, match="symlink"):
        RatingService(project).save_rating(_rating())

    assert list(outside.iterdir()) == []


def test_save_synchronizes_rating_and_new_directory_entries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _project(tmp_path)
    synced: list[Path] = []
    monkeypatch.setattr(
        "core.rating_service._fsync_directory",
        synced.append,
    )

    target = RatingService(project).save_rating(_rating())

    assert target.parent in synced
    assert project.path in synced


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
        json.dumps(_current_payload(_rating(easyqcid="SUB001"))),
        encoding="utf-8",
    )

    scan = RatingService(project).scan_rating_records()

    assert not scan.records
    assert len(scan.errors) == 1
    assert scan.errors[0].code == "path_identity_mismatch"


def test_canonical_records_aggregate_after_save_reload(tmp_path: Path) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    service.save_rating(_rating(easyqcid="001", score="Poor"))
    service.save_rating(_rating(easyqcid="002", score="Good"))

    subjects = pd.DataFrame(
        {"easyqcid": ["001", "002"], "site": ["A", "B"]}
    )
    result = service.aggregate_to_wide(service.load_all_ratings(), subjects)

    assert list(result["easyqcid"]) == ["001", "002"]
    assert list(result["AnatQC.rater_1.score1"]) == ["Poor", "Good"]
