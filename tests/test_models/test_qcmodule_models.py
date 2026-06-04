import json
from pathlib import Path

from models.qcmodule import QCModule, Score, Tag


def test_score_and_tag_round_trip() -> None:
    score = Score.from_legacy_dict("1", {"label": "Quality", "num": "1-3", "num_": "1,2,3", "value": "2"})
    tag = Tag.from_legacy_dict("1", {"label": "Artifact", "value": True})

    assert score.allowed_values == ["1", "2", "3"]
    assert score.to_legacy_dict() == {"label": "Quality", "num": "1-3", "num_": "1,2,3", "value": "2"}
    assert tag.to_legacy_dict() == {"label": "Artifact", "value": True}


def test_qcmodule_round_trip_from_legacy_fixture(fixtures_dir: Path) -> None:
    settings = json.loads((fixtures_dir / "sample_settings.json").read_text(encoding="utf-8"))
    legacy = settings["qcmodule"]["1"]

    module = QCModule.from_legacy_dict(legacy)
    result = module.to_legacy_dict()

    assert result["name"] == legacy["name"]
    assert result["scores"]["1"]["num_"] == "Poor,Fair,Good"
    assert result["scores"]["1"]["value"] == "Good"
    assert result["tags"]["1"]["value"] is True
    assert result["time"] == "2026-06-02 00:00:00"


def test_qcmodule_accepts_missing_optional_legacy_fields() -> None:
    module = QCModule.from_legacy_dict({"name": "example", "label": "Example"})

    assert module.name == "example"
    assert module.scores == {}
    assert module.to_legacy_dict()["interper"] == "shell"
