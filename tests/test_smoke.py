from pathlib import Path


def test_test_fixtures_are_available(
    fixtures_dir: Path,
    sample_project_dir: Path,
) -> None:
    assert (fixtures_dir / "sample_settings.json").exists()
    assert (fixtures_dir / "sample_easyqc_all.csv").exists()
    assert (sample_project_dir / "settings_SAMPLE.json").exists()
    assert (sample_project_dir / "Table" / "easyqc_all.csv").exists()
    assert (
        sample_project_dir
        / "RatingFiles"
        / "example"
        / "rater1"
        / "example-rater1-SUB001.json"
    ).exists()
