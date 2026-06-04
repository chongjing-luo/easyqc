import pandas as pd

from core.table_service import TABLE_ALL, TABLE_QCTABLE, TableService
from models.project import Project


def test_table_service_loads_missing_table_as_none(tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")

    assert service.load_table(project, TABLE_ALL) is None


def test_table_service_saves_and_loads_csv(tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    df = pd.DataFrame({"ezqcid": ["SUB001"], "age": [29]})

    service.save_table(project, TABLE_ALL, df)
    result = service.load_table(project, TABLE_ALL)

    pd.testing.assert_frame_equal(result, df)


def test_table_service_delete_removes_csv(tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    df = pd.DataFrame({"ezqcid": ["SUB001"]})

    service.save_table(project, TABLE_QCTABLE, df)
    service.save_table(project, TABLE_QCTABLE, df, delete=True)

    assert service.load_table(project, TABLE_QCTABLE) is None


def test_table_service_atomic_write_keeps_original_when_replace_fails(monkeypatch, tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    original = pd.DataFrame({"ezqcid": ["SUB001"], "age": [29]})
    replacement = pd.DataFrame({"ezqcid": ["SUB002"], "age": [31]})
    service.save_table(project, TABLE_ALL, original)

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr("core.table_service.os.replace", fail_replace)

    try:
        service.save_table(project, TABLE_ALL, replacement)
    except OSError:
        pass

    result = service.load_table(project, TABLE_ALL)
    pd.testing.assert_frame_equal(result, original)


def test_table_service_load_all_tables(tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    service.save_table(project, TABLE_ALL, pd.DataFrame({"ezqcid": ["SUB001"]}))
    service.save_table(project, TABLE_QCTABLE, pd.DataFrame({"ezqcid": ["SUB001"], "score": ["Good"]}))

    tables = service.load_all_tables(project)

    assert set(tables) == {TABLE_ALL, TABLE_QCTABLE}


def test_table_service_loads_tables_for_legacy_state_shape(tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    service.save_table(project, TABLE_ALL, pd.DataFrame({"ezqcid": ["SUB001"]}))
    service.save_table(project, TABLE_QCTABLE, pd.DataFrame({"ezqcid": ["SUB001"], "score": ["Good"]}))
    service.save_table(project, "ezqc_AnatRestAll", pd.DataFrame({"ezqcid": ["SUB001"], "x": [1]}))

    tables = service.load_legacy_state_tables(project, module_names=["AnatRestAll", "MissingModule"])

    assert tables.variables[TABLE_ALL]["ezqcid"].tolist() == ["SUB001"]
    assert tables.results[TABLE_QCTABLE]["score"].tolist() == ["Good"]
    assert tables.results["AnatRestAll"]["x"].tolist() == [1]
    assert tables.results["MissingModule"] is None


def test_table_service_normalizes_legacy_module_table_names() -> None:
    assert TableService.module_name_from_table_type("ezqc_AnatRestAll") == "AnatRestAll"
    assert TableService.module_name_from_table_type("AnatRestAll") == "AnatRestAll"
