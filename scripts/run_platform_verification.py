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
import os
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
_DIAGNOSTIC_TAIL_BYTES = 64 * 1024
_GITHUB_ANNOTATION_MESSAGE_CHARS = 3500


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


def _diagnostic_tail(
    handle: BinaryIO,
    *,
    size: int,
    sha256: str,
) -> bytes:
    if size <= _DIAGNOSTIC_TAIL_BYTES:
        handle.seek(0)
        return handle.read()
    marker = (
        f"[truncated to final {_DIAGNOSTIC_TAIL_BYTES} bytes; "
        f"full_size={size}; full_sha256={sha256}]\n"
    ).encode("ascii")
    retained_bytes = _DIAGNOSTIC_TAIL_BYTES - len(marker)
    handle.seek(size - retained_bytes)
    return marker + handle.read(retained_bytes)


def _diagnostic_report_path(report_path: str, stream_name: str) -> str:
    report = PurePosixPath(report_path)
    return (report.parent / f"{report.stem}.{stream_name}.log").as_posix()


def _github_command_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _github_failure_annotation_payloads(
    data: bytes,
) -> tuple[tuple[str, str], ...]:
    text = data.decode("utf-8", errors="replace")
    identifiers: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if not line.startswith("FAILED "):
            continue
        identifier, separator, _summary = line[len("FAILED ") :].partition(" - ")
        identifier = identifier.strip()
        if not separator or not identifier or identifier in seen:
            continue
        seen.add(identifier)
        identifiers.append(identifier)

    inventory_chunks: list[str] = []
    current: list[str] = []
    current_chars = 0
    for identifier in identifiers:
        separator_chars = 1 if current else 0
        if (
            current
            and current_chars + separator_chars + len(identifier)
            > _GITHUB_ANNOTATION_MESSAGE_CHARS
        ):
            inventory_chunks.append("\n".join(current))
            current = []
            current_chars = 0
            separator_chars = 0
        current.append(identifier)
        current_chars += separator_chars + len(identifier)
    if current:
        inventory_chunks.append("\n".join(current))

    payloads = [
        (f"failed tests {index}/{len(inventory_chunks)}", message)
        for index, message in enumerate(inventory_chunks, start=1)
    ]
    if len(text) > _GITHUB_ANNOTATION_MESSAGE_CHARS:
        marker = "[truncated]\n"
        text = marker + text[
            -(_GITHUB_ANNOTATION_MESSAGE_CHARS - len(marker)) :
        ]
    payloads.append(("diagnostic tail", text))
    return tuple(payloads)


def _emit_github_failure_annotation(
    check_name: str,
    stream_name: str,
    data: bytes,
) -> None:
    if os.environ.get("GITHUB_ACTIONS", "").lower() != "true":
        return
    for kind, message in _github_failure_annotation_payloads(data):
        title = f"EasyQC {check_name} {stream_name} {kind}"
        print(
            f"::error title={_github_command_escape(title)}::"
            f"{_github_command_escape(message)}",
            file=sys.stderr,
        )


def _retain_and_surface_failure_stream(
    *,
    check_name: str,
    stream_name: str,
    handle: BinaryIO,
    size: int,
    sha256: str,
    root: Path,
    report_path: str,
) -> RawEvidenceArtifactV1 | None:
    if size == 0:
        return None
    data = _diagnostic_tail(handle, size=size, sha256=sha256)
    relative_path = _diagnostic_report_path(report_path, stream_name)
    destination = _report_destination(root, relative_path)
    write_new_verification_authority(destination, data)
    print(
        f"--- {check_name} {stream_name} diagnostic tail ---",
        file=sys.stderr,
    )
    print(data.decode("utf-8", errors="replace"), file=sys.stderr, end="")
    if not data.endswith(b"\n"):
        print(file=sys.stderr)
    _emit_github_failure_annotation(check_name, stream_name, data)
    return RawEvidenceArtifactV1(path=relative_path, classification="log")


def _report_destination(root: Path, relative_path: str) -> Path:
    destination = root.joinpath(*PurePosixPath(relative_path).parts)
    if destination.exists() or destination.is_symlink():
        raise PlatformVerificationError(
            f"check report path already exists: {relative_path}"
        )
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise PlatformVerificationError(
            f"check report parent is invalid: {relative_path}"
        )
    return destination


def _prepare_report_directories(
    root: Path,
    plan: AutomatedCheckPlanV1,
) -> None:
    for check in plan.checks:
        cursor = root
        for part in PurePosixPath(check.report_path).parts[:-1]:
            cursor = cursor / part
            if cursor.exists() or cursor.is_symlink():
                if cursor.is_symlink() or not cursor.is_dir():
                    raise PlatformVerificationError(
                        f"check report parent is invalid: {check.report_path}"
                    )
                continue
            try:
                cursor.mkdir()
            except FileExistsError:
                if cursor.is_symlink() or not cursor.is_dir():
                    raise PlatformVerificationError(
                        f"check report parent is invalid: {check.report_path}"
                    )


def _execute_check(
    check: AutomatedCheckV1,
    root: Path,
) -> tuple[RawTestEvidenceV1, str, tuple[RawEvidenceArtifactV1, ...]]:
    name = check.name
    started_at = _utc_now()
    started = time.perf_counter()
    returncode: int | None = None
    timed_out = False
    failure_kind: str | None = None
    with tempfile.TemporaryFile(dir=root) as stdout, tempfile.TemporaryFile(
        dir=root
    ) as stderr:
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
        diagnostic_artifacts: list[RawEvidenceArtifactV1] = []
        if not passed:
            for stream_name, handle, size, sha256 in (
                ("stdout", stdout, stdout_bytes, stdout_sha256),
                ("stderr", stderr, stderr_bytes, stderr_sha256),
            ):
                artifact = _retain_and_surface_failure_stream(
                    check_name=name,
                    stream_name=stream_name,
                    handle=handle,
                    size=size,
                    sha256=sha256,
                    root=root,
                    report_path=check.report_path,
                )
                if artifact is not None:
                    diagnostic_artifacts.append(artifact)
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
        tuple(diagnostic_artifacts),
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
    _prepare_report_directories(root, plan)
    started_at = _utc_now()
    tests: list[RawTestEvidenceV1] = []
    limitations: list[str] = []
    diagnostic_artifacts: list[RawEvidenceArtifactV1] = []
    for check in plan.checks:
        test, limitation, check_diagnostics = _execute_check(check, root)
        tests.append(test)
        diagnostic_artifacts.extend(check_diagnostics)
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
        )
        + tuple(diagnostic_artifacts),
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
