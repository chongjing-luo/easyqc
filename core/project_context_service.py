"""Toolkit-neutral project context shared by Qt Table, QC and configuration."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import pandas as pd

from core.code_executor import CodeExecutor
from core.configuration_service import ConfigurationError, ConfigurationService
from core.event_bus import EventBus
from core.qc_workflow_service import QcWorkflowService
from core.rating_service import RatingService
from core.project_service import PreparedProjectLoad
from core.table_service import TABLE_ALL
from core.table_view_service import TableViewService
from models.qcmodule import QCModule


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
    table_view_service: TableViewService

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
    table_view_service: TableViewService


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
        self.event_bus = event_bus
        self._identity: tuple[str, Path] | None = None
        self._context_revision = 0

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
        subjects = pd.DataFrame(columns=["ezqcid"])
        return PreparedProjectContext(
            project_load=None,
            project_names=tuple(project_service.list_all()),
            subjects=subjects,
            constants={},
            modules=(),
            table_view_service=TableViewService(subjects),
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
            return ProjectContextSnapshot(
                project_names=prepared.project_names,
                project_name="",
                project_path=None,
                context_revision=self._context_revision,
                subjects=prepared.subjects.copy(deep=True),
                constants={},
                modules=(),
                table_view_service=prepared.table_view_service,
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
        return ProjectContextSnapshot(
            project_names=prepared.project_names,
            project_name=project.name,
            project_path=project_path,
            context_revision=self._context_revision,
            subjects=prepared.subjects.copy(deep=True),
            constants=deepcopy(prepared.constants),
            modules=tuple(deepcopy(prepared.modules)),
            table_view_service=prepared.table_view_service,
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
        subjects = self.configuration_service.table_service.load_table(project, TABLE_ALL)
        if subjects is None:
            subjects = pd.DataFrame(columns=["ezqcid"])
        subjects = self._validate_subjects(subjects, constants)
        loaded_ratings = RatingService(project).load_legacy_state(subjects)
        table_source = self._professional_table_source(subjects, loaded_ratings.qctable)
        return PreparedProjectContext(
            project_load=prepared,
            project_names=tuple(self.configuration_service.project_service.list_all()),
            subjects=subjects,
            constants=deepcopy(constants),
            modules=typed_modules,
            table_view_service=TableViewService(table_source),
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
        if "ezqcid" not in frame.columns:
            raise ConfigurationError("Subject table requires ezqcid")
        result = frame.copy(deep=True)
        identities = result["ezqcid"].map(cls._normalize_identity)
        if identities.eq("").any():
            raise ConfigurationError("Subject table contains blank ezqcid")
        duplicates = sorted(identities[identities.duplicated(keep=False)].unique().tolist())
        if duplicates:
            raise ConfigurationError(f"Subject table contains duplicate ezqcid: {duplicates}")
        collisions = sorted(set(map(str, result.columns)) & set(constants))
        if collisions:
            raise ConfigurationError(f"Subject column conflicts with constant: {collisions}")
        result["ezqcid"] = identities
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
    def _normalize_identity(value: Any) -> str:
        if value is None or pd.isna(value):
            return ""
        return str(value).strip()

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
            or snapshot_identity != self._identity
            or current_identity != self._identity
        ):
            raise ProjectContextError("Project context is stale; reload the current project view")

    def create_qc_workflow(
        self,
        snapshot: ProjectContextSnapshot,
        *,
        module_name: str,
        initial_ezqcid: str,
        navigation_ids: tuple[str, ...] | None = None,
    ) -> QcWorkflowService:
        """Create one workflow from a validated snapshot and ordered identity queue."""

        self._validate_current_snapshot(snapshot)
        module = next((item for item in snapshot.modules if item.name == module_name), None)
        if module is None:
            raise ProjectContextError(f"Unknown QC module in current project: {module_name}")

        source_ids = tuple(
            self._normalize_identity(value) for value in snapshot.subjects["ezqcid"]
        )
        positions = {identity: index for index, identity in enumerate(source_ids)}
        requested = source_ids if navigation_ids is None else tuple(
            self._normalize_identity(value) for value in navigation_ids
        )
        if not requested:
            raise ProjectContextError("QC navigation requires at least one ezqcid")
        if any(not identity for identity in requested):
            raise ProjectContextError("QC navigation contains a blank ezqcid")
        duplicates = sorted(
            identity for identity, count in Counter(requested).items() if count > 1
        )
        if duplicates:
            raise ProjectContextError(f"QC navigation contains duplicate ezqcid: {duplicates}")
        foreign = [identity for identity in requested if identity not in positions]
        if foreign:
            raise ProjectContextError(
                f"QC navigation ezqcid is not in the current project: {foreign}"
            )
        initial = self._normalize_identity(initial_ezqcid)
        if initial not in requested:
            raise ProjectContextError(
                f"Initial ezqcid is not in the applied QC navigation queue: {initial}"
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
        return QcWorkflowService(
            module,
            ordered_subjects,
            rating_dir=rating_dir,
            constants=snapshot.constants,
            rating_service=self.rating_service,
            code_executor=self.code_executor,
            initial_ezqcid=initial,
            event_bus=self.event_bus,
            event_context={
                "context_revision": snapshot.context_revision,
                "project_name": snapshot.project_name,
                "project_path": str(snapshot.project_path),
            },
        )


__all__ = [
    "ProjectContextError",
    "ProjectContextService",
    "ProjectContextSnapshot",
    "PreparedProjectContext",
]
