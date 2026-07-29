"""Session-scoped rating lookup index behavior and safety."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
import pytest

from core.qc_workflow_service import QcWorkflowService
from core.rating_service import (
    RatingLegacyConflictError,
    RatingRaterDirectoryIndex,
    RatingService,
)
from models.rating import Rating


def _payload(module_name: str, rater: str, ezqcid: str) -> dict[str, object]:
    return {
        "name": module_name,
        "label": "Synthetic QC",
        "rater": rater,
        "ezqcid": ezqcid,
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


def _legacy_path(
    target_dir: Path,
    module_name: str,
    rater: str,
    ezqcid: str,
) -> Path:
    return target_dir / (
        f"{module_name}._.{ezqcid}._.{rater}._.Good._.False.json"
    )


def _rating(module_name: str, rater: str, ezqcid: str) -> Rating:
    payload = _payload(module_name, rater, ezqcid)
    return Rating.from_legacy_dict(payload)


def test_rater_directory_index_scans_once_for_repeated_legacy_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    first = _write_rating(
        _legacy_path(target_dir, "AnatQC", "rater1", "SUB001"),
        _payload("AnatQC", "rater1", "SUB001"),
    )
    second = _write_rating(
        _legacy_path(target_dir, "AnatQC", "rater1", "SUB002"),
        _payload("AnatQC", "rater1", "SUB002"),
    )
    real_scan = RatingRaterDirectoryIndex._scan_legacy_paths
    scans = 0

    def counted_scan(index: RatingRaterDirectoryIndex):
        nonlocal scans
        scans += 1
        return real_scan(index)

    monkeypatch.setattr(RatingRaterDirectoryIndex, "_scan_legacy_paths", counted_scan)

    index = RatingRaterDirectoryIndex.build(
        target_dir,
        module_name="AnatQC",
        rater="rater1",
    )

    assert index.find("SUB001") == [first]
    assert index.find("SUB002") == [second]
    assert index.find("SUB003") == []
    assert scans == 1


def test_indexed_save_updates_directory_signature_without_rescanning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target_dir.mkdir(parents=True)
    real_scan = RatingRaterDirectoryIndex._scan_legacy_paths
    scans = 0

    def counted_scan(index: RatingRaterDirectoryIndex):
        nonlocal scans
        scans += 1
        return real_scan(index)

    monkeypatch.setattr(RatingRaterDirectoryIndex, "_scan_legacy_paths", counted_scan)
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
    assert scans == 1


def test_index_refreshes_after_external_legacy_file_appears(
    tmp_path: Path,
) -> None:
    target_dir = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target_dir.mkdir(parents=True)
    indexed_mtime = target_dir.stat().st_mtime_ns
    index = RatingRaterDirectoryIndex.build(
        target_dir,
        module_name="AnatQC",
        rater="rater1",
    )
    legacy = _write_rating(
        _legacy_path(target_dir, "AnatQC", "rater1", "SUB001"),
        _payload("AnatQC", "rater1", "SUB001"),
    )
    metadata = target_dir.stat()
    os.utime(
        target_dir,
        ns=(metadata.st_atime_ns, max(metadata.st_mtime_ns, indexed_mtime) + 1),
    )

    assert index.find("SUB001") == [legacy]
    with pytest.raises(RatingLegacyConflictError):
        RatingService.save_rating_to_rater_dir(
            target_dir,
            _rating("AnatQC", "rater1", "SUB001"),
            directory_index=index,
        )


def test_qc_workflow_reuses_one_rater_directory_index_across_navigation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    for ezqcid in ("SUB001", "SUB002"):
        _write_rating(
            _legacy_path(target_dir, "AnatQC", "rater1", ezqcid),
            _payload("AnatQC", "rater1", ezqcid),
        )
    real_scan = RatingRaterDirectoryIndex._scan_legacy_paths
    scans = 0

    def counted_scan(index: RatingRaterDirectoryIndex):
        nonlocal scans
        scans += 1
        return real_scan(index)

    monkeypatch.setattr(
        RatingRaterDirectoryIndex,
        "_scan_legacy_paths",
        counted_scan,
    )
    module = {
        "name": "AnatQC",
        "label": "Synthetic QC",
        "rater": "rater1",
        "ezqcid": None,
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
        pd.DataFrame({"ezqcid": ["SUB001", "SUB002"]}),
        rating_dir=target_dir,
    )

    assert workflow.current_module.scores["1"].value == "Good"
    assert workflow.navigate_to("SUB002")
    assert workflow.current_module.scores["1"].value == "Good"
    assert scans == 1
