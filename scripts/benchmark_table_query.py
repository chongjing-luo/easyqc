"""Reproducible synthetic benchmark for the EasyQC table query path."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shlex
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.table_view_service import TableViewService
from models.table_view_state import FilterCondition, SortRule


@dataclass(frozen=True)
class BenchmarkConfig:
    rows: int = 50_000
    columns: int = 300
    iterations: int = 7
    warmups: int = 1
    seed: int = 20_260_718
    page_size: int = 200
    budget_p95_ms: float = 300.0

    def __post_init__(self) -> None:
        if self.rows <= 0:
            raise ValueError("rows must be greater than zero")
        if self.columns < 5:
            raise ValueError("columns must include metadata and two sort columns")
        if self.iterations < 5:
            raise ValueError("iterations must be at least five")
        if self.warmups < 0:
            raise ValueError("warmups cannot be negative")
        if self.page_size <= 0:
            raise ValueError("page_size must be greater than zero")
        if self.budget_p95_ms <= 0:
            raise ValueError("budget_p95_ms must be greater than zero")


def build_synthetic_table(config: BenchmarkConfig) -> pd.DataFrame:
    """Build the fixed-shape benchmark frame without reading runtime data."""
    numeric_count = config.columns - 3
    rng = np.random.default_rng(config.seed)
    numeric = pd.DataFrame(
        rng.standard_normal((config.rows, numeric_count)),
        columns=[f"metric_{index:03d}" for index in range(numeric_count)],
    )
    positions = np.arange(config.rows)
    metadata = pd.DataFrame(
        {
            "ezqcid": [f"SUB{index:07d}" for index in range(config.rows)],
            "group": np.where(positions % 2 == 0, "control", "case"),
            "marker": np.take(
                np.asarray([f"M{index:02d}" for index in range(10)]),
                positions % 10,
            ),
        }
    )
    return pd.concat((metadata, numeric), axis=1, copy=False)


def run_benchmark(config: BenchmarkConfig) -> dict[str, Any]:
    """Measure complete filter/sort plus bounded window materialization."""
    build_started = time.perf_counter_ns()
    source = build_synthetic_table(config)
    source_build_ms = _elapsed_ms(build_started)

    service_started = time.perf_counter_ns()
    service = TableViewService(source)
    service_init_ms = _elapsed_ms(service_started)
    state = replace(
        service.default_state(page_size=config.page_size),
        conditions=(
            FilterCondition("group", "==", "case", "benchmark-group"),
            FilterCondition("marker", "in", ("M03", "M07"), "benchmark-marker"),
        ),
        sort_rules=(
            SortRule("metric_000", True),
            SortRule("metric_001", False),
        ),
    )

    expected_match_count: int | None = None
    last_window_rows = 0
    for _ in range(config.warmups):
        result = service.apply_state(state)
        window = service.get_window(result, 0, config.page_size)
        expected_match_count = result.matched_total
        last_window_rows = len(window.dataframe)

    samples: list[float] = []
    for _ in range(config.iterations):
        started = time.perf_counter_ns()
        result = service.apply_state(state)
        window = service.get_window(result, 0, config.page_size)
        samples.append(_elapsed_ms(started))
        if expected_match_count is None:
            expected_match_count = result.matched_total
        elif result.matched_total != expected_match_count:
            raise RuntimeError("benchmark query produced a non-deterministic row count")
        last_window_rows = len(window.dataframe)

    p50 = _percentile(samples, 50)
    p95 = _percentile(samples, 95)
    return {
        "schema_version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {
            "source": "synthetic",
            "shape": [config.rows, config.columns],
            "seed": config.seed,
            "authoritative_runtime_data_used": False,
        },
        "workload": {
            "description": "two typed filters + stable two-column sort + first row window",
            "iterations": config.iterations,
            "warmups": config.warmups,
            "page_size": config.page_size,
            "query_backend": "pandas",
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
            "matched_rows": int(expected_match_count or 0),
            "window_rows": last_window_rows,
        },
        "memory": _peak_rss(),
        "environment": {
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or platform.machine(),
            "logical_cpu_count": os.cpu_count(),
        },
        "gate": {
            "metric": "common filter/sort/window p95",
            "budget_p95_ms": float(config.budget_p95_ms),
            "passed": p95 <= config.budget_p95_ms,
        },
        "limitations": [
            "Synthetic warm-cache measurement on the recorded machine.",
            "Measures Core query and window materialization, not Qt repaint latency.",
            "Peak RSS is process-lifetime high-water memory, not per-query allocation.",
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
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--columns", type=int, default=300)
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20_260_718)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--budget-p95-ms", type=float, default=300.0)
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
    )
    report = run_benchmark(config)
    report["command"] = shlex.join([sys.executable, *sys.argv])
    report["output"] = str(args.output.resolve())
    write_report(report, args.output)
    latency = report["latency_ms"]
    verdict = "PASS" if report["gate"]["passed"] else "FAIL"
    print(
        f"{verdict}: p50={latency['p50']:.3f} ms, "
        f"p95={latency['p95']:.3f} ms, report={args.output.resolve()}"
    )
    return 0 if report["gate"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
