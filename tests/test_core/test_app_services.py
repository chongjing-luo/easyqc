from __future__ import annotations

import subprocess
import sys

from core.app_services import AppServices, build_app_services
from core.code_executor import CodeExecutor
from core.configuration_service import ConfigurationService
from core.event_bus import EventBus
from core.project_context_service import ProjectContextService
from core.project_service import ProjectService
from core.project_template_service import ProjectTemplateService
from core.rating_service import RatingService
from core.session_state import SessionState
from core.table_service import TableService
from core.table_transform import TableTransformEngine
from core.template_service import TemplateService


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
    assert isinstance(services.template_service, TemplateService)
    assert isinstance(services.project_template_service, ProjectTemplateService)
    assert isinstance(services.table_transform, TableTransformEngine)
    assert isinstance(services.event_bus, EventBus)
    assert isinstance(services.session_state, SessionState)
    assert services.project_service.event_bus is services.event_bus
    assert services.rating_service.project_or_service is services.project_service
    assert services.configuration_service.project_service is services.project_service
    assert services.project_context_service.configuration_service is services.configuration_service
    assert services.template_service.application_root == tmp_path
    assert (
        services.project_template_service.templates
        is services.template_service
    )
    assert services.code_executor.shell_enabled is False
    assert not registry_path.exists()
    assert not (tmp_path / "app_settings.json").exists()


def test_build_app_services_applies_isolated_installation_shell_settings(
    tmp_path,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    TemplateService(first_root).set_shell_enabled(True)
    TemplateService(second_root).set_shell_enabled(False)

    first = build_app_services(first_root / "projects.json")
    second = build_app_services(second_root / "projects.json")

    assert first.code_executor.shell_enabled is True
    assert second.code_executor.shell_enabled is False
    assert first.template_service.application_root == first_root
    assert second.template_service.application_root == second_root


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
