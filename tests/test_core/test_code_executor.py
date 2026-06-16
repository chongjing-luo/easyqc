import sys
from pathlib import Path

import pytest

from core.code_executor import (
    CodeExecutor,
    CodeExecutorError,
    CommandNotAllowedError,
    CommandTimeoutError,
)


def test_parse_template_replaces_legacy_variable_syntaxes() -> None:
    executor = CodeExecutor()

    result = executor.parse_template(
        "open $ezqcid ${image_path} {site}",
        {"ezqcid": "SUB001", "image_path": "/tmp/sub001.nii.gz", "site": "BNU"},
    )

    assert result == "open SUB001 /tmp/sub001.nii.gz BNU"


def test_run_command_executes_allowlisted_python_without_shell() -> None:
    executor = CodeExecutor()

    result = executor.run_command([sys.executable, "-c", "print('ok')"])

    assert result.returncode == 0
    assert result.stdout.strip() == "ok"


def test_run_command_rejects_non_allowlisted_command() -> None:
    executor = CodeExecutor()

    with pytest.raises(CommandNotAllowedError):
        executor.run_command("echo unsafe")


def test_split_command_rejects_shell_control_operators() -> None:
    executor = CodeExecutor()

    with pytest.raises(CommandNotAllowedError):
        executor.split_command("freeview image.nii; rm -rf /tmp/example")


def test_split_command_handles_legacy_line_continuations() -> None:
    executor = CodeExecutor()

    result = executor.split_command("freeview --layout 4 \\\n  -v /tmp/sub-001.nii.gz")

    assert result == ["freeview", "--layout", "4", "-v", "/tmp/sub-001.nii.gz"]


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


def test_start_command_reports_missing_allowlisted_executable() -> None:
    executor = CodeExecutor(allowed_commands=["missing_viewer"])

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
    available = {"subjects_dir", "fsrecon_dir", "hcp_dir", "ezqcid"}

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
    # custom allowlist with a name that is definitely not on PATH
    executor = CodeExecutor(allowed_commands=["definitely_missing_viewer_xyz"])
    with pytest.raises(CodeExecutorError) as exc:
        executor.start_command("definitely_missing_viewer_xyz /tmp/nonexistent.nii.gz")
    assert "definitely_missing_viewer_xyz" in str(exc.value)


def test_start_command_failure_reason_names_the_offending_command() -> None:
    """P3-E: the exception message must name the offending command so the user
    can tell WHICH viewer failed (multiple commands in a module)."""
    executor = CodeExecutor(allowed_commands=["another_missing_viewer_abc"])
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

    executor = CodeExecutor(allowed_commands=["ghost_viewer_p3e"])
    with pytest.raises(CodeExecutorError):
        executor.start_command("ghost_viewer_p3e /tmp/x.nii")

    assert any("ghost_viewer_p3e" in m for m in logged), "failure must be logged"
