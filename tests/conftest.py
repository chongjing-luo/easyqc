from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def easyqc_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def legacy_easyqc_root(easyqc_root: Path) -> Path:
    return easyqc_root.parent / "easyqc_back"


@pytest.fixture
def fixtures_dir(easyqc_root: Path) -> Path:
    return easyqc_root / "tests" / "fixtures"


@pytest.fixture
def sample_project_dir(tmp_path: Path, fixtures_dir: Path) -> Path:
    project_dir = tmp_path / "easyqc_SAMPLE"
    table_dir = project_dir / "Table"
    rating_dir = project_dir / "RatingFiles" / "example" / "rater1"

    table_dir.mkdir(parents=True)
    rating_dir.mkdir(parents=True)

    shutil.copy2(fixtures_dir / "sample_settings.json", project_dir / "settings_SAMPLE.json")
    shutil.copy2(fixtures_dir / "sample_ezqc_all.csv", table_dir / "ezqc_all.csv")

    source_rating = (
        fixtures_dir
        / "sample_ratings"
        / "example"
        / "rater1"
        / "example._.SUB001._.rater1._.Good._.True.json"
    )
    shutil.copy2(source_rating, rating_dir / source_rating.name)

    return project_dir


@pytest.fixture
def ccnppeki_compat_project_dir(tmp_path: Path, fixtures_dir: Path) -> Path:
    source_dir = fixtures_dir / "ccnppeki_compat" / "easyqc_CCNPPEKI_COMPAT"
    project_dir = tmp_path / "easyqc_CCNPPEKI_COMPAT"
    shutil.copytree(source_dir, project_dir)
    return project_dir
