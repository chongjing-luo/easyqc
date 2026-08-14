"""Toolkit-neutral capture and journaling for external viewer output."""

from __future__ import annotations

import codecs
import os
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Literal


EventKind = Literal["start", "command", "stdout", "stderr", "exit", "warning"]
_ANSI_ESCAPE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))"
)


@dataclass(frozen=True)
class ViewerExecutionContext:
    """Describe the QC identity associated with one rendered command."""

    module_name: str
    rater: str
    easyqcid: str
    command_index: int


@dataclass(frozen=True)
class CommandOutputEvent:
    """Canonical immutable record consumed by logs and presentation layers."""

    sequence: int
    timestamp: datetime
    execution_id: str
    kind: EventKind
    text: str
    context: ViewerExecutionContext
    returncode: int | None = None


@dataclass(frozen=True)
class CommandOutputStatus:
    """Expose journal health without leaking mutable capture internals."""

    session_id: str
    file_logging_enabled: bool
    session_log_path: Path | None
    warning_message: str | None = None


@dataclass(frozen=True)
class CommandOutputBatch:
    """One non-destructive sequence-cursor view of canonical events."""

    events: tuple[CommandOutputEvent, ...]
    next_sequence: int
    truncated: bool
    status: CommandOutputStatus


@dataclass
class CommandCapture:
    """Writable child handles returned by one begin-execution operation."""

    execution_id: str
    stdout_path: Path
    stderr_path: Path
    stdout_handle: BinaryIO
    stderr_handle: BinaryIO

    def close_parent_handles(self) -> None:
        """Close only EasyQC's writable copies after the child inherits them."""

        for handle in (self.stdout_handle, self.stderr_handle):
            if not handle.closed:
                handle.close()


@dataclass
class _StreamState:
    path: Path
    offset: int = 0
    pending: str = ""
    decoder: codecs.IncrementalDecoder = field(
        default_factory=lambda: codecs.getincrementaldecoder("utf-8")(
            errors="replace"
        )
    )


@dataclass
class _ExecutionState:
    capture: CommandCapture
    context: ViewerExecutionContext
    command: str
    stdout: _StreamState
    stderr: _StreamState
    pid: int | None = None
    finalized: bool = False
    returncode: int | None = None


class CommandOutputJournal:
    """Normalize viewer output into events and one local session journal.

    Input is one execution context plus append-only stdout/stderr files. Output
    is immutable sequence-addressed events. Side effects are limited to the
    supplied log root. Interactive input or binary-artifact capture is a split
    trigger and belongs in a separate adapter.
    """

    def __init__(
        self,
        *,
        log_root: str | os.PathLike[str],
        retention_days: int = 14,
        max_events: int = 6000,
        read_budget_bytes: int = 256 * 1024,
    ) -> None:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        if max_events < 1:
            raise ValueError("max_events must be positive")
        if read_budget_bytes < 1:
            raise ValueError("read_budget_bytes must be positive")
        self.retention_days = retention_days
        self.read_budget_bytes = read_budget_bytes
        self.session_id = datetime.now().astimezone().strftime(
            "%Y%m%d_%H%M%S_"
        ) + str(os.getpid())
        self.log_dir = Path(log_root) / "viewer-commands"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_log_path = (
            self.log_dir / f"viewer_commands_{self.session_id}.log"
        )
        self._log_handle = self.session_log_path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        )
        self._spool_dir = self.log_dir / f".spool_{self.session_id}"
        self._spool_dir.mkdir(parents=True, exist_ok=False)
        self._events: deque[CommandOutputEvent] = deque(maxlen=max_events)
        self._executions: dict[str, _ExecutionState] = {}
        self._sequence = 0
        self._execution_counter = 0
        self._closed = False

    def begin_execution(
        self,
        context: ViewerExecutionContext,
        command: str,
    ) -> CommandCapture:
        """Create one capture and emit its START/CMD records."""

        self._require_open()
        if not isinstance(context, ViewerExecutionContext):
            raise TypeError("context must be ViewerExecutionContext")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("command must be non-empty text")
        self._execution_counter += 1
        execution_id = f"C{self._execution_counter:03d}"
        stdout_path = self._spool_dir / f"{execution_id}.stdout"
        stderr_path = self._spool_dir / f"{execution_id}.stderr"
        capture = CommandCapture(
            execution_id=execution_id,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            stdout_handle=stdout_path.open("w+b", buffering=0),
            stderr_handle=stderr_path.open("w+b", buffering=0),
        )
        self._executions[execution_id] = _ExecutionState(
            capture=capture,
            context=context,
            command=command,
            stdout=_StreamState(stdout_path),
            stderr=_StreamState(stderr_path),
        )
        self._emit(
            execution_id,
            "start",
            (
                f"module={context.module_name} rater={context.rater} "
                f"easyqcid={context.easyqcid} index={context.command_index}"
            ),
        )
        self._emit(execution_id, "command", command)
        return capture

    def mark_started(self, execution_id: str, pid: int) -> None:
        """Record the child PID and close EasyQC's inherited writer copies."""

        state = self._state(execution_id)
        if not isinstance(pid, int) or pid <= 0:
            raise ValueError("pid must be a positive integer")
        state.pid = pid
        state.capture.close_parent_handles()

    def poll(self) -> tuple[CommandOutputEvent, ...]:
        """Read a bounded increment from every active capture."""

        self._require_open()
        before = self._sequence
        remaining = self.read_budget_bytes
        for state in self._executions.values():
            if remaining <= 0:
                break
            for kind, stream in (
                ("stdout", state.stdout),
                ("stderr", state.stderr),
            ):
                if remaining <= 0:
                    break
                consumed = self._read_stream(
                    state,
                    kind,
                    stream,
                    remaining,
                    final=False,
                )
                remaining -= consumed
        return tuple(event for event in self._events if event.sequence > before)

    def finalize(
        self,
        execution_id: str,
        returncode: int,
    ) -> tuple[CommandOutputEvent, ...]:
        """Drain one completed capture and emit exactly one EXIT record."""

        self._require_open()
        state = self._state(execution_id)
        if state.finalized:
            if state.returncode != returncode:
                raise ValueError("execution already finalized with another code")
            return ()
        before = self._sequence
        for kind, stream in (
            ("stdout", state.stdout),
            ("stderr", state.stderr),
        ):
            while self._read_stream(
                state,
                kind,
                stream,
                self.read_budget_bytes,
                final=False,
            ):
                pass
            self._read_stream(
                state,
                kind,
                stream,
                self.read_budget_bytes,
                final=True,
            )
        state.finalized = True
        state.returncode = int(returncode)
        self._emit(
            execution_id,
            "exit",
            f"code={returncode}",
            returncode=int(returncode),
        )
        return tuple(event for event in self._events if event.sequence > before)

    def events_since(self, after_sequence: int) -> CommandOutputBatch:
        """Return events after one cursor without consuming shared history."""

        self._require_open()
        if not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError("after_sequence must be a non-negative integer")
        first_sequence = self._events[0].sequence if self._events else None
        truncated = (
            first_sequence is not None
            and after_sequence < first_sequence - 1
        )
        events = tuple(
            event for event in self._events if event.sequence > after_sequence
        )
        return CommandOutputBatch(
            events=events,
            next_sequence=self._sequence,
            truncated=truncated,
            status=self.status(),
        )

    def status(self) -> CommandOutputStatus:
        """Return the immutable persistence status for presentation."""

        return CommandOutputStatus(
            session_id=self.session_id,
            file_logging_enabled=not self._closed,
            session_log_path=self.session_log_path,
        )

    def close(self) -> None:
        """Close parent-owned resources without terminating child processes."""

        if self._closed:
            return
        for state in self._executions.values():
            state.capture.close_parent_handles()
        self._log_handle.flush()
        self._log_handle.close()
        self._closed = True

    def _read_stream(
        self,
        execution: _ExecutionState,
        kind: Literal["stdout", "stderr"],
        stream: _StreamState,
        budget: int,
        *,
        final: bool,
    ) -> int:
        data = b""
        if budget > 0:
            with stream.path.open("rb") as handle:
                handle.seek(stream.offset)
                data = handle.read(budget)
            stream.offset += len(data)
        decoded = stream.decoder.decode(data, final=final)
        stream.pending += decoded
        lines = stream.pending.splitlines(keepends=True)
        stream.pending = ""
        for line in lines:
            if line.endswith(("\n", "\r")):
                self._emit(
                    execution.capture.execution_id,
                    kind,
                    self._sanitize(line.rstrip("\r\n")),
                )
            else:
                stream.pending = line
        if final and stream.pending:
            self._emit(
                execution.capture.execution_id,
                kind,
                self._sanitize(stream.pending),
            )
            stream.pending = ""
        return len(data)

    def _emit(
        self,
        execution_id: str,
        kind: EventKind,
        text: str,
        *,
        returncode: int | None = None,
    ) -> CommandOutputEvent:
        state = self._state(execution_id)
        self._sequence += 1
        event = CommandOutputEvent(
            sequence=self._sequence,
            timestamp=datetime.now().astimezone(),
            execution_id=execution_id,
            kind=kind,
            text=text,
            context=state.context,
            returncode=returncode,
        )
        self._events.append(event)
        self._log_handle.write(self._format_event(event) + "\n")
        self._log_handle.flush()
        return event

    @staticmethod
    def _format_event(event: CommandOutputEvent) -> str:
        timestamp = event.timestamp.isoformat(timespec="milliseconds")
        return (
            f"[{timestamp}] [{event.execution_id}] "
            f"{event.kind.upper():7s} {event.text}"
        )

    @staticmethod
    def _sanitize(text: str) -> str:
        without_ansi = _ANSI_ESCAPE.sub("", text)
        return "".join(
            character if character.isprintable() or character == "\t" else " "
            for character in without_ansi
        )

    def _state(self, execution_id: str) -> _ExecutionState:
        try:
            return self._executions[execution_id]
        except KeyError as exc:
            raise KeyError(f"unknown execution_id: {execution_id}") from exc

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("command output journal is closed")


__all__ = [
    "CommandCapture",
    "CommandOutputBatch",
    "CommandOutputEvent",
    "CommandOutputJournal",
    "CommandOutputStatus",
    "ViewerExecutionContext",
]
