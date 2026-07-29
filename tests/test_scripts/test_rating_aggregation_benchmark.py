from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.benchmark_rating_aggregation import (
    RatingBenchmarkConfig,
    build_synthetic_rating_project,
    run_rating_aggregation_benchmark,
)


def test_rating_benchmark_config_rejects_nonpositive_inputs() -> None:
    with pytest.raises(ValueError, match="rating_count"):
        RatingBenchmarkConfig(rating_count=0)
    with pytest.raises(ValueError, match="budget_elapsed_seconds"):
        RatingBenchmarkConfig(budget_elapsed_seconds=0)
    with pytest.raises(ValueError, match="budget_peak_rss_mib"):
        RatingBenchmarkConfig(budget_peak_rss_mib=0)


def test_small_rating_benchmark_uses_real_schema_v3_files(
    tmp_path: Path,
) -> None:
    config = RatingBenchmarkConfig(
        rating_count=24,
        budget_elapsed_seconds=20,
        budget_peak_rss_mib=4096,
    )
    project, subjects, generation = build_synthetic_rating_project(
        tmp_path,
        config,
    )

    files = sorted(project.rating_dir.rglob("*.json"))
    sample = json.loads(files[0].read_text(encoding="utf-8"))
    result = run_rating_aggregation_benchmark(
        project,
        subjects,
        config,
        generation=generation,
    )

    assert len(files) == 24
    assert sample["schema_version"] == 3
    assert sample["easyqcid"] == "ROW0000000"
    assert result["schema_version"] == 1
    assert result["dataset"]["rating_count"] == 24
    assert result["dataset"]["json_file_count"] == 24
    assert result["correctness"] == {
        "rating_count_matches": True,
        "rating_dict_count_matches": True,
        "qctable_row_count_matches": True,
        "first_easyqcid": "ROW0000000",
        "last_easyqcid": "ROW0000023",
        "score_values_match": True,
    }
    assert result["gates"]["elapsed_seconds"]["passed"] is True
    assert result["gates"]["peak_rss_mib"]["passed"] is True
    assert result["passed"] is True


def test_rating_benchmark_cli_emits_machine_readable_result(
    easyqc_root: Path,
) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/benchmark_rating_aggregation.py",
            "--rating-count",
            "12",
            "--budget-elapsed-seconds",
            "20",
            "--budget-peak-rss-mib",
            "4096",
        ],
        cwd=easyqc_root,
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["dataset"]["rating_count"] == 12
    assert result["passed"] is True
