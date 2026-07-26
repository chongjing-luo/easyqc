"""Schema-versioned, one-file-per-module JSON persistence."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
from typing import Any, Literal, Mapping
from uuid import UUID, uuid4

from models.qcmodule import QCModule
from utils.file_utils import FileUtils


MODULE_FILE_SCHEMA_VERSION = 1
ModuleScope = Literal["template", "project"]


class ModuleRepositoryError(ValueError):
    """Raised when a module catalog or record violates its file contract."""


@dataclass(frozen=True)
class ModuleFileError:
    """One path-scoped read error that leaves valid sibling records visible."""

    path: Path
    message: str


@dataclass(frozen=True)
class ModuleCatalogSnapshot:
    """Detached records plus every path-scoped catalog error."""

    records: tuple["ModuleRecord", ...]
    errors: tuple[ModuleFileError, ...]


@dataclass(frozen=True)
class ModuleRecord:
    """One module's stable file identity and complete legacy-compatible value."""

    module_id: str
    scope: ModuleScope
    display_order: int
    module: QCModule

    @classmethod
    def create(
        cls,
        module: QCModule,
        *,
        scope: ModuleScope,
        display_order: int,
        module_id: str | None = None,
    ) -> "ModuleRecord":
        """Create one detached validated record without filesystem side effects."""

        if not isinstance(module, QCModule):
            raise TypeError("module must be QCModule")
        normalized_scope = _validate_scope(scope)
        normalized_id = _canonical_module_id(
            str(uuid4()) if module_id is None else module_id
        )
        normalized_order = _validate_display_order(display_order)
        return cls(
            module_id=normalized_id,
            scope=normalized_scope,
            display_order=normalized_order,
            module=deepcopy(module),
        )

    @classmethod
    def from_json_object(
        cls,
        payload: Mapping[str, Any],
        *,
        expected_scope: ModuleScope,
    ) -> "ModuleRecord":
        """Normalize one JSON object into a detached module record."""

        if not isinstance(payload, Mapping):
            raise ModuleRepositoryError("module file must contain one object")
        if payload.get("schema_version") != MODULE_FILE_SCHEMA_VERSION:
            raise ModuleRepositoryError(
                "unsupported module schema_version: "
                f"{payload.get('schema_version')!r}"
            )
        scope = _validate_scope(payload.get("scope"))
        if scope != expected_scope:
            raise ModuleRepositoryError(
                f"module scope {scope!r} does not match repository {expected_scope!r}"
            )
        raw_module = payload.get("module")
        if not isinstance(raw_module, dict):
            raise ModuleRepositoryError("module payload must be an object")
        try:
            module = QCModule.from_legacy_dict(deepcopy(raw_module))
        except (KeyError, TypeError, ValueError) as exc:
            raise ModuleRepositoryError(f"invalid QCModule payload: {exc}") from exc
        return cls.create(
            module,
            scope=scope,
            display_order=payload.get("display_order"),
            module_id=payload.get("module_id"),
        )

    def to_json_object(self) -> dict[str, Any]:
        """Return the complete JSON object written for this one module."""

        return {
            "schema_version": MODULE_FILE_SCHEMA_VERSION,
            "module_id": self.module_id,
            "scope": self.scope,
            "display_order": self.display_order,
            "module": deepcopy(self.module).to_legacy_dict(),
        }


class ModuleRepository:
    """Own one directory whose JSON files each contain exactly one module."""

    def __init__(self, root: str | Path, *, scope: ModuleScope) -> None:
        self.root = Path(root)
        self.scope = _validate_scope(scope)

    def snapshot(self) -> ModuleCatalogSnapshot:
        """Read a detached catalog while retaining path-scoped sibling errors."""

        if not self.root.exists():
            return ModuleCatalogSnapshot(records=(), errors=())
        if not self.root.is_dir():
            error = ModuleFileError(
                self.root,
                f"module repository is not a directory: {self.root}",
            )
            return ModuleCatalogSnapshot(records=(), errors=(error,))

        records: list[ModuleRecord] = []
        errors: list[ModuleFileError] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.name):
            try:
                record = self._read_path(path)
            except (OSError, ValueError, TypeError) as exc:
                errors.append(
                    ModuleFileError(path, f"{path.name}: {exc}")
                )
                continue
            records.append(record)

        records.sort(
            key=lambda record: (
                record.display_order,
                record.module.name.casefold(),
                record.module_id,
            )
        )
        duplicate_names = _duplicates(record.module.name for record in records)
        for name in duplicate_names:
            errors.append(
                ModuleFileError(
                    self.root,
                    f"duplicate module name in {self.root}: {name}",
                )
            )
        return ModuleCatalogSnapshot(tuple(records), tuple(errors))

    def load(self, module_id: str) -> ModuleRecord:
        """Read one module ID or raise a path-specific repository error."""

        normalized_id = _canonical_module_id(module_id)
        path = self.root / f"{normalized_id}.json"
        if not path.is_file():
            raise ModuleRepositoryError(f"unknown module: {normalized_id}")
        try:
            return self._read_path(path)
        except (OSError, ValueError, TypeError) as exc:
            raise ModuleRepositoryError(f"{path}: {exc}") from exc

    def save(self, record: ModuleRecord) -> ModuleRecord:
        """Atomically persist one record and return a detached saved value."""

        normalized = self._validated_record(record)
        snapshot = self.snapshot()
        if snapshot.errors:
            raise ModuleRepositoryError(
                "module catalog contains errors: "
                + "; ".join(error.message for error in snapshot.errors)
            )
        conflict = next(
            (
                item
                for item in snapshot.records
                if item.module.name == normalized.module.name
                and item.module_id != normalized.module_id
            ),
            None,
        )
        if conflict is not None:
            raise ModuleRepositoryError(
                f"module name already exists: {normalized.module.name}"
            )
        target = self.root / f"{normalized.module_id}.json"
        FileUtils.safe_json_save(target, normalized.to_json_object())
        return self.load(normalized.module_id)

    def delete(self, module_id: str) -> bool:
        """Delete one exact module file; unknown IDs leave the catalog unchanged."""

        normalized_id = _canonical_module_id(module_id)
        path = self.root / f"{normalized_id}.json"
        if not path.exists():
            return False
        if not path.is_file():
            raise ModuleRepositoryError(f"module path is not a file: {path}")
        path.unlink()
        return True

    def migrate_legacy(
        self,
        modules: Mapping[str, Mapping[str, Any]],
    ) -> tuple[ModuleRecord, ...]:
        """Publish one complete module directory from a legacy qcmodule mapping."""

        if self.root.exists():
            return self._strict_records()
        return self.replace_legacy(modules)

    def replace_legacy(
        self,
        modules: Mapping[str, Mapping[str, Any]],
    ) -> tuple[ModuleRecord, ...]:
        """Atomically replace this catalog from one complete legacy mapping."""

        records = self._records_from_legacy(modules)
        self.replace_records(records)
        return self._strict_records()

    def replace_records(
        self,
        records: tuple[ModuleRecord, ...] | list[ModuleRecord],
    ) -> tuple[ModuleRecord, ...]:
        """Atomically replace the complete directory with validated records."""

        normalized = tuple(self._validated_record(record) for record in records)
        if not normalized:
            raise ModuleRepositoryError(
                "module catalog must contain at least one module"
            )
        self.root.parent.mkdir(parents=True, exist_ok=True)
        transaction_id = str(uuid4())
        staging = self.root.parent / f".{self.root.name}.stage.{transaction_id}"
        backup = self.root.parent / f".{self.root.name}.backup.{transaction_id}"
        staging_repository = ModuleRepository(staging, scope=self.scope)
        moved_original = False
        try:
            for record in normalized:
                staging_repository.save(record)
            staged = staging_repository.snapshot()
            if staged.errors or len(staged.records) != len(normalized):
                raise ModuleRepositoryError(
                    "staged module replacement did not validate completely"
                )
            if self.root.exists():
                if not self.root.is_dir() or self.root.is_symlink():
                    raise ModuleRepositoryError(
                        f"module repository is not a regular directory: {self.root}"
                    )
                os.replace(self.root, backup)
                moved_original = True
            try:
                os.replace(staging, self.root)
            except Exception:
                if moved_original and backup.exists() and not self.root.exists():
                    os.replace(backup, self.root)
                    moved_original = False
                raise
            published = self.snapshot()
            if published.errors or len(published.records) != len(normalized):
                raise ModuleRepositoryError(
                    "published module replacement did not validate completely"
                )
            if backup.exists():
                shutil.rmtree(backup)
                moved_original = False
            return published.records
        except Exception:
            if moved_original and backup.exists():
                if self.root.exists():
                    shutil.rmtree(self.root)
                os.replace(backup, self.root)
                moved_original = False
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
            if backup.exists() and not moved_original:
                shutil.rmtree(backup)

    def remove_catalog(self) -> bool:
        """Remove this exact owned module directory for transaction rollback."""

        if not self.root.exists():
            return False
        if not self.root.is_dir() or self.root.is_symlink():
            raise ModuleRepositoryError(
                f"module repository is not a regular directory: {self.root}"
            )
        shutil.rmtree(self.root)
        return True

    @staticmethod
    def legacy_mapping(
        records: tuple[ModuleRecord, ...] | list[ModuleRecord],
    ) -> dict[str, dict[str, Any]]:
        """Return one ordered compatibility qcmodule mapping."""

        ordered = sorted(
            records,
            key=lambda record: (
                record.display_order,
                record.module.name.casefold(),
                record.module_id,
            ),
        )
        return {
            str(index): deepcopy(record.module).to_legacy_dict()
            for index, record in enumerate(ordered, start=1)
        }

    def _read_path(self, path: Path) -> ModuleRecord:
        payload = FileUtils.safe_json_load(path)
        record = ModuleRecord.from_json_object(
            payload,
            expected_scope=self.scope,
        )
        expected_name = f"{record.module_id}.json"
        if path.name != expected_name:
            raise ModuleRepositoryError(
                f"filename must match module_id: expected {expected_name}"
            )
        return record

    def _validated_record(self, record: ModuleRecord) -> ModuleRecord:
        if not isinstance(record, ModuleRecord):
            raise TypeError("record must be ModuleRecord")
        if record.scope != self.scope:
            raise ModuleRepositoryError(
                f"record scope {record.scope!r} does not match {self.scope!r}"
            )
        return ModuleRecord.create(
            record.module,
            scope=record.scope,
            display_order=record.display_order,
            module_id=record.module_id,
        )

    def _strict_records(self) -> tuple[ModuleRecord, ...]:
        snapshot = self.snapshot()
        if snapshot.errors:
            raise ModuleRepositoryError(
                "module catalog contains errors: "
                + "; ".join(error.message for error in snapshot.errors)
            )
        if not snapshot.records:
            raise ModuleRepositoryError(
                "module catalog must contain at least one module"
            )
        return snapshot.records

    def _records_from_legacy(
        self,
        modules: Mapping[str, Mapping[str, Any]],
    ) -> tuple[ModuleRecord, ...]:
        if not isinstance(modules, Mapping):
            raise ModuleRepositoryError("legacy qcmodule must be an object")
        try:
            ordered = sorted(modules.items(), key=lambda item: int(item[0]))
        except (TypeError, ValueError) as exc:
            raise ModuleRepositoryError("legacy module keys must be integers") from exc
        if not ordered:
            raise ModuleRepositoryError(
                "legacy qcmodule must contain at least one module"
            )
        for key, _ in ordered:
            if str(int(key)) != str(key):
                raise ModuleRepositoryError(
                    f"legacy module key is not canonical: {key}"
                )

        existing = self._strict_records() if self.root.exists() else ()
        existing_by_name = {
            record.module.name: record
            for record in existing
        }
        used_ids: set[str] = set()
        records: list[ModuleRecord] = []
        for index, (_, payload) in enumerate(ordered, start=1):
            if not isinstance(payload, Mapping):
                raise ModuleRepositoryError(
                    f"legacy module {index} must be an object"
                )
            try:
                module = QCModule.from_legacy_dict(deepcopy(dict(payload)))
            except (KeyError, TypeError, ValueError) as exc:
                raise ModuleRepositoryError(
                    f"invalid legacy module {index}: {exc}"
                ) from exc
            matched = existing_by_name.get(module.name)
            if matched is None and len(existing) == len(ordered):
                positional = existing[index - 1]
                if positional.module_id not in used_ids:
                    matched = positional
            module_id = (
                matched.module_id
                if matched is not None and matched.module_id not in used_ids
                else None
            )
            record = ModuleRecord.create(
                module,
                scope=self.scope,
                display_order=index * 10,
                module_id=module_id,
            )
            used_ids.add(record.module_id)
            records.append(record)
        return tuple(records)


def _canonical_module_id(value: Any) -> str:
    try:
        parsed = UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ModuleRepositoryError(f"invalid module_id: {value!r}") from exc
    canonical = str(parsed)
    if str(value) != canonical:
        raise ModuleRepositoryError(
            f"module_id must be canonical lowercase UUID: {value!r}"
        )
    return canonical


def _validate_scope(value: Any) -> ModuleScope:
    if value not in {"template", "project"}:
        raise ModuleRepositoryError(f"invalid module scope: {value!r}")
    return value


def _validate_display_order(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ModuleRepositoryError(
            f"display_order must be a non-negative integer: {value!r}"
        )
    return value


def _duplicates(values) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return tuple(sorted(duplicates))


__all__ = [
    "MODULE_FILE_SCHEMA_VERSION",
    "ModuleCatalogSnapshot",
    "ModuleFileError",
    "ModuleRecord",
    "ModuleRepository",
    "ModuleRepositoryError",
]
