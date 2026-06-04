from pathlib import Path


def test_test_fixtures_are_available(
    fixtures_dir: Path,
    legacy_easyqc_root: Path,
    sample_project_dir: Path,
) -> None:
    assert legacy_easyqc_root.exists()
    assert (fixtures_dir / "sample_settings.json").exists()
    assert (fixtures_dir / "sample_ezqc_all.csv").exists()
    assert (sample_project_dir / "settings_SAMPLE.json").exists()
    assert (sample_project_dir / "Table" / "ezqc_all.csv").exists()
    assert (
        sample_project_dir
        / "RatingFiles"
        / "example"
        / "rater1"
        / "example._.SUB001._.rater1._.Good._.True.json"
    ).exists()
