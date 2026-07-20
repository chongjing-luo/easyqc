"""Pure, deterministic release gate for typed platform-verification records."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from itertools import islice

from models.platform_verification import (
    REQUIRED_MATRIX_ROWS,
    ReleaseBlockerV1,
    ReleaseDecisionV1,
    ReleaseGateRequestV1,
    VerificationRunV1,
)


class PlatformVerificationError(RuntimeError):
    """Raised when release evidence is ambiguous or has the wrong identity."""


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


__all__ = ["PlatformVerificationError", "evaluate_release_gate"]
