from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from core.platform_verification import (
    PlatformVerificationError,
    create_verification_attempt_root,
    normalize_verification_run,
    record_native_check,
    select_latest_accepted,
    write_new_verification_authority,
)
from models.platform_verification import (
    NativeUiChecklistV1,
    REQUIRED_NATIVE_UI_ITEM_IDS,
    RawVerificationResultV1,
    ReleaseGateRequestV1,
    VerificationRequestV1,
    VerificationRunV1,
)


GIB = 1024 * 1024 * 1024


def _request_object(
    *,
    run_id: str = "run-ci-001",
    row_id: str = "ci-ubuntu-22.04-x86_64",
    target_id: str = "ubuntu-22.04-x86_64",
    release_id: str = "easyqc-1.0.0",
    manifest_sha256: str = "b" * 64,
    command_version: str = "platform-v1",
    remote: bool = False,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "matrix_row_id": row_id,
        "source": {"revision": "a" * 40, "dirty": False},
        "release": {
            "release_id": release_id,
            "manifest_sha256": manifest_sha256,
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
            "runner_label": row_id,
            "runner_image": "fixture-only",
        },
        "display": {
            "session": (
                "offscreen"
                if row_id.startswith("ci-")
                else ("rdp" if remote else "xcb")
            ),
            "scale_percent": 100,
            "logical_viewport": "1280x720",
            "color_scheme": "system",
            "remote": remote,
        },
        "hardware": {
            "cpu": "synthetic",
            "ram_bytes": 16 * GIB,
            "gpu_or_renderer": "synthetic",
        },
        "command_or_checklist_version": command_version,
    }


def _request(**overrides: object) -> VerificationRequestV1:
    return VerificationRequestV1.from_json_object(_request_object(**overrides))


def _raw_result(
    request: VerificationRequestV1,
    *,
    report_path: str = "reports/core-suite.json",
    status: str = "PASS",
    reason_code: str | None = None,
    completed_at: str = "2026-07-22T06:00:02Z",
) -> RawVerificationResultV1:
    passing = status == "PASS"
    return RawVerificationResultV1.from_json_object(
        {
            "schema_version": 1,
            "request": request.to_json_object(),
            "started_at_utc": "2026-07-22T06:00:00Z",
            "completed_at_utc": completed_at,
            "status": status,
            "reason_code": reason_code,
            "tests": [
                {
                    "name": "core-suite",
                    "status": "PASS" if passing else "FAIL",
                    "duration": 2.0,
                    "report_path": report_path,
                }
            ],
            "metrics": {
                "query_p50_ms": None,
                "query_p95_ms": None,
                "peak_rss_bytes": None,
                "max_event_loop_delay_ms": None,
            },
            "artifacts": [
                {"path": report_path, "classification": "test-report"}
            ],
            "limitations": [] if passing else ["synthetic failed attempt"],
        }
    )


def _native_checklist(
    *,
    changed_item: str | None = None,
    changed_status: str = "PASS",
    reason_code: str | None = None,
    notes: str | None = None,
    version: str = "native-ui-v1",
) -> NativeUiChecklistV1:
    items = []
    for item_id in REQUIRED_NATIVE_UI_ITEM_IDS:
        if item_id == changed_item:
            status = changed_status
            item_reason = reason_code
            item_notes = notes
        elif item_id == "UI-REMOTE-01":
            status = "NOT_RUN"
            item_reason = "session-not-available"
            item_notes = "Remote behavior was not claimed for this local session"
        else:
            status = "PASS"
            item_reason = None
            item_notes = None
        items.append(
            {
                "item_id": item_id,
                "status": status,
                "reason_code": item_reason,
                "notes": item_notes,
            }
        )
    return NativeUiChecklistV1.from_json_object(
        {
            "schema_version": 1,
            "checklist_version": version,
            "operator_id": "operator-01",
            "started_at_utc": "2026-07-22T06:00:00Z",
            "completed_at_utc": "2026-07-22T06:05:00Z",
            "items": items,
        }
    )


def _gate_request(request: VerificationRequestV1) -> ReleaseGateRequestV1:
    return ReleaseGateRequestV1.from_json_object(
        {
            "schema_version": 1,
            "source": request.source.to_json_object(),
            "release_id": request.release.release_id,
            "manifest_sha256": request.release.manifest_sha256,
            "runtime": request.runtime.to_json_object(),
            "command_or_checklist_version": request.command_or_checklist_version,
        }
    )


def _run(
    request: VerificationRequestV1,
    *,
    run_id: str,
    completed_at: str,
    status: str = "PASS",
    reason_code: str | None = None,
) -> VerificationRunV1:
    passing = status == "PASS"
    report_sha256 = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return VerificationRunV1.from_json_object(
        {
            "schema_version": 1,
            "run_id": run_id,
            "matrix_row_id": request.matrix_row_id,
            "status": status,
            "reason_code": reason_code,
            "started_at_utc": "2026-07-22T06:00:00Z",
            "completed_at_utc": completed_at,
            "source": request.source.to_json_object(),
            "release": request.release.to_json_object(),
            "runtime": request.runtime.to_json_object(),
            "platform": request.platform.to_json_object(),
            "display": request.display.to_json_object(),
            "hardware": request.hardware.to_json_object(),
            "command_or_checklist_version": request.command_or_checklist_version,
            "tests": [
                {
                    "name": "suite",
                    "status": "PASS" if passing else "FAIL",
                    "duration": 1.0,
                    "report_path": f"reports/{run_id}.json",
                    "report_sha256": report_sha256,
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
                    "path": f"reports/{run_id}.json",
                    "sha256": report_sha256,
                    "classification": "test-report",
                }
            ],
            "limitations": [] if passing else ["synthetic failure"],
        }
    )


def test_normalizer_hashes_exact_regular_evidence_and_preserves_identity(
    tmp_path: Path,
) -> None:
    report = tmp_path / "reports" / "core-suite.json"
    report.parent.mkdir()
    report.write_bytes(b'{"passed":true}\n')
    request = _request()
    raw = _raw_result(request)

    run = normalize_verification_run(raw, tmp_path)

    expected_hash = hashlib.sha256(report.read_bytes()).hexdigest()
    assert run.run_id == request.run_id
    assert run.matrix_row_id == request.matrix_row_id
    assert run.source == request.source
    assert run.release == request.release
    assert run.runtime == request.runtime
    assert run.platform == request.platform
    assert run.display == request.display
    assert run.hardware == request.hardware
    assert run.tests[0].report_sha256 == expected_hash
    assert run.artifacts[0].sha256 == expected_hash
    assert VerificationRunV1.from_canonical_bytes(run.canonical_bytes) == run


def test_normalizer_rejects_missing_symlinked_or_non_directory_evidence_root(
    tmp_path: Path,
) -> None:
    request = _request()
    raw = _raw_result(request)

    with pytest.raises(PlatformVerificationError, match="missing.*evidence"):
        normalize_verification_run(raw, tmp_path)

    outside = tmp_path.parent / f"{tmp_path.name}-outside.json"
    outside.write_bytes(b"outside")
    report = tmp_path / "reports" / "core-suite.json"
    report.parent.mkdir()
    report.symlink_to(outside)
    with pytest.raises(PlatformVerificationError, match="regular non-symlink"):
        normalize_verification_run(raw, tmp_path)

    with pytest.raises(PlatformVerificationError, match="evidence root"):
        normalize_verification_run(raw, outside)


def test_native_recorder_derives_pass_fail_and_not_run_without_fake_pass() -> None:
    request = _request(
        run_id="run-native-001",
        row_id="native-ubuntu-22.04-x86_64",
        target_id="ubuntu-22.04-x86_64",
        command_version="native-ui-v1",
    )

    passed = record_native_check(
        request,
        _native_checklist(),
        "reports/native-ui-checklist.json",
    )
    assert passed.status == "PASS"
    assert passed.reason_code is None
    assert passed.tests[0].status == "PASS"

    failed = record_native_check(
        request,
        _native_checklist(
            changed_item="UI-DIALOG-01",
            changed_status="FAIL",
            notes="Apply button is unreachable",
        ),
        "reports/native-ui-checklist.json",
    )
    assert failed.status == "FAIL"
    assert failed.reason_code == "native-checklist-failure"
    assert failed.tests[0].status == "FAIL"
    assert "UI-DIALOG-01" in failed.limitations[0]

    not_run = record_native_check(
        request,
        _native_checklist(
            changed_item="UI-DPI-01",
            changed_status="NOT_RUN",
            reason_code="session-not-available",
            notes="No remote session was available",
        ),
        "reports/native-ui-checklist.json",
    )
    assert not_run.status == "NOT_RUN"
    assert not_run.reason_code == "session-not-available"
    assert not_run.tests[0].status == "SKIP"

    ci_request = _request(command_version="native-ui-v1")
    with pytest.raises(PlatformVerificationError, match="native matrix row"):
        record_native_check(
            ci_request,
            _native_checklist(),
            "reports/native-ui-checklist.json",
        )

    with pytest.raises(PlatformVerificationError, match="checklist version"):
        record_native_check(
            request,
            _native_checklist(version="native-ui-v2"),
            "reports/native-ui-checklist.json",
        )


def test_native_remote_item_is_conditional_and_bound_to_display_identity() -> None:
    local_request = _request(
        run_id="run-native-local",
        row_id="native-ubuntu-22.04-x86_64",
        target_id="ubuntu-22.04-x86_64",
        command_version="native-ui-v1",
    )
    local = record_native_check(
        local_request,
        _native_checklist(),
        "reports/native-ui-checklist.json",
    )
    assert local.status == "PASS"
    assert local.reason_code is None
    assert "UI-REMOTE-01" in local.limitations[-1]

    false_remote_claim = _native_checklist(
        changed_item="UI-REMOTE-01",
        changed_status="PASS",
    )
    with pytest.raises(PlatformVerificationError, match="non-remote.*NOT_RUN"):
        record_native_check(
            local_request,
            false_remote_claim,
            "reports/native-ui-checklist.json",
        )

    remote_request = _request(
        run_id="run-native-remote",
        row_id="native-ubuntu-22.04-x86_64",
        target_id="ubuntu-22.04-x86_64",
        command_version="native-ui-v1",
        remote=True,
    )
    remote_not_run = record_native_check(
        remote_request,
        _native_checklist(),
        "reports/native-ui-checklist.json",
    )
    assert remote_not_run.status == "NOT_RUN"
    assert remote_not_run.reason_code == "session-not-available"


def test_latest_selection_uses_newest_exact_attempt_and_retains_history() -> None:
    request = _request()
    gate = _gate_request(request)
    older_failure = _run(
        request,
        run_id="attempt-old-fail",
        completed_at="2026-07-22T06:01:00Z",
        status="FAIL",
        reason_code="test-failure",
    )
    newer_pass = _run(
        request,
        run_id="attempt-new-pass",
        completed_at="2026-07-22T06:02:00Z",
    )
    wrong_request = _request(
        run_id="wrong-release-request",
        release_id="easyqc-2.0.0",
        manifest_sha256="c" * 64,
    )
    wrong_release = _run(
        wrong_request,
        run_id="attempt-wrong-release",
        completed_at="2026-07-22T06:03:00Z",
    )
    attempts = (newer_pass, wrong_release, older_failure)
    snapshots = tuple(deepcopy(run.to_json_object()) for run in attempts)

    selected = select_latest_accepted(
        gate,
        attempts,
        request.matrix_row_id,
    )

    assert selected == newer_pass
    assert tuple(run.to_json_object() for run in attempts) == snapshots

    newest_failure = _run(
        request,
        run_id="attempt-newest-fail",
        completed_at="2026-07-22T06:04:00Z",
        status="FAIL",
        reason_code="test-failure",
    )
    assert (
        select_latest_accepted(
            gate,
            (*attempts, newest_failure),
            request.matrix_row_id,
        )
        == newest_failure
    )


def test_latest_selection_fails_closed_on_duplicate_or_equal_latest_attempts() -> None:
    request = _request()
    gate = _gate_request(request)
    first = _run(
        request,
        run_id="attempt-a",
        completed_at="2026-07-22T06:02:00Z",
    )
    second = _run(
        request,
        run_id="attempt-b",
        completed_at="2026-07-22T06:02:00Z",
    )

    with pytest.raises(PlatformVerificationError, match="latest.*ambiguous"):
        select_latest_accepted(gate, (first, second), request.matrix_row_id)

    with pytest.raises(PlatformVerificationError, match="duplicate run_id"):
        select_latest_accepted(gate, (first, first), request.matrix_row_id)

    with pytest.raises(PlatformVerificationError, match="matrix row"):
        select_latest_accepted(gate, (first,), "native-unknown")

    assert select_latest_accepted(
        gate,
        (),
        request.matrix_row_id,
    ) is None


def test_normalizer_requires_typed_inputs() -> None:
    with pytest.raises(PlatformVerificationError, match="RawVerificationResultV1"):
        normalize_verification_run(object(), Path("."))  # type: ignore[arg-type]
    with pytest.raises(PlatformVerificationError, match="pathlib.Path"):
        normalize_verification_run(  # type: ignore[arg-type]
            _raw_result(_request()),
            ".",
        )


def test_authority_and_attempt_creation_reject_symlinked_parent(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    alias = tmp_path / "parent-alias"
    alias.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(PlatformVerificationError, match="real directory"):
        create_verification_attempt_root(alias / "attempt")
    with pytest.raises(PlatformVerificationError, match="real directory"):
        write_new_verification_authority(alias / "run.json", b"preserve")

    assert tuple(real_parent.iterdir()) == ()
