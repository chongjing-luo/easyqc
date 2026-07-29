"""Session-scoped canonical rating lookup behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from core.qc_workflow_service import QcWorkflowService
from core.rating_identity import RatingIdentity, canonical_rating_path
from core.rating_service import RatingRaterDirectoryIndex, RatingService
from models.rating import Rating


def _payload(module_name: str, rater: str, easyqcid: str) -> dict[str, object]:
    return {
        "schema_version": 3,
        "name": module_name,
        "label": "Synthetic QC",
        "rater": rater,
        "easyqcid": easyqcid,
        "scores": {"1": {"label": "Quality", "value": "Good"}},
        "tags": {"1": {"label": "Artifact", "value": False}},
        "notes": "synthetic",
        "time": "2026-07-28 12:00:00",
        "code_exe": {},
    }


def _write_rating(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _rating(module_name: str, rater: str, easyqcid: str) -> Rating:
    return Rating.from_legacy_dict(_payload(module_name, rater, easyqcid))


def _path(
    target_dir: Path,
    module_name: str,
    rater: str,
    easyqcid: str,
) -> Path:
    identity = RatingIdentity(module_name, rater, easyqcid)
    return canonical_rating_path(target_dir.parent.parent, identity)


def test_rater_directory_index_finds_only_the_exact_canonical_path(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    first = _write_rating(
        _path(target_dir, "AnatQC", "rater1", "SUB001"),
        _payload("AnatQC", "rater1", "SUB001"),
    )
    second = _write_rating(
        _path(target_dir, "AnatQC", "rater1", "SUB002"),
        _payload("AnatQC", "rater1", "SUB002"),
    )
    index = RatingRaterDirectoryIndex.build(
        target_dir,
        module_name="AnatQC",
        rater="rater1",
    )

    assert index.find("SUB001") == [first]
    assert index.find("SUB002") == [second]
    assert index.find("SUB003") == []


def test_indexed_save_is_immediately_available(tmp_path: Path) -> None:
    target_dir = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    index = RatingRaterDirectoryIndex.build(
        target_dir,
        module_name="AnatQC",
        rater="rater1",
    )

    saved = RatingService.save_rating_to_rater_dir(
        target_dir,
        _rating("AnatQC", "rater1", "SUB001"),
        directory_index=index,
    )

    assert index.find("SUB001") == [saved]


def test_index_finds_external_canonical_file_without_refresh_state(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    index = RatingRaterDirectoryIndex.build(
        target_dir,
        module_name="AnatQC",
        rater="rater1",
    )
    external = _write_rating(
        _path(target_dir, "AnatQC", "rater1", "SUB001"),
        _payload("AnatQC", "rater1", "SUB001"),
    )

    assert index.find("SUB001") == [external]


def test_qc_workflow_reuses_canonical_lookup_across_navigation(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    for easyqcid in ("SUB001", "SUB002"):
        _write_rating(
            _path(target_dir, "AnatQC", "rater1", easyqcid),
            _payload("AnatQC", "rater1", easyqcid),
        )
    module = {
        "name": "AnatQC",
        "label": "Synthetic QC",
        "rater": "rater1",
        "easyqcid": None,
        "scores": {
            "1": {
                "label": "Quality",
                "num": "Bad,Good",
                "num_": "Bad,Good",
                "value": None,
            }
        },
        "tags": {"1": {"label": "Artifact", "value": False}},
        "notes": None,
        "time": None,
        "code_exe": None,
    }
    workflow = QcWorkflowService(
        module,
        pd.DataFrame({"easyqcid": ["SUB001", "SUB002"]}),
        rating_dir=target_dir,
    )

    assert workflow.current_module.scores["1"].value == "Good"
    assert workflow.navigate_to("SUB002")
    assert workflow.current_module.scores["1"].value == "Good"
