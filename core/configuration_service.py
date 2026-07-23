"""Transactional project and configuration operations for GUI adapters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import keyword
from pathlib import Path
import re
from typing import Any

import pandas as pd

from core.event_bus import Event, EventType
from core.project_service import MODULE_NAME_PATTERN, ProjectService
from core.table_service import TABLE_ALL, TableService
from core.table_transform import TableTransformEngine
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


@dataclass(frozen=True)
class ProjectListEntry:
    """Detached registered-project metadata for non-mutating GUI previews."""

    name: str
    path: Path
    is_current: bool
    is_most_recent: bool


class ConfigurationService:
    """Provide typed, atomic operations for project configuration pages."""

    def __init__(self, project_service: ProjectService, table_service: TableService) -> None:
        self.project_service = project_service
        self.table_service = table_service

    def projects(self) -> tuple[str, ...]:
        return tuple(self.project_service.list_all())

    def project_entries(self) -> tuple[ProjectListEntry, ...]:
        """Return registered paths and open state without exposing the registry."""

        current_name = (
            self.current_project.name if self.current_project is not None else None
        )
        recent_name = self.project_service.registry.last_project
        return tuple(
            ProjectListEntry(
                name=name,
                path=Path(project.path),
                is_current=name == current_name,
                is_most_recent=name == recent_name,
            )
            for name, project in self.project_service.registry.projects.items()
        )

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

    @staticmethod
    def _import_column_name(value: str) -> str:
        name = str(value).strip()
        if not name or not name.isidentifier():
            raise ConfigurationError(f"单列字段名不合法: {name!r}")
        return name

    @classmethod
    def _normalize_import_frame(
        cls,
        frame: pd.DataFrame,
        *,
        single_column_name: str | None = None,
    ) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("导入草稿必须是 pandas DataFrame")
        if frame.empty:
            raise ConfigurationError("导入数据为空")
        columns = tuple(str(column).strip() for column in frame.columns)
        if any(not column for column in columns):
            raise ConfigurationError("导入数据包含空白字段名")
        if len(set(columns)) != len(columns):
            raise ConfigurationError("导入数据包含重复字段名")
        result = frame.copy(deep=True)
        result.columns = columns
        if len(columns) == 1 and single_column_name is not None:
            result.columns = (cls._import_column_name(single_column_name),)
        return result.reset_index(drop=True)

    def draft_from_folder(
        self,
        path: str | Path,
        column_name: str,
    ) -> pd.DataFrame:
        """Read immediate child-directory names into one detached draft."""

        directory = Path(path)
        if not directory.is_dir():
            raise ConfigurationError(f"导入目录不存在: {directory}")
        name = self._import_column_name(column_name)
        values = sorted(entry.name for entry in directory.iterdir() if entry.is_dir())
        return self._normalize_import_frame(pd.DataFrame({name: values}))

    def draft_from_file(
        self,
        path: str | Path,
        single_column_name: str | None = None,
    ) -> pd.DataFrame:
        """Read one supported table/list file into a detached draft."""

        source = Path(path)
        if not source.is_file():
            raise ConfigurationError(f"导入文件不存在: {source}")
        suffix = source.suffix.casefold()
        try:
            if suffix == ".csv":
                frame = pd.read_csv(source, encoding="utf-8")
            elif suffix in {".xlsx", ".xls"}:
                frame = pd.read_excel(source)
            elif suffix in {".txt", ".list"}:
                name = self._import_column_name(single_column_name or "")
                values = [
                    line.strip()
                    for line in source.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                frame = pd.DataFrame({name: values})
                single_column_name = None
            else:
                raise ConfigurationError(
                    f"不支持的导入文件格式: {suffix or '<none>'}"
                )
        except ConfigurationError:
            raise
        except Exception as exc:
            raise ConfigurationError(f"读取导入文件失败: {exc}") from exc
        return self._normalize_import_frame(
            frame,
            single_column_name=(
                single_column_name.strip()
                if single_column_name is not None and single_column_name.strip()
                else None
            ),
        )

    def draft_from_text(self, text: str, column_name: str) -> pd.DataFrame:
        """Split direct comma/whitespace text into one detached draft column."""

        name = self._import_column_name(column_name)
        values = [value for value in re.split(r"[,\s]+", str(text).strip()) if value]
        return self._normalize_import_frame(pd.DataFrame({name: values}))

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
            raise TypeError("质控名单必须是 pandas DataFrame")
        if frame.columns.has_duplicates:
            raise ConfigurationError("质控名单包含重复字段")
        if "ezqcid" not in frame.columns:
            raise ConfigurationError("质控名单缺少 ezqcid")
        result = frame.copy(deep=True)
        identities = result["ezqcid"].map(
            lambda value: "" if value is None or pd.isna(value) else str(value).strip()
        )
        if identities.eq("").any():
            raise ConfigurationError("质控名单包含空白 ezqcid")
        duplicates = sorted(identities[identities.duplicated(keep=False)].unique().tolist())
        if duplicates:
            raise ConfigurationError(f"质控名单包含重复 ezqcid: {duplicates}")
        result["ezqcid"] = identities
        constant_collisions = sorted(set(map(str, result.columns)) & set(self.constants()))
        if constant_collisions:
            raise ConfigurationError(
                f"质控名单字段与常量冲突: {constant_collisions}"
            )
        return result

    def replace_subjects(self, frame: pd.DataFrame, *, notify: bool = True) -> None:
        validated = self._validated_subjects(frame)
        self.table_service.save_table(self._require_project(), TABLE_ALL, validated)
        if notify:
            self.publish_subjects_changed()

    def derive_subject_column(
        self,
        name: str,
        expression: str,
        *,
        notify: bool = True,
    ) -> str:
        """Calculate and atomically persist one ordinary subject-table column."""

        if not isinstance(name, str):
            raise ConfigurationError("新增列名必须是文本")
        if not isinstance(expression, str):
            raise ConfigurationError("新增列表达式必须是文本")
        column_name = name.strip()
        formula = expression.strip()
        if not column_name:
            raise ConfigurationError("新增列名不能为空")
        if not column_name.isidentifier() or keyword.iskeyword(column_name):
            raise ConfigurationError("新增列名必须是不含空格或标点的有效字段名")
        if not formula:
            raise ConfigurationError("新增列表达式不能为空")
        frame = self.subjects()
        if column_name in frame.columns:
            raise ConfigurationError(f"列已存在: {column_name}")
        try:
            frame = TableTransformEngine().derive_column(
                frame,
                column_name,
                formula,
            )
            frame = self._validated_subjects(frame)
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise ConfigurationError(str(exc)) from exc
        self.table_service.save_table(
            self._require_project(),
            TABLE_ALL,
            frame,
        )
        if notify:
            self.publish_subjects_changed()
        return column_name

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
            raise ConfigurationError(f"Unsupported list merge mode: {mode}")
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
            raise ConfigurationError(f"Constant conflicts with list column: {name}")
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


__all__ = [
    "ConfigurationError",
    "ConfigurationService",
    "ConfigurationSnapshot",
    "ProjectListEntry",
]
