#!/usr/bin/env python3
"""Normalize one raw evidence bundle into one new canonical verification run.

Input: one canonical RawVerificationResultV1 file, one contained evidence root
and one absent output path. Output: one VerificationRunV1 file. Side effects:
reads/hashes referenced evidence and atomically creates only the output file.
Registry selection and release-gate evaluation are separate contracts.
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
    normalize_verification_run,
    write_new_verification_authority,
)
from models.platform_verification import (  # noqa: E402
    PlatformVerificationContractError,
    RawVerificationResultV1,
)


def _read_authority(path: Path, parser: object, label: str) -> object:
    if path.is_symlink() or not path.is_file():
        raise PlatformVerificationError(
            f"{label} must be a regular non-symlink file"
        )
    data = path.read_bytes()
    parse = getattr(parser, "from_canonical_bytes")
    return parse(data)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-result", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        raw = _read_authority(
            args.raw_result,
            RawVerificationResultV1,
            "raw result",
        )
        if not isinstance(raw, RawVerificationResultV1):
            raise PlatformVerificationError("raw result parser returned wrong type")
        run = normalize_verification_run(raw, args.evidence_root)
        write_new_verification_authority(args.output, run.canonical_bytes)
    except (
        OSError,
        PlatformVerificationContractError,
        PlatformVerificationError,
    ) as exc:
        print(f"ERROR normalize verification: {exc}", file=sys.stderr)
        return 2
    print(
        f"PASS normalized run_id={run.run_id} row={run.matrix_row_id} "
        f"output={args.output.resolve()}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
