"""Hermetic integration coverage for the copied CCNPPEKI compatibility fixture."""

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pandas.testing as pdt
from core.cli_service import resolve_qcpage_launch
from core.rating_service import RatingService
from models.project import Project


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


def test_rating_service_loads_copied_ccnppeki_compat_rating_files(
    ccnppeki_compat_project_dir: Path,
) -> None:
    project_dir = ccnppeki_compat_project_dir
    service = RatingService(Project("CCNPPEKI_COMPAT", project_dir))
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

    assert len(files) == 10
    assert len(ratings) == len(files)
    assert service.validate_rating_file(sample)
    assert rating.module_name == "AnatRestAll"
    assert rating.rater == "rf"
    assert rating.ezqcid == "CCNPPEK0001_01_rest01"
    assert rating.scores["1"] == "3"
    assert rating.tags["1"] is False
    assert "皮层重建" in rating.notes


def test_rating_service_aggregates_copied_ccnppeki_compat_project_to_wide_table(
    ccnppeki_compat_project_dir: Path,
) -> None:
    project_dir = ccnppeki_compat_project_dir
    service = RatingService(Project("CCNPPEKI_COMPAT", project_dir))
    subjects = pd.read_csv(project_dir / "Table" / "ezqc_all.csv")

    result = service.aggregate_to_wide(service.load_all_ratings(), subjects)

    assert len(result) == len(subjects)
    assert result["ezqcid"].iloc[0] == "CCNPPEK0001_01_anat"
    assert "AnatRestAll.rf.score1" in result.columns
    assert "AnatRestAll.rf.tag1" in result.columns
    assert "hcpall.lcj.score1" in result.columns
    assert "openHCP_DIR.lcj.tag1" in result.columns


def test_rating_service_rebuilds_copied_ccnppeki_qctable_snapshot_without_writes(
    ccnppeki_compat_project_dir: Path,
) -> None:
    """Rebuild against a temporary copy and compare without updating fixtures."""

    project_dir = ccnppeki_compat_project_dir
    expected_path = project_dir / "Table" / "ezqc_qctable.csv"

    service = RatingService(Project("CCNPPEKI_COMPAT", project_dir))
    subjects = pd.read_csv(project_dir / "Table" / "ezqc_all.csv")

    actual = service.load_legacy_state(subjects).qctable

    # Basic validity: must have ezqcid column and >= 1 row
    assert "ezqcid" in actual.columns
    assert len(actual) > 0

    expected = pd.read_csv(expected_path)

    pdt.assert_frame_equal(
        _normalize_legacy_qctable(actual),
        _normalize_legacy_qctable(expected),
        check_dtype=False,
    )


def test_cli_launch_context_resolves_copied_ccnppeki_project(
    tmp_path: Path,
    ccnppeki_compat_project_dir: Path,
) -> None:
    project_dir = ccnppeki_compat_project_dir
    registry_path = tmp_path / "projects.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": {"CCNPPEKI_COMPAT": str(project_dir)},
                "last_project": "CCNPPEKI_COMPAT",
            }
        ),
        encoding="utf-8",
    )

    context = resolve_qcpage_launch(
        "CCNPPEKI_COMPAT",
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
