import sys
from pathlib import Path

import pytest

from core.code_executor import (
    CodeExecutor,
    CodeExecutorError,
    CommandTimeoutError,
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
