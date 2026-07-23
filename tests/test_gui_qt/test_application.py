from __future__ import annotations

import ast
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from pandas.testing import assert_frame_equal
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QTableView

from core.app_services import build_app_services
from gui_qt import application as application_module
from gui_qt.application import (
    build_preview_window,
    get_or_create_qapplication,
)


def test_get_or_create_qapplication_reuses_instance_without_overriding_host_theme(qapp):
    original_stylesheet = qapp.styleSheet()
    original_font = QFont(qapp.font())
    original_palette = QPalette(qapp.palette())
    host_stylesheet = "QWidget { color: #123456; }"
    host_font = QFont(original_font)
    host_font.setPointSize(max(8, original_font.pointSize() + 1))
    host_palette = QPalette(original_palette)
    host_palette.setColor(QPalette.ColorRole.Window, QColor("#abcdef"))
    qapp.setStyleSheet(host_stylesheet)
    qapp.setFont(host_font)
    qapp.setPalette(host_palette)
    style_before = qapp.style()

    try:
        app = get_or_create_qapplication(["easyqc-test"])

        assert app is qapp
        assert QApplication.instance() is app
        assert app.style() is style_before
        assert app.styleSheet() == host_stylesheet
        assert app.font() == host_font
        assert app.palette() == host_palette
        assert app.applicationName() == "EasyQC"
        assert app.organizationName() == "EasyQC"
    finally:
        qapp.setStyleSheet(original_stylesheet)
        qapp.setFont(original_font)
        qapp.setPalette(original_palette)


def test_preview_window_renders_injected_core_table_and_closes_cleanly(qtbot, tmp_path):
    services = build_app_services(tmp_path / "projects.json")
    source = pd.DataFrame(
        {"ezqcid": ["SUB001", "SUB002"], "status": ["pending", "rated"]}
    )
    original = source.copy(deep=True)

    window = build_preview_window(services, source)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)

    table = window.findChild(QTableView, "previewTable")
    assert table is not None
    assert window.centralWidget().objectName() == "qtPreviewRoot"
    assert table.model().data(table.model().index(1, 0)) == "SUB002"
    assert window.services is services
    assert "2 / 2" in window.status_label.text()
    assert window.windowTitle() == "EasyQC"
    assert window.accessibleName() == "EasyQC 表格预览"
    assert_frame_equal(source, original)

    window.close()
    assert not window.isVisible()


def test_product_preview_event_loop_exits_cleanly_offscreen(tmp_path, easyqc_root):
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["EASYQC_TEST_REGISTRY"] = str(tmp_path / "projects.json")
    program = textwrap.dedent(
        """
        import os
        from pathlib import Path

        import pandas as pd

        from core.app_services import build_app_services
        from gui_qt.application import run_qt_preview

        services = build_app_services(Path(os.environ["EASYQC_TEST_REGISTRY"]))
        source = pd.DataFrame({"ezqcid": ["SUB001"], "status": ["pending"]})
        raise SystemExit(
            run_qt_preview(
                ["easyqc-test"],
                services,
                source,
                exit_after_ms=0,
            )
        )
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=easyqc_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_empty_preview_explains_that_no_project_table_is_connected(qtbot, tmp_path):
    services = build_app_services(tmp_path / "projects.json")
    window = build_preview_window(
        services,
        pd.DataFrame(columns=["ezqcid", "status"]),
    )
    qtbot.addWidget(window)

    empty_state = window.findChild(QLabel, "previewEmptyState")
    table = window.findChild(QTableView, "previewTable")

    assert empty_state is not None
    assert not empty_state.isHidden()
    assert "没有可显示" in empty_state.text()
    assert table.accessibleName() == "EasyQC 质控前名单"


def test_gui_qt_package_has_no_tkinter_dependency():
    for module_name in tuple(sys.modules):
        if module_name == "gui_qt" or module_name.startswith("gui_qt."):
            module = sys.modules[module_name]
            source_file = getattr(module, "__file__", None)
            if source_file:
                tree = ast.parse(Path(source_file).read_text(encoding="utf-8"))
                imports = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.update(alias.name.split(".")[0] for alias in node.names)
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        imports.add(node.module.split(".")[0])
                assert "tkinter" not in imports


def test_qt_startup_warning_is_noop_when_logging_is_healthy(monkeypatch):
    callbacks = []
    monkeypatch.setattr(
        application_module,
        "QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: callbacks.append((delay, callback))),
    )

    application_module.schedule_qt_startup_warning(object(), None)
    application_module.schedule_qt_startup_warning(object(), "")

    assert callbacks == []


def test_qt_startup_warning_schedules_exactly_one_toolkit_dialog(monkeypatch):
    callbacks = []
    shown = []
    window = object()
    monkeypatch.setattr(
        application_module,
        "QTimer",
        SimpleNamespace(singleShot=lambda delay, callback: callbacks.append((delay, callback))),
    )
    monkeypatch.setattr(
        application_module,
        "QMessageBox",
        SimpleNamespace(
            warning=lambda parent, title, message: shown.append((parent, title, message))
        ),
    )

    application_module.schedule_qt_startup_warning(window, "file logging unavailable")

    assert len(callbacks) == 1
    assert callbacks[0][0] == 0
    callbacks[0][1]()
    assert shown == [(window, "日志记录受限", "file logging unavailable")]


def test_qt_event_loop_consumes_current_logging_status_once(monkeypatch):
    scheduled = []

    class FakeApplication:
        def exec(self):
            return 0

        def processEvents(self):
            pass

    class FakeWindow:
        def __init__(self, services, source=None):
            self.services = services
            self.source = source

        def show(self):
            pass

        def close(self):
            pass

        def deleteLater(self):
            pass

    fake_app = FakeApplication()
    monkeypatch.setattr(
        application_module,
        "get_or_create_qapplication",
        lambda _argv=None: fake_app,
    )
    monkeypatch.setattr(application_module, "QtMainWindow", FakeWindow)
    monkeypatch.setattr(
        application_module,
        "get_logging_status",
        lambda: SimpleNamespace(warning_message="degraded"),
    )
    monkeypatch.setattr(
        application_module,
        "schedule_qt_startup_warning",
        lambda window, message: scheduled.append((window, message)),
    )

    assert application_module.run_qt_preview(["easyqc-test"], object()) == 0
    assert len(scheduled) == 1
    assert scheduled[0][1] == "degraded"
