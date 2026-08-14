from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from core import command_output as command_output_module
from core.command_output import CommandOutputJournal, ViewerExecutionContext


def _context(
    *,
    module_name: str = "AnatQC",
    rater: str = "rater1",
    easyqcid: str = "SUB001",
    command_index: int = 0,
) -> ViewerExecutionContext:
    return ViewerExecutionContext(
        module_name=module_name,
        rater=rater,
        easyqcid=easyqcid,
        command_index=command_index,
    )


def test_first_throughput_normalizes_real_subprocess_output_to_session_log(
    tmp_path,
) -> None:
    journal = CommandOutputJournal(log_root=tmp_path, retention_days=14)
    context = ViewerExecutionContext(
        module_name="AnatQC",
        rater="rater1",
        easyqcid="SUB001",
        command_index=0,
    )
    rendered_command = "fixture-viewer --subject SUB001"
    capture = journal.begin_execution(context, rendered_command)

    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import sys, time; "
                "print('volume loaded', flush=True); "
                "time.sleep(0.05); "
                "print('surface warning', file=sys.stderr, flush=True)"
            ),
        ],
        stdout=capture.stdout_handle,
        stderr=capture.stderr_handle,
        shell=False,
    )
    journal.mark_started(capture.execution_id, process.pid)
    returncode = process.wait(timeout=5)
    journal.poll()
    journal.finalize(capture.execution_id, returncode)

    first = journal.events_since(0)
    second = journal.events_since(0)
    kinds = [event.kind for event in first.events]

    assert returncode == 0
    assert kinds[:2] == ["start", "command"]
    assert "stdout" in kinds
    assert "stderr" in kinds
    assert kinds[-1] == "exit"
    assert {event.execution_id for event in first.events} == {
        capture.execution_id
    }
    assert first.events == second.events
    assert first.next_sequence == second.next_sequence
    assert any(event.text == "volume loaded" for event in first.events)
    assert any(event.text == "surface warning" for event in first.events)
    assert first.events[0].context == context
    assert first.events[-1].returncode == 0

    status = journal.status()
    assert status.file_logging_enabled is True
    assert status.session_log_path is not None
    log_text = status.session_log_path.read_text(encoding="utf-8")
    assert "fixture-viewer --subject SUB001" in log_text
    assert "volume loaded" in log_text
    assert "surface warning" in log_text
    assert "EXIT" in log_text

    journal.close()


def test_default_log_root_reuses_the_platform_log_resolver(
    tmp_path,
    monkeypatch,
) -> None:
    resolved_root = (tmp_path / "native-log-root").resolve()
    decision = SimpleNamespace(
        path=resolved_root,
        attempted_path=resolved_root,
        problem=None,
        source="platformdirs",
    )
    monkeypatch.setattr(
        command_output_module,
        "resolve_log_dir",
        lambda: decision,
        raising=False,
    )

    journal = CommandOutputJournal()

    assert journal.status().file_logging_enabled is True
    assert journal.status().session_log_path is not None
    assert journal.status().session_log_path.parent == (
        resolved_root / "viewer-commands"
    )
    journal.close()


def test_cleanup_removes_only_expired_owned_command_output_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    command_dir = tmp_path / "viewer-commands"
    command_dir.mkdir()
    old_log = command_dir / "viewer_commands_20000101_010101_123.log"
    boundary_log = command_dir / "viewer_commands_20000102_010101_123.log"
    recent_log = command_dir / "viewer_commands_20990101_010101_123.log"
    unrelated_log = command_dir / "viewer_commands_manual.log"
    old_spool = command_dir / ".spool_20000101_010101_123"
    recent_spool = command_dir / ".spool_20990101_010101_123"
    unrelated_spool = command_dir / ".spool_manual"
    old_log.write_text("old", encoding="utf-8")
    boundary_log.write_text("boundary", encoding="utf-8")
    recent_log.write_text("recent", encoding="utf-8")
    unrelated_log.write_text("unrelated", encoding="utf-8")
    for directory in (old_spool, recent_spool, unrelated_spool):
        directory.mkdir()
        (directory / "capture.stdout").write_text("output", encoding="utf-8")

    now = 2_000_000_000.0
    monkeypatch.setattr(command_output_module.time, "time", lambda: now)
    boundary_timestamp = now - (14 * 24 * 60 * 60)
    old_timestamp = boundary_timestamp - 1
    recent_timestamp = boundary_timestamp + 1
    for path in (old_log, old_spool):
        os.utime(path, (old_timestamp, old_timestamp))
    for path in (recent_log, recent_spool):
        os.utime(path, (recent_timestamp, recent_timestamp))
    os.utime(boundary_log, (boundary_timestamp, boundary_timestamp))
    os.utime(unrelated_log, (old_timestamp, old_timestamp))
    os.utime(unrelated_spool, (old_timestamp, old_timestamp))

    journal = CommandOutputJournal(log_root=tmp_path, retention_days=14)

    assert not old_log.exists()
    assert not old_spool.exists()
    assert boundary_log.read_text(encoding="utf-8") == "boundary"
    assert recent_log.read_text(encoding="utf-8") == "recent"
    assert recent_spool.is_dir()
    assert unrelated_log.read_text(encoding="utf-8") == "unrelated"
    assert unrelated_spool.is_dir()
    journal.close()


def test_unavailable_persistent_root_degrades_to_temp_with_one_warning(
    tmp_path,
) -> None:
    unavailable_root = tmp_path / "not-a-directory"
    unavailable_root.write_text("occupied", encoding="utf-8")

    journal = CommandOutputJournal(log_root=unavailable_root)
    first = journal.begin_execution(_context(), "viewer first")
    second = journal.begin_execution(
        _context(easyqcid="SUB002", command_index=1),
        "viewer second",
    )
    first.stdout_handle.write(b"first output\n")
    second.stderr_handle.write(b"second warning\n")
    first.close_parent_handles()
    second.close_parent_handles()
    journal.finalize(first.execution_id, 0)
    journal.finalize(second.execution_id, 0)

    status = journal.status()
    warnings = [
        event for event in journal.events_since(0).events
        if event.kind == "warning" and "persistence" in event.text.lower()
    ]
    assert status.file_logging_enabled is False
    assert status.session_log_path is None
    assert status.warning_message is not None
    assert "persistence" in status.warning_message.lower()
    assert len(warnings) == 1
    assert first.stdout_path.exists()
    assert not str(first.stdout_path).startswith(str(unavailable_root))
    assert any(
        event.kind == "stdout" and event.text == "first output"
        for event in journal.events_since(0).events
    )
    journal.close()


def test_invalid_utf8_controls_partial_and_long_lines_normalize_without_loss(
    tmp_path,
) -> None:
    journal = CommandOutputJournal(
        log_root=tmp_path,
        read_budget_bytes=8 * 1024,
        max_events=100,
    )
    capture = journal.begin_execution(_context(), "viewer unsafe-output")
    capture.stdout_handle.write(
        b"\x1b[31mred\x1b[0m\tok\x00bell\x07bad:\xff\npartial"
    )

    initial = journal.poll()

    assert [event.text for event in initial if event.kind == "stdout"] == [
        "red\tok bell bad:\ufffd"
    ]
    long_tail = "x" * 200_000
    capture.stdout_handle.write((" tail-" + long_tail).encode("utf-8"))
    capture.close_parent_handles()
    journal.finalize(capture.execution_id, 0)
    valid_replacement = journal.begin_execution(
        _context(easyqcid="SUB002", command_index=1),
        "viewer valid-replacement-character",
    )
    valid_replacement.stdout_handle.write(
        "literal \ufffd character\n".encode("utf-8")
    )
    valid_replacement.close_parent_handles()
    journal.finalize(valid_replacement.execution_id, 0)

    events = journal.events_since(0).events
    output_texts = [
        event.text for event in events
        if event.execution_id == capture.execution_id
        and event.kind == "stdout"
    ]
    decode_warnings = [
        event for event in events
        if event.kind == "warning" and "utf-8" in event.text.lower()
    ]
    assert "\x1b" not in "".join(output_texts)
    assert "\x00" not in "".join(output_texts)
    assert "\x07" not in "".join(output_texts)
    assert output_texts[0] == "red\tok bell bad:\ufffd"
    assert "".join(output_texts[1:]) == "partial tail-" + long_tail
    assert len(output_texts) > 3
    assert max(len(text) for text in output_texts) <= 64 * 1024
    assert len(decode_warnings) == 1
    assert decode_warnings[0].execution_id == capture.execution_id
    journal.close()


def test_only_cr_lf_delimit_records_and_other_line_controls_do_not_drop_text(
    tmp_path,
) -> None:
    journal = CommandOutputJournal(log_root=tmp_path)
    capture = journal.begin_execution(_context(), "viewer line-controls")
    capture.stdout_handle.write(
        "alpha\x0bbeta\x0cgamma\u0085delta\n".encode("utf-8")
    )
    capture.close_parent_handles()
    journal.finalize(capture.execution_id, 0)

    output_texts = [
        event.text
        for event in journal.events_since(0).events
        if event.kind == "stdout"
    ]
    assert output_texts == ["alpha beta gamma delta"]
    journal.close()


def test_newline_terminated_long_record_is_chunked_without_loss(
    tmp_path,
) -> None:
    journal = CommandOutputJournal(log_root=tmp_path)
    capture = journal.begin_execution(_context(), "viewer long-record")
    long_record = "z" * 200_000
    capture.stdout_handle.write((long_record + "\n").encode("utf-8"))
    capture.close_parent_handles()
    journal.finalize(capture.execution_id, 0)

    output_texts = [
        event.text
        for event in journal.events_since(0).events
        if event.kind == "stdout"
    ]
    assert "".join(output_texts) == long_record
    assert len(output_texts) > 3
    assert max(len(text) for text in output_texts) <= 64 * 1024
    journal.close()


def test_start_and_command_are_canonical_safe_text_without_mutating_command(
    tmp_path,
) -> None:
    journal = CommandOutputJournal(log_root=tmp_path)
    context = _context(module_name="Anat\x0bQC")
    rendered_command = "\x1b[31mviewer\x1b[0m first\nsecond\x00"

    capture = journal.begin_execution(context, rendered_command)
    capture.close_parent_handles()
    journal.finalize(capture.execution_id, 0)

    events = journal.events_since(0).events
    start_event = next(event for event in events if event.kind == "start")
    command_event = next(event for event in events if event.kind == "command")
    assert start_event.text.startswith("module=Anat QC ")
    assert command_event.text == "viewer first second "
    assert journal._executions[capture.execution_id].command == rendered_command
    log_text = journal.status().session_log_path.read_text(encoding="utf-8")
    assert "\x1b" not in log_text
    assert "\x0b" not in log_text
    assert "\x00" not in log_text
    assert "COMMAND viewer first second " in log_text
    journal.close()


def test_transient_capture_read_failure_warns_once_and_retries(
    tmp_path,
) -> None:
    journal = CommandOutputJournal(log_root=tmp_path)
    capture = journal.begin_execution(_context(), "viewer retry")
    capture.close_parent_handles()
    capture.stdout_path.unlink()

    journal.poll()
    journal.poll()
    capture.stdout_path.write_bytes(b"recovered output\n")
    journal.finalize(capture.execution_id, 0)

    events = journal.events_since(0).events
    read_warnings = [
        event for event in events
        if event.kind == "warning" and "stdout" in event.text.lower()
    ]
    assert len(read_warnings) == 1
    assert any(
        event.kind == "stdout" and event.text == "recovered output"
        for event in events
    )
    journal.close()


def test_high_volume_concurrent_processes_do_not_deadlock_and_poll_is_bounded(
    tmp_path,
) -> None:
    budget = 4096
    journal = CommandOutputJournal(
        log_root=tmp_path,
        read_budget_bytes=budget,
        max_events=5000,
    )
    captures = [
        journal.begin_execution(
            _context(easyqcid=f"SUB00{index}", command_index=index),
            f"fixture-viewer {token}",
        )
        for index, token in enumerate(("A", "B"), start=1)
    ]
    processes = []
    for capture, token in zip(captures, ("A", "B"), strict=True):
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import sys; token=sys.argv[1]; "
                    "sys.stdout.write((token * 64 + '\\n') * 1200); "
                    "sys.stdout.flush(); "
                    "sys.stderr.write(token + '-done\\n'); "
                    "sys.stderr.flush()"
                ),
                token,
            ],
            stdout=capture.stdout_handle,
            stderr=capture.stderr_handle,
            shell=False,
        )
        journal.mark_started(capture.execution_id, process.pid)
        processes.append(process)

    returncodes = [process.wait(timeout=10) for process in processes]
    first_poll = journal.poll()
    first_poll_bytes = sum(
        len(event.text.encode("utf-8")) + 1
        for event in first_poll
        if event.kind in {"stdout", "stderr"}
    )
    for capture, returncode in zip(captures, returncodes, strict=True):
        journal.finalize(capture.execution_id, returncode)

    events = journal.events_since(0).events
    stdout_by_execution = {
        capture.execution_id: "".join(
            event.text
            for event in events
            if event.execution_id == capture.execution_id
            and event.kind == "stdout"
        )
        for capture in captures
    }
    assert returncodes == [0, 0]
    assert first_poll_bytes <= budget
    assert set(stdout_by_execution[captures[0].execution_id]) == {"A"}
    assert set(stdout_by_execution[captures[1].execution_id]) == {"B"}
    assert all(len(text) == 64 * 1200 for text in stdout_by_execution.values())
    assert {
        event.execution_id for event in events if event.kind == "exit"
    } == {capture.execution_id for capture in captures}
    journal.close()


def test_memory_truncation_is_explicit_while_session_log_remains_complete(
    tmp_path,
) -> None:
    journal = CommandOutputJournal(log_root=tmp_path, max_events=5)
    capture = journal.begin_execution(_context(), "viewer many-lines")
    source_lines = [f"unique-line-{index:02d}" for index in range(20)]
    capture.stdout_handle.write(
        ("\n".join(source_lines) + "\n").encode("utf-8")
    )
    capture.close_parent_handles()
    journal.finalize(capture.execution_id, 0)

    batch = journal.events_since(0)
    log_text = batch.status.session_log_path.read_text(encoding="utf-8")
    assert batch.truncated is True
    assert len(batch.events) == 5
    assert all(line in log_text for line in source_lines)
    assert "START" in log_text
    assert "EXIT" in log_text
    journal.close()


def test_finalize_is_idempotent_and_rejects_invalid_return_codes(
    tmp_path,
) -> None:
    journal = CommandOutputJournal(log_root=tmp_path)
    capture = journal.begin_execution(_context(), "viewer finalize")
    capture.stdout_handle.write(b"tail without newline")
    capture.close_parent_handles()

    first = journal.finalize(capture.execution_id, 7)
    second = journal.finalize(capture.execution_id, 7)

    assert any(event.kind == "stdout" for event in first)
    assert [event.kind for event in first][-1] == "exit"
    assert second == ()
    assert len(
        [event for event in journal.events_since(0).events if event.kind == "exit"]
    ) == 1
    with pytest.raises(ValueError, match="another code"):
        journal.finalize(capture.execution_id, 8)
    with pytest.raises(TypeError, match="returncode"):
        journal.finalize("missing", "0")
    with pytest.raises(TypeError, match="returncode"):
        journal.finalize("missing", True)
    with pytest.raises(KeyError, match="unknown execution_id"):
        journal.finalize("missing", 0)
    journal.close()


def test_context_allows_explicit_empty_rater_and_identity_but_rejects_nontext(
    tmp_path,
) -> None:
    journal = CommandOutputJournal(log_root=tmp_path)
    context = _context(rater="", easyqcid="")

    capture = journal.begin_execution(context, "viewer no-rater-yet")
    capture.close_parent_handles()
    journal.finalize(capture.execution_id, 0)

    assert journal.events_since(0).events[0].context == context
    with pytest.raises(TypeError, match="context.rater"):
        journal.begin_execution(_context(rater=None), "viewer invalid-rater")
    with pytest.raises(TypeError, match="context.easyqcid"):
        journal.begin_execution(
            _context(easyqcid=None),
            "viewer invalid-identity",
        )
    journal.close()


def test_session_log_append_failure_degrades_once_while_capture_continues(
    tmp_path,
) -> None:
    class FailingLogHandle:
        def __init__(self) -> None:
            self.closed = False

        def write(self, _text: str) -> None:
            raise OSError("controlled disk-full failure")

        def flush(self) -> None:
            raise AssertionError("flush must not follow a failed write")

        def close(self) -> None:
            self.closed = True

    journal = CommandOutputJournal(log_root=tmp_path)
    journal._log_handle.close()
    failing_handle = FailingLogHandle()
    journal._log_handle = failing_handle

    capture = journal.begin_execution(_context(), "viewer disk-full")
    capture.stdout_handle.write(b"still visible in memory\n")
    capture.close_parent_handles()
    journal.finalize(capture.execution_id, 0)

    batch = journal.events_since(0)
    persistence_warnings = [
        event for event in batch.events
        if event.kind == "warning" and "persistence" in event.text.lower()
    ]
    assert failing_handle.closed is True
    assert batch.status.file_logging_enabled is False
    assert batch.status.session_log_path is None
    assert len(persistence_warnings) == 1
    assert any(
        event.kind == "stdout" and event.text == "still visible in memory"
        for event in batch.events
    )
    journal.close()
