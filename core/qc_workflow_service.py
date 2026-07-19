"""Toolkit-neutral interactive QC session and transaction boundary."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from core.code_executor import CodeExecutor
from core.event_bus import Event, EventBus, EventType
from core.rating_service import RatingService
from models.qcmodule import QCModule
from models.rating import Rating


class QcSessionError(RuntimeError):
    """Raised when an interactive QC session cannot complete an action."""


class QcIdentityError(QcSessionError):
    """Raised when the subject sequence has unsafe identities."""


class QcReadOnlyError(QcSessionError):
    """Raised when a mutation is attempted in enforced watch mode."""


@dataclass(frozen=True)
class ViewerPlan:
    rendered_template: str
    commands: dict[int, str]
    control: bool


class QcWorkflowService:
    """Own one copied QC draft and coordinate existing Core services safely."""

    def __init__(
        self,
        module: QCModule | dict[str, Any],
        subjects: pd.DataFrame,
        *,
        rating_dir: str | Path | None,
        constants: Mapping[str, Any] | None = None,
        rating_service: RatingService | None = None,
        code_executor: CodeExecutor | Any | None = None,
        initial_ezqcid: str | None = None,
        event_bus: EventBus | None = None,
        event_context: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(subjects, pd.DataFrame):
            raise TypeError("QC subjects must be a pandas DataFrame")
        self._module_template = (
            module.to_legacy_dict() if isinstance(module, QCModule) else deepcopy(module)
        )
        self._source = subjects.copy(deep=True)
        self._subject_ids = self._validated_subject_ids(self._source)
        self._constants = dict(constants or {})
        self._rating_dir = Path(rating_dir) if rating_dir is not None else None
        self._rating_store = rating_service or RatingService
        self._code_executor = code_executor or CodeExecutor()
        self._event_bus = event_bus
        self._event_context = dict(event_context or {})
        self._session_read_only_reasons: list[str] = []
        self._case_read_only_reasons: list[str] = []
        self._dirty = False
        self._closed = False

        configured_module = QCModule.from_legacy_dict(deepcopy(self._module_template))
        rater = self._normalize_identity(configured_module.rater)
        if configured_module.watch_mode:
            self._force_session_read_only("Module is configured for watch mode")
        if not rater:
            self._force_session_read_only("Rater is not configured")
        if not self._subject_ids:
            raise QcIdentityError("QC requires at least one subject")
        if initial_ezqcid is None:
            self._current_index = 0
        else:
            normalized = self._normalize_identity(initial_ezqcid)
            try:
                self._current_index = self._subject_ids.index(normalized)
            except ValueError as exc:
                raise QcIdentityError(f"Initial ezqcid is not in the QC table: {normalized}") from exc
        self._working_module = configured_module
        self._load_current_rating()

    @staticmethod
    def _normalize_identity(value: Any) -> str:
        if value is None or pd.isna(value):
            return ""
        return str(value).strip()

    @classmethod
    def _validated_subject_ids(cls, source: pd.DataFrame) -> tuple[str, ...]:
        if "ezqcid" not in source.columns:
            raise QcIdentityError("QC subject table is missing ezqcid")
        identities = tuple(cls._normalize_identity(value) for value in source["ezqcid"])
        if any(not identity for identity in identities):
            raise QcIdentityError("QC subject table contains a blank ezqcid")
        duplicates = sorted(
            identity for identity, count in Counter(identities).items() if count > 1
        )
        if duplicates:
            raise QcIdentityError(f"QC subject table contains duplicate ezqcid: {duplicates}")
        return identities

    @property
    def subject_ids(self) -> tuple[str, ...]:
        return self._subject_ids

    @property
    def current_index(self) -> int:
        return self._current_index

    @property
    def current_ezqcid(self) -> str:
        return self._subject_ids[self._current_index]

    @property
    def current_module(self) -> QCModule:
        return deepcopy(self._working_module)

    @property
    def watch_mode(self) -> bool:
        return bool(self._session_read_only_reasons or self._case_read_only_reasons)

    @property
    def read_only_reason(self) -> str:
        reasons = dict.fromkeys(
            (*self._session_read_only_reasons, *self._case_read_only_reasons)
        )
        return "; ".join(reasons)

    @property
    def dirty(self) -> bool:
        return self._dirty

    def _force_session_read_only(self, reason: str) -> None:
        if reason not in self._session_read_only_reasons:
            self._session_read_only_reasons.append(reason)

    def _require_writable(self) -> None:
        self._require_active()
        if self.watch_mode:
            raise QcReadOnlyError(f"QC session is read-only: {self.read_only_reason}")

    def _require_active(self) -> None:
        if self._closed:
            raise QcSessionError("QC session is closed")

    def _fresh_working_module(self) -> QCModule:
        module = QCModule.from_legacy_dict(deepcopy(self._module_template))
        module.ezqcid = self.current_ezqcid
        module.time = None
        module.notes = None
        module.code_exe = None
        for score in module.scores.values():
            score.value = None
        for tag in module.tags.values():
            tag.value = False
        return module

    def _load_current_rating(self) -> None:
        module = self._fresh_working_module()
        case_reasons: list[str] = []
        rater = self._normalize_identity(module.rater)
        if not rater or self._rating_dir is None:
            files = []
        else:
            files = self._rating_store.find_rating_files_in_rater_dir(
                self._rating_dir,
                module.name,
                self.current_ezqcid,
                rater,
            )

        if files:
            if len(files) > 1:
                case_reasons.append(
                    f"Multiple rating files exist for {self.current_ezqcid}"
                )
            rating = self._rating_store.load_legacy_rating_file(files[0])
            issues = self._compatibility_issues(rating, module)
            if issues:
                case_reasons.append(
                    "Rating schema differs from the current module: "
                    + ", ".join(issues)
                )
            Rating.from_legacy_dict(rating).apply_to_module(module)

        self._working_module = module
        self._case_read_only_reasons = case_reasons
        self._dirty = False

    def _compatibility_issues(
        self,
        rating_payload: dict[str, Any],
        module: QCModule,
    ) -> list[str]:
        issues: list[str] = []
        saved_scores = rating_payload.get("scores", {}) or {}
        for key, score in module.scores.items():
            saved = saved_scores.get(key)
            if not isinstance(saved, dict):
                issues.append(f"score {key} missing")
                continue
            current_values = tuple(score.allowed_values)
            saved_raw = saved.get("num_", "")
            saved_values = tuple(
                str(value).strip()
                for value in (saved_raw if isinstance(saved_raw, list) else str(saved_raw).split(","))
                if str(value).strip()
            )
            if current_values != saved_values:
                issues.append(f"score {key} schema")
        saved_tags = rating_payload.get("tags", {}) or {}
        for key, tag in module.tags.items():
            saved = saved_tags.get(key)
            if not isinstance(saved, dict) or saved.get("label") != tag.label:
                issues.append(f"tag {key} schema")
        return issues

    def set_score(self, key: str, value: str | None) -> None:
        self._require_writable()
        if key not in self._working_module.scores:
            raise QcSessionError(f"Unknown score key: {key}")
        score = self._working_module.scores[key]
        if value is not None and str(value) not in score.allowed_values:
            raise QcSessionError(
                f"Invalid value for {score.label}: {value!r}; allowed={score.allowed_values}"
            )
        normalized = None if value is None else str(value)
        if score.value != normalized:
            score.value = normalized
            self._dirty = True

    def set_tag(self, key: str, value: bool) -> None:
        self._require_writable()
        if key not in self._working_module.tags:
            raise QcSessionError(f"Unknown tag key: {key}")
        normalized = bool(value)
        if self._working_module.tags[key].value != normalized:
            self._working_module.tags[key].value = normalized
            self._dirty = True

    def set_notes(self, notes: str) -> None:
        self._require_writable()
        normalized = str(notes)
        if (self._working_module.notes or "") != normalized:
            self._working_module.notes = normalized
            self._dirty = True

    def viewer_plan(self) -> ViewerPlan:
        template = (self._working_module.code or "").strip()
        if not template:
            raise QcSessionError("The QC module has no viewer command template")
        row = self._source.iloc[self._current_index]
        if self._normalize_identity(row["ezqcid"]) != self.current_ezqcid:
            raise QcIdentityError("Current ezqcid no longer resolves to its stable source row")
        variables = {**row.to_dict(), **self._constants}
        rendered, commands = self._code_executor.render_command_plan(template, variables)
        if not commands:
            raise QcSessionError("Viewer template produced no commands")
        self._working_module.code_exe = {str(key): command for key, command in commands.items()}
        return ViewerPlan(
            rendered_template=rendered,
            commands=dict(commands),
            control=bool(self._working_module.control),
        )

    def launch_viewer(self) -> list[Any]:
        self._require_active()
        plan = self.viewer_plan()
        try:
            return list(self._code_executor.start_commands(
                plan.commands,
                control=plan.control,
            ))
        except Exception:
            self._code_executor.close_current_processes()
            raise

    def save(self) -> Path:
        self._require_writable()
        if self._rating_dir is None:
            raise QcSessionError("Rating directory is not configured")
        rater = self._normalize_identity(self._working_module.rater)
        if not rater:
            raise QcReadOnlyError("QC session is read-only: rater is not configured")
        previous_time = self._working_module.time
        self._working_module.time = datetime.now()
        rating = Rating.from_module(self._working_module)
        try:
            path = self._rating_store.save_rating_to_rater_dir(
                self._rating_dir,
                rating,
                self._module_template,
            )
        except Exception:
            self._working_module.time = previous_time
            raise
        self._dirty = False
        if self._event_bus is not None:
            data = {
                **self._event_context,
                "module_name": self._working_module.name,
                "rater": rater,
                "ezqcid": self.current_ezqcid,
                "path": str(path),
            }
            self._event_bus.emit(
                Event(type=EventType.RATING_SAVED, source="QcWorkflowService", data=data)
            )
        return path

    def save_and_move(self, delta: int) -> Path:
        path = self.save()
        self.move(delta)
        return path

    def move(self, delta: int, *, discard_changes: bool = False) -> bool:
        target = self._current_index + int(delta)
        if target < 0 or target >= len(self._subject_ids):
            return False
        return self.navigate_to(target, discard_changes=discard_changes)

    def navigate_to(
        self,
        target: int | str,
        *,
        discard_changes: bool = False,
    ) -> bool:
        self._require_active()
        if isinstance(target, str):
            normalized = self._normalize_identity(target)
            try:
                target_index = self._subject_ids.index(normalized)
            except ValueError as exc:
                raise QcIdentityError(f"Unknown QC ezqcid: {normalized}") from exc
        else:
            target_index = int(target)
            if target_index < 0 or target_index >= len(self._subject_ids):
                raise QcIdentityError(f"QC subject index is outside the session: {target_index}")
        if target_index == self._current_index:
            return False
        if self._dirty and not discard_changes:
            raise QcSessionError("Cannot navigate with unsaved QC draft")

        previous_index = self._current_index
        previous_module = self._working_module
        previous_dirty = self._dirty
        previous_case_reasons = self._case_read_only_reasons
        self._code_executor.close_current_processes()
        self._current_index = target_index
        try:
            self._load_current_rating()
        except Exception:
            self._current_index = previous_index
            self._working_module = previous_module
            self._dirty = previous_dirty
            self._case_read_only_reasons = previous_case_reasons
            raise
        return True

    def discard_changes(self) -> bool:
        """Reload the current persisted state without changing case identity."""

        self._require_active()
        if not self._dirty:
            return False
        self._load_current_rating()
        return True

    def close(self) -> None:
        if self._closed:
            return
        self._code_executor.close_current_processes()
        self._closed = True


__all__ = [
    "QcIdentityError",
    "QcReadOnlyError",
    "QcSessionError",
    "QcWorkflowService",
    "ViewerPlan",
]
