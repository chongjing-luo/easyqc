"""Operation-count guards for large rating aggregation."""

from pathlib import Path

from core.rating_service import RatingService
from models.project import Project
from models.rating import Rating


def _rating(easyqcid: str) -> Rating:
    return Rating.from_legacy_dict(
        {
            "name": "AnatQC",
            "label": "Anatomical QC",
            "rater": "rater_1",
            "easyqcid": easyqcid,
            "scores": {"1": {"label": "Quality", "value": "Good"}},
            "tags": {"1": {"label": "Review", "value": False}},
            "notes": None,
            "time": None,
            "code_exe": {},
        }
    )


def test_long_table_builds_one_dataframe_for_all_rating_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = RatingService(Project("SAMPLE", tmp_path / "easyqc_SAMPLE"))

    def reject_per_rating_dataframe(*_args, **_kwargs):
        raise AssertionError("aggregation created one DataFrame per rating")

    monkeypatch.setattr(
        service,
        "rating_to_flat_dataframe",
        reject_per_rating_dataframe,
    )
    frame = service.rating_records_to_long_dataframe(
        [(_rating("SUB001"), None), (_rating("SUB002"), None)]
    )

    assert frame["easyqcid"].tolist() == ["SUB001", "SUB002"]
    assert frame["module_name"].tolist() == ["AnatQC", "AnatQC"]
    assert frame["score1"].tolist() == ["Good", "Good"]
