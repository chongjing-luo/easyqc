from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.rating_identity import RatingIdentity, canonical_rating_path
from core.rating_service import (
    RatingLegacyConflictError,
    RatingScanError,
    RatingService,
)
from models.project import Project
from models.rating import Rating


def _rating() -> Rating:
    return Rating.from_legacy_dict(
        {
            "name": "AnatQC",
            "label": "Anatomical",
            "rater": "rater_1",
            "ezqcid": "SUB001",
            "scores": {"1": {"label": "Overall", "value": "Good"}},
            "tags": {"1": {"label": "Review", "value": False}},
            "notes": None,
            "time": "2026-07-28 10:00:00",
            "code": "viewer",
            "code_exe": {},
            "interper": "shell",
            "control": True,
            "select_filter": None,
            "showing": True,
        }
    )


def _uppercase_legacy_path(project: Project) -> Path:
    return (
        project.rating_dir
        / "AnatQC"
        / "rater_1"
        / "AnatQC._.SUB001._.rater_1._.Good._.False.JSON"
    )


def test_case_variant_legacy_suffix_is_visible_and_blocks_normal_save(
    tmp_path,
) -> None:
    project = Project("SYNTHETIC", tmp_path / "easyqc_SYNTHETIC")
    service = RatingService(project)
    rating = _rating()
    legacy = _uppercase_legacy_path(project)
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps(rating.to_legacy_dict(), ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(RatingScanError) as exc_info:
        service.find_rating_files_in_rater_dir(
            legacy.parent,
            "AnatQC",
            "SUB001",
            "rater_1",
        )
    assert exc_info.value.result.errors[0].path == legacy
    assert exc_info.value.result.errors[0].code == "filename"

    with pytest.raises(RatingLegacyConflictError):
        service.save_rating(rating)

    canonical = canonical_rating_path(
        project.rating_dir,
        RatingIdentity("AnatQC", "rater_1", "SUB001"),
    )
    assert legacy.exists()
    assert not canonical.exists()
