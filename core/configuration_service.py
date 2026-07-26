"""Transactional project and configuration operations for GUI adapters."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
import re
from typing import Any

import pandas as pd

from core.event_bus import Event, EventType
from core.module_filter import resolve_module_filter_identities
from core.project_service import (
    MODULE_NAME_PATTERN,
    ProjectService,
    ProjectSettingsState,
    ProjectStateConflictError,
)
from core.table_service import TABLE_ALL, TableService
from core.table_transform import TableTransformEngine, TableTransformError
from core.table_view_service import TableViewError
from models.derived_formula import DerivedColumnFormula
from models.folder_match import FolderMatchRequest
from models.project import Project
from models.qcmodule import QCModule, Score, Tag
from models.table_view_state import (
    FilterExpression,
    TableViewStateContractError,
    filter_expression_to_json_object,
)
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


@dataclass(frozen=True)
class SubjectImportSummary:
    """Stable counts describing one validated subject-list import candidate."""

    mode: str
    conflict_policy: str | None
    current_rows: int
    incoming_rows: int
    result_rows: int
    matching_identities: int
    new_identities: int
    overlapping_columns: tuple[str, ...]


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

    def draft_from_folder_matches(
        self,
        path: str | Path,
        request: FolderMatchRequest,
    ) -> pd.DataFrame:
        """Discover safe basename matches as a deterministic detached draft."""

        directory = Path(path)
        if not directory.is_dir() or directory.is_symlink():
            raise ConfigurationError(f"导入目录不存在或不可用: {directory}")
        if not isinstance(request, FolderMatchRequest):
            raise ConfigurationError("文件夹匹配请求必须是 FolderMatchRequest")

        compiled_regex: re.Pattern[str] | None = None
        if request.match_kind == "regex":
            try:
                compiled_regex = re.compile(request.pattern)
            except re.error as exc:
                raise ConfigurationError(f"正则表达式无效: {exc}") from exc

        def name_matches(name: str) -> bool:
            if request.match_kind == "starts_with":
                return name.startswith(request.pattern)
            if request.match_kind == "ends_with":
                return name.endswith(request.pattern)
            if request.match_kind == "contains":
                return request.pattern in name
            if request.match_kind == "wildcard":
                return fnmatchcase(name, request.pattern)
            assert compiled_regex is not None
            return compiled_regex.search(name) is not None

        matches: list[tuple[str, str, str]] = []
        pending: list[tuple[Path, int]] = [(directory, 0)]
        while pending:
            parent, parent_depth = pending.pop()
            try:
                entries = sorted(parent.iterdir(), key=lambda entry: entry.name)
            except OSError as exc:
                raise ConfigurationError(f"无法读取目录 {parent}: {exc}") from exc

            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    is_directory = entry.is_dir()
                    is_file = entry.is_file()
                except OSError as exc:
                    raise ConfigurationError(f"无法读取路径 {entry}: {exc}") from exc

                depth = parent_depth + 1
                in_scope = (
                    request.scope == "all"
                    or (request.scope == "direct" and depth == 1)
                    or (
                        request.scope == "exact"
                        and depth == request.exact_depth
                    )
                )
                target_matches = (
                    request.target_kind == "both"
                    or (request.target_kind == "directory" and is_directory)
                    or (request.target_kind == "file" and is_file)
                )
                relative = entry.relative_to(directory)
                relative_posix = relative.as_posix()
                if in_scope and target_matches and name_matches(entry.name):
                    parent_value = (
                        ""
                        if relative.parent == Path(".")
                        else relative.parent.as_posix()
                    )
                    matches.append((relative_posix, parent_value, entry.name))

                should_descend = is_directory and (
                    request.scope == "all"
                    or (
                        request.scope == "exact"
                        and depth < request.exact_depth
                    )
                )
                if should_descend:
                    pending.append((entry, depth))

        if not matches:
            raise ConfigurationError("没有找到符合条件的匹配项")
        matches.sort(key=lambda item: item[0])
        if request.parent_column is None:
            frame = pd.DataFrame(
                {request.item_column: [item_name for _, _, item_name in matches]}
            )
        else:
            frame = pd.DataFrame(
                {
                    request.parent_column: [
                        parent_value for _, parent_value, _ in matches
                    ],
                    request.item_column: [
                        item_name for _, _, item_name in matches
                    ],
                }
            )
        return self._normalize_import_frame(frame)

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

    def delete_subject_rows(
        self,
        ezqcids: tuple[str, ...],
        *,
        notify: bool = True,
    ) -> int:
        """Atomically remove exact identities from the authoritative QC list.

        Input is one nonempty tuple of unique, nonblank ``ezqcid`` strings.
        Output is the number of rows removed. The only side effect is replacing
        ``TABLE_ALL`` and publishing after that save succeeds; rating storage is
        intentionally outside this contract.
        """

        if not isinstance(ezqcids, tuple):
            raise TypeError("删除名单行必须提供 ezqcid 元组")
        normalized = tuple(
            value.strip() if isinstance(value, str) else ""
            for value in ezqcids
        )
        if not normalized or any(not value for value in normalized):
            raise ConfigurationError("删除名单行必须提供非空 ezqcid")
        if len(set(normalized)) != len(normalized):
            raise ConfigurationError("删除名单行包含重复 ezqcid")
        frame = self._validated_subjects(self.subjects())
        available = set(frame["ezqcid"])
        missing = tuple(value for value in normalized if value not in available)
        if missing:
            raise ConfigurationError(f"质控前名单行不存在或已变化: {missing}")
        candidate = frame.loc[~frame["ezqcid"].isin(normalized)].reset_index(
            drop=True
        )
        self.replace_subjects(candidate, notify=notify)
        return len(normalized)

    def delete_subject_columns(
        self,
        columns: tuple[str, ...],
        *,
        notify: bool = True,
    ) -> int:
        """Atomically remove exact ordinary columns from the authoritative list.

        ``ezqcid`` is always protected. The only persistent side effect is the
        validated ``TABLE_ALL`` replacement; no formula or rating data is read
        or changed.
        """

        if not isinstance(columns, tuple):
            raise TypeError("删除名单列必须提供列名元组")
        normalized = tuple(
            value if isinstance(value, str) else ""
            for value in columns
        )
        if not normalized or any(not value for value in normalized):
            raise ConfigurationError("删除名单列必须提供非空列名")
        if len(set(normalized)) != len(normalized):
            raise ConfigurationError("删除名单列包含重复列名")
        if "ezqcid" in normalized:
            raise ConfigurationError("质控前名单不能删除 ezqcid")
        frame = self._validated_subjects(self.subjects())
        missing = tuple(value for value in normalized if value not in frame.columns)
        if missing:
            raise ConfigurationError(f"质控前名单列不存在或已变化: {missing}")
        try:
            candidate = TableTransformEngine().drop_columns(
                frame,
                list(normalized),
            )
        except TableTransformError as exc:
            raise ConfigurationError(str(exc)) from exc
        self.replace_subjects(candidate, notify=notify)
        return len(normalized)

    def derive_subject_column(
        self,
        request: DerivedColumnFormula,
        *,
        notify: bool = True,
    ) -> str:
        """Calculate and atomically persist one ordinary derived column."""

        if not isinstance(request, DerivedColumnFormula):
            raise ConfigurationError("新增列请求必须是 DerivedColumnFormula")
        column_name = request.name
        frame = self.subjects()
        if column_name in frame.columns:
            raise ConfigurationError(f"列已存在: {column_name}")
        try:
            frame = TableTransformEngine().derive_column_from_formula(
                frame,
                request,
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

    def preview_subject_import(
        self,
        incoming: pd.DataFrame,
        *,
        mode: str,
        conflict_policy: str | None,
    ) -> SubjectImportSummary:
        """Validate one explicit import policy without changing project data."""

        _candidate, summary = self._subject_import_candidate(
            incoming,
            mode=mode,
            conflict_policy=conflict_policy,
        )
        return summary

    def import_subjects(
        self,
        incoming: pd.DataFrame,
        *,
        mode: str,
        conflict_policy: str | None,
        notify: bool = True,
    ) -> SubjectImportSummary:
        """Atomically apply one explicit subject-list import policy.

        Input is one detached table plus a compatible mode/policy pair. Output
        is the validated impact summary. The only persistent side effect is one
        ``TABLE_ALL`` replacement followed by an optional success event.
        """

        candidate, summary = self._subject_import_candidate(
            incoming,
            mode=mode,
            conflict_policy=conflict_policy,
        )
        self.replace_subjects(candidate, notify=notify)
        return summary

    def _subject_import_candidate(
        self,
        incoming: pd.DataFrame,
        *,
        mode: str,
        conflict_policy: str | None,
    ) -> tuple[pd.DataFrame, SubjectImportSummary]:
        policies = {
            "replace": {None},
            "append": {"deduplicate", "replace"},
            "merge_columns": {"preserve", "update"},
        }
        if mode not in policies:
            raise ConfigurationError(f"不支持的质控名单导入方式: {mode}")
        if conflict_policy not in policies[mode]:
            raise ConfigurationError(
                f"质控名单导入方式 {mode} 不支持冲突策略: {conflict_policy}"
            )
        incoming = self._validated_subjects(incoming)
        current = self._validated_subjects(self.subjects())
        current_ids = current["ezqcid"].tolist()
        incoming_ids = incoming["ezqcid"].tolist()
        current_id_set = set(current_ids)
        matching = tuple(
            identity for identity in incoming_ids if identity in current_id_set
        )
        new_ids = tuple(
            identity for identity in incoming_ids if identity not in current_id_set
        )
        overlapping = tuple(
            str(column)
            for column in incoming.columns
            if column != "ezqcid" and column in current.columns
        )

        if mode == "replace" or (
            current.empty and tuple(current.columns) == ("ezqcid",)
        ):
            candidate = incoming.copy(deep=True)
        elif mode == "append":
            candidate = self._append_subject_import(
                current,
                incoming,
                conflict_policy=str(conflict_policy),
            )
        else:
            candidate = self._merge_subject_import_columns(
                current,
                incoming,
                conflict_policy=str(conflict_policy),
            )
        candidate = self._validated_subjects(candidate)
        summary = SubjectImportSummary(
            mode=mode,
            conflict_policy=conflict_policy,
            current_rows=len(current),
            incoming_rows=len(incoming),
            result_rows=len(candidate),
            matching_identities=len(matching),
            new_identities=len(new_ids),
            overlapping_columns=overlapping,
        )
        return candidate, summary

    def _append_subject_import(
        self,
        current: pd.DataFrame,
        incoming: pd.DataFrame,
        *,
        conflict_policy: str,
    ) -> pd.DataFrame:
        """Append schema-compatible rows using one duplicate-identity policy."""

        if set(current.columns) != set(incoming.columns):
            raise ConfigurationError("追加行要求导入名单与现有名单包含相同字段")
        ordered = incoming.loc[:, current.columns]
        current_ids = current["ezqcid"].tolist()
        current_id_set = set(current_ids)
        new_rows = ordered.loc[~ordered["ezqcid"].isin(current_id_set)]
        if conflict_policy == "deduplicate":
            base = current
        else:
            base = current.set_index("ezqcid", drop=False)
            replacements = ordered.set_index("ezqcid", drop=False)
            matching = [
                identity
                for identity in current_ids
                if identity in replacements.index
            ]
            if matching:
                base.loc[matching, current.columns] = replacements.loc[
                    matching,
                    current.columns,
                ].to_numpy()
            base = base.reset_index(drop=True)
        return pd.concat([base, new_rows], ignore_index=True)

    @staticmethod
    def _incoming_value_is_present(value: object) -> bool:
        if value is None or pd.isna(value):
            return False
        return not (isinstance(value, str) and not value.strip())

    def _merge_subject_import_columns(
        self,
        current: pd.DataFrame,
        incoming: pd.DataFrame,
        *,
        conflict_policy: str,
    ) -> pd.DataFrame:
        """Merge columns by identity without ambiguous suffix columns."""

        current_ids = current["ezqcid"].tolist()
        incoming_ids = incoming["ezqcid"].tolist()
        current_id_set = set(current_ids)
        new_ids = [
            identity for identity in incoming_ids if identity not in current_id_set
        ]
        result_ids = [*current_ids, *new_ids]
        current_by_id = current.set_index("ezqcid", drop=False)
        incoming_by_id = incoming.set_index("ezqcid", drop=False)
        output_columns = [
            *current.columns,
            *(
                column
                for column in incoming.columns
                if column not in current.columns
            ),
        ]
        candidate = pd.DataFrame({"ezqcid": result_ids})
        for column in output_columns:
            if column == "ezqcid":
                continue
            values = pd.Series(pd.NA, index=result_ids, dtype="object")
            if column in current_by_id.columns:
                values.loc[current_ids] = current_by_id[column].tolist()
            if column in incoming_by_id.columns:
                if column not in current_by_id.columns:
                    values.loc[incoming_ids] = incoming_by_id[column].tolist()
                else:
                    if new_ids:
                        values.loc[new_ids] = incoming_by_id.loc[
                            new_ids,
                            column,
                        ].tolist()
                    if conflict_policy == "update":
                        matching = [
                            identity
                            for identity in incoming_ids
                            if identity in current_id_set
                        ]
                        updates = incoming_by_id.loc[matching, column]
                        accepted = updates.map(self._incoming_value_is_present)
                        accepted_ids = updates.index[accepted].tolist()
                        if accepted_ids:
                            values.loc[accepted_ids] = updates.loc[
                                accepted_ids
                            ].tolist()
            candidate[column] = values.tolist()
        return candidate.loc[:, output_columns]

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

    def capture_settings_state(self) -> ProjectSettingsState:
        """Capture the project/settings authority for one background write."""

        try:
            return self.project_service.capture_settings_state()
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc

    @staticmethod
    def _constant_name(name: str) -> str:
        normalized = name.strip()
        if not normalized or not normalized.isidentifier():
            raise ConfigurationError(f"Invalid constant name: {normalized!r}")
        return normalized

    def add_constant(self, name: str, value: Any) -> None:
        """Add one absent project constant through the settings transaction.

        ``name`` must be a Python identifier not already owned by either the
        project constants or current master-list columns. The only side effect
        is one settings commit; rejected candidates do not mutate the project.
        """

        normalized = self._constant_name(name)
        candidate = self._settings_candidate()
        constants = candidate.setdefault("constants", {})
        if normalized in constants:
            raise ConfigurationError(f"Constant already exists: {normalized}")
        if normalized in set(map(str, self.subjects().columns)):
            raise ConfigurationError(
                f"Constant conflicts with list column: {normalized}"
            )
        constants[normalized] = value
        self.project_service.commit_settings(candidate)

    def update_constant(
        self,
        old_name: str,
        name: str,
        value: Any,
    ) -> None:
        """Update or rename one existing project constant atomically."""

        source_name = old_name.strip()
        normalized = self._constant_name(name)
        candidate = self._settings_candidate()
        constants = candidate.setdefault("constants", {})
        if source_name not in constants:
            raise ConfigurationError(f"Constant does not exist: {source_name}")
        if normalized != source_name:
            if normalized in constants:
                raise ConfigurationError(
                    f"Constant already exists: {normalized}"
                )
            if normalized in set(map(str, self.subjects().columns)):
                raise ConfigurationError(
                    f"Constant conflicts with list column: {normalized}"
                )
            del constants[source_name]
        constants[normalized] = value
        self.project_service.commit_settings(candidate)

    def set_constant(
        self,
        name: str,
        value: Any,
        *,
        old_name: str | None = None,
    ) -> None:
        """Compatibility upsert; new callers should choose add or update."""

        normalized = self._constant_name(name)
        source_name = old_name.strip() if old_name is not None else normalized
        if source_name in self.constants():
            self.update_constant(source_name, normalized, value)
        else:
            self.add_constant(normalized, value)

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

    def save_module_filter(
        self,
        module_name: str,
        expression: FilterExpression,
        *,
        notify: bool = True,
        expected_identities: tuple[str, ...] | None = None,
        expected_state: ProjectSettingsState | None = None,
    ) -> tuple[str, ...]:
        """Validate and atomically save only one module's structured filter.

        When ``expected_identities`` is supplied, the current complete list
        must still resolve to that exact ordered tuple before settings commit.
        """

        if not isinstance(module_name, str) or not module_name.strip():
            raise ConfigurationError("Module name must be a nonblank string")
        name = module_name.strip()
        state = expected_state or self.capture_settings_state()
        if not isinstance(state, ProjectSettingsState):
            raise ConfigurationError(
                "Expected module filter state must be ProjectSettingsState"
            )
        candidate = deepcopy(state.settings)
        modules = candidate.get("qcmodule")
        if not isinstance(modules, dict):
            raise ConfigurationError("qcmodule must be an object")
        matches = [
            payload
            for payload in modules.values()
            if isinstance(payload, dict) and payload.get("name") == name
        ]
        if not matches:
            raise ConfigurationError(f"Unknown module: {name}")
        if len(matches) != 1:
            raise ConfigurationError(f"Module name is ambiguous: {name}")

        try:
            identities = resolve_module_filter_identities(
                self.subjects(),
                expression,
            )
            serialized = filter_expression_to_json_object(expression)
        except (TableViewError, TableViewStateContractError) as exc:
            raise ConfigurationError(str(exc)) from exc
        if expected_identities is not None:
            if not isinstance(expected_identities, tuple) or not all(
                isinstance(identity, str) for identity in expected_identities
            ):
                raise ConfigurationError(
                    "Expected module filter identities must be a tuple of strings"
                )
            if identities != expected_identities:
                raise ConfigurationError(
                    "QC module filter matches changed before settings commit"
                )

        selected = matches[0]
        selected["qc_filter"] = serialized
        selected["select_filter"] = None
        try:
            self.project_service.commit_settings(
                candidate,
                notify=notify,
                expected_state=state,
            )
        except ProjectStateConflictError as exc:
            raise ConfigurationError(str(exc)) from exc
        return identities

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
    "SubjectImportSummary",
]
