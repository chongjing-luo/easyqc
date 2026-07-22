#!/usr/bin/env python3
"""Execute one automated CI verification request and retain one attempt.

Input: one canonical CI VerificationRequestV1, one AutomatedCheckPlanV1 and one
absent absolute attempt root. Output: one raw bundle and one canonical run.
Side effects: runs bounded argv with shell=False and writes only below the new
attempt root. Native interaction and CI workflow orchestration are separate.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import time
from typing import BinaryIO, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.platform_verification import (  # noqa: E402
    PlatformVerificationError,
    create_verification_attempt_root,
    normalize_verification_run,
    write_new_verification_authority,
)
from models.platform_verification import (  # noqa: E402
    AutomatedCheckPlanV1,
    AutomatedCheckV1,
    MetricsV1,
    PlatformVerificationContractError,
    RawEvidenceArtifactV1,
    RawTestEvidenceV1,
    RawVerificationResultV1,
    VerificationRequestV1,
    canonical_json_bytes,
)


_RESERVED_PATHS = {
    "raw-verification-result.json",
    "verification-run.json",
}


def _read_authority(path: Path, parser: object, label: str) -> object:
    if path.is_symlink() or not path.is_file():
        raise PlatformVerificationError(
            f"{label} must be a regular non-symlink file"
        )
    parse = getattr(parser, "from_canonical_bytes")
    return parse(path.read_bytes())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _stream_identity(handle: BinaryIO) -> tuple[int, str]:
    handle.seek(0)
    size = 0
    digest = hashlib.sha256()
    while chunk := handle.read(1024 * 1024):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def _report_destination(root: Path, relative_path: str) -> Path:
    destination = root.joinpath(*PurePosixPath(relative_path).parts)
    if destination.exists() or destination.is_symlink():
        raise PlatformVerificationError(
            f"check report path already exists: {relative_path}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise PlatformVerificationError(
            f"check report parent is invalid: {relative_path}"
        )
    return destination


def _execute_check(
    check: AutomatedCheckV1,
    root: Path,
) -> tuple[RawTestEvidenceV1, str]:
    name = check.name
    started_at = _utc_now()
    started = time.perf_counter()
    returncode: int | None = None
    timed_out = False
    failure_kind: str | None = None
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            completed = subprocess.run(
                list(check.argv),
                cwd=PROJECT_ROOT,
                stdout=stdout,
                stderr=stderr,
                timeout=check.timeout_seconds,
                check=False,
                shell=False,
            )
            returncode = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            failure_kind = "timeout"
        except OSError as exc:
            failure_kind = "spawn-error"
            stderr.write(str(exc).encode("utf-8", errors="replace"))
            stderr.flush()
        duration = max(0.0, time.perf_counter() - started)
        completed_at = _utc_now()
        passed = returncode == 0 and not timed_out and failure_kind is None
        stdout_bytes, stdout_sha256 = _stream_identity(stdout)
        stderr_bytes, stderr_sha256 = _stream_identity(stderr)
        report = {
            "schema_version": 1,
            "check_name": name,
            "argv": list(check.argv),
            "started_at_utc": started_at,
            "completed_at_utc": completed_at,
            "duration_seconds": duration,
            "status": "PASS" if passed else "FAIL",
            "returncode": returncode,
            "timed_out": timed_out,
            "failure_kind": failure_kind,
            "stdout_bytes": stdout_bytes,
            "stdout_sha256": stdout_sha256,
            "stderr_bytes": stderr_bytes,
            "stderr_sha256": stderr_sha256,
        }
    destination = _report_destination(root, check.report_path)
    write_new_verification_authority(destination, canonical_json_bytes(report))
    limitation = ""
    if timed_out:
        limitation = f"check {name} timed out"
    elif failure_kind is not None:
        limitation = f"check {name} could not start"
    elif returncode != 0:
        limitation = f"check {name} exited with code {returncode}"
    return (
        RawTestEvidenceV1(
            name=name,
            status="PASS" if passed else "FAIL",
            duration=duration,
            report_path=check.report_path,
        ),
        limitation,
    )


def run_automated_request(
    request: VerificationRequestV1,
    plan: AutomatedCheckPlanV1,
    attempt_root: Path,
) -> RawVerificationResultV1:
    """Execute one CI request and persist its raw reports and authorities."""

    if request.evidence_class != "ci":
        raise PlatformVerificationError(
            "automated runner cannot execute a native matrix row"
        )
    if plan.command_version != request.command_or_checklist_version:
        raise PlatformVerificationError(
            "automated check command version does not match the request"
        )
    if any(check.report_path in _RESERVED_PATHS for check in plan.checks):
        raise PlatformVerificationError(
            "automated check report_path collides with reserved authority"
        )
    root = create_verification_attempt_root(attempt_root)
    started_at = _utc_now()
    tests: list[RawTestEvidenceV1] = []
    limitations: list[str] = []
    for check in plan.checks:
        test, limitation = _execute_check(check, root)
        tests.append(test)
        if limitation:
            limitations.append(limitation)
    completed_at = _utc_now()
    passed = all(test.status == "PASS" for test in tests)
    raw = RawVerificationResultV1(
        schema_version=1,
        request=request,
        started_at_utc=started_at,
        completed_at_utc=completed_at,
        status="PASS" if passed else "FAIL",
        reason_code=None if passed else "test-failure",
        tests=tuple(tests),
        metrics=MetricsV1(
            query_p50_ms=None,
            query_p95_ms=None,
            peak_rss_bytes=None,
            max_event_loop_delay_ms=None,
        ),
        artifacts=tuple(
            RawEvidenceArtifactV1(
                path=check.report_path,
                classification=check.classification,
            )
            for check in plan.checks
        ),
        limitations=tuple(limitations),
    )
    raw_path = root / "raw-verification-result.json"
    write_new_verification_authority(raw_path, raw.canonical_bytes)
    run = normalize_verification_run(raw, root)
    write_new_verification_authority(
        root / "verification-run.json",
        run.canonical_bytes,
    )
    return raw


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--check-plan", type=Path, required=True)
    parser.add_argument("--attempt-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        request = _read_authority(
            args.request,
            VerificationRequestV1,
            "verification request",
        )
        plan = _read_authority(
            args.check_plan,
            AutomatedCheckPlanV1,
            "automated check plan",
        )
        if not isinstance(request, VerificationRequestV1):
            raise PlatformVerificationError("request parser returned wrong type")
        if not isinstance(plan, AutomatedCheckPlanV1):
            raise PlatformVerificationError("plan parser returned wrong type")
        raw = run_automated_request(request, plan, args.attempt_root)
    except (
        OSError,
        PlatformVerificationContractError,
        PlatformVerificationError,
    ) as exc:
        print(f"ERROR platform verification: {exc}", file=sys.stderr)
        return 2
    message = (
        f"{raw.status} run_id={request.run_id} row={request.matrix_row_id} "
        f"output={args.attempt_root.resolve()}"
    )
    if raw.status == "PASS":
        print(message, flush=True)
        return 0
    print(message, file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
