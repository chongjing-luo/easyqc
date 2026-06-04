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
