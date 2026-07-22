"""Pure, deterministic release gate for typed platform-verification records."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
import hashlib
from itertools import islice
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile

from models.platform_verification import (
    EvidenceArtifactV1,
    MATRIX_ROW_BY_ID,
    MetricsV1,
    NativeUiChecklistV1,
    REQUIRED_MATRIX_ROWS,
    RawEvidenceArtifactV1,
    RawTestEvidenceV1,
    RawVerificationResultV1,
    ReleaseBlockerV1,
    ReleaseDecisionV1,
    ReleaseGateRequestV1,
    TestEvidenceV1,
    VerificationRequestV1,
    VerificationRunV1,
)


class PlatformVerificationError(RuntimeError):
    """Raised when release evidence is ambiguous or has the wrong identity."""


_MAX_ATTEMPTS = 4096


def normalize_verification_run(
    raw: RawVerificationResultV1,
    evidence_root: Path,
) -> VerificationRunV1:
    """Hash one contained raw evidence bundle into one immutable run.

    Input: one typed raw result and one existing real evidence-root directory.
    Output: one VerificationRunV1. Side effects: reads and hashes the referenced
    regular non-symlink files only. Remote acquisition and persistence are
    separate contracts.
    """

    if not isinstance(raw, RawVerificationResultV1):
        raise PlatformVerificationError(
            "raw verification result must be RawVerificationResultV1"
        )
    root = _validated_evidence_root(evidence_root)
    artifact_hashes = {
        artifact.path: _hash_evidence_file(root, artifact.path)
        for artifact in raw.artifacts
    }
    tests = tuple(
        TestEvidenceV1(
            name=test.name,
            status=test.status,
            duration=test.duration,
            report_path=test.report_path,
            report_sha256=artifact_hashes[test.report_path],
        )
        for test in raw.tests
    )
    artifacts = tuple(
        EvidenceArtifactV1(
            path=artifact.path,
            sha256=artifact_hashes[artifact.path],
            classification=artifact.classification,
        )
        for artifact in raw.artifacts
    )
    request = raw.request
    return VerificationRunV1(
        schema_version=1,
        run_id=request.run_id,
        matrix_row_id=request.matrix_row_id,
        status=raw.status,
        reason_code=raw.reason_code,
        started_at_utc=raw.started_at_utc,
        completed_at_utc=raw.completed_at_utc,
        source=request.source,
        release=request.release,
        runtime=request.runtime,
        platform=request.platform,
        display=request.display,
        hardware=request.hardware,
        command_or_checklist_version=request.command_or_checklist_version,
        tests=tests,
        metrics=raw.metrics,
        artifacts=artifacts,
        limitations=raw.limitations,
    )


def record_native_check(
    request: VerificationRequestV1,
    checklist: NativeUiChecklistV1,
    report_path: str,
) -> RawVerificationResultV1:
    """Project one complete native checklist into one unhashed raw result.

    Input: one native request, one exact stable-ID checklist and its contained
    report path. Output: one RawVerificationResultV1. Side effects: none. UI
    execution, evidence persistence and cryptographic signing are separate.
    """

    if not isinstance(request, VerificationRequestV1):
        raise PlatformVerificationError(
            "native request must be VerificationRequestV1"
        )
    if not isinstance(checklist, NativeUiChecklistV1):
        raise PlatformVerificationError(
            "native checklist must be NativeUiChecklistV1"
        )
    if request.evidence_class != "native":
        raise PlatformVerificationError(
            "native checklist requires a native matrix row"
        )
    if (
        checklist.checklist_version
        != request.command_or_checklist_version
    ):
        raise PlatformVerificationError(
            "native checklist version does not match the request"
        )

    failed = tuple(item for item in checklist.items if item.status == "FAIL")
    not_run = tuple(
        item for item in checklist.items if item.status == "NOT_RUN"
    )
    remote_item = next(
        item for item in checklist.items if item.item_id == "UI-REMOTE-01"
    )
    if not request.display.remote and remote_item.status != "NOT_RUN":
        raise PlatformVerificationError(
            "non-remote request must record UI-REMOTE-01 as NOT_RUN"
        )
    blocking_not_run = tuple(
        item
        for item in not_run
        if request.display.remote or item.item_id != "UI-REMOTE-01"
    )
    if failed:
        status = "FAIL"
        reason_code = "native-checklist-failure"
        test_status = "FAIL"
    elif blocking_not_run:
        status = "NOT_RUN"
        reason_code = blocking_not_run[0].reason_code
        test_status = "SKIP"
    else:
        status = "PASS"
        reason_code = None
        test_status = "PASS"
    limitations = tuple(
        f"{item.item_id}: {item.notes}"
        for item in checklist.items
        if item.status != "PASS"
    )
    duration = max(
        0.0,
        (
            _utc_value(checklist.completed_at_utc)
            - _utc_value(checklist.started_at_utc)
        ).total_seconds(),
    )
    return RawVerificationResultV1(
        schema_version=1,
        request=request,
        started_at_utc=checklist.started_at_utc,
        completed_at_utc=checklist.completed_at_utc,
        status=status,
        reason_code=reason_code,
        tests=(
            RawTestEvidenceV1(
                name="native-ui-checklist",
                status=test_status,
                duration=duration,
                report_path=report_path,
            ),
        ),
        metrics=MetricsV1(
            query_p50_ms=None,
            query_p95_ms=None,
            peak_rss_bytes=None,
            max_event_loop_delay_ms=None,
        ),
        artifacts=(
            RawEvidenceArtifactV1(
                path=report_path,
                classification="native-checklist",
            ),
        ),
        limitations=limitations,
    )


def select_latest_accepted(
    request: ReleaseGateRequestV1,
    runs: Iterable[VerificationRunV1],
    matrix_row_id: str,
) -> VerificationRunV1 | None:
    """Select the newest exact-identity attempt for one required row.

    Input: one exact gate identity, immutable attempts and one required row ID.
    Output: one newest accepted record or None. Side effects: none. Accepted
    means schema-valid and identity-matching, not PASS; a newer failure remains
    authoritative. Equal latest completion timestamps fail as ambiguous.
    """

    if not isinstance(request, ReleaseGateRequestV1):
        raise PlatformVerificationError(
            "latest selection request must be ReleaseGateRequestV1"
        )
    if matrix_row_id not in MATRIX_ROW_BY_ID:
        raise PlatformVerificationError("latest selection matrix row is unknown")
    try:
        records = tuple(islice(iter(runs), _MAX_ATTEMPTS + 1))
    except TypeError as exc:
        raise PlatformVerificationError(
            "verification attempts must be iterable"
        ) from exc
    if len(records) > _MAX_ATTEMPTS:
        raise PlatformVerificationError(
            f"latest selection accepts at most {_MAX_ATTEMPTS} attempts"
        )
    if any(not isinstance(run, VerificationRunV1) for run in records):
        raise PlatformVerificationError(
            "every verification attempt must be VerificationRunV1"
        )
    duplicate_ids = sorted(
        run_id
        for run_id, count in Counter(run.run_id for run in records).items()
        if count > 1
    )
    if duplicate_ids:
        raise PlatformVerificationError(
            f"duplicate run_id is ambiguous: {duplicate_ids[0]}"
        )
    matching = tuple(
        run
        for run in records
        if run.matrix_row_id == matrix_row_id
        and _matches_gate_identity(request, run)
    )
    if not matching:
        return None
    latest_time = max(_utc_value(run.completed_at_utc) for run in matching)
    latest = tuple(
        run
        for run in matching
        if _utc_value(run.completed_at_utc) == latest_time
    )
    if len(latest) != 1:
        raise PlatformVerificationError(
            f"latest accepted attempt is ambiguous for matrix row {matrix_row_id}"
        )
    return latest[0]


def create_verification_attempt_root(path: Path) -> Path:
    """Create one explicit absent attempt root and return its resolved path."""

    if not isinstance(path, Path):
        raise PlatformVerificationError("attempt root must be a pathlib.Path")
    if not path.is_absolute():
        raise PlatformVerificationError("attempt root must be an absolute path")
    if path.exists() or path.is_symlink():
        raise PlatformVerificationError("attempt root must not exist")
    parent = _resolve_real_directory(path.parent, "attempt root parent")
    root = parent / path.name
    try:
        root.mkdir(mode=0o700)
    except OSError as exc:
        raise PlatformVerificationError(
            f"cannot create attempt root: {exc}"
        ) from exc
    return root


def write_new_verification_authority(path: Path, data: bytes) -> None:
    """Atomically create one authority file without replacing existing bytes."""

    if not isinstance(path, Path):
        raise PlatformVerificationError("authority output must be a pathlib.Path")
    if not isinstance(data, bytes):
        raise PlatformVerificationError("authority data must be bytes")
    if path.exists() or path.is_symlink():
        raise PlatformVerificationError("authority output must not exist")
    parent = _resolve_real_directory(path.parent, "authority output parent")
    destination = parent / path.name
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp.",
        dir=parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError as exc:
            raise PlatformVerificationError(
                "authority output must not exist"
            ) from exc
        except OSError as exc:
            raise PlatformVerificationError(
                f"cannot create authority output: {exc}"
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def evaluate_release_gate(
    request: ReleaseGateRequestV1,
    runs: Iterable[VerificationRunV1],
) -> ReleaseDecisionV1:
    """Evaluate one exact release identity against the code-owned matrix.

    Input: one validated gate request and zero or more validated run records.
    Output: one immutable deterministic decision. Side effects: none. Invalid,
    duplicate or mismatched authority fails before the truth table is applied.
    Matrix selection and evidence acquisition are separate contracts.
    """

    if not isinstance(request, ReleaseGateRequestV1):
        raise PlatformVerificationError(
            "release gate request must be ReleaseGateRequestV1"
        )
    try:
        records = tuple(islice(iter(runs), len(REQUIRED_MATRIX_ROWS) + 1))
    except TypeError as exc:
        raise PlatformVerificationError("verification runs must be iterable") from exc
    if len(records) > len(REQUIRED_MATRIX_ROWS):
        raise PlatformVerificationError(
            f"release gate accepts at most {len(REQUIRED_MATRIX_ROWS)} runs"
        )
    if any(not isinstance(run, VerificationRunV1) for run in records):
        raise PlatformVerificationError(
            "every verification run must be VerificationRunV1"
        )

    _reject_duplicate_authority(records)
    for run in records:
        _validate_identity(request, run)

    by_row = {run.matrix_row_id: run for run in records}
    evaluated_run_ids = tuple(
        by_row[row.matrix_row_id].run_id
        for row in REQUIRED_MATRIX_ROWS
        if row.matrix_row_id in by_row
    )
    blockers: list[ReleaseBlockerV1] = []
    for row in REQUIRED_MATRIX_ROWS:
        run = by_row.get(row.matrix_row_id)
        if run is None:
            blockers.append(
                ReleaseBlockerV1(
                    matrix_row_id=row.matrix_row_id,
                    status="MISSING",
                    reason_code="missing-required-row",
                    run_id=None,
                )
            )
        elif run.status != "PASS":
            if run.reason_code is None:  # guarded by VerificationRunV1
                raise PlatformVerificationError(
                    f"non-PASS run has no reason code: {run.run_id}"
                )
            blockers.append(
                ReleaseBlockerV1(
                    matrix_row_id=row.matrix_row_id,
                    status=run.status,
                    reason_code=run.reason_code,
                    run_id=run.run_id,
                )
            )

    ordered_blockers = tuple(
        sorted(blockers, key=lambda blocker: blocker.matrix_row_id)
    )
    return ReleaseDecisionV1(
        schema_version=1,
        decision="NO-GO" if ordered_blockers else "GO",
        source_revision=request.source.revision,
        release_id=request.release_id,
        manifest_sha256=request.manifest_sha256,
        evaluated_run_ids=evaluated_run_ids,
        blockers=ordered_blockers,
    )


def _reject_duplicate_authority(runs: tuple[VerificationRunV1, ...]) -> None:
    duplicate_run_ids = sorted(
        run_id
        for run_id, count in Counter(run.run_id for run in runs).items()
        if count > 1
    )
    if duplicate_run_ids:
        raise PlatformVerificationError(
            f"duplicate run_id is ambiguous: {duplicate_run_ids[0]}"
        )

    duplicate_rows = sorted(
        row_id
        for row_id, count in Counter(run.matrix_row_id for run in runs).items()
        if count > 1
    )
    if duplicate_rows:
        raise PlatformVerificationError(
            f"duplicate matrix row is ambiguous: {duplicate_rows[0]}"
        )


def _validate_identity(
    request: ReleaseGateRequestV1,
    run: VerificationRunV1,
) -> None:
    if run.source.revision != request.source.revision:
        raise PlatformVerificationError(
            f"source revision mismatch for run {run.run_id}"
        )
    if run.source.dirty != request.source.dirty:
        raise PlatformVerificationError(
            f"source dirty state mismatch for run {run.run_id}"
        )
    if run.release.release_id != request.release_id:
        raise PlatformVerificationError(f"release ID mismatch for run {run.run_id}")
    if run.release.manifest_sha256 != request.manifest_sha256:
        raise PlatformVerificationError(
            f"release manifest mismatch for run {run.run_id}"
        )
    if run.runtime != request.runtime:
        raise PlatformVerificationError(
            f"runtime identity mismatch for run {run.run_id}"
        )
    if (
        run.command_or_checklist_version
        != request.command_or_checklist_version
    ):
        raise PlatformVerificationError(
            f"command or checklist version mismatch for run {run.run_id}"
        )


def _matches_gate_identity(
    request: ReleaseGateRequestV1,
    run: VerificationRunV1,
) -> bool:
    return (
        run.source == request.source
        and run.release.release_id == request.release_id
        and run.release.manifest_sha256 == request.manifest_sha256
        and run.runtime == request.runtime
        and run.command_or_checklist_version
        == request.command_or_checklist_version
    )


def _utc_value(value: str) -> datetime:
    return datetime.fromisoformat(f"{value[:-1]}+00:00")


def _validated_evidence_root(evidence_root: Path) -> Path:
    if not isinstance(evidence_root, Path):
        raise PlatformVerificationError("evidence root must be a pathlib.Path")
    return _resolve_real_directory(evidence_root, "evidence root")


def _resolve_real_directory(directory: Path, label: str) -> Path:
    absolute = Path(os.path.abspath(directory))
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PlatformVerificationError(f"{label} must be a real directory")
    if not cursor.is_dir():
        raise PlatformVerificationError(f"{label} must be a real directory")
    try:
        return cursor.resolve(strict=True)
    except OSError as exc:
        raise PlatformVerificationError(
            f"{label} must be a real directory"
        ) from exc


def _hash_evidence_file(root: Path, relative_path: str) -> str:
    candidate = root.joinpath(*PurePosixPath(relative_path).parts)
    cursor = root
    for part in PurePosixPath(relative_path).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise PlatformVerificationError(
                f"evidence file must be a regular non-symlink: {relative_path}"
            )
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as exc:
        raise PlatformVerificationError(
            f"missing evidence file: {relative_path}"
        ) from exc
    except OSError as exc:
        raise PlatformVerificationError(
            f"cannot inspect evidence file {relative_path}: {exc}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise PlatformVerificationError(
            f"evidence file must be a regular non-symlink: {relative_path}"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise PlatformVerificationError(
            f"cannot resolve evidence file {relative_path}: {exc}"
        ) from exc
    if not resolved.is_relative_to(root):
        raise PlatformVerificationError(
            f"evidence file escapes evidence root: {relative_path}"
        )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        raise PlatformVerificationError(
            f"cannot open evidence file {relative_path}: {exc}"
        ) from exc
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise PlatformVerificationError(
                f"evidence file must be regular: {relative_path}"
            )
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return digest.hexdigest()


__all__ = [
    "PlatformVerificationError",
    "create_verification_attempt_root",
    "evaluate_release_gate",
    "normalize_verification_run",
    "record_native_check",
    "select_latest_accepted",
    "write_new_verification_authority",
]
