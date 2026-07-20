from __future__ import annotations

from copy import deepcopy

import pytest

from core.platform_verification import (
    PlatformVerificationError,
    evaluate_release_gate,
)
from models.platform_verification import (
    REQUIRED_MATRIX_ROWS,
    ReleaseGateRequestV1,
    VerificationRunV1,
)


GIB = 1024 * 1024 * 1024


def _runtime_object() -> dict[str, object]:
    return {
        "uv": "0.11.29",
        "python": "3.13.13",
        "qt": "6.11.1",
        "pyside": "6.11.1",
        "pandas": "2.3.3",
        "numpy": "2.4.2",
        "lock_sha256": "1" * 64,
    }


def _request() -> ReleaseGateRequestV1:
    return ReleaseGateRequestV1.from_json_object(
        {
            "schema_version": 1,
            "source": {"revision": "a" * 40, "dirty": False},
            "release_id": "easyqc-1.0.0",
            "manifest_sha256": "b" * 64,
            "runtime": _runtime_object(),
            "command_or_checklist_version": "platform-v1",
        }
    )


def _run_object(
    row_id: str,
    target_id: str,
    index: int,
    *,
    status: str = "PASS",
    reason_code: str | None = None,
) -> dict[str, object]:
    passing = status == "PASS"
    return {
        "schema_version": 1,
        "run_id": f"run-{index:02d}-{row_id}",
        "matrix_row_id": row_id,
        "status": status,
        "reason_code": reason_code,
        "started_at_utc": "2026-07-20T02:00:00Z",
        "completed_at_utc": "2026-07-20T02:00:02Z",
        "source": {"revision": "a" * 40, "dirty": False},
        "release": {
            "release_id": "easyqc-1.0.0",
            "manifest_sha256": "b" * 64,
            "target_id": target_id,
        },
        "runtime": _runtime_object(),
        "platform": {
            "os": "synthetic",
            "version": "1",
            "arch": "x86_64-or-arm64",
            "kernel": "synthetic",
            "runner_label": row_id,
            "runner_image": "fixture-only",
        },
        "display": {
            "session": "offscreen" if row_id.startswith("ci-") else "native-fixture",
            "scale_percent": 100,
            "logical_viewport": "1280x720",
            "color_scheme": "light",
            "remote": False,
        },
        "hardware": {
            "cpu": "synthetic",
            "ram_bytes": 16 * GIB,
            "gpu_or_renderer": "synthetic",
        },
        "command_or_checklist_version": "platform-v1",
        "tests": (
            [
                {
                    "name": "required-suite",
                    "status": "PASS",
                    "duration": 1.0,
                    "report_path": f"reports/{row_id}.json",
                    "report_sha256": f"{index + 1:x}" * 64,
                }
            ]
            if passing
            else []
        ),
        "metrics": {
            "query_p50_ms": 100.0 if passing else None,
            "query_p95_ms": 120.0 if passing else None,
            "peak_rss_bytes": 800 * 1024 * 1024 if passing else None,
            "max_event_loop_delay_ms": 10.0 if passing else None,
        },
        "artifacts": (
            [
                {
                    "path": f"reports/{row_id}.json",
                    "sha256": f"{index + 1:x}" * 64,
                    "classification": "test-report",
                }
            ]
            if passing
            else []
        ),
        "limitations": [] if passing else [f"synthetic {status.lower()} fixture"],
    }


def _run(
    row_index: int,
    *,
    status: str = "PASS",
    reason_code: str | None = None,
) -> VerificationRunV1:
    row = REQUIRED_MATRIX_ROWS[row_index]
    return VerificationRunV1.from_json_object(
        _run_object(
            row.matrix_row_id,
            row.target_id,
            row_index,
            status=status,
            reason_code=reason_code,
        )
    )


def _all_pass() -> tuple[VerificationRunV1, ...]:
    return tuple(_run(index) for index in range(len(REQUIRED_MATRIX_ROWS)))


def test_required_matrix_has_four_distinct_ci_and_native_rows() -> None:
    ids = tuple(row.matrix_row_id for row in REQUIRED_MATRIX_ROWS)

    assert len(ids) == len(set(ids)) == 8
    assert len([row for row in REQUIRED_MATRIX_ROWS if row.evidence_class == "ci"]) == 4
    assert len([row for row in REQUIRED_MATRIX_ROWS if row.evidence_class == "native"]) == 4
    assert all(
        row.matrix_row_id.startswith(f"{row.evidence_class}-")
        for row in REQUIRED_MATRIX_ROWS
    )


def test_complete_exact_pass_set_alone_produces_deterministic_go() -> None:
    request = _request()
    runs = _all_pass()
    original_objects = tuple(run.to_json_object() for run in runs)

    forward = evaluate_release_gate(request, runs)
    reverse = evaluate_release_gate(request, tuple(reversed(runs)))

    assert forward.decision == "GO"
    assert forward.blockers == ()
    assert forward == reverse
    assert forward.canonical_bytes == reverse.canonical_bytes
    assert forward.evaluated_run_ids == tuple(
        run.run_id for run in runs
    )
    assert tuple(run.to_json_object() for run in runs) == original_objects


def test_ci_passes_cannot_fill_native_rows() -> None:
    ci_runs = tuple(
        _run(index)
        for index, row in enumerate(REQUIRED_MATRIX_ROWS)
        if row.evidence_class == "ci"
    )

    decision = evaluate_release_gate(_request(), ci_runs)

    assert decision.decision == "NO-GO"
    assert tuple(blocker.matrix_row_id for blocker in decision.blockers) == tuple(
        sorted(
            row.matrix_row_id
            for row in REQUIRED_MATRIX_ROWS
            if row.evidence_class == "native"
        )
    )
    assert {blocker.reason_code for blocker in decision.blockers} == {
        "missing-required-row"
    }


@pytest.mark.parametrize(
    ("status", "reason_code"),
    [
        ("FAIL", "test-failure"),
        ("BLOCKED", "environment-blocked"),
        ("NOT_RUN", "runner-not-provided"),
    ],
)
def test_non_pass_status_truth_table_produces_exact_nogo_blocker(
    status: str, reason_code: str
) -> None:
    runs = list(_all_pass())
    runs[5] = _run(5, status=status, reason_code=reason_code)

    decision = evaluate_release_gate(_request(), runs)

    assert decision.decision == "NO-GO"
    assert len(decision.blockers) == 1
    blocker = decision.blockers[0]
    assert blocker.matrix_row_id == REQUIRED_MATRIX_ROWS[5].matrix_row_id
    assert blocker.status == status
    assert blocker.reason_code == reason_code
    assert blocker.run_id == runs[5].run_id


def test_mixed_blockers_are_exact_and_sorted_independent_of_input_order() -> None:
    runs = list(_all_pass())
    runs[0] = _run(0, status="FAIL", reason_code="test-failure")
    runs[5] = _run(5, status="BLOCKED", reason_code="environment-blocked")
    runs[6] = _run(6, status="NOT_RUN", reason_code="runner-not-provided")
    del runs[7]

    decision = evaluate_release_gate(_request(), tuple(reversed(runs)))

    assert decision.decision == "NO-GO"
    assert [blocker.matrix_row_id for blocker in decision.blockers] == sorted(
        [
            REQUIRED_MATRIX_ROWS[0].matrix_row_id,
            REQUIRED_MATRIX_ROWS[5].matrix_row_id,
            REQUIRED_MATRIX_ROWS[6].matrix_row_id,
            REQUIRED_MATRIX_ROWS[7].matrix_row_id,
        ]
    )
    by_row = {blocker.matrix_row_id: blocker for blocker in decision.blockers}
    assert by_row[REQUIRED_MATRIX_ROWS[7].matrix_row_id].status == "MISSING"
    assert (
        by_row[REQUIRED_MATRIX_ROWS[7].matrix_row_id].reason_code
        == "missing-required-row"
    )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("source_revision", "f" * 40, "source revision"),
        ("source_dirty", True, "dirty state"),
        ("release_id", "easyqc-2.0.0", "release ID"),
        ("manifest_sha256", "f" * 64, "manifest"),
        ("lock_sha256", "f" * 64, "runtime"),
        ("command_version", "platform-v2", "command.*version"),
    ],
)
def test_wrong_identity_evidence_fails_before_evaluation(
    field: str, value: object, message: str
) -> None:
    row = REQUIRED_MATRIX_ROWS[0]
    payload = _run_object(row.matrix_row_id, row.target_id, 0)
    if field == "source_revision":
        payload["source"]["revision"] = value  # type: ignore[index]
    elif field == "source_dirty":
        payload["source"]["dirty"] = value  # type: ignore[index]
    elif field == "release_id":
        payload["release"]["release_id"] = value  # type: ignore[index]
    elif field == "manifest_sha256":
        payload["release"]["manifest_sha256"] = value  # type: ignore[index]
    elif field == "lock_sha256":
        payload["runtime"]["lock_sha256"] = value  # type: ignore[index]
    else:
        payload["command_or_checklist_version"] = value
    wrong = VerificationRunV1.from_json_object(payload)

    with pytest.raises(PlatformVerificationError, match=message):
        evaluate_release_gate(_request(), (wrong,))


def test_duplicate_run_or_matrix_row_is_rejected_as_ambiguous() -> None:
    first = _run(0)
    duplicate_id = _run(1)
    duplicate_id_payload = duplicate_id.to_json_object()
    duplicate_id_payload["run_id"] = first.run_id
    duplicate_id = VerificationRunV1.from_json_object(duplicate_id_payload)
    with pytest.raises(PlatformVerificationError, match="duplicate run_id"):
        evaluate_release_gate(_request(), (first, duplicate_id))

    duplicate_row_payload = deepcopy(first.to_json_object())
    duplicate_row_payload["run_id"] = "run-another-attempt"
    duplicate_row = VerificationRunV1.from_json_object(duplicate_row_payload)
    with pytest.raises(PlatformVerificationError, match="duplicate matrix row"):
        evaluate_release_gate(_request(), (first, duplicate_row))


def test_gate_rejects_more_records_than_the_fixed_matrix_before_scanning() -> None:
    oversized = (_run(0) for _ in range(len(REQUIRED_MATRIX_ROWS) + 1))

    with pytest.raises(PlatformVerificationError, match="at most 8"):
        evaluate_release_gate(_request(), oversized)
