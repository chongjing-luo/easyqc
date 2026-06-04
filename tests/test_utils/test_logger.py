import inspect
from pathlib import Path

from utils import logger as logger_module
from utils.logger import EasyQCLogger, LogContext, log_error, log_function, log_info


def test_logger_source_does_not_import_tkinter() -> None:
    source = inspect.getsource(logger_module)

    assert "import tkinter" not in source
    assert "from tkinter" not in source


def test_logger_can_use_configurable_project_root(tmp_path) -> None:
    instance = EasyQCLogger(project_root=tmp_path)

    log_info("hello", "LoggerTest")

    assert instance.get_log_file_path().startswith(str(tmp_path / "logs"))
    assert (tmp_path / "logs").exists()


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
