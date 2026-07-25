from __future__ import annotations

import pandas as pd
import pytest

from core.configuration_service import ConfigurationError, ConfigurationService
from core.event_bus import EventType
from core.formula_engine import (
    FormulaEngine,
    FormulaEvaluationError,
)
from core.project_service import ProjectService
from core.table_service import TableService
from core.table_transform import TableTransformEngine, TableTransformError
from models.derived_formula import DerivedColumnFormula


def _service(tmp_path) -> tuple[ConfigurationService, ProjectService]:
    projects = ProjectService(tmp_path / "projects.json")
    service = ConfigurationService(projects, TableService())
    service.create_project("FORMULA", tmp_path)
    service.replace_subjects(
        pd.DataFrame(
            {
                "ezqcid": ["ROW001", "ROW002"],
                "age": [29, 31],
                "site": ["A", "B"],
            }
        ),
        notify=False,
    )
    return service, projects


def test_formula_evaluation_error_summary_is_bounded_and_index_aware() -> None:
    frame = pd.DataFrame(
        {"raw": ["bad", "worse", "invalid", "still bad"]},
        index=[101, 102, 103, 104],
    )
    failed = FormulaEngine().evaluate(frame, "VALUE([raw])")
    clean = FormulaEngine().evaluate(frame, '"ok"')

    clean.raise_for_errors()
    with pytest.raises(FormulaEvaluationError) as caught:
        failed.raise_for_errors(max_examples=3)

    message = str(caught.value)
    assert "4 行" in message
    assert "101" in message
    assert "102" in message
    assert "103" in message
    assert "104" not in message
    assert "VALUE 无法转换为数值" in message


def test_formula_transform_can_create_missing_ezqcid_but_never_overwrite_it() -> None:
    source = pd.DataFrame(
        {"raw id": [" ROW001 ", "ROW002"]},
        index=[7, 9],
    )
    original = source.copy(deep=True)
    request = DerivedColumnFormula("ezqcid", "TRIM([raw id])")
    engine = TableTransformEngine()

    result = engine.derive_column_from_formula(source, request)

    assert result["ezqcid"].tolist() == ["ROW001", "ROW002"]
    assert result.index.equals(source.index)
    pd.testing.assert_frame_equal(source, original)
    with pytest.raises(TableTransformError, match="已存在"):
        engine.derive_column_from_formula(result, request)


def test_formula_transform_rejects_unresolved_rows_without_mutating_source() -> None:
    source = pd.DataFrame(
        {"raw": ["4", "bad", "also bad"]},
        index=[3, 5, 8],
    )
    original = source.copy(deep=True)

    with pytest.raises(TableTransformError) as caught:
        TableTransformEngine().derive_column_from_formula(
            source,
            DerivedColumnFormula("number", "VALUE([raw])"),
        )

    assert "2 行" in str(caught.value)
    assert "5" in str(caught.value)
    pd.testing.assert_frame_equal(source, original)


def test_formula_service_persists_one_ordinary_column_once_and_then_notifies(
    tmp_path,
    monkeypatch,
) -> None:
    service, projects = _service(tmp_path)
    save_calls: list[pd.DataFrame] = []
    original_save = service.table_service.save_table

    def tracked_save(project, table_type, frame, *args, **kwargs):
        save_calls.append(frame.copy(deep=True))
        return original_save(project, table_type, frame, *args, **kwargs)

    monkeypatch.setattr(service.table_service, "save_table", tracked_save)
    events = []
    service.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        events.append,
    )
    expression = "ROUND(([age] + 1) / 2, 1)"

    result = service.derive_subject_column(
        DerivedColumnFormula("age next", expression)
    )

    assert result == "age next"
    assert len(save_calls) == 1
    assert save_calls[0]["age next"].tolist() == [15.0, 16.0]
    assert service.subjects()["age next"].tolist() == [15.0, 16.0]
    assert len(events) == 1
    csv_path = projects.current_project.table_dir / "ezqc_all.csv"
    csv_text = csv_path.read_text(encoding="utf-8")
    settings_text = projects.current_project.settings_path.read_text(
        encoding="utf-8"
    )
    assert "age next" in csv_text.splitlines()[0]
    assert expression not in csv_text
    assert expression not in settings_text


@pytest.mark.parametrize(
    "formula_request",
    (
        DerivedColumnFormula("age", "[age] + 1"),
        DerivedColumnFormula("broken", "[age] / 0"),
    ),
)
def test_formula_service_failure_preserves_csv_and_publishes_nothing(
    tmp_path,
    formula_request,
) -> None:
    service, projects = _service(tmp_path)
    csv_path = projects.current_project.table_dir / "ezqc_all.csv"
    before = csv_path.read_bytes()
    before_frame = service.subjects()
    events = []
    service.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        events.append,
    )

    with pytest.raises(ConfigurationError):
        service.derive_subject_column(formula_request)

    assert csv_path.read_bytes() == before
    assert events == []
    pd.testing.assert_frame_equal(service.subjects(), before_frame)


def test_formula_service_rejects_constant_collision_before_writing(
    tmp_path,
) -> None:
    service, projects = _service(tmp_path)
    service.set_constant("site_label", "A")
    csv_path = projects.current_project.table_dir / "ezqc_all.csv"
    before = csv_path.read_bytes()

    with pytest.raises(ConfigurationError, match="常量冲突"):
        service.derive_subject_column(
            DerivedColumnFormula("site_label", "[site]")
        )

    assert csv_path.read_bytes() == before


def test_formula_service_atomic_save_failure_publishes_nothing(
    tmp_path,
    monkeypatch,
) -> None:
    service, projects = _service(tmp_path)
    csv_path = projects.current_project.table_dir / "ezqc_all.csv"
    before = csv_path.read_bytes()
    events = []
    service.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        events.append,
    )

    def fail_save(*_args, **_kwargs):
        raise OSError("atomic formula save failed")

    monkeypatch.setattr(service.table_service, "save_table", fail_save)

    with pytest.raises(OSError, match="atomic formula save failed"):
        service.derive_subject_column(
            DerivedColumnFormula("age_next", "[age] + 1")
        )

    assert csv_path.read_bytes() == before
    assert events == []
