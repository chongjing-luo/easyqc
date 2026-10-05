from __future__ import annotations

import os
import platform
import re
import shlex
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.command_output import (
    CommandOutputBatch,
    CommandOutputEvent,
    CommandOutputJournal,
    ViewerExecutionContext,
)
from utils.logger import log_error


class CodeExecutorError(Exception):
    """Base exception for controlled command execution errors."""


class CommandTimeoutError(CodeExecutorError):
    """Raised when a command exceeds the configured timeout."""


_VIEWER_STARTUP_TIMEOUT = 0.35
_VIEWER_ERROR_LIMIT = 2000
_VIEWER_ERROR_READ_BYTES = 8192
_FROZEN_QT_ENVIRONMENT_KEYS = (
    "QT_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
)
_GENERIC_OUTPUT_CONTEXT = ViewerExecutionContext(
    module_name="EasyQC",
    rater="",
    easyqcid="",
    command_index=0,
)
_MULTICMD_KEYWORD = "MULTICMD"
_MULTICMD_SEPARATOR = ";|"
_SCRIPT_KEYWORD = "SCRIPT"


def _is_script_template(template: str) -> bool:
    """True when the template's first token is the SCRIPT keyword."""

    stripped = template.lstrip()
    return bool(stripped) and (
        stripped.split(None, 1)[0] == _SCRIPT_KEYWORD
        or stripped.startswith(_SCRIPT_KEYWORD + "\n")
    )


def split_multicmd_template(template: str) -> tuple[str, str] | None:
    """Return (prefix, command_text) for a MULTICMD template, else None.

    Detection runs on the raw template so only the keyword the configurator
    wrote can create a multi-command plan; substituted values can never
    introduce one. ``prefix`` is empty for the leading-keyword form.
    """

    stripped = template.strip()
    if stripped.startswith(_MULTICMD_KEYWORD):
        return "", stripped[len(_MULTICMD_KEYWORD):]
    index = stripped.find(_MULTICMD_KEYWORD)
    if index == -1:
        return None
    return stripped[:index], stripped[index + len(_MULTICMD_KEYWORD):]


def _split_outside_quotes(text: str, delimiter: str) -> list[str]:
    """Split on one delimiter, keeping quoted regions (single/double) intact."""

    parts: list[str] = []
    buffer: list[str] = []
    quote = ""
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if quote:
            buffer.append(character)
            if character == quote:
                quote = ""
            index += 1
            continue
        if character in "\"'":
            quote = character
            buffer.append(character)
            index += 1
            continue
        if text.startswith(delimiter, index):
            parts.append("".join(buffer))
            buffer = []
            index += len(delimiter)
            continue
        buffer.append(character)
        index += 1
    parts.append("".join(buffer))
    return parts


def validate_module_command_template(code: str, interper: str) -> None:
    """Reject the MULTICMD prefix form combined with direct execution.

    The prefix form copies its prefix into every split command joined by a
    ";" that only a Shell interprets; direct execution would glue that ";"
    into one bogus argument. Failing here surfaces the invalid combination
    when the module is saved instead of silently at viewer launch.
    """

    parts = split_multicmd_template(code or "")
    if parts is None:
        return
    prefix, _command_text = parts
    if prefix.strip() and interper != "shell":
        raise CodeExecutorError(
            "MULTICMD 前缀写法需要 Shell 解释器：请将模块切换为 shell 模式，"
            "或移除 MULTICMD 之前的命令"
        )


def build_external_process_environment(
    environ: Mapping[str, str] | None = None,
    *,
    system: str | None = None,
    frozen: bool | None = None,
) -> dict[str, str]:
    """Return a child-only environment suitable for an external viewer.

    PyInstaller must alter the frozen application's loader and Qt paths so
    EasyQC can find its bundled libraries.  External programs must not inherit
    those private paths: Freeview and similar viewers ship their own Qt/VTK
    runtime and can exit immediately after loading an incompatible EasyQC
    library or plugin.

    Input is one environment mapping; output is an independent string mapping.
    The function has no side effects and never mutates ``os.environ``.  A
    platform that needs process-global launcher state rather than environment
    repair must use a separate platform adapter instead of expanding this
    function's contract.
    """

    inherited = os.environ if environ is None else environ
    result = dict(inherited)
    target_system = system or platform.system()
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if not is_frozen:
        return result

    for key in _FROZEN_QT_ENVIRONMENT_KEYS:
        result.pop(key, None)

    if target_system == "Linux":
        original_library_path = result.pop("LD_LIBRARY_PATH_ORIG", None)
        if original_library_path:
            result["LD_LIBRARY_PATH"] = original_library_path
        else:
            result.pop("LD_LIBRARY_PATH", None)

    return result


class CodeExecutor:
    def __init__(
        self,
        shell_enabled: bool = False,
        timeout: float = 300,
        system: str | None = None,
        command_output_journal: CommandOutputJournal | None = None,
    ) -> None:
        if not isinstance(shell_enabled, bool):
            raise TypeError("shell_enabled must be a Boolean")
        self.shell_enabled = shell_enabled
        self.timeout = timeout
        self.system = system or platform.system()
        self.current_processes: list[subprocess.Popen] = []
        self._closed = False
        self._command_output_journal = command_output_journal
        self._pending_temp_files: list[Path] = []
        self._process_temp_files: dict[int, list[Path]] = {}
        self._process_execution_ids: dict[int, str] = {}
        self._process_shell_modes: dict[int, bool] = {}
        self._finalized_process_ids: set[int] = set()
        self._process_stderr_paths: dict[int, Path] = {}
        self._process_labels: dict[int, str] = {}

    def _output_journal(self) -> CommandOutputJournal:
        """Return the one command-output journal owned by this executor."""

        if self._command_output_journal is None:
            self._command_output_journal = CommandOutputJournal()
        return self._command_output_journal

    def _require_open(self) -> None:
        if self._closed:
            raise CodeExecutorError("CodeExecutor is closed")

    def set_shell_enabled(self, enabled: bool) -> None:
        """Select direct or Shell execution for future commands."""

        if not isinstance(enabled, bool):
            raise TypeError("shell_enabled must be a Boolean")
        self.shell_enabled = enabled

    # Expand known EasyQC variables in one pass. Unknown names in all three
    # supported forms remain literal, allowing the selected command interpreter
    # to handle Shell-local/environment variables without a separate escape.
    _BRACE_PLACEHOLDER = re.compile(r"\$\{(\w+)\}|\{(\w+)\}")
    _BARE_DOLLAR = re.compile(r"\$(\w+)")
    _PLACEHOLDER = re.compile(
        r"\$\{(?P<dollar_brace>\w+)\}"
        r"|\{(?P<brace>\w+)\}"
        r"|\$(?P<bare>\w+)"
    )

    def parse_template(self, template: str, variables: Mapping[str, Any]) -> str:
        """Replace known names once, preserving unknown tokens exactly.

        This is template expansion, not Shell evaluation: only the user's
        selected execution mode determines whether the remaining text expands.
        Values inserted here are never scanned for more EasyQC placeholders.
        """
        var_lookup = dict(variables)

        def _substitute(match: re.Match) -> str:
            name = (
                match.group("dollar_brace")
                or match.group("brace")
                or match.group("bare")
            )
            if name in var_lookup:
                return str(var_lookup[name])
            return match.group(0)

        return self._PLACEHOLDER.sub(_substitute, template)

    def render_command_plan(
        self,
        template: str,
        variables: Mapping[str, Any],
    ) -> tuple[str, dict[int, str]]:
        """Expand one viewer template into an ordered, explicit command plan.

        Structure comes from the raw template only: the SCRIPT, MULTICMD
        keywords and ";|" separators are located before variable
        substitution, so values can never split commands or introduce a
        keyword. For plain and MULTICMD forms the first return value is the
        rendered text (keyword removed for MULTICMD); the SCRIPT carrier
        keeps its keyword because execution recognises it by token.
        """

        if _is_script_template(template):
            header, _, body = template.strip().partition("\n")
            viewer_text = header[len(_SCRIPT_KEYWORD):].strip()
            carrier = (
                f"{_SCRIPT_KEYWORD} {self.parse_template(viewer_text, variables)}"
                f"\n{self.parse_template(body, variables)}"
            )
            return carrier, {0: carrier}
        parts = split_multicmd_template(template)
        if parts is None:
            rendered = self.parse_template(template, variables)
            return rendered, {0: rendered}
        prefix_text, command_text = parts
        segments = [
            segment.strip()
            for segment in _split_outside_quotes(command_text, _MULTICMD_SEPARATOR)
            if segment.strip()
        ]
        rendered_prefix = self.parse_template(prefix_text, variables).strip()
        prefix = ""
        if rendered_prefix:
            prefix = (
                rendered_prefix
                if rendered_prefix.endswith(";")
                else f"{rendered_prefix};"
            )
        commands = [
            f"{prefix}{self.parse_template(segment, variables)}"
            for segment in segments
        ]
        return _MULTICMD_SEPARATOR.join(commands), {
            index: command for index, command in enumerate(commands)
        }

    def split_command(self, command: str | Sequence[str]) -> list[str]:
        if isinstance(command, str):
            command = self._normalize_command_text(command)
            legacy_mricrogl_parts = self._split_legacy_mricrogl_temp_script(command)
            if legacy_mricrogl_parts is not None:
                return legacy_mricrogl_parts
            if self.system == "Windows":
                parts = self._split_windows_command_line(command)
            else:
                parts = shlex.split(command, posix=True)
        else:
            parts = [str(part) for part in command]

        self._validate_command_parts(parts)
        return parts

    def _normalize_command_text(self, command: str) -> str:
        return (
            command
            .replace("\\\r\n", " ")
            .replace("\\\n", " ")
            .strip()
        )

    def _split_windows_command_line(self, command: str) -> list[str]:
        """Split a command using Windows C-runtime quoting rules.

        ``shlex.split(..., posix=False)`` keeps surrounding double quotes in
        returned arguments. That breaks both executable allowlist matching and
        paths containing spaces. This parser keeps backslashes verbatim unless
        they precede a double quote, matching the rules used by
        ``subprocess.list2cmdline`` when the argument list is launched.
        """

        parts: list[str] = []
        index = 0
        length = len(command)

        while index < length:
            while index < length and command[index] in " \t":
                index += 1
            if index >= length:
                break

            argument: list[str] = []
            in_quotes = False

            while index < length:
                character = command[index]
                if character in " \t" and not in_quotes:
                    break

                if character == "\\":
                    slash_start = index
                    while index < length and command[index] == "\\":
                        index += 1
                    slash_count = index - slash_start

                    if index < length and command[index] == '"':
                        argument.extend("\\" * (slash_count // 2))
                        if slash_count % 2:
                            argument.append('"')
                        else:
                            in_quotes = not in_quotes
                        index += 1
                    else:
                        argument.extend("\\" * slash_count)
                    continue

                if character == '"':
                    in_quotes = not in_quotes
                    index += 1
                    continue

                argument.append(character)
                index += 1

            if in_quotes:
                raise CodeExecutorError("命令包含未闭合的双引号")

            parts.append("".join(argument))
            while index < length and command[index] in " \t":
                index += 1

        return parts

    def _validate_command_parts(self, parts: list[str]) -> None:
        if not parts:
            raise CodeExecutorError("命令不能为空")

    def _command_for_subprocess(
        self,
        command: str | Sequence[str],
        *,
        shell_enabled: bool,
    ) -> str | list[str]:
        """Return one command in the shape required by the selected mode."""

        if isinstance(command, str) and _is_script_template(command):
            return self._split_script_command(command)
        if not shell_enabled:
            return self.split_command(command)
        if isinstance(command, str):
            if not command.strip():
                raise CodeExecutorError("命令不能为空")
            return command
        parts = [str(part) for part in command]
        self._validate_command_parts(parts)
        if self.system == "Windows":
            return subprocess.list2cmdline(parts)
        return shlex.join(parts)

    @staticmethod
    def _command_label(command: str | Sequence[str]) -> str:
        if isinstance(command, str):
            return command.strip().split(maxsplit=1)[0] if command.strip() else "<empty>"
        return str(command[0]) if command else "<empty>"

    def _split_legacy_mricrogl_temp_script(self, command: str) -> list[str] | None:
        pattern = re.compile(
            r"""^TMP=\$\(mktemp\s+[^)]+\);\s*"""
            r"""TMP="\$TMP\.py";\s*"""
            r"""printf\s+'(?P<script>.*?)'\s*>\s*"\$TMP";\s*"""
            r"""(?P<viewer>.+?)\s+"\$TMP";\s*"""
            r"""rm\s+-f\s+"\$TMP"\s*$""",
            re.DOTALL,
        )
        match = pattern.match(command)
        if match is None:
            return None

        viewer_parts = shlex.split(match.group("viewer"), posix=self.system != "Windows")
        self._validate_command_parts(viewer_parts)

        script_text = match.group("script").replace("\\n", "\n")
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".py",
            prefix="easyqc_mgl_",
            delete=False,
        ) as handle:
            handle.write(script_text)
            script_path = Path(handle.name)
        self._pending_temp_files.append(script_path)
        return viewer_parts + [str(script_path)]

    def _split_script_command(self, command: str) -> list[str]:
        """Turn one SCRIPT carrier into a direct viewer + temp-script launch.

        ``SCRIPT <viewer>\\n<body>`` is EasyQC's readable form for
        script-driven viewers (MRIcroGL and friends). The body is written
        verbatim to a temp file and the viewer is launched directly with
        it, regardless of the module's interpreter setting: no shell ever
        parses the script text. The viewer command is validated before the
        temp file exists so a malformed carrier leaks nothing.
        """

        header, _, body = command.partition("\n")
        viewer = header[len(_SCRIPT_KEYWORD):].strip()
        if not viewer:
            raise CodeExecutorError("SCRIPT 模板首行缺少查看器命令")
        if not body.strip():
            raise CodeExecutorError("SCRIPT 模板缺少脚本文本")
        viewer_parts = self.split_command(viewer)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".py",
            prefix="easyqc_script_",
            delete=False,
        ) as handle:
            handle.write(body)
            if not body.endswith("\n"):
                handle.write("\n")
            script_path = Path(handle.name)
        self._pending_temp_files.append(script_path)
        return viewer_parts + [str(script_path)]

    def _consume_pending_temp_files(self) -> list[Path]:
        temp_files = self._pending_temp_files
        self._pending_temp_files = []
        return temp_files

    def _cleanup_temp_files(self, temp_files: Sequence[Path]) -> None:
        for temp_file in temp_files:
            try:
                temp_file.unlink(missing_ok=True)
            except Exception as exc:
                log_error(
                    f"viewer 临时文件清理失败: {temp_file}: {exc}",
                    "CodeExecutor",
                    show_popup=False,
                )

    def _cleanup_process_temp_files(self, process: subprocess.Popen) -> None:
        self._cleanup_temp_files(self._process_temp_files.pop(process.pid, []))

    def _cleanup_process_identity(self, process: subprocess.Popen) -> None:
        self._process_execution_ids.pop(process.pid, None)
        self._process_shell_modes.pop(process.pid, None)
        self._finalized_process_ids.discard(process.pid)
        self._process_stderr_paths.pop(process.pid, None)
        self._process_labels.pop(process.pid, None)

    @staticmethod
    def _read_bounded_stderr(stderr_path: Path) -> str:
        """Return a compact, untrusted diagnostic excerpt without executing it."""

        try:
            with stderr_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - _VIEWER_ERROR_READ_BYTES))
                text = handle.read(_VIEWER_ERROR_READ_BYTES).decode(
                    "utf-8",
                    errors="replace",
                )
        except OSError:
            return ""
        lines = []
        for raw_line in text.splitlines():
            line = "".join(
                character if character.isprintable() else " "
                for character in raw_line
            ).strip()
            if line:
                lines.append(line)
        if not lines:
            return ""
        return " | ".join(lines[-6:])[-_VIEWER_ERROR_LIMIT:]

    def _startup_failure_message(
        self,
        process: subprocess.Popen,
        command_label: str,
        returncode: int,
    ) -> str:
        stderr_path = self._process_stderr_paths.get(process.pid)
        detail = (
            self._read_bounded_stderr(stderr_path)
            if stderr_path is not None
            else ""
        )
        message = f"查看器启动后立即退出: {command_label} (exit={returncode})"
        return f"{message}: {detail}" if detail else message

    def _finalize_process_output(
        self,
        process: subprocess.Popen,
        returncode: int,
    ) -> tuple[CommandOutputEvent, ...]:
        execution_id = self._process_execution_ids.get(process.pid)
        if execution_id is None or process.pid in self._finalized_process_ids:
            return ()
        journal = self._output_journal()
        events = journal.finalize(execution_id, returncode)
        self._finalized_process_ids.add(process.pid)
        return events

    def _forget_process(self, process: subprocess.Popen) -> None:
        if (
            process.pid in self._process_execution_ids
            and process.pid not in self._finalized_process_ids
        ):
            raise RuntimeError(
                "viewer command output must be finalized before process cleanup"
            )
        self.current_processes = [
            current for current in self.current_processes if current is not process
        ]
        self._cleanup_process_temp_files(process)
        self._cleanup_process_identity(process)

    def _render_command_for_output(
        self,
        command: str | Sequence[str],
    ) -> str:
        """Render a sequence command for display without changing execution."""

        if isinstance(command, str):
            return command
        parts = [str(part) for part in command]
        if self.system == "Windows":
            return subprocess.list2cmdline(parts)
        return shlex.join(parts)

    def run_command(
        self,
        command: str | Sequence[str],
        cwd: str | os.PathLike[str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        shell_enabled = self.shell_enabled
        script_carrier = isinstance(command, str) and _is_script_template(command)
        args = self._command_for_subprocess(
            command,
            shell_enabled=shell_enabled,
        )
        command_label = self._command_label(args)
        temp_files = self._consume_pending_temp_files()
        try:
            return subprocess.run(
                args,
                cwd=cwd,
                env=build_external_process_environment(system=self.system),
                shell=shell_enabled and not script_carrier,
                check=False,
                text=True,
                capture_output=True,
                timeout=self.timeout if timeout is None else timeout,
            )
        except OSError as exc:
            log_error(
                f"命令运行失败: {command_label}: {exc}",
                "CodeExecutor",
                show_popup=False,
            )
            raise CodeExecutorError(
                f"命令启动失败: {command_label}: {exc}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeoutError(f"命令超时: {command_label}") from exc
        finally:
            self._cleanup_temp_files(temp_files)

    def start_command(
        self,
        command: str | Sequence[str],
        cwd: str | os.PathLike[str] | None = None,
        output_context: ViewerExecutionContext | None = None,
        shell: bool | None = None,
    ) -> subprocess.Popen:
        self._require_open()
        shell_enabled = self.shell_enabled if shell is None else bool(shell)
        script_carrier = isinstance(command, str) and _is_script_template(command)
        effective_shell = shell_enabled and not script_carrier
        args = self._command_for_subprocess(
            command,
            shell_enabled=shell_enabled,
        )
        command_label = self._command_label(args)
        temp_files = self._consume_pending_temp_files()
        child_environment = build_external_process_environment(system=self.system)
        journal = self._output_journal()
        try:
            capture = journal.begin_execution(
                output_context or _GENERIC_OUTPUT_CONTEXT,
                self._render_command_for_output(command),
            )
        except Exception:
            self._cleanup_temp_files(temp_files)
            raise
        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": child_environment,
            "shell": effective_shell,
            "stdout": capture.stdout_handle,
            "stderr": capture.stderr_handle,
        }

        if self.system == "Windows":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(args, **popen_kwargs)
        except OSError as exc:
            capture.close_parent_handles()
            self._cleanup_temp_files(temp_files)
            log_error(
                f"viewer 启动失败: {command_label}: {exc}",
                "CodeExecutor",
                show_popup=False,
            )
            raise CodeExecutorError(
                f"命令启动失败: {command_label}: {exc}"
            ) from exc
        except Exception:
            capture.close_parent_handles()
            self._cleanup_temp_files(temp_files)
            raise
        self.current_processes.append(process)
        self._process_execution_ids[process.pid] = capture.execution_id
        self._process_shell_modes[process.pid] = effective_shell
        self._process_stderr_paths[process.pid] = capture.stderr_path
        self._process_labels[process.pid] = command_label
        if temp_files:
            self._process_temp_files[process.pid] = temp_files
        try:
            journal.mark_started(capture.execution_id, process.pid)
        except Exception:
            capture.close_parent_handles()
            raise

        try:
            returncode = process.wait(timeout=_VIEWER_STARTUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            return process

        if returncode != 0:
            self._finalize_process_output(process, returncode)
            message = self._startup_failure_message(
                process,
                command_label,
                returncode,
            )
            log_error(message, "CodeExecutor", show_popup=False)
            self._forget_process(process)
            raise CodeExecutorError(message)

        # Keep a successful short-lived wrapper tracked until normal cleanup.
        # A Shell wrapper may have delegated to a background GUI process that
        # still needs any command temp files for a short period.
        return process

    def start_commands(
        self,
        commands: Mapping[Any, str | Sequence[str]],
        control: bool = False,
        cwd: str | os.PathLike[str] | None = None,
        output_contexts: Mapping[Any, ViewerExecutionContext] | None = None,
        shell: bool | None = None,
    ) -> list[subprocess.Popen]:
        self._require_open()
        if output_contexts is not None:
            missing_contexts = [
                key for key in commands if key not in output_contexts
            ]
            if missing_contexts:
                raise CodeExecutorError(
                    "命令输出上下文缺少命令键: "
                    f"{missing_contexts!r}"
                )
            invalid_contexts = [
                key
                for key in commands
                if not isinstance(
                    output_contexts[key],
                    ViewerExecutionContext,
                )
            ]
            if invalid_contexts:
                raise CodeExecutorError(
                    "命令输出上下文类型无效，命令键: "
                    f"{invalid_contexts!r}"
                )
        if control:
            self.close_current_processes()

        processes = []
        for key, command in commands.items():
            context = (
                output_contexts[key]
                if output_contexts is not None
                else None
            )
            processes.append(
                self.start_command(
                    command,
                    cwd=cwd,
                    output_context=context,
                    shell=shell,
                )
            )
        return processes

    def command_output_since(self, after_sequence: int) -> CommandOutputBatch:
        """Poll bounded output and return a non-destructive sequence view."""

        self._require_open()
        journal = self._output_journal()
        journal.poll()
        for process in tuple(self.current_processes):
            returncode = process.poll()
            if returncode is None:
                continue
            if (
                self._process_shell_modes.get(process.pid, False)
                and returncode == 0
            ):
                continue
            self._finalize_process_output(process, returncode)
            self._forget_process(process)
        return journal.events_since(after_sequence)

    def close_current_processes(self, timeout: float = 5) -> None:
        remaining: list[subprocess.Popen] = []

        for process in self.current_processes:
            try:
                returncode = process.poll()
                if returncode is not None:
                    self._finalize_process_output(process, returncode)
                    if returncode != 0:
                        command_label = self._process_labels.get(
                            process.pid,
                            "<viewer>",
                        )
                        log_error(
                            self._startup_failure_message(
                                process,
                                command_label,
                                returncode,
                            ),
                            "CodeExecutor",
                            show_popup=False,
                        )
                    self._forget_process(process)
                    continue

                if self.system == "Windows":
                    process.terminate()
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)

                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    if self.system == "Windows":
                        process.kill()
                    else:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    returncode = process.wait()
                if returncode is None:
                    returncode = process.returncode
                if returncode is None:
                    raise RuntimeError(
                        "viewer process ended without an observable return code"
                    )
                self._finalize_process_output(process, returncode)
                self._forget_process(process)
            except ProcessLookupError as exc:
                try:
                    returncode = process.poll()
                    if returncode is None:
                        raise RuntimeError(
                            "viewer process disappeared before its return code "
                            f"became observable: {exc}"
                        ) from exc
                    self._finalize_process_output(process, returncode)
                    self._forget_process(process)
                except Exception as recovery_exc:
                    log_error(
                        "viewer 进程清理失败 "
                        f"(PID {getattr(process, 'pid', '?')}): "
                        f"{recovery_exc}",
                        "CodeExecutor",
                        show_popup=False,
                    )
                    remaining.append(process)
            except Exception as exc:
                log_error(
                    f"viewer 进程清理失败 (PID {getattr(process, 'pid', '?')}): "
                    f"{exc}",
                    "CodeExecutor",
                    show_popup=False,
                )
                remaining.append(process)

        self.current_processes = remaining

    def close(self) -> None:
        """Finalize managed viewers, then close the application journal once."""

        if self._closed:
            return
        self.close_current_processes()
        if self.current_processes:
            process_ids = [
                getattr(process, "pid", "?")
                for process in self.current_processes
            ]
            raise CodeExecutorError(
                "viewer process cleanup is incomplete for PID(s): "
                f"{process_ids!r}"
            )
        if self._command_output_journal is not None:
            self._command_output_journal.close()
        self._closed = True


def validate_template_columns(code: str, available_columns: set[str]) -> list[str]:
    """Report referenced identifier names absent from the available mapping.

    ``available_columns`` should be the union of easyqc_all columns and constants
    keys. The sorted, unique report is advisory, not a launch validation gate:
    a name in ${var}/{var}/$var may intentionally belong to the Shell rather
    than EasyQC. Shell command substitutions such as $(mktemp) are not names.
    """
    referenced: set[str] = set()
    for match in CodeExecutor._BRACE_PLACEHOLDER.finditer(code):
        name = match.group(1) or match.group(2)
        if name:
            referenced.add(name)
    for match in CodeExecutor._BARE_DOLLAR.finditer(code):
        referenced.add(match.group(1))

    missing = referenced - set(available_columns)
    return sorted(missing)
