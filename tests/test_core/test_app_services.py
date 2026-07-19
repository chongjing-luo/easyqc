from __future__ import annotations

import subprocess
import sys

from core.app_services import AppServices, build_app_services
from core.code_executor import CodeExecutor
from core.configuration_service import ConfigurationService
from core.event_bus import EventBus
from core.project_context_service import ProjectContextService
from core.project_service import ProjectService
from core.rating_service import RatingService
from core.session_state import SessionState
from core.table_service import TableService
from core.table_transform import TableTransformEngine


def test_build_app_services_returns_one_shared_toolkit_neutral_context(tmp_path):
    registry_path = tmp_path / "projects.json"

    services = build_app_services(registry_path)

    assert isinstance(services, AppServices)
    assert isinstance(services.project_service, ProjectService)
    assert isinstance(services.rating_service, RatingService)
    assert isinstance(services.table_service, TableService)
    assert isinstance(services.code_executor, CodeExecutor)
    assert isinstance(services.configuration_service, ConfigurationService)
    assert isinstance(services.project_context_service, ProjectContextService)
    assert isinstance(services.table_transform, TableTransformEngine)
    assert isinstance(services.event_bus, EventBus)
    assert isinstance(services.session_state, SessionState)
    assert services.project_service.event_bus is services.event_bus
    assert services.rating_service.project_or_service is services.project_service
    assert services.configuration_service.project_service is services.project_service
    assert services.project_context_service.configuration_service is services.configuration_service
    assert not registry_path.exists()


def test_importing_app_services_does_not_import_a_gui_toolkit(easyqc_root):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import core.app_services; "
                "assert 'tkinter' not in sys.modules; "
                "assert 'PySide6' not in sys.modules"
            ),
        ],
        cwd=easyqc_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
