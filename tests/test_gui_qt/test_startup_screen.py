from __future__ import annotations

from PySide6.QtCore import QRect, QSettings
from PySide6.QtWidgets import QLabel, QProgressBar

from gui_qt.i18n import LanguageController
from gui_qt.startup_screen import QtStartupScreen


def _controller(tmp_path) -> LanguageController:
    return LanguageController(
        settings=QSettings(str(tmp_path / "startup-language.ini"), QSettings.IniFormat)
    )


def test_startup_screen_has_accessible_indeterminate_status(qtbot, tmp_path):
    screen = QtStartupScreen(_controller(tmp_path))
    qtbot.addWidget(screen)
    screen.show()

    progress = screen.findChild(QProgressBar, "startupProgress")
    status = screen.findChild(QLabel, "startupStatus")

    assert screen.objectName() == "startupScreen"
    assert screen.accessibleName() == "EasyQC 启动"
    assert progress.minimum() == 0
    assert progress.maximum() == 0
    assert status.text() == "正在准备工作区…"


def test_startup_screen_retranslates_current_status_in_place(qtbot, tmp_path):
    controller = _controller(tmp_path)
    screen = QtStartupScreen(controller)
    qtbot.addWidget(screen)
    screen.set_status("startup.loading_project")

    controller.set_language("en")

    assert screen.accessibleName() == "EasyQC startup"
    assert screen.status_label.text() == "Loading project data…"
    assert screen.subtitle_label.text() == "Reliable quality control, focused on the work."


def test_startup_screen_centers_in_offset_available_geometry(qtbot, tmp_path):
    class SyntheticScreen:
        @staticmethod
        def availableGeometry():
            return QRect(1600, 80, 1200, 800)

    startup = QtStartupScreen(_controller(tmp_path))
    qtbot.addWidget(startup)

    startup.center_on_screen(SyntheticScreen())

    assert startup.x() == 1940
    assert startup.y() == 354


def test_startup_show_event_invokes_centering(qtbot, tmp_path, monkeypatch):
    startup = QtStartupScreen(_controller(tmp_path))
    qtbot.addWidget(startup)
    calls = []
    monkeypatch.setattr(
        startup,
        "center_on_screen",
        lambda _screen=None: calls.append("center"),
    )

    startup.show()

    assert calls == ["center"]
