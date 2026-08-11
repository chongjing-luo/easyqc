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
    ) -> None:
        if not isinstance(shell_enabled, bool):
            raise TypeError("shell_enabled must be a Boolean")
        self.shell_enabled = shell_enabled
        self.timeout = timeout
        self.system = system or platform.system()
        self.current_processes: list[subprocess.Popen] = []
        self._pending_temp_files: list[Path] = []
        self._process_temp_files: dict[int, list[Path]] = {}
        self._process_stderr_files: dict[int, Path] = {}
        self._process_labels: dict[int, str] = {}

    def set_shell_enabled(self, enabled: bool) -> None:
        """Select direct or Shell execution for future commands."""

        if not isinstance(enabled, bool):
            raise TypeError("shell_enabled must be a Boolean")
        self.shell_enabled = enabled

    # P3-B: placeholders are ${name} and {name} (the two explicit forms in real
    # settings). Bare $name is ALSO substituted for backward compat, but it is
    # NOT subject to unresolved-rejection (because $TMP / $(mktemp) / $HOME are
    # shell constructs, not template vars — F-VIEW-4 MRIcroGL compatibility).
    _BRACE_PLACEHOLDER = re.compile(r"\$\{(\w+)\}|\{(\w+)\}")
    _BARE_DOLLAR = re.compile(r"\$(\w+)")
    _PLACEHOLDER = re.compile(
        r"\$\{(?P<dollar_brace>\w+)\}"
        r"|\{(?P<brace>\w+)\}"
        r"|\$(?P<bare>\w+)"
    )

    def parse_template(self, template: str, variables: Mapping[str, Any]) -> str:
        var_lookup = dict(variables)
        unresolved: set[str] = set()

        def _substitute(match: re.Match) -> str:
            name = (
                match.group("dollar_brace")
                or match.group("brace")
                or match.group("bare")
            )
            if name in var_lookup:
                return str(var_lookup[name])
            if match.group("bare") is None:
                unresolved.add(name)
            return match.group(0)

        result = self._PLACEHOLDER.sub(_substitute, template)

        # Fail loud on author-intended placeholders that have no variable.
        # Bare $word (shell vars like $TMP) is NOT checked — F-VIEW-4.
        if unresolved:
            raise CodeExecutorError(
                f"模板含未解析的占位符(变量不存在): {sorted(unresolved)}"
            )
        return result

    def render_command_plan(
        self,
        template: str,
        variables: Mapping[str, Any],
    ) -> tuple[str, dict[int, str]]:
        """Expand one viewer template into an ordered, explicit command plan."""

        rendered = self.parse_template(template, variables)
        if rendered.startswith("MULTICMD"):
            command_text = rendered.replace("MULTICMD", "", 1).strip()
            commands = [part.strip() for part in command_text.split(";|") if part.strip()]
            return command_text, {index: command for index, command in enumerate(commands)}
        if "MULTICMD" in rendered:
            prefix, command_text = rendered.split("MULTICMD", 1)
            prefix = prefix.strip()
            if prefix and not prefix.endswith(";"):
                prefix += ";"
            commands = [part.strip() for part in command_text.strip().split(";|") if part.strip()]
            return rendered, {
                index: f"{prefix}{command}"
                for index, command in enumerate(commands)
            }
        return rendered, {0: rendered}

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

    def _cleanup_process_diagnostics(self, process: subprocess.Popen) -> None:
        stderr_path = self._process_stderr_files.pop(process.pid, None)
        self._process_labels.pop(process.pid, None)
        if stderr_path is not None:
            self._cleanup_temp_files([stderr_path])

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
        stderr_path = self._process_stderr_files.get(process.pid)
        detail = (
            self._read_bounded_stderr(stderr_path)
            if stderr_path is not None
            else ""
        )
        message = f"查看器启动后立即退出: {command_label} (exit={returncode})"
        return f"{message}: {detail}" if detail else message

    def _forget_process(self, process: subprocess.Popen) -> None:
        self.current_processes = [
            current for current in self.current_processes if current is not process
        ]
        self._cleanup_process_temp_files(process)
        self._cleanup_process_diagnostics(process)

    def run_command(
        self,
        command: str | Sequence[str],
        cwd: str | os.PathLike[str] | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        shell_enabled = self.shell_enabled
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
                shell=shell_enabled,
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
    ) -> subprocess.Popen:
        shell_enabled = self.shell_enabled
        args = self._command_for_subprocess(
            command,
            shell_enabled=shell_enabled,
        )
        command_label = self._command_label(args)
        temp_files = self._consume_pending_temp_files()
        child_environment = build_external_process_environment(system=self.system)
        stderr_file = tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix="easyqc_viewer_",
            suffix=".stderr",
            delete=False,
        )
        stderr_path = Path(stderr_file.name)
        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "env": child_environment,
            "shell": shell_enabled,
            "stdout": subprocess.DEVNULL,
            "stderr": stderr_file,
        }

        if self.system == "Windows":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(args, **popen_kwargs)
        except OSError as exc:
            stderr_file.close()
            self._cleanup_temp_files([stderr_path])
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
            stderr_file.close()
            self._cleanup_temp_files([stderr_path])
            self._cleanup_temp_files(temp_files)
            raise
        finally:
            if not stderr_file.closed:
                stderr_file.close()
        self.current_processes.append(process)
        self._process_stderr_files[process.pid] = stderr_path
        self._process_labels[process.pid] = command_label
        if temp_files:
            self._process_temp_files[process.pid] = temp_files

        try:
            returncode = process.wait(timeout=_VIEWER_STARTUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            return process

        if returncode != 0:
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
    ) -> list[subprocess.Popen]:
        if control:
            self.close_current_processes()

        processes = []
        for _, command in commands.items():
            processes.append(self.start_command(command, cwd=cwd))
        return processes

    def close_current_processes(self, timeout: float = 5) -> None:
        remaining: list[subprocess.Popen] = []

        for process in self.current_processes:
            if process.poll() is not None:
                if process.returncode not in (None, 0):
                    command_label = self._process_labels.get(
                        process.pid,
                        "<viewer>",
                    )
                    log_error(
                        self._startup_failure_message(
                            process,
                            command_label,
                            process.returncode,
                        ),
                        "CodeExecutor",
                        show_popup=False,
                    )
                self._cleanup_process_temp_files(process)
                self._cleanup_process_diagnostics(process)
                continue

            try:
                if self.system == "Windows":
                    process.terminate()
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)

                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    if self.system == "Windows":
                        process.kill()
                    else:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    process.wait()
                self._cleanup_process_temp_files(process)
                self._cleanup_process_diagnostics(process)
            except ProcessLookupError:
                self._cleanup_process_temp_files(process)
                self._cleanup_process_diagnostics(process)
                continue
            except Exception as exc:
                log_error(
                    f"viewer 进程清理失败 (PID {getattr(process, 'pid', '?')}): "
                    f"{exc}",
                    "CodeExecutor",
                    show_popup=False,
                )
                remaining.append(process)

        self.current_processes = remaining


def validate_template_columns(code: str, available_columns: set[str]) -> list[str]:
    """P3-B / F-IMP-6: return placeholder names referenced in ``code`` that are
    NOT in ``available_columns`` (sorted, unique). Detects a module code that
    references a removed/renamed subject column BEFORE viewer launch fails.

    ``available_columns`` should be the union of easyqc_all columns and constants
    keys — i.e. everything generate_code would inject as template variables.
    Shell constructs ($TMP, $(mktemp)) are ignored (only ${var}/{var}/$var with
    identifier names are considered placeholders).
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
