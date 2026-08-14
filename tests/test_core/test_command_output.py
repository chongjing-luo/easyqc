from __future__ import annotations

import subprocess
import sys

from core.command_output import CommandOutputJournal, ViewerExecutionContext


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
