import json
from pathlib import Path

import pandas as pd

from utils.projects_manager import ProjectManager


def _project_manager_for(project_dir: Path) -> ProjectManager:
    pm = ProjectManager()
    pm.dt = pm.DataContainer()
    pm.dt.project = "SAMPLE"
    pm.dt.output_dir = str(project_dir)
    pm.dt.projects = {"SAMPLE": str(project_dir)}
    pm.dt.projects_info = {"projects": pm.dt.projects, "last_project": "SAMPLE"}
    pm.dt.settings = json.loads((project_dir / "settings_SAMPLE.json").read_text(encoding="utf-8"))
    pm.dt.var["ezqc_all"] = pd.read_csv(project_dir / "Table" / "ezqc_all.csv")
    return pm


def test_load_ratings_builds_wide_rating_table(sample_project_dir: Path) -> None:
    pm = _project_manager_for(sample_project_dir)

    pm.load_ratings()

    result = pm.dt.tab["ezqc_qctable"]
    assert result is not None
    assert result.loc[0, "ezqcid"] == "SUB001"
    assert result.loc[0, "example.rater1.score1"] == "Good"
    assert result.loc[0, "example.rater1.tag1"] is True
    assert (sample_project_dir / "Table" / "ezqc_qctable.csv").exists()


def test_load_ratings_returns_when_project_is_none(sample_project_dir: Path) -> None:
    pm = _project_manager_for(sample_project_dir)
    pm.dt.project = None

    pm.load_ratings()

    assert pm.dt.tab["ezqc_qctable"] is None


def test_load_ratings_keeps_ezqc_all_when_rating_dir_missing(sample_project_dir: Path) -> None:
    rating_dir = sample_project_dir / "RatingFiles"
    for rating_file in rating_dir.rglob("*.json"):
        rating_file.unlink()
    for child in sorted(rating_dir.rglob("*"), reverse=True):
        if child.is_dir():
            child.rmdir()
    rating_dir.rmdir()
    pm = _project_manager_for(sample_project_dir)

    pm.load_ratings()

    pd.testing.assert_frame_equal(pm.dt.tab["ezqc_qctable"], pm.dt.var["ezqc_all"])
