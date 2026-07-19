"""QApplication lifecycle and explicit Qt preview launcher."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from core.app_services import AppServices, build_app_services
from gui_qt.main_window import QtMainWindow
from gui_qt.theme import apply_application_theme
from utils.logger import get_logging_status


def schedule_qt_startup_warning(window, message: str | None) -> None:
    """Schedule one logging-degradation warning after Qt is ready."""

    if not message:
        return
    QTimer.singleShot(
        0,
        lambda: QMessageBox.warning(
            window,
            "日志记录受限",
            message,
        ),
    )


def get_or_create_qapplication(argv: Sequence[str] | None = None) -> QApplication:
    """Return the process's sole QApplication and apply EasyQC content style."""

    app = QApplication.instance()
    if app is None:
        app = QApplication(list(argv or sys.argv))
    apply_application_theme(app)
    return app


def build_preview_window(services: AppServices, source: pd.DataFrame) -> QtMainWindow:
    """Build one preview window from injected services and a copied table."""

    get_or_create_qapplication()
    return QtMainWindow(services=services, source=source)


def build_product_window(services: AppServices) -> QtMainWindow:
    """Build the routed Qt shell; project materialization starts in background."""

    get_or_create_qapplication()
    return QtMainWindow(services=services)


def run_qt_preview(
    argv: Sequence[str],
    services: AppServices,
    source: pd.DataFrame | None = None,
    *,
    exit_after_ms: int | None = None,
) -> int:
    """Run exactly one Qt preview event loop and return its exit code."""

    app = get_or_create_qapplication(argv)
    window = QtMainWindow(services=services, source=source)
    window.show()
    schedule_qt_startup_warning(
        window,
        get_logging_status().warning_message,
    )
    if exit_after_ms is not None:
        QTimer.singleShot(max(0, int(exit_after_ms)), app.quit)
    try:
        return int(app.exec())
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def launch_qt_preview(
    argv: Sequence[str],
    registry_path: Path | None = None,
) -> int:
    """Compose Core services and run the explicit routed Qt preview shell."""

    services = build_app_services(registry_path)
    return run_qt_preview(argv, services)


__all__ = [
    "build_preview_window",
    "build_product_window",
    "get_or_create_qapplication",
    "launch_qt_preview",
    "run_qt_preview",
    "schedule_qt_startup_warning",
]
