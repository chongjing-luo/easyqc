from __future__ import annotations

import hashlib
from dataclasses import replace
from io import StringIO
from threading import Event

import pandas as pd
from pandas.testing import assert_frame_equal
import pytest

from core.table_export_service import (
    TableExportCancelled,
    TableExportError,
    TableExportService,
)
from core.table_view_service import TableViewService
from models.table_view_state import ColumnViewState, FilterCondition, SortRule


def _applied_view():
    source = pd.DataFrame(
        {
            "easyqcid": ["S1", "S2", "S3", "S4", "S5", "S6", "S7"],
            "group": ["B", "A", "A", "B", "A", "B", "A"],
            "score": [1, 2, 2, 3, 4, 3, 0],
            "note": ["one", "中文", "comma, value", None, "five", "six", "seven"],
            "hidden": list(range(7)),
        }
    )
    service = TableViewService(source)
    state = replace(
        service.default_state(page_size=2),
        columns=ColumnViewState(
            order=("easyqcid", "note", "score", "group", "hidden"),
            hidden=("hidden",),
            pinned=("easyqcid",),
        ),
        conditions=(FilterCondition("score", ">=", 1, "scored"),),
        sort_rules=(SortRule("group", True), SortRule("score", False)),
        revision=7,
    )
    result = service.apply_state(state)
    return service, result, result.state.columns.visible_columns


def _partial_files(destination) -> list:
    return list(destination.parent.glob(f".{destination.name}.*.partial"))


def test_export_writes_all_applied_rows_in_bounded_order_with_exact_receipt(
    tmp_path,
    monkeypatch,
) -> None:
    service, result, columns = _applied_view()
    exporter = TableExportService(service)
    destination = tmp_path / "完整结果.csv"
    calls = []
    progress = []
    real_get_window = service.get_window

    def tracked_window(current_result, offset, limit=None, columns=None):
        calls.append((offset, limit, columns))
        return real_get_window(current_result, offset, limit, columns)

    monkeypatch.setattr(service, "get_window", tracked_window)

    receipt = exporter.export_applied_csv(
        result,
        columns,
        destination,
        Event(),
        lambda completed, total: progress.append((completed, total)),
        chunk_size=2,
    )

    expected = real_get_window(
        result,
        0,
        result.matched_total,
        columns,
    ).dataframe
    expected_csv = StringIO()
    expected.to_csv(expected_csv, index=False, lineterminator="\n")
    actual = pd.read_csv(destination, encoding="utf-8")
    assert_frame_equal(actual, pd.read_csv(StringIO(expected_csv.getvalue())))
    assert actual["easyqcid"].tolist() == ["S5", "S2", "S3", "S4", "S6", "S1"]
    assert tuple(actual.columns) == columns
    assert calls == [(0, 2, columns), (2, 2, columns), (4, 2, columns)]
    assert progress == [(2, 6), (4, 6), (6, 6)]
    assert destination.read_text(encoding="utf-8").splitlines()[0] == (
        "easyqcid,note,score,group"
    )
    assert receipt.destination == destination
    assert receipt.rows == 6
    assert receipt.columns == columns
    assert receipt.state_revision == 7
    assert receipt.sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert _partial_files(destination) == []


def test_empty_export_writes_one_header_and_reports_zero_progress(tmp_path) -> None:
    service, result, columns = _applied_view()
    empty_state = result.state.with_conditions(
        (FilterCondition("score", ">", 999, "no-match"),)
    )
    empty_result = service.apply_state(empty_state)
    progress = []
    destination = tmp_path / "empty.csv"

    receipt = TableExportService(service).export_applied_csv(
        empty_result,
        columns,
        destination,
        Event(),
        lambda completed, total: progress.append((completed, total)),
        chunk_size=2,
    )

    assert destination.read_text(encoding="utf-8") == "easyqcid,note,score,group\n"
    assert progress == [(0, 0)]
    assert receipt.rows == 0
    assert receipt.sha256 == hashlib.sha256(destination.read_bytes()).hexdigest()


def test_cancellation_after_a_chunk_preserves_existing_destination(tmp_path) -> None:
    service, result, columns = _applied_view()
    destination = tmp_path / "existing.csv"
    original = b"trusted,content\n1,old\n"
    destination.write_bytes(original)
    cancel_event = Event()
    progress = []

    def cancel_after_first_chunk(completed: int, total: int) -> None:
        progress.append((completed, total))
        cancel_event.set()

    with pytest.raises(TableExportCancelled, match="cancel"):
        TableExportService(service).export_applied_csv(
            result,
            columns,
            destination,
            cancel_event,
            cancel_after_first_chunk,
            chunk_size=2,
        )

    assert progress == [(2, 6)]
    assert destination.read_bytes() == original
    assert _partial_files(destination) == []


@pytest.mark.parametrize("failure_point", ["write", "fsync", "replace"])
def test_io_failure_preserves_existing_destination_and_cleans_partial(
    tmp_path,
    monkeypatch,
    failure_point,
) -> None:
    service, result, columns = _applied_view()
    destination = tmp_path / "existing.csv"
    original = b"trusted,content\n1,old\n"
    destination.write_bytes(original)

    if failure_point == "write":
        monkeypatch.setattr(
            pd.DataFrame,
            "to_csv",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")),
        )
    elif failure_point == "fsync":
        monkeypatch.setattr(
            "core.table_export_service.os.fsync",
            lambda _fd: (_ for _ in ()).throw(OSError("fsync failed")),
        )
    else:
        monkeypatch.setattr(
            "core.table_export_service.os.replace",
            lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
        )

    with pytest.raises(TableExportError, match=failure_point):
        TableExportService(service).export_applied_csv(
            result,
            columns,
            destination,
            Event(),
            chunk_size=2,
        )

    assert destination.read_bytes() == original
    assert _partial_files(destination) == []


def test_materialization_failure_and_invalid_requests_fail_before_commit(
    tmp_path,
    monkeypatch,
) -> None:
    service, result, columns = _applied_view()
    destination = tmp_path / "existing.csv"
    original = b"trusted\n"
    destination.write_bytes(original)
    real_get_window = service.get_window
    calls = 0

    def fail_second_chunk(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("materialize failed")
        return real_get_window(*args, **kwargs)

    monkeypatch.setattr(service, "get_window", fail_second_chunk)
    with pytest.raises(TableExportError, match="materialize failed"):
        TableExportService(service).export_applied_csv(
            result,
            columns,
            destination,
            Event(),
            chunk_size=2,
        )
    assert destination.read_bytes() == original
    assert _partial_files(destination) == []

    exporter = TableExportService(service)
    with pytest.raises(TableExportError, match="chunk_size"):
        exporter.export_applied_csv(result, columns, destination, Event(), chunk_size=0)
    with pytest.raises(TableExportError, match="visible column"):
        exporter.export_applied_csv(result, (), destination, Event(), chunk_size=2)
    with pytest.raises(TableExportError, match="directory"):
        exporter.export_applied_csv(
            result,
            columns,
            tmp_path / "missing" / "output.csv",
            Event(),
            chunk_size=2,
        )
