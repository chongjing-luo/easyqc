#!/usr/bin/env python3
"""Record one complete native UI checklist as one retained verification run.

Input: one canonical native VerificationRequestV1, one NativeUiChecklistV1 and
one absent absolute attempt root. Output: copied checklist evidence, one raw
result and one canonical run. Side effects: writes only under the new attempt
root; it never performs or infers native UI execution.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.platform_verification import (  # noqa: E402
    PlatformVerificationError,
    create_verification_attempt_root,
    normalize_verification_run,
    record_native_check,
    write_new_verification_authority,
)
from models.platform_verification import (  # noqa: E402
    NativeUiChecklistV1,
    PlatformVerificationContractError,
    VerificationRequestV1,
)


REPORT_PATH = "reports/native-ui-checklist.json"


def _read_authority(path: Path, parser: object, label: str) -> object:
    if path.is_symlink() or not path.is_file():
        raise PlatformVerificationError(
            f"{label} must be a regular non-symlink file"
        )
    parse = getattr(parser, "from_canonical_bytes")
    return parse(path.read_bytes())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--checklist", type=Path, required=True)
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
        checklist = _read_authority(
            args.checklist,
            NativeUiChecklistV1,
            "native checklist",
        )
        if not isinstance(request, VerificationRequestV1):
            raise PlatformVerificationError("request parser returned wrong type")
        if not isinstance(checklist, NativeUiChecklistV1):
            raise PlatformVerificationError("checklist parser returned wrong type")
        raw = record_native_check(request, checklist, REPORT_PATH)
        root = create_verification_attempt_root(args.attempt_root)
        report = root / "reports" / "native-ui-checklist.json"
        report.parent.mkdir()
        write_new_verification_authority(report, checklist.canonical_bytes)
        write_new_verification_authority(
            root / "raw-verification-result.json",
            raw.canonical_bytes,
        )
        run = normalize_verification_run(raw, root)
        write_new_verification_authority(
            root / "verification-run.json",
            run.canonical_bytes,
        )
    except (
        OSError,
        PlatformVerificationContractError,
        PlatformVerificationError,
    ) as exc:
        print(f"ERROR native UI checklist: {exc}", file=sys.stderr)
        return 2
    message = (
        f"{run.status} run_id={run.run_id} row={run.matrix_row_id} "
        f"output={args.attempt_root.resolve()}"
    )
    if run.status == "PASS":
        print(message, flush=True)
        return 0
    print(message, file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
