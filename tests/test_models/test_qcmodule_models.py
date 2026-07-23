import json
from pathlib import Path

from models.qcmodule import QCModule, Score, Tag
from models.rating import Rating


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


# The 16-key legacy schema was verified against real easyqc_CCNPPEKI settings
# and rating JSON. qc_filter is the one optional structured extension.
_ALL_MODULE_KEYS = {
    "name", "label", "rater", "ezqcid", "watch_mode", "interper", "code",
    "code_exe", "tags", "scores", "notes", "time", "control", "showing",
    "select_filter", "qc_filter", "button",
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


def test_qcmodule_round_trip_preserves_all_module_keys() -> None:
    """The module model must preserve the legacy keys plus qc_filter."""
    legacy = _sixteen_key_legacy_module()

    module = QCModule.from_legacy_dict(legacy)
    result = module.to_legacy_dict()

    assert set(result.keys()) == _ALL_MODULE_KEYS, (
        f"missing keys: {_ALL_MODULE_KEYS - set(result.keys())}; "
        f"extra keys: {set(result.keys()) - _ALL_MODULE_KEYS}"
    )
    assert result["watch_mode"] is False
    assert result["qc_filter"] is None


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


def test_qcmodule_and_rating_snapshot_preserve_structured_qc_filter() -> None:
    legacy = _sixteen_key_legacy_module()
    legacy["qc_filter"] = {
        "schema_version": 1,
        "group_join": "all",
        "groups": [
            {
                "group_id": "site-group",
                "join": "any",
                "conditions": [
                    {
                        "column": "site",
                        "operator": "==",
                        "value": "A",
                        "condition_id": "site-a",
                        "enabled": True,
                    }
                ],
            }
        ],
    }

    module = QCModule.from_legacy_dict(legacy)
    module_payload = module.to_legacy_dict()
    rating_payload = Rating.from_module(module).to_legacy_dict()

    assert module.qc_filter == legacy["qc_filter"]
    assert module_payload["qc_filter"] == legacy["qc_filter"]
    assert rating_payload["qc_filter"] == legacy["qc_filter"]
