"""Normalize per-module viewer execution mode (``interper``) in a project.

ADR-013 amendment (2026-09-23): the viewer execution mode is module-specific
(``QCModule.interper``: ``"shell"`` or ``"direct"``). This script rewrites a
real project's ``modules/*.json`` files and the ``qcmodule`` mapping inside
``settings_<project>.json`` so every module stores an explicit target value.

Safety rules:
- EasyQC must be closed for the project (no in-memory writer clobbering us).
- A timestamped backup is written OUTSIDE the project tree first.
- Each file is rewritten only when its value actually changes.
- Writes are atomic (temp file + ``os.replace``) and preserve the original
  owner, group and permission bits.

Usage (run with sufficient permissions, e.g. under sudo, EasyQC closed):

    python3 migrate_interper_per_module.py <project_dir> [--value shell|direct]
        [--backup-root DIR]

Default value: shell (preserves pre-amendment behaviour of installations that
had ``viewer_execution.shell=true``).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path

VALID_VALUES = ("shell", "direct")


def _die(message: str) -> None:
    print(f"ABORT: {message}", file=sys.stderr)
    raise SystemExit(1)


def _normalize_module_file(path: Path, target: str, report: list[tuple[str, str, str]]) -> bool:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    module = data.get("module")
    if not isinstance(module, dict):
        _die(f"{path}: module object missing")
    current = module.get("interper", "<missing>")
    if current == target:
        report.append((path.name, str(current), "unchanged"))
        return False
    module["interper"] = target
    indent = _detect_indent(raw)
    rewritten = json.dumps(
        data, ensure_ascii=False, indent=indent, separators=(",", ": ")
    )
    if not raw.endswith("\n"):
        rewritten += ""
    elif not rewritten.endswith("\n"):
        rewritten += "\n"
    _atomic_write(path, rewritten)
    report.append((path.name, str(current), f"-> {target}"))
    return True


def _detect_indent(raw: str) -> int:
    for line in raw.splitlines():
        stripped = line.lstrip(" ")
        if stripped and len(line) - len(stripped) > 0:
            return len(line) - len(stripped)
    return 2


def _atomic_write(path: Path, text: str) -> None:
    stat = path.stat()
    fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".migrate-"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temp_name, stat.st_mode & 0o7777)
        os.chown(temp_name, stat.st_uid, stat.st_gid)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _normalize_settings_file(path: Path, target: str) -> bool:
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    qcmodule = data.get("qcmodule")
    if not isinstance(qcmodule, dict):
        _die(f"{path}: qcmodule mapping missing")
    changed = False
    for payload in qcmodule.values():
        if not isinstance(payload, dict):
            _die(f"{path}: non-dict qcmodule payload")
        if payload.get("interper") != target:
            payload["interper"] = target
            changed = True
    if not changed:
        return False
    indent = _detect_indent(raw)
    rewritten = json.dumps(
        data, ensure_ascii=False, indent=indent, separators=(",", ": ")
    )
    if raw.endswith("\n") and not rewritten.endswith("\n"):
        rewritten += "\n"
    _atomic_write(path, rewritten)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--value", choices=VALID_VALUES, default="shell")
    parser.add_argument("--backup-root", type=Path, default=None)
    args = parser.parse_args()

    project = args.project_dir.resolve()
    modules_dir = project / "modules"
    if not modules_dir.is_dir():
        _die(f"{project}: no modules/ directory (not a v3 project?)")
    settings_files = [
        item
        for item in project.glob("settings_*.json")
        if item.is_file()
    ]
    if not settings_files:
        _die(f"{project}: no settings_*.json found")
    if len(settings_files) > 1:
        _die(f"{project}: expected exactly one settings file, found {settings_files}")
    settings_path = settings_files[0]
    module_files = sorted(modules_dir.glob("*.json"))
    if not module_files:
        _die(f"{modules_dir}: no module files")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = (
        args.backup_root.resolve()
        if args.backup_root is not None
        else project.parent / f"_interper_migration_backup_{stamp}" / project.name
    )
    backup_root.mkdir(parents=True, exist_ok=True)
    (backup_root / "modules").mkdir(exist_ok=True)
    for module_file in module_files:
        shutil.copy2(module_file, backup_root / "modules" / module_file.name)
    shutil.copy2(settings_path, backup_root / settings_path.name)
    print(f"backup written: {backup_root}")

    report: list[tuple[str, str, str]] = []
    changed_files = 0
    for module_file in module_files:
        if _normalize_module_file(module_file, args.value, report):
            changed_files += 1
    if _normalize_settings_file(settings_path, args.value):
        changed_files += 1
        report.append((settings_path.name, "qcmodule", f"-> {args.value}"))

    print(f"target interper: {args.value} | changed files: {changed_files}")
    for name, current, outcome in report:
        print(f"  {name}: {current} {outcome}")

    failed = []
    for module_file in module_files:
        module = json.loads(module_file.read_text(encoding="utf-8"))["module"]
        if module.get("interper") != args.value:
            failed.append(module_file.name)
    settings_value = {
        payload.get("interper")
        for payload in json.loads(settings_path.read_text(encoding="utf-8"))[
            "qcmodule"
        ].values()
    }
    if failed or settings_value != {args.value}:
        _die(f"post-verification failed: modules={failed} settings={settings_value}")
    print("post-verification OK: all modules and settings qcmodule are "
          f"interper={args.value!r}")


if __name__ == "__main__":
    main()
