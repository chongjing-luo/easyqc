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


# 16-key schema is the durable contract (verified against real
# easyqc_CCNPPEKI settings + rating JSON). watch_mode + button are the two
# keys the model used to drop silently. button was already modeled; watch_mode
# was not (BUG-1).
_ALL_SIXTEEN_KEYS = {
    "name", "label", "rater", "ezqcid", "watch_mode", "interper", "code",
    "code_exe", "tags", "scores", "notes", "time", "control", "showing",
    "select_filter", "button",
}


def _sixteen_key_legacy_module() -> dict:
    """Full 16-key legacy module dict matching the real CCNPPEKI schema."""
    return {
        "name": "AnatRestAll",
        "label": "Anatomical + Rest QC",
        "rater": "zhuyan",
        "ezqcid": "CCNPPEK0001_01_anat",
        "watch_mode": False,
        "interper": "shell",
        "code": "freeview ${subjects_dir}/${subid}/anat/${subid}_anat.nii.gz",
        "code_exe": None,
        "tags": {"1": {"label": "bad_quality", "value": False}},
        "scores": {"1": {"label": "overall", "num": "1-5", "num_": "1,2,3,4,5", "value": "3"}},
        "notes": None,
        "time": "2024-06-15 14:32:01",
        "control": False,
        "showing": True,
        "select_filter": None,
        "button": {},
    }


def test_qcmodule_sixteen_key_round_trip_preserves_all_keys() -> None:
    """BUG-1: from_legacy_dict → to_legacy_dict must preserve all 16 keys,
    including watch_mode (previously dropped silently)."""
    legacy = _sixteen_key_legacy_module()

    module = QCModule.from_legacy_dict(legacy)
    result = module.to_legacy_dict()

    assert set(result.keys()) == _ALL_SIXTEEN_KEYS, (
        f"missing keys: {_ALL_SIXTEEN_KEYS - set(result.keys())}; "
        f"extra keys: {set(result.keys()) - _ALL_SIXTEEN_KEYS}"
    )
    assert result["watch_mode"] is False


def test_qcmodule_watch_mode_field_round_trip_values() -> None:
    """watch_mode=True must survive the round-trip, not silently reset to default."""
    legacy = _sixteen_key_legacy_module()
    legacy["watch_mode"] = True

    module = QCModule.from_legacy_dict(legacy)
    assert module.watch_mode is True
    assert module.to_legacy_dict()["watch_mode"] is True


def test_qcmodule_watch_mode_defaults_false_when_absent() -> None:
    """Legacy v0 module without watch_mode key reads as False (backward-compat)."""
    legacy = _sixteen_key_legacy_module()
    del legacy["watch_mode"]

    module = QCModule.from_legacy_dict(legacy)
    assert module.watch_mode is False
    assert module.to_legacy_dict()["watch_mode"] is False
