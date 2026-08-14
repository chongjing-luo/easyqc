"""Toolkit-neutral capture and journaling for external viewer output."""

from __future__ import annotations

import codecs
import os
import re
import shutil
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Literal, TextIO

from utils.logger import resolve_log_dir


EventKind = Literal["start", "command", "stdout", "stderr", "exit", "warning"]
_ANSI_ESCAPE = re.compile(
    r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))"
)
_OWNED_SESSION_LOG = re.compile(
    r"^viewer_commands_\d{8}_\d{6}_\d+\.log$"
)
_OWNED_SPOOL_DIRECTORY = re.compile(r"^\.spool_\d{8}_\d{6}_\d+$")
_MAX_EVENT_TEXT_CHARACTERS = 64 * 1024
_SECONDS_PER_DAY = 24 * 60 * 60


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
        log_root: str | os.PathLike[str] | None = None,
        retention_days: int = 14,
        max_events: int = 6000,
        read_budget_bytes: int = 256 * 1024,
    ) -> None:
        if not isinstance(retention_days, int) or isinstance(retention_days, bool):
            raise TypeError("retention_days must be an integer")
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        if not isinstance(max_events, int) or isinstance(max_events, bool):
            raise TypeError("max_events must be an integer")
        if max_events < 1:
            raise ValueError("max_events must be positive")
        if not isinstance(read_budget_bytes, int) or isinstance(
            read_budget_bytes,
            bool,
        ):
            raise TypeError("read_budget_bytes must be an integer")
        if read_budget_bytes < 1:
            raise ValueError("read_budget_bytes must be positive")
        self.retention_days = retention_days
        self.read_budget_bytes = read_budget_bytes
        self.session_id = datetime.now().astimezone().strftime(
            "%Y%m%d_%H%M%S_"
        ) + str(os.getpid())
        self._events: deque[CommandOutputEvent] = deque(maxlen=max_events)
        self._executions: dict[str, _ExecutionState] = {}
        self._sequence = 0
        self._execution_counter = 0
        self._closed = False
        self._file_logging_enabled = False
        self._log_handle: TextIO | None = None
        self._warning_keys: set[str] = set()
        self._warning_messages: list[str] = []
        self._pending_warning_events: list[str] = []

        persistent_root, root_problem = self._persistent_root(log_root)
        if persistent_root is not None:
            try:
                self.log_dir = persistent_root / "viewer-commands"
                self.log_dir.mkdir(parents=True, exist_ok=True)
                self._cleanup_expired_artifacts(self.log_dir)
                self._spool_dir = self.log_dir / f".spool_{self.session_id}"
                self._spool_dir.mkdir(parents=True, exist_ok=False)
                self.session_log_path: Path | None = (
                    self.log_dir
                    / f"viewer_commands_{self.session_id}.log"
                )
                self._log_handle = self.session_log_path.open(
                    "a",
                    encoding="utf-8",
                    newline="\n",
                )
            except OSError as exc:
                root_problem = (
                    "persistent command-output storage could not be created "
                    f"({type(exc).__name__})"
                )
            else:
                self._file_logging_enabled = True
                return

        self._configure_temporary_capture(
            root_problem or "persistent log root is unavailable"
        )

    def begin_execution(
        self,
        context: ViewerExecutionContext,
        command: str,
    ) -> CommandCapture:
        """Create one capture and emit its START/CMD records."""

        self._require_open()
        if not isinstance(context, ViewerExecutionContext):
            raise TypeError("context must be ViewerExecutionContext")
        self._validate_context(context)
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
        self._flush_pending_warnings(execution_id)
        return capture

    def mark_started(self, execution_id: str, pid: int) -> None:
        """Record the child PID and close EasyQC's inherited writer copies."""

        state = self._state(execution_id)
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ValueError("pid must be a positive integer")
        state.pid = pid
        state.capture.close_parent_handles()

    def poll(self) -> tuple[CommandOutputEvent, ...]:
        """Read a bounded increment from every active capture."""

        self._require_open()
        before = self._sequence
        remaining = self.read_budget_bytes
        streams = [
            (state, kind, stream)
            for state in self._executions.values()
            if not state.finalized
            for kind, stream in (
                ("stdout", state.stdout),
                ("stderr", state.stderr),
            )
        ]
        for index, (state, kind, stream) in enumerate(streams):
            if remaining <= 0:
                break
            fair_share = max(1, remaining // (len(streams) - index))
            consumed = self._read_stream(
                state,
                kind,
                stream,
                fair_share,
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
        if not isinstance(returncode, int) or isinstance(returncode, bool):
            raise TypeError("returncode must be an integer")
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
        state.returncode = returncode
        self._emit(
            execution_id,
            "exit",
            f"code={returncode}",
            returncode=returncode,
        )
        return tuple(event for event in self._events if event.sequence > before)

    def events_since(self, after_sequence: int) -> CommandOutputBatch:
        """Return events after one cursor without consuming shared history."""

        self._require_open()
        if (
            not isinstance(after_sequence, int)
            or isinstance(after_sequence, bool)
            or after_sequence < 0
        ):
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
            file_logging_enabled=self._file_logging_enabled,
            session_log_path=self.session_log_path,
            warning_message=(
                "\n".join(self._warning_messages)
                if self._warning_messages
                else None
            ),
        )

    def close(self) -> None:
        """Close parent-owned resources without terminating child processes."""

        if self._closed:
            return
        for state in self._executions.values():
            state.capture.close_parent_handles()
        self._remove_completed_spool()
        if self._log_handle is not None:
            try:
                self._log_handle.flush()
                self._log_handle.close()
            except (OSError, ValueError) as exc:
                self._disable_file_logging(
                    "Command output persistence failed while closing "
                    f"({type(exc).__name__})."
                )
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
            try:
                with stream.path.open("rb") as handle:
                    handle.seek(stream.offset)
                    data = handle.read(budget)
            except OSError as exc:
                self._warn_once(
                    f"capture-read:{execution.capture.execution_id}:{kind}",
                    (
                        f"Unable to read {kind} capture "
                        f"({type(exc).__name__}); polling will retry."
                    ),
                    execution_id=execution.capture.execution_id,
                )
                return 0
            stream.offset += len(data)
        invalid_utf8 = self._contains_invalid_utf8(
            stream.decoder,
            data,
            final=final,
        )
        decoded = stream.decoder.decode(data, final=final)
        if invalid_utf8:
            self._warn_once(
                f"invalid-utf8:{execution.capture.execution_id}:{kind}",
                f"Invalid UTF-8 bytes in {kind} were replaced.",
                execution_id=execution.capture.execution_id,
            )
        stream.pending += decoded
        complete_lines, stream.pending = self._split_cr_lf_records(
            stream.pending,
            final=final,
        )
        for line in complete_lines:
            self._emit_record_chunks(
                execution.capture.execution_id,
                kind,
                line,
            )
        while len(stream.pending) > _MAX_EVENT_TEXT_CHARACTERS:
            self._emit_record_chunks(
                execution.capture.execution_id,
                kind,
                stream.pending[:_MAX_EVENT_TEXT_CHARACTERS],
            )
            stream.pending = stream.pending[_MAX_EVENT_TEXT_CHARACTERS:]
        if final and stream.pending:
            self._emit_record_chunks(
                execution.capture.execution_id,
                kind,
                stream.pending,
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
            text=self._sanitize(text),
            context=state.context,
            returncode=returncode,
        )
        self._events.append(event)
        self._append_to_session_log(event)
        return event

    def _emit_record_chunks(
        self,
        execution_id: str,
        kind: Literal["stdout", "stderr"],
        text: str,
    ) -> None:
        if not text:
            self._emit(execution_id, kind, "")
            return
        for start in range(0, len(text), _MAX_EVENT_TEXT_CHARACTERS):
            self._emit(
                execution_id,
                kind,
                text[start : start + _MAX_EVENT_TEXT_CHARACTERS],
            )

    def _append_to_session_log(self, event: CommandOutputEvent) -> None:
        if self._log_handle is None:
            return
        try:
            self._log_handle.write(self._format_event(event) + "\n")
            self._log_handle.flush()
        except (OSError, ValueError) as exc:
            self._disable_file_logging(
                "Command output persistence failed "
                f"({type(exc).__name__}); current output remains in memory.",
                execution_id=event.execution_id,
            )

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

    @staticmethod
    def _contains_invalid_utf8(
        decoder: codecs.IncrementalDecoder,
        data: bytes,
        *,
        final: bool,
    ) -> bool:
        strict_decoder = codecs.getincrementaldecoder("utf-8")(
            errors="strict"
        )
        strict_decoder.setstate(decoder.getstate())
        try:
            strict_decoder.decode(data, final=final)
        except UnicodeDecodeError:
            return True
        return False

    @staticmethod
    def _split_cr_lf_records(
        text: str,
        *,
        final: bool,
    ) -> tuple[list[str], str]:
        records: list[str] = []
        record_start = 0
        index = 0
        while index < len(text):
            character = text[index]
            if character not in {"\r", "\n"}:
                index += 1
                continue
            if character == "\r" and index == len(text) - 1 and not final:
                break
            records.append(text[record_start:index])
            if (
                character == "\r"
                and index + 1 < len(text)
                and text[index + 1] == "\n"
            ):
                index += 2
            else:
                index += 1
            record_start = index
        return records, text[record_start:]

    def _state(self, execution_id: str) -> _ExecutionState:
        if not isinstance(execution_id, str) or not execution_id:
            raise TypeError("execution_id must be non-empty text")
        try:
            return self._executions[execution_id]
        except KeyError as exc:
            raise KeyError(f"unknown execution_id: {execution_id}") from exc

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("command output journal is closed")

    @staticmethod
    def _validate_context(context: ViewerExecutionContext) -> None:
        for field_name in ("module_name", "rater", "easyqcid"):
            value = getattr(context, field_name)
            if not isinstance(value, str):
                raise TypeError(f"context.{field_name} must be text")
        if not context.module_name.strip():
            raise ValueError("context.module_name must be non-empty text")
        if (
            not isinstance(context.command_index, int)
            or isinstance(context.command_index, bool)
            or context.command_index < 0
        ):
            raise ValueError(
                "context.command_index must be a non-negative integer"
            )

    @staticmethod
    def _persistent_root(
        log_root: str | os.PathLike[str] | None,
    ) -> tuple[Path | None, str | None]:
        if log_root is None:
            decision = resolve_log_dir()
            return decision.path, decision.problem
        try:
            return Path(log_root).expanduser().resolve(), None
        except (OSError, RuntimeError) as exc:
            return (
                None,
                "persistent log-root resolution failed "
                f"({type(exc).__name__})",
            )

    def _configure_temporary_capture(self, problem: str) -> None:
        try:
            self.log_dir = (
                Path(tempfile.gettempdir())
                / "EasyQC"
                / "viewer-commands"
            )
            self.log_dir.mkdir(parents=True, exist_ok=True)
            self._cleanup_expired_artifacts(self.log_dir)
            self._spool_dir = self.log_dir / f".spool_{self.session_id}"
            self._spool_dir.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise RuntimeError(
                "command output capture storage is unavailable"
            ) from exc
        self.session_log_path = None
        self._log_handle = None
        self._file_logging_enabled = False
        self._warn_once(
            "persistence-unavailable",
            (
                "Command output persistence is unavailable; current-run "
                f"capture is using temporary storage. Reason: {problem}."
            ),
        )

    def _cleanup_expired_artifacts(self, directory: Path) -> None:
        boundary = time.time() - (self.retention_days * _SECONDS_PER_DAY)
        try:
            artifacts = tuple(directory.iterdir())
        except OSError as exc:
            self._warn_once(
                "retention-scan",
                "Command output retention scan failed "
                f"({type(exc).__name__}).",
            )
            return
        for artifact in artifacts:
            is_owned_log = bool(_OWNED_SESSION_LOG.fullmatch(artifact.name))
            is_owned_spool = bool(
                _OWNED_SPOOL_DIRECTORY.fullmatch(artifact.name)
            )
            if not (is_owned_log or is_owned_spool) or artifact.is_symlink():
                continue
            try:
                if artifact.stat().st_mtime >= boundary:
                    continue
                if is_owned_log and artifact.is_file():
                    artifact.unlink()
                elif is_owned_spool and artifact.is_dir():
                    shutil.rmtree(artifact)
            except OSError as exc:
                self._warn_once(
                    f"retention-remove:{artifact.name}",
                    "An expired command-output artifact could not be removed "
                    f"({type(exc).__name__}).",
                )

    def _warn_once(
        self,
        key: str,
        message: str,
        *,
        execution_id: str | None = None,
    ) -> None:
        if key in self._warning_keys:
            return
        self._warning_keys.add(key)
        self._warning_messages.append(message)
        if execution_id is None:
            self._pending_warning_events.append(message)
        else:
            self._emit(execution_id, "warning", message)

    def _flush_pending_warnings(self, execution_id: str) -> None:
        pending = tuple(self._pending_warning_events)
        self._pending_warning_events.clear()
        for message in pending:
            self._emit(execution_id, "warning", message)

    def _disable_file_logging(
        self,
        message: str,
        *,
        execution_id: str | None = None,
    ) -> None:
        handle = self._log_handle
        self._log_handle = None
        self._file_logging_enabled = False
        self.session_log_path = None
        close_problem = ""
        if handle is not None:
            try:
                handle.close()
            except (OSError, ValueError) as exc:
                close_problem = (
                    " The failed log handle also could not be closed "
                    f"({type(exc).__name__})."
                )
        self._warn_once(
            "persistence-unavailable",
            message + close_problem,
            execution_id=execution_id,
        )

    def _remove_completed_spool(self) -> None:
        if any(not state.finalized for state in self._executions.values()):
            return
        try:
            shutil.rmtree(self._spool_dir)
        except FileNotFoundError:
            return
        except OSError as exc:
            self._warn_once(
                "spool-cleanup",
                "Completed command-output capture cleanup failed "
                f"({type(exc).__name__}).",
            )


__all__ = [
    "CommandCapture",
    "CommandOutputBatch",
    "CommandOutputEvent",
    "CommandOutputJournal",
    "CommandOutputStatus",
    "ViewerExecutionContext",
]
