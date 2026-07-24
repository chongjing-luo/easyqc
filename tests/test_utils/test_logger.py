import os
import inspect
import time
from pathlib import Path

import pytest

from utils import logger as logger_module
from utils.logger import EasyQCLogger, LogContext, log_error, log_function, log_info


def test_logger_source_does_not_import_tkinter() -> None:
    source = inspect.getsource(logger_module)

    assert "import tkinter" not in source
    assert "from tkinter" not in source
    assert "import PySide6" not in source
    assert "from PySide6" not in source


def test_log_directory_precedence_is_project_override_then_platform_default(
    tmp_path, monkeypatch
) -> None:
    native_dir = tmp_path / "native" / "EasyQC" / "log"

    class FakePlatformDirs:
        def __init__(self, appname, appauthor):
            assert appname == "EasyQC"
            assert appauthor is False

        @property
        def user_log_path(self) -> Path:
            return native_dir

    monkeypatch.setattr(logger_module, "PlatformDirs", FakePlatformDirs, raising=False)

    project_decision = logger_module.resolve_log_dir(
        project_root=tmp_path / "project",
        environ={"EASYQC_LOG_DIR": str(tmp_path / "override")},
    )
    override_decision = logger_module.resolve_log_dir(
        environ={"EASYQC_LOG_DIR": str(tmp_path / "override")}
    )
    native_decision = logger_module.resolve_log_dir(environ={})

    assert project_decision.path == (tmp_path / "project" / "logs").resolve()
    assert project_decision.source == "project_root"
    assert override_decision.path == (tmp_path / "override").resolve()
    assert override_decision.source == "environment"
    assert native_decision.path == native_dir.resolve()
    assert native_decision.source == "platformdirs"


def test_relative_log_override_is_visible_and_not_silently_replaced(
    tmp_path, monkeypatch
) -> None:
    class UnexpectedPlatformDirs:
        def __init__(self, *args, **kwargs):
            raise AssertionError("platformdirs must not replace an invalid explicit override")

    monkeypatch.setattr(
        logger_module,
        "PlatformDirs",
        UnexpectedPlatformDirs,
        raising=False,
    )

    decision = logger_module.resolve_log_dir(
        environ={"EASYQC_LOG_DIR": "relative/logs"}
    )

    assert decision.path is None
    assert decision.source == "environment"
    assert decision.attempted_path == Path("relative/logs")
    assert "absolute" in decision.problem.lower()


def test_path_resolution_io_failure_becomes_visible_degraded_decision(
    tmp_path, monkeypatch
) -> None:
    attempted = tmp_path / "resolve-denied"

    def deny_resolution(self, *args, **kwargs):
        raise PermissionError("controlled path resolution denial")

    with monkeypatch.context() as scoped:
        scoped.setattr(type(Path()), "resolve", deny_resolution)
        decision = logger_module.resolve_log_dir(
            environ={"EASYQC_LOG_DIR": str(attempted)}
        )

    assert decision.path is None
    assert decision.source == "environment"
    assert decision.attempted_path == attempted
    assert "controlled path resolution denial" in decision.problem


def test_unexpected_path_resolution_error_propagates(tmp_path, monkeypatch) -> None:
    def programming_error(self, *args, **kwargs):
        raise AssertionError("controlled programming error")

    with monkeypatch.context() as scoped:
        scoped.setattr(type(Path()), "resolve", programming_error)
        with pytest.raises(AssertionError, match="controlled programming error"):
            logger_module.resolve_log_dir(
                environ={"EASYQC_LOG_DIR": str(tmp_path / "unexpected")}
            )


def test_file_handler_failure_degrades_without_fake_active_path(
    tmp_path, monkeypatch
) -> None:
    real_file_handler = logger_module.logging.FileHandler

    def deny_file_handler(*args, **kwargs):
        raise PermissionError("controlled log denial")

    with monkeypatch.context() as scoped:
        scoped.setattr(logger_module.logging, "FileHandler", deny_file_handler)
        instance = EasyQCLogger(project_root=tmp_path / "denied")
        status = logger_module.get_logging_status()

        assert status.file_logging_enabled is False
        assert status.active_log_file is None
        assert status.attempted_log_dir == (tmp_path / "denied" / "logs").resolve()
        assert "controlled log denial" in status.warning_message
        assert status.stream_logging_enabled is True
        assert instance.get_log_file_path() is None

    assert logger_module.logging.FileHandler is real_file_handler
    EasyQCLogger(project_root=tmp_path / "recovered")


def test_missing_standard_streams_do_not_install_invalid_stream_handler(
    tmp_path, monkeypatch
) -> None:
    with monkeypatch.context() as scoped:
        scoped.setattr(logger_module.sys, "stdout", None)
        scoped.setattr(logger_module.sys, "stderr", None)

        EasyQCLogger(project_root=tmp_path / "no-stream")
        status = logger_module.get_logging_status()

        assert status.stream_logging_enabled is False
        assert status.file_logging_enabled is True
        assert status.active_log_file is not None
        assert Path(status.active_log_file).exists()

    EasyQCLogger(project_root=tmp_path / "stream-recovered")


def test_logger_can_use_configurable_project_root(tmp_path) -> None:
    instance = EasyQCLogger(project_root=tmp_path)

    log_info("hello", "LoggerTest")

    assert instance.get_log_file_path().startswith(str(tmp_path / "logs"))
    assert (tmp_path / "logs").exists()

    status = logger_module.get_logging_status()
    assert status.file_logging_enabled is True
    assert status.active_log_file == Path(instance.get_log_file_path())
    assert status.warning_message is None


def test_log_error_popup_flag_is_text_only() -> None:
    log_error("text only popup compatibility", "LoggerTest", show_popup=True)


def test_log_function_records_args_and_preserves_function_metadata() -> None:
    @log_function("LoggerTest")
    def add(left, right=0):
        return left + right

    assert add.__name__ == "add"
    assert add(2, right=3) == 5


def test_log_context_writes_completion_message(tmp_path) -> None:
    instance = EasyQCLogger(project_root=tmp_path)

    with LogContext("context operation", "LoggerTest"):
        pass

    log_text = (tmp_path / "logs" / Path(instance.get_log_file_path()).name).read_text(encoding="utf-8")
    assert "开始执行: context operation" in log_text
    assert "完成执行: context operation" in log_text


def test_old_log_cleanup_stays_inside_the_active_directory(tmp_path) -> None:
    instance = EasyQCLogger(project_root=tmp_path)
    old_log = tmp_path / "logs" / "easyqc_20000101.log"
    unrelated = tmp_path / "logs" / "keep.txt"
    old_log.write_text("old", encoding="utf-8")
    unrelated.write_text("keep", encoding="utf-8")
    old_timestamp = time.time() - 31 * 24 * 60 * 60
    os.utime(old_log, (old_timestamp, old_timestamp))
    expected_log_dir = (tmp_path / "logs").resolve()
    matched_before = tuple(
        sorted(path.name for path in expected_log_dir.glob("easyqc_*.log"))
    )
    observed_age_seconds = time.time() - old_log.stat().st_mtime

    assert instance.log_dir == expected_log_dir
    assert instance.status.file_logging_enabled is True
    assert old_log.name in matched_before
    assert observed_age_seconds > 30 * 24 * 60 * 60

    instance.clear_old_logs(days=30)

    active_log = Path(instance.get_log_file_path())
    diagnostic = (
        f"log_dir={instance.log_dir}; matched_before={matched_before}; "
        f"age_seconds={observed_age_seconds}; "
        f"active_log={active_log.read_text(encoding='utf-8')!r}"
    )
    assert not old_log.exists(), diagnostic
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_logging_status_is_immutable(tmp_path) -> None:
    EasyQCLogger(project_root=tmp_path)
    status = logger_module.get_logging_status()

    with pytest.raises((AttributeError, TypeError)):
        status.file_logging_enabled = False
