#!/usr/bin/env python3
"""CLI for one strict schema-v3 EasyQC project migration.

Purpose:
    Invoke the offline migration transaction for one registered project.
Input:
    Registry path, exact project name, backup parent, and optional timestamp.
Output:
    One compact JSON completion summary on stdout.
Side effects:
    Delegates backup, conversion, validation, and live switchover to
    ``project_schema_v3_migration``.
Errors:
    Exits nonzero with one explicit migration error.
Split trigger:
    Additional commands or unrelated input/output modes require another CLI.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.project_schema_v3_migration import (  # noqa: E402
    MigrationError,
    migrate_registered_project,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Back up and migrate one exact registered EasyQC project to "
            "strict schema version 3."
        )
    )
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--backup-parent", required=True, type=Path)
    parser.add_argument("--timestamp")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = migrate_registered_project(
            registry_path=args.registry,
            project_name=args.project,
            backup_parent=args.backup_parent,
            timestamp=args.timestamp,
        )
    except MigrationError as exc:
        raise SystemExit(f"migration failed: {exc}") from exc
    print(
        json.dumps(
            {
                "status": "completed",
                "project": args.project,
                "backup": str(result.backup_dir),
                "ratings": result.validation.rating_files,
                "modules": result.validation.module_files,
                "csv_files": result.validation.csv_files,
                "subject_rows": result.validation.subject_rows,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
