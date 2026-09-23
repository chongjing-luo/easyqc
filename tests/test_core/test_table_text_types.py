"""Text values must survive the real CSV persistence boundary (D012)."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from fractions import Fraction

import pandas as pd
import pytest

from core.configuration_service import ConfigurationError, ConfigurationService
from core.formula_engine import FormulaEngine
from core.project_service import ProjectService
from core.table_service import (
    TABLE_ALL, TableService, TableServiceError, TableStateConflictError,
)
from core.table_transform import TableTransformEngine
from models.derived_formula import DerivedColumnFormula
from models.project import Project


def _configuration(tmp_path):
    projects = ProjectService(tmp_path / "projects.json")
    service = ConfigurationService(projects, TableService())
    service.create_project("TEXT", tmp_path)
    return service, projects


def _project(tmp_path):
    return Project("TEXT", tmp_path / "easyqc_TEXT")


def _types_path(service, project):
    return Path(str(service.table_path(project, TABLE_ALL)) + ".types.json")


@pytest.mark.parametrize("expression", [
    "LEFT([source], 2)", "RIGHT([source], 2)", "MID([source], 1, 2)",
    'TEXTBEFORE([source], "_")', 'TEXTAFTER([source], "scan")',
    'IF(TRUE, LEFT([source], 2), "unused")',
])
def test_formula_text_survives_commit_reopen_and_another_save(tmp_path, expression):
    config, projects = _configuration(tmp_path)
    frame = pd.DataFrame({
        "easyqcid": ["A", "B"], "source": ["02_scan02", "01_scan01"],
        "score": [2, 1],
    })
    config.replace_subjects(frame, notify=False)
    assert FormulaEngine().evaluate(frame, expression).values.tolist() == ["02", "01"]

    config.derive_subject_column(DerivedColumnFormula("code", expression), notify=False)
    reopened = ConfigurationService(projects, TableService())
    assert reopened.subjects()["code"].tolist() == ["02", "01"]
    reopened.derive_subject_column(
        DerivedColumnFormula("number", "VALUE([code])"), notify=False,
    )
    result = reopened.subjects()
    assert result["code"].tolist() == ["02", "01"]
    assert result["number"].tolist() == [2, 1]
    assert pd.api.types.is_numeric_dtype(result["number"])
    assert result["score"].tolist() == [2, 1]
    with pytest.raises(ConfigurationError, match="需要数值"):
        reopened.derive_subject_column(DerivedColumnFormula("implicit", "[code] + 1"))


@pytest.mark.parametrize("dtype", [object, "string"])
def test_text_cells_preserve_numeric_na_boolean_literals_and_blank(tmp_path, dtype):
    service, project = TableService(), _project(tmp_path)
    values = ["02", "01", "12", "True", "NA", "", "nan", "null", "1e3", pd.NA]
    frame = pd.DataFrame({
        "easyqcid": [f"R{i}" for i in range(len(values))],
        "text": pd.Series(values, dtype=dtype),
        "number": range(len(values)),
    })
    frame.index = range(10, 10 + len(values))

    service.save_table(project, TABLE_ALL, frame)
    result = TableService().load_table(project, TABLE_ALL)

    assert result["text"].iloc[:-1].tolist() == values[:-1]
    assert pd.isna(result["text"].iloc[-1])
    assert result["number"].tolist() == list(range(len(values)))
    assert str(result["text"].dtype) == str(frame["text"].dtype)


def test_mixed_text_numeric_boolean_column_preserves_cell_semantics(tmp_path):
    service, project = TableService(), _project(tmp_path)
    values = ["02", 2, "True", True, "", None, 2.5, "2.5", False]
    frame = pd.DataFrame({"easyqcid": [f"R{i}" for i in range(9)], "mixed": values})

    service.save_table(project, TABLE_ALL, frame)
    actual = service.load_table(project, TABLE_ALL)["mixed"].tolist()

    for expected, value in zip(values, actual):
        if expected is None:
            assert pd.isna(value)
        else:
            assert value == expected
            if isinstance(expected, str):
                assert isinstance(value, str)
            elif isinstance(expected, bool):
                assert isinstance(value, bool)
            else:
                assert not isinstance(value, (str, bool))


def test_mixed_column_does_not_round_large_integers_through_float(tmp_path):
    service, project = TableService(), _project(tmp_path)
    values = ["02", 9007199254740993, 2.5, -9007199254740993]
    service.save_table(project, TABLE_ALL, pd.DataFrame({
        "easyqcid": ["A", "B", "C", "D"], "mixed": values,
    }))
    actual = service.load_table(project, TABLE_ALL)["mixed"].tolist()
    assert actual == values


def test_import_draft_derived_code_retains_text_after_merge(tmp_path):
    config, projects = _configuration(tmp_path)
    config.replace_subjects(pd.DataFrame({"easyqcid": ["OLD"], "source": ["row00"], "code": ["00"]}))
    draft = pd.DataFrame({"source": ["row02", "row01"]})
    transform = TableTransformEngine()
    draft = transform.derive_column_from_formula(draft, DerivedColumnFormula("easyqcid", "[source]"))
    draft = transform.derive_column_from_formula(draft, DerivedColumnFormula("code", "RIGHT([source], 2)"))

    config.import_subjects(draft, mode="append", conflict_policy="deduplicate", notify=False)

    assert config.subjects()["code"].tolist() == ["00", "02", "01"]


def test_csv_import_uses_matching_type_sidecar(tmp_path):
    source_service, source_project = TableService(), _project(tmp_path / "source")
    source_service.save_table(source_project, TABLE_ALL, pd.DataFrame({
        "easyqcid": ["A", "B"], "code": ["02", "01"], "score": [2, 1],
    }))
    path = source_service.table_path(source_project, TABLE_ALL)
    config, _ = _configuration(tmp_path / "destination")

    assert config.draft_from_file(path)["code"].tolist() == ["02", "01"]
    config.import_subject_csv(path, notify=False)
    assert config.subjects()["code"].tolist() == ["02", "01"]
    assert config.subjects()["score"].tolist() == [2, 1]


@pytest.mark.parametrize("failure_target", ["csv", "types"])
def test_failed_commit_keeps_old_values_and_types(tmp_path, monkeypatch, failure_target):
    service, project = TableService(), _project(tmp_path)
    before_frame = pd.DataFrame({"easyqcid": ["A"], "code": ["02"]})
    service.save_table(project, TABLE_ALL, before_frame)
    snapshot = service.load_table_snapshot(project, TABLE_ALL)
    csv_path = service.table_path(project, TABLE_ALL)
    before_bytes = csv_path.read_bytes()
    replace = __import__("os").replace

    def fail_selected(source, destination):
        target = csv_path if failure_target == "csv" else _types_path(service, project)
        if Path(destination) == target:
            raise OSError("injected commit failure")
        return replace(source, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr("core.table_service.os.replace", fail_selected)
        with pytest.raises(OSError, match="injected"):
            service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["B"], "code": [3]}))

    assert csv_path.read_bytes() == before_bytes
    current = TableService().load_table_snapshot(project, TABLE_ALL)
    pd.testing.assert_frame_equal(current.dataframe, before_frame)
    assert current.revision == snapshot.revision
    assert list(project.table_dir.glob("*.tmp.*")) == []
    service.save_table(project, TABLE_ALL, before_frame, expected_revision=snapshot.revision)


def test_identical_csv_bytes_type_change_has_a_new_revision(tmp_path):
    service, project = TableService(), _project(tmp_path)
    frame = pd.DataFrame({"easyqcid": ["A"], "code": ["2"]})
    service.save_table(project, TABLE_ALL, frame)
    before = service.load_table_snapshot(project, TABLE_ALL)
    payload = service.table_path(project, TABLE_ALL).read_bytes()

    changed = frame.assign(code=[2])
    service.save_table(project, TABLE_ALL, changed, expected_revision=before.revision)
    after = service.load_table_snapshot(project, TABLE_ALL)

    assert service.table_path(project, TABLE_ALL).read_bytes() == payload
    assert after.revision != before.revision
    assert before.dataframe["code"].tolist() == ["2"]
    assert after.dataframe["code"].tolist() == [2]
    with pytest.raises(TableStateConflictError):
        service.save_table(project, TABLE_ALL, frame, expected_revision=before.revision)


def test_existing_mismatched_or_corrupt_types_fail_loudly(tmp_path):
    service, project = TableService(), _project(tmp_path)
    service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["A"], "code": ["02"]}))
    csv_path = service.table_path(project, TABLE_ALL)
    csv_path.write_text("easyqcid,code\nA,03\n", encoding="utf-8")

    with pytest.raises(TableServiceError, match="类型|type"):
        service.load_table(project, TABLE_ALL)
    _types_path(service, project).write_text("not json", encoding="utf-8")
    with pytest.raises(TableServiceError, match="类型|type"):
        service.load_table(project, TABLE_ALL)


def test_type_records_are_bounded_and_removed_with_table(tmp_path):
    service, project = TableService(), _project(tmp_path)
    for number in range(5):
        service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["A"], "text": [str(number)]}))
    types_path = _types_path(service, project)
    metadata = json.loads(types_path.read_text(encoding="utf-8"))
    assert len(metadata["versions"]) <= 2

    service.save_table(project, TABLE_ALL, None, delete=True)
    assert not types_path.exists()
    assert service.load_table(project, TABLE_ALL) is None


def test_empty_text_column_keeps_type_and_old_csv_remains_readable(tmp_path):
    service, project = TableService(), _project(tmp_path)
    frame = pd.DataFrame({"easyqcid": pd.Series(dtype=object), "code": pd.Series(dtype="string")})
    service.save_table(project, TABLE_ALL, frame)
    result = service.load_table(project, TABLE_ALL)
    assert str(result["code"].dtype) == "string"
    legacy = Project("OLD", tmp_path / "old")
    legacy.table_dir.mkdir(parents=True)
    service.table_path(legacy, TABLE_ALL).write_text("easyqcid,age\n001,2\n", encoding="utf-8")
    old = service.load_table(legacy, TABLE_ALL)
    assert old["easyqcid"].tolist() == ["001"]
    assert old["age"].tolist() == [2]


def test_blank_and_null_can_change_without_changing_csv_bytes(tmp_path):
    service, project = TableService(), _project(tmp_path)
    service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["A"], "text": [""]}))
    before = service.load_table_snapshot(project, TABLE_ALL)
    payload = service.table_path(project, TABLE_ALL).read_bytes()
    service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["A"], "text": [None]}))
    after = service.load_table_snapshot(project, TABLE_ALL)
    assert before.dataframe["text"].tolist() == [""]
    assert pd.isna(after.dataframe["text"].iloc[0])
    assert payload == service.table_path(project, TABLE_ALL).read_bytes()
    assert after.revision != before.revision


def test_first_type_publication_then_csv_failure_keeps_legacy_readable(tmp_path, monkeypatch):
    service, project = TableService(), _project(tmp_path)
    path = service.table_path(project, TABLE_ALL)
    path.parent.mkdir(parents=True)
    path.write_text("easyqcid,age\n001,2\n", encoding="utf-8")
    before = service.load_table_snapshot(project, TABLE_ALL)
    replace = __import__("os").replace

    def fail_csv(source, destination):
        if Path(destination) == path:
            raise OSError("CSV commit failed")
        return replace(source, destination)

    with monkeypatch.context() as scoped:
        scoped.setattr("core.table_service.os.replace", fail_csv)
        with pytest.raises(OSError, match="CSV commit failed"):
            service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["B"], "code": ["02"]}))
    after = TableService().load_table_snapshot(project, TABLE_ALL)
    pd.testing.assert_frame_equal(after.dataframe, before.dataframe)
    assert after.revision == before.revision


@pytest.mark.parametrize("damage", ["version", "shape", "bitmap", "nonempty_null", "fields"])
def test_invalid_type_schema_cannot_fall_back_to_inference(tmp_path, damage):
    service, project = TableService(), _project(tmp_path)
    service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["A"], "text": ["02"]}))
    path = _types_path(service, project)
    catalog = json.loads(path.read_text(encoding="utf-8"))
    schema = next(iter(catalog["versions"].values()))
    if damage == "version":
        catalog["schema_version"] = 999
    elif damage == "shape":
        schema["row_count"] = 99
    elif damage == "bitmap":
        schema["preserved"]["text"]["nulls"] = "not-base64!"
    elif damage == "nonempty_null":
        schema["preserved"]["text"]["nulls"] = "gA=="  # Marks the nonempty "02" cell as missing.
    else:
        schema["preserved"]["text"]["surprise"] = "02"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(TableServiceError, match="类型"):
        service.load_table(project, TABLE_ALL)


@pytest.mark.parametrize("boundary", ["types", "csv"])
def test_interruption_at_each_publication_boundary_has_consistent_types(
    tmp_path, monkeypatch, boundary,
):
    service, project = TableService(), _project(tmp_path)
    old = pd.DataFrame({"easyqcid": ["A"], "code": ["02"]})
    new = pd.DataFrame({"easyqcid": ["B"], "code": [3]})
    service.save_table(project, TABLE_ALL, old)
    path = service.table_path(project, TABLE_ALL)
    target = _types_path(service, project) if boundary == "types" else path
    replace = __import__("os").replace

    def interrupt_after_replace(source, destination):
        replace(source, destination)
        if Path(destination) == target:
            raise SystemExit("simulated interruption after atomic replacement")

    with monkeypatch.context() as scoped:
        scoped.setattr("core.table_service.os.replace", interrupt_after_replace)
        with pytest.raises(SystemExit):
            service.save_table(project, TABLE_ALL, new)

    actual = TableService().load_table(project, TABLE_ALL)
    pd.testing.assert_frame_equal(actual, old if boundary == "types" else new)


def test_dense_missing_masks_stay_small_at_100000_rows(tmp_path):
    service, project = TableService(), _project(tmp_path)
    frame = pd.DataFrame({
        "easyqcid": [f"R{i}" for i in range(100_000)],
        "code": pd.Series(["02", pd.NA] * 50_000, dtype="string"),
    })
    service.save_table(project, TABLE_ALL, frame)
    # Bit packing avoids a large JSON list of 50,000 missing row positions.
    assert _types_path(service, project).stat().st_size < 20_000
    actual = service.load_table(project, TABLE_ALL)
    pd.testing.assert_frame_equal(actual, frame)


def test_reader_holds_lock_until_csv_and_types_are_both_read(tmp_path, monkeypatch):
    service, project = TableService(), _project(tmp_path)
    service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["A"], "code": ["02"]}))
    reader_waiting, release_reader, writer_started = (threading.Event() for _ in range(3))
    local = threading.local()
    read_types = TableService._read_types

    def paused_read_types(cls, path, digest):
        if getattr(local, "reader", False):
            reader_waiting.set()
            assert release_reader.wait(5), "test reader was not released"
        return read_types(path, digest)

    def read():
        local.reader = True
        return service.load_table(project, TABLE_ALL)

    def write():
        writer_started.set()
        for code in ("03", "04"):
            service.save_table(project, TABLE_ALL, pd.DataFrame({"easyqcid": ["A"], "code": [code]}))

    monkeypatch.setattr(TableService, "_read_types", classmethod(paused_read_types))
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(read)
        assert reader_waiting.wait(5)
        writer = pool.submit(write)
        try:
            assert writer_started.wait(5)
            # A writer cannot prune the reader's selected type generation.
            assert not writer.done()
        finally:
            release_reader.set()
        assert reader.result(timeout=5)["code"].tolist() == ["02"]
        writer.result(timeout=5)
    assert service.load_table(project, TABLE_ALL)["code"].tolist() == ["04"]


def test_categorical_text_preserves_characters_as_ordinary_text(tmp_path):
    service, project = TableService(), _project(tmp_path)
    frame = pd.DataFrame({
        "easyqcid": ["A", "B", "C"],
        "code": pd.Series(["02", "01", None], dtype="category"),
    })
    service.save_table(project, TABLE_ALL, frame)
    actual = service.load_table(project, TABLE_ALL)
    assert actual["code"].iloc[:2].tolist() == ["02", "01"]
    assert pd.isna(actual["code"].iloc[2])


def test_unsupported_mixed_number_fails_before_publishing(tmp_path):
    service, project = TableService(), _project(tmp_path)
    old = pd.DataFrame({"easyqcid": ["A"], "code": ["02"]})
    service.save_table(project, TABLE_ALL, old)
    path, types_path = service.table_path(project, TABLE_ALL), _types_path(service, project)
    before = (path.read_bytes(), types_path.read_bytes())
    incoming = pd.DataFrame({"easyqcid": ["A", "B"], "code": ["02", Fraction(1, 3)]})
    with pytest.raises(ValueError, match="unsupported"):
        service.save_table(project, TABLE_ALL, incoming)
    assert (path.read_bytes(), types_path.read_bytes()) == before
    pd.testing.assert_frame_equal(service.load_table(project, TABLE_ALL), old)
