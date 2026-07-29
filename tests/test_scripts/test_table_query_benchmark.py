from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from scripts.benchmark_table_query import (
    CANONICAL_MATCHED_ROWS,
    CANONICAL_RESULT_SHA256,
    DATASET_SCHEMA_VERSION,
    METADATA_COLUMNS,
    BenchmarkConfig,
    build_synthetic_table,
    expected_result_reference,
    run_benchmark,
)


def test_default_config_is_the_approved_canonical_capacity_contract() -> None:
    config = BenchmarkConfig()

    assert config.rows == 100_000
    assert config.columns == 300
    assert config.iterations == 11
    assert config.warmups == 2
    assert config.budget_p95_ms == 500.0
    assert config.budget_peak_rss_mib == 4096.0
    assert config.budget_qt_delay_ms == 100.0
    assert config.heartbeat_interval_ms == 10
    assert DATASET_SCHEMA_VERSION == 2
    assert CANONICAL_MATCHED_ROWS == 6667
    assert CANONICAL_RESULT_SHA256 == (
        "12661802f76731af04ba8d187f01a43bb7c1423cc6be7699327de184774b5c19"
    )


def test_synthetic_table_has_mixed_schema_nulls_and_bounded_text() -> None:
    config = BenchmarkConfig(rows=120, columns=16, iterations=5, warmups=0, seed=17)

    first = build_synthetic_table(config)
    second = build_synthetic_table(config)

    assert first.shape == (120, 16)
    assert tuple(first.columns[: len(METADATA_COLUMNS)]) == METADATA_COLUMNS
    assert first["easyqcid"].is_unique
    assert first.equals(second)
    assert all(str(first[column].dtype) == "category" for column in ("group", "marker", "site", "cohort"))
    assert isinstance(first["flag"].dtype, pd.BooleanDtype)
    assert pd.api.types.is_datetime64_any_dtype(first["acquired_at"])
    assert str(first["integer_score"].dtype) == "Int32"
    assert first[["cohort", "flag", "acquired_at", "integer_score", "metric_002"]].isna().any().all()
    assert first["chinese_text"].str.contains("质控", na=False).any()
    assert first["long_label"].str.len().max() <= 96


def test_reference_result_is_independent_and_fixed_for_canonical_config() -> None:
    count, digest = expected_result_reference(BenchmarkConfig())

    assert count == CANONICAL_MATCHED_ROWS
    assert digest == CANONICAL_RESULT_SHA256


def test_benchmark_records_hash_rss_qt_delay_and_reproducibility_environment() -> None:
    report = run_benchmark(
        BenchmarkConfig(
            rows=120,
            columns=16,
            iterations=5,
            warmups=1,
            seed=23,
            budget_p95_ms=10_000,
            budget_peak_rss_mib=10_000,
            budget_qt_delay_ms=10_000,
            heartbeat_interval_ms=2,
        )
    )

    assert report["schema_version"] == 2
    assert report["dataset"]["source"] == "synthetic"
    assert report["dataset"]["schema_version"] == DATASET_SCHEMA_VERSION
    assert report["dataset"]["shape"] == [120, 16]
    assert report["dataset"]["column_families"] == {
        "identity": 1,
        "categorical": 4,
        "bounded_text": 3,
        "boolean": 1,
        "datetime": 1,
        "nullable_integer": 1,
        "numeric": 5,
    }
    assert len(report["latency_ms"]["samples"]) == 5
    assert report["latency_ms"]["p50"] <= report["latency_ms"]["p95"]
    assert report["result"]["matched_rows"] > 0
    assert report["result"]["positions_sha256"] == report["result"]["expected_sha256"]
    assert report["result"]["deterministic_hash_passed"]
    assert report["result"]["window_rows"] <= 100
    assert report["environment"]["python"]
    assert report["environment"]["pandas"]
    assert report["environment"]["numpy"]
    assert report["environment"]["pyside"]
    assert report["environment"]["source_revision"]
    assert report["environment"]["physical_memory_mib"] > 0
    assert report["memory"]["peak_rss_mib"] > 0
    assert report["qt_heartbeat"]["measurement_complete"]
    assert report["qt_heartbeat"]["sample_count"] >= 2
    assert report["qt_heartbeat"]["result_sha256"] == report["result"]["positions_sha256"]
    assert report["gate"]["latency"]["budget"] == 10_000
    assert report["gate"]["memory"]["budget"] == 10_000
    assert report["gate"]["qt_application_delay"]["budget"] == 10_000
    assert report["gate"]["result_hash"]["passed"]
    assert report["gate"]["overall_passed"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("rows", 0),
        ("columns", len(METADATA_COLUMNS) + 1),
        ("iterations", 4),
        ("warmups", -1),
        ("page_size", 0),
        ("budget_p95_ms", 0),
        ("budget_peak_rss_mib", 0),
        ("budget_qt_delay_ms", 0),
        ("heartbeat_interval_ms", 0),
    ),
)
def test_benchmark_rejects_nonrepresentative_configuration(
    field: str,
    value: int,
) -> None:
    values = {
        "rows": 100,
        "columns": 16,
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
            "16",
            "--iterations",
            "5",
            "--warmups",
            "0",
            "--budget-p95-ms",
            "10000",
            "--budget-peak-rss-mib",
            "10000",
            "--budget-qt-delay-ms",
            "10000",
            "--heartbeat-interval-ms",
            "2",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["dataset"]["shape"] == [100, 16]
    assert report["command"]
    assert report["output"] == str(output.resolve())
    assert report["gate"]["overall_passed"]


def test_benchmark_script_writes_failed_composite_gate_and_exits_two(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    output = tmp_path / "failed-benchmark.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(easyqc_root / "scripts" / "benchmark_table_query.py"),
            "--output",
            str(output),
            "--rows",
            "100",
            "--columns",
            "16",
            "--iterations",
            "5",
            "--warmups",
            "0",
            "--budget-p95-ms",
            "0.000001",
            "--budget-peak-rss-mib",
            "10000",
            "--budget-qt-delay-ms",
            "10000",
            "--heartbeat-interval-ms",
            "2",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2, completed.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert not report["gate"]["latency"]["passed"]
    assert not report["gate"]["overall_passed"]
    assert completed.stdout.startswith("FAIL:")
