from __future__ import annotations

import os
import platform
import re
import shlex
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from utils.logger import log_error


class CodeExecutorError(Exception):
    """Base exception for controlled command execution errors."""


class CommandTimeoutError(CodeExecutorError):
    """Raised when a command exceeds the configured timeout."""


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

    def parse_template(self, template: str, variables: Mapping[str, Any]) -> str:
        var_lookup = dict(variables)

        # Detect unresolved placeholders from the ORIGINAL template (a variable
        # VALUE that happens to contain {x} must not be flagged as unresolved —
        # only placeholders the author wrote in the template are checked).
        unresolved: set[str] = set()
        for match in self._BRACE_PLACEHOLDER.finditer(template):
            name = match.group(1) or match.group(2)
            if name and name not in var_lookup:
                unresolved.add(name)

        # Pass 1 — ${var} and {var}: single-pass via re.sub callback, so a
        # variable VALUE containing $ or { is never re-interpreted by a later
        # variable (the old multi-pass str.replace bug).
        def _brace_sub(match: re.Match) -> str:
            name = match.group(1) or match.group(2)
            if name in var_lookup:
                return str(var_lookup[name])
            return match.group(0)  # leave in place

        result = self._BRACE_PLACEHOLDER.sub(_brace_sub, template)

        # Pass 2 — bare $var: backward-compat substitution. Only replaces names
        # that ARE variables; $TMP / $(...) pass through untouched.
        def _dollar_sub(match: re.Match) -> str:
            name = match.group(1)
            if name in var_lookup:
                return str(var_lookup[name])
            return match.group(0)

        result = self._BARE_DOLLAR.sub(_dollar_sub, result)

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
            except Exception:
                pass

    def _cleanup_process_temp_files(self, process: subprocess.Popen) -> None:
        self._cleanup_temp_files(self._process_temp_files.pop(process.pid, []))

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
        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "shell": shell_enabled,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }

        if self.system == "Windows":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            popen_kwargs["preexec_fn"] = os.setsid

        try:
            process = subprocess.Popen(args, **popen_kwargs)
        except OSError as exc:
            self._cleanup_temp_files(temp_files)
            log_error(
                f"viewer 启动失败: {command_label}: {exc}",
                "CodeExecutor",
                show_popup=False,
            )
            raise CodeExecutorError(
                f"命令启动失败: {command_label}: {exc}"
            ) from exc
        self.current_processes.append(process)
        if temp_files:
            self._process_temp_files[process.pid] = temp_files
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
                self._cleanup_process_temp_files(process)
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
            except ProcessLookupError:
                self._cleanup_process_temp_files(process)
                continue
            except Exception:
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
