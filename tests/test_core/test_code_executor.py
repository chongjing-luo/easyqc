import signal
import subprocess
import sys
from pathlib import Path

import pytest

from core.command_output import CommandOutputJournal, ViewerExecutionContext
from core.code_executor import (
    CodeExecutor,
    CodeExecutorError,
    CommandTimeoutError,
)


@pytest.fixture(autouse=True)
def _isolate_default_command_output_storage(tmp_path, monkeypatch) -> None:
    """Keep default executor journals inside each test's temporary root."""

    monkeypatch.setattr(
        "core.code_executor.CommandOutputJournal",
        lambda: CommandOutputJournal(log_root=tmp_path / "default-journal"),
    )


def test_parse_template_replaces_legacy_variable_syntaxes() -> None:
    executor = CodeExecutor()

    result = executor.parse_template(
        "open $easyqcid ${image_path} {site}",
        {"easyqcid": "SUB001", "image_path": "/tmp/sub001.nii.gz", "site": "BNU"},
    )

    assert result == "open SUB001 /tmp/sub001.nii.gz BNU"


def test_run_command_executes_python_without_shell() -> None:
    executor = CodeExecutor()

    result = executor.run_command([sys.executable, "-c", "print('ok')"])

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_run_command_has_no_executable_name_gate(monkeypatch) -> None:
    observed = []

    def fake_run(command, **options):
        observed.append((command, options))
        return __import__("subprocess").CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("core.code_executor.subprocess.run", fake_run)
    executor = CodeExecutor()

    executor.run_command("custom-laboratory-viewer image.nii.gz")

    assert observed[0][0] == [
        "custom-laboratory-viewer",
        "image.nii.gz",
    ]
    assert observed[0][1]["shell"] is False


def test_shell_mode_controls_raw_string_and_subprocess_flag(monkeypatch) -> None:
    observed = []

    def fake_run(command, **options):
        observed.append((command, options))
        return __import__("subprocess").CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("core.code_executor.subprocess.run", fake_run)
    direct = CodeExecutor(shell_enabled=False)
    shell = CodeExecutor(shell_enabled=True)
    command = 'viewer "image one.nii.gz" | postprocess'

    direct.run_command(command)
    shell.run_command(command)

    assert observed[0][0] == [
        "viewer",
        "image one.nii.gz",
        "|",
        "postprocess",
    ]
    assert observed[0][1]["shell"] is False
    assert observed[1][0] == command
    assert observed[1][1]["shell"] is True


def test_start_command_uses_current_shell_setting(monkeypatch) -> None:
    observed = []

    class FakeProcess:
        pid = 4721

        @staticmethod
        def wait(timeout):
            raise subprocess.TimeoutExpired("viewer", timeout)

    def fake_popen(command, **options):
        observed.append((command, options))
        return FakeProcess()

    monkeypatch.setattr("core.code_executor.subprocess.Popen", fake_popen)
    executor = CodeExecutor(shell_enabled=False, system="Linux")
    command = "viewer image.nii.gz && write-report"

    executor.start_command(command)
    executor.set_shell_enabled(True)
    executor.start_command(command)

    assert observed[0][0] == [
        "viewer",
        "image.nii.gz",
        "&&",
        "write-report",
    ]
    assert observed[0][1]["shell"] is False
    assert observed[1][0] == command
    assert observed[1][1]["shell"] is True


def test_start_command_uses_safe_posix_session_option(monkeypatch) -> None:
    observed = []

    class FakeProcess:
        pid = 4722

        @staticmethod
        def wait(timeout):
            raise subprocess.TimeoutExpired("viewer", timeout)

    def fake_popen(command, **options):
        observed.append((command, options))
        return FakeProcess()

    monkeypatch.setattr("core.code_executor.subprocess.Popen", fake_popen)

    CodeExecutor(system="Linux").start_command("viewer image.nii.gz")

    assert observed[0][1]["start_new_session"] is True
    assert "preexec_fn" not in observed[0][1]


def test_process_cleanup_failure_is_logged_and_remains_tracked(
    monkeypatch,
) -> None:
    logged = []

    class BrokenProcess:
        pid = 4811

        @staticmethod
        def poll():
            return None

    process = BrokenProcess()
    executor = CodeExecutor(system="Linux")
    executor.current_processes = [process]
    monkeypatch.setattr("core.code_executor.os.getpgid", lambda _pid: process.pid)
    monkeypatch.setattr(
        "core.code_executor.os.killpg",
        lambda *_args: (_ for _ in ()).throw(PermissionError("denied")),
    )
    monkeypatch.setattr(
        "core.code_executor.log_error",
        lambda message, *_args, **_kwargs: logged.append(message),
    )

    executor.close_current_processes()

    assert executor.current_processes == [process]
    assert any("4811" in message and "denied" in message for message in logged)


def test_run_command_uses_one_shell_mode_snapshot(monkeypatch) -> None:
    observed = []
    executor = CodeExecutor(shell_enabled=False)
    original_prepare = executor._command_for_subprocess

    def prepare_then_toggle(command, *, shell_enabled):
        prepared = original_prepare(
            command,
            shell_enabled=shell_enabled,
        )
        executor.set_shell_enabled(True)
        return prepared

    def fake_run(command, **options):
        observed.append((command, options))
        return __import__("subprocess").CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(executor, "_command_for_subprocess", prepare_then_toggle)
    monkeypatch.setattr("core.code_executor.subprocess.run", fake_run)

    executor.run_command("viewer image.nii.gz")

    assert observed[0][0] == ["viewer", "image.nii.gz"]
    assert observed[0][1]["shell"] is False


def test_split_command_handles_legacy_line_continuations() -> None:
    executor = CodeExecutor()

    result = executor.split_command("freeview --layout 4 \\\n  -v /tmp/sub-001.nii.gz")

    assert result == ["freeview", "--layout", "4", "-v", "/tmp/sub-001.nii.gz"]


def test_split_command_windows_preserves_quoted_paths_and_unicode() -> None:
    executor = CodeExecutor(system="Windows")

    result = executor.split_command(
        '"C:\\Program Files\\MRIcroGL\\MRIcroGL.EXE" '
        '"D:\\研究数据\\质控记录 01.nii.gz"'
    )

    assert result == [
        "C:\\Program Files\\MRIcroGL\\MRIcroGL.EXE",
        "D:\\研究数据\\质控记录 01.nii.gz",
    ]


def test_split_command_windows_accepts_com_suffix() -> None:
    executor = CodeExecutor(system="Windows")

    result = executor.split_command(
        '"C:\\QC Tools\\VIEWER.COM" "D:\\data\\record 01.nii.gz"'
    )

    assert result[0] == "C:\\QC Tools\\VIEWER.COM"


@pytest.mark.parametrize("extension", [".bat", ".cmd"])
def test_split_command_windows_has_no_script_suffix_denylist(
    extension: str,
) -> None:
    executor = CodeExecutor(system="Windows")

    result = executor.split_command(
        f'"C:\\QC Tools\\viewer{extension}" record.nii.gz'
    )

    assert result == [
        f"C:\\QC Tools\\viewer{extension}",
        "record.nii.gz",
    ]


def test_split_command_macos_preserves_quoted_application_and_unicode_paths() -> None:
    executor = CodeExecutor(system="Darwin")

    result = executor.split_command(
        'open -a "MRIcroGL" "/Users/reviewer/研究数据/质控记录 01.nii.gz"'
    )

    assert result == [
        "open",
        "-a",
        "MRIcroGL",
        "/Users/reviewer/研究数据/质控记录 01.nii.gz",
    ]


def test_split_command_converts_legacy_mricrogl_temp_script_without_shell() -> None:
    executor = CodeExecutor()

    result = executor.split_command(
        "TMP=$(mktemp /tmp/mgl.XXXXXX); "
        'TMP="$TMP.py"; '
        "printf 'import gl\\ngl.loadimage(\"/tmp/sub-001.nii.gz\")\\n' > \"$TMP\"; "
        '/opt/MRIcroGL/MRIcroGL "$TMP"; '
        'rm -f "$TMP"'
    )

    script_path = Path(result[-1])
    try:
        assert result[0] == "/opt/MRIcroGL/MRIcroGL"
        assert script_path.exists()
        assert script_path.read_text(encoding="utf-8") == 'import gl\ngl.loadimage("/tmp/sub-001.nii.gz")\n'
    finally:
        script_path.unlink(missing_ok=True)


def test_start_command_reports_missing_executable() -> None:
    executor = CodeExecutor()

    with pytest.raises(CodeExecutorError, match="命令启动失败"):
        executor.start_command("missing_viewer /tmp/sub-001.nii.gz")


def test_run_command_raises_timeout() -> None:
    executor = CodeExecutor(timeout=0.05)

    with pytest.raises(CommandTimeoutError):
        executor.run_command([sys.executable, "-c", "import time; time.sleep(1)"])


# ---- P3-B: parse_template hardening (single-pass + reject unresolved) ----

def test_parse_template_rejects_unresolved_dollar_brace_placeholder() -> None:
    """P3-B: a ${var} whose name is not in variables must raise, not silently
    leak the placeholder into the viewer command (which then breaks launch)."""
    executor = CodeExecutor()
    with pytest.raises(CodeExecutorError) as exc:
        executor.parse_template("open ${missing_col}", {"present": "x"})
    assert "missing_col" in str(exc.value)


def test_parse_template_rejects_unresolved_brace_placeholder() -> None:
    """P3-B: same for {var} form."""
    executor = CodeExecutor()
    with pytest.raises(CodeExecutorError) as exc:
        executor.parse_template("open {missing_col}", {"present": "x"})
    assert "missing_col" in str(exc.value)


def test_parse_template_single_pass_no_re_substitution_on_values() -> None:
    """P3-B: single-pass means a variable VALUE containing $ or { is not
    re-interpreted by a later variable (the multi-pass str.replace bug)."""
    executor = CodeExecutor()
    # var 'a' value contains literal '{b}' which must NOT be eaten by var 'b'
    result = executor.parse_template("X={a}", {"a": "val{b}", "b": "OTHER"})
    assert result == "X=val{b}"


def test_parse_template_does_not_reinterpret_dollar_syntax_in_inserted_value() -> None:
    executor = CodeExecutor()

    result = executor.parse_template(
        "X={a}",
        {"a": "literal-$b", "b": "REPLACED"},
    )

    assert result == "X=literal-$b"


def test_parse_template_passes_through_mricrogl_shell_variables() -> None:
    """P3-B: MRIcroGL temp-script pattern uses $TMP / $(mktemp ...) which are
    shell constructs, NOT template variables. They must pass through untouched
    (TMP is not in variables). This is F-VIEW-4 compatibility."""
    executor = CodeExecutor()
    template = 'TMP=$(mktemp /tmp/mgl.XXXXXX); open {subjects_dir}/img.nii'
    result = executor.parse_template(template, {"subjects_dir": "/data"})
    assert "$TMP" in result or "$(mktemp" in result
    assert "/data/img.nii" in result


def test_parse_template_preserves_literal_braces_not_matching_variables() -> None:
    """P3-B defense: a literal {x} where x is NOT a variable name should pass
    through, not raise (e.g. a future f-string in a viewer script). Only
    unresolved placeholders that LOOK like intended vars are reported — but to
    stay safe and simple, unknown {name} is reported. This test pins that a
    KNOWN variable's value with braces survives, and MRIcroGL $-shell passes."""
    executor = CodeExecutor()
    result = executor.parse_template("{a}", {"a": '{"k": 1}'})
    assert result == '{"k": 1}'


def test_validate_template_columns_reports_missing_columns() -> None:
    """P3-B block 2 (F-IMP-6): a module code referencing columns absent from
    the available set is reported explicitly, before viewer launch."""
    from core.code_executor import validate_template_columns

    code = "freeview {subjects_dir}/{fsrecon_dir}/{missing_col}.nii ${hcp_dir}"
    available = {"subjects_dir", "fsrecon_dir", "hcp_dir", "easyqcid"}

    missing = validate_template_columns(code, available)

    assert "missing_col" in missing
    assert "subjects_dir" not in missing
    assert "hcp_dir" not in missing


def test_validate_template_columns_empty_when_all_present() -> None:
    from core.code_executor import validate_template_columns

    code = "open {a} ${b} $c"
    missing = validate_template_columns(code, {"a", "b", "c"})

    assert missing == []


# ---- P3-E: viewer launch error visibility ----

def test_start_command_raises_on_missing_binary_with_logged_reason() -> None:
    """P3-E / N1.2: when a viewer binary does not exist (typical launch failure),
    CodeExecutor must raise CodeExecutorError AND the reason must be visible
    (logged), not silently swallowed by DEVNULL."""
    executor = CodeExecutor()
    with pytest.raises(CodeExecutorError) as exc:
        executor.start_command("definitely_missing_viewer_xyz /tmp/nonexistent.nii.gz")
    assert "definitely_missing_viewer_xyz" in str(exc.value)


def test_start_command_failure_reason_names_the_offending_command() -> None:
    """P3-E: the exception message must name the offending command so the user
    can tell WHICH viewer failed (multiple commands in a module)."""
    executor = CodeExecutor()
    with pytest.raises(CodeExecutorError) as exc:
        executor.start_command("another_missing_viewer_abc /tmp/missing.nii")
    assert "another_missing_viewer_abc" in str(exc.value)



def test_start_command_logs_error_before_raising(monkeypatch) -> None:
    """P3-E: a missing-binary failure must be LOGGED (visible in logs even if
    the caller swallows the exception), not only raised. Viewer launch errors
    were previously invisible because stderr went to DEVNULL."""
    logged = []
    import core.code_executor as ce_mod
    monkeypatch.setattr(ce_mod, "log_error", lambda msg, *a, **k: logged.append(msg))

    executor = CodeExecutor()
    with pytest.raises(CodeExecutorError):
        executor.start_command("ghost_viewer_p3e /tmp/x.nii")

    assert any("ghost_viewer_p3e" in m for m in logged), "failure must be logged"


# ---- Frozen external-viewer environment and startup diagnostics ----

def test_frozen_linux_viewer_environment_restores_original_loader_path() -> None:
    from core.code_executor import build_external_process_environment

    inherited = {
        "PATH": "/opt/freesurfer/bin:/usr/bin",
        "FREESURFER_HOME": "/opt/freesurfer",
        "SUBJECTS_DIR": "/data/subjects",
        "DISPLAY": ":12",
        "XAUTHORITY": "/run/user/1000/xauth",
        "LD_LIBRARY_PATH": "/opt/easyqc/_internal:/opt/freesurfer/lib",
        "LD_LIBRARY_PATH_ORIG": "/opt/freesurfer/lib",
        "QT_PLUGIN_PATH": "/opt/easyqc/_internal/PySide6/Qt/plugins",
        "QML2_IMPORT_PATH": "/opt/easyqc/_internal/PySide6/Qt/qml",
    }

    result = build_external_process_environment(
        inherited,
        system="Linux",
        frozen=True,
    )

    assert result["LD_LIBRARY_PATH"] == "/opt/freesurfer/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in result
    assert "QT_PLUGIN_PATH" not in result
    assert "QML2_IMPORT_PATH" not in result
    assert result["PATH"] == inherited["PATH"]
    assert result["FREESURFER_HOME"] == inherited["FREESURFER_HOME"]
    assert result["SUBJECTS_DIR"] == inherited["SUBJECTS_DIR"]
    assert result["DISPLAY"] == inherited["DISPLAY"]
    assert result["XAUTHORITY"] == inherited["XAUTHORITY"]
    assert inherited["LD_LIBRARY_PATH"].startswith("/opt/easyqc/_internal")


def test_frozen_linux_viewer_environment_removes_injected_path_without_original() -> None:
    from core.code_executor import build_external_process_environment

    result = build_external_process_environment(
        {
            "PATH": "/usr/bin:/bin",
            "LD_LIBRARY_PATH": "/opt/easyqc/_internal",
        },
        system="Linux",
        frozen=True,
    )

    assert result == {"PATH": "/usr/bin:/bin"}


def test_source_viewer_environment_is_an_unchanged_copy() -> None:
    from core.code_executor import build_external_process_environment

    inherited = {
        "PATH": "/lab/bin:/usr/bin",
        "LD_LIBRARY_PATH": "/lab/lib",
        "LD_LIBRARY_PATH_ORIG": "/older/lib",
    }

    result = build_external_process_environment(
        inherited,
        system="Linux",
        frozen=False,
    )

    assert result == inherited
    assert result is not inherited


def test_run_command_passes_clean_environment_to_subprocess(monkeypatch) -> None:
    observed = []

    def fake_run(command, **options):
        observed.append((command, options))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("core.code_executor.subprocess.run", fake_run)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/easyqc/_internal:/fs/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/fs/lib")
    monkeypatch.setenv("FREESURFER_HOME", "/fs")

    CodeExecutor(system="Linux").run_command("freeview image.mgz")

    child_environment = observed[0][1]["env"]
    assert child_environment["LD_LIBRARY_PATH"] == "/fs/lib"
    assert "LD_LIBRARY_PATH_ORIG" not in child_environment
    assert child_environment["FREESURFER_HOME"] == "/fs"


@pytest.mark.parametrize("shell_enabled", [False, True])
def test_start_command_passes_clean_environment_and_tracks_live_viewer(
    monkeypatch,
    shell_enabled,
) -> None:
    observed = []

    class LiveProcess:
        pid = 5091

        @staticmethod
        def wait(timeout):
            raise subprocess.TimeoutExpired("freeview", timeout)

    def fake_popen(command, **options):
        observed.append((command, options))
        return LiveProcess()

    monkeypatch.setattr("core.code_executor.subprocess.Popen", fake_popen)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/easyqc/_internal:/fs/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/fs/lib")

    executor = CodeExecutor(system="Linux", shell_enabled=shell_enabled)
    process = executor.start_command("freeview image.mgz")

    assert observed[0][1]["env"]["LD_LIBRARY_PATH"] == "/fs/lib"
    assert observed[0][1]["shell"] is shell_enabled
    assert process in executor.current_processes


def test_start_command_reports_immediate_nonzero_stderr(monkeypatch) -> None:
    monkeypatch.setattr("core.code_executor._VIEWER_STARTUP_TIMEOUT", 2.0)
    executor = CodeExecutor(system="Linux")

    with pytest.raises(CodeExecutorError) as exc:
        executor.start_command(
            [
                sys.executable,
                "-c",
                "import sys; print('freeview loader mismatch', file=sys.stderr); sys.exit(7)",
            ]
        )

    message = str(exc.value)
    assert "freeview loader mismatch" in message
    assert "7" in message
    assert executor.current_processes == []
    assert executor._process_execution_ids == {}
    assert executor._process_stderr_paths == {}
    assert executor._process_labels == {}


def test_viewer_stderr_excerpt_is_bounded_and_removes_control_characters(
    tmp_path,
) -> None:
    stderr_path = tmp_path / "viewer.stderr"
    stderr_path.write_bytes(b"old\n" + b"x" * 9000 + b"\x1b[31m final-error\n")

    excerpt = CodeExecutor._read_bounded_stderr(stderr_path)

    assert len(excerpt) <= 2000
    assert "\x1b" not in excerpt
    assert excerpt.endswith("[31m final-error")


# ---- Viewer command-output capture lifecycle ----


def _viewer_context(
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


class _RecordingJournal(CommandOutputJournal):
    def __init__(self, *, log_root) -> None:
        super().__init__(log_root=log_root)
        self.lifecycle: list[tuple[str, object]] = []

    def mark_started(self, execution_id: str, pid: int) -> None:
        self.lifecycle.append(("started", execution_id))
        super().mark_started(execution_id, pid)

    def finalize(self, execution_id: str, returncode: int):
        self.lifecycle.append(("finalize", execution_id))
        return super().finalize(execution_id, returncode)


@pytest.mark.parametrize("shell_enabled", [False, True])
def test_start_command_inherits_journal_handles_and_supplied_context(
    tmp_path,
    monkeypatch,
    shell_enabled,
) -> None:
    observed = []

    class LiveProcess:
        pid = 6101

        @staticmethod
        def wait(timeout):
            raise subprocess.TimeoutExpired("viewer", timeout)

    def fake_popen(command, **options):
        observed.append((command, options))
        return LiveProcess()

    journal = _RecordingJournal(log_root=tmp_path)
    context = _viewer_context(command_index=3)
    monkeypatch.setattr("core.code_executor.subprocess.Popen", fake_popen)
    executor = CodeExecutor(
        system="Linux",
        shell_enabled=shell_enabled,
        command_output_journal=journal,
    )

    executor.start_command(
        "viewer --subject SUB001",
        output_context=context,
    )

    state = next(iter(journal._executions.values()))
    assert observed[0][1]["stdout"] is state.capture.stdout_handle
    assert observed[0][1]["stderr"] is state.capture.stderr_handle
    assert state.capture.stdout_handle.closed is True
    assert state.capture.stderr_handle.closed is True
    assert state.context == context
    assert state.command == "viewer --subject SUB001"
    assert journal.lifecycle == [("started", state.capture.execution_id)]
    assert executor._process_execution_ids == {
        LiveProcess.pid: state.capture.execution_id
    }
    assert executor._process_stderr_paths == {
        LiveProcess.pid: state.capture.stderr_path
    }
    journal.close()


def test_start_command_without_context_records_explicit_generic_context(
    tmp_path,
    monkeypatch,
) -> None:
    class LiveProcess:
        pid = 6102

        @staticmethod
        def wait(timeout):
            raise subprocess.TimeoutExpired("viewer", timeout)

    journal = CommandOutputJournal(log_root=tmp_path)
    monkeypatch.setattr(
        "core.code_executor.subprocess.Popen",
        lambda *_args, **_kwargs: LiveProcess(),
    )
    executor = CodeExecutor(
        system="Linux",
        command_output_journal=journal,
    )

    executor.start_command("viewer image.nii.gz")

    context = journal.events_since(0).events[0].context
    assert context.module_name
    assert context.rater == ""
    assert context.easyqcid == ""
    assert context.command_index == 0
    journal.close()


def test_start_commands_preserves_order_control_and_per_command_context(
    monkeypatch,
) -> None:
    calls = []
    executor = CodeExecutor()
    contexts = {
        "first": _viewer_context(easyqcid="SUB001", command_index=0),
        "second": _viewer_context(easyqcid="SUB001", command_index=1),
    }

    monkeypatch.setattr(
        executor,
        "close_current_processes",
        lambda: calls.append(("close", None)),
    )

    def fake_start(command, cwd=None, output_context=None):
        calls.append((command, output_context))
        return command

    monkeypatch.setattr(executor, "start_command", fake_start)

    result = executor.start_commands(
        {
            "first": "viewer first",
            "second": "viewer second",
        },
        control=True,
        cwd="/data",
        output_contexts=contexts,
    )

    assert result == ["viewer first", "viewer second"]
    assert calls == [
        ("close", None),
        ("viewer first", contexts["first"]),
        ("viewer second", contexts["second"]),
    ]


def test_start_commands_rejects_missing_context_before_any_side_effect(
    monkeypatch,
) -> None:
    calls = []
    executor = CodeExecutor()
    monkeypatch.setattr(
        executor,
        "close_current_processes",
        lambda: calls.append("close"),
    )
    monkeypatch.setattr(
        executor,
        "start_command",
        lambda *_args, **_kwargs: calls.append("start"),
    )

    with pytest.raises(CodeExecutorError, match="second"):
        executor.start_commands(
            {
                "first": "viewer first",
                "second": "viewer second",
            },
            control=True,
            output_contexts={
                "first": _viewer_context(command_index=0),
            },
        )

    assert calls == []


def test_immediate_nonzero_finalizes_before_diagnostic_and_forget(
    tmp_path,
    monkeypatch,
) -> None:
    journal = _RecordingJournal(log_root=tmp_path)
    executor = CodeExecutor(
        system="Linux",
        command_output_journal=journal,
    )
    original_forget = executor._forget_process
    original_read = executor._read_bounded_stderr

    def recording_forget(process):
        journal.lifecycle.append(("forget", process.pid))
        return original_forget(process)

    def recording_read(stderr_path):
        journal.lifecycle.append(("diagnostic", stderr_path))
        return original_read(stderr_path)

    monkeypatch.setattr(executor, "_forget_process", recording_forget)
    monkeypatch.setattr(executor, "_read_bounded_stderr", recording_read)
    monkeypatch.setattr("core.code_executor._VIEWER_STARTUP_TIMEOUT", 2.0)

    with pytest.raises(CodeExecutorError) as exc:
        executor.start_command(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "print('captured startup failure', file=sys.stderr); "
                    "sys.exit(9)"
                ),
            ],
            output_context=_viewer_context(),
        )

    action_names = [name for name, _value in journal.lifecycle]
    assert (
        action_names.index("finalize")
        < action_names.index("diagnostic")
        < action_names.index("forget")
    )
    assert "captured startup failure" in str(exc.value)
    assert "9" in str(exc.value)
    assert [
        event.returncode
        for event in journal.events_since(0).events
        if event.kind == "exit"
    ] == [9]
    assert executor.current_processes == []
    assert executor._process_execution_ids == {}
    journal.close()


def test_immediate_nonzero_diagnostic_survives_event_ring_truncation(
    tmp_path,
    monkeypatch,
) -> None:
    journal = CommandOutputJournal(log_root=tmp_path, max_events=1)
    executor = CodeExecutor(
        system="Linux",
        command_output_journal=journal,
    )
    monkeypatch.setattr("core.code_executor._VIEWER_STARTUP_TIMEOUT", 2.0)

    with pytest.raises(CodeExecutorError) as exc:
        executor.start_command(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "print('raw stderr survives ring truncation', file=sys.stderr); "
                    "sys.exit(11)"
                ),
            ]
        )

    assert "raw stderr survives ring truncation" in str(exc.value)
    assert [event.kind for event in journal.events_since(0).events] == [
        "exit"
    ]
    journal.close()


def test_spawn_failure_closes_capture_and_temp_file_without_false_exit(
    tmp_path,
    monkeypatch,
) -> None:
    journal = CommandOutputJournal(log_root=tmp_path)
    launched_args = []

    def fail_popen(args, **_options):
        launched_args.extend(args)
        raise OSError("controlled spawn failure")

    monkeypatch.setattr("core.code_executor.subprocess.Popen", fail_popen)
    executor = CodeExecutor(
        system="Linux",
        command_output_journal=journal,
    )
    command = (
        "TMP=$(mktemp /tmp/mgl.XXXXXX); "
        'TMP="$TMP.py"; '
        "printf 'print(1)\\n' > \"$TMP\"; "
        '/opt/MRIcroGL/MRIcroGL "$TMP"; '
        'rm -f "$TMP"'
    )

    with pytest.raises(CodeExecutorError, match="controlled spawn failure"):
        executor.start_command(command, output_context=_viewer_context())

    state = next(iter(journal._executions.values()))
    assert state.capture.stdout_handle.closed is True
    assert state.capture.stderr_handle.closed is True
    assert not Path(launched_args[-1]).exists()
    assert not any(
        event.kind == "exit" for event in journal.events_since(0).events
    )
    assert executor.current_processes == []
    assert executor._process_execution_ids == {}
    journal.close()


def test_output_query_finalizes_exited_direct_process_once_but_defers_shell_wrapper(
    tmp_path,
    monkeypatch,
) -> None:
    processes = []

    class ExitedAfterStartup:
        def __init__(self, pid):
            self.pid = pid
            self.returncode = None

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("viewer", timeout)

        def poll(self):
            self.returncode = 0
            return self.returncode

    def fake_popen(_command, **_options):
        process = ExitedAfterStartup(6200 + len(processes))
        processes.append(process)
        return process

    monkeypatch.setattr("core.code_executor.subprocess.Popen", fake_popen)
    direct_journal = _RecordingJournal(log_root=tmp_path / "direct")
    shell_journal = _RecordingJournal(log_root=tmp_path / "shell")
    direct = CodeExecutor(
        system="Linux",
        shell_enabled=False,
        command_output_journal=direct_journal,
    )
    shell = CodeExecutor(
        system="Linux",
        shell_enabled=True,
        command_output_journal=shell_journal,
    )
    direct.start_command("viewer direct")
    shell.start_command("viewer shell")
    direct_temp_file = tmp_path / "direct-command-temp.py"
    direct_temp_file.write_text("temporary", encoding="utf-8")
    direct._process_temp_files[processes[0].pid] = [direct_temp_file]

    first = direct.command_output_since(0)
    second = direct.command_output_since(0)
    shell_before_cleanup = shell.command_output_since(0)

    assert first.events == second.events
    assert [name for name, _value in direct_journal.lifecycle].count(
        "finalize"
    ) == 1
    assert direct.current_processes == []
    assert direct._process_execution_ids == {}
    assert direct._process_temp_files == {}
    assert not direct_temp_file.exists()
    assert not any(
        event.kind == "exit" for event in shell_before_cleanup.events
    )
    assert [name for name, _value in shell_journal.lifecycle].count(
        "finalize"
    ) == 0

    shell.close_current_processes()

    assert [name for name, _value in shell_journal.lifecycle].count(
        "finalize"
    ) == 1
    assert any(
        event.kind == "exit"
        for event in shell.command_output_since(0).events
    )
    direct.close_current_processes()
    direct_journal.close()
    shell_journal.close()


def test_output_query_finalizes_and_forgets_nonzero_shell_wrapper(
    tmp_path,
    monkeypatch,
) -> None:
    class FailedShellAfterStartup:
        pid = 6250
        returncode = None

        @classmethod
        def wait(cls, timeout):
            raise subprocess.TimeoutExpired("viewer", timeout)

        @classmethod
        def poll(cls):
            cls.returncode = 5
            return cls.returncode

    journal = _RecordingJournal(log_root=tmp_path)
    monkeypatch.setattr(
        "core.code_executor.subprocess.Popen",
        lambda *_args, **_kwargs: FailedShellAfterStartup(),
    )
    executor = CodeExecutor(
        system="Linux",
        shell_enabled=True,
        command_output_journal=journal,
    )
    executor.start_command("viewer failed-shell")

    batch = executor.command_output_since(0)

    assert [name for name, _value in journal.lifecycle].count("finalize") == 1
    assert any(
        event.kind == "exit" and event.returncode == 5
        for event in batch.events
    )
    assert executor.current_processes == []
    assert executor._process_execution_ids == {}
    journal.close()


@pytest.mark.parametrize(
    ("observable_returncode", "should_remain_tracked"),
    [(0, False), (None, True)],
)
def test_process_lookup_race_finalizes_only_with_observable_returncode(
    tmp_path,
    monkeypatch,
    observable_returncode,
    should_remain_tracked,
) -> None:
    logged = []

    class VanishedProcess:
        pid = 6260

        def __init__(self):
            self.returncode = None
            self.poll_count = 0

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("viewer", timeout)

        def poll(self):
            self.poll_count += 1
            if self.poll_count > 1:
                self.returncode = observable_returncode
            return self.returncode

    process = VanishedProcess()
    journal = _RecordingJournal(log_root=tmp_path)
    monkeypatch.setattr(
        "core.code_executor.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr("core.code_executor.os.getpgid", lambda _pid: process.pid)
    monkeypatch.setattr(
        "core.code_executor.os.killpg",
        lambda *_args: (_ for _ in ()).throw(ProcessLookupError("gone")),
    )
    monkeypatch.setattr(
        "core.code_executor.log_error",
        lambda message, *_args, **_kwargs: logged.append(message),
    )
    executor = CodeExecutor(
        system="Linux",
        command_output_journal=journal,
    )
    executor.start_command("viewer race")

    executor.close_current_processes()

    assert bool(executor.current_processes) is should_remain_tracked
    exit_events = [
        event for event in journal.events_since(0).events
        if event.kind == "exit"
    ]
    if observable_returncode is None:
        assert exit_events == []
        assert any("gone" in message for message in logged)
    else:
        assert [event.returncode for event in exit_events] == [
            observable_returncode
        ]
        assert executor._process_execution_ids == {}
    journal.close()


@pytest.mark.parametrize(
    ("mode", "expected_returncode"),
    [
        ("completed", 3),
        ("terminated", -signal.SIGTERM),
        ("killed", -signal.SIGKILL),
    ],
)
def test_close_current_processes_finalizes_before_forgetting_every_exit_path(
    tmp_path,
    monkeypatch,
    mode,
    expected_returncode,
) -> None:
    class ControlledProcess:
        pid = 6301

        def __init__(self):
            self.returncode = None
            self.startup_checked = False
            self.cleanup_waits = 0

        def wait(self, timeout=None):
            if not self.startup_checked:
                self.startup_checked = True
                raise subprocess.TimeoutExpired("viewer", timeout)
            self.cleanup_waits += 1
            if mode == "killed" and self.cleanup_waits == 1:
                raise subprocess.TimeoutExpired("viewer", timeout)
            self.returncode = expected_returncode
            return self.returncode

        def poll(self):
            if mode == "completed":
                self.returncode = expected_returncode
            return self.returncode

    process = ControlledProcess()
    journal = _RecordingJournal(log_root=tmp_path)
    monkeypatch.setattr(
        "core.code_executor.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr("core.code_executor.os.getpgid", lambda _pid: process.pid)
    monkeypatch.setattr("core.code_executor.os.killpg", lambda *_args: None)
    executor = CodeExecutor(
        system="Linux",
        command_output_journal=journal,
    )
    executor.start_command("viewer image.nii.gz")
    original_forget = executor._forget_process

    def recording_forget(current):
        journal.lifecycle.append(("forget", current.pid))
        return original_forget(current)

    monkeypatch.setattr(executor, "_forget_process", recording_forget)

    executor.close_current_processes(timeout=0.01)

    action_names = [name for name, _value in journal.lifecycle]
    assert action_names.index("finalize") < action_names.index("forget")
    assert [
        event.returncode
        for event in journal.events_since(0).events
        if event.kind == "exit"
    ] == [expected_returncode]
    assert executor.current_processes == []
    assert executor._process_execution_ids == {}
    journal.close()
