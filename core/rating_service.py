from __future__ import annotations

from bisect import bisect_left
import os
import re
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any

import pandas as pd

from core.rating_identity import (
    RatingFilenameError,
    RatingIdentity,
    RatingIdentityError,
    build_rating_filename,
    canonical_rating_path,
    parse_rating_filename,
)
from core.rating_write_lock import rating_write_lock
from models.project import Project
from models.qcmodule import QCModule
from models.rating import Rating
from utils.file_utils import FileUtils


def _load_rating_file(path: Path) -> Rating:
    return Rating.from_legacy_dict(FileUtils.safe_json_load(path))


def _rating_identity(rating: Rating) -> RatingIdentity:
    return RatingIdentity(
        module_name=rating.module_name,
        rater=rating.rater,
        ezqcid=rating.ezqcid,
    )


def _validate_legacy_rating_filename(
    filename: str,
    identity: RatingIdentity,
) -> None:
    if not filename.endswith(".json"):
        raise RatingFilenameError(filename, "must end with the exact suffix '.json'")
    expected_prefix = (
        f"{identity.module_name}._.{identity.ezqcid}._.{identity.rater}._."
)
    if not filename.startswith(expected_prefix):
        raise RatingFilenameError(
            filename,
            "legacy basename does not match the complete JSON body identity prefix",
        )


class RatingServiceError(RuntimeError):
    """Base class for rating persistence failures."""


class RatingIdentityConflictError(RatingServiceError):
    """An occupied canonical path declares another identity."""

    def __init__(
        self,
        path: Path,
        requested: RatingIdentity,
        actual: RatingIdentity | None,
        reason: str,
    ) -> None:
        self.path = path
        self.requested = requested
        self.actual = actual
        self.reason = reason
        super().__init__(f"Refusing to overwrite rating {path}: {reason}")


class RatingLegacyConflictError(RatingServiceError):
    """A legacy snapshot must be explicitly migrated before a v2 save."""

    def __init__(self, identity: RatingIdentity, paths: list[Path]) -> None:
        self.identity = identity
        self.paths = tuple(paths)
        super().__init__(
            "Legacy rating requires explicit migration before canonical save: "
            + ", ".join(str(path) for path in paths)
        )


@dataclass(frozen=True)
class RatingScanIssue:
    path: Path
    code: str
    message: str


@dataclass(frozen=True)
class RatingScanRecord:
    rating: Rating
    path: Path
    identity: RatingIdentity
    filename_format: str


@dataclass
class RatingScanResult:
    records: list[RatingScanRecord]
    errors: list[RatingScanIssue]


class RatingScanError(RatingServiceError):
    """One or more rating files failed structural validation."""

    def __init__(self, result: RatingScanResult) -> None:
        self.result = result
        details = "; ".join(
            f"{issue.path}: {issue.code}: {issue.message}"
            for issue in result.errors
        )
        message = "Rating scan failed"
        if details:
            message = f"{message}: {details}"
        super().__init__(message)


@dataclass
class LoadedRatingsState:
    ratings: list[Rating]
    rating_dict: dict[str, dict[str, dict[str, Any]]]
    qctable: pd.DataFrame
    original_table: pd.DataFrame
    original_wide_table: pd.DataFrame


class RatingRaterDirectoryIndex:
    """Refreshable filename index for one interactive module/rater session.

    Canonical records are found by their deterministic path. Legacy prefix
    lookup becomes ``O(log n)`` after one directory scan. A metadata change
    rebuilds the index, so files created outside this session are not hidden.
    """

    def __init__(
        self,
        target_dir: str | os.PathLike[str],
        *,
        module_name: str,
        rater: str,
    ) -> None:
        RatingIdentity(module_name, rater, "_index_probe_")
        self.target_dir = Path(target_dir)
        self.module_name = module_name
        self.rater = rater
        self._legacy_names: tuple[str, ...] = ()
        self._legacy_paths: tuple[Path, ...] = ()
        self._signature: tuple[int, int, int, int] | None = None
        self._guard = threading.RLock()

    @classmethod
    def build(
        cls,
        target_dir: str | os.PathLike[str],
        *,
        module_name: str,
        rater: str,
    ) -> "RatingRaterDirectoryIndex":
        index = cls(
            target_dir,
            module_name=module_name,
            rater=rater,
        )
        index._rebuild()
        return index

    def _directory_signature(self) -> tuple[int, int, int, int] | None:
        try:
            metadata = self.target_dir.stat()
        except FileNotFoundError:
            return None
        if not self.target_dir.is_dir():
            raise RatingServiceError(
                f"rating rater path is not a directory: {self.target_dir}"
            )
        return (
            int(metadata.st_dev),
            int(metadata.st_ino),
            int(metadata.st_mtime_ns),
            int(metadata.st_ctime_ns),
        )

    def _scan_legacy_paths(self) -> tuple[tuple[str, ...], tuple[Path, ...]]:
        if not self.target_dir.exists():
            return (), ()
        prefix = f"{self.module_name}._."
        candidates: list[tuple[str, Path]] = []
        try:
            with os.scandir(self.target_dir) as entries:
                for entry in entries:
                    path = self.target_dir / entry.name
                    if (
                        entry.is_file()
                        and entry.name.startswith(prefix)
                        and path.suffix.casefold() == ".json"
                    ):
                        candidates.append((entry.name, path))
        except OSError as exc:
            raise RatingServiceError(
                f"cannot index rating directory {self.target_dir}: {exc}"
            ) from exc
        candidates.sort(key=lambda item: item[0])
        return (
            tuple(name for name, _ in candidates),
            tuple(path for _, path in candidates),
        )

    def _rebuild(self) -> None:
        with self._guard:
            for _attempt in range(3):
                before = self._directory_signature()
                names, paths = self._scan_legacy_paths()
                after = self._directory_signature()
                if before == after:
                    self._legacy_names = names
                    self._legacy_paths = paths
                    self._signature = after
                    return
            raise RatingServiceError(
                "rating directory changed repeatedly while its legacy index "
                f"was being built: {self.target_dir}"
            )

    def _refresh_if_changed(self) -> None:
        with self._guard:
            if self._directory_signature() != self._signature:
                self._rebuild()

    def _identity(self, ezqcid: str) -> RatingIdentity:
        return RatingIdentity(self.module_name, self.rater, ezqcid)

    def legacy_files(self, ezqcid: str) -> list[Path]:
        identity = self._identity(ezqcid)
        self._refresh_if_changed()
        prefix = (
            f"{identity.module_name}._.{identity.ezqcid}._."
            f"{identity.rater}._."
        )
        with self._guard:
            position = bisect_left(self._legacy_names, prefix)
            matches: list[Path] = []
            while (
                position < len(self._legacy_names)
                and self._legacy_names[position].startswith(prefix)
            ):
                matches.append(self._legacy_paths[position])
                position += 1
            return matches

    def find(self, ezqcid: str) -> list[Path]:
        identity = self._identity(ezqcid)
        matches = self.legacy_files(identity.ezqcid)
        canonical = self.target_dir / build_rating_filename(identity)
        if canonical.is_file():
            matches.append(canonical)
        return RatingService._validated_matching_paths(matches)

    def note_canonical_write(self) -> None:
        """Record the known metadata change after this session saves."""

        with self._guard:
            self._signature = self._directory_signature()

    def owns(self, target_dir: Path, identity: RatingIdentity) -> bool:
        return (
            Path(target_dir) == self.target_dir
            and identity.module_name == self.module_name
            and identity.rater == self.rater
        )


class RatingService:
    def __init__(self, project_or_service: Project | Any) -> None:
        self.project_or_service = project_or_service

    @property
    def project(self) -> Project:
        if isinstance(self.project_or_service, Project):
            return self.project_or_service
        current = getattr(self.project_or_service, "current_project", None)
        if current is None:
            raise ValueError("当前项目未加载")
        return current

    def scan_rating_files(self) -> list[Path]:
        rating_dir = self.project.rating_dir
        if not rating_dir.exists():
            return []
        return sorted(
            path
            for path in rating_dir.rglob("*")
            if path.is_file() and path.suffix.casefold() == ".json"
        )

    @staticmethod
    def _scan_one(
        path: Path,
        *,
        rating_root: Path | None = None,
    ) -> tuple[RatingScanRecord | None, RatingScanIssue | None]:
        if rating_root is not None:
            try:
                relative_parts = path.relative_to(rating_root).parts
            except ValueError:
                relative_parts = ()
            if len(relative_parts) != 3:
                return None, RatingScanIssue(
                    path,
                    "path_depth",
                    "rating path must be exactly module/rater/file below RatingFiles",
                )

        try:
            canonical_identity = parse_rating_filename(path.name)
        except RatingFilenameError as exc:
            canonical_identity = None
            canonical_error = exc
            if not path.name.endswith(".json"):
                return None, RatingScanIssue(path, "filename", str(exc))

        try:
            rating = _load_rating_file(path)
        except Exception as exc:
            return None, RatingScanIssue(path, "json_load", str(exc))

        try:
            body_identity = _rating_identity(rating)
        except RatingIdentityError as exc:
            return None, RatingScanIssue(path, "body_identity", str(exc))

        if canonical_identity is None:
            try:
                _validate_legacy_rating_filename(path.name, body_identity)
            except RatingFilenameError:
                return None, RatingScanIssue(
                    path,
                    "path_identity_mismatch",
                    (
                        "legacy filename does not match the complete JSON body "
                        f"identity ({canonical_error})"
                    ),
                )
            path_identity = body_identity
            filename_format = "legacy"
        else:
            path_identity = canonical_identity
            filename_format = "canonical"

        if (
            path.parent.parent.name != path_identity.module_name
            or path.parent.name != path_identity.rater
        ):
            return None, RatingScanIssue(
                path,
                "path_identity_mismatch",
                "directory identity does not match the filename identity",
            )
        if body_identity != path_identity:
            return None, RatingScanIssue(
                path,
                "path_identity_mismatch",
                "JSON body identity does not match directory and filename identity",
            )

        return (
            RatingScanRecord(
                rating=rating,
                path=path,
                identity=path_identity,
                filename_format=filename_format,
            ),
            None,
        )

    @staticmethod
    def _validated_record(
        path: Path,
        *,
        rating_root: Path | None = None,
    ) -> RatingScanRecord:
        record, issue = RatingService._scan_one(path, rating_root=rating_root)
        if issue is not None:
            raise RatingScanError(RatingScanResult(records=[], errors=[issue]))
        if record is None:
            raise AssertionError("rating validation produced neither a record nor an issue")
        return record

    def validate_rating_file(self, path: Path) -> bool:
        record, issue = self._scan_one(
            path,
            rating_root=self.project.rating_dir,
)
        return record is not None and issue is None

    def load_rating(self, path: Path) -> Rating:
        return self._validated_record(
            path,
            rating_root=self.project.rating_dir,
        ).rating

    @staticmethod
    def _legacy_files_for_identity(
        target_dir: Path,
        identity: RatingIdentity,
    ) -> list[Path]:
        prefix = (
            f"{identity.module_name}._.{identity.ezqcid}._.{identity.rater}._."
        )
        if not target_dir.exists():
            return []
        return sorted(
            path
            for path in target_dir.iterdir()
            if path.is_file()
            and path.name.startswith(prefix)
            and path.suffix.casefold() == ".json"
        )

    @staticmethod
    def find_rating_files_in_rater_dir(
        target_dir: Path,
        module_name: str,
        ezqcid: str,
        rater: str,
    ) -> list[Path]:
        index = RatingRaterDirectoryIndex.build(
            target_dir,
            module_name=module_name,
            rater=rater,
        )
        return index.find(ezqcid)

    @staticmethod
    def _validated_matching_paths(matches: list[Path]) -> list[Path]:
        records: list[RatingScanRecord] = []
        errors: list[RatingScanIssue] = []
        for path in sorted(set(matches)):
            record, issue = RatingService._scan_one(path)
            if issue is not None:
                errors.append(issue)
            elif record is None:
                raise AssertionError(
                    "rating validation produced neither a record nor an issue"
                )
            else:
                records.append(record)
        if errors:
            raise RatingScanError(RatingScanResult(records=records, errors=errors))
        return [record.path for record in records]

    @staticmethod
    def load_legacy_rating_file(path: Path) -> dict[str, Any]:
        return RatingService._validated_record(path).rating.to_legacy_dict()

    @staticmethod
    def _casefold_sibling(
        parent: Path,
        requested_name: str,
    ) -> Path | None:
        if not parent.exists():
            return None
        try:
            for candidate in parent.iterdir():
                if (
                    candidate.name != requested_name
                    and candidate.name.casefold() == requested_name.casefold()
                ):
                    return candidate
        except OSError as exc:
            raise RatingServiceError(
                f"cannot inspect rating identity siblings in {parent}: {exc}"
            ) from exc
        return None

    @staticmethod
    def _reject_casefold_write_conflicts(
        target_path: Path,
        identity: RatingIdentity,
    ) -> None:
        rating_root = target_path.parents[2]
        module_conflict = RatingService._casefold_sibling(
            rating_root,
            identity.module_name,
        )
        if module_conflict is not None:
            raise RatingIdentityConflictError(
                target_path,
                identity,
                None,
                f"case-insensitive module_name conflicts with {module_conflict}",
            )

        module_dir = rating_root / identity.module_name
        rater_conflict = RatingService._casefold_sibling(
            module_dir,
            identity.rater,
        )
        if rater_conflict is not None:
            raise RatingIdentityConflictError(
                target_path,
                identity,
                None,
                f"case-insensitive rater conflicts with {rater_conflict}",
            )

        if not target_path.parent.exists():
            return
        legacy_prefix = (
            f"{identity.module_name}._.{identity.ezqcid}._."
            f"{identity.rater}._."
        )
        try:
            entries = tuple(target_path.parent.iterdir())
        except OSError as exc:
            raise RatingServiceError(
                f"cannot inspect rating files in {target_path.parent}: {exc}"
            ) from exc
        for candidate in entries:
            if not candidate.is_file() or candidate.suffix.casefold() != ".json":
                continue
            canonical_collision = (
                candidate.name != target_path.name
                and candidate.name.casefold() == target_path.name.casefold()
            )
            legacy_collision = (
                not candidate.name.startswith(legacy_prefix)
                and candidate.name.casefold().startswith(legacy_prefix.casefold())
            )
            if canonical_collision or legacy_collision:
                raise RatingIdentityConflictError(
                    target_path,
                    identity,
                    None,
                    f"case-insensitive rating identity conflicts with {candidate}",
                )

    def save_rating(
        self,
        rating: Rating,
        legacy_module: QCModule | dict[str, Any] | None = None,
    ) -> Path:
        identity = _rating_identity(rating)
        target_path = canonical_rating_path(self.project.rating_dir, identity)
        return self._save_rating_to_path(target_path, identity, rating, legacy_module)

    @staticmethod
    def save_rating_to_rater_dir(
        target_dir: Path,
        rating: Rating,
        legacy_module: QCModule | dict[str, Any] | None = None,
        *,
        directory_index: RatingRaterDirectoryIndex | None = None,
    ) -> Path:
        identity = _rating_identity(rating)
        target_path = canonical_rating_path(target_dir.parent.parent, identity)
        if target_path.parent != target_dir:
            raise RatingIdentityConflictError(
                target_path,
                identity,
                None,
                "target directory does not match the requested module and rater",
            )
        return RatingService._save_rating_to_path(
            target_path,
            identity,
            rating,
            legacy_module,
            directory_index,
        )

    @staticmethod
    def _save_rating_to_path(
        target_path: Path,
        identity: RatingIdentity,
        rating: Rating,
        legacy_module: QCModule | dict[str, Any] | None,
        directory_index: RatingRaterDirectoryIndex | None = None,
    ) -> Path:
        if legacy_module is None and rating.legacy_payload is None:
            raise ValueError("保存评分 JSON 需要完整 legacy qcmodule payload")

        payload = rating.to_legacy_dict(legacy_module)
        payload["schema_version"] = 2

        with rating_write_lock(target_path):
            RatingService._reject_casefold_write_conflicts(target_path, identity)
            if directory_index is None:
                legacy_files = RatingService._legacy_files_for_identity(
                    target_path.parent,
                    identity,
                )
            else:
                if not directory_index.owns(target_path.parent, identity):
                    raise RatingIdentityConflictError(
                        target_path,
                        identity,
                        None,
                        "rating directory index does not own the target identity",
                    )
                legacy_files = directory_index.legacy_files(identity.ezqcid)
            if legacy_files:
                raise RatingLegacyConflictError(identity, legacy_files)

            if target_path.exists():
                try:
                    actual = _rating_identity(_load_rating_file(target_path))
                except Exception as exc:
                    raise RatingIdentityConflictError(
                        target_path,
                        identity,
                        None,
                        f"existing target cannot be validated: {exc}",
                    ) from exc
                if actual != identity:
                    raise RatingIdentityConflictError(
                        target_path,
                        identity,
                        actual,
                        "existing JSON body declares another identity",
                    )

            target_path.parent.mkdir(parents=True, exist_ok=True)
            FileUtils.safe_json_save(target_path, payload)
            if directory_index is not None:
                directory_index.note_canonical_write()
        return target_path

    def scan_rating_records(self) -> RatingScanResult:
        records: list[RatingScanRecord] = []
        errors: list[RatingScanIssue] = []
        by_identity: dict[RatingIdentity, list[RatingScanRecord]] = {}
        module_casefold: dict[str, RatingScanRecord] = {}
        rater_casefold: dict[tuple[str, str], RatingScanRecord] = {}
        ezqcid_casefold: dict[str, RatingScanRecord] = {}

        for path in self.scan_rating_files():
            record, issue = self._scan_one(
                path,
                rating_root=self.project.rating_dir,
)
            if issue is not None:
                errors.append(issue)
                continue
            if record is None:
                raise AssertionError("rating scan produced neither a record nor an issue")
            records.append(record)
            by_identity.setdefault(record.identity, []).append(record)

            identity = record.identity
            collision_fields: list[str] = []
            collision_paths: set[Path] = set()

            module_key = identity.module_name.casefold()
            prior_module = module_casefold.setdefault(module_key, record)
            if prior_module.identity.module_name != identity.module_name:
                collision_fields.append("module_name")
                collision_paths.add(prior_module.path)

            rater_key = (module_key, identity.rater.casefold())
            prior_rater = rater_casefold.setdefault(rater_key, record)
            if prior_rater.identity.rater != identity.rater:
                collision_fields.append("rater")
                collision_paths.add(prior_rater.path)

            ezqcid_key = identity.ezqcid.casefold()
            prior_ezqcid = ezqcid_casefold.setdefault(ezqcid_key, record)
            if prior_ezqcid.identity.ezqcid != identity.ezqcid:
                collision_fields.append("ezqcid")
                collision_paths.add(prior_ezqcid.path)

            if collision_fields:
                all_paths = sorted({record.path, *collision_paths})
                errors.append(
                    RatingScanIssue(
                        record.path,
                        "casefold_identity_collision",
                        (
                            "case-insensitive identity collision in "
                            f"{', '.join(collision_fields)}: "
                            + ", ".join(str(item) for item in all_paths)
                        ),
                    )
                )

        for identity, duplicate_records in by_identity.items():
            if len(duplicate_records) > 1:
                errors.append(
                    RatingScanIssue(
                        duplicate_records[1].path,
                        "duplicate_identity",
                        (
                            "duplicate rating identity "
                            f"{identity.module_name}/{identity.rater}/{identity.ezqcid}: "
                            + ", ".join(str(item.path) for item in duplicate_records)
                        ),
                    )
                )

        return RatingScanResult(records=records, errors=errors)

    def load_all_ratings(self) -> list[Rating]:
        return [rating for rating, _ in self.load_all_rating_records()]

    def load_all_rating_records(self) -> list[tuple[Rating, Path]]:
        result = self.scan_rating_records()
        if result.errors:
            raise RatingScanError(result)
        return [(record.rating, record.path) for record in result.records]

    def load_legacy_state(self, subjects: pd.DataFrame) -> LoadedRatingsState:
        """Load ratings in the shape expected by the legacy GUI state."""
        records = self.load_all_rating_records()
        ratings = [rating for rating, _ in records]
        original_table = self.rating_records_to_long_dataframe(records)
        original_wide_table = self.long_table_to_wide(original_table)
        return LoadedRatingsState(
            ratings=ratings,
            rating_dict=self.build_rating_dict(ratings),
            qctable=self.merge_subjects_with_rating_wide(original_wide_table, subjects),
            original_table=original_table,
            original_wide_table=original_wide_table,
        )

    def build_rating_dict(self, ratings: list[Rating]) -> dict[str, dict[str, dict[str, Any]]]:
        rating_dict: dict[str, dict[str, dict[str, Any]]] = {}
        for rating in ratings:
            rating_dict.setdefault(rating.ezqcid, {})
            rating_dict[rating.ezqcid][f"{rating.module_name}-{rating.rater}"] = rating.to_legacy_dict()
        return rating_dict

    def aggregate_to_wide(self, ratings: list[Rating], subjects: pd.DataFrame) -> pd.DataFrame:
        original_table = self.rating_records_to_long_dataframe([(rating, None) for rating in ratings])
        original_wide_table = self.long_table_to_wide(original_table)
        return self.merge_subjects_with_rating_wide(original_wide_table, subjects)

    def rating_records_to_long_dataframe(self, records: list[tuple[Rating, Path | None]]) -> pd.DataFrame:
        if not records:
            return pd.DataFrame()
        return pd.DataFrame.from_records(
            [self._rating_to_flat_record(rating, path) for rating, path in records]
        )

    def long_table_to_wide(self, long_df: pd.DataFrame) -> pd.DataFrame:
        if long_df.empty:
            return pd.DataFrame()

        dup_keys = ["ezqcid", "module_name", "rater"]
        duplicates = long_df[long_df.duplicated(subset=dup_keys, keep=False)]
        if not duplicates.empty:
            offenders = (
                duplicates[dup_keys].drop_duplicates()
                .astype(str)
                .agg("/".join, axis=1)
                .tolist()
            )
            raise ValueError(
                f"重复的评分身份(ezqcid/module/rater),F-RAT-3 不变量被破坏: {offenders[:5]}"
            )

        wide = long_df.pivot_table(index="ezqcid", columns=["module_name", "rater"], aggfunc="first").reset_index()
        new_columns = []
        for col in wide.columns:
            if col == "ezqcid" or (isinstance(col, tuple) and col[0] == "ezqcid"):
                new_columns.append("ezqcid")
            else:
                new_columns.append(f"{col[1]}.{col[2]}.{col[0]}")
        wide.columns = new_columns
        wide["ezqcid"] = wide["ezqcid"].astype(str)
        return wide

    def merge_subjects_with_rating_wide(self, rating_wide: pd.DataFrame, subjects: pd.DataFrame) -> pd.DataFrame:
        if rating_wide.empty:
            return subjects.copy()

        subjects = subjects.copy()
        if "ezqcid" in subjects.columns:
            subjects["ezqcid"] = subjects["ezqcid"].astype(str)
        if "ezqcid" in rating_wide.columns:
            rating_wide = rating_wide.copy()
            rating_wide["ezqcid"] = rating_wide["ezqcid"].astype(str)

        result = pd.merge(subjects, rating_wide, on="ezqcid", how="left")
        result = result.drop(columns=[col for col in result.columns if ".code" in col or ".code_exe" in col])

        score_cols = [col for col in result.columns if re.search(r"\.score\d+$", col) and col != "ezqcid"]
        tag_cols = [col for col in result.columns if re.search(r"\.tag\d+$", col) and col != "ezqcid"]
        notes_cols = [col for col in result.columns if re.search(r"\.notes\d+$", col) and col != "ezqcid"]
        other_cols = [
            col
            for col in result.columns
            if col != "ezqcid"
            and not re.search(r"\.score\d+$", col)
            and not re.search(r"\.tag\d+$", col)
            and not re.search(r"\.notes\d+$", col)
        ]

        return result[["ezqcid"] + score_cols + tag_cols + notes_cols + other_cols]

    def _rating_to_flat_record(self, rating: Rating, path: Path | None = None) -> dict[str, Any]:
        """Flatten one rating without allocating an intermediate DataFrame."""
        data = rating.to_legacy_dict()
        flattened: dict[str, Any] = {}

        for key, value in data.items():
            if key not in ["scores", "tags", "code_exe"]:
                flattened[key] = value

        if isinstance(data.get("code_exe"), dict):
            flattened["code_exe"] = ""
            for code_value in data["code_exe"].values():
                if isinstance(code_value, dict):
                    for sub_value in code_value.values():
                        flattened["code_exe"] = f"{flattened['code_exe']}; {sub_value};"
                else:
                    flattened["code_exe"] = code_value

        for score_key, score_value in data.get("scores", {}).items():
            if isinstance(score_value, dict):
                for sub_key, sub_value in score_value.items():
                    suffix = "" if sub_key == "value" else sub_key
                    flattened[f"score{score_key}{suffix}"] = sub_value
            else:
                flattened[f"score{score_key}"] = score_value

        for tag_key, tag_value in data.get("tags", {}).items():
            if isinstance(tag_value, dict):
                for sub_key, sub_value in tag_value.items():
                    suffix = "" if sub_key == "value" else sub_key
                    flattened[f"tag{tag_key}{suffix}"] = sub_value
            else:
                flattened[f"tag{tag_key}"] = tag_value

        if path is not None:
            flattened["filename"] = path.name
            flattened["filepath"] = str(path)

        flattened["module_name"] = flattened.pop("name")
        return flattened

    def rating_to_flat_dataframe(
        self,
        rating: Rating,
        path: Path | None = None,
    ) -> pd.DataFrame:
        return pd.DataFrame([self._rating_to_flat_record(rating, path)])


__all__ = [
    "LoadedRatingsState",
    "RatingIdentityConflictError",
    "RatingLegacyConflictError",
    "RatingRaterDirectoryIndex",
    "RatingScanError",
    "RatingScanIssue",
    "RatingScanRecord",
    "RatingScanResult",
    "RatingService",
    "RatingServiceError",
]
