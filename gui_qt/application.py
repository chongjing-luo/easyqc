"""QApplication lifecycle for the sole EasyQC Qt presentation."""

from __future__ import annotations

import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from shiboken6 import isValid

from core.app_services import AppServices, build_app_services
from core.cli_service import resolve_qcpage_launch
from gui_qt.main_window import QtMainWindow
from gui_qt.i18n import get_or_create_language_controller, translate_ui_text
from gui_qt.qc_workspace import QtQcControllerWindow
from gui_qt.startup_screen import QtStartupScreen
from gui_qt.theme import apply_easyqc_theme, configure_application_identity
from utils.logger import get_logging_status


def schedule_qt_startup_warning(window, message: str | None) -> None:
    """Schedule one logging-degradation warning after Qt is ready."""

    if not message:
        return
    QTimer.singleShot(
        0,
        lambda: QMessageBox.warning(
            window,
            translate_ui_text("日志记录受限"),
            message,
        ),
    )


def get_or_create_qapplication(argv: Sequence[str] | None = None) -> QApplication:
    """Return the sole QApplication without overriding its platform theme."""

    app = QApplication.instance()
    if app is None:
        app = QApplication(list(argv or sys.argv))
    configure_application_identity(app)
    return app


def build_table_window(services: AppServices, source: pd.DataFrame) -> QtMainWindow:
    """Build one product window from injected services and a copied table."""

    app = get_or_create_qapplication()
    apply_easyqc_theme(app)
    language = get_or_create_language_controller(app)
    return QtMainWindow(services=services, source=source, language=language)


def build_product_window(services: AppServices) -> QtMainWindow:
    """Build the routed Qt shell; project materialization starts in background."""

    app = get_or_create_qapplication()
    apply_easyqc_theme(app)
    language = get_or_create_language_controller(app)
    return QtMainWindow(services=services, language=language)


def run_qt_application(
    argv: Sequence[str],
    services: AppServices,
    source: pd.DataFrame | None = None,
    *,
    exit_after_ms: int | None = None,
    startup_minimum_ms: int = 500,
) -> int:
    """Run exactly one Qt product event loop and return its exit code."""

    app = get_or_create_qapplication(argv)
    if isinstance(app, QApplication):
        apply_easyqc_theme(app)
    language = get_or_create_language_controller(app)
    startup = QtStartupScreen(language)
    startup.set_status("startup.loading_project")
    started_at = time.monotonic()
    startup.show()
    app.processEvents()
    try:
        window = QtMainWindow(
            services=services,
            source=source,
            language=language,
        )
    except Exception as exc:
        try:
            startup.close()
            startup.deleteLater()
            QCoreApplication.sendPostedEvents(
                startup,
                QEvent.Type.DeferredDelete,
            )
            QMessageBox.critical(
                None,
                language.translate_source("启动失败"),
                str(exc),
            )
            app.processEvents()
        finally:
            services.code_executor.close()
        return 1
    main_presented = False

    def present_main(_succeeded: bool = True) -> None:
        nonlocal main_presented
        if main_presented:
            return
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        remaining = max(0, int(startup_minimum_ms) - elapsed_ms)
        if remaining:
            QTimer.singleShot(remaining, present_main)
            return
        main_presented = True
        window.show()
        startup.close()
        schedule_qt_startup_warning(
            window,
            get_logging_status().warning_message,
        )

    if bool(getattr(window, "initialization_complete", False)):
        present_main(bool(getattr(window, "initialization_succeeded", True)))
    else:
        window.initializationFinished.connect(present_main)
    if exit_after_ms is not None:
        QTimer.singleShot(max(0, int(exit_after_ms)), app.quit)
    try:
        return int(app.exec())
    finally:
        try:
            window.close()
            window.deleteLater()
            startup.close()
            startup.deleteLater()
            app.processEvents()
        finally:
            services.code_executor.close()


def launch_qt(
    argv: Sequence[str],
    registry_path: Path | None = None,
) -> int:
    """Compose Core services and run the routed Qt product shell."""

    services = build_app_services(registry_path)
    return run_qt_application(argv, services)


def run_qt_qc(
    argv: Sequence[str],
    services: AppServices,
    *,
    project: str,
    module: str,
    rater: str,
    easyqcid: str,
    exit_after_ms: int | None = None,
) -> int:
    """Open one identity-safe Qt QC controller without the product shell."""

    app = get_or_create_qapplication(argv)
    if isinstance(app, QApplication):
        apply_easyqc_theme(app)
    language = get_or_create_language_controller(app)
    snapshot = services.project_context_service.load_project(project)
    workflow = services.project_context_service.create_qc_workflow(
        snapshot,
        module_name=module,
        rater_override=rater,
        initial_easyqcid=easyqcid,
    )
    window = QtQcControllerWindow(workflow, language=language)
    window.show()
    window.raise_()
    window.activateWindow()
    schedule_qt_startup_warning(
        window,
        get_logging_status().warning_message,
    )
    if exit_after_ms is not None:
        QTimer.singleShot(max(0, int(exit_after_ms)), app.quit)
    try:
        return int(app.exec())
    finally:
        try:
            if isValid(window) and window.isVisible():
                window.close_discarding_draft()
            if isValid(window):
                window.deleteLater()
            app.processEvents()
        finally:
            services.code_executor.close()


def launch_qt_qc(
    argv: Sequence[str],
    *,
    project: str,
    module: str,
    rater: str,
    easyqcid: str,
    registry_path: Path | None = None,
) -> int:
    """Compose Core services and launch one direct Qt QC session."""

    resolved_registry_path = (
        Path(registry_path)
        if registry_path is not None
        else Path(__file__).resolve().parents[1] / "projects.json"
    )
    launch = resolve_qcpage_launch(
        project,
        module,
        rater,
        easyqcid,
        resolved_registry_path,
    )
    services = build_app_services(resolved_registry_path)
    return run_qt_qc(
        argv,
        services,
        project=launch.project.name,
        module=launch.module_name,
        rater=launch.rater,
        easyqcid=launch.easyqcid,
    )


__all__ = [
    "build_table_window",
    "build_product_window",
    "get_or_create_qapplication",
    "get_or_create_language_controller",
    "launch_qt",
    "launch_qt_qc",
    "run_qt_application",
    "run_qt_qc",
    "schedule_qt_startup_warning",
]
