#!/usr/bin/env python3
"""Plan or prepare an inactive EasyQC rating migration candidate.

Input is one exact directory named ``RatingFiles``. The default is a read-only
dry-run. ``--apply`` builds and fully validates a sibling candidate tree whose
rating records are canonical-only. It does not activate that tree, rename the
active tree, or delete/move any legacy source. Cutover and archival require a
separately authorized operation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from core.rating_migration import (  # noqa: E402
    MigrationApplyError,
    MigrationConflictError,
    MigrationPlanStaleError,
    apply_legacy_migration,
    plan_legacy_migration,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "rating_root",
        type=Path,
        help="exact active RatingFiles directory to inspect",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "prepare a verified inactive sibling candidate; omitted means "
            "read-only dry-run"
        ),
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help=(
            "include per-file entries, issues, and candidate paths in stdout; "
            "default output is summary-only"
        ),
    )
    return parser


def _write_json(payload: dict[str, object]) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def _stdout_payload(
    payload: dict[str, object],
    *,
    include_details: bool,
) -> dict[str, object]:
    if not include_details:
        for key in ("entries", "issues", "canonical_files"):
            payload.pop(key, None)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    """Run one dry-run or explicit candidate preparation.

    Output is one structured JSON report on stdout. Exit code is 0 for a clean
    dry-run or verified inactive candidate, 2 for plan conflicts, and 3 for a
    stale/failed apply. No mode discovers projects, activates a candidate, or
    deletes/moves active runtime data.
    """

    args = _parser().parse_args(argv)
    plan = plan_legacy_migration(args.rating_root)

    if not args.apply:
        output = _stdout_payload(
            plan.to_dict(),
            include_details=args.details,
        )
        output["mode"] = "dry-run"
        _write_json(output)
        return 2 if plan.has_conflicts else 0

    if plan.has_conflicts:
        output = _stdout_payload(
            plan.to_dict(),
            include_details=args.details,
        )
        output.update(
            {
                "mode": "apply-candidate",
                "applied": False,
                "activated": False,
            }
        )
        _write_json(output)
        return 2

    try:
        report = apply_legacy_migration(plan)
    except (
        MigrationApplyError,
        MigrationConflictError,
        MigrationPlanStaleError,
    ) as exc:
        current = plan_legacy_migration(args.rating_root)
        output = _stdout_payload(
            current.to_dict(),
            include_details=args.details,
        )
        output.update(
            {
                "mode": "apply-candidate",
                "applied": False,
                "activated": False,
                "error": str(exc),
            }
        )
        _write_json(output)
        return 3

    output = _stdout_payload(
        report.to_dict(),
        include_details=args.details,
    )
    output["mode"] = "apply-candidate"
    _write_json(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
