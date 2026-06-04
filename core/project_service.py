from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from models.project import Project, ProjectRegistry
from models.qcmodule import QCModule, Score, Tag
from utils.file_utils import FileUtils
from utils.validators import validate_project_name


MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProjectService:
    def __init__(self, registry_path: Path) -> None:
        self.registry_path = Path(registry_path)
        self.registry = self._load_registry()
        self.current: Project | None = None
        self._settings: dict[str, Any] = {}
        self._observers: list[Callable[[str], None]] = []

    @property
    def current_project(self) -> Project | None:
        return self.current

    @property
    def settings(self) -> Mapping[str, Any]:
        return MappingProxyType(self._settings)

    def create(self, name: str, path: Path) -> Project:
        if not validate_project_name(name):
            raise ValueError(f"项目名不合法: {name}")
        if name in self.registry.projects:
            raise ValueError(f"项目已存在: {name}")

        project_path = Path(path)
        if project_path.name != f"easyqc_{name}":
            project_path = project_path / f"easyqc_{name}"
        project_path.mkdir(parents=True, exist_ok=True)
        (project_path / "Table").mkdir(exist_ok=True)
        (project_path / "RatingFiles").mkdir(exist_ok=True)

        project = Project(name=name, path=project_path)
        self.registry.projects[name] = project
        self.registry.last_project = name
        self.current = project
        self._settings = self.new_settings()
        self.save()
        self._notify("project_changed")
        return project

    def load(self, name: str) -> Project:
        if name not in self.registry.projects:
            raise KeyError(name)
        project = self.registry.projects[name]
        if not project.settings_path.exists():
            raise FileNotFoundError(project.settings_path)

        self.current = project
        self.registry.last_project = name
        self._settings = FileUtils.safe_json_load(project.settings_path)
        self._notify("project_changed")
        return project

    def remove(self, name: str) -> None:
        if name not in self.registry.projects:
            raise KeyError(name)
        del self.registry.projects[name]
        if self.registry.last_project == name:
            self.registry.last_project = next(iter(self.registry.projects), None)
        if self.current and self.current.name == name:
            self.current = None
            self._settings = {}
        self._save_registry()
        self._notify("project_changed")

    def list_all(self) -> list[str]:
        return list(self.registry.projects.keys())

    def reload_registry(self) -> ProjectRegistry:
        self.registry = self._load_registry()
        if self.current is not None and self.current.name not in self.registry.projects:
            self.current = None
            self._settings = {}
        return self.registry

    def get_modules(self) -> dict[str, QCModule]:
        return {
            key: QCModule.from_legacy_dict(module)
            for key, module in self._settings.get("qcmodule", {}).items()
        }

    def add_module(self, name: str, label: str, index: int | None = None) -> QCModule:
        self._require_current_project()
        self._validate_module_name(name)
        if any(module.get("name") == name for module in self._settings.get("qcmodule", {}).values()):
            raise ValueError(f"模块已存在: {name}")

        module = self.default_module(name, label)
        modules = self._settings.setdefault("qcmodule", {})
        insert_index = index or (max([int(key) for key in modules.keys()] or [0]) + 1)
        self._settings["qcmodule"] = self.add_key(modules, insert_index, module.to_legacy_dict())
        self._notify("modules_changed")
        return module

    def remove_module(self, name: str) -> None:
        self._require_current_project()
        modules = self._settings.get("qcmodule", {})
        if len(modules) <= 1:
            raise ValueError("至少保留一个 QC 模块")
        index = next((key for key, module in modules.items() if module.get("name") == name), None)
        if index is None:
            raise KeyError(name)
        self._settings["qcmodule"] = self.add_key(modules, int(index))
        self._notify("modules_changed")

    def update_module(self, name: str, **kwargs: Any) -> None:
        self._require_current_project()
        allowed = {"label", "rater", "code", "control", "select_filter"}
        invalid = set(kwargs) - allowed
        if invalid:
            raise ValueError(f"不允许更新字段: {sorted(invalid)}")

        for module in self._settings.get("qcmodule", {}).values():
            if module.get("name") == name:
                module.update(kwargs)
                self._notify("modules_changed")
                return
        raise KeyError(name)

    def save(self) -> None:
        self._save_registry()
        if self.current is not None:
            FileUtils.safe_json_save(self.current.settings_path, self._settings)

    def add_observer(self, callback: Callable[[str], None]) -> None:
        self._observers.append(callback)

    def new_settings(self) -> dict[str, Any]:
        return {
            "version": 2,
            "constants": {},
            "variables": {},
            "var_select_filter": None,
            "select_filter": None,
            "qcmodule": {
                "1": self.default_module("example", "Example").to_legacy_dict(),
            },
        }

    def default_module(self, name: str, label: str) -> QCModule:
        return QCModule(
            name=name,
            label=label,
            scores={"1": Score(key="1", label=None, num=None, num_=None, value=None)},
            tags={"1": Tag(key="1", label=None, value=False)},
        )

    def add_key(self, values: dict[str, Any], index: int, value: Any | None = None) -> dict[str, Any]:
        result = {str(key): item for key, item in values.items()}
        if value is None:
            if str(index) not in result:
                return result
            del result[str(index)]
            return {
                str(new_index): result[str(old_index)]
                for new_index, old_index in enumerate(sorted(int(key) for key in result), 1)
            }

        keys = [int(key) for key in result.keys()] if result else []
        if not keys:
            return {"1": value}
        if str(index) in result:
            for key in range(max(keys), index - 1, -1):
                if str(key) in result:
                    result[str(key + 1)] = result[str(key)]
        result[str(index)] = value
        return {
            str(new_index): result[str(old_index)]
            for new_index, old_index in enumerate(sorted(int(key) for key in result), 1)
        }

    def _load_registry(self) -> ProjectRegistry:
        if not self.registry_path.exists():
            return ProjectRegistry()
        return ProjectRegistry.from_legacy_dict(FileUtils.safe_json_load(self.registry_path))

    def _save_registry(self) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        FileUtils.safe_json_save(self.registry_path, self.registry.to_legacy_dict())

    def _notify(self, event: str) -> None:
        for callback in self._observers:
            callback(event)

    def _require_current_project(self) -> None:
        if self.current is None:
            raise ValueError("当前项目未加载")

    def _validate_module_name(self, name: str) -> None:
        if not MODULE_NAME_PATTERN.match(name):
            raise ValueError(f"模块名不合法: {name}")


__all__ = ["ProjectService"]
