"""Reproducible real-file benchmark for rating scan and aggregation.

Input: one empty caller-owned workspace and a :class:`RatingBenchmarkConfig`.
Output: one JSON-serializable report for the production
``RatingService.load_state`` path.
Side effects: creates only a synthetic project below the supplied workspace.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass
import json
import platform
from pathlib import Path
import sys
import tempfile
from time import perf_counter
from typing import Any, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

with redirect_stdout(sys.stderr if __name__ == "__main__" else sys.stdout):
    from core.rating_identity import (  # noqa: E402
        RatingIdentity,
        build_rating_filename,
    )
    from core.rating_service import RatingService  # noqa: E402
    from models.project import Project  # noqa: E402
    from scripts.benchmark_table_query import _peak_rss  # noqa: E402


@dataclass(frozen=True)
class RatingBenchmarkConfig:
    """Dataset size and fail-loud resource budgets."""

    rating_count: int = 100_000
    budget_elapsed_seconds: float = 60.0
    budget_peak_rss_mib: float = 4096.0
    module_name: str = "BenchmarkQC"
    rater: str = "benchmark_rater"

    def __post_init__(self) -> None:
        if type(self.rating_count) is not int or self.rating_count <= 0:
            raise ValueError("rating_count must be a positive integer")
        if self.budget_elapsed_seconds <= 0:
            raise ValueError("budget_elapsed_seconds must be greater than zero")
        if self.budget_peak_rss_mib <= 0:
            raise ValueError("budget_peak_rss_mib must be greater than zero")
        RatingIdentity(self.module_name, self.rater, "_benchmark_probe_")


def build_synthetic_rating_project(
    workspace: Path,
    config: RatingBenchmarkConfig,
) -> tuple[Project, pd.DataFrame, dict[str, Any]]:
    """Create one deterministic schema-v3 rating tree before measurement."""

    workspace = Path(workspace)
    if not workspace.is_dir():
        raise ValueError("benchmark workspace must be an existing directory")
    project = Project(
        "RATING_BENCHMARK",
        workspace / "easyqc_RATING_BENCHMARK",
    )
    if project.path.exists():
        raise ValueError("benchmark project path must not already exist")

    target_dir = project.rating_dir / config.module_name / config.rater
    target_dir.mkdir(parents=True)
    identities = [
        f"ROW{position:07d}"
        for position in range(config.rating_count)
    ]
    base_payload: dict[str, Any] = {
        "schema_version": 3,
        "name": config.module_name,
        "label": "Synthetic benchmark QC",
        "rater": config.rater,
        "easyqcid": None,
        "watch_mode": False,
        "scores": {
            "1": {
                "label": "Quality",
                "num": "Poor,Good",
                "num_": "Poor,Good",
                "value": "Good",
            }
        },
        "tags": {
            "1": {
                "label": "Review",
                "value": False,
            }
        },
        "code": None,
        "code_exe": {},
        "notes": None,
        "time": None,
        "interper": "shell",
        "control": False,
        "showing": True,
        "select_filter": None,
        "qc_filter": None,
        "button": {},
    }

    generated_bytes = 0
    started = perf_counter()
    for easyqcid in identities:
        payload = dict(base_payload)
        payload["easyqcid"] = easyqcid
        content = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        identity = RatingIdentity(
            config.module_name,
            config.rater,
            easyqcid,
        )
        (target_dir / build_rating_filename(identity)).write_bytes(content)
        generated_bytes += len(content)
    generation_seconds = perf_counter() - started

    subjects = pd.DataFrame(
        {
            "easyqcid": identities,
            "batch": [
                f"B{position % 4}"
                for position in range(config.rating_count)
            ],
        }
    )
    return (
        project,
        subjects,
        {
            "generation_seconds": round(generation_seconds, 6),
            "generated_json_bytes": generated_bytes,
            "json_file_count": config.rating_count,
        },
    )


def run_rating_aggregation_benchmark(
    project: Project,
    subjects: pd.DataFrame,
    config: RatingBenchmarkConfig,
    *,
    generation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Measure the production load-state stages without changing their order."""

    service = RatingService(project)
    stage_measurements: dict[str, dict[str, float | None]] = {}
    total_started = perf_counter()

    stage_started = perf_counter()
    records = service.load_all_rating_records()
    stage_measurements["scan_validate_json"] = _stage_measurement(stage_started)

    ratings = [rating for rating, _path in records]
    stage_started = perf_counter()
    original_table = service.rating_records_to_long_dataframe(records)
    stage_measurements["flatten_long"] = _stage_measurement(stage_started)

    stage_started = perf_counter()
    original_wide_table = service.long_table_to_wide(original_table)
    stage_measurements["pivot_wide"] = _stage_measurement(stage_started)

    stage_started = perf_counter()
    rating_dict = service.build_rating_dict(ratings)
    stage_measurements["build_rating_dict"] = _stage_measurement(stage_started)

    stage_started = perf_counter()
    qctable = service.merge_subjects_with_rating_wide(
        original_wide_table,
        subjects,
    )
    stage_measurements["merge_subjects"] = _stage_measurement(stage_started)

    elapsed_seconds = perf_counter() - total_started
    memory = _peak_rss()
    peak_rss_mib = memory["peak_rss_mib"]
    json_file_count = sum(
        1
        for _path in project.rating_dir.rglob("*.json")
    )
    expected_first = "ROW0000000"
    expected_last = f"ROW{config.rating_count - 1:07d}"
    score_column = f"{config.module_name}.{config.rater}.score1"
    first_easyqcid = (
        str(qctable["easyqcid"].iloc[0])
        if not qctable.empty
        else None
    )
    last_easyqcid = (
        str(qctable["easyqcid"].iloc[-1])
        if not qctable.empty
        else None
    )
    correctness = {
        "rating_count_matches": len(ratings) == config.rating_count,
        "rating_dict_count_matches": (
            len(rating_dict) == config.rating_count
        ),
        "qctable_row_count_matches": (
            len(qctable) == config.rating_count
        ),
        "first_easyqcid": first_easyqcid,
        "last_easyqcid": last_easyqcid,
        "score_values_match": (
            score_column in qctable.columns
            and bool(qctable[score_column].eq("Good").all())
        ),
    }
    correctness_passed = (
        correctness["rating_count_matches"] is True
        and correctness["rating_dict_count_matches"] is True
        and correctness["qctable_row_count_matches"] is True
        and first_easyqcid == expected_first
        and last_easyqcid == expected_last
        and correctness["score_values_match"] is True
    )
    elapsed_passed = elapsed_seconds <= config.budget_elapsed_seconds
    rss_passed = (
        peak_rss_mib is not None
        and peak_rss_mib <= config.budget_peak_rss_mib
    )
    report = {
        "schema_version": 1,
        "benchmark": "easyqc_rating_scan_aggregation",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "peak_rss_method": memory["method"],
        },
        "dataset": {
            "rating_count": config.rating_count,
            "json_file_count": json_file_count,
            "subject_count": len(subjects),
            "module_name": config.module_name,
            "rater": config.rater,
            **(generation or {}),
        },
        "measurement": {
            "elapsed_seconds": round(elapsed_seconds, 6),
            "peak_rss_mib": peak_rss_mib,
            "stages": stage_measurements,
        },
        "correctness": correctness,
        "gates": {
            "correctness": {
                "passed": correctness_passed,
            },
            "elapsed_seconds": {
                "value": round(elapsed_seconds, 6),
                "budget": config.budget_elapsed_seconds,
                "passed": elapsed_passed,
            },
            "peak_rss_mib": {
                "value": peak_rss_mib,
                "budget": config.budget_peak_rss_mib,
                "passed": rss_passed,
            },
        },
    }
    report["passed"] = (
        correctness_passed
        and elapsed_passed
        and rss_passed
        and json_file_count == config.rating_count
    )
    return report


def _stage_measurement(started: float) -> dict[str, float | None]:
    memory = _peak_rss()
    return {
        "elapsed_seconds": round(perf_counter() - started, 6),
        "peak_rss_mib": memory["peak_rss_mib"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark EasyQC rating JSON scan and aggregation",
    )
    parser.add_argument("--rating-count", type=int, default=100_000)
    parser.add_argument(
        "--budget-elapsed-seconds",
        type=float,
        default=60.0,
    )
    parser.add_argument(
        "--budget-peak-rss-mib",
        type=float,
        default=4096.0,
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional new JSON evidence file; existing files are not replaced",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = RatingBenchmarkConfig(
        rating_count=args.rating_count,
        budget_elapsed_seconds=args.budget_elapsed_seconds,
        budget_peak_rss_mib=args.budget_peak_rss_mib,
    )
    with tempfile.TemporaryDirectory(
        prefix="easyqc-rating-benchmark-",
    ) as temporary:
        project, subjects, generation = build_synthetic_rating_project(
            Path(temporary),
            config,
        )
        result = run_rating_aggregation_benchmark(
            project,
            subjects,
            config,
            generation=generation,
        )
    rendered = json.dumps(
        result,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    if args.output is not None:
        output = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.write("\n")
    print(rendered)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
