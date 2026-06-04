from pathlib import Path

from models.project import Project, ProjectRegistry


def test_project_paths_are_derived_from_project_root(tmp_path) -> None:
    project = Project(name="SAMPLE", path=tmp_path / "easyqc_SAMPLE")

    assert project.settings_path == tmp_path / "easyqc_SAMPLE" / "settings_SAMPLE.json"
    assert project.table_dir == tmp_path / "easyqc_SAMPLE" / "Table"
    assert project.rating_dir == tmp_path / "easyqc_SAMPLE" / "RatingFiles"


def test_project_registry_round_trip_legacy_dict(tmp_path) -> None:
    legacy = {"projects": {"SAMPLE": str(tmp_path / "easyqc_SAMPLE")}, "last_project": "SAMPLE"}

    registry = ProjectRegistry.from_legacy_dict(legacy)

    assert registry.projects["SAMPLE"].path == Path(legacy["projects"]["SAMPLE"])
    assert registry.to_legacy_dict() == legacy
