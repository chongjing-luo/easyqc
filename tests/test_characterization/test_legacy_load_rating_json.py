import json
from pathlib import Path

import pandas as pd

from utils.projects_manager import ProjectManager


def test_load_rating_json_flattens_valid_legacy_payload(fixtures_dir: Path) -> None:
    json_file = (
        fixtures_dir
        / "sample_ratings"
        / "example"
        / "rater1"
        / "example._.SUB001._.rater1._.Good._.True.json"
    )

    result = ProjectManager().load_rating_json(str(json_file))

    row = result.iloc[0].to_dict()
    assert row["ezqcid"] == "SUB001"
    assert row["module_name"] == "example"
    assert row["score1"] == "Good"
    assert row["score1label"] == "Overall quality"
    assert row["tag1"] is True
    assert row["tag1label"] == "Visible artifact"
    assert row["filename"] == "example._.SUB001._.rater1._.Good._.True.json"


def test_load_rating_json_returns_empty_dataframe_when_payload_ezqcid_mismatches(
    tmp_path: Path,
) -> None:
    json_file = tmp_path / "example._.SUB001._.rater1._.Good._.True.json"
    json_file.write_text(
        json.dumps(
            {
                "name": "example",
                "rater": "rater1",
                "ezqcid": "SUB999",
            }
        ),
        encoding="utf-8",
    )

    result = ProjectManager().load_rating_json(str(json_file))

    assert isinstance(result, pd.DataFrame)
    assert result.empty


def test_load_rating_json_returns_empty_dataframe_on_malformed_json(tmp_path: Path) -> None:
    json_file = tmp_path / "example._.SUB001._.rater1._.Good._.True.json"
    json_file.write_text("{", encoding="utf-8")

    result = ProjectManager().load_rating_json(str(json_file))

    assert isinstance(result, pd.DataFrame)
    assert result.empty
