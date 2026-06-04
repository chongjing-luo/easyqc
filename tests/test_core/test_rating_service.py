import json
from pathlib import Path

import pandas as pd

from core.rating_service import RatingService
from models.project import Project
from models.qcmodule import QCModule
from models.rating import Rating
from utils.projects_manager import ProjectManager


def _project(project_dir: Path) -> Project:
    return Project("SAMPLE", project_dir)


def _synthetic_legacy_rating(
    module_name: str,
    rater: str,
    ezqcid: str,
    score1: str,
    score2: str,
    tag1: bool,
) -> Rating:
    return Rating.from_legacy_dict(
        {
            "name": module_name,
            "label": module_name,
            "rater": rater,
            "ezqcid": ezqcid,
            "scores": {
                "1": {"label": "overall", "num": "1-5", "num_": "1,2,3,4,5", "value": score1},
                "2": {"label": "artifact", "num": "1-3", "num_": "1,2,3", "value": score2},
            },
            "tags": {
                "1": {"label": "needs_review", "value": tag1},
            },
            "notes": f"{module_name}/{rater}/{ezqcid}",
            "code": "freeview $image",
            "code_exe": {"1": "freeview /tmp/example.nii.gz"},
            "interper": "shell",
            "control": True,
            "select_filter": None,
            "showing": True,
        }
    )


def test_rating_service_scans_and_validates_fixture(sample_project_dir: Path) -> None:
    service = RatingService(_project(sample_project_dir))
    files = service.scan_rating_files()

    assert len(files) == 1
    assert service.validate_rating_file(files[0])


def test_rating_service_rejects_mismatched_directory(tmp_path, fixtures_dir: Path) -> None:
    bad_dir = tmp_path / "easyqc_SAMPLE" / "RatingFiles" / "other" / "rater1"
    bad_dir.mkdir(parents=True)
    source = (
        fixtures_dir
        / "sample_ratings"
        / "example"
        / "rater1"
        / "example._.SUB001._.rater1._.Good._.True.json"
    )
    target = bad_dir / source.name
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    assert not RatingService(_project(tmp_path / "easyqc_SAMPLE")).validate_rating_file(target)


def test_rating_service_loads_rating_and_builds_dict(sample_project_dir: Path) -> None:
    service = RatingService(_project(sample_project_dir))
    rating = service.load_all_ratings()[0]

    assert rating.module_name == "example"
    assert rating.scores["1"] == "Good"
    assert service.build_rating_dict([rating])["SUB001"]["example-rater1"]["name"] == "example"


def test_rating_service_aggregate_matches_legacy_wide_shape(sample_project_dir: Path) -> None:
    service = RatingService(_project(sample_project_dir))
    subjects = pd.read_csv(sample_project_dir / "Table" / "ezqc_all.csv")

    result = service.aggregate_to_wide(service.load_all_ratings(), subjects)

    assert result.loc[0, "ezqcid"] == "SUB001"
    assert result.loc[0, "example.rater1.score1"] == "Good"
    assert result.loc[0, "example.rater1.tag1"] is True
    assert "example.rater1.code" not in result.columns


def test_rating_service_handles_multi_module_multi_rater_synthetic_project(tmp_path) -> None:
    project = Project("PRESSURE", tmp_path / "easyqc_PRESSURE")
    service = RatingService(project)
    subject_ids = [f"SUB{index:03d}" for index in range(30)]
    subjects = pd.DataFrame(
        {
            "ezqcid": subject_ids,
            "site": [f"site{index % 3}" for index in range(len(subject_ids))],
        }
    )
    modules = ["Anat", "Rest", "Surface"]
    raters = ["r1", "r2", "r3"]

    for subject_index, ezqcid in enumerate(subject_ids):
        for module_index, module_name in enumerate(modules):
            for rater_index, rater in enumerate(raters):
                service.save_rating(
                    _synthetic_legacy_rating(
                        module_name,
                        rater,
                        ezqcid,
                        score1=str((subject_index + module_index + rater_index) % 5 + 1),
                        score2=str(subject_index % 3 + 1),
                        tag1=(subject_index + module_index) % 2 == 0,
                    )
                )

    replacement = service.save_rating(
        _synthetic_legacy_rating("Anat", "r1", "SUB000", score1="5", score2="3", tag1=True)
    )

    files_for_replaced_rating = list((project.rating_dir / "Anat" / "r1").glob("Anat._.SUB000._.r1*"))
    ratings = service.load_all_ratings()
    result = service.aggregate_to_wide(ratings, subjects)

    assert files_for_replaced_rating == [replacement]
    assert len(ratings) == len(subject_ids) * len(modules) * len(raters)
    assert len(result) == len(subject_ids)
    assert {
        "Anat.r1.score1",
        "Rest.r2.score1",
        "Surface.r3.score2",
        "Surface.r3.tag1",
    }.issubset(result.columns)
    assert not any(".code" in column or ".code_exe" in column for column in result.columns)
    assert result.loc[result["ezqcid"] == "SUB000", "Anat.r1.score1"].iloc[0] == "5"
    assert result.loc[result["ezqcid"] == "SUB010", "Rest.r2.score1"].iloc[0] == "3"
    assert result.loc[result["ezqcid"] == "SUB010", "site"].iloc[0] == "site1"


def test_rating_service_loads_legacy_gui_state(sample_project_dir: Path) -> None:
    service = RatingService(_project(sample_project_dir))
    subjects = pd.read_csv(sample_project_dir / "Table" / "ezqc_all.csv")

    state = service.load_legacy_state(subjects)

    assert len(state.ratings) == 1
    assert state.rating_dict["SUB001"]["example-rater1"]["scores"]["1"]["value"] == "Good"
    assert state.qctable.loc[0, "ezqcid"] == "SUB001"
    assert state.qctable.loc[0, "example.rater1.score1"] == "Good"
    assert state.original_table.loc[0, "filename"].startswith("example._.SUB001")
    assert state.original_table.loc[0, "filepath"].endswith(".json")
    assert state.original_wide_table.loc[0, "example.rater1.filename"].startswith("example._.SUB001")


def test_rating_service_save_rating_is_atomic_and_cleans_old_files(tmp_path, fixtures_dir: Path) -> None:
    settings = json.loads((fixtures_dir / "sample_settings.json").read_text(encoding="utf-8"))
    module = QCModule.from_legacy_dict(settings["qcmodule"]["1"])
    rating = Rating.from_module(module)
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    service = RatingService(project)
    old_dir = project.rating_dir / "example" / "rater1"
    old_dir.mkdir(parents=True)
    old_file = old_dir / "example._.SUB001._.rater1._.Old._.False.json"
    old_file.write_text("{}", encoding="utf-8")

    saved_path = service.save_rating(rating)

    assert saved_path.exists()
    assert not old_file.exists()
    payload = json.loads(saved_path.read_text(encoding="utf-8"))
    assert payload["scores"]["1"]["label"] == "Overall quality"
    assert payload["scores"]["1"]["value"] == "Good"


def test_rating_service_saved_json_is_readable_by_legacy_loader(tmp_path, fixtures_dir: Path) -> None:
    settings = json.loads((fixtures_dir / "sample_settings.json").read_text(encoding="utf-8"))
    module = QCModule.from_legacy_dict(settings["qcmodule"]["1"])
    rating = Rating.from_module(module)
    saved_path = RatingService(Project("SAMPLE", tmp_path / "easyqc_SAMPLE")).save_rating(rating)

    result = ProjectManager().load_rating_json(str(saved_path))

    assert result.loc[0, "module_name"] == "example"
    assert result.loc[0, "score1"] == "Good"
