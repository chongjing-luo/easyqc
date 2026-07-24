from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from models.platform_verification import (
    AutomatedCheckPlanV1,
    NativeUiChecklistV1,
    REQUIRED_NATIVE_UI_ITEM_IDS,
    RawVerificationResultV1,
    VerificationRequestV1,
    VerificationRunV1,
    canonical_json_bytes,
)


GIB = 1024 * 1024 * 1024


def _request(
    *,
    run_id: str,
    row_id: str = "ci-ubuntu-22.04-x86_64",
    target_id: str = "ubuntu-22.04-x86_64",
    command_version: str = "platform-v1",
) -> VerificationRequestV1:
    return VerificationRequestV1.from_json_object(
        {
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
                "runner_label": row_id,
                "runner_image": "fixture-only",
            },
            "display": {
                "session": "offscreen" if row_id.startswith("ci-") else "xcb",
                "scale_percent": 100,
                "logical_viewport": "1280x720",
                "color_scheme": "system",
                "remote": False,
            },
            "hardware": {
                "cpu": "synthetic",
                "ram_bytes": 16 * GIB,
                "gpu_or_renderer": "synthetic",
            },
            "command_or_checklist_version": command_version,
        }
    )


def _plan(exit_code: int) -> AutomatedCheckPlanV1:
    return AutomatedCheckPlanV1.from_json_object(
        {
            "schema_version": 1,
            "command_version": "platform-v1",
            "checks": [
                {
                    "name": "synthetic-suite",
                    "argv": [
                        sys.executable,
                        "-c",
                        f"raise SystemExit({exit_code})",
                    ],
                    "timeout_seconds": 30.0,
                    "report_path": "reports/synthetic-suite.json",
                    "classification": "test-report",
                }
            ],
        }
    )


def _checklist() -> NativeUiChecklistV1:
    return NativeUiChecklistV1.from_json_object(
        {
            "schema_version": 1,
            "checklist_version": "native-ui-v1",
            "operator_id": "operator-01",
            "started_at_utc": "2026-07-22T06:00:00Z",
            "completed_at_utc": "2026-07-22T06:05:00Z",
            "items": [
                {
                    "item_id": item_id,
                    "status": "NOT_RUN" if item_id == "UI-REMOTE-01" else "PASS",
                    "reason_code": (
                        "session-not-available"
                        if item_id == "UI-REMOTE-01"
                        else None
                    ),
                    "notes": (
                        "Remote behavior was not claimed for this local session"
                        if item_id == "UI-REMOTE-01"
                        else None
                    ),
                }
                for item_id in REQUIRED_NATIVE_UI_ITEM_IDS
            ],
        }
    )


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _run_script(
    easyqc_root: Path,
    tmp_path: Path,
    name: str,
    *arguments: str,
    github_actions: bool = False,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["EASYQC_LOG_DIR"] = str(tmp_path / "logs")
    environment["HOME"] = str(tmp_path / "fake-home")
    if github_actions:
        environment["GITHUB_ACTIONS"] = "true"
    else:
        environment.pop("GITHUB_ACTIONS", None)
    return subprocess.run(
        [sys.executable, str(easyqc_root / "scripts" / name), *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=60,
        check=False,
    )


def _raw_result(request: VerificationRequestV1) -> RawVerificationResultV1:
    return RawVerificationResultV1.from_json_object(
        {
            "schema_version": 1,
            "request": request.to_json_object(),
            "started_at_utc": "2026-07-22T06:00:00Z",
            "completed_at_utc": "2026-07-22T06:00:01Z",
            "status": "PASS",
            "reason_code": None,
            "tests": [
                {
                    "name": "synthetic-suite",
                    "status": "PASS",
                    "duration": 1.0,
                    "report_path": "reports/synthetic-suite.json",
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
                    "path": "reports/synthetic-suite.json",
                    "classification": "test-report",
                }
            ],
            "limitations": [],
        }
    )


def test_automated_runner_writes_one_canonical_pass_run(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    request = _request(run_id="runner-pass-001")
    request_path = tmp_path / "request.json"
    plan_path = tmp_path / "plan.json"
    attempt_root = tmp_path / "attempt-pass"
    _write(request_path, request.canonical_bytes)
    _write(plan_path, _plan(0).canonical_bytes)

    completed = _run_script(
        easyqc_root,
        tmp_path,
        "run_platform_verification.py",
        "--request",
        str(request_path),
        "--check-plan",
        str(plan_path),
        "--attempt-root",
        str(attempt_root),
    )

    assert completed.returncode == 0, completed.stderr
    assert "PASS" in completed.stdout
    raw = RawVerificationResultV1.from_canonical_bytes(
        (attempt_root / "raw-verification-result.json").read_bytes()
    )
    run = VerificationRunV1.from_canonical_bytes(
        (attempt_root / "verification-run.json").read_bytes()
    )
    report_path = attempt_root / "reports" / "synthetic-suite.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()

    assert raw.request == request
    assert run.run_id == request.run_id
    assert run.status == "PASS"
    assert run.source == request.source
    assert run.runtime == request.runtime
    assert run.platform == request.platform
    assert run.tests[0].report_sha256 == report_hash
    assert run.artifacts[0].sha256 == report_hash
    assert report["check_name"] == "synthetic-suite"
    assert report["returncode"] == 0
    assert report["status"] == "PASS"
    assert "stdout" not in report and "stderr" not in report


def test_automated_runner_retains_failed_attempt_and_rejects_overwrite(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    request = _request(run_id="runner-fail-001")
    request_path = tmp_path / "request-fail.json"
    plan_path = tmp_path / "plan-fail.json"
    attempt_root = tmp_path / "attempt-fail"
    _write(request_path, request.canonical_bytes)
    _write(plan_path, _plan(7).canonical_bytes)
    arguments = (
        "--request",
        str(request_path),
        "--check-plan",
        str(plan_path),
        "--attempt-root",
        str(attempt_root),
    )

    completed = _run_script(
        easyqc_root,
        tmp_path,
        "run_platform_verification.py",
        *arguments,
    )

    assert completed.returncode == 1, completed.stderr
    assert "::error" not in completed.stderr
    run_path = attempt_root / "verification-run.json"
    run = VerificationRunV1.from_canonical_bytes(run_path.read_bytes())
    assert run.status == "FAIL"
    assert run.reason_code == "test-failure"
    assert run.limitations
    report = json.loads(
        (attempt_root / "reports" / "synthetic-suite.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["returncode"] == 7
    before = {
        path.relative_to(attempt_root).as_posix(): path.read_bytes()
        for path in attempt_root.rglob("*")
        if path.is_file()
    }

    collision = _run_script(
        easyqc_root,
        tmp_path,
        "run_platform_verification.py",
        *arguments,
    )

    assert collision.returncode == 2
    assert "attempt root" in collision.stderr
    after = {
        path.relative_to(attempt_root).as_posix(): path.read_bytes()
        for path in attempt_root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_automated_runner_retains_and_surfaces_bounded_failure_output(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    request = _request(run_id="runner-diagnostics-001")
    request_path = tmp_path / "request-diagnostics.json"
    plan_path = tmp_path / "plan-diagnostics.json"
    attempt_root = tmp_path / "attempt-diagnostics"
    plan = AutomatedCheckPlanV1.from_json_object(
        {
            "schema_version": 1,
            "command_version": "platform-v1",
            "checks": [
                {
                    "name": "diagnostic-suite",
                    "argv": [
                        sys.executable,
                        "-c",
                        (
                            "import sys;"
                            "sys.stdout.write('o'*70000+'VISIBLE%STDOUT-MARKER\\n');"
                            "sys.stderr.write('e'*70000+'VISIBLE%STDERR-MARKER\\n');"
                            "raise SystemExit(9)"
                        ),
                    ],
                    "timeout_seconds": 30.0,
                    "report_path": "reports/diagnostic-suite.json",
                    "classification": "test-report",
                }
            ],
        }
    )
    _write(request_path, request.canonical_bytes)
    _write(plan_path, plan.canonical_bytes)

    completed = _run_script(
        easyqc_root,
        tmp_path,
        "run_platform_verification.py",
        "--request",
        str(request_path),
        "--check-plan",
        str(plan_path),
        "--attempt-root",
        str(attempt_root),
        github_actions=True,
    )

    assert completed.returncode == 1
    assert "VISIBLE%STDOUT-MARKER" in completed.stderr
    assert "VISIBLE%STDERR-MARKER" in completed.stderr
    annotations = [
        line for line in completed.stderr.splitlines() if line.startswith("::error ")
    ]
    assert len(annotations) == 2
    assert any(
        "title=EasyQC diagnostic-suite stdout failed::" in line
        and "VISIBLE%25STDOUT-MARKER%0A" in line
        for line in annotations
    )
    assert any(
        "title=EasyQC diagnostic-suite stderr failed::" in line
        and "VISIBLE%25STDERR-MARKER%0A" in line
        for line in annotations
    )
    assert all(len(line) <= 6200 for line in annotations)
    stdout_path = attempt_root / "reports" / "diagnostic-suite.stdout.log"
    stderr_path = attempt_root / "reports" / "diagnostic-suite.stderr.log"
    assert stdout_path.stat().st_size == 64 * 1024
    assert stderr_path.stat().st_size == 64 * 1024
    assert stdout_path.read_text(encoding="utf-8").startswith("[truncated")
    assert stderr_path.read_text(encoding="utf-8").startswith("[truncated")
    assert stdout_path.read_text(encoding="utf-8").endswith(
        "VISIBLE%STDOUT-MARKER\n"
    )
    assert stderr_path.read_text(encoding="utf-8").endswith(
        "VISIBLE%STDERR-MARKER\n"
    )
    raw = RawVerificationResultV1.from_canonical_bytes(
        (attempt_root / "raw-verification-result.json").read_bytes()
    )
    assert {
        (artifact.path, artifact.classification) for artifact in raw.artifacts
    } >= {
        ("reports/diagnostic-suite.stdout.log", "log"),
        ("reports/diagnostic-suite.stderr.log", "log"),
    }


def test_automated_runner_hashes_large_child_streams_without_embedding_them(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    request = _request(run_id="runner-streams-001")
    request_path = tmp_path / "request-streams.json"
    plan_path = tmp_path / "plan-streams.json"
    attempt_root = tmp_path / "attempt-streams"
    stdout = b"o" * (2 * 1024 * 1024)
    stderr = b"e" * (1024 * 1024 + 1)
    plan = AutomatedCheckPlanV1.from_json_object(
        {
            "schema_version": 1,
            "command_version": "platform-v1",
            "checks": [
                {
                    "name": "bounded-stream-suite",
                    "argv": [
                        sys.executable,
                        "-c",
                        (
                            "import sys;"
                            f"sys.stdout.buffer.write(b'o'*{len(stdout)});"
                            f"sys.stderr.buffer.write(b'e'*{len(stderr)})"
                        ),
                    ],
                    "timeout_seconds": 30.0,
                    "report_path": "reports/bounded-stream-suite.json",
                    "classification": "test-report",
                }
            ],
        }
    )
    _write(request_path, request.canonical_bytes)
    _write(plan_path, plan.canonical_bytes)

    completed = _run_script(
        easyqc_root,
        tmp_path,
        "run_platform_verification.py",
        "--request",
        str(request_path),
        "--check-plan",
        str(plan_path),
        "--attempt-root",
        str(attempt_root),
    )

    assert completed.returncode == 0, completed.stderr
    report_path = attempt_root / "reports" / "bounded-stream-suite.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["stdout_bytes"] == len(stdout)
    assert report["stdout_sha256"] == hashlib.sha256(stdout).hexdigest()
    assert report["stderr_bytes"] == len(stderr)
    assert report["stderr_sha256"] == hashlib.sha256(stderr).hexdigest()
    assert report_path.stat().st_size < 4096


def test_automated_runner_retains_timeout_and_spawn_error_as_failures(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    cases = (
        (
            "timeout",
            [sys.executable, "-c", "import time; time.sleep(5)"],
            0.05,
            True,
        ),
        (
            "spawn-error",
            [str(tmp_path / "missing-executable")],
            30.0,
            False,
        ),
    )
    for name, argv, timeout_seconds, timed_out in cases:
        request = _request(run_id=f"runner-{name}-001")
        request_path = tmp_path / f"request-{name}.json"
        plan_path = tmp_path / f"plan-{name}.json"
        attempt_root = tmp_path / f"attempt-{name}"
        plan = AutomatedCheckPlanV1.from_json_object(
            {
                "schema_version": 1,
                "command_version": "platform-v1",
                "checks": [
                    {
                        "name": name,
                        "argv": argv,
                        "timeout_seconds": timeout_seconds,
                        "report_path": f"reports/{name}.json",
                        "classification": "test-report",
                    }
                ],
            }
        )
        _write(request_path, request.canonical_bytes)
        _write(plan_path, plan.canonical_bytes)

        completed = _run_script(
            easyqc_root,
            tmp_path,
            "run_platform_verification.py",
            "--request",
            str(request_path),
            "--check-plan",
            str(plan_path),
            "--attempt-root",
            str(attempt_root),
        )

        assert completed.returncode == 1, completed.stderr
        run = VerificationRunV1.from_canonical_bytes(
            (attempt_root / "verification-run.json").read_bytes()
        )
        report = json.loads(
            (attempt_root / "reports" / f"{name}.json").read_text(
                encoding="utf-8"
            )
        )
        assert run.status == "FAIL"
        assert run.reason_code == "test-failure"
        assert report["status"] == "FAIL"
        assert report["failure_kind"] == name
        assert report["timed_out"] is timed_out
        assert report["returncode"] is None


def test_automated_runner_rejects_native_row_before_creating_attempt(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    request = _request(
        run_id="runner-native-forbidden",
        row_id="native-ubuntu-22.04-x86_64",
        command_version="native-ui-v1",
    )
    request_path = tmp_path / "native-request.json"
    plan_path = tmp_path / "plan.json"
    attempt_root = tmp_path / "forbidden-attempt"
    _write(request_path, request.canonical_bytes)
    _write(plan_path, _plan(0).canonical_bytes)

    completed = _run_script(
        easyqc_root,
        tmp_path,
        "run_platform_verification.py",
        "--request",
        str(request_path),
        "--check-plan",
        str(plan_path),
        "--attempt-root",
        str(attempt_root),
    )

    assert completed.returncode == 2
    assert "native matrix row" in completed.stderr
    assert not attempt_root.exists()


def test_automated_runner_rejects_command_version_mismatch_before_attempt(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    request = _request(run_id="runner-version-mismatch")
    request_path = tmp_path / "version-request.json"
    plan_path = tmp_path / "version-plan.json"
    attempt_root = tmp_path / "version-attempt"
    plan = _plan(0).to_json_object()
    plan["command_version"] = "platform-v2"
    _write(request_path, request.canonical_bytes)
    _write(
        plan_path,
        AutomatedCheckPlanV1.from_json_object(plan).canonical_bytes,
    )

    completed = _run_script(
        easyqc_root,
        tmp_path,
        "run_platform_verification.py",
        "--request",
        str(request_path),
        "--check-plan",
        str(plan_path),
        "--attempt-root",
        str(attempt_root),
    )

    assert completed.returncode == 2
    assert "command version" in completed.stderr
    assert not attempt_root.exists()


def test_normalizer_subprocess_hashes_evidence_and_preserves_collision(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    request = _request(run_id="normalize-001")
    evidence_root = tmp_path / "evidence"
    report_path = evidence_root / "reports" / "synthetic-suite.json"
    _write(report_path, b'{"status":"PASS"}\n')
    raw_path = tmp_path / "raw.json"
    _write(raw_path, _raw_result(request).canonical_bytes)
    output_path = tmp_path / "normalized-run.json"
    arguments = (
        "--raw-result",
        str(raw_path),
        "--evidence-root",
        str(evidence_root),
        "--output",
        str(output_path),
    )

    completed = _run_script(
        easyqc_root,
        tmp_path,
        "normalize_verification_run.py",
        *arguments,
    )

    assert completed.returncode == 0, completed.stderr
    run = VerificationRunV1.from_canonical_bytes(output_path.read_bytes())
    assert run.sha256 == hashlib.sha256(output_path.read_bytes()).hexdigest()
    expected_bytes = output_path.read_bytes()

    collision = _run_script(
        easyqc_root,
        tmp_path,
        "normalize_verification_run.py",
        *arguments,
    )

    assert collision.returncode == 2
    assert "output" in collision.stderr
    assert output_path.read_bytes() == expected_bytes


def test_native_recorder_requires_complete_checklist_and_emits_hashed_run(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    request = _request(
        run_id="native-check-001",
        row_id="native-ubuntu-22.04-x86_64",
        command_version="native-ui-v1",
    )
    request_path = tmp_path / "native-request.json"
    checklist_path = tmp_path / "native-checklist.json"
    attempt_root = tmp_path / "native-attempt"
    _write(request_path, request.canonical_bytes)
    checklist = _checklist()
    _write(checklist_path, checklist.canonical_bytes)

    completed = _run_script(
        easyqc_root,
        tmp_path,
        "record_native_ui_checklist.py",
        "--request",
        str(request_path),
        "--checklist",
        str(checklist_path),
        "--attempt-root",
        str(attempt_root),
    )

    assert completed.returncode == 0, completed.stderr
    run = VerificationRunV1.from_canonical_bytes(
        (attempt_root / "verification-run.json").read_bytes()
    )
    copied = attempt_root / "reports" / "native-ui-checklist.json"
    assert run.status == "PASS"
    assert run.matrix_row_id.startswith("native-")
    assert copied.read_bytes() == checklist.canonical_bytes
    assert run.tests[0].report_sha256 == hashlib.sha256(copied.read_bytes()).hexdigest()
    assert run.artifacts[0].classification == "native-checklist"
    assert "UI-REMOTE-01" in run.limitations[-1]

    incomplete = checklist.to_json_object()
    incomplete["items"] = incomplete["items"][:-1]  # type: ignore[index]
    incomplete_path = tmp_path / "incomplete-checklist.json"
    _write(incomplete_path, canonical_json_bytes(incomplete))
    rejected_root = tmp_path / "rejected-native-attempt"
    rejected = _run_script(
        easyqc_root,
        tmp_path,
        "record_native_ui_checklist.py",
        "--request",
        str(request_path),
        "--checklist",
        str(incomplete_path),
        "--attempt-root",
        str(rejected_root),
    )
    assert rejected.returncode == 2
    assert "required" in rejected.stderr
    assert not rejected_root.exists()


def test_native_checklist_markdown_ids_exactly_match_model_contract(
    easyqc_root: Path,
) -> None:
    text = (easyqc_root / "tests" / "native_ui_checklist.md").read_text(
        encoding="utf-8"
    )
    item_ids = tuple(
        re.findall(r"^\| (UI-[A-Z0-9]+-[0-9]{2}) \|", text, re.MULTILINE)
    )

    assert item_ids == REQUIRED_NATIVE_UI_ITEM_IDS
    assert len(item_ids) == len(set(item_ids))
    assert "automated CI runner cannot close" in text
