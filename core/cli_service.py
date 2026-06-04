from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.project_service import ProjectService
from models.project import Project


class QCPageLaunchError(ValueError):
    """Raised when a CLI QC page request cannot be resolved."""


@dataclass(frozen=True)
class QCPageLaunchContext:
    project: Project
    module_index: str
    module: dict[str, Any]
    module_name: str
    rater: str
    ezqcid: str
    module_rater_dir: Path
    available_modules: list[str]


def resolve_qcpage_launch(
    project_name: str,
    module_name: str,
    rater: str,
    ezqcid: str,
    registry_path: Path,
) -> QCPageLaunchContext:
    project_service = ProjectService(registry_path)
    if project_name not in project_service.list_all():
        raise QCPageLaunchError(f"项目不存在: {project_name}; 可用项目: {project_service.list_all()}")

    project = project_service.load(project_name)
    modules = project_service.settings.get("qcmodule", {})
    available_modules = [module.get("name", "") for module in modules.values()]
    module_index = next(
        (index for index, module in modules.items() if module.get("name") == module_name),
        None,
    )
    if module_index is None:
        raise QCPageLaunchError(f"模块不存在: {module_name}; 可用模块: {available_modules}")

    module = dict(modules[module_index])
    module["rater"] = rater
    return QCPageLaunchContext(
        project=project,
        module_index=module_index,
        module=module,
        module_name=module_name,
        rater=rater,
        ezqcid=ezqcid,
        module_rater_dir=project.rating_dir / module_name / rater,
        available_modules=available_modules,
    )


__all__ = ["QCPageLaunchContext", "QCPageLaunchError", "resolve_qcpage_launch"]
