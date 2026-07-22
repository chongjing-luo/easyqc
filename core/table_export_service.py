"""Bounded, atomic CSV export for one applied Table result snapshot."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event

from core.table_view_service import TableViewService
from models.table_view_state import TableViewResult


DEFAULT_EXPORT_CHUNK_ROWS = 1_000
MAX_EXPORT_CHUNK_ROWS = 10_000


class TableExportError(RuntimeError):
    """Raised when a derived Table export cannot be committed safely."""


class TableExportCancelled(TableExportError):
    """Raised after a cooperative export cancellation request."""


@dataclass(frozen=True)
class ExportReceipt:
    """Describe the exact CSV bytes committed by one export."""

    destination: Path
    rows: int
    columns: tuple[str, ...]
    state_revision: int
    sha256: str


class TableExportService:
    """Export bounded windows from one stable ``TableViewService`` snapshot."""

    def __init__(self, table_service: TableViewService) -> None:
        if not isinstance(table_service, TableViewService):
            raise TypeError("table_service must be a TableViewService")
        self._table_service = table_service

    def export_applied_csv(
        self,
        result: TableViewResult,
        visible_columns: Sequence[str],
        destination: str | os.PathLike[str],
        cancel_event: Event | None = None,
        progress: Callable[[int, int], None] | None = None,
        *,
        chunk_size: int = DEFAULT_EXPORT_CHUNK_ROWS,
    ) -> ExportReceipt:
        """Write one complete applied result and atomically publish its receipt."""

        request = self._validate_request(
            result,
            visible_columns,
            destination,
            cancel_event,
            progress,
            chunk_size,
        )
        columns, output_path, token, progress_callback, rows_per_chunk = request
        self._raise_if_cancelled(token)
        temporary_path: Path | None = None

        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                prefix=f".{output_path.name}.",
                suffix=".partial",
                dir=output_path.parent,
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                if result.matched_total:
                    for offset in range(0, result.matched_total, rows_per_chunk):
                        self._raise_if_cancelled(token)
                        window = self._table_service.get_window(
                            result,
                            offset,
                            rows_per_chunk,
                            columns,
                        )
                        window.dataframe.to_csv(
                            output,
                            index=False,
                            header=offset == 0,
                            lineterminator="\n",
                        )
                        completed = min(
                            result.matched_total,
                            offset + len(window.dataframe),
                        )
                        progress_callback(completed, result.matched_total)
                        self._raise_if_cancelled(token)
                else:
                    empty = self._table_service.get_window(
                        result,
                        0,
                        1,
                        columns,
                    )
                    empty.dataframe.to_csv(
                        output,
                        index=False,
                        header=True,
                        lineterminator="\n",
                    )
                    progress_callback(0, 0)
                    self._raise_if_cancelled(token)
                output.flush()
                os.fsync(output.fileno())

            self._raise_if_cancelled(token)
            output_hash = self._sha256_file(temporary_path)
            self._raise_if_cancelled(token)
            os.replace(temporary_path, output_path)
            return ExportReceipt(
                destination=output_path,
                rows=result.matched_total,
                columns=columns,
                state_revision=result.state.revision,
                sha256=output_hash,
            )
        except TableExportError:
            raise
        except Exception as exc:
            raise TableExportError(f"Table export failed: {exc}") from exc
        finally:
            if temporary_path is not None and temporary_path.exists():
                try:
                    temporary_path.unlink()
                except OSError as exc:
                    raise TableExportError(
                        f"Table export partial cleanup failed: {exc}"
                    ) from exc

    @staticmethod
    def _validate_request(
        result: TableViewResult,
        visible_columns: Sequence[str],
        destination: str | os.PathLike[str],
        cancel_event: Event | None,
        progress: Callable[[int, int], None] | None,
        chunk_size: int,
    ) -> tuple[tuple[str, ...], Path, Event, Callable[[int, int], None], int]:
        if not isinstance(result, TableViewResult):
            raise TableExportError("result must be a TableViewResult")
        columns = tuple(visible_columns)
        if not columns:
            raise TableExportError("at least one visible column is required")
        if any(not isinstance(column, str) for column in columns):
            raise TableExportError("visible column names must be strings")
        if len(set(columns)) != len(columns):
            raise TableExportError("visible columns cannot contain duplicates")
        if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
            raise TableExportError("chunk_size must be an integer")
        if chunk_size <= 0 or chunk_size > MAX_EXPORT_CHUNK_ROWS:
            raise TableExportError(
                f"chunk_size must be between 1 and {MAX_EXPORT_CHUNK_ROWS}"
            )
        try:
            output_path = Path(destination)
        except TypeError as exc:
            raise TableExportError("destination must be a filesystem path") from exc
        if not output_path.parent.is_dir():
            raise TableExportError("destination directory does not exist")
        if output_path.exists() and not output_path.is_file():
            raise TableExportError("destination must not be a directory")
        token = cancel_event if cancel_event is not None else Event()
        if not callable(getattr(token, "is_set", None)):
            raise TableExportError("cancel_event must provide is_set()")
        if progress is not None and not callable(progress):
            raise TableExportError("progress must be callable")
        progress_callback = progress if progress is not None else lambda _done, _total: None
        return columns, output_path, token, progress_callback, chunk_size

    @staticmethod
    def _raise_if_cancelled(cancel_event: Event) -> None:
        if cancel_event.is_set():
            raise TableExportCancelled("Table export cancelled")

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()


__all__ = [
    "DEFAULT_EXPORT_CHUNK_ROWS",
    "MAX_EXPORT_CHUNK_ROWS",
    "ExportReceipt",
    "TableExportCancelled",
    "TableExportError",
    "TableExportService",
]
