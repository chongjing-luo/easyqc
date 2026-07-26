"""Copy installation templates into independently editable project storage."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.configuration_service import ConfigurationService
from core.module_repository import ModuleRecord, ModuleRepository, ModuleRepositoryError
from core.template_service import TemplateService, TemplateServiceError
from models.qcmodule import QCModule


_USE_TEMPLATE_VALUE = object()


class ProjectTemplateService:
    """Create detached project-owned values from installation templates."""

    def __init__(self, templates: TemplateService) -> None:
        if not isinstance(templates, TemplateService):
            raise TypeError("templates must be TemplateService")
        self.templates = templates

    def copy_constant(
        self,
        template_name: str,
        configuration: ConfigurationService,
        *,
        candidate_name: str | None = None,
        candidate_value: Any = _USE_TEMPLATE_VALUE,
    ) -> str:
        """Copy one detached constant template into current project settings."""

        if not isinstance(configuration, ConfigurationService):
            raise TypeError("configuration must be ConfigurationService")
        name = (
            template_name.strip()
            if candidate_name is None
            else candidate_name.strip()
        )
        value = (
            self.templates.constant(template_name)
            if candidate_value is _USE_TEMPLATE_VALUE
            else deepcopy(candidate_value)
        )
        try:
            configuration.add_constant(name, value)
        except ValueError as exc:
            raise TemplateServiceError(str(exc)) from exc
        return name

    def copy_module(
        self,
        template_id: str,
        destination: ModuleRepository,
        *,
        candidate: QCModule | None = None,
    ) -> ModuleRecord:
        """Copy one template candidate into one project module repository."""

        if not isinstance(destination, ModuleRepository):
            raise TypeError("destination must be ModuleRepository")
        if destination.scope != "project":
            raise TemplateServiceError(
                "module template destination must have project scope"
            )
        source = self.templates.module(template_id)
        module = deepcopy(source.module if candidate is None else candidate)
        if not isinstance(module, QCModule):
            raise TypeError("candidate must be QCModule or None")

        snapshot = destination.snapshot()
        if snapshot.errors:
            raise TemplateServiceError(
                "project module catalog contains errors: "
                + "; ".join(error.message for error in snapshot.errors)
            )
        if any(record.module.name == module.name for record in snapshot.records):
            raise TemplateServiceError(
                f"project module already exists: {module.name}"
            )
        next_order = (
            max(
                (record.display_order for record in snapshot.records),
                default=0,
            )
            + 10
        )
        record = ModuleRecord.create(
            module,
            scope="project",
            display_order=next_order,
        )
        try:
            return destination.save(record)
        except ModuleRepositoryError as exc:
            raise TemplateServiceError(str(exc)) from exc


__all__ = ["ProjectTemplateService"]
