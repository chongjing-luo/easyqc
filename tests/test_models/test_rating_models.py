import json
from pathlib import Path

from models.qcmodule import QCModule
from models.rating import Rating


def test_rating_round_trip_preserves_legacy_payload_shape(fixtures_dir: Path) -> None:
    legacy = json.loads(
        (
            fixtures_dir
            / "sample_ratings"
            / "example"
            / "rater1"
            / "example._.SUB001._.rater1._.Good._.True.json"
        ).read_text(encoding="utf-8")
    )

    rating = Rating.from_legacy_dict(legacy)
    result = rating.to_legacy_dict()

    assert rating.filename == "example._.SUB001._.rater1._.Good._.True.json"
    assert result["name"] == "example"
    assert result["scores"]["1"]["label"] == "Overall quality"
    assert result["scores"]["1"]["value"] == "Good"
    assert result["tags"]["1"]["value"] is True
    assert result["time"] == "2026-06-02 00:00:00"


def test_rating_from_module_and_apply_to_module(fixtures_dir: Path) -> None:
    settings = json.loads((fixtures_dir / "sample_settings.json").read_text(encoding="utf-8"))
    module = QCModule.from_legacy_dict(settings["qcmodule"]["1"])
    rating = Rating.from_module(module)

    module.scores["1"].value = None
    module.tags["1"].value = False
    rating.apply_to_module(module)

    assert module.scores["1"].value == "Good"
    assert module.tags["1"].value is True


def test_rating_json_file_round_trip(tmp_path, fixtures_dir: Path) -> None:
    settings = json.loads((fixtures_dir / "sample_settings.json").read_text(encoding="utf-8"))
    module = QCModule.from_legacy_dict(settings["qcmodule"]["1"])
    rating = Rating.from_module(module)
    path = tmp_path / rating.filename

    rating.to_json_file(path, module)
    loaded = Rating.from_json_file(path)

    assert loaded.module_name == "example"
    assert loaded.scores["1"] == "Good"
