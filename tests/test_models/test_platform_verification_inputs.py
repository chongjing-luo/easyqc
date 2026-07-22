from __future__ import annotations

from copy import deepcopy
import sys

import pytest

from models.platform_verification import (
    AutomatedCheckPlanV1,
    NativeUiChecklistV1,
    PlatformVerificationContractError,
    REQUIRED_NATIVE_UI_ITEM_IDS,
    RawVerificationResultV1,
    VerificationRequestV1,
)


GIB = 1024 * 1024 * 1024


def request_object(
    *,
    run_id: str = "run-ci-ubuntu-22-001",
    row_id: str = "ci-ubuntu-22.04-x86_64",
    target_id: str = "ubuntu-22.04-x86_64",
    command_version: str = "platform-v1",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "matrix_row_id": row_id,
        "source": {"revision": "a" * 40, "dirty": False},
        "release": {
            "release_id": "easyqc-1.0.0",
            "manifest_sha256": "b" * 64,
            "target_id": target_id,
        },
        "runtime": {
            "uv": "0.11.29",
            "python": "3.13.13",
            "qt": "6.11.1",
            "pyside": "6.11.1",
            "pandas": "2.3.3",
            "numpy": "2.4.2",
            "lock_sha256": "1" * 64,
        },
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
            "color_scheme": "system",
            "remote": False,
        },
        "hardware": {
            "cpu": "synthetic-4-core",
            "ram_bytes": 16 * GIB,
            "gpu_or_renderer": "llvmpipe",
        },
        "command_or_checklist_version": command_version,
    }


def check_plan_object() -> dict[str, object]:
    return {
        "schema_version": 1,
        "command_version": "platform-v1",
        "checks": [
            {
                "name": "core-suite",
                "argv": [sys.executable, "-c", "raise SystemExit(0)"],
                "timeout_seconds": 30.0,
                "report_path": "reports/core-suite.json",
                "classification": "test-report",
            }
        ],
    }


def raw_result_object() -> dict[str, object]:
    return {
        "schema_version": 1,
        "request": request_object(),
        "started_at_utc": "2026-07-22T06:00:00Z",
        "completed_at_utc": "2026-07-22T06:00:02Z",
        "status": "PASS",
        "reason_code": None,
        "tests": [
            {
                "name": "core-suite",
                "status": "PASS",
                "duration": 2.0,
                "report_path": "reports/core-suite.json",
            }
        ],
        "metrics": {
            "query_p50_ms": None,
            "query_p95_ms": None,
            "peak_rss_bytes": None,
            "max_event_loop_delay_ms": None,
        },
        "artifacts": [
            {
                "path": "reports/core-suite.json",
                "classification": "test-report",
            }
        ],
        "limitations": [],
    }


def native_checklist_object(
    *,
    status: str = "PASS",
    reason_code: str | None = None,
    notes: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "checklist_version": "native-ui-v1",
        "operator_id": "operator-01",
        "started_at_utc": "2026-07-22T06:00:00Z",
        "completed_at_utc": "2026-07-22T06:05:00Z",
        "items": [
            {
                "item_id": item_id,
                "status": status,
                "reason_code": reason_code,
                "notes": notes,
            }
            for item_id in REQUIRED_NATIVE_UI_ITEM_IDS
        ],
    }


def test_request_plan_and_raw_result_have_strict_canonical_round_trips() -> None:
    request = VerificationRequestV1.from_json_object(request_object())
    plan = AutomatedCheckPlanV1.from_json_object(check_plan_object())
    raw = RawVerificationResultV1.from_json_object(raw_result_object())

    assert VerificationRequestV1.from_canonical_bytes(request.canonical_bytes) == request
    assert AutomatedCheckPlanV1.from_canonical_bytes(plan.canonical_bytes) == plan
    assert RawVerificationResultV1.from_canonical_bytes(raw.canonical_bytes) == raw
    assert raw.request == request
    assert plan.command_version == request.command_or_checklist_version
    assert tuple(check.name for check in plan.checks) == ("core-suite",)

    unknown = request.to_json_object()
    unknown["unexpected"] = True
    with pytest.raises(PlatformVerificationContractError, match="unknown fields"):
        VerificationRequestV1.from_json_object(unknown)


def test_request_and_plan_reject_wrong_identity_or_unsafe_command_contracts() -> None:
    wrong_target = request_object(target_id="ubuntu-24.04-x86_64")
    with pytest.raises(PlatformVerificationContractError, match="target.*matrix row"):
        VerificationRequestV1.from_json_object(wrong_target)

    empty_argv = check_plan_object()
    empty_argv["checks"][0]["argv"] = []  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="argv"):
        AutomatedCheckPlanV1.from_json_object(empty_argv)

    unsafe_path = check_plan_object()
    unsafe_path["checks"][0]["report_path"] = "../report.json"  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="relative.*path"):
        AutomatedCheckPlanV1.from_json_object(unsafe_path)

    duplicate = check_plan_object()
    duplicate["checks"].append(deepcopy(duplicate["checks"][0]))  # type: ignore[union-attr,index]
    with pytest.raises(PlatformVerificationContractError, match="duplicate.*name"):
        AutomatedCheckPlanV1.from_json_object(duplicate)


def test_native_checklist_requires_exact_stable_nonblank_items() -> None:
    checklist = NativeUiChecklistV1.from_json_object(native_checklist_object())

    assert NativeUiChecklistV1.from_canonical_bytes(checklist.canonical_bytes) == checklist
    assert tuple(item.item_id for item in checklist.items) == REQUIRED_NATIVE_UI_ITEM_IDS

    missing = native_checklist_object()
    missing["items"] = missing["items"][:-1]  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="required.*item"):
        NativeUiChecklistV1.from_json_object(missing)

    duplicate = native_checklist_object()
    duplicate["items"][-1]["item_id"] = REQUIRED_NATIVE_UI_ITEM_IDS[0]  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="duplicate.*item"):
        NativeUiChecklistV1.from_json_object(duplicate)

    unknown = native_checklist_object()
    unknown["items"][-1]["item_id"] = "UI-UNKNOWN-01"  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="unknown.*item"):
        NativeUiChecklistV1.from_json_object(unknown)

    blank = native_checklist_object()
    blank["items"][0]["status"] = ""  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="status"):
        NativeUiChecklistV1.from_json_object(blank)


def test_native_non_pass_items_require_notes_and_controlled_reasons() -> None:
    failed_without_notes = native_checklist_object(status="FAIL")
    with pytest.raises(PlatformVerificationContractError, match="FAIL.*notes"):
        NativeUiChecklistV1.from_json_object(failed_without_notes)

    not_run_without_reason = native_checklist_object(
        status="NOT_RUN",
        notes="session unavailable",
    )
    with pytest.raises(PlatformVerificationContractError, match="NOT_RUN.*reason"):
        NativeUiChecklistV1.from_json_object(not_run_without_reason)

    pass_with_reason = native_checklist_object(reason_code="session-not-available")
    with pytest.raises(PlatformVerificationContractError, match="PASS.*reason"):
        NativeUiChecklistV1.from_json_object(pass_with_reason)

    ambiguous = native_checklist_object(
        status="NOT_RUN",
        reason_code="session-not-available",
        notes="not run",
    )
    ambiguous["items"][-1]["reason_code"] = "runner-not-provided"  # type: ignore[index]
    with pytest.raises(PlatformVerificationContractError, match="one.*NOT_RUN.*reason"):
        NativeUiChecklistV1.from_json_object(ambiguous)
