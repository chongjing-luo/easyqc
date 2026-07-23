from __future__ import annotations

import re
from copy import deepcopy
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, Callable

from core.event_bus import EventBus, Event, EventType
from models.project import Project, ProjectRegistry
from models.qcmodule import QCModule, Score, Tag
from utils.file_utils import FileUtils
from utils.validators import validate_project_name


MODULE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class PreparedProjectLoad:
    """Parsed candidate that has not changed the active project/settings pair."""

    project: Project
    settings: dict[str, Any]


@dataclass(frozen=True)
class ProjectSettingsState:
    """Detached project/settings authority captured before background work."""

    project_name: str
    project_path: Path
    settings: dict[str, Any]


class ProjectStateConflictError(ValueError):
    """Raised when an optimistic settings write no longer owns current state."""


class ProjectService:
    def __init__(self, registry_path: Path, event_bus: EventBus | None = None) -> None:
        self.registry_path = Path(registry_path)
        self.registry = self._load_registry()
        self.current: Project | None = None
        self._settings: dict[str, Any] = {}
        self._state_lock = RLock()
        # Typed event bus (P1-C, AC-10). Created internally if not injected so
        # CLI/tests that do not supply one still work; GUI injects a shared bus.
        self.event_bus = event_bus or EventBus()
        # Legacy string observers — bridged to the typed bus for transition.
        # Deprecated; will be removed once GUI subscribes directly (P1-D/P2).
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

    def load(self, name: str, *, notify: bool = True) -> Project:
        return self.commit_load(self.prepare_load(name), notify=notify)

    def prepare_load(self, name: str) -> PreparedProjectLoad:
        """Read one project candidate without mutating active service state."""

        if name not in self.registry.projects:
            raise KeyError(name)
        project = self.registry.projects[name]
        if not project.settings_path.exists():
            raise FileNotFoundError(project.settings_path)

        # Read and validate the candidate before changing the active pair.  A
        # damaged settings file must never leave ``current`` pointing at the
        # new project while ``_settings`` still belongs to the previous one.
        candidate_settings = FileUtils.safe_json_load(project.settings_path)
        if not isinstance(candidate_settings, dict):
            raise ValueError(f"项目设置必须是对象: {project.settings_path}")
        return PreparedProjectLoad(project=project, settings=deepcopy(candidate_settings))

    def commit_load(
        self,
        prepared: PreparedProjectLoad,
        *,
        notify: bool = True,
    ) -> Project:
        """Atomically accept a previously parsed project candidate."""

        if not isinstance(prepared, PreparedProjectLoad):
            raise TypeError("commit_load requires PreparedProjectLoad")
        with self._state_lock:
            registered = self.registry.projects.get(prepared.project.name)
            if registered is None or registered.path != prepared.project.path:
                raise ValueError("Prepared project is no longer registered")
            project = prepared.project
            previous_current = self.current
            previous_last_project = self.registry.last_project
            previous_settings = self._settings
            self.current = project
            self.registry.last_project = project.name
            self._settings = deepcopy(prepared.settings)
            try:
                if previous_last_project != project.name:
                    self._save_registry()
            except Exception:
                self.current = previous_current
                self.registry.last_project = previous_last_project
                self._settings = previous_settings
                raise
        if notify:
            self._notify("project_changed")
        return project

    def capture_settings_state(self) -> ProjectSettingsState:
        """Return one detached state token for an optimistic settings write."""

        with self._state_lock:
            self._require_current_project()
            project = self.current
            assert project is not None
            return ProjectSettingsState(
                project_name=project.name,
                project_path=Path(project.path),
                settings=deepcopy(self._settings),
            )

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

    # ---- P2-A1: score / tag / constant CRUD (F-MOD-4 sanctioned paths) ----
    # These let the GUI edit module config without back-door dict mutation
    # (ADR-002). They mirror the legacy adapter semantics: empty shells on
    # add, reindex on delete, no validation here (validation is the GUI's
    # job via validate_score, same as before). Module identity = name.

    def _module_index_by_name(self, name: str) -> str:
        modules = self._settings.get("qcmodule", {})
        index = next((k for k, m in modules.items() if m.get("name") == name), None)
        if index is None:
            raise KeyError(name)
        return index

    def add_score(self, name: str, index: int) -> None:
        """Insert an empty score at position ``index``; existing keys reindex."""
        self._require_current_project()
        qcidx = self._module_index_by_name(name)
        scores = self._settings["qcmodule"][qcidx].setdefault("scores", {})
        self._settings["qcmodule"][qcidx]["scores"] = self.add_key(
            scores, index, {"label": None, "num": None, "num_": None, "value": None}
        )
        self._notify("modules_changed")

    def delete_score(self, name: str, index: int) -> None:
        self._require_current_project()
        qcidx = self._module_index_by_name(name)
        scores = self._settings["qcmodule"][qcidx].get("scores", {})
        self._settings["qcmodule"][qcidx]["scores"] = self.add_key(scores, index)
        self._notify("modules_changed")

    def update_score_fields(self, name: str, score_key: str, **fields: Any) -> None:
        self._require_current_project()
        qcidx = self._module_index_by_name(name)
        self._settings["qcmodule"][qcidx].setdefault("scores", {}).setdefault(score_key, {}).update(fields)
        self._notify("modules_changed")

    def add_tag(self, name: str, index: int) -> None:
        self._require_current_project()
        qcidx = self._module_index_by_name(name)
        tags = self._settings["qcmodule"][qcidx].setdefault("tags", {})
        self._settings["qcmodule"][qcidx]["tags"] = self.add_key(
            tags, index, {"label": None, "value": None}
        )
        self._notify("modules_changed")

    def delete_tag(self, name: str, index: int) -> None:
        self._require_current_project()
        qcidx = self._module_index_by_name(name)
        tags = self._settings["qcmodule"][qcidx].get("tags", {})
        self._settings["qcmodule"][qcidx]["tags"] = self.add_key(tags, index)
        self._notify("modules_changed")

    def update_tag_fields(self, name: str, tag_key: str, **fields: Any) -> None:
        self._require_current_project()
        qcidx = self._module_index_by_name(name)
        self._settings["qcmodule"][qcidx].setdefault("tags", {}).setdefault(tag_key, {}).update(fields)
        self._notify("modules_changed")

    def has_constant(self, name: str) -> bool:
        self._require_current_project()
        return name in self._settings.setdefault("constants", {})

    def constant_items(self):
        self._require_current_project()
        return self._settings.setdefault("constants", {}).items()

    def set_constant(self, name: str, value: Any) -> None:
        self._require_current_project()
        self._settings.setdefault("constants", {})[name] = value
        self._notify("modules_changed")

    def rename_constant(self, old_name: str, new_name: str, value: Any) -> None:
        self._require_current_project()
        constants = self._settings.setdefault("constants", {})
        if old_name != new_name and old_name in constants:
            del constants[old_name]
        constants[new_name] = value
        self._notify("modules_changed")

    def delete_constant(self, name: str) -> None:
        self._require_current_project()
        constants = self._settings.setdefault("constants", {})
        if name in constants:
            del constants[name]
        self._notify("modules_changed")

    def update_module(self, name: str, **kwargs: Any) -> None:
        """Update module CONFIG fields.

        Only durable module configuration keys are sanctioned here. Rating /
        runtime / view state (ezqcid, notes, time, code_exe, scores values, tags
        values, button, showing) is NOT module config — it is per-subject rating
        state or GUI view state and must go through RatingService / the QC page
        controller. Routing it here would persist transient state into the
        settings schema (ADR-002 violation).
        """
        self._require_current_project()
        # Module-config keys: label, rater, code, control, select_filter (existed)
        # + interper (viewer interpreter type — P1-A, F-MOD-4).
        allowed = {"label", "rater", "code", "control", "select_filter", "interper"}
        invalid = set(kwargs) - allowed
        if invalid:
            raise ValueError(f"不允许更新字段: {sorted(invalid)}")

        for module in self._settings.get("qcmodule", {}).values():
            if module.get("name") == name:
                module.update(kwargs)
                self._notify("modules_changed")
                return
        raise KeyError(name)

    # ---- P2-A gap: project lifecycle + module CRUD helpers (for dialogs migration) ----

    def current_project_name(self) -> str | None:
        current = self.current_project
        return current.name if current is not None else None

    def has_project(self, name: str) -> bool:
        return name in self.registry.projects

    def project_display_rows(self) -> list:
        """Rows for the project combo/list: (name,) tuples."""
        return [(name,) for name in self.registry.projects]

    def import_project_from_dir(self, output_dir, *, notify: bool = True) -> None:
        """Import a project from a directory containing settings_<name>.json.
        Registers it in the registry and loads it (no copy)."""
        output_dir = Path(output_dir)
        settings_file = None
        for f in output_dir.iterdir():
            if f.name.startswith("settings_") and f.name.endswith(".json"):
                project_name = f.name[9:-5]
                settings_file = f
                break
        if settings_file is None or not settings_file.exists():
            raise FileNotFoundError(
                f"目录 {output_dir} 不是合法项目路径（缺少 settings_*.json）"
            )
        project = Project(name=project_name, path=output_dir)
        self.registry.projects[project_name] = project
        self.registry.last_project = project_name
        self._save_registry()
        self.load(project_name, notify=notify)
        if notify:
            self._notify("project_changed")

    def constants(self) -> dict[str, Any]:
        """Return the constants dict (mutable view for read checks)."""
        return self._settings.setdefault("constants", {})

    def module_name_exists(self, name: str, exclude_name: str | None = None) -> bool:
        modules = self._settings.get("qcmodule", {})
        return any(
            m.get("name") == name and name != exclude_name
            for m in modules.values()
        )

    def module_index_by_name(self, name: str) -> str | None:
        """Public lookup; returns None if absent (does NOT raise)."""
        modules = self._settings.get("qcmodule", {})
        return next((k for k, m in modules.items() if m.get("name") == name), None)

    def next_module_index(self) -> int:
        modules = self._settings.get("qcmodule", {})
        return max([int(k) for k in modules.keys()] or [0]) + 1

    def module_table_rows(self) -> list[tuple]:
        """Rows for the module list: (index, name, label) tuples."""
        modules = self._settings.get("qcmodule", {})
        return [
            (int(k), m.get("name", ""), m.get("label", ""))
            for k, m in sorted(modules.items(), key=lambda x: int(x[0]))
        ]

    def can_delete_module(self) -> bool:
        return len(self._settings.get("qcmodule", {})) > 1

    def delete_module(self, index: int) -> None:
        """Delete by numeric index (dialogs passes index). Reindexes remaining."""
        self._require_current_project()
        modules = self._settings.get("qcmodule", {})
        if len(modules) <= 1:
            raise ValueError("至少保留一个 QC 模块")
        key = str(index)
        if key not in modules:
            raise KeyError(index)
        self._settings["qcmodule"] = self.add_key(modules, int(index))
        self._notify("modules_changed")

    def insert_module(self, index: int, module: dict) -> None:
        """Insert an external module dict at position index (import path)."""
        self._require_current_project()
        modules = self._settings.setdefault("qcmodule", {})
        self._settings["qcmodule"] = self.add_key(modules, int(index), module)
        self._notify("modules_changed")

    def export_module(self, module_name: str, path) -> None:
        """Write a sanitized module copy (rater/ezqcid=None) to path."""
        idx = self._module_index_by_name(module_name)
        module = self._settings["qcmodule"][idx]
        export_copy = deepcopy(module)
        export_copy["rater"] = None
        export_copy["ezqcid"] = None
        FileUtils.safe_json_save(path, export_copy)

    def commit_settings(
        self,
        candidate: Mapping[str, Any],
        *,
        change_event: str = "modules_changed",
        notify: bool = True,
        expected_state: ProjectSettingsState | None = None,
    ) -> None:
        """Atomically persist a copied settings candidate or restore memory."""

        if change_event not in {"modules_changed", "settings_saved"}:
            raise ValueError(f"不支持的设置变更事件: {change_event}")
        if expected_state is not None and not isinstance(
            expected_state,
            ProjectSettingsState,
        ):
            raise TypeError("expected_state must be ProjectSettingsState or None")
        prepared = deepcopy(dict(candidate))
        constants = prepared.get("constants")
        modules = prepared.get("qcmodule")
        if not isinstance(constants, dict):
            raise ValueError("constants 必须是对象")
        if not isinstance(modules, dict) or not modules:
            raise ValueError("至少保留一个 QC 模块")
        names: list[str] = []
        for key, payload in sorted(modules.items(), key=lambda item: int(item[0])):
            if str(int(key)) != str(key):
                raise ValueError(f"模块索引不合法: {key}")
            if not isinstance(payload, dict):
                raise ValueError(f"模块 {key} 必须是对象")
            module = QCModule.from_legacy_dict(payload)
            self._validate_module_name(module.name)
            names.append(module.name)
        if len(set(names)) != len(names):
            raise ValueError("模块名称不能重复")

        with self._state_lock:
            self._require_current_project()
            current = self.current
            assert current is not None
            if expected_state is not None:
                if (
                    current.name != expected_state.project_name
                    or Path(current.path) != expected_state.project_path
                ):
                    raise ProjectStateConflictError(
                        "QC module filter project context changed before settings commit"
                    )
                if self._settings != expected_state.settings:
                    raise ProjectStateConflictError(
                        "QC module filter project settings changed before settings commit"
                    )
            previous = self._settings
            self._settings = prepared
            try:
                self.save(notify=notify)
            except Exception:
                self._settings = previous
                raise
        if notify and change_event != "settings_saved":
            self._notify(change_event)

    def save(self, *, notify: bool = True) -> None:
        self._save_registry()
        if self.current is not None:
            self._ensure_schema_version(self._settings)
            FileUtils.safe_json_save(self.current.settings_path, self._settings)
            # SETTINGS_SAVED is a typed-only event (new in P1-C). It is emitted
            # directly rather than via _notify so it does not fire the legacy
            # string observers, which only expect project/modules events.
            if notify:
                self.publish_change("settings_saved")

    def publish_change(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Publish a validated service change on the caller's thread."""
        if event == "settings_saved":
            self.event_bus.emit(
                Event(type=EventType.SETTINGS_SAVED, source="ProjectService", data=data)
            )
            return
        if event not in {"project_changed", "modules_changed"}:
            raise ValueError(f"不支持的项目变更事件: {event}")
        self._notify(event, data)

    @staticmethod
    def _ensure_schema_version(payload: dict[str, Any]) -> None:
        """Stamp the current schema version onto an outbound payload, but never
        downgrade an existing higher version (forward-compat). Legacy v0 files
        lack the key entirely; they get versioned on first explicit save."""
        current = payload.get("schema_version")
        if not isinstance(current, int) or current < 1:
            payload["schema_version"] = 1

    def add_observer(self, callback: Callable[[str], None]) -> None:
        """[DEPRECATED] Legacy string observer. Bridged to the typed EventBus
        for transition. Prefer ``service.event_bus.subscribe(EventType.X, ...)``
        directly (P1-D/P2 will retire this bridge)."""
        self._observers.append(callback)

    # Legacy string event name -> typed EventType. settings_saved is new.
    _EVENT_TYPE_MAP = {
        "project_changed": EventType.PROJECT_CHANGED,
        "modules_changed": EventType.MODULES_CHANGED,
        "settings_saved": EventType.SETTINGS_SAVED,
    }

    def new_settings(self) -> dict[str, Any]:
        """Seed settings for a freshly-created project.

        Uses ``schema_version`` (the unified key) instead of the legacy divergent
        ``version`` key (F-SET-9). Version 1 is the stable schema after the P0
        data-safety wave; absence of the key in a loaded file means legacy v0
        (read-only normalization on load, write only on explicit save — P0-E).
        """
        return {
            "schema_version": 1,
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

    def _notify(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Emit a typed Event AND fire legacy string observers (bridge).

        The legacy string observers are told the raw ``event`` name so existing
        callers/tests keep working. The typed bus gets an ``Event`` with the
        mapped EventType, source='ProjectService', and optional payload.
        Unknown event names still notify legacy observers but do not emit a
        typed event (defensive — should not happen in practice).
        """
        for callback in self._observers:
            callback(event)
        event_type = self._EVENT_TYPE_MAP.get(event)
        if event_type is not None:
            self.event_bus.emit(Event(type=event_type, source="ProjectService", data=data))

    def _require_current_project(self) -> None:
        if self.current is None:
            raise ValueError("当前项目未加载")

    def _validate_module_name(self, name: str) -> None:
        if not MODULE_NAME_PATTERN.match(name):
            raise ValueError(f"模块名不合法: {name}")


__all__ = [
    "PreparedProjectLoad",
    "ProjectService",
    "ProjectSettingsState",
    "ProjectStateConflictError",
]
