"""Qt application identity without overriding the host platform theme."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication


def configure_application_identity(app: QApplication) -> None:
    """Set EasyQC metadata while preserving the host style, font and palette."""

    app.setApplicationName("EasyQC")
    app.setOrganizationName("EasyQC")


__all__ = ["configure_application_identity"]
