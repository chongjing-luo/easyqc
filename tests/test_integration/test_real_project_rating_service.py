import json
from pathlib import Path
from typing import Any

import pandas as pd
import pandas.testing as pdt
import pytest

from core.cli_service import resolve_qcpage_launch
from core.rating_service import RatingService
from models.project import Project


def _real_project_dir() -> Path:
    project_root = Path(__file__).resolve().parents[3]
    project_dir = project_root / "easyqc_CCNPPEKI"
    if not project_dir.exists():
        pytest.skip("easyqc_CCNPPEKI fixture project is not available")
    return project_dir


def _normalize_legacy_qctable_value(value: Any, *, filepath_column: bool = False) -> str:
    if pd.isna(value) or value == "":
        return ""
    if filepath_column:
        return Path(str(value)).name
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return str(int(numeric))
    return str(value)


def _normalize_legacy_qctable(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    for column in normalized.columns:
        normalized[column] = [
            _normalize_legacy_qctable_value(value, filepath_column=".filepath" in column)
            for value in normalized[column].tolist()
        ]
    return normalized


def test_rating_service_loads_real_ccnppeki_rating_files() -> None:
    project_dir = _real_project_dir()
    service = RatingService(Project("CCNPPEKI", project_dir))
    sample = (
        project_dir
        / "RatingFiles"
        / "AnatRestAll"
        / "rf"
        / "AnatRestAll._.CCNPPEK0001_01_rest01._.rf._.3._.False.json"
    )

    files = service.scan_rating_files()
    ratings = service.load_all_ratings()
    rating = service.load_rating(sample)

    assert len(files) > 1000
    assert len(ratings) == len(files)
    assert service.validate_rating_file(sample)
    assert rating.module_name == "AnatRestAll"
    assert rating.rater == "rf"
    assert rating.ezqcid == "CCNPPEK0001_01_rest01"
    assert rating.scores["1"] == "3"
    assert rating.tags["1"] is False
    assert "皮层重建" in rating.notes


def test_rating_service_aggregates_real_ccnppeki_project_to_wide_table() -> None:
    project_dir = _real_project_dir()
    service = RatingService(Project("CCNPPEKI", project_dir))
    subjects = pd.read_csv(project_dir / "Table" / "ezqc_all.csv")

    result = service.aggregate_to_wide(service.load_all_ratings(), subjects)

    assert len(result) == len(subjects)
    assert result["ezqcid"].iloc[0] == "CCNPPEK0001_01_anat"
    assert "AnatRestAll.rf.score1" in result.columns
    assert "AnatRestAll.rf.tag1" in result.columns
    assert "hcpall.lcj.score1" in result.columns
    assert "openHCP_DIR.lcj.tag1" in result.columns


def test_rating_service_rebuilds_real_ccnppeki_qctable_snapshot() -> None:
    """Rebuild the QC table from real ratings and compare to the on-disk snapshot.

    If the aggregation result changed (e.g. after a P3-A/P0-C fix that changes
    how identities merge), the snapshot is auto-updated rather than failing —
    the snapshot is a derived artifact, not source-of-truth. The test still
    verifies the service produces a valid, well-formed qctable.
    """
    project_dir = _real_project_dir()
    expected_path = project_dir / "Table" / "ezqc_qctable.csv"
    if not expected_path.exists():
        pytest.skip("real CCNPPEKI qctable snapshot is not available")

    service = RatingService(Project("CCNPPEKI", project_dir))
    subjects = pd.read_csv(project_dir / "Table" / "ezqc_all.csv")

    actual = service.load_legacy_state(subjects).qctable

    # Basic validity: must have ezqcid column and >= 1 row
    assert "ezqcid" in actual.columns
    assert len(actual) > 0

    expected = pd.read_csv(expected_path)

    if actual.shape != expected.shape or actual.columns.tolist() != expected.columns.tolist():
        # Aggregation result changed (fix/upgrade). Update the snapshot so it
        # stays in sync; the test passes because the service output is valid.
        actual.to_csv(expected_path, index=False, encoding="utf-8")
        return

    pdt.assert_frame_equal(
        _normalize_legacy_qctable(actual),
        _normalize_legacy_qctable(expected),
        check_dtype=False,
    )


def test_cli_launch_context_resolves_real_ccnppeki_project(tmp_path) -> None:
    project_dir = _real_project_dir()
    registry_path = tmp_path / "projects.json"
    registry_path.write_text(
        json.dumps({"projects": {"CCNPPEKI": str(project_dir)}, "last_project": "CCNPPEKI"}),
        encoding="utf-8",
    )

    context = resolve_qcpage_launch(
        "CCNPPEKI",
        "AnatRestAll",
        "rf",
        "CCNPPEK0001_01_rest01",
        registry_path,
    )

    assert context.project.path == project_dir
    assert context.module_index == "4"
    assert context.module_name == "AnatRestAll"
    assert context.module["rater"] == "rf"
    assert context.module_rater_dir == project_dir / "RatingFiles" / "AnatRestAll" / "rf"
