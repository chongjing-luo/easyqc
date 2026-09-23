"""RI-12c: known EasyQC variables expand once; other script text survives."""

import os
import sys

import pytest

from core.code_executor import CodeExecutor, validate_template_columns


@pytest.mark.parametrize("token", ["${TEMP_SCENE}", "{TEMP_SCENE}", "$TEMP_SCENE"])
def test_unknown_names_remain_byte_for_byte_in_rendered_plan(token):
    executor = CodeExecutor()
    template = f'viewer "${{root}}/{token}"\n'

    rendered, commands = executor.render_command_plan(template, {"root": "/data"})

    assert rendered == f'viewer "/data/{token}"\n'
    assert commands == {0: rendered}


@pytest.mark.parametrize("token", ["${path}", "{path}", "$path"])
def test_known_values_are_inserted_once_without_recursive_substitution(token):
    executor = CodeExecutor()
    variables = {"path": "${other}/{other}/$other", "other": "do-not-expand"}

    assert executor.parse_template(token, variables) == "${other}/{other}/$other"


def test_non_identifier_shell_expansions_and_literal_braces_survive():
    template = 'echo "${PATH:-fallback}" "${path##*/}" "$(echo ok)" {a,b} {}'

    assert CodeExecutor().parse_template(template, {}) == template


def test_reference_report_does_not_block_rendering_shell_local_names():
    template = 'TEMP_SCENE="${root}/scene"; echo "${TEMP_SCENE}"'

    assert validate_template_columns(template, {"root"}) == ["TEMP_SCENE"]
    assert CodeExecutor().parse_template(template, {"root": "/tmp"}) == (
        'TEMP_SCENE="/tmp/scene"; echo "${TEMP_SCENE}"'
    )


def test_direct_execution_keeps_unknown_variables_and_shell_punctuation_literal():
    executor = CodeExecutor()
    literal = "${TEMP_SCENE}; echo SECOND"
    script = "import sys; print(sys.argv[1])"
    rendered = executor.parse_template(literal, {})

    result = executor.run_command([sys.executable, "-c", script, rendered])

    assert executor.shell_enabled is False
    assert result.returncode == 0
    assert result.stdout.strip() == literal


@pytest.mark.skipif(os.name != "posix", reason="Exercises a POSIX Shell script")
def test_shell_mode_preserves_locals_across_lines_sed_semicolons_and_redirection(tmp_path):
    executor = CodeExecutor(shell_enabled=True)
    template = (
        'TEMP_SCENE="${output_dir}/scene.txt"\n'
        'printf "%s\\n" "001_03-rest01" | '
        'sed "s/001_03/${subses}/g; s/rest01/${moddir}/g" > "${TEMP_SCENE}";\n'
        'printf "%s\\n" \\\n'
        '  "${TEMP_SCENE}"; cat "${TEMP_SCENE}"\n'
    )
    rendered, commands = executor.render_command_plan(
        template,
        {"output_dir": str(tmp_path), "subses": "002_01", "moddir": "rest02"},
    )

    result = executor.run_command(commands[0], cwd=tmp_path)

    assert commands == {0: rendered}
    assert '"${TEMP_SCENE}"' in rendered
    assert "s/001_03/002_01/g; s/rest01/rest02/g" in rendered
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [str(tmp_path / "scene.txt"), "002_01-rest02"]
    assert (tmp_path / "scene.txt").read_text() == "002_01-rest02\n"


@pytest.mark.skipif(os.name != "posix", reason="Exercises POSIX Shell separators")
def test_semicolon_requires_explicit_shell_mode():
    command = "echo FIRST; echo SECOND"

    direct_result = CodeExecutor().run_command(command)
    shell_result = CodeExecutor(shell_enabled=True).run_command(command)

    assert direct_result.returncode == shell_result.returncode == 0
    assert direct_result.stdout.splitlines() == ["FIRST; echo SECOND"]
    assert shell_result.stdout.splitlines() == ["FIRST", "SECOND"]
