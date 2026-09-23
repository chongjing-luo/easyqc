"""Toolkit-neutral project context shared by Qt Table, QC and configuration."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd
from pandas.api import types as ptypes

from core.code_executor import CodeExecutor
from core.command_output import ViewerExecutionContext
from core.configuration_service import ConfigurationError, ConfigurationService
from core.event_bus import EventBus
from core.module_filter import (
    normalize_module_filter,
    resolve_module_filter_identities,
)
from core.qc_workflow_service import QcWorkflowService
from core.rating_identity import (
    RatingIdentityError,
    validate_easyqcid,
    validate_module_name,
    validate_rater,
    validate_rating_identity,
)
from core.rating_service import RatingService
from core.project_service import PreparedProjectLoad
from core.table_service import TABLE_ALL
from core.table_view_service import TableViewService
from models.qcmodule import QCModule
from models.qc_row_context import (
    QcModuleMenuEntry,
    QcRecordMenuEntry,
    QcRowContext,
)
from models.rating import Rating
from models.result_association import AssociationSource, ResultAssociationRule
from core.result_association import parse_association_rules, project_associations


class ProjectContextError(RuntimeError):
    """Raised when a cross-workspace project route is stale or ambiguous."""


@dataclass(frozen=True)
class ProjectContextSnapshot:
    """Detached rendering values for one accepted project identity/revision."""

    project_names: tuple[str, ...]
    project_name: str
    project_path: Path | None
    context_revision: int
    subjects: pd.DataFrame
    constants: dict[str, Any]
    modules: tuple[QCModule, ...]
    ratings: tuple[Rating, ...]
    rating_positions_by_easyqcid: Mapping[str, tuple[int, ...]]
    table_view_service: TableViewService
    long_results_table_view_service: TableViewService
    association_rules: tuple[ResultAssociationRule, ...] = ()
    association_sources: Mapping[str, tuple[AssociationSource, ...]] = field(default_factory=dict)
    view_revision: int = 0

    @property
    def has_project(self) -> bool:
        return self.project_path is not None


@dataclass(frozen=True)
class PreparedProjectContext:
    """Fully read candidate that can be accepted after a latest-request check."""

    project_load: PreparedProjectLoad | None
    project_names: tuple[str, ...]
    subjects: pd.DataFrame
    constants: dict[str, Any]
    modules: tuple[QCModule, ...]
    ratings: tuple[Rating, ...]
    rating_positions_by_easyqcid: Mapping[str, tuple[int, ...]]
    table_view_service: TableViewService
    long_results_table_view_service: TableViewService
    association_rules: tuple[ResultAssociationRule, ...] = ()
    association_sources: Mapping[str, tuple[AssociationSource, ...]] = field(default_factory=dict)


class ProjectContextService:
    """Prepare project views and create identity-safe QC sessions.

    Purpose: compose existing project/configuration/rating/table/QC services for
    one active project without introducing a second persistence owner.
    Input: the current project or an explicit registry project name.
    Output: a detached snapshot or one bound ``QcWorkflowService``.
    Side effects: explicit project loads update ``ProjectService.current``;
    snapshots only read facts, while returned workflows own viewer/rating work.
    Errors: load, validation and aggregation errors propagate without an empty
    fallback; stale snapshots raise ``ProjectContextError``.
    Split trigger: configuration writes, cache persistence or GUI rendering stay
    in their existing owners.
    """

    def __init__(
        self,
        configuration_service: ConfigurationService,
        rating_service: RatingService,
        code_executor: CodeExecutor,
        event_bus: EventBus,
    ) -> None:
        self.configuration_service = configuration_service
        self.rating_service = rating_service
        self.code_executor = code_executor
        self._command_executor = None
        self.event_bus = event_bus
        self._identity: tuple[str, Path] | None = None
        self._context_revision = 0
        self._view_revision = 0

    @staticmethod
    def _canonical_path(path: Path) -> Path:
        return Path(path).resolve(strict=False)

    def open_initial(self) -> ProjectContextSnapshot:
        """Load the registered last project when needed, then prepare a view."""

        return self.activate(self.prepare_initial())

    def prepare_initial(self) -> PreparedProjectContext:
        """Read the current/last project without changing active service state."""

        project_service = self.configuration_service.project_service
        current = project_service.current_project
        if current is not None:
            prepared = PreparedProjectLoad(
                project=current,
                settings=deepcopy(dict(project_service.settings)),
            )
            return self._prepare_context(prepared)
        last = project_service.registry.last_project
        if last:
            return self.prepare_project(last)
        subjects = pd.DataFrame(columns=["easyqcid"])
        long_results = RatingService.professional_long_results(pd.DataFrame())
        return PreparedProjectContext(
            project_load=None,
            project_names=tuple(project_service.list_all()),
            subjects=subjects,
            constants={},
            modules=(),
            ratings=(),
            rating_positions_by_easyqcid=MappingProxyType({}),
            table_view_service=TableViewService(subjects),
            long_results_table_view_service=TableViewService(long_results),
        )

    def prepare_project(self, name: str) -> PreparedProjectContext:
        """Read settings, subjects, ratings and Table profiles before activation."""

        prepared = self.configuration_service.project_service.prepare_load(name)
        return self._prepare_context(prepared)

    def load_project(self, name: str) -> ProjectContextSnapshot:
        """Synchronously prepare and activate one named project."""

        return self.activate(self.prepare_project(name))

    def snapshot(self) -> ProjectContextSnapshot:
        """Build a copied configuration/rating/Table view of the current project."""

        return self.activate(self.prepare_initial())

    def activate(self, prepared: PreparedProjectContext) -> ProjectContextSnapshot:
        """Accept a fully read candidate; intended for the GUI completion thread."""

        if not isinstance(prepared, PreparedProjectContext):
            raise TypeError("activate requires PreparedProjectContext")
        if prepared.project_load is None:
            if self.configuration_service.current_project is not None:
                raise ProjectContextError("Cannot activate an empty context over a loaded project")
            if self._identity is not None:
                self._identity = None
                self._context_revision += 1
            self._view_revision += 1
            return ProjectContextSnapshot(
                project_names=prepared.project_names,
                project_name="",
                project_path=None,
                context_revision=self._context_revision,
                view_revision=self._view_revision,
                subjects=prepared.subjects.copy(deep=True),
                constants={},
                modules=(),
                ratings=(),
                rating_positions_by_easyqcid=MappingProxyType({}),
                table_view_service=prepared.table_view_service,
                long_results_table_view_service=(
                    prepared.long_results_table_view_service
                ),
            )

        project = self.configuration_service.project_service.commit_load(
            prepared.project_load,
            notify=False,
        )
        project_path = self._canonical_path(project.path)
        identity = (project.name, project_path)
        if identity != self._identity:
            self._identity = identity
            self._context_revision += 1
        self._view_revision += 1
        return ProjectContextSnapshot(
            project_names=prepared.project_names,
            project_name=project.name,
            project_path=project_path,
            context_revision=self._context_revision,
            view_revision=self._view_revision,
            subjects=prepared.subjects.copy(deep=True),
            constants=deepcopy(prepared.constants),
            modules=tuple(deepcopy(prepared.modules)),
            ratings=tuple(deepcopy(prepared.ratings)),
            rating_positions_by_easyqcid=prepared.rating_positions_by_easyqcid,
            table_view_service=prepared.table_view_service,
            long_results_table_view_service=(
                prepared.long_results_table_view_service
            ),
            association_rules=prepared.association_rules,
            association_sources=prepared.association_sources,
        )

    def _prepare_context(self, prepared: PreparedProjectLoad) -> PreparedProjectContext:
        project = prepared.project
        settings = deepcopy(prepared.settings)
        constants = settings.get("constants", {})
        modules = settings.get("qcmodule", {})
        if not isinstance(constants, dict):
            raise ConfigurationError("constants must be an object")
        if not isinstance(modules, dict) or not modules:
            raise ConfigurationError("At least one QC module is required")
        typed_modules = tuple(
            QCModule.from_legacy_dict(payload)
            for _, payload in sorted(modules.items(), key=lambda item: int(item[0]))
        )
        for module in typed_modules:
            self._validate_module_identity(module)
        subjects = self.configuration_service.table_service.load_table(project, TABLE_ALL)
        if subjects is None:
            subjects = pd.DataFrame(columns=["easyqcid"])
        subjects = self._validate_subjects(subjects, constants)
        loaded_ratings = RatingService(project).load_state(subjects)
        ratings = tuple(deepcopy(loaded_ratings.ratings))
        table_source = self._professional_table_source(subjects, loaded_ratings.qctable)
        long_results = RatingService.attach_master_columns_to_long(
            RatingService.professional_long_results(
                loaded_ratings.original_table
            ),
            subjects,
        )
        rules = parse_association_rules(settings.get("result_associations", []))
        association_sources = MappingProxyType({})
        if rules:
            projection = project_associations(subjects, ratings, rules)
            values_by_identity = projection.columns.set_index("easyqcid")
            for column in values_by_identity.columns:
                if column in table_source.columns or column in long_results.columns:
                    raise ConfigurationError(f"关联列与现有列重名: {column}")
                table_source[column] = table_source["easyqcid"].map(values_by_identity[column])
                long_results[column] = long_results["easyqcid"].map(values_by_identity[column])
            association_sources = MappingProxyType(dict(projection.sources_by_easyqcid))
        return PreparedProjectContext(
            project_load=prepared,
            project_names=tuple(self.configuration_service.project_service.list_all()),
            subjects=subjects,
            constants=deepcopy(constants),
            modules=typed_modules,
            ratings=ratings,
            rating_positions_by_easyqcid=self._index_rating_positions(ratings),
            table_view_service=TableViewService(table_source),
            long_results_table_view_service=TableViewService(long_results),
            association_rules=rules,
            association_sources=association_sources,
        )

    @classmethod
    def _validate_subjects(
        cls,
        frame: pd.DataFrame,
        constants: dict[str, Any],
    ) -> pd.DataFrame:
        if not isinstance(frame, pd.DataFrame):
            raise TypeError("Subjects must be a pandas DataFrame")
        if frame.columns.has_duplicates:
            raise ConfigurationError("Subject table contains duplicate columns")
        if "easyqcid" not in frame.columns:
            raise ConfigurationError("Subject table requires easyqcid")
        result = frame.copy(deep=True)
        identities = tuple(cls._validated_easyqcid(value) for value in result["easyqcid"])
        duplicates = sorted(
            identity for identity, count in Counter(identities).items() if count > 1
        )
        if duplicates:
            raise ProjectContextError(f"Subject table contains duplicate easyqcid: {duplicates}")
        casefolded: dict[str, str] = {}
        case_collisions: set[tuple[str, str]] = set()
        for identity in identities:
            prior = casefolded.setdefault(identity.casefold(), identity)
            if prior != identity:
                case_collisions.add(tuple(sorted((prior, identity))))
        if case_collisions:
            raise ProjectContextError(
                "Subject table contains case-only easyqcid collisions: "
                f"{sorted(case_collisions)}"
            )
        collisions = sorted(set(map(str, result.columns)) & set(constants))
        if collisions:
            raise ConfigurationError(f"Subject column conflicts with constant: {collisions}")
        # An empty Python list infers float64, which cannot join the textual
        # identity column of an empty results table during first project load.
        result["easyqcid"] = pd.Series(identities, index=result.index, dtype=object)
        return result

    @staticmethod
    def _professional_table_source(
        subjects: pd.DataFrame,
        aggregated: pd.DataFrame,
    ) -> pd.DataFrame:
        """Project user-facing rating facts without serializing nested payloads.

        Full legacy module metadata remains authoritative in rating JSON.  The
        Table surface shows subject fields plus review values/status metadata;
        command templates, button dictionaries, schema labels and file paths
        are deliberately not rendered as JSON-like cells.
        """

        subject_columns = [str(column) for column in subjects.columns]
        rating_value = re.compile(r"\.(?:score\d+|tag\d+|notes|time)$")
        rating_columns = [
            str(column)
            for column in aggregated.columns
            if str(column) not in subject_columns and rating_value.search(str(column))
        ]
        return aggregated.loc[:, subject_columns + rating_columns].copy(deep=True)

    @staticmethod
    def _validated_easyqcid(value: Any) -> str:
        try:
            return validate_easyqcid(value)
        except RatingIdentityError as exc:
            raise ProjectContextError(str(exc)) from exc

    @classmethod
    def _normalize_identity(cls, value: Any) -> str:
        if value in (None, ""):
            return ""
        return cls._validated_easyqcid(value)

    @staticmethod
    def _validate_module_identity(module: QCModule) -> None:
        try:
            validate_module_name(module.name)
            if module.rater not in (None, ""):
                validate_rater(module.rater)
        except RatingIdentityError as exc:
            raise ProjectContextError(str(exc)) from exc

    @staticmethod
    def _validated_rating_identity(
        module_name: Any,
        rater: Any,
        easyqcid: Any,
    ) -> tuple[str, str, str]:
        try:
            identity = validate_rating_identity(module_name, rater, easyqcid)
        except RatingIdentityError as exc:
            raise ProjectContextError(str(exc)) from exc
        return identity.module_name, identity.rater, identity.easyqcid

    @classmethod
    def _index_rating_positions(
        cls,
        ratings: tuple[Rating, ...],
    ) -> Mapping[str, tuple[int, ...]]:
        positions: dict[str, list[int]] = {}
        for position, rating in enumerate(ratings):
            identity = cls._normalize_identity(rating.easyqcid)
            if not identity:
                raise ProjectContextError("Rating snapshot contains a blank easyqcid")
            positions.setdefault(identity, []).append(position)
        return MappingProxyType(
            {
                identity: tuple(identity_positions)
                for identity, identity_positions in positions.items()
            }
        )

    @classmethod
    def _ratings_for_identity(
        cls,
        snapshot: ProjectContextSnapshot,
        identity: str,
    ) -> tuple[Rating, ...]:
        positions = snapshot.rating_positions_by_easyqcid.get(identity, ())
        if any(
            position < 0 or position >= len(snapshot.ratings)
            for position in positions
        ):
            raise ProjectContextError("QC rating snapshot index is inconsistent")
        ratings = tuple(snapshot.ratings[position] for position in positions)
        if any(rating.easyqcid != identity for rating in ratings):
            raise ProjectContextError("QC rating snapshot index is inconsistent")
        return ratings

    def _validate_current_snapshot(self, snapshot: ProjectContextSnapshot) -> None:
        project = self.configuration_service.current_project
        current_identity = (
            (project.name, self._canonical_path(project.path))
            if project is not None
            else None
        )
        snapshot_identity = (
            (snapshot.project_name, snapshot.project_path)
            if snapshot.project_path is not None
            else None
        )
        if (
            snapshot.context_revision != self._context_revision
            or snapshot.view_revision != self._view_revision
            or snapshot_identity != self._identity
            or current_identity != self._identity
        ):
            raise ProjectContextError("Project context is stale; reload the current project view")

    def qc_row_context(
        self,
        snapshot: ProjectContextSnapshot,
        easyqcid: str,
    ) -> QcRowContext:
        """Resolve snapshot-backed module and rating facts for one exact row."""

        self._validate_current_snapshot(snapshot)
        identity = self._normalize_identity(easyqcid)
        if not identity:
            raise ProjectContextError(
                f"QC row easyqcid is not in the current project: {identity}"
            )
        selected_subject = snapshot.subjects.loc[
            snapshot.subjects["easyqcid"].eq(identity)
        ].copy(deep=True)
        if selected_subject.empty:
            raise ProjectContextError(
                f"QC row easyqcid is not in the current project: {identity}"
            )
        if len(selected_subject) > 1:
            raise ProjectContextError(
                f"QC row easyqcid is ambiguous in the current project: {identity}"
            )

        module_entries = []
        module_order = {
            module.name: index for index, module in enumerate(snapshot.modules)
        }
        module_labels = {
            module.name: str(module.label or module.name).strip()
            for module in snapshot.modules
        }
        for module in snapshot.modules:
            try:
                expression = normalize_module_filter(module.to_legacy_dict())
                enabled = identity in resolve_module_filter_identities(
                    selected_subject,
                    expression,
                )
            except (TypeError, ValueError) as exc:
                raise ProjectContextError(
                    f"Invalid QC module filter for '{module.name}': {exc}"
                ) from exc
            module_entries.append(
                QcModuleMenuEntry(
                    module_name=module.name,
                    label=str(module.label or module.name),
                    enabled=enabled,
                    disabled_reason=(
                        ""
                        if enabled
                        else "该条目不在此模块的独立质控名单中"
                    ),
                    read_only=bool(module.watch_mode or module.rater in (None, "")),
                )
            )

        record_entries = []
        for rating in self._ratings_for_identity(snapshot, identity):
            payload = rating.to_legacy_dict()
            module_name = self._normalize_identity(rating.module_name)
            rater = self._normalize_identity(rating.rater)
            record_entries.append(
                QcRecordMenuEntry(
                    easyqcid=identity,
                    module_name=module_name,
                    module_label=str(
                        payload.get("label")
                        or module_labels.get(module_name)
                        or module_name
                    ),
                    rater=rater,
                    recorded_at=rating.time,
                )
            )
        record_entries.sort(
            key=lambda entry: (
                module_order.get(entry.module_name, len(module_order)),
                entry.module_name.casefold(),
                entry.rater.casefold(),
            )
        )
        linked_modules = []
        linked_records = []
        local_record_keys = {record.key for record in record_entries}
        seen = set()
        for source in snapshot.association_sources.get(identity, ()):
            key = (source.easyqcid, source.module_name, source.rater)
            if key in seen:
                continue
            seen.add(key)
            source_module = next((m for m in snapshot.modules if m.name == source.module_name), None)
            if source_module is not None and not (
                source.easyqcid == identity and source.rater == source_module.rater
            ):
                source_row = snapshot.subjects.loc[snapshot.subjects["easyqcid"].eq(source.easyqcid)]
                enabled = source.easyqcid in resolve_module_filter_identities(
                    source_row, normalize_module_filter(source_module.to_legacy_dict()),
                )
                linked_modules.append(QcModuleMenuEntry(
                    module_name=source.module_name, label=str(source_module.label or source.module_name),
                    enabled=enabled, disabled_reason="" if enabled else "该条目不在此模块的独立质控名单中",
                    read_only=True, easyqcid=source.easyqcid, rater=source.rater,
                ))
            for rating in self._ratings_for_identity(snapshot, source.easyqcid):
                if (rating.module_name == source.module_name and rating.rater == source.rater
                        and key not in local_record_keys):
                    linked_records.append(QcRecordMenuEntry(
                        easyqcid=source.easyqcid, module_name=source.module_name,
                        module_label=str((rating.module_payload or {}).get("label") or source.module_name),
                        rater=source.rater, recorded_at=rating.time,
                    ))
        try:
            return QcRowContext(
                easyqcid=identity,
                modules=tuple(module_entries),
                records=tuple(record_entries),
                linked_modules=tuple(linked_modules), linked_records=tuple(linked_records),
            )
        except (TypeError, ValueError) as exc:
            raise ProjectContextError(f"Invalid QC row context: {exc}") from exc

    def create_qc_record_workflow(
        self,
        snapshot: ProjectContextSnapshot,
        *,
        easyqcid: str,
        module_name: str,
        rater: str,
    ) -> QcWorkflowService:
        """Create a complete saved-schema workflow for one accepted rating."""

        self._validate_current_snapshot(snapshot)
        module_key, rater_key, identity = self._validated_rating_identity(
            module_name,
            rater,
            easyqcid,
        )
        matches = [
            rating
            for rating in self._ratings_for_identity(snapshot, identity)
            if (
                rating.easyqcid,
                rating.module_name,
                rating.rater,
            )
            == (identity, module_key, rater_key)
        ]
        if not matches:
            raise ProjectContextError(
                "Historical QC record was not found in the current snapshot"
            )
        if len(matches) > 1:
            raise ProjectContextError(
                "Historical QC record is ambiguous in the current snapshot"
            )

        matching_rows = snapshot.subjects.loc[
            snapshot.subjects["easyqcid"].eq(identity)
        ]
        if len(matching_rows) != 1:
            raise ProjectContextError(
                f"Historical QC easyqcid is not in the current project: {identity}"
            )
        payload = deepcopy(matches[0].to_legacy_dict())
        if (
            payload.get("easyqcid") != identity
            or payload.get("name") != module_key
            or payload.get("rater") != rater_key
        ):
            raise ProjectContextError(
                "Historical QC payload does not match its snapshot identity"
            )
        saved_module = QCModule.from_legacy_dict(deepcopy(payload))
        try:
            expression = normalize_module_filter(payload)
            requested = resolve_module_filter_identities(
                snapshot.subjects,
                expression,
            )
        except (TypeError, ValueError) as exc:
            raise ProjectContextError(
                f"Invalid saved QC module filter for '{module_key}': {exc}"
            ) from exc
        if not requested:
            raise ProjectContextError(
                f"Saved QC module filter matches no subjects: {module_key}"
            )
        if identity not in requested:
            raise ProjectContextError(
                "Historical QC easyqcid is not in the saved module queue: "
                f"{identity}"
            )
        source_ids = tuple(
            self._normalize_identity(value) for value in snapshot.subjects["easyqcid"]
        )
        positions = {
            subject_identity: index
            for index, subject_identity in enumerate(source_ids)
        }
        ordered_subjects = snapshot.subjects.iloc[
            [positions[subject_identity] for subject_identity in requested]
        ].reset_index(drop=True)
        rating_dir = (
            snapshot.project_path / "RatingFiles" / module_key / rater_key
            if snapshot.project_path is not None and rater_key
            else None
        )
        return QcWorkflowService(
            saved_module,
            ordered_subjects,
            rating_dir=rating_dir,
            constants=snapshot.constants,
            rating_service=self.rating_service,
            code_executor=self.code_executor,
            initial_easyqcid=identity,
            event_bus=self.event_bus,
            event_context={
                "context_revision": snapshot.context_revision,
                "project_name": snapshot.project_name,
                "project_path": str(snapshot.project_path),
            },
            queue_summaries=self._qc_queue_summaries(
                snapshot,
                saved_module,
                requested,
            ),
            initial_read_only=True,
        )

    def create_qc_workflow(
        self,
        snapshot: ProjectContextSnapshot,
        *,
        module_name: str,
        initial_easyqcid: str,
        navigation_ids: tuple[str, ...] | None = None,
        rater_override: str | None = None,
        code_executor: CodeExecutor | None = None,
        initial_read_only: bool = False,
    ) -> QcWorkflowService:
        """Create one workflow from a validated snapshot and ordered identity queue."""

        self._validate_current_snapshot(snapshot)
        try:
            module_key = validate_module_name(module_name)
        except RatingIdentityError as exc:
            raise ProjectContextError(str(exc)) from exc
        configured_module = next(
            (item for item in snapshot.modules if item.name == module_key),
            None,
        )
        if configured_module is None:
            raise ProjectContextError(f"Unknown QC module in current project: {module_name}")
        module = deepcopy(configured_module)
        if rater_override is not None:
            try:
                module.rater = validate_rater(rater_override)
            except RatingIdentityError as exc:
                raise ProjectContextError(str(exc)) from exc

        source_ids = tuple(
            self._normalize_identity(value) for value in snapshot.subjects["easyqcid"]
        )
        positions = {identity: index for index, identity in enumerate(source_ids)}
        if navigation_ids is None:
            requested = self.resolve_module_queue(snapshot, module_name)
            if not requested:
                raise ProjectContextError(
                    f"QC module filter matches no subjects: {module_name}"
                )
        else:
            requested = tuple(
                self._validated_easyqcid(value) for value in navigation_ids
            )
        if not requested:
            raise ProjectContextError("QC navigation requires at least one easyqcid")
        if any(not identity for identity in requested):
            raise ProjectContextError("QC navigation contains a blank easyqcid")
        duplicates = sorted(
            identity for identity, count in Counter(requested).items() if count > 1
        )
        if duplicates:
            raise ProjectContextError(f"QC navigation contains duplicate easyqcid: {duplicates}")
        foreign = [identity for identity in requested if identity not in positions]
        if foreign:
            raise ProjectContextError(
                f"QC navigation easyqcid is not in the current project: {foreign}"
            )
        initial = self._normalize_identity(initial_easyqcid)
        if initial not in requested:
            raise ProjectContextError(
                f"Initial easyqcid is not in the applied QC navigation queue: {initial}"
            )

        ordered_subjects = snapshot.subjects.iloc[
            [positions[identity] for identity in requested]
        ].reset_index(drop=True)
        rater = self._normalize_identity(module.rater)
        rating_dir = (
            snapshot.project_path / "RatingFiles" / module.name / rater
            if snapshot.project_path is not None and rater
            else None
        )
        queue_summaries = self._qc_queue_summaries(
            snapshot,
            module,
            requested,
        )
        return QcWorkflowService(
            module,
            ordered_subjects,
            rating_dir=rating_dir,
            constants=snapshot.constants,
            rating_service=self.rating_service,
            code_executor=code_executor if code_executor is not None else self.code_executor,
            initial_easyqcid=initial,
            event_bus=self.event_bus,
            event_context={
                "context_revision": snapshot.context_revision,
                "project_name": snapshot.project_name,
                "project_path": str(snapshot.project_path),
            },
            queue_summaries=queue_summaries,
            initial_read_only=initial_read_only,
        )

    @property
    def command_executor(self):
        """Own command-only processes independently from the current QC session."""
        if self._command_executor is None:
            self._command_executor = CodeExecutor(
                shell_enabled=self.code_executor.shell_enabled,
                timeout=self.code_executor.timeout, system=self.code_executor.system,
            )
        return self._command_executor

    def launch_row_command(self, snapshot, easyqcid, module_name, *, rater_override=None):
        """Execute one applicable source row; never save a rating or close QC."""
        context = self.qc_row_context(snapshot, easyqcid)
        entry = next((m for m in context.modules if m.module_name == module_name), None)
        if entry is None or not entry.enabled:
            raise ProjectContextError("该条目不在此模块的独立质控名单中")
        executor = self.command_executor
        if isinstance(executor, CodeExecutor):
            executor.set_shell_enabled(self.code_executor.shell_enabled)
        workflow = self.create_qc_workflow(
            snapshot, module_name=module_name, initial_easyqcid=easyqcid,
            navigation_ids=(easyqcid,), rater_override=rater_override, code_executor=executor,
        )
        # Only the long-lived executor owns these processes. No ephemeral
        # window is created and workflow.close() must not terminate them here.
        plan = workflow.viewer_plan()
        # This independent executor may own earlier, uncontrolled commands.
        # A failed new launch must not call the QC session's broad cleanup.
        return executor.start_commands(
            plan.commands, control=plan.control, shell=plan.shell,
            output_contexts={
                key: ViewerExecutionContext(
                    module_name=module_name,
                    rater=workflow.current_module.rater or "",
                    easyqcid=easyqcid, command_index=key,
                )
                for key in plan.commands
            },
        )

    def close_row_commands(self) -> None:
        if self._command_executor is not None:
            self._command_executor.close()
            self._command_executor = None

    def resolve_module_queue(
        self,
        snapshot: ProjectContextSnapshot,
        module_name: str,
    ) -> tuple[str, ...]:
        """Resolve one saved module rule against ordinary complete-list columns."""

        self._validate_current_snapshot(snapshot)
        try:
            module_key = validate_module_name(module_name)
        except RatingIdentityError as exc:
            raise ProjectContextError(str(exc)) from exc
        module = next(
            (item for item in snapshot.modules if item.name == module_key),
            None,
        )
        if module is None:
            raise ProjectContextError(
                f"Unknown QC module in current project: {module_name}"
            )
        try:
            expression = normalize_module_filter(module.to_legacy_dict())
            return resolve_module_filter_identities(
                snapshot.subjects,
                expression,
            )
        except (TypeError, ValueError) as exc:
            raise ProjectContextError(
                f"Invalid QC module filter for '{module_name}': {exc}"
            ) from exc

    @classmethod
    def _qc_queue_summaries(
        cls,
        snapshot: ProjectContextSnapshot,
        module: QCModule,
        identities: tuple[str, ...],
    ) -> dict[str, tuple[str, str]]:
        """Project accepted rating facts into compact queue display strings."""

        rater = cls._normalize_identity(module.rater)
        if not rater:
            return {}
        service = snapshot.table_view_service
        available = {profile.name for profile in service.profiles}
        prefix = f"{module.name}.{rater}."
        score_columns = tuple(
            column
            for key in module.scores
            for column in (f"{prefix}score{key}",)
            if column in available
        )
        tag_columns = tuple(
            (column, module.tags[key].label)
            for key in module.tags
            for column in (f"{prefix}tag{key}",)
            if column in available
        )
        if not score_columns and not tag_columns:
            return {}
        selected_columns = (
            "easyqcid",
            *score_columns,
            *(column for column, _label in tag_columns),
        )
        result = service.apply_state(
            service.default_state(page_size=max(1, service.source_total))
        )
        frame = service.get_window(
            result,
            0,
            max(1, service.source_total),
            selected_columns,
        ).dataframe
        normalized_ids = frame["easyqcid"].map(cls._normalize_identity)
        requested = set(identities)
        keep = normalized_ids.isin(requested)
        frame = frame.loc[keep].reset_index(drop=True)
        normalized_ids = normalized_ids.loc[keep].reset_index(drop=True)
        scores = pd.Series("", index=frame.index, dtype="string")
        for column in score_columns:
            values = frame[column].astype("string").fillna("").str.strip()
            scores = cls._join_summary_values(scores, values, " / ")
        tags = pd.Series("", index=frame.index, dtype="string")
        for column, label in tag_columns:
            values = pd.Series("", index=frame.index, dtype="string")
            values.loc[cls._summary_tag_mask(frame[column])] = str(label).strip()
            tags = cls._join_summary_values(tags, values, "、")
        return {
            identity: (str(score), str(tag))
            for identity, score, tag in zip(normalized_ids, scores, tags)
        }

    @staticmethod
    def _join_summary_values(
        existing: pd.Series,
        values: pd.Series,
        separator: str,
    ) -> pd.Series:
        both = existing.ne("") & values.ne("")
        combined = existing.where(values.eq(""), values)
        combined.loc[both] = existing.loc[both] + separator + values.loc[both]
        return combined

    @staticmethod
    def _summary_tag_mask(series: pd.Series) -> pd.Series:
        if ptypes.is_bool_dtype(series.dtype):
            return series.fillna(False).astype(bool)
        if ptypes.is_numeric_dtype(series.dtype):
            return series.fillna(0).ne(0)
        return (
            series.astype("string")
            .fillna("")
            .str.strip()
            .str.casefold()
            .isin({"true", "1", "yes"})
        )


__all__ = [
    "ProjectContextError",
    "ProjectContextService",
    "ProjectContextSnapshot",
    "PreparedProjectContext",
]
