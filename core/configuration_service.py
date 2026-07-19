"""Transactional project and configuration operations for GUI adapters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from core.event_bus import Event, EventType
from core.project_service import MODULE_NAME_PATTERN, ProjectService
from core.table_service import TABLE_ALL, TableService
from models.project import Project
from models.qcmodule import QCModule, Score, Tag
from utils.file_utils import FileUtils


class ConfigurationError(ValueError):
    """Raised when a project configuration candidate is unsafe."""


@dataclass(frozen=True)
class ConfigurationSnapshot:
    """Detached values safe to hand from a Core worker to a GUI adapter."""

    projects: tuple[str, ...]
    current_project_name: str
    subjects: pd.DataFrame
    constants: dict[str, Any]
    modules: tuple[QCModule, ...]


class ConfigurationService:
    """Provide typed, atomic operations for project configuration pages."""

    def __init__(self, project_service: ProjectService, table_service: TableService) -> None:
        self.project_service = project_service
        self.table_service = table_service

    def projects(self) -> tuple[str, ...]:
        return tuple(self.project_service.list_all())

    @property
    def current_project(self) -> Project | None:
        return self.project_service.current_project

    def create_project(self, name: str, path: str | Path) -> Project:
        return self.project_service.create(name.strip(), Path(path))

    def import_project(self, path: str | Path, *, notify: bool = True) -> Project:
        self.project_service.import_project_from_dir(path, notify=notify)
        project = self.current_project
        if project is None:
            raise ConfigurationError("Imported project did not become current")
        return project

    def load_project(self, name: str, *, notify: bool = True) -> Project:
        return self.project_service.load(name, notify=notify)

    def remove_project(self, name: str) -> None:
        self.project_service.remove(name)

    def _require_project(self) -> Project:
        project = self.current_project
        if project is None:
            raise ConfigurationError("No project is loaded")
        return project

    def subjects(self) -> pd.DataFrame:
        table = self.table_service.load_table(self._require_project(), TABLE_ALL)
        return table.copy(deep=True) if table is not None else pd.DataFrame(columns=["ezqcid"])

    def snapshot(self) -> ConfigurationSnapshot:
        """Read one detached configuration view for UI-thread rendering."""
        project = self.current_project
        subjects = (
            self._validated_subjects(self.subjects())
            if project is not None
            else pd.DataFrame(columns=["ezqcid"])
        )
        return ConfigurationSnapshot(
            projects=self.projects(),
            current_project_name=project.name if project is not None else "",
            subjects=subjects,
            constants=self.constants(),
            modules=self.modules(),
        )

    def _validated_subjects(self, frame: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("Subjects must be a pandas DataFrame")
        if frame.columns.has_duplicates:
            raise ConfigurationError("Subject table contains duplicate columns")
        if "ezqcid" not in frame.columns:
            raise ConfigurationError("Subject table requires ezqcid")
        result = frame.copy(deep=True)
        identities = result["ezqcid"].map(
            lambda value: "" if value is None or pd.isna(value) else str(value).strip()
        )
        if identities.eq("").any():
            raise ConfigurationError("Subject table contains blank ezqcid")
        duplicates = sorted(identities[identities.duplicated(keep=False)].unique().tolist())
        if duplicates:
            raise ConfigurationError(f"Subject table contains duplicate ezqcid: {duplicates}")
        result["ezqcid"] = identities
        constant_collisions = sorted(set(map(str, result.columns)) & set(self.constants()))
        if constant_collisions:
            raise ConfigurationError(
                f"Subject column conflicts with constant: {constant_collisions}"
            )
        return result

    def replace_subjects(self, frame: pd.DataFrame, *, notify: bool = True) -> None:
        validated = self._validated_subjects(frame)
        self.table_service.save_table(self._require_project(), TABLE_ALL, validated)
        if notify:
            self.publish_subjects_changed()

    def import_subject_csv(
        self,
        path: str | Path,
        *,
        mode: str = "replace",
        notify: bool = True,
    ) -> None:
        frame = pd.read_csv(path, encoding="utf-8")
        if mode == "replace":
            self.replace_subjects(frame, notify=notify)
        else:
            self.merge_subjects(frame, mode=mode, notify=notify)

    def merge_subjects(
        self,
        incoming: pd.DataFrame,
        *,
        mode: str,
        notify: bool = True,
    ) -> None:
        incoming = self._validated_subjects(incoming)
        current = self.subjects()
        if current.empty and tuple(current.columns) == ("ezqcid",):
            self.replace_subjects(incoming, notify=notify)
            return
        if mode == "rows":
            if set(current.columns) != set(incoming.columns):
                raise ConfigurationError("Row merge requires the same columns")
            candidate = pd.concat(
                [current, incoming.loc[:, current.columns]],
                ignore_index=True,
            )
        elif mode == "columns":
            overlap = sorted((set(current.columns) & set(incoming.columns)) - {"ezqcid"})
            if overlap:
                raise ConfigurationError(f"Column merge overlap: {overlap}")
            candidate = current.merge(incoming, on="ezqcid", how="outer", validate="one_to_one")
        else:
            raise ConfigurationError(f"Unsupported subject merge mode: {mode}")
        self.replace_subjects(candidate, notify=notify)

    def constants(self) -> dict[str, Any]:
        if self.current_project is None:
            return {}
        return deepcopy(dict(self.project_service.settings.get("constants", {})))

    def _settings_candidate(self) -> dict[str, Any]:
        self._require_project()
        return deepcopy(dict(self.project_service.settings))

    def set_constant(
        self,
        name: str,
        value: Any,
        *,
        old_name: str | None = None,
    ) -> None:
        name = name.strip()
        if not name or not name.isidentifier():
            raise ConfigurationError(f"Invalid constant name: {name!r}")
        if name in set(map(str, self.subjects().columns)):
            raise ConfigurationError(f"Constant conflicts with subject column: {name}")
        candidate = self._settings_candidate()
        constants = candidate.setdefault("constants", {})
        if old_name and old_name != name:
            if name in constants:
                raise ConfigurationError(f"Constant already exists: {name}")
            constants.pop(old_name, None)
        constants[name] = value
        self.project_service.commit_settings(candidate)

    def delete_constant(self, name: str) -> bool:
        candidate = self._settings_candidate()
        constants = candidate.setdefault("constants", {})
        if name not in constants:
            return False
        del constants[name]
        self.project_service.commit_settings(candidate)
        return True

    def modules(self) -> tuple[QCModule, ...]:
        if self.current_project is None:
            return ()
        modules = self.project_service.settings.get("qcmodule", {})
        return tuple(
            QCModule.from_legacy_dict(payload)
            for _, payload in sorted(modules.items(), key=lambda item: int(item[0]))
        )

    @staticmethod
    def _normalized_module_payload(module: QCModule | dict[str, Any]) -> dict[str, Any]:
        typed = deepcopy(module) if isinstance(module, QCModule) else QCModule.from_legacy_dict(deepcopy(module))
        if not MODULE_NAME_PATTERN.match(typed.name):
            raise ConfigurationError(f"Invalid module name: {typed.name}")
        typed.ezqcid = None
        typed.time = None
        typed.notes = None
        typed.code_exe = None
        for score in typed.scores.values():
            score.value = None
        for tag in typed.tags.values():
            tag.value = False
        return typed.to_legacy_dict()

    def _commit_module_payloads(
        self,
        payloads: list[dict[str, Any]],
        *,
        notify: bool = True,
    ) -> None:
        if not payloads:
            raise ConfigurationError("At least one QC module is required")
        names = [str(payload.get("name", "")) for payload in payloads]
        if len(set(names)) != len(names):
            raise ConfigurationError("Module name already exists")
        candidate = self._settings_candidate()
        candidate["qcmodule"] = {
            str(index): deepcopy(payload)
            for index, payload in enumerate(payloads, start=1)
        }
        self.project_service.commit_settings(candidate, notify=notify)

    def add_module(self, name: str, label: str, *, index: int | None = None) -> None:
        name = name.strip()
        if any(module.name == name for module in self.modules()):
            raise ConfigurationError(f"Module already exists: {name}")
        module = self.project_service.default_module(name, label.strip() or name)
        payloads = [module.to_legacy_dict() for module in self.modules()]
        position = len(payloads) if index is None else max(0, min(len(payloads), int(index)))
        payloads.insert(position, self._normalized_module_payload(module))
        self._commit_module_payloads(payloads)

    def save_module(
        self,
        module: QCModule | dict[str, Any],
        *,
        original_name: str,
    ) -> None:
        payload = self._normalized_module_payload(module)
        payloads = [item.to_legacy_dict() for item in self.modules()]
        index = next(
            (position for position, item in enumerate(payloads) if item.get("name") == original_name),
            None,
        )
        if index is None:
            raise ConfigurationError(f"Unknown module: {original_name}")
        payloads[index] = payload
        self._commit_module_payloads(payloads)

    def remove_module(self, name: str) -> None:
        payloads = [module.to_legacy_dict() for module in self.modules() if module.name != name]
        if len(payloads) == len(self.modules()):
            raise ConfigurationError(f"Unknown module: {name}")
        self._commit_module_payloads(payloads)

    def move_module(self, name: str, delta: int) -> bool:
        payloads = [module.to_legacy_dict() for module in self.modules()]
        index = next((i for i, payload in enumerate(payloads) if payload.get("name") == name), None)
        if index is None:
            raise ConfigurationError(f"Unknown module: {name}")
        target = max(0, min(len(payloads) - 1, index + int(delta)))
        if target == index:
            return False
        payloads.insert(target, payloads.pop(index))
        self._commit_module_payloads(payloads)
        return True

    def import_module_payload(
        self,
        payload: dict[str, Any],
        *,
        index: int | None = None,
        notify: bool = True,
    ) -> None:
        normalized = self._normalized_module_payload(payload)
        name = normalized["name"]
        if any(module.name == name for module in self.modules()):
            raise ConfigurationError(f"Module already exists: {name}")
        payloads = [module.to_legacy_dict() for module in self.modules()]
        position = len(payloads) if index is None else max(0, min(len(payloads), int(index)))
        payloads.insert(position, normalized)
        self._commit_module_payloads(payloads, notify=notify)

    def import_module_file(
        self,
        path: str | Path,
        *,
        index: int | None = None,
        notify: bool = True,
    ) -> None:
        payload = FileUtils.safe_json_load(path)
        if not isinstance(payload, dict):
            raise ConfigurationError("Module file must contain one object")
        self.import_module_payload(payload, index=index, notify=notify)

    def export_module(self, name: str, path: str | Path) -> None:
        self.project_service.export_module(name, path)

    def publish_project_changed(self) -> None:
        self.project_service.publish_change("project_changed")

    def publish_subjects_changed(self) -> None:
        self.project_service.event_bus.emit(
            Event(type=EventType.SUBJECTS_CHANGED, source="ConfigurationService")
        )

    def publish_modules_changed(self) -> None:
        self.project_service.publish_change("settings_saved")
        self.project_service.publish_change("modules_changed")


__all__ = ["ConfigurationError", "ConfigurationService", "ConfigurationSnapshot"]
