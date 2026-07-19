from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from pathlib import Path

from core.app_services import AppServices, build_app_services
from gui.main_window import EasyQCApp as LegacyEasyQCApp
from utils.logger import get_logging_status


def schedule_tk_startup_warning(root, message: str | None) -> None:
    """Schedule one logging-degradation warning after tkinter is ready."""

    if not message:
        return
    root.after_idle(
        lambda: messagebox.showwarning(
            "日志记录受限",
            message,
            parent=root,
        )
    )


class EasyQCApp:
    def __init__(self, registry_path: Path | None = None):
        self.root = tk.Tk()
        self.services = build_app_services(registry_path)
        self.project_service = self.services.project_service
        self.rating_service = self.services.rating_service
        self.table_service = self.services.table_service
        self.code_executor = self.services.code_executor
        self.table_transform = self.services.table_transform
        self.main_window = LegacyEasyQCApp(self.root, services=self.services)
        schedule_tk_startup_warning(
            self.root,
            get_logging_status().warning_message,
        )

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self.main_window.quit_app)
        self.root.mainloop()


__all__ = ["AppServices", "EasyQCApp", "schedule_tk_startup_warning"]
