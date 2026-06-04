from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path

from core.code_executor import CodeExecutor
from core.project_service import ProjectService
from core.rating_service import RatingService
from core.table_service import TableService
from core.table_transform import TableTransformEngine
from gui.main_window import EasyQCApp as LegacyEasyQCApp


@dataclass(frozen=True)
class AppServices:
    project_service: ProjectService
    rating_service: RatingService
    table_service: TableService
    code_executor: CodeExecutor
    table_transform: TableTransformEngine


class EasyQCApp:
    def __init__(self, registry_path: Path | None = None):
        self.root = tk.Tk()
        project_service = ProjectService(registry_path or Path(__file__).parent.parent / "projects.json")
        rating_service = RatingService(project_service)
        table_service = TableService()
        code_executor = CodeExecutor()
        table_transform = TableTransformEngine(max_rows=5000, max_columns=200)
        self.services = AppServices(
            project_service=project_service,
            rating_service=rating_service,
            table_service=table_service,
            code_executor=code_executor,
            table_transform=table_transform,
        )
        self.project_service = self.services.project_service
        self.rating_service = self.services.rating_service
        self.table_service = self.services.table_service
        self.code_executor = self.services.code_executor
        self.table_transform = self.services.table_transform
        self.main_window = LegacyEasyQCApp(self.root, services=self.services)

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self.main_window.quit_app)
        self.root.mainloop()


__all__ = ["AppServices", "EasyQCApp"]
