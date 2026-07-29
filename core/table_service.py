from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
from io import BytesIO
import os
from dataclasses import dataclass
from pathlib import Path
import tempfile
from typing import Final, Iterator

import pandas as pd

from core.rating_write_lock import RatingWriteLockError, rating_write_lock
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
        path = self.table_path(project, table_type)
        return self._read_snapshot(path).dataframe

    def load_table_snapshot(
        self,
        project: Project,
        table_type: str,
    ) -> TableSnapshot:
        """Read one DataFrame and revision under the project writer lease."""

        path = self.table_path(project, table_type)
        with self._project_write_lock(project):
            return self._read_snapshot(path)

    @staticmethod
    def _read_snapshot(path: Path) -> TableSnapshot:
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            return TableSnapshot(dataframe=None, revision=None)
        return TableSnapshot(
            dataframe=pd.read_csv(
                BytesIO(payload),
                encoding="utf-8",
                converters={"easyqcid": lambda value: value},
            ),
            revision=hashlib.sha256(payload).hexdigest(),
        )

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

        Side effects are limited to the selected CSV, one unique temporary
        sibling and the shared project lock file. A stale expected revision
        raises before the target CSV is changed.
        """

        self._validate_expected_revision(expected_revision)
        path = self.table_path(project, table_type)
        with self._project_write_lock(project):
            project.table_dir.mkdir(parents=True, exist_ok=True)
            current_revision: str | None = None
            if not isinstance(expected_revision, _UncheckedRevision):
                current_revision = self._file_revision(path)
                if current_revision != expected_revision:
                    raise TableStateConflictError(
                        f"table state is stale for {path}: expected "
                        f"{expected_revision!r}, current {current_revision!r}"
                    )

            if delete:
                if path.exists():
                    path.unlink()
                    self._fsync_directory(path.parent)
                return None

            if df is None:
                return (
                    current_revision
                    if not isinstance(expected_revision, _UncheckedRevision)
                    else self._file_revision(path)
                )

            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{path.name}.tmp.",
                dir=path.parent,
            )
            os.close(descriptor)
            temp_path = Path(temp_name)
            try:
                df.to_csv(temp_path, index=False, encoding="utf-8")
                self._fsync_file(temp_path)
                revision = self._file_revision(temp_path)
                os.replace(temp_path, path)
                self._fsync_directory(path.parent)
                return revision
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
        """Load project tables in the shape expected by the legacy GUI state."""

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
