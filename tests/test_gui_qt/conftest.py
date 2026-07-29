"""Per-test isolation for process-global Qt presentation settings."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings


def _detach_language_controller(application) -> None:
    controller = getattr(application, "_easyqc_language_controller", None)
    if controller is None:
        return
    application.removeEventFilter(controller)
    translator = getattr(controller, "_qt_translator", None)
    if translator is not None:
        application.removeTranslator(translator)
        translator.deleteLater()
        controller._qt_translator = None
    delattr(application, "_easyqc_language_controller")


@pytest.fixture(autouse=True)
def isolate_default_qt_settings(qapp, tmp_path: Path):
    """Keep QSettings and the session QApplication from leaking across tests."""

    _detach_language_controller(qapp)
    previous_format = QSettings.defaultFormat()
    settings_root = tmp_path / "qt-settings"
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(
        QSettings.IniFormat,
        QSettings.UserScope,
        str(settings_root),
    )
    QSettings.setPath(
        QSettings.IniFormat,
        QSettings.SystemScope,
        str(settings_root / "system"),
    )
    yield
    _detach_language_controller(qapp)
    QSettings.setDefaultFormat(previous_format)
