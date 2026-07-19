"""Toolkit-neutral composition root shared by every EasyQC interface."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.code_executor import CodeExecutor
from core.configuration_service import ConfigurationService
from core.event_bus import EventBus
from core.project_context_service import ProjectContextService
from core.project_service import ProjectService
from core.rating_service import RatingService
from core.session_state import SessionState
from core.table_service import TableService
from core.table_transform import TableTransformEngine


@dataclass(frozen=True)
class AppServices:
    """One shared application-service graph with no GUI-toolkit dependency."""

    project_service: ProjectService
    rating_service: RatingService
    table_service: TableService
    code_executor: CodeExecutor
    table_transform: TableTransformEngine
    event_bus: EventBus
    session_state: SessionState
    configuration_service: ConfigurationService
    project_context_service: ProjectContextService


def build_app_services(registry_path: Path | None = None) -> AppServices:
    """Build the one service graph consumed by CLI, tkinter and Qt adapters.

    Input: an optional project-registry path.
    Output: one immutable :class:`AppServices` context.
    Side effects: the existing ``ProjectService`` may read the registry when it
    exists; this function does not create a GUI or write project data.
    Errors: dependency construction errors propagate to the launcher.
    """

    event_bus = EventBus()
    project_service = ProjectService(
        registry_path or Path(__file__).parent.parent / "projects.json",
        event_bus=event_bus,
    )
    rating_service = RatingService(project_service)
    table_service = TableService()
    code_executor = CodeExecutor()
    configuration_service = ConfigurationService(project_service, table_service)
    project_context_service = ProjectContextService(
        configuration_service,
        rating_service,
        code_executor,
        event_bus,
    )
    return AppServices(
        project_service=project_service,
        rating_service=rating_service,
        table_service=table_service,
        code_executor=code_executor,
        table_transform=TableTransformEngine(max_rows=5000, max_columns=200),
        event_bus=event_bus,
        session_state=SessionState(),
        configuration_service=configuration_service,
        project_context_service=project_context_service,
    )


__all__ = ["AppServices", "build_app_services"]
