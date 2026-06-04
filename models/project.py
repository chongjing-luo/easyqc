from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Project:
    name: str
    path: Path
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def settings_path(self) -> Path:
        return self.path / f"settings_{self.name}.json"

    @property
    def table_dir(self) -> Path:
        return self.path / "Table"

    @property
    def rating_dir(self) -> Path:
        return self.path / "RatingFiles"

    @classmethod
    def from_legacy_dict(cls, name: str, path: str | Path) -> "Project":
        return cls(name=name, path=Path(path))

    def to_legacy_dict(self) -> str:
        return str(self.path)


@dataclass
class ProjectRegistry:
    projects: dict[str, Project] = field(default_factory=dict)
    last_project: str | None = None

    @classmethod
    def from_legacy_dict(cls, data: dict[str, Any]) -> "ProjectRegistry":
        return cls(
            projects={
                name: Project.from_legacy_dict(name, path)
                for name, path in data.get("projects", {}).items()
            },
            last_project=data.get("last_project"),
        )

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "projects": {name: project.to_legacy_dict() for name, project in self.projects.items()},
            "last_project": self.last_project,
        }


__all__ = ["Project", "ProjectRegistry"]
