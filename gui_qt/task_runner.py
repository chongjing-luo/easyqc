"""Revision-safe Qt worker orchestration for non-GUI Core calls."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


@dataclass
class _TaskToken:
    cancelled: bool = False


class _WorkerSignals(QObject):
    completed = Signal(object, int, object)
    failed = Signal(object, int, object)
    finished = Signal(object, int)


class _Worker(QRunnable):
    def __init__(
        self,
        token: _TaskToken,
        revision: int,
        function: Callable[[], Any],
    ) -> None:
        super().__init__()
        self.token = token
        self.revision = int(revision)
        self.function = function
        self.signals = _WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:
            self.signals.failed.emit(self.token, self.revision, exc)
        else:
            self.signals.completed.emit(self.token, self.revision, result)
        finally:
            self.signals.finished.emit(self.token, self.revision)


class RevisionedTaskController(QObject):
    """Serialize expensive calls and discard cancelled/stale results."""

    resultReady = Signal(int, object)
    errorRaised = Signal(int, object)
    busyChanged = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._current_token: _TaskToken | None = None
        self._current_revision: int | None = None
        self._busy = False
        self._workers: set[_Worker] = set()

    @property
    def busy(self) -> bool:
        return self._busy

    def submit(self, revision: int, function: Callable[[], Any]) -> None:
        if not callable(function):
            raise TypeError("Background task must be callable")
        if self._current_token is not None:
            self._current_token.cancelled = True
        token = _TaskToken()
        worker = _Worker(token, revision, function)
        worker.signals.completed.connect(self._handle_completed)
        worker.signals.failed.connect(self._handle_failed)
        worker.signals.finished.connect(self._handle_finished)
        self._workers.add(worker)
        self._current_token = token
        self._current_revision = int(revision)
        self._set_busy(True)
        self._pool.start(worker)

    def cancel(self) -> None:
        if self._current_token is not None:
            self._current_token.cancelled = True
        self._current_token = None
        self._current_revision = None
        self._set_busy(False)

    def _is_current(self, token: _TaskToken, revision: int) -> bool:
        return (
            token is self._current_token
            and not token.cancelled
            and revision == self._current_revision
        )

    @Slot(object, int, object)
    def _handle_completed(self, token: _TaskToken, revision: int, result: Any) -> None:
        if self._is_current(token, revision):
            self.resultReady.emit(revision, result)

    @Slot(object, int, object)
    def _handle_failed(self, token: _TaskToken, revision: int, error: Exception) -> None:
        if self._is_current(token, revision):
            self.errorRaised.emit(revision, error)

    @Slot(object, int)
    def _handle_finished(self, token: _TaskToken, revision: int) -> None:
        if self._is_current(token, revision):
            self._current_token = None
            self._current_revision = None
            self._set_busy(False)
        self._workers = {item for item in self._workers if item.signals is not self.sender()}

    def _set_busy(self, busy: bool) -> None:
        if self._busy == bool(busy):
            return
        self._busy = bool(busy)
        self.busyChanged.emit(self._busy)


__all__ = ["RevisionedTaskController"]
