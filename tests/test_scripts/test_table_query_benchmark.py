from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.benchmark_table_query import (
    BenchmarkConfig,
    build_synthetic_table,
    run_benchmark,
)


def test_synthetic_table_has_fixed_shape_and_identity_contract() -> None:
    config = BenchmarkConfig(rows=40, columns=8, iterations=5, warmups=0, seed=17)

    first = build_synthetic_table(config)
    second = build_synthetic_table(config)

    assert first.shape == (40, 8)
    assert tuple(first.columns[:3]) == ("ezqcid", "group", "marker")
    assert first["ezqcid"].is_unique
    assert first.equals(second)


def test_benchmark_records_query_window_latency_and_environment() -> None:
    report = run_benchmark(
        BenchmarkConfig(rows=100, columns=8, iterations=5, warmups=1, seed=23)
    )

    assert report["dataset"]["source"] == "synthetic"
    assert report["dataset"]["shape"] == [100, 8]
    assert len(report["latency_ms"]["samples"]) == 5
    assert report["latency_ms"]["p50"] <= report["latency_ms"]["p95"]
    assert report["result"]["matched_rows"] > 0
    assert report["result"]["window_rows"] <= 100
    assert report["environment"]["python"]
    assert report["environment"]["pandas"]
    assert report["gate"]["budget_p95_ms"] == 300.0
    assert isinstance(report["gate"]["passed"], bool)


@pytest.mark.parametrize(
    ("field", "value"),
    (("rows", 0), ("columns", 4), ("iterations", 4), ("warmups", -1)),
)
def test_benchmark_rejects_nonrepresentative_configuration(field: str, value: int) -> None:
    values = {
        "rows": 100,
        "columns": 8,
        "iterations": 5,
        "warmups": 0,
    }
    values[field] = value

    with pytest.raises(ValueError):
        BenchmarkConfig(**values)


def test_benchmark_script_self_locates_project_root(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "benchmark.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(easyqc_root / "scripts" / "benchmark_table_query.py"),
            "--output",
            str(output),
            "--rows",
            "100",
            "--columns",
            "8",
            "--iterations",
            "5",
            "--warmups",
            "0",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["dataset"]["shape"] == [100, 8]
    assert report["command"]
