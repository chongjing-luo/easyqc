"""Implementation for strict schema-v3 offline project migration.

Purpose:
    Back up, convert, validate, and switch one explicitly registered project.
Input:
    One registry JSON path, one exact registered project name, and one backup
    parent directory.
Output:
    The same registered live path using schema v3 plus a verified, checksummed
    pre-migration backup and rollback report.
Side effects:
    Creates backup/staging files and replaces only the selected live project
    after both backup and staged validation pass.
Errors:
    ``MigrationError`` fails closed on path, schema, identity, collision,
    checksum, validation, or switchover errors.
Split trigger:
    Migrating several projects, changing the schema contract, or adding runtime
    compatibility belongs in a separate command/task.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from core.module_repository import ModuleRepository
from core.project_service import ProjectService
from core.rating_identity import (
    RatingIdentity,
    build_rating_filename,
    validate_easyqcid,
)
from core.rating_service import RatingService
from models.project import Project


_TIMESTAMP_PATTERN = re.compile(r"^[0-9]{8}_[0-9]{6}$")
_LEGACY_REPLACEMENTS = (
    ("ezqcid", "easyqcid"),
    ("ezqcbatch", "easyqcbatch"),
)
_REQUIRED_DIRECTORIES = frozenset({"Table", "RatingFiles", "modules"})


class MigrationError(RuntimeError):
    """A project cannot be migrated without violating a safety gate."""


@dataclass(frozen=True)
class ConversionSummary:
    source_path: Path
    output_path: Path
    rating_files: int
    rating_identities: int
    module_files: int
    csv_files: int
    csv_rows: int


@dataclass(frozen=True)
class ValidationSummary:
    project_path: Path
    rating_files: int
    rating_unique_easyqcids: int
    module_files: int
    csv_files: int
    subject_rows: int
    aggregation_rows: int


@dataclass(frozen=True)
class BackupSummary:
    backup_dir: Path
    project_copy: Path
    registry_copy: Path
    manifest_path: Path
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class MigrationRunSummary:
    backup_dir: Path
    stage_path: Path
    old_live_path: Path
    conversion: ConversionSummary
    validation: ValidationSummary
    removed_old_live: bool


def _lexists(path: Path) -> bool:
    return os.path.lexists(os.fspath(path))


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _require_outside(path: Path, root: Path, label: str) -> None:
    candidate = _absolute_lexical(path)
    boundary = _absolute_lexical(root)
    if candidate == boundary or boundary in candidate.parents:
        raise MigrationError(f"{label} must be outside the {root.name} project")


def _require_plain_directory(path: Path, label: str) -> Path:
    candidate = Path(path)
    if not _lexists(candidate):
        raise MigrationError(f"{label} does not exist: {candidate}")
    if candidate.is_symlink():
        raise MigrationError(f"{label} must not be a symlink: {candidate}")
    if not candidate.is_dir():
        raise MigrationError(f"{label} must be a directory: {candidate}")
    return candidate


def _require_plain_file(path: Path, label: str) -> Path:
    candidate = Path(path)
    if not _lexists(candidate):
        raise MigrationError(f"{label} does not exist: {candidate}")
    if candidate.is_symlink():
        raise MigrationError(f"{label} must not be a symlink: {candidate}")
    if not candidate.is_file():
        raise MigrationError(f"{label} must be a regular file: {candidate}")
    return candidate


def _walk_plain_files(root: Path) -> list[Path]:
    """Return deterministic regular files and reject every link/special node."""

    root = _require_plain_directory(root, "directory tree")
    result: list[Path] = []
    for current, directory_names, file_names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in sorted(directory_names):
            path = current_path / name
            if path.is_symlink():
                raise MigrationError(f"directory tree contains symlink: {path}")
            if not path.is_dir():
                raise MigrationError(
                    f"directory tree contains non-directory entry: {path}"
                )
        for name in sorted(file_names):
            path = current_path / name
            if path.is_symlink():
                raise MigrationError(f"directory tree contains symlink: {path}")
            if not path.is_file():
                raise MigrationError(
                    f"directory tree contains special file: {path}"
                )
            result.append(path)
    return sorted(result, key=lambda item: item.relative_to(root).as_posix())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _file_snapshot(root: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(root).as_posix(): (path.stat().st_size, _sha256(path))
        for path in _walk_plain_files(root)
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"{label} must contain one JSON object: {path}")
    return value


def _replace_legacy_text(value: str) -> str:
    result = value
    for old, new in _LEGACY_REPLACEMENTS:
        result = result.replace(old, new)
    return result


def _convert_json_value(value: Any, *, location: str) -> Any:
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise MigrationError(f"JSON object key is not text at {location}")
            target_key = _replace_legacy_text(key)
            if target_key in converted:
                raise MigrationError(
                    f"JSON key collision after migration at {location}: "
                    f"{key!r} -> {target_key!r}"
                )
            converted[target_key] = _convert_json_value(
                item,
                location=f"{location}.{target_key}",
            )
        return converted
    if isinstance(value, list):
        return [
            _convert_json_value(item, location=f"{location}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, str):
        return _replace_legacy_text(value)
    return value


def _validate_source_layout(source: Path, project_name: str) -> None:
    source = _require_plain_directory(source, "source project")
    expected_settings = f"settings_{project_name}.json"
    entries = {entry.name: entry for entry in source.iterdir()}
    expected_names = set(_REQUIRED_DIRECTORIES) | {expected_settings}
    if set(entries) != expected_names:
        missing = sorted(expected_names - set(entries))
        unexpected = sorted(set(entries) - expected_names)
        raise MigrationError(
            "legacy project layout mismatch: "
            f"missing={missing}, unexpected={unexpected}"
        )
    _require_plain_file(entries[expected_settings], "project settings")
    for directory_name in _REQUIRED_DIRECTORIES:
        _require_plain_directory(entries[directory_name], directory_name)

    table_files = _walk_plain_files(source / "Table")
    if any(path.parent != source / "Table" or path.suffix != ".csv" for path in table_files):
        raise MigrationError("Table must contain only direct .csv files")

    module_files = _walk_plain_files(source / "modules")
    if not module_files:
        raise MigrationError("project must contain at least one module JSON")
    if any(
        path.parent != source / "modules" or path.suffix != ".json"
        for path in module_files
    ):
        raise MigrationError("modules must contain only direct .json files")

    for path in _walk_plain_files(source / "RatingFiles"):
        relative = path.relative_to(source / "RatingFiles")
        if len(relative.parts) != 3 or path.suffix != ".json":
            raise MigrationError(
                "RatingFiles must contain only module/rater/file.json entries: "
                f"{relative}"
            )


def _convert_settings(source: Path, target: Path) -> None:
    payload = _load_json_object(source, "project settings")
    if payload.get("schema_version") != 1:
        raise MigrationError(
            f"legacy project settings must use schema_version 1: {source}"
        )
    converted = _convert_json_value(payload, location="settings")
    converted["schema_version"] = 3
    modules = converted.get("qcmodule")
    if not isinstance(modules, dict) or not modules:
        raise MigrationError("project settings qcmodule must be a nonempty object")
    _write_json(target, converted)


def _convert_module_file(source: Path, target: Path) -> None:
    payload = _load_json_object(source, "module file")
    if payload.get("schema_version") != 1:
        raise MigrationError(
            f"module file must retain independent schema_version 1: {source}"
        )
    module = payload.get("module")
    if not isinstance(module, dict):
        raise MigrationError(f"module file has no module object: {source}")
    if "ezqcid" not in module or "easyqcid" in module:
        raise MigrationError(
            f"legacy module must contain only the ezqcid identity key: {source}"
        )
    converted = _convert_json_value(payload, location=f"module:{source.name}")
    converted["schema_version"] = 1
    _write_json(target, converted)


def _convert_csv_file(source: Path, target: Path) -> int:
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MigrationError(f"cannot read CSV {source}: {exc}") from exc
    if not rows:
        raise MigrationError(f"CSV has no header: {source}")

    header = [_replace_legacy_text(value) for value in rows[0]]
    duplicates = sorted({value for value in header if header.count(value) > 1})
    if duplicates:
        raise MigrationError(
            f"CSV would contain duplicate columns after migration {source}: "
            f"{duplicates}"
        )
    width = len(header)
    for row_number, row in enumerate(rows[1:], 2):
        if len(row) != width:
            raise MigrationError(
                f"CSV row width mismatch {source}:{row_number}: "
                f"expected {width}, got {len(row)}"
            )

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with target.open("x", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(header)
            writer.writerows(rows[1:])
    except (OSError, UnicodeError, csv.Error) as exc:
        raise MigrationError(f"cannot write converted CSV {target}: {exc}") from exc
    return len(rows) - 1


def _convert_rating_file(
    source: Path,
    output_rating_root: Path,
) -> tuple[Path, RatingIdentity]:
    payload = _load_json_object(source, "rating")
    if payload.get("schema_version") not in {None, 1}:
        raise MigrationError(
            f"legacy rating schema_version must be absent or 1: {source}"
        )
    if "ezqcid" not in payload or "easyqcid" in payload:
        raise MigrationError(
            f"legacy rating must contain only the ezqcid identity key: {source}"
        )
    try:
        identity = RatingIdentity(
            module_name=payload.get("name"),
            rater=payload.get("rater"),
            easyqcid=payload.get("ezqcid"),
        )
    except (TypeError, ValueError) as exc:
        raise MigrationError(f"invalid rating identity in {source}: {exc}") from exc
    if (
        source.parent.parent.name != identity.module_name
        or source.parent.name != identity.rater
    ):
        raise MigrationError(
            f"rating directory identity disagrees with JSON payload: {source}"
        )

    converted = _convert_json_value(payload, location=f"rating:{source.name}")
    converted["schema_version"] = 3
    target = (
        output_rating_root
        / identity.module_name
        / identity.rater
        / build_rating_filename(identity)
    )
    if _lexists(target):
        raise MigrationError(f"duplicate rating target after migration: {target}")
    _write_json(target, converted)
    return target, identity


def convert_project_tree(
    source_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    project_name: str,
) -> ConversionSummary:
    """Create one all-or-nothing converted tree without changing the source."""

    source = Path(source_path)
    output = Path(output_path)
    if not isinstance(project_name, str) or not project_name:
        raise MigrationError("project_name must be nonempty text")
    _validate_source_layout(source, project_name)
    if _lexists(output):
        raise MigrationError(f"output already exists: {output}")
    _require_outside(output, source, "output")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise MigrationError(f"output parent must be a plain directory: {output.parent}")

    partial = Path(
        tempfile.mkdtemp(
            prefix=f".{output.name}.partial-",
            dir=output.parent,
        )
    )
    try:
        settings_name = f"settings_{project_name}.json"
        _convert_settings(source / settings_name, partial / settings_name)

        module_files = sorted((source / "modules").glob("*.json"))
        for path in module_files:
            _convert_module_file(path, partial / "modules" / path.name)

        csv_rows = 0
        csv_files = sorted((source / "Table").glob("*.csv"))
        csv_targets: set[str] = set()
        for path in csv_files:
            target_name = path.name.replace("ezqc", "easyqc")
            if target_name in csv_targets:
                raise MigrationError(
                    f"duplicate CSV target filename after migration: {target_name}"
                )
            csv_targets.add(target_name)
            csv_rows += _convert_csv_file(
                path,
                partial / "Table" / target_name,
            )

        rating_files = sorted(
            (source / "RatingFiles").glob("*/*/*.json"),
            key=lambda item: item.relative_to(source / "RatingFiles").as_posix(),
        )
        identities: set[RatingIdentity] = set()
        for path in rating_files:
            _, identity = _convert_rating_file(path, partial / "RatingFiles")
            if identity in identities:
                raise MigrationError(
                    "duplicate rating identity after migration: "
                    f"{identity.module_name}/{identity.rater}/{identity.easyqcid}"
                )
            identities.add(identity)

        for required in _REQUIRED_DIRECTORIES:
            (partial / required).mkdir(parents=True, exist_ok=True)
        if _lexists(output):
            raise MigrationError(f"output appeared during conversion: {output}")
        os.rename(partial, output)
    except Exception:
        if _lexists(partial):
            shutil.rmtree(partial)
        raise

    return ConversionSummary(
        source_path=source,
        output_path=output,
        rating_files=len(rating_files),
        rating_identities=len(identities),
        module_files=len(module_files),
        csv_files=len(csv_files),
        csv_rows=csv_rows,
    )


def _assert_no_retired_json(value: Any, *, location: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if "ezqc" in str(key).casefold():
                raise MigrationError(f"retired JSON key remains at {location}.{key}")
            _assert_no_retired_json(item, location=f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_retired_json(item, location=f"{location}[{index}]")
    elif isinstance(value, str) and "ezqcid" in value.casefold():
        raise MigrationError(f"retired identity text remains at {location}")


def _validate_no_retired_contract(
    project_path: Path,
    project_name: str,
) -> None:
    for path in sorted((project_path / "Table").glob("*.csv")):
        if "ezqc" in path.name.casefold():
            raise MigrationError(f"retired table filename remains: {path}")
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                header = next(csv.reader(handle))
        except (OSError, UnicodeError, csv.Error, StopIteration) as exc:
            raise MigrationError(f"cannot validate CSV header {path}: {exc}") from exc
        retired = [column for column in header if "ezqc" in column.casefold()]
        if retired:
            raise MigrationError(f"retired CSV columns remain in {path}: {retired}")

    json_paths = [
        project_path / f"settings_{project_name}.json",
        *sorted((project_path / "modules").glob("*.json")),
        *sorted((project_path / "RatingFiles").glob("*/*/*.json")),
    ]
    for path in json_paths:
        _assert_no_retired_json(
            _load_json_object(path, "schema-v3 JSON"),
            location=str(path),
        )


def validate_schema3_project(
    project_path: str | os.PathLike[str],
    project_name: str,
    *,
    expected_ratings: int | None = None,
    expected_modules: int | None = None,
    expected_csv_files: int | None = None,
) -> ValidationSummary:
    """Validate one detached tree with current production Core services."""

    project_path = _require_plain_directory(Path(project_path), "schema-v3 project")
    _validate_no_retired_contract(project_path, project_name)
    for canonical in (
        "easyqc_all.csv",
        "easyqc_qctable.csv",
        "easyqc_qctable_filter.csv",
    ):
        if not (project_path / "Table" / canonical).is_file():
            raise MigrationError(f"canonical table is missing: {canonical}")

    with tempfile.TemporaryDirectory(prefix="easyqc-schema3-validation-") as temp:
        # Avoid the repository's pytest runtime-data guard treating shutil's
        # dir-fd-relative cleanup name ``projects.json`` as the real registry.
        registry_path = Path(temp) / "schema3_validation_registry.json"
        registry_path.write_text(
            json.dumps(
                {
                    "projects": {project_name: str(project_path)},
                    "last_project": project_name,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            prepared = ProjectService(registry_path).prepare_load(project_name)
        except Exception as exc:
            raise MigrationError(f"ProjectService validation failed: {exc}") from exc

    repository = ModuleRepository(project_path / "modules", scope="project")
    module_snapshot = repository.snapshot()
    if module_snapshot.errors:
        raise MigrationError(
            "module repository validation failed: "
            + "; ".join(error.message for error in module_snapshot.errors)
        )
    if not module_snapshot.records:
        raise MigrationError("module repository is empty")
    settings_payload = _load_json_object(
        project_path / f"settings_{project_name}.json",
        "project settings",
    )
    if settings_payload.get("qcmodule") != repository.settings_mapping(
        module_snapshot.records
    ):
        raise MigrationError(
            "project settings qcmodule disagrees with the module repository"
        )

    project = prepared.project
    rating_scan = RatingService(project).scan_rating_records()
    if rating_scan.errors:
        details = "; ".join(
            f"{issue.path}: {issue.code}: {issue.message}"
            for issue in rating_scan.errors[:10]
        )
        raise MigrationError(f"rating scan failed: {details}")

    subject_path = project_path / "Table" / "easyqc_all.csv"
    try:
        subjects = pd.read_csv(
            subject_path,
            dtype={"easyqcid": "string"},
            keep_default_na=False,
        )
    except Exception as exc:
        raise MigrationError(f"cannot load easyqc_all.csv: {exc}") from exc
    if "easyqcid" not in subjects.columns:
        raise MigrationError("easyqc_all.csv has no easyqcid column")
    normalized_ids: list[str] = []
    for value in subjects["easyqcid"].tolist():
        try:
            normalized_ids.append(validate_easyqcid(str(value)))
        except (TypeError, ValueError) as exc:
            raise MigrationError(f"invalid easyqc_all identity {value!r}: {exc}") from exc
    if len(set(normalized_ids)) != len(normalized_ids):
        raise MigrationError("easyqc_all.csv contains duplicate easyqcid values")
    casefold_ids = [value.casefold() for value in normalized_ids]
    if len(set(casefold_ids)) != len(casefold_ids):
        raise MigrationError("easyqc_all.csv contains case-only easyqcid collisions")

    rating_identities = {record.identity for record in rating_scan.records}
    rating_easyqcids = {identity.easyqcid for identity in rating_identities}
    missing_subjects = sorted(rating_easyqcids - set(normalized_ids))
    if missing_subjects:
        raise MigrationError(
            "ratings refer to easyqcid values absent from easyqc_all.csv: "
            f"{missing_subjects[:10]}"
        )
    try:
        aggregated = RatingService(project).aggregate_to_wide(
            [record.rating for record in rating_scan.records],
            subjects,
        )
    except Exception as exc:
        raise MigrationError(f"rating aggregation failed: {exc}") from exc

    csv_count = len(list((project_path / "Table").glob("*.csv")))
    actual = {
        "ratings": len(rating_scan.records),
        "modules": len(module_snapshot.records),
        "csv_files": csv_count,
    }
    expected = {
        "ratings": expected_ratings,
        "modules": expected_modules,
        "csv_files": expected_csv_files,
    }
    for key, expected_value in expected.items():
        if expected_value is not None and actual[key] != expected_value:
            raise MigrationError(
                f"{key} count mismatch: expected {expected_value}, got {actual[key]}"
            )
    if len(aggregated) != len(subjects):
        raise MigrationError(
            "aggregation row count mismatch: "
            f"expected {len(subjects)}, got {len(aggregated)}"
        )

    return ValidationSummary(
        project_path=project_path,
        rating_files=len(rating_scan.records),
        rating_unique_easyqcids=len(rating_easyqcids),
        module_files=len(module_snapshot.records),
        csv_files=csv_count,
        subject_rows=len(subjects),
        aggregation_rows=len(aggregated),
    )


def _manifest_entries(
    project_copy: Path,
    registry_copy: Path,
    backup_dir: Path,
) -> list[dict[str, Any]]:
    paths = [*_walk_plain_files(project_copy), registry_copy]
    return [
        {
            "relative_path": path.relative_to(backup_dir).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(paths, key=lambda item: item.relative_to(backup_dir).as_posix())
    ]


def create_verified_backup(
    project_path: str | os.PathLike[str],
    registry_path: str | os.PathLike[str],
    backup_dir: str | os.PathLike[str],
) -> BackupSummary:
    """Create a complete no-overwrite backup and verify source stability."""

    project = _require_plain_directory(Path(project_path), "live project")
    registry = _require_plain_file(Path(registry_path), "project registry")
    backup = Path(backup_dir)
    if _lexists(backup):
        raise MigrationError(f"backup already exists: {backup}")
    _require_outside(backup, project, "backup")
    backup.parent.mkdir(parents=True, exist_ok=True)
    _require_plain_directory(backup.parent, "backup parent")

    project_before = _file_snapshot(project)
    registry_before = (registry.stat().st_size, _sha256(registry))
    partial = Path(
        tempfile.mkdtemp(prefix=f".{backup.name}.partial-", dir=backup.parent)
    )
    try:
        project_copy = partial / "project" / project.name
        registry_copy = partial / "registry" / registry.name
        project_copy.parent.mkdir(parents=True, exist_ok=True)
        registry_copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(project, project_copy, copy_function=shutil.copy2)
        shutil.copy2(registry, registry_copy)

        project_after = _file_snapshot(project)
        registry_after = (registry.stat().st_size, _sha256(registry))
        if project_after != project_before or registry_after != registry_before:
            raise MigrationError(
                "live project or registry changed while the backup was being created"
            )
        if _file_snapshot(project_copy) != project_before:
            raise MigrationError("backup project checksum does not match source")
        if (registry_copy.stat().st_size, _sha256(registry_copy)) != registry_before:
            raise MigrationError("backup registry checksum does not match source")

        entries = _manifest_entries(project_copy, registry_copy, partial)
        manifest = {
            "schema_version": 1,
            "created_at": datetime.now().astimezone().isoformat(),
            "source_project_path": str(project),
            "source_registry_path": str(registry),
            "project_directory_name": project.name,
            "files": entries,
        }
        _write_json(partial / "backup_manifest.json", manifest)
        if _lexists(backup):
            raise MigrationError(f"backup appeared during creation: {backup}")
        os.rename(partial, backup)
    except Exception:
        if _lexists(partial):
            shutil.rmtree(partial)
        raise
    return verify_backup(backup)


def verify_backup(backup_dir: str | os.PathLike[str]) -> BackupSummary:
    """Recompute every manifest entry and reject missing, extra, or changed data."""

    backup = _require_plain_directory(Path(backup_dir), "backup")
    manifest_path = _require_plain_file(
        backup / "backup_manifest.json",
        "backup manifest",
    )
    manifest = _load_json_object(manifest_path, "backup manifest")
    if manifest.get("schema_version") != 1:
        raise MigrationError("backup manifest must use schema_version 1")
    project_name = manifest.get("project_directory_name")
    if not isinstance(project_name, str) or not project_name:
        raise MigrationError("backup manifest has no project directory name")
    project_copy = _require_plain_directory(
        backup / "project" / project_name,
        "backed-up project",
    )
    registry_copy = _require_plain_file(
        backup / "registry" / "projects.json",
        "backed-up registry",
    )
    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise MigrationError("backup manifest files must be a nonempty array")

    expected_paths: set[str] = set()
    total_bytes = 0
    for entry in raw_entries:
        if not isinstance(entry, dict):
            raise MigrationError("backup manifest file entry must be an object")
        relative = entry.get("relative_path")
        size = entry.get("size")
        digest = entry.get("sha256")
        if not isinstance(relative, str) or not relative:
            raise MigrationError("backup manifest file path must be nonempty text")
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise MigrationError(f"unsafe backup manifest path: {relative}")
        if relative in expected_paths:
            raise MigrationError(f"duplicate backup manifest path: {relative}")
        expected_paths.add(relative)
        path = _require_plain_file(backup / relative_path, "backed-up file")
        actual_size = path.stat().st_size
        actual_digest = _sha256(path)
        if actual_size != size or actual_digest != digest:
            raise MigrationError(f"backup checksum mismatch: {relative}")
        total_bytes += actual_size

    actual_paths = {
        path.relative_to(backup).as_posix()
        for path in [*_walk_plain_files(project_copy), registry_copy]
    }
    if actual_paths != expected_paths:
        raise MigrationError(
            "backup manifest file set mismatch: "
            f"missing={sorted(expected_paths - actual_paths)}, "
            f"extra={sorted(actual_paths - expected_paths)}"
        )
    return BackupSummary(
        backup_dir=backup,
        project_copy=project_copy,
        registry_copy=registry_copy,
        manifest_path=manifest_path,
        file_count=len(expected_paths),
        total_bytes=total_bytes,
    )


def _load_registered_project(registry_path: Path, project_name: str) -> Path:
    registry = _load_json_object(registry_path, "project registry")
    projects = registry.get("projects")
    if not isinstance(projects, dict):
        raise MigrationError("project registry has no projects object")
    value = projects.get(project_name)
    if not isinstance(value, str) or not value:
        raise MigrationError(f"project is not registered: {project_name}")
    project = Path(value)
    if project.name != f"easyqc_{project_name}":
        raise MigrationError(
            f"registered project directory name is not easyqc_{project_name}: {project}"
        )
    return _require_plain_directory(project, "registered live project")


def _assert_source_matches_backup(
    project_path: Path,
    registry_path: Path,
    backup: BackupSummary,
) -> None:
    if _file_snapshot(project_path) != _file_snapshot(backup.project_copy):
        raise MigrationError("live project changed after backup verification")
    if (
        registry_path.stat().st_size,
        _sha256(registry_path),
    ) != (
        backup.registry_copy.stat().st_size,
        _sha256(backup.registry_copy),
    ):
        raise MigrationError("project registry changed after backup verification")


def _write_rollback(
    backup: BackupSummary,
    live_project: Path,
    registry_path: Path,
) -> None:
    rollback_path = backup.backup_dir / "ROLLBACK.md"
    if _lexists(rollback_path):
        raise MigrationError(f"rollback instructions already exist: {rollback_path}")
    text = f"""# EasyQC project rollback

This backup is the complete pre-schema-v3 state of `{live_project.name}`.

1. Close EasyQC.
2. Move the current `{live_project}` directory to a new, unused diagnostic path.
3. Copy `{backup.project_copy}` back to `{live_project}`.
4. If registry recovery is also required, first preserve the current registry,
   then copy `{backup.registry_copy}` to `{registry_path}`.
5. Recompute and compare files with `{backup.manifest_path}` before use.

Never copy over an existing project directory; move it aside first.
"""
    with rollback_path.open("x", encoding="utf-8") as handle:
        handle.write(text)


def _jsonable_summary(summary: Any) -> dict[str, Any]:
    return {
        key: str(value) if isinstance(value, Path) else value
        for key, value in asdict(summary).items()
    }


def migrate_registered_project(
    *,
    registry_path: str | os.PathLike[str],
    project_name: str,
    backup_parent: str | os.PathLike[str],
    timestamp: str | None = None,
) -> MigrationRunSummary:
    """Back up and atomically migrate one exact registered project."""

    registry = _require_plain_file(Path(registry_path), "project registry")
    live = _load_registered_project(registry, project_name)
    stamp = timestamp or datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    if _TIMESTAMP_PATTERN.fullmatch(stamp) is None:
        raise MigrationError("timestamp must use YYYYMMDD_HHMMSS")
    backup_parent_path = Path(backup_parent)
    _require_outside(backup_parent_path, live, "backup parent")
    backup_parent_path.mkdir(parents=True, exist_ok=True)
    _require_plain_directory(backup_parent_path, "backup parent")

    backup_dir = backup_parent_path / f"{live.name}_pre_schema3_{stamp}"
    stage = live.parent / f".{live.name}_schema3_stage_{stamp}"
    old_live = live.parent / f".{live.name}_pre_schema3_hold_{stamp}"
    for path, label in (
        (backup_dir, "backup"),
        (stage, "stage"),
        (old_live, "old-live hold"),
    ):
        if _lexists(path):
            raise MigrationError(f"{label} path already exists: {path}")
    if live.parent.stat().st_dev != backup_parent_path.stat().st_dev:
        raise MigrationError("backup and live project must be on the same filesystem")

    backup = create_verified_backup(live, registry, backup_dir)
    conversion = convert_project_tree(live, stage, project_name)
    staged_validation = validate_schema3_project(
        stage,
        project_name,
        expected_ratings=conversion.rating_files,
        expected_modules=conversion.module_files,
        expected_csv_files=conversion.csv_files,
    )
    verify_backup(backup_dir)
    _assert_source_matches_backup(live, registry, backup)

    switched = False
    try:
        os.rename(live, old_live)
        try:
            os.rename(stage, live)
        except Exception:
            os.rename(old_live, live)
            raise
        switched = True

        # This is the exact registry and public load path used by the running app.
        ProjectService(registry).prepare_load(project_name)
        live_validation = validate_schema3_project(
            live,
            project_name,
            expected_ratings=conversion.rating_files,
            expected_modules=conversion.module_files,
            expected_csv_files=conversion.csv_files,
        )
        if live_validation != ValidationSummary(
            project_path=live,
            rating_files=staged_validation.rating_files,
            rating_unique_easyqcids=staged_validation.rating_unique_easyqcids,
            module_files=staged_validation.module_files,
            csv_files=staged_validation.csv_files,
            subject_rows=staged_validation.subject_rows,
            aggregation_rows=staged_validation.aggregation_rows,
        ):
            raise MigrationError("live validation differs from staged validation")
    except Exception as exc:
        if switched and _lexists(live) and _lexists(old_live):
            if _lexists(stage):
                raise MigrationError(
                    "post-switchover validation failed and automatic rollback "
                    f"cannot preserve the failed stage at {stage}: {exc}"
                ) from exc
            os.rename(live, stage)
            os.rename(old_live, live)
        raise

    if _file_snapshot(old_live) != _file_snapshot(backup.project_copy):
        raise MigrationError(
            "old-live hold differs from the verified backup; refusing cleanup"
        )
    _write_rollback(backup, live, registry)
    report = {
        "schema_version": 1,
        "status": "completed",
        "completed_at": datetime.now().astimezone().isoformat(),
        "project_name": project_name,
        "live_project_path": str(live),
        "registry_path": str(registry),
        "backup_dir": str(backup_dir),
        "conversion": _jsonable_summary(conversion),
        "validation": _jsonable_summary(live_validation),
        "backup": _jsonable_summary(backup),
    }
    shutil.rmtree(old_live)
    _write_json(backup_dir / "migration_report.json", report)

    return MigrationRunSummary(
        backup_dir=backup_dir,
        stage_path=stage,
        old_live_path=old_live,
        conversion=conversion,
        validation=live_validation,
        removed_old_live=True,
    )
