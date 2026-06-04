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


DEFAULT_ALLOWED_COMMANDS = (
    "python",
    "python3",
    "fslview",
    "mricron",
    "freeview",
    "itksnap",
    "open",
    'wb_view',
    'mricroGL',
    'mricrogl',
    'MRIcroGL',
)


class CodeExecutorError(Exception):
    """Base exception for controlled command execution errors."""


class CommandNotAllowedError(CodeExecutorError):
    """Raised when a command is not in the allowlist."""


class CommandTimeoutError(CodeExecutorError):
    """Raised when a command exceeds the configured timeout."""


class CodeExecutor:
    def __init__(
        self,
        allowed_commands: Sequence[str] | None = None,
        timeout: float = 300,
        system: str | None = None,
    ) -> None:
        self.allowed_commands = set(allowed_commands or DEFAULT_ALLOWED_COMMANDS)
        self.timeout = timeout
        self.system = system or platform.system()
        self.current_processes: list[subprocess.Popen] = []
        self._pending_temp_files: list[Path] = []
        self._process_temp_files: dict[int, list[Path]] = {}

    def parse_template(self, template: str, variables: Mapping[str, Any]) -> str:
        result = template
        for name, value in variables.items():
            value_text = str(value)
            result = result.replace(f"${{{name}}}", value_text)
            result = result.replace(f"${name}", value_text)
            result = result.replace(f"{{{name}}}", value_text)
        return result

    def split_command(self, command: str | Sequence[str]) -> list[str]:
        if isinstance(command, str):
            command = self._normalize_command_text(command)
            legacy_mricrogl_parts = self._split_legacy_mricrogl_temp_script(command)
            if legacy_mricrogl_parts is not None:
                return legacy_mricrogl_parts
            parts = shlex.split(command, posix=self.system != "Windows")
        else:
            parts = [str(part) for part in command]

        self._validate_command_parts(parts, reject_shell_controls=isinstance(command, str))
        return parts

    def _normalize_command_text(self, command: str) -> str:
        return (
            command
            .replace("\\\r\n", " ")
            .replace("\\\n", " ")
            .strip()
        )

    def _validate_command_parts(self, parts: list[str], reject_shell_controls: bool = True) -> None:
        if not parts:
            raise CommandNotAllowedError("空命令不允许执行")

        if reject_shell_controls and any(re.search(r";|&&|\|\||\|", part) for part in parts):
            raise CommandNotAllowedError("不允许 shell 控制操作符")

        executable = Path(parts[0]).name
        if executable not in self.allowed_commands:
            raise CommandNotAllowedError(f"命令不在白名单中: {executable}")

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
        args = self.split_command(command)
        temp_files = self._consume_pending_temp_files()
        try:
            return subprocess.run(
                args,
                cwd=cwd,
                shell=False,
                check=False,
                text=True,
                capture_output=True,
                timeout=self.timeout if timeout is None else timeout,
            )
        except OSError as exc:
            raise CodeExecutorError(f"命令启动失败: {args[0]}: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandTimeoutError(f"命令超时: {args[0]}") from exc
        finally:
            self._cleanup_temp_files(temp_files)

    def start_command(
        self,
        command: str | Sequence[str],
        cwd: str | os.PathLike[str] | None = None,
    ) -> subprocess.Popen:
        args = self.split_command(command)
        temp_files = self._consume_pending_temp_files()
        popen_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "shell": False,
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
            raise CodeExecutorError(f"命令启动失败: {args[0]}: {exc}") from exc
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
