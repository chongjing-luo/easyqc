"""Schema-v3 CSV identity acquisition tests."""

from __future__ import annotations

import pandas as pd

from core.configuration_service import ConfigurationService
from core.project_service import ProjectService
from core.table_service import TABLE_ALL, TableService
from models.project import Project
from models.subject_table import SubjectTable


def _csv(path) -> None:
    path.write_text(
        "easyqcid,visit,note\n001,1,NA\nNA,2,value\n01-A,3,value\n",
        encoding="utf-8",
    )


def test_table_and_subject_csv_acquisition_preserve_na_and_leading_zero(
    tmp_path,
) -> None:
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    source = project.table_dir / f"{TABLE_ALL}.csv"
    source.parent.mkdir(parents=True)
    _csv(source)

    loaded = TableService().load_table(project, TABLE_ALL)
    subject_table = SubjectTable.from_csv(source).dataframe

    for frame in (loaded, subject_table):
        assert frame["easyqcid"].tolist() == ["001", "NA", "01-A"]
        assert frame["visit"].tolist() == [1, 2, 3]
        assert pd.isna(frame.loc[0, "note"])


def test_configuration_csv_draft_and_import_preserve_exact_easyqcid(
    tmp_path,
) -> None:
    projects = ProjectService(tmp_path / "projects.json")
    service = ConfigurationService(projects, TableService())
    service.create_project("SAMPLE", tmp_path)
    source = tmp_path / "incoming.csv"
    _csv(source)

    draft = service.draft_from_file(source)
    service.import_subject_csv(source)

    assert draft["easyqcid"].tolist() == ["001", "NA", "01-A"]
    assert service.subjects()["easyqcid"].tolist() == ["001", "NA", "01-A"]
    assert draft["visit"].tolist() == [1, 2, 3]
    assert pd.isna(draft.loc[0, "note"])


def test_configuration_excel_converter_preserves_values_without_cleaning(
    tmp_path,
    monkeypatch,
) -> None:
    projects = ProjectService(tmp_path / "projects.json")
    service = ConfigurationService(projects, TableService())
    service.create_project("SAMPLE", tmp_path)
    source = tmp_path / "incoming.xlsx"
    source.write_bytes(b"synthetic workbook placeholder")
    captured = {}

    def fake_read_excel(path, **kwargs):
        captured.update(kwargs)
        converter = kwargs["converters"]["easyqcid"]
        return pd.DataFrame(
            {
                "easyqcid": [converter("NA"), converter("001"), converter(7)],
                "visit": [1, 2, 3],
            }
        )

    monkeypatch.setattr("core.configuration_service.pd.read_excel", fake_read_excel)

    draft = service.draft_from_file(source)

    assert draft["easyqcid"].tolist() == ["NA", "001", 7]
    assert set(captured) == {"converters"}
