"""Reproducible synthetic benchmark for the EasyQC table query path."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shlex
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

with redirect_stdout(sys.stderr if __name__ == "__main__" else sys.stdout):
    from core.table_view_service import TableViewService
    from gui_qt.task_runner import RevisionedTaskController
    from models.table_view_state import FilterCondition, SortRule, TableViewResult


DATASET_SCHEMA_VERSION = 2
METADATA_COLUMNS = (
    "ezqcid",
    "group",
    "marker",
    "site",
    "cohort",
    "short_text",
    "chinese_text",
    "long_label",
    "flag",
    "acquired_at",
    "integer_score",
)
CANONICAL_MATCHED_ROWS = 6667
CANONICAL_RESULT_SHA256 = (
    "12661802f76731af04ba8d187f01a43bb7c1423cc6be7699327de184774b5c19"
)
_T = TypeVar("_T")


@dataclass(frozen=True)
class BenchmarkConfig:
    rows: int = 100_000
    columns: int = 300
    iterations: int = 11
    warmups: int = 2
    seed: int = 20_260_718
    page_size: int = 200
    budget_p95_ms: float = 500.0
    budget_peak_rss_mib: float = 4096.0
    budget_qt_delay_ms: float = 100.0
    heartbeat_interval_ms: int = 10

    def __post_init__(self) -> None:
        if self.rows <= 0:
            raise ValueError("rows must be greater than zero")
        if self.columns < len(METADATA_COLUMNS) + 2:
            raise ValueError("columns must include mixed metadata and two sort columns")
        if self.iterations < 5:
            raise ValueError("iterations must be at least five")
        if self.warmups < 0:
            raise ValueError("warmups cannot be negative")
        if self.page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        if self.budget_p95_ms <= 0:
            raise ValueError("budget_p95_ms must be greater than zero")
        if self.budget_peak_rss_mib <= 0:
            raise ValueError("budget_peak_rss_mib must be greater than zero")
        if self.budget_qt_delay_ms <= 0:
            raise ValueError("budget_qt_delay_ms must be greater than zero")
        if self.heartbeat_interval_ms <= 0:
            raise ValueError("heartbeat_interval_ms must be greater than zero")


def build_synthetic_table(config: BenchmarkConfig) -> pd.DataFrame:
    """Build the versioned mixed-schema frame without reading runtime data."""

    positions = np.arange(config.rows, dtype=np.int64)
    numeric_count = config.columns - len(METADATA_COLUMNS)
    rng = np.random.default_rng(config.seed)
    numeric_values = rng.standard_normal((config.rows, numeric_count))
    numeric_values[:, 0] = (positions * 37) % 1009
    numeric_values[:, 1] = (positions * 17) % 101
    if numeric_count > 2:
        numeric_values[:, 2::4] = np.abs(numeric_values[:, 2::4])
    if numeric_count > 3:
        numeric_values[:, 3::4] = np.round(
            numeric_values[:, 3::4] * 100,
            decimals=2,
        )
    for column_index in range(2, numeric_count):
        numeric_values[
            (positions + column_index * 13) % 97 == 0,
            column_index,
        ] = np.nan
    numeric = pd.DataFrame(
        numeric_values,
        columns=[f"metric_{index:03d}" for index in range(numeric_count)],
        copy=False,
    )

    marker_values = np.asarray([f"M{index:02d}" for index in range(10)])
    site_values = np.asarray([f"SITE_{index:03d}" for index in range(128)])
    cohort_values = np.take(
        np.asarray(["baseline", "followup", "pilot", "external"], dtype=object),
        positions % 4,
    ).copy()
    cohort_values[positions % 29 == 0] = None
    review_values = np.asarray(
        [f"review-needed-{index:02d}" for index in range(64)],
        dtype=object,
    )
    normal_values = np.asarray(
        [f"review-complete-{index:02d}" for index in range(64)],
        dtype=object,
    )
    short_text = np.where(
        positions % 3 == 0,
        np.take(review_values, positions % len(review_values)),
        np.take(normal_values, positions % len(normal_values)),
    )
    chinese_values = np.asarray(
        [f"质控标签_{index:02d}_需人工复核" for index in range(32)],
        dtype=object,
    )
    long_values = np.asarray(
        [
            f"Cross-platform QC label {index:02d} · 中文路径 · " + "模块" * 20
            for index in range(32)
        ],
        dtype=object,
    )
    flags = pd.array(positions % 2 == 0, dtype="boolean")
    flags[positions % 31 == 0] = pd.NA
    acquired_at = pd.Series(
        pd.Timestamp("2020-01-01")
        + pd.to_timedelta(positions % 365, unit="D"),
        dtype="datetime64[ns]",
    )
    acquired_at.iloc[positions % 43 == 0] = pd.NaT
    integer_score = pd.array((positions * 11) % 101, dtype="Int32")
    integer_score[positions % 37 == 0] = pd.NA

    metadata = pd.DataFrame(
        {
            "ezqcid": [f"SUB{index:07d}" for index in range(config.rows)],
            "group": pd.Categorical(
                np.where(positions % 2 == 0, "control", "case")
            ),
            "marker": pd.Categorical(np.take(marker_values, positions % 10)),
            "site": pd.Categorical(
                np.take(site_values, (positions * 7) % len(site_values))
            ),
            "cohort": pd.Categorical(cohort_values),
            "short_text": short_text,
            "chinese_text": np.take(chinese_values, positions % len(chinese_values)),
            "long_label": np.take(long_values, positions % len(long_values)),
            "flag": flags,
            "acquired_at": acquired_at,
            "integer_score": integer_score,
        }
    )
    return pd.concat((metadata, numeric), axis=1, copy=False)


def expected_result_reference(config: BenchmarkConfig) -> tuple[int, str]:
    """Return the arithmetic reference count and ordered identity digest."""

    selected = [
        position
        for position in range(config.rows)
        if position % 10 in (3, 7) and position % 3 == 0
    ]
    selected.sort(
        key=lambda position: (
            (position * 37) % 1009,
            -((position * 17) % 101),
            position,
        )
    )
    return len(selected), _positions_sha256(selected)


def measure_qt_application_delay(
    workload: Callable[[], _T],
    *,
    accept_result: Callable[[_T], object] | None = None,
    interval_ms: int = 10,
    timeout_ms: int = 60_000,
) -> tuple[_T, dict[str, Any]]:
    """Run one worker call while measuring Qt timer and acceptance delays."""

    if not callable(workload):
        raise TypeError("workload must be callable")
    if accept_result is not None and not callable(accept_result):
        raise TypeError("accept_result must be callable")
    if interval_ms <= 0:
        raise ValueError("interval_ms must be greater than zero")
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be greater than zero")

    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, Qt

    application = QCoreApplication.instance()
    if application is None:
        application = QCoreApplication(["easyqc-table-benchmark"])
    loop = QEventLoop()
    controller = RevisionedTaskController()
    timer = QTimer()
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setInterval(interval_ms)
    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)

    tick_times: list[int] = []
    result_holder: list[_T] = []
    error_holder: list[object] = []
    acceptance_ms = 0.0
    workload_started_ns: int | None = None
    workload_finished_ns: int | None = None
    submitted = False
    timed_out = False

    def tick() -> None:
        tick_times.append(time.perf_counter_ns())

    def submit() -> None:
        nonlocal submitted, workload_started_ns
        submitted = True
        workload_started_ns = time.perf_counter_ns()
        controller.submit(1, workload)

    def result_ready(_revision: int, result: object) -> None:
        nonlocal acceptance_ms
        started = time.perf_counter_ns()
        typed_result = result
        if accept_result is not None:
            accept_result(typed_result)  # type: ignore[arg-type]
        acceptance_ms = _elapsed_ms(started)
        result_holder.append(typed_result)  # type: ignore[arg-type]

    def error_raised(_revision: int, error: object) -> None:
        error_holder.append(error)

    def busy_changed(busy: bool) -> None:
        nonlocal workload_finished_ns
        if submitted and not busy:
            workload_finished_ns = time.perf_counter_ns()
            QTimer.singleShot(interval_ms * 2, loop.quit)

    def timeout() -> None:
        nonlocal timed_out
        timed_out = True
        controller.cancel()
        loop.quit()

    timer.timeout.connect(tick)
    timeout_timer.timeout.connect(timeout)
    controller.resultReady.connect(result_ready)
    controller.errorRaised.connect(error_raised)
    controller.busyChanged.connect(busy_changed)
    timer.start()
    timeout_timer.start(timeout_ms)
    QTimer.singleShot(interval_ms * 2, submit)
    loop.exec()
    timer.stop()
    timeout_timer.stop()
    application.processEvents()

    if timed_out:
        raise TimeoutError(f"Qt heartbeat workload exceeded {timeout_ms} ms")
    if error_holder:
        error = error_holder[0]
        if isinstance(error, Exception):
            raise RuntimeError("Qt heartbeat workload failed") from error
        raise RuntimeError(f"Qt heartbeat workload failed: {error}")
    if not result_holder or workload_started_ns is None or workload_finished_ns is None:
        raise RuntimeError("Qt heartbeat workload returned no measurable result")

    intervals = [
        (current - previous) / 1_000_000
        for previous, current in zip(tick_times, tick_times[1:])
    ]
    maximum_interval = max(intervals, default=0.0)
    timer_excess = max(0.0, maximum_interval - interval_ms)
    maximum_delay = max(timer_excess, acceptance_ms)
    measurement_complete = len(intervals) >= 2
    return result_holder[0], {
        "timer_interval_ms": interval_ms,
        "sample_count": len(intervals),
        "samples_ms": [round(value, 3) for value in intervals],
        "max_timer_interval_ms": round(maximum_interval, 3),
        "timer_excess_delay_ms": round(timer_excess, 3),
        "acceptance_ms": round(acceptance_ms, 3),
        "max_application_delay_ms": round(maximum_delay, 3),
        "workload_elapsed_ms": round(
            (workload_finished_ns - workload_started_ns) / 1_000_000,
            3,
        ),
        "measurement_complete": measurement_complete,
    }


def run_benchmark(config: BenchmarkConfig) -> dict[str, Any]:
    """Measure one generated filter/sort/window workload and all gates."""

    build_started = time.perf_counter_ns()
    source = build_synthetic_table(config)
    source_build_ms = _elapsed_ms(build_started)
    distribution = _dataset_distribution(source)

    service_started = time.perf_counter_ns()
    service = TableViewService(source)
    service_init_ms = _elapsed_ms(service_started)
    state = replace(
        service.default_state(page_size=config.page_size),
        conditions=(
            FilterCondition("group", "==", "case", "benchmark-group"),
            FilterCondition(
                "marker",
                "in",
                ("M03", "M07"),
                "benchmark-marker",
            ),
            FilterCondition(
                "short_text",
                "contains",
                "review-needed",
                "benchmark-text",
            ),
        ),
        sort_rules=(
            SortRule("metric_000", True),
            SortRule("metric_001", False),
        ),
    )
    expected_count, expected_sha256 = expected_result_reference(config)
    if _is_canonical(config) and (
        expected_count != CANONICAL_MATCHED_ROWS
        or expected_sha256 != CANONICAL_RESULT_SHA256
    ):
        raise RuntimeError("canonical arithmetic reference no longer matches its lock")

    last_window_rows = 0
    for _ in range(config.warmups):
        result = service.apply_state(state)
        _assert_expected_result(result, expected_count, expected_sha256)
        window = service.get_window(result, 0, config.page_size)
        last_window_rows = len(window.dataframe)

    samples: list[float] = []
    for _ in range(config.iterations):
        started = time.perf_counter_ns()
        result = service.apply_state(state)
        window = service.get_window(result, 0, config.page_size)
        samples.append(_elapsed_ms(started))
        _assert_expected_result(result, expected_count, expected_sha256)
        last_window_rows = len(window.dataframe)

    heartbeat_result, heartbeat = measure_qt_application_delay(
        lambda: service.apply_state(state),
        accept_result=lambda result: service.get_window(
            result,
            0,
            config.page_size,
        ),
        interval_ms=config.heartbeat_interval_ms,
    )
    _assert_expected_result(heartbeat_result, expected_count, expected_sha256)
    heartbeat["result_sha256"] = _positions_sha256(
        heartbeat_result.source_positions
    )

    p50 = _percentile(samples, 50)
    p95 = _percentile(samples, 95)
    memory = _peak_rss()
    latency_passed = p95 <= config.budget_p95_ms
    memory_value = memory["peak_rss_mib"]
    memory_passed = (
        memory_value is not None
        and float(memory_value) <= config.budget_peak_rss_mib
    )
    heartbeat_passed = (
        heartbeat["measurement_complete"]
        and heartbeat["max_application_delay_ms"] <= config.budget_qt_delay_ms
    )
    result_hash_passed = (
        heartbeat["result_sha256"] == expected_sha256
        and expected_count == heartbeat_result.matched_total
    )
    overall_passed = all(
        (latency_passed, memory_passed, heartbeat_passed, result_hash_passed)
    )

    return {
        "schema_version": 2,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "source": "synthetic",
            "schema_version": DATASET_SCHEMA_VERSION,
            "shape": [config.rows, config.columns],
            "seed": config.seed,
            "column_families": _column_families(config),
            "distribution": distribution,
            "authoritative_runtime_data_used": False,
        },
        "workload": {
            "description": (
                "three typed filters + stable two-column sort + first row window"
            ),
            "filters": [
                "group == case",
                "marker in [M03, M07]",
                "short_text contains review-needed",
            ],
            "sort": ["metric_000 ascending", "metric_001 descending"],
            "iterations": config.iterations,
            "warmups": config.warmups,
            "page_size": config.page_size,
            "query_backend": "pandas",
            "qt_dispatch": "RevisionedTaskController",
        },
        "initialization_ms": {
            "source_build": round(source_build_ms, 3),
            "table_service": round(service_init_ms, 3),
        },
        "latency_ms": {
            "samples": [round(sample, 3) for sample in samples],
            "p50": round(p50, 3),
            "p95": round(p95, 3),
            "minimum": round(min(samples), 3),
            "maximum": round(max(samples), 3),
        },
        "result": {
            "matched_rows": heartbeat_result.matched_total,
            "expected_matched_rows": expected_count,
            "window_rows": last_window_rows,
            "positions_sha256": heartbeat["result_sha256"],
            "expected_sha256": expected_sha256,
            "canonical_expected_sha256": CANONICAL_RESULT_SHA256,
            "deterministic_hash_passed": result_hash_passed,
        },
        "memory": memory,
        "qt_heartbeat": heartbeat,
        "environment": _environment(),
        "gate": {
            "latency": {
                "metric": "common filter/sort/window p95",
                "value": round(p95, 3),
                "budget": float(config.budget_p95_ms),
                "passed": latency_passed,
            },
            "memory": {
                "metric": "process peak RSS MiB",
                "value": memory_value,
                "budget": float(config.budget_peak_rss_mib),
                "passed": memory_passed,
            },
            "qt_application_delay": {
                "metric": "maximum Qt application delay ms",
                "value": heartbeat["max_application_delay_ms"],
                "budget": float(config.budget_qt_delay_ms),
                "measurement_complete": heartbeat["measurement_complete"],
                "passed": heartbeat_passed,
            },
            "result_hash": {
                "metric": "ordered identity SHA-256",
                "value": heartbeat["result_sha256"],
                "expected": expected_sha256,
                "passed": result_hash_passed,
            },
            "overall_passed": overall_passed,
        },
        "limitations": [
            "Synthetic warm-cache measurement on the recorded machine.",
            "Qt heartbeat uses QCoreApplication and bounded window acceptance, not native paint latency.",
            "Peak RSS is process-lifetime high-water memory, not per-query allocation.",
            "A constrained server process is not represented as a physical 16 GiB workstation.",
        ],
    }


def _benchmark_state_positions(result: TableViewResult) -> np.ndarray:
    return np.asarray(result.source_positions, dtype=np.int64)


def _assert_expected_result(
    result: TableViewResult,
    expected_count: int,
    expected_sha256: str,
) -> None:
    actual_sha256 = _positions_sha256(_benchmark_state_positions(result))
    if result.matched_total != expected_count or actual_sha256 != expected_sha256:
        raise RuntimeError(
            "benchmark query result mismatch: "
            f"expected {expected_count}/{expected_sha256}, "
            f"received {result.matched_total}/{actual_sha256}"
        )


def _positions_sha256(positions: Sequence[int] | np.ndarray) -> str:
    digest = hashlib.sha256()
    for position in positions:
        digest.update(f"SUB{int(position):07d}\n".encode("ascii"))
    return digest.hexdigest()


def _is_canonical(config: BenchmarkConfig) -> bool:
    return (
        config.rows == 100_000
        and config.columns == 300
        and config.seed == 20_260_718
    )


def _column_families(config: BenchmarkConfig) -> dict[str, int]:
    return {
        "identity": 1,
        "categorical": 4,
        "bounded_text": 3,
        "boolean": 1,
        "datetime": 1,
        "nullable_integer": 1,
        "numeric": config.columns - len(METADATA_COLUMNS),
    }


def _dataset_distribution(source: pd.DataFrame) -> dict[str, Any]:
    numeric = source.iloc[:, len(METADATA_COLUMNS) :]
    text_columns = ("short_text", "chinese_text", "long_label")
    return {
        "source_memory_mib": round(
            float(source.memory_usage(index=True, deep=True).sum()) / 1024**2,
            3,
        ),
        "numeric_null_cells": int(numeric.isna().sum().sum()),
        "nullable_metadata_nulls": {
            column: int(source[column].isna().sum())
            for column in ("cohort", "flag", "acquired_at", "integer_score")
        },
        "categorical_cardinality": {
            column: int(source[column].nunique(dropna=True))
            for column in ("group", "marker", "site", "cohort")
        },
        "bounded_text_max_length": {
            column: int(source[column].str.len().max()) for column in text_columns
        },
        "numeric_distributions": [
            "deterministic modular sort keys",
            "normal",
            "absolute normal",
            "rounded scaled normal",
        ],
    }


def _elapsed_ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1_000_000


def _percentile(samples: Sequence[float], percentile: float) -> float:
    ordered = sorted(float(sample) for sample in samples)
    if not ordered:
        raise ValueError("percentile requires at least one sample")
    rank = (len(ordered) - 1) * percentile / 100
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _peak_rss() -> dict[str, Any]:
    if os.name == "nt":
        return _windows_peak_rss()
    try:
        import resource
    except ImportError:
        return {"peak_rss_mib": None, "method": "unavailable"}
    value = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return {
        "peak_rss_mib": round(value / divisor, 3),
        "method": "resource.getrusage(RUSAGE_SELF).ru_maxrss",
    }


def _windows_peak_rss() -> dict[str, Any]:
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        succeeded = ctypes.windll.psapi.GetProcessMemoryInfo(
            process,
            ctypes.byref(counters),
            counters.cb,
        )
        if not succeeded:
            raise OSError("GetProcessMemoryInfo failed")
        return {
            "peak_rss_mib": round(counters.PeakWorkingSetSize / 1024**2, 3),
            "method": "GetProcessMemoryInfo.PeakWorkingSetSize",
        }
    except Exception as exc:
        return {
            "peak_rss_mib": None,
            "method": f"unavailable: {type(exc).__name__}",
        }


def _environment() -> dict[str, Any]:
    import PySide6

    affinity = (
        sorted(int(cpu) for cpu in os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else None
    )
    return {
        "source_revision": _source_revision(),
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "numpy": np.__version__,
        "pyside": PySide6.__version__,
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": _processor_name(),
        "logical_cpu_count": os.cpu_count(),
        "cpu_affinity": affinity,
        "cpu_affinity_count": len(affinity) if affinity is not None else None,
        "physical_memory_mib": _physical_memory_mib(),
        "address_space_limit_mib": _address_space_limit_mib(),
    }


def _source_revision() -> str:
    explicit = os.environ.get("EASYQC_SOURCE_REVISION", "").strip()
    if explicit:
        return explicit
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision = completed.stdout.strip()
    return revision if completed.returncode == 0 and revision else "unknown"


def _processor_name() -> str:
    value = platform.processor().strip()
    if value:
        return value
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return platform.machine()


def _physical_memory_mib() -> float | None:
    if os.name == "nt":
        try:
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatusEx()
            status.dwLength = ctypes.sizeof(status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return round(status.ullTotalPhys / 1024**2, 3)
        except Exception:
            return None
    try:
        pages = int(os.sysconf("SC_PHYS_PAGES"))
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return None
    return round(pages * page_size / 1024**2, 3)


def _address_space_limit_mib() -> float | None:
    try:
        import resource
    except ImportError:
        return None
    soft_limit, _hard_limit = resource.getrlimit(resource.RLIMIT_AS)
    if soft_limit == resource.RLIM_INFINITY:
        return None
    return round(float(soft_limit) / 1024**2, 3)


def write_report(report: dict[str, Any], output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_path, output)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=100_000)
    parser.add_argument("--columns", type=int, default=300)
    parser.add_argument("--iterations", type=int, default=11)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--seed", type=int, default=20_260_718)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--budget-p95-ms", type=float, default=500.0)
    parser.add_argument("--budget-peak-rss-mib", type=float, default=4096.0)
    parser.add_argument("--budget-qt-delay-ms", type=float, default=100.0)
    parser.add_argument("--heartbeat-interval-ms", type=int, default=10)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = BenchmarkConfig(
        rows=args.rows,
        columns=args.columns,
        iterations=args.iterations,
        warmups=args.warmups,
        seed=args.seed,
        page_size=args.page_size,
        budget_p95_ms=args.budget_p95_ms,
        budget_peak_rss_mib=args.budget_peak_rss_mib,
        budget_qt_delay_ms=args.budget_qt_delay_ms,
        heartbeat_interval_ms=args.heartbeat_interval_ms,
    )
    report = run_benchmark(config)
    report["command"] = shlex.join([sys.executable, *sys.argv])
    report["output"] = str(args.output.resolve())
    write_report(report, args.output)
    latency = report["latency_ms"]
    memory = report["memory"]["peak_rss_mib"]
    qt_delay = report["qt_heartbeat"]["max_application_delay_ms"]
    verdict = "PASS" if report["gate"]["overall_passed"] else "FAIL"
    print(
        f"{verdict}: p50={latency['p50']:.3f} ms, "
        f"p95={latency['p95']:.3f} ms, peak_rss={memory} MiB, "
        f"qt_delay={qt_delay:.3f} ms, report={args.output.resolve()}"
    )
    return 0 if report["gate"]["overall_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
