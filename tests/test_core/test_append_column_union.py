"""RI-14: heterogeneous append through temporary projects, never real data."""
from copy import deepcopy
from numbers import Integral

import pandas as pd
import pytest

from core.configuration_service import ConfigurationError, ConfigurationService
from core.project_service import ProjectService
from core.table_service import TableService
from core.table_view_service import TableViewService
from tests.test_core.test_configuration_service import _service


@pytest.mark.parametrize("policy", ["deduplicate", "replace"])
def test_append_unions_columns_and_preserves_missing_fields_after_reopen(tmp_path, policy):
    service, projects = _service(tmp_path)
    current = pd.DataFrame({
        "easyqcid": ["ID1", "ID2"], "kept": ["01", "02"], "shared": ["A", "B"],
    })
    incoming = pd.DataFrame({
        "new": ["003", "004"], "easyqcid": ["ID2", "ID3"], "shared": ["C", "D"],
    })
    untouched = deepcopy(incoming)
    service.replace_subjects(current, notify=False)
    table = projects.current_project.table_dir / "easyqc_all.csv"
    before = table.read_bytes()
    rating = projects.current_project.path / "RatingFiles" / "synthetic.json"
    rating.parent.mkdir(exist_ok=True)
    rating.write_bytes(b'{"synthetic": true}')
    preview = service.preview_subject_import(incoming, mode="append", conflict_policy=policy)
    assert (preview.result_rows, preview.matching_identities, preview.new_identities) == (3, 1, 1)
    assert table.read_bytes() == before
    summary = service.import_subjects(incoming, mode="append", conflict_policy=policy, notify=False)
    assert preview == summary
    fresh_projects = ProjectService(tmp_path / "projects.json")
    fresh_projects.load("SAMPLE")
    result = ConfigurationService(fresh_projects, TableService()).subjects()
    assert list(result.columns) == ["easyqcid", "kept", "shared", "new"]
    assert result.easyqcid.tolist() == ["ID1", "ID2", "ID3"]
    assert result.kept.tolist()[:2] == ["01", "02"]
    assert pd.isna(result.loc[2, "kept"])
    assert result.shared.tolist() == (["A", "B", "D"] if policy == "deduplicate" else ["A", "C", "D"])
    assert pd.isna(result.loc[0, "new"])
    if policy == "deduplicate":
        assert pd.isna(result.loc[1, "new"])
    else:
        assert result.loc[1, "new"] == "003"
    assert result.loc[2, "new"] == "004"
    assert rating.read_bytes() == b'{"synthetic": true}'
    pd.testing.assert_frame_equal(incoming, untouched)


@pytest.mark.parametrize("blank", [None, "", pd.NA])
def test_append_replace_only_provided_columns_including_explicit_blanks(tmp_path, blank):
    service, _ = _service(tmp_path)
    service.replace_subjects(pd.DataFrame({"easyqcid": ["ID1"], "keep": ["02"], "clear": ["old"]}), notify=False)
    service.import_subjects(pd.DataFrame({"easyqcid": ["ID1"], "clear": [blank]}),
                            mode="append", conflict_policy="replace", notify=False)
    result = service.subjects()
    assert result.loc[0, "keep"] == "02"
    value = result.loc[0, "clear"]
    assert value == "" if isinstance(blank, str) else pd.isna(value)


@pytest.mark.parametrize("policy", ["deduplicate", "replace"])
def test_append_only_id_and_duplicate_only_new_column(tmp_path, policy):
    service, _ = _service(tmp_path)
    service.replace_subjects(pd.DataFrame({"easyqcid": ["ID1"], "keep": ["01"]}), notify=False)
    service.import_subjects(pd.DataFrame({"easyqcid": ["ID1", "ID2"]}),
                            mode="append", conflict_policy=policy, notify=False)
    result = service.subjects()
    assert result.easyqcid.tolist() == ["ID1", "ID2"]
    assert result.loc[0, "keep"] == "01" and pd.isna(result.loc[1, "keep"])
    service.import_subjects(pd.DataFrame({"easyqcid": ["ID1"], "extra": ["02"]}),
                            mode="append", conflict_policy=policy, notify=False)
    result = service.subjects()
    assert list(result.columns) == ["easyqcid", "keep", "extra"]
    if policy == "deduplicate":
        assert result.extra.isna().all()
    else:
        assert result.loc[0, "extra"] == "02"


def test_append_empty_schema_and_native_numeric_values_do_not_mutate_inputs(tmp_path):
    service, _ = _service(tmp_path)
    current = pd.DataFrame({"easyqcid": ["ID1"], "number": [2**53 + 1]})
    incoming = pd.DataFrame({"easyqcid": ["ID2"], "text": ["01"]})
    result = service._append_subject_import(current, incoming, conflict_policy="deduplicate")
    assert result.loc[0, "number"] == 2**53 + 1
    assert isinstance(result.loc[0, "number"], int)
    empty = current.iloc[:0]
    result = service._append_subject_import(empty, incoming, conflict_policy="replace")
    assert list(result.columns) == ["easyqcid", "number", "text"]
    assert result.easyqcid.tolist() == ["ID2"]


def test_append_column_union_failure_does_not_write(tmp_path, monkeypatch):
    service, projects = _service(tmp_path)
    service.replace_subjects(pd.DataFrame({"easyqcid": ["ID1"], "kept": ["01"]}), notify=False)
    table = projects.current_project.table_dir / "easyqc_all.csv"
    before = table.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("synthetic append write failure")
    monkeypatch.setattr(service.table_service, "save_table", fail)
    with pytest.raises(OSError, match="synthetic append"):
        service.import_subjects(pd.DataFrame({"easyqcid": ["ID2"], "extra": ["02"]}),
                                mode="append", conflict_policy="replace", notify=False)
    assert table.read_bytes() == before


def test_append_still_rejects_case_collisions_and_constant_columns(tmp_path):
    service, projects = _service(tmp_path)
    service.replace_subjects(pd.DataFrame({"easyqcid": ["ID1"], "kept": ["01"]}), notify=False)
    service.add_constant("root", "/synthetic")
    table = projects.current_project.table_dir / "easyqc_all.csv"
    before = table.read_bytes()
    for incoming in (pd.DataFrame({"easyqcid": ["id1"], "extra": ["02"]}),
                     pd.DataFrame({"easyqcid": ["ID2"], "root": ["bad"]})):
        with pytest.raises(ConfigurationError, match="冲突"):
            service.import_subjects(incoming, mode="append", conflict_policy="replace", notify=False)
    assert table.read_bytes() == before


def test_csv_row_import_also_accepts_different_columns_without_skipping_duplicates(tmp_path):
    service, _ = _service(tmp_path)
    service.replace_subjects(pd.DataFrame({"easyqcid": ["ID1"], "kept": ["01"]}), notify=False)
    source = tmp_path / "incoming.csv"
    pd.DataFrame({"easyqcid": ["ID2"], "extra": ["other"]}).to_csv(source, index=False)
    service.import_subject_csv(source, mode="rows", notify=False)
    assert service.subjects().easyqcid.tolist() == ["ID1", "ID2"]
    assert list(service.subjects().columns) == ["easyqcid", "kept", "extra"]
    with pytest.raises(ConfigurationError, match="重复"):
        service.import_subject_csv(source, mode="rows", notify=False)


@pytest.mark.parametrize("integer", [2**53 + 1, -(2**53 + 1), 2**63 + 1])
def test_missing_columns_do_not_round_large_integers_on_reload(tmp_path, integer):
    service, projects = _service(tmp_path)
    service.replace_subjects(pd.DataFrame({"easyqcid": ["ID1"], "number": [integer]}), notify=False)
    service.import_subjects(pd.DataFrame({"easyqcid": ["ID2"], "new_number": [integer + 2]}),
                            mode="append", conflict_policy="replace", notify=False)
    fresh = ConfigurationService(projects, TableService()).subjects()
    assert isinstance(fresh.loc[0, "number"], Integral)
    assert fresh.loc[0, "number"] == integer
    assert isinstance(fresh.loc[1, "new_number"], Integral)
    assert fresh.loc[1, "new_number"] == integer + 2
    assert pd.isna(fresh.loc[1, "number"]) and pd.isna(fresh.loc[0, "new_number"])
    assert pd.api.types.is_integer_dtype(fresh["number"])
    assert TableViewService(fresh).profiles[1].kind.value == "number"
    service.replace_subjects(fresh, notify=False)
    again = ConfigurationService(projects, TableService()).subjects()
    pd.testing.assert_frame_equal(again, fresh)
