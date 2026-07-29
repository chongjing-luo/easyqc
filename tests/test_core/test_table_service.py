from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

import pandas as pd
import pytest

from core.table_service import (
    TABLE_ALL,
    TABLE_QCTABLE,
    TableService,
    TableStateConflictError,
)
from models.project import Project


def test_table_service_loads_missing_table_as_none(tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")

    assert service.load_table(project, TABLE_ALL) is None


def test_table_service_saves_and_loads_csv(tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    df = pd.DataFrame({"easyqcid": ["SUB001"], "age": [29]})

    service.save_table(project, TABLE_ALL, df)
    result = service.load_table(project, TABLE_ALL)

    pd.testing.assert_frame_equal(result, df)


def test_table_service_preserves_text_easyqcid_while_inferring_other_columns(
    tmp_path,
) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    path = service.table_path(project, TABLE_ALL)
    path.parent.mkdir(parents=True)
    path.write_text(
        "easyqcid,visit\n001,1\n01-A,2\n",
        encoding="utf-8",
    )

    result = service.load_table(project, TABLE_ALL)

    assert result["easyqcid"].tolist() == ["001", "01-A"]
    assert result["visit"].tolist() == [1, 2]


def test_table_service_delete_removes_csv(tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    df = pd.DataFrame({"easyqcid": ["SUB001"]})

    service.save_table(project, TABLE_QCTABLE, df)
    service.save_table(project, TABLE_QCTABLE, df, delete=True)

    assert service.load_table(project, TABLE_QCTABLE) is None


def test_table_service_atomic_write_keeps_original_when_replace_fails(monkeypatch, tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    original = pd.DataFrame({"easyqcid": ["SUB001"], "age": [29]})
    replacement = pd.DataFrame({"easyqcid": ["SUB002"], "age": [31]})
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


def test_table_service_uses_a_unique_temporary_path_for_every_write(
    monkeypatch,
    tmp_path,
) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    observed: list[Path] = []
    real_to_csv = pd.DataFrame.to_csv

    def capture_path(frame, path, *args, **kwargs):
        observed.append(Path(path))
        return real_to_csv(frame, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", capture_path)

    service.save_table(
        project,
        TABLE_ALL,
        pd.DataFrame({"easyqcid": ["SUB001"]}),
    )
    service.save_table(
        project,
        TABLE_ALL,
        pd.DataFrame({"easyqcid": ["SUB002"]}),
    )

    assert len(observed) == 2
    assert observed[0].name != observed[1].name
    assert all(path.parent == project.table_dir for path in observed)
    assert list(project.table_dir.glob("*.tmp.*")) == []


def test_table_service_serializes_same_process_concurrent_writers(
    monkeypatch,
    tmp_path,
) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    start = threading.Barrier(3)
    counter_lock = threading.Lock()
    active_replaces = 0
    max_active_replaces = 0
    real_replace = __import__("os").replace

    def tracked_replace(source, destination):
        nonlocal active_replaces, max_active_replaces
        with counter_lock:
            active_replaces += 1
            max_active_replaces = max(max_active_replaces, active_replaces)
        try:
            time.sleep(0.03)
            return real_replace(source, destination)
        finally:
            with counter_lock:
                active_replaces -= 1

    monkeypatch.setattr("core.table_service.os.replace", tracked_replace)

    def write(identity: str) -> None:
        start.wait()
        service.save_table(
            project,
            TABLE_ALL,
            pd.DataFrame(
                {
                    "easyqcid": [identity] * 20,
                    "value": [identity] * 20,
                }
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, identity) for identity in ("A", "B")]
        start.wait()
        for future in futures:
            future.result()

    assert max_active_replaces == 1
    result = service.load_table(project, TABLE_ALL)
    assert result is not None
    assert result["easyqcid"].tolist() in [["A"] * 20, ["B"] * 20]
    assert result["value"].tolist() == result["easyqcid"].tolist()


def test_table_service_rejects_stale_revision_without_overwriting_newer_table(
    tmp_path,
) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    service.save_table(
        project,
        TABLE_ALL,
        pd.DataFrame({"easyqcid": ["SUB001"], "value": ["old"]}),
    )
    stale = service.load_table_snapshot(project, TABLE_ALL)
    service.save_table(
        project,
        TABLE_ALL,
        pd.DataFrame({"easyqcid": ["SUB001"], "value": ["new"]}),
    )

    with pytest.raises(TableStateConflictError, match="stale"):
        service.save_table(
            project,
            TABLE_ALL,
            pd.DataFrame({"easyqcid": ["SUB001"], "value": ["stale"]}),
            expected_revision=stale.revision,
        )

    current = service.load_table(project, TABLE_ALL)
    assert current is not None
    assert current["value"].tolist() == ["new"]


def test_unconditional_table_replacement_does_not_parse_damaged_old_csv(
    tmp_path,
) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    path = service.table_path(project, TABLE_ALL)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")
    replacement = pd.DataFrame({"easyqcid": ["SUB001"], "value": ["recovered"]})

    service.save_table(project, TABLE_ALL, replacement)

    current = service.load_table(project, TABLE_ALL)
    pd.testing.assert_frame_equal(current, replacement)


def test_table_service_load_all_tables(tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["SUB001"]}))
    service.save_table(project, TABLE_QCTABLE, pd.DataFrame({"easyqcid": ["SUB001"], "score": ["Good"]}))

    tables = service.load_all_tables(project)

    assert set(tables) == {TABLE_ALL, TABLE_QCTABLE}


def test_table_service_loads_tables_for_legacy_state_shape(tmp_path) -> None:
    service = TableService()
    project = Project("SAMPLE", tmp_path / "easyqc_SAMPLE")
    service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["SUB001"]}))
    service.save_table(project, TABLE_QCTABLE, pd.DataFrame({"easyqcid": ["SUB001"], "score": ["Good"]}))
    service.save_table(project, "easyqc_AnatRestAll", pd.DataFrame({"easyqcid": ["SUB001"], "x": [1]}))

    tables = service.load_state_tables(project, module_names=["AnatRestAll", "MissingModule"])

    assert tables.variables[TABLE_ALL]["easyqcid"].tolist() == ["SUB001"]
    assert tables.results[TABLE_QCTABLE]["score"].tolist() == ["Good"]
    assert tables.results["AnatRestAll"]["x"].tolist() == [1]
    assert tables.results["MissingModule"] is None


def test_table_service_normalizes_legacy_module_table_names() -> None:
    assert TableService.module_name_from_table_type("easyqc_AnatRestAll") == "AnatRestAll"
    assert TableService.module_name_from_table_type("AnatRestAll") == "AnatRestAll"
