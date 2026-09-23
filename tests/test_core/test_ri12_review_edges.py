"""Independent RI-12 source rename persistence and revision boundaries."""

from copy import deepcopy

import pandas as pd
import pytest

from core.configuration_service import ConfigurationService
from core.project_service import ProjectService
from core.table_service import TableService, TableStateConflictError
from core.table_transform import TableTransformEngine


def _configuration(tmp_path, *, create=False):
    configuration = ConfigurationService(
        ProjectService(tmp_path / "projects.json"), TableService()
    )
    if create:
        configuration.create_project("SAMPLE", tmp_path)
    else:
        configuration.load_project("SAMPLE")
    return configuration


def test_column_rename_reopened_types_and_module_references_are_unchanged(tmp_path):
    configuration = _configuration(tmp_path, create=True)
    frame = pd.DataFrame({
        "easyqcid": ["A", "B", "C"],
        "site": ["01", "02", ""],
        "mixed": ["03", 4, None],
    })
    configuration.replace_subjects(frame)
    module = configuration.modules()[0]
    module.code = 'printf "%s" "${site}"'
    module.qc_filter = {"groups": [{"id": "g", "join": "all", "conditions": [
        {"column": "site", "operator": "==", "value": "01", "id": "c"}
    ]}]}
    configuration.save_module(module, original_name=module.name)
    before = deepcopy(configuration.modules()[0].to_legacy_dict())

    configuration.rename_subject_column("site", "session")

    reopened = _configuration(tmp_path)
    pd.testing.assert_frame_equal(
        reopened.subjects(), frame.rename(columns={"site": "session"})
    )
    assert reopened.modules()[0].to_legacy_dict() == before


def test_column_rename_cannot_overwrite_newer_list_written_during_transform(
    tmp_path, monkeypatch
):
    configuration = _configuration(tmp_path, create=True)
    configuration.replace_subjects(pd.DataFrame({"easyqcid": ["A"], "site": ["01"]}))
    newer = pd.DataFrame({"easyqcid": ["B"], "site": ["02"]})
    concurrent = _configuration(tmp_path)
    rename = TableTransformEngine.rename_columns

    def race(engine, frame, names):
        concurrent.replace_subjects(newer)
        return rename(engine, frame, names)

    monkeypatch.setattr(TableTransformEngine, "rename_columns", race)

    with pytest.raises(TableStateConflictError, match="stale"):
        configuration.rename_subject_column("site", "session")

    pd.testing.assert_frame_equal(_configuration(tmp_path).subjects(), newer)
