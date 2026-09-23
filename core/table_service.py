from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Final, Iterator

import pandas as pd

from core.rating_write_lock import RatingWriteLockError, rating_write_lock
from core.table_type_metadata import describe_table_types, read_typed_csv
from models.project import Project


TABLE_ALL: Final = "easyqc_all"
TABLE_QCTABLE: Final = "easyqc_qctable"
TABLE_QCTABLE_FILTER: Final = "easyqc_qctable_filter"


@dataclass
class LoadedProjectTables:
    variables: dict[str, pd.DataFrame | None]
    results: dict[str, pd.DataFrame | None]


@dataclass(frozen=True)
class TableSnapshot:
    """One table value and the exact content revision it was read from."""

    dataframe: pd.DataFrame | None
    revision: str | None


class TableServiceError(RuntimeError):
    """Base class for table persistence failures."""


class TableStateConflictError(TableServiceError):
    """A table changed after the caller captured its source revision."""


class _UncheckedRevision:
    pass


_UNCHECKED_REVISION = _UncheckedRevision()


class TableService:
    def table_path(self, project: Project, table_type: str) -> Path:
        return project.table_dir / f"{table_type}.csv"

    def load_table(self, project: Project, table_type: str) -> pd.DataFrame | None:
        return self.load_table_snapshot(project, table_type).dataframe

    @classmethod
    def read_csv(cls, path: str | Path) -> pd.DataFrame:
        """Read an external CSV and its optional types companion, without writes."""

        snapshot = cls._read_snapshot(Path(path))
        if snapshot.dataframe is None:
            raise FileNotFoundError(path)
        return snapshot.dataframe

    def load_table_snapshot(
        self,
        project: Project,
        table_type: str,
    ) -> TableSnapshot:
        """Read one DataFrame and revision under the project writer lease."""

        path = self.table_path(project, table_type)
        if not path.exists():
            return TableSnapshot(dataframe=None, revision=None)
        with self._project_write_lock(project):
            return self._read_snapshot(path)

    @classmethod
    def _read_snapshot(cls, path: Path) -> TableSnapshot:
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            return TableSnapshot(dataframe=None, revision=None)
        digest = hashlib.sha256(payload).hexdigest()
        schema = cls._read_types(path, digest)
        try:
            frame = read_typed_csv(payload, schema)
        except (TypeError, ValueError) as error:
            if schema is None:
                raise
            raise TableServiceError(f"表格类型记录无效 {cls._types_path(path)}: {error}") from error
        return TableSnapshot(frame, cls._typed_revision(digest, schema))

    @staticmethod
    def _types_path(path: Path) -> Path:
        return path.with_name(path.name + ".types.json")

    @staticmethod
    def _canonical_json(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    @classmethod
    def _typed_revision(cls, digest: str, schema: dict | None) -> str:
        if schema is None:
            return digest
        return hashlib.sha256(digest.encode("ascii") + b"\0" + cls._canonical_json(schema)).hexdigest()

    @classmethod
    def _read_types(cls, path: Path, digest: str) -> dict | None:
        types_path = cls._types_path(path)
        try:
            payload = types_path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            catalog = json.loads(payload)
            if (not isinstance(catalog, dict)
                    or set(catalog) != {"format", "schema_version", "versions"}
                    or catalog["format"] != "easyqc-table-types"
                    or type(catalog["schema_version"]) is not int
                    or catalog["schema_version"] != 1
                    or not isinstance(catalog["versions"], dict)
                    or not 1 <= len(catalog["versions"]) <= 2):
                raise ValueError("invalid type catalog")
            if digest not in catalog["versions"]:
                raise ValueError("CSV content no longer matches its type record; reimport the edited CSV explicitly")
            schema = catalog["versions"][digest]
            if schema is not None and not isinstance(schema, dict):
                raise ValueError("invalid type schema")
            return schema
        except (UnicodeError, ValueError) as error:
            raise TableServiceError(f"表格类型记录无效 {types_path}: {error}") from error

    @classmethod
    def _publish_types(cls, path: Path, versions: dict) -> None:
        """Durably publish old and candidate schemas before committing the CSV."""

        types_path = cls._types_path(path)
        descriptor, name = tempfile.mkstemp(prefix=f".{types_path.name}.tmp.", dir=path.parent)
        temp_path = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(cls._canonical_json({
                    "format": "easyqc-table-types", "schema_version": 1, "versions": versions,
                }))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, types_path)
            cls._fsync_directory(path.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _file_revision(path: Path) -> str | None:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        except FileNotFoundError:
            return None
        return digest.hexdigest()

    @contextmanager
    def _project_write_lock(self, project: Project) -> Iterator[Path]:
        """Reuse the project-scoped writer lease for table transactions."""

        lock_target = project.rating_dir / ".table-transaction.json"
        try:
            with rating_write_lock(lock_target) as lock_path:
                yield lock_path
        except RatingWriteLockError as exc:
            raise TableServiceError(
                f"cannot acquire the project table writer lock: {exc}"
            ) from exc

    @staticmethod
    def _validate_expected_revision(
        expected_revision: str | None | _UncheckedRevision,
    ) -> None:
        if isinstance(expected_revision, _UncheckedRevision):
            return
        if expected_revision is None:
            return
        if (
            not isinstance(expected_revision, str)
            or len(expected_revision) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_revision
            )
        ):
            raise ValueError(
                "expected table revision must be a lowercase SHA-256 hex "
                "string or None"
            )

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name != "posix":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(path, flags)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as exc:
            if exc.errno in {
                errno.EBADF,
                errno.EINVAL,
                getattr(errno, "ENOTSUP", errno.EINVAL),
            }:
                return
            raise

    def save_table(
        self,
        project: Project,
        table_type: str,
        df: pd.DataFrame | None,
        delete: bool = False,
        *,
        expected_revision: str | None | _UncheckedRevision = _UNCHECKED_REVISION,
    ) -> str | None:
        """Publish one table if its source revision is still current.

        Side effects are limited to the selected CSV/types JSON, temporary
        siblings and the shared project lock. The types catalog retains both
        current and candidate schemas: a failed CSV commit keeps the old types
        readable. A stale expected revision raises before any publication.
        """

        self._validate_expected_revision(expected_revision)
        path = self.table_path(project, table_type)
        with self._project_write_lock(project):
            project.table_dir.mkdir(parents=True, exist_ok=True)
            digest = self._file_revision(path)
            current_schema = self._read_types(path, digest) if digest is not None else None
            current_revision = self._typed_revision(digest, current_schema) if digest is not None else None
            if not isinstance(expected_revision, _UncheckedRevision):
                if current_revision != expected_revision:
                    raise TableStateConflictError(
                        f"table state is stale for {path}: expected "
                        f"{expected_revision!r}, current {current_revision!r}"
                    )

            if delete:
                if path.exists():
                    path.unlink()
                self._types_path(path).unlink(missing_ok=True)
                self._fsync_directory(path.parent)
                return None

            if df is None:
                return current_revision

            schema = describe_table_types(df)
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.tmp.",
                dir=path.parent,
            )
            os.close(descriptor)
            temp_path = Path(temp_name)
            try:
                df.to_csv(temp_path, index=False, encoding="utf-8")
                self._fsync_file(temp_path)
                candidate_digest = self._file_revision(temp_path)
                assert candidate_digest is not None
                versions = {digest: current_schema} if digest is not None else {}
                versions[candidate_digest] = schema
                self._publish_types(path, versions)
                # Equal CSV bytes can still have different text/number types.
                # In that case the atomic JSON replace is the sole commit.
                if candidate_digest != digest:
                    os.replace(temp_path, path)
                    self._fsync_directory(path.parent)
                return self._typed_revision(candidate_digest, schema)
            finally:
                temp_path.unlink(missing_ok=True)

    def load_all_tables(self, project: Project) -> dict[str, pd.DataFrame]:
        tables: dict[str, pd.DataFrame] = {}
        if not project.table_dir.exists():
            return tables

        for path in project.table_dir.glob("easyqc_*.csv"):
            table = self.load_table(project, path.stem)
            if table is not None:
                tables[path.stem] = table
        return tables

    def load_state_tables(
        self,
        project: Project,
        module_names: list[str] | tuple[str, ...] | None = None,
    ) -> LoadedProjectTables:
        """Load project tables into the toolkit-neutral session-state shape."""

        variables: dict[str, pd.DataFrame | None] = {
            TABLE_ALL: self.load_table(project, TABLE_ALL),
        }
        results: dict[str, pd.DataFrame | None] = {
            TABLE_QCTABLE: self.load_table(project, TABLE_QCTABLE),
            TABLE_QCTABLE_FILTER: self.load_table(project, TABLE_QCTABLE_FILTER),
        }

        if project.table_dir.exists():
            for path in sorted(project.table_dir.glob("easyqc_*.csv")):
                table_type = path.stem
                if table_type in {TABLE_ALL, TABLE_QCTABLE, TABLE_QCTABLE_FILTER}:
                    continue
                results[self.module_name_from_table_type(table_type)] = self.load_table(project, table_type)

        for module_name in module_names or ():
            results.setdefault(module_name, None)

        return LoadedProjectTables(variables=variables, results=results)

    @staticmethod
    def module_name_from_table_type(table_type: str) -> str:
        return table_type.removeprefix("easyqc_")


__all__ = [
    "TABLE_ALL",
    "TABLE_QCTABLE",
    "TABLE_QCTABLE_FILTER",
    "LoadedProjectTables",
    "TableServiceError",
    "TableSnapshot",
    "TableStateConflictError",
    "TableService",
]
