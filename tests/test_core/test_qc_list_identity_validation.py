from __future__ import annotations

import pandas as pd
import pytest

from core.configuration_service import ConfigurationError, ConfigurationService
from core.project_service import ProjectService
from core.table_service import TABLE_ALL, TableService


def _service(tmp_path) -> ConfigurationService:
    projects = ProjectService(tmp_path / "projects.json")
    service = ConfigurationService(projects, TableService())
    service.create_project("SAMPLE", tmp_path)
    return service


@pytest.mark.parametrize(
    "invalid",
    [
        7,
        None,
        " SUB001",
        "SUB001 ",
        "SUB/001",
        ".",
        "非ASCII",
    ],
)
def test_qc_list_rejects_invalid_easyqcid_without_cleaning_or_coercion(
    invalid,
    tmp_path,
) -> None:
    service = _service(tmp_path)
    service.replace_subjects(
        pd.DataFrame({"easyqcid": ["001", "NA"], "site": ["A", "B"]})
    )
    project = service.current_project
    assert project is not None
    table_path = service.table_service.table_path(project, TABLE_ALL)
    before = table_path.read_bytes()

    with pytest.raises(ConfigurationError, match="easyqcid"):
        service.replace_subjects(
            pd.DataFrame({"easyqcid": ["VALID", invalid]})
        )

    assert table_path.read_bytes() == before
    assert service.subjects()["easyqcid"].tolist() == ["001", "NA"]


def test_qc_list_rejects_case_only_identity_collision_before_write(
    tmp_path,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(ConfigurationError, match="case"):
        service.replace_subjects(
            pd.DataFrame({"easyqcid": ["SUB001", "sub001"]})
        )

    project = service.current_project
    assert project is not None
    assert not service.table_service.table_path(project, TABLE_ALL).exists()


def test_qc_list_preserves_exact_valid_identifiers(tmp_path) -> None:
    service = _service(tmp_path)
    service.replace_subjects(
        pd.DataFrame({"easyqcid": ["001", "NA", "01-A", "A.B_C"]})
    )

    assert service.subjects()["easyqcid"].tolist() == [
        "001",
        "NA",
        "01-A",
        "A.B_C",
    ]
