from __future__ import annotations

import json

import pandas as pd

from core.project_service import ProjectService
from core.rating_service import RatingService
from core.table_service import TABLE_ALL, TABLE_QCTABLE, TableService
from models.rating import Rating


def test_schema_v3_project_list_rating_and_result_use_easyqc_contract(
    tmp_path,
) -> None:
    project_service = ProjectService(tmp_path / "projects.json")
    project = project_service.create("SCHEMA3", tmp_path)

    assert project_service.settings["schema_version"] == 3

    tables = TableService()
    subjects = pd.DataFrame(
        {"easyqcid": ["ROW000001"], "image": ["/data/ROW000001.nii.gz"]}
    )
    tables.save_table(project, TABLE_ALL, subjects)

    assert tables.table_path(project, TABLE_ALL).name == "easyqc_all.csv"
    assert not (project.table_dir / "ezqc_all.csv").exists()
    assert tables.load_table(project, TABLE_ALL)["easyqcid"].tolist() == [
        "ROW000001"
    ]

    rating = Rating(
        module_name="AnatQC",
        rater="rater_1",
        easyqcid="ROW000001",
        scores={"1": "Good"},
        tags={"1": False},
        module_payload={
            "name": "AnatQC",
            "label": "Anatomical QC",
            "rater": "rater_1",
            "easyqcid": "ROW000001",
            "scores": {
                "1": {
                    "label": "Quality",
                    "num": "Poor,Good",
                    "num_": "Poor,Good",
                    "value": "Good",
                }
            },
            "tags": {"1": {"label": "Review", "value": False}},
            "notes": None,
            "time": None,
            "code_exe": {},
        },
    )
    rating_path = RatingService(project).save_rating(rating)
    payload = json.loads(rating_path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 3
    assert payload["easyqcid"] == "ROW000001"
    assert "ezqcid" not in payload

    result = RatingService(project).aggregate_to_wide([rating], subjects)
    tables.save_table(project, TABLE_QCTABLE, result)

    assert result["easyqcid"].tolist() == ["ROW000001"]
    assert tables.table_path(project, TABLE_QCTABLE).name == "easyqc_qctable.csv"
