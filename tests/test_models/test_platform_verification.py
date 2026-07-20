from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

from models.platform_verification import (
    PlatformVerificationContractError,
    ReleaseDecisionV1,
    ReleaseGateRequestV1,
    VerificationRunV1,
    canonical_json_bytes,
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


def _run_object() -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": "run-ci-ubuntu-22-001",
        "matrix_row_id": "ci-ubuntu-22.04-x86_64",
        "status": "PASS",
        "reason_code": None,
        "started_at_utc": "2026-07-20T02:00:00Z",
        "completed_at_utc": "2026-07-20T02:00:02.500000Z",
        "source": {"revision": "a" * 40, "dirty": False},
        "release": {
            "release_id": "easyqc-1.0.0",
            "manifest_sha256": "b" * 64,
            "target_id": "ubuntu-22.04-x86_64",
        },
        "runtime": _runtime_object(),
        "platform": {
            "os": "linux",
            "version": "22.04",
            "arch": "x86_64",
            "kernel": "6.8.0",
            "runner_label": "ubuntu-22.04",
            "runner_image": "ubuntu-22.04-pinned",
        },
        "display": {
            "session": "offscreen",
            "scale_percent": 100,
            "logical_viewport": "1280x720",
            "color_scheme": "light",
            "remote": False,
        },
        "hardware": {
            "cpu": "synthetic-4-core",
            "ram_bytes": 16 * GIB,
            "gpu_or_renderer": "llvmpipe",
        },
        "command_or_checklist_version": "platform-v1",
        "tests": [
            {
                "name": "core-suite",
                "status": "PASS",
                "duration": 1.25,
                "report_path": "reports/core-suite.json",
                "report_sha256": "c" * 64,
            }
        ],
        "metrics": {
            "query_p50_ms": 100.0,
            "query_p95_ms": 125.0,
            "peak_rss_bytes": 900 * 1024 * 1024,
            "max_event_loop_delay_ms": 12.5,
        },
        "artifacts": [
            {
                "path": "reports/core-suite.json",
                "sha256": "c" * 64,
                "classification": "test-report",
            }
        ],
        "limitations": [],
    }


def _request_object() -> dict[str, object]:
    return {
        "schema_version": 1,
        "source": {"revision": "a" * 40, "dirty": False},
        "release_id": "easyqc-1.0.0",
        "manifest_sha256": "b" * 64,
        "runtime": _runtime_object(),
        "command_or_checklist_version": "platform-v1",
    }


def _set_path(payload: dict[str, object], path: tuple[str, ...], value: object) -> None:
    cursor: dict[str, object] = payload
    for part in path[:-1]:
        cursor = cursor[part]  # type: ignore[assignment,index]
    cursor[path[-1]] = value


def test_verification_run_has_one_canonical_full_schema_round_trip() -> None:
    run = VerificationRunV1.from_json_object(_run_object())

    assert VerificationRunV1.from_canonical_bytes(run.canonical_bytes) == run
    assert run.to_json_object() == _run_object()
    assert run.sha256 == hashlib.sha256(run.canonical_bytes).hexdigest()
    assert run.evidence_class == "ci"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("unexpected",), True, "unknown fields"),
        (("source", "unexpected"), True, "unknown fields"),
        (("schema_version",), 2, "schema_version"),
        (("run_id",), "../run", "run_id"),
        (("matrix_row_id",), "ci-unknown", "matrix_row_id"),
        (("status",), "SUCCESS", "status"),
        (("reason_code",), "test-failure", "PASS.*reason"),
        (("source", "revision"), "BAD", "revision"),
        (("source", "dirty"), "false", "dirty"),
        (("release", "manifest_sha256"), "BAD", "SHA-256"),
        (("runtime", "lock_sha256"), "BAD", "SHA-256"),
        (("platform", "runner_label"), "", "non-empty"),
        (("display", "scale_percent"), 0, "scale_percent"),
        (("display", "logical_viewport"), "fullscreen", "viewport"),
        (("display", "remote"), "false", "remote"),
        (("hardware", "ram_bytes"), -1, "ram_bytes"),
        (("metrics", "query_p50_ms"), -1.0, "query_p50_ms"),
    ],
)
def test_verification_run_rejects_malformed_authority_fields(
    path: tuple[str, ...], value: object, message: str
) -> None:
    payload = deepcopy(_run_object())
    _set_path(payload, path, value)

    with pytest.raises(PlatformVerificationContractError, match=message):
        VerificationRunV1.from_json_object(payload)


def test_verification_run_rejects_wrong_row_target_and_time_order() -> None:
    wrong_target = _run_object()
    wrong_target["release"]["target_id"] = "ubuntu-24.04-x86_64"  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="target.*matrix row"):
        VerificationRunV1.from_json_object(wrong_target)

    reverse_time = _run_object()
    reverse_time["completed_at_utc"] = "2026-07-20T01:59:59Z"
    with pytest.raises(PlatformVerificationContractError, match="completed.*start"):
        VerificationRunV1.from_json_object(reverse_time)


def test_pass_requires_passed_tests_and_non_screenshot_hashed_evidence() -> None:
    failed_test = _run_object()
    failed_test["tests"][0]["status"] = "FAIL"  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="PASS.*tests"):
        VerificationRunV1.from_json_object(failed_test)

    screenshot_only = _run_object()
    screenshot_only["artifacts"][0]["classification"] = "screenshot"  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="non-screenshot"):
        VerificationRunV1.from_json_object(screenshot_only)

    unsafe_report = _run_object()
    unsafe_report["tests"][0]["report_path"] = "../report.json"  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="relative.*path"):
        VerificationRunV1.from_json_object(unsafe_report)


def test_metrics_and_non_pass_reason_truth_tables_are_strict() -> None:
    inverted = _run_object()
    inverted["metrics"]["query_p95_ms"] = 99.0  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="p95.*p50"):
        VerificationRunV1.from_json_object(inverted)

    missing_reason = _run_object()
    missing_reason["status"] = "NOT_RUN"
    missing_reason["reason_code"] = None
    missing_reason["tests"] = []
    missing_reason["artifacts"] = []
    missing_reason["limitations"] = ["runner absent"]
    with pytest.raises(PlatformVerificationContractError, match="NOT_RUN.*reason"):
        VerificationRunV1.from_json_object(missing_reason)

    wrong_reason = deepcopy(missing_reason)
    wrong_reason["reason_code"] = "test-failure"
    with pytest.raises(PlatformVerificationContractError, match="reason.*NOT_RUN"):
        VerificationRunV1.from_json_object(wrong_reason)

    no_limitation = deepcopy(missing_reason)
    no_limitation["reason_code"] = "runner-not-provided"
    no_limitation["limitations"] = []
    with pytest.raises(PlatformVerificationContractError, match="limitation"):
        VerificationRunV1.from_json_object(no_limitation)


def test_canonical_parser_rejects_formatting_and_duplicate_keys() -> None:
    run = VerificationRunV1.from_json_object(_run_object())
    noncanonical = run.canonical_bytes.replace(b'"arch":"x86_64"', b'"arch": "x86_64"')
    with pytest.raises(PlatformVerificationContractError, match="canonical JSON"):
        VerificationRunV1.from_canonical_bytes(noncanonical)

    duplicate = run.canonical_bytes.replace(
        b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1
    )
    with pytest.raises(PlatformVerificationContractError, match="duplicate JSON key"):
        VerificationRunV1.from_canonical_bytes(duplicate)


def test_canonical_parser_rejects_oversized_authority_before_json_parsing() -> None:
    oversized = b" " * (4 * 1024 * 1024 + 1)

    with pytest.raises(PlatformVerificationContractError, match="exceeds maximum"):
        VerificationRunV1.from_canonical_bytes(oversized)
    with pytest.raises(PlatformVerificationContractError, match="exceeds maximum"):
        canonical_json_bytes({"padding": "x" * (4 * 1024 * 1024)})


def test_release_gate_request_and_decision_contracts_are_canonical() -> None:
    request = ReleaseGateRequestV1.from_json_object(_request_object())
    assert ReleaseGateRequestV1.from_canonical_bytes(request.canonical_bytes) == request

    go = ReleaseDecisionV1.from_json_object(
        {
            "schema_version": 1,
            "decision": "GO",
            "source_revision": "a" * 40,
            "release_id": "easyqc-1.0.0",
            "manifest_sha256": "b" * 64,
            "evaluated_run_ids": ["run-1"],
            "blockers": [],
        }
    )
    assert ReleaseDecisionV1.from_canonical_bytes(go.canonical_bytes) == go

    invalid_go = go.to_json_object()
    invalid_go["blockers"] = [
        {
            "matrix_row_id": "native-macos-13-arm64",
            "status": "MISSING",
            "reason_code": "missing-required-row",
            "run_id": None,
        }
    ]
    with pytest.raises(PlatformVerificationContractError, match="GO.*blocker"):
        ReleaseDecisionV1.from_json_object(invalid_go)

    unsorted = {
        "schema_version": 1,
        "decision": "NO-GO",
        "source_revision": "a" * 40,
        "release_id": "easyqc-1.0.0",
        "manifest_sha256": "b" * 64,
        "evaluated_run_ids": [],
        "blockers": [
            {
                "matrix_row_id": "native-windows-11-x86_64",
                "status": "MISSING",
                "reason_code": "missing-required-row",
                "run_id": None,
            },
            {
                "matrix_row_id": "ci-ubuntu-22.04-x86_64",
                "status": "MISSING",
                "reason_code": "missing-required-row",
                "run_id": None,
            },
        ],
    }
    with pytest.raises(PlatformVerificationContractError, match="sorted"):
        ReleaseDecisionV1.from_json_object(unsorted)
