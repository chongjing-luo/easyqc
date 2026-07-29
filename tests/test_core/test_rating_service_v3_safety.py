"""Strict schema-v3 rating safety tests."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path

import pytest

from core.rating_identity import RatingIdentity, canonical_rating_path
from core.rating_service import (
    RatingIdentityConflictError,
    RatingScanError,
    RatingService,
)
from models.project import Project
from models.rating import Rating


def _rating(
    module_name: str = "AnatQC",
    rater: str = "rater_1",
    easyqcid: str = "SUB001-session-1",
) -> Rating:
    return Rating.from_legacy_dict(
        {
            "name": module_name,
            "label": "Anatomical QC",
            "rater": rater,
            "easyqcid": easyqcid,
            "scores": {"1": {"label": "Overall", "value": "Good"}},
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


def test_save_holds_writer_lock_across_collision_guard_and_publish(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    rating = _rating()
    target = canonical_rating_path(
        project.rating_dir,
        RatingIdentity("AnatQC", "rater_1", "SUB001-session-1"),
    )
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(_current_payload(rating), ensure_ascii=False),
        encoding="utf-8",
    )
    events: list[str] = []

    @contextmanager
    def fake_writer_lock(path: Path):
        assert path == target
        events.append("lock_enter")
        try:
            yield path
        finally:
            events.append("lock_exit")

    def guarded_load(path: Path) -> Rating:
        assert path == target
        assert events == ["lock_enter"]
        events.append("body_guard")
        return rating

    def guarded_publish(path: Path, payload: dict) -> None:
        assert path == target
        assert payload["schema_version"] == 3
        assert events == ["lock_enter", "body_guard"]
        events.append("publish")

    monkeypatch.setattr("core.rating_service.rating_write_lock", fake_writer_lock)
    monkeypatch.setattr("core.rating_service._load_rating_file", guarded_load)
    monkeypatch.setattr(
        "core.rating_service.FileUtils.safe_json_save",
        guarded_publish,
    )

    assert service.save_rating(rating) == target
    assert events == [
        "lock_enter",
        "body_guard",
        "publish",
        "lock_exit",
    ]


def test_find_rating_files_rejects_corrupt_candidate_instead_of_returning_it(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    identity = RatingIdentity("AnatQC", "rater_1", "SUB001")
    target = canonical_rating_path(project.rating_dir, identity)
    target.parent.mkdir(parents=True)
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(RatingScanError) as exc_info:
        RatingService.find_rating_files_in_rater_dir(
            target.parent,
            identity.module_name,
            identity.easyqcid,
            identity.rater,
        )

    assert exc_info.value.result.errors[0].path == target
    assert str(target) in str(exc_info.value)
    assert "json_load" in str(exc_info.value)
    assert "Expecting property name" in str(exc_info.value)


def test_project_scan_rejects_rating_below_an_extra_directory_level(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    declared = _rating(easyqcid="SUB001")
    nested_path = (
        project.rating_dir
        / "unexpected"
        / "AnatQC"
        / "rater_1"
        / "AnatQC-rater_1-SUB001.json"
    )
    nested_path.parent.mkdir(parents=True)
    nested_path.write_text(
        json.dumps(_current_payload(declared), ensure_ascii=False),
        encoding="utf-8",
    )

    scan = RatingService(project).scan_rating_records()

    assert not scan.records
    assert len(scan.errors) == 1
    assert scan.errors[0].path == nested_path
    assert scan.errors[0].code == "path_depth"


@pytest.mark.parametrize(
    ("existing_identity", "requested_identity"),
    [
        (
            ("anatqc", "rater_1", "SUB001"),
            ("AnatQC", "rater_1", "SUB001"),
        ),
        (
            ("AnatQC", "RATER_1", "SUB001"),
            ("AnatQC", "rater_1", "SUB001"),
        ),
        (
            ("AnatQC", "rater_1", "sub001"),
            ("AnatQC", "rater_1", "SUB001"),
        ),
    ],
)
def test_save_rejects_casefold_equivalent_identity_before_writing(
    existing_identity: tuple[str, str, str],
    requested_identity: tuple[str, str, str],
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    existing = service.save_rating(_rating(*existing_identity))
    requested = _rating(*requested_identity)
    requested_path = canonical_rating_path(
        project.rating_dir,
        RatingIdentity(*requested_identity),
    )

    with pytest.raises(RatingIdentityConflictError):
        service.save_rating(requested)

    assert existing.exists()
    assert not requested_path.exists()


def test_save_rejects_case_variant_json_suffix_before_writing(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    rating = _rating(easyqcid="SUB001")
    canonical = canonical_rating_path(
        project.rating_dir,
        RatingIdentity("AnatQC", "rater_1", "SUB001"),
    )
    case_variant = canonical.with_suffix(".JSON")
    case_variant.parent.mkdir(parents=True)
    original = json.dumps(_current_payload(rating), ensure_ascii=False)
    case_variant.write_text(original, encoding="utf-8")

    with pytest.raises(RatingIdentityConflictError):
        service.save_rating(rating)

    assert case_variant.read_text(encoding="utf-8") == original
    assert not canonical.exists()


def test_scan_reports_casefold_identity_collision(tmp_path: Path) -> None:
    project = _project(tmp_path)
    service = RatingService(project)
    upper_rating = _rating(
        module_name="AnatQC",
        rater="Rater_1",
        easyqcid="SUB001",
    )
    lower_rating = _rating(
        module_name="anatqc",
        rater="rater_1",
        easyqcid="sub001",
    )
    upper = canonical_rating_path(
        project.rating_dir,
        RatingIdentity("AnatQC", "Rater_1", "SUB001"),
    )
    lower = canonical_rating_path(
        project.rating_dir,
        RatingIdentity("anatqc", "rater_1", "sub001"),
    )
    for path, rating in ((upper, upper_rating), (lower, lower_rating)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_current_payload(rating), ensure_ascii=False),
            encoding="utf-8",
        )

    scan = service.scan_rating_records()

    assert {record.path for record in scan.records} == {upper, lower}
    collisions = [
        issue
        for issue in scan.errors
        if issue.code == "casefold_identity_collision"
    ]
    assert len(collisions) == 1
    assert collisions[0].path == lower
    assert str(upper) in collisions[0].message
    assert str(lower) in collisions[0].message
