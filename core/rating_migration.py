"""Prepare a verified, inactive canonical candidate from legacy ratings.

Planning is read-only. Applying creates one sibling candidate whose rating
records are canonical-only. Active rating JSON and legacy sources are never
renamed, overwritten, or deleted; the existing writer-lock protocol may create
its persistent lock file. Candidate activation/archive remains a separately
authorized operation.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
from typing import Any, Iterable

from core.rating_identity import (
    PORTABLE_FILENAME_MAX_UTF8_BYTES,
    PORTABLE_PATH_MAX_UTF16_UNITS,
    RatingFilenameError,
    RatingIdentity,
    RatingIdentityError,
    RatingPathError,
    canonical_rating_path,
    parse_rating_filename,
)
from core.rating_write_lock import RatingWriteLockError, rating_write_lock
from models.rating import Rating


_EXPECTED_ROOT_NAME = "RatingFiles"
_MANIFEST_FORMAT = "easyqc-rating-tree-manifest"
_MANIFEST_FORMAT_VERSION = 1
_CANDIDATE_PREFIX = ".q-"
_CANDIDATE_TEMPLATE = ".q-00000000"
_COMPLETE_MARKER = ".easyqc-migration-complete"
_RATING_WRITE_LOCK_NAME = ".easyqc-rating-write.lock"
_ATOMIC_TEMP_TEMPLATE = ".qt-00000000.tmp"


class RatingMigrationError(RuntimeError):
    """Base class for migration candidate failures."""


class MigrationConflictError(RatingMigrationError):
    """The supplied plan contains unresolved conflicts."""

    def __init__(self, plan: "MigrationPlan") -> None:
        self.plan = plan
        codes = ", ".join(sorted({issue.code for issue in plan.issues}))
        super().__init__(f"rating migration plan has conflicts: {codes}")


class MigrationPlanStaleError(RatingMigrationError):
    """The active rating tree no longer matches the reviewed plan."""

    def __init__(
        self,
        planned: "MigrationPlan",
        current: "MigrationPlan",
    ) -> None:
        self.planned = planned
        self.current = current
        super().__init__(
            "rating migration plan is stale; run a new dry-run before applying"
        )


class MigrationApplyError(RatingMigrationError):
    """Candidate construction or cleanup failed without activation."""

    def __init__(
        self,
        message: str,
        *,
        cause: BaseException | None = None,
        candidate_root: Path | None = None,
    ) -> None:
        self.cause = cause
        self.candidate_root = candidate_root
        suffix = ""
        if candidate_root is not None and os.path.lexists(candidate_root):
            suffix = f"; incomplete candidate requires inspection: {candidate_root}"
        super().__init__(message + suffix)


@dataclass(frozen=True, slots=True)
class MigrationIssue:
    code: str
    message: str
    paths: tuple[Path, ...]
    identity: RatingIdentity | None = None

    def to_dict(self, rating_root: Path) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "paths": [_display_path(path, rating_root) for path in self.paths],
            "identity": (
                _identity_dict(self.identity)
                if self.identity is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class MigrationSourceSnapshot:
    """Lightweight validated source; no rating payload is retained."""

    path: Path
    identity: RatingIdentity
    filename_format: str
    sha256: str


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    source: Path
    canonical_relative_path: Path
    identity: RatingIdentity
    source_sha256: str

    def to_dict(self, rating_root: Path) -> dict[str, Any]:
        return {
            "source": _display_path(self.source, rating_root),
            "candidate_relative_path": self.canonical_relative_path.as_posix(),
            "identity": _identity_dict(self.identity),
            "source_sha256": self.source_sha256,
        }


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    rating_root: Path
    scanned_file_count: int
    legacy_source_count: int
    canonical_record_count: int
    entries: tuple[MigrationEntry, ...]
    issues: tuple[MigrationIssue, ...]
    source_snapshots: tuple[MigrationSourceSnapshot, ...]
    expected_identities: tuple[RatingIdentity, ...]
    source_manifest_sha256: str

    @property
    def has_conflicts(self) -> bool:
        return bool(self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rating_root": str(self.rating_root),
            "conflict_free": not self.has_conflicts,
            "manifest_format_version": _MANIFEST_FORMAT_VERSION,
            "source_manifest_sha256": self.source_manifest_sha256,
            "summary": {
                "scanned_files": self.scanned_file_count,
                "legacy_sources": self.legacy_source_count,
                "canonical_records": self.canonical_record_count,
                "planned_conversions": len(self.entries),
                "expected_candidate_records": len(self.expected_identities),
                "conflicts": len(self.issues),
            },
            "entries": [
                entry.to_dict(self.rating_root) for entry in self.entries
            ],
            "issues": [
                issue.to_dict(self.rating_root) for issue in self.issues
            ],
            "apply_semantics": {
                "active_rating_json_modified": False,
                "writer_lock_file_may_be_created": True,
                "candidate_only": True,
                "activation_requires_separate_authorization": True,
            },
        }


@dataclass(frozen=True, slots=True)
class MigrationReport:
    plan: MigrationPlan
    candidate_root: Path
    canonical_files: tuple[Path, ...]
    retained_source_root: Path
    candidate_manifest_sha256: str
    applied: bool = True
    activated: bool = False

    @property
    def applied_count(self) -> int:
        return len(self.canonical_files)

    @property
    def retained_sources(self) -> tuple[Path, ...]:
        return tuple(entry.source for entry in self.plan.entries)

    def to_dict(self) -> dict[str, Any]:
        result = self.plan.to_dict()
        result.update(
            {
                "applied": self.applied,
                "activated": self.activated,
                "applied_count": self.applied_count,
                "candidate_root": str(self.candidate_root),
                "candidate_manifest_sha256": self.candidate_manifest_sha256,
                "canonical_files": [
                    path.relative_to(self.candidate_root).as_posix()
                    for path in self.canonical_files
                ],
                "retained_source_root": str(self.retained_source_root),
                "next_action": (
                    "Candidate verified; a separately authorized activation "
                    "must recheck both manifest digests under the writer lock."
                ),
            }
        )
        return result


def _absolute_path(path: str | os.PathLike[str]) -> Path:
    try:
        return Path(os.path.abspath(os.fspath(path)))
    except (TypeError, ValueError) as exc:
        raise TypeError("rating_root must be a valid path") from exc


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _identity_dict(identity: RatingIdentity) -> dict[str, str]:
    return {
        "module_name": identity.module_name,
        "rater": identity.rater,
        "ezqcid": identity.ezqcid,
    }


def _deterministic_json_bytes(value: Any) -> bytes:
    """Encode JSON deterministically without collapsing scalar types."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sorted_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(sorted(paths, key=str))


def _manifest_digest(
    root: Path,
    path_hashes: Iterable[tuple[Path, str]],
) -> str:
    digest = hashlib.sha256()
    header = _deterministic_json_bytes(
        {
            "format": _MANIFEST_FORMAT,
            "version": _MANIFEST_FORMAT_VERSION,
        }
    )
    digest.update(len(header).to_bytes(8, "big"))
    digest.update(header)
    items = sorted(
        (
            (path.relative_to(root).as_posix(), sha256)
            for path, sha256 in path_hashes
        ),
        key=lambda item: item[0],
    )
    for relative_path, sha256 in items:
        encoded = _deterministic_json_bytes([relative_path, sha256])
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _source_manifest(
    root: Path,
    snapshots: Iterable[MigrationSourceSnapshot],
) -> str:
    return _manifest_digest(
        root,
        ((snapshot.path, snapshot.sha256) for snapshot in snapshots),
    )


def _validate_portable_path(path: Path) -> None:
    component_bytes = len(path.name.encode("utf-8"))
    if component_bytes > PORTABLE_FILENAME_MAX_UTF8_BYTES:
        raise RatingPathError(
            path,
            (
                f"filename UTF-8 size is {component_bytes}, exceeding "
                f"{PORTABLE_FILENAME_MAX_UTF8_BYTES}"
            ),
        )
    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        units = len(str(absolute).encode("utf-16-le")) // 2
    except UnicodeEncodeError as exc:
        raise RatingPathError(path, "path contains an invalid Unicode surrogate") from exc
    if units > PORTABLE_PATH_MAX_UTF16_UNITS:
        raise RatingPathError(
            path,
            (
                f"absolute path UTF-16 units are {units}, exceeding "
                f"{PORTABLE_PATH_MAX_UTF16_UNITS}"
            ),
        )


def _prospective_candidate_root(rating_root: Path) -> Path:
    return rating_root.parent / _CANDIDATE_TEMPLATE


def _validate_candidate_auxiliary_paths(candidate_root: Path) -> None:
    _validate_portable_path(candidate_root)
    _validate_portable_path(candidate_root / _COMPLETE_MARKER)
    _validate_portable_path(candidate_root / _ATOMIC_TEMP_TEMPLATE)


def _validate_candidate_rating_path(
    candidate_root: Path,
    identity: RatingIdentity,
) -> Path:
    target = canonical_rating_path(candidate_root, identity)
    _validate_portable_path(target.parent / _ATOMIC_TEMP_TEMPLATE)
    return target


def _walk_rating_tree(
    root: Path,
) -> tuple[list[Path], list[MigrationIssue]]:
    json_paths: list[Path] = []
    issues: list[MigrationIssue] = []

    def on_error(error: OSError) -> None:
        path = Path(error.filename) if error.filename else root
        issues.append(
            MigrationIssue(
                "tree_scan_error",
                f"cannot scan rating tree: {error}",
                (path,),
            )
        )

    for directory, directory_names, filenames in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=on_error,
    ):
        directory_path = Path(directory)
        for name in list(directory_names):
            path = directory_path / name
            if path.is_symlink():
                issues.append(
                    MigrationIssue(
                        "source_symlink",
                        "rating migration does not follow symbolic links",
                        (path,),
                    )
                )
                directory_names.remove(name)
        for name in filenames:
            path = directory_path / name
            if path.is_symlink():
                issues.append(
                    MigrationIssue(
                        "source_symlink",
                        "rating migration does not follow symbolic links",
                        (path,),
                    )
                )
                continue
            if not path.is_file():
                issues.append(
                    MigrationIssue(
                        "source_special_file",
                        "candidate construction accepts regular files only",
                        (path,),
                    )
                )
                continue
            if path.suffix.casefold() == ".json":
                json_paths.append(path)
                continue
            if path == root / _RATING_WRITE_LOCK_NAME:
                continue
            issues.append(
                MigrationIssue(
                    "source_non_json_file",
                    (
                        "rating tree contains an ordinary non-JSON file; "
                        "candidate copying refuses untracked content"
                    ),
                    (path,),
                )
            )
    return sorted(json_paths, key=str), issues


def _snapshot_one(
    root: Path,
    path: Path,
) -> tuple[MigrationSourceSnapshot | None, MigrationIssue | None]:
    """Read/hash once, derive legacy identity from the body, then release it."""

    try:
        relative = path.relative_to(root)
    except ValueError:
        return None, MigrationIssue(
            "source_outside_root",
            "rating path is outside the supplied root",
            (path,),
        )
    if len(relative.parts) != 3:
        return None, MigrationIssue(
            "source_depth",
            "rating JSON must be exactly module/rater/file below RatingFiles",
            (path,),
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, MigrationIssue(
            "source_read",
            f"cannot read rating source: {exc}",
            (path,),
        )
    digest = _sha256_bytes(raw)
    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("rating JSON root must be an object")
        rating = Rating.from_legacy_dict(payload)
    except Exception as exc:
        return None, MigrationIssue(
            "invalid_source",
            f"cannot load complete rating payload: {exc}",
            (path,),
        )
    try:
        body_identity = RatingIdentity(
            module_name=rating.module_name,
            rater=rating.rater,
            ezqcid=rating.ezqcid,
        )
    except RatingIdentityError as exc:
        return None, MigrationIssue(
            "invalid_identifier",
            str(exc),
            (path,),
        )
    finally:
        del payload
        del rating

    module_dir, rater_dir, filename = relative.parts
    if module_dir != body_identity.module_name or rater_dir != body_identity.rater:
        return None, MigrationIssue(
            "source_identity_mismatch",
            "directory identity does not match JSON body identity",
            (path,),
            body_identity,
        )

    try:
        filename_identity = parse_rating_filename(filename)
    except RatingFilenameError:
        if not filename.endswith(".json"):
            return None, MigrationIssue(
                "invalid_source",
                "rating filename must end with exact suffix '.json'",
                (path,),
                body_identity,
            )
        legacy_stem = (
            f"{body_identity.module_name}._.{body_identity.ezqcid}"
            f"._.{body_identity.rater}"
        )
        observed_stem = filename[:-5]
        if not (
            observed_stem == legacy_stem
            or observed_stem.startswith(legacy_stem + "._.")
        ):
            return None, MigrationIssue(
                "source_identity_mismatch",
                (
                    "legacy filename cannot be reconciled with the validated "
                    "JSON body identity"
                ),
                (path,),
                body_identity,
            )
        filename_identity = body_identity
        filename_format = "legacy"
    else:
        filename_format = "canonical"
    if filename_identity != body_identity:
        return None, MigrationIssue(
            "source_identity_mismatch",
            "filename identity does not match JSON body identity",
            (path,),
            body_identity,
        )
    return (
        MigrationSourceSnapshot(
            path=path,
            identity=body_identity,
            filename_format=filename_format,
            sha256=digest,
        ),
        None,
    )


def _casefold_issues(
    snapshots: list[MigrationSourceSnapshot],
) -> list[MigrationIssue]:
    module_groups: dict[str, dict[str, list[MigrationSourceSnapshot]]] = {}
    rater_groups: dict[
        tuple[str, str],
        dict[str, list[MigrationSourceSnapshot]],
    ] = {}
    ezqcid_groups: dict[str, dict[str, list[MigrationSourceSnapshot]]] = {}
    for snapshot in snapshots:
        identity = snapshot.identity
        module_groups.setdefault(identity.module_name.casefold(), {}).setdefault(
            identity.module_name, []
        ).append(snapshot)
        rater_groups.setdefault(
            (identity.module_name.casefold(), identity.rater.casefold()), {}
        ).setdefault(identity.rater, []).append(snapshot)
        ezqcid_groups.setdefault(identity.ezqcid.casefold(), {}).setdefault(
            identity.ezqcid, []
        ).append(snapshot)
    issues: list[MigrationIssue] = []
    issues.extend(_field_casefold_issues("module_name", module_groups.values()))
    issues.extend(_field_casefold_issues("rater", rater_groups.values()))
    issues.extend(_field_casefold_issues("ezqcid", ezqcid_groups.values()))
    return issues


def _field_casefold_issues(
    field: str,
    groups: Iterable[dict[str, list[MigrationSourceSnapshot]]],
) -> list[MigrationIssue]:
    issues: list[MigrationIssue] = []
    for exact_values in groups:
        if len(exact_values) < 2:
            continue
        snapshots = [
            snapshot
            for grouped in exact_values.values()
            for snapshot in grouped
        ]
        issues.append(
            MigrationIssue(
                "casefold_collision",
                (
                    f"{field} values collide under case-insensitive "
                    f"comparison: {sorted(exact_values)}"
                ),
                _sorted_paths(snapshot.path for snapshot in snapshots),
            )
        )
    return issues


def _candidate_sibling_issues(root: Path) -> list[MigrationIssue]:
    try:
        siblings = list(root.parent.iterdir())
    except OSError as exc:
        return [
            MigrationIssue(
                "candidate_parent_scan",
                f"cannot inspect candidate output directory: {exc}",
                (root.parent,),
            )
        ]
    issues: list[MigrationIssue] = []
    for sibling in sorted(siblings, key=str):
        if not sibling.name.startswith(_CANDIDATE_PREFIX):
            continue
        if (sibling / _COMPLETE_MARKER).is_file():
            code = "candidate_tree_exists"
            message = (
                "a completed candidate exists; activation/removal requires a "
                "separately authorized task"
            )
        else:
            code = "incomplete_candidate_tree"
            message = "an incomplete candidate exists and requires inspection"
        issues.append(MigrationIssue(code, message, (sibling,)))
    return issues


def _issue_sort_key(issue: MigrationIssue) -> tuple[str, tuple[str, ...], str]:
    return issue.code, tuple(str(path) for path in issue.paths), issue.message


def _empty_plan(root: Path, issue: MigrationIssue) -> MigrationPlan:
    return MigrationPlan(
        rating_root=root,
        scanned_file_count=0,
        legacy_source_count=0,
        canonical_record_count=0,
        entries=(),
        issues=(issue,),
        source_snapshots=(),
        expected_identities=(),
        source_manifest_sha256=_manifest_digest(root, ()),
    )


def plan_legacy_migration(
    rating_root: str | os.PathLike[str],
) -> MigrationPlan:
    """Return a deterministic read-only conflict/candidate plan."""

    root = _absolute_path(rating_root)
    if root.name != _EXPECTED_ROOT_NAME:
        return _empty_plan(
            root,
            MigrationIssue(
                "rating_root_name",
                "rating root basename must be exactly 'RatingFiles'",
                (root,),
            ),
        )
    if root.is_symlink():
        return _empty_plan(
            root,
            MigrationIssue(
                "rating_root_symlink",
                "rating root must not be a symbolic link",
                (root,),
            ),
        )
    if not root.exists():
        return _empty_plan(
            root,
            MigrationIssue(
                "rating_root_missing",
                "rating root does not exist",
                (root,),
            ),
        )
    if not root.is_dir():
        return _empty_plan(
            root,
            MigrationIssue(
                "rating_root_not_directory",
                "rating root must be a directory",
                (root,),
            ),
        )

    files, issues = _walk_rating_tree(root)
    issues.extend(_candidate_sibling_issues(root))
    snapshots: list[MigrationSourceSnapshot] = []
    for path in files:
        snapshot, issue = _snapshot_one(root, path)
        if issue is not None:
            issues.append(issue)
        elif snapshot is not None:
            snapshots.append(snapshot)
        else:
            raise AssertionError("migration scan returned no result")
    issues.extend(_casefold_issues(snapshots))

    prospective_root = _prospective_candidate_root(root)
    try:
        _validate_candidate_auxiliary_paths(prospective_root)
    except RatingPathError as exc:
        issues.append(
            MigrationIssue(
                "candidate_path_invalid",
                str(exc),
                (prospective_root,),
            )
        )

    legacy_by_identity: dict[
        RatingIdentity, list[MigrationSourceSnapshot]
    ] = {}
    canonical_by_identity: dict[
        RatingIdentity, list[MigrationSourceSnapshot]
    ] = {}
    for snapshot in snapshots:
        target = (
            legacy_by_identity
            if snapshot.filename_format == "legacy"
            else canonical_by_identity
        )
        target.setdefault(snapshot.identity, []).append(snapshot)

    candidates: list[MigrationEntry] = []
    for identity, source_snapshots in sorted(
        legacy_by_identity.items(),
        key=lambda item: (
            item[0].module_name,
            item[0].rater,
            item[0].ezqcid,
        ),
    ):
        source_snapshots = sorted(source_snapshots, key=lambda item: str(item.path))
        try:
            active_destination = canonical_rating_path(root, identity)
            candidate_destination = _validate_candidate_rating_path(
                prospective_root, identity
            )
        except RatingPathError as exc:
            issues.append(
                MigrationIssue(
                    "candidate_path_invalid",
                    str(exc),
                    _sorted_paths(item.path for item in source_snapshots),
                    identity,
                )
            )
            continue
        if len(source_snapshots) > 1:
            issues.append(
                MigrationIssue(
                    "duplicate_identity",
                    (
                        "multiple legacy files declare one identity; "
                        "migration never chooses a winner"
                    ),
                    _sorted_paths(item.path for item in source_snapshots),
                    identity,
                )
            )
            continue
        matching_canonical = canonical_by_identity.get(identity, [])
        if matching_canonical:
            issues.append(
                MigrationIssue(
                    "legacy_canonical_coexistence",
                    (
                        "legacy and canonical files coexist for one identity; "
                        "manual resolution is required"
                    ),
                    (
                        source_snapshots[0].path,
                        *(
                            item.path
                            for item in sorted(
                                matching_canonical, key=lambda item: str(item.path)
                            )
                        ),
                    ),
                    identity,
                )
            )
            continue
        if os.path.lexists(active_destination):
            issues.append(
                MigrationIssue(
                    "target_occupied",
                    "canonical destination is occupied and is not reusable",
                    (source_snapshots[0].path, active_destination),
                    identity,
                )
            )
            continue
        source = source_snapshots[0]
        candidates.append(
            MigrationEntry(
                source=source.path,
                canonical_relative_path=candidate_destination.relative_to(
                    prospective_root
                ),
                identity=identity,
                source_sha256=source.sha256,
            )
        )

    # Canonical-only records also need to fit the candidate/temp budgets.
    legacy_identities = set(legacy_by_identity)
    for identity, canonical_snapshots in canonical_by_identity.items():
        if identity in legacy_identities:
            continue
        try:
            _validate_candidate_rating_path(prospective_root, identity)
        except RatingPathError as exc:
            issues.append(
                MigrationIssue(
                    "candidate_path_invalid",
                    str(exc),
                    _sorted_paths(item.path for item in canonical_snapshots),
                    identity,
                )
            )

    conflict_paths = {path for issue in issues for path in issue.paths}
    entries = tuple(
        entry
        for entry in sorted(candidates, key=lambda item: str(item.source))
        if entry.source not in conflict_paths
    )
    expected_identities = tuple(
        sorted(
            {snapshot.identity for snapshot in snapshots},
            key=lambda identity: (
                identity.module_name,
                identity.rater,
                identity.ezqcid,
            ),
        )
    )
    ordered_snapshots = tuple(sorted(snapshots, key=lambda item: str(item.path)))
    return MigrationPlan(
        rating_root=root,
        scanned_file_count=len(files),
        legacy_source_count=sum(
            item.filename_format == "legacy" for item in snapshots
        ),
        canonical_record_count=sum(
            item.filename_format == "canonical" for item in snapshots
        ),
        entries=entries,
        issues=tuple(sorted(issues, key=_issue_sort_key)),
        source_snapshots=ordered_snapshots,
        expected_identities=expected_identities,
        source_manifest_sha256=_source_manifest(root, ordered_snapshots),
    )


def _canonical_payload_from_copy(
    copied_source: Path,
    expected_sha256: str,
    *,
    raw: bytes | None = None,
) -> dict[str, Any]:
    if raw is None:
        raw = copied_source.read_bytes()
    if _sha256_bytes(raw) != expected_sha256:
        raise ValueError(f"copied source differs from plan: {copied_source}")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("rating JSON root must be an object")
    canonical = dict(payload)
    current_version = canonical.get("schema_version")
    if type(current_version) is not int or current_version < 2:
        canonical["schema_version"] = 2
    return canonical


def _allocate_atomic_temp(parent: Path) -> tuple[int, Path]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_BINARY", 0)
    for _attempt in range(128):
        path = parent / f".qt-{secrets.token_hex(4)}.tmp"
        _validate_portable_path(path)
        try:
            return os.open(path, flags, 0o600), path
        except FileExistsError:
            continue
    raise FileExistsError("cannot allocate a unique migration temporary file")


def _atomic_write_text(path: Path, text: str) -> None:
    _validate_portable_path(path)
    _validate_portable_path(path.parent / _ATOMIC_TEMP_TEMPLATE)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_path = _allocate_atomic_temp(path.parent)
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="",
        ) as stream:
            descriptor = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)


def _copy_active_tree(source_root: Path, candidate_root: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        return (
            {_RATING_WRITE_LOCK_NAME}
            if (
                Path(directory) == source_root
                and _RATING_WRITE_LOCK_NAME in names
            )
            else set()
        )

    shutil.copytree(
        source_root,
        candidate_root,
        dirs_exist_ok=True,
        copy_function=shutil.copy2,
        ignore=ignore,
        symlinks=True,
    )


def _write_candidate_payload(
    destination: Path,
    payload: dict[str, Any],
) -> None:
    _atomic_write_text(
        destination,
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=4,
            sort_keys=True,
            allow_nan=False,
        ),
    )


def _assert_exact_payload(path: Path, expected: dict[str, Any]) -> None:
    actual = json.loads(path.read_bytes().decode("utf-8"))
    actual_bytes = _deterministic_json_bytes(actual)
    expected_bytes = _deterministic_json_bytes(expected)
    if (
        _sha256_bytes(actual_bytes) != _sha256_bytes(expected_bytes)
        or actual_bytes != expected_bytes
    ):
        raise ValueError(f"candidate payload differs from source snapshot: {path}")


def _verify_candidate_tree(
    candidate_root: Path,
    expected_identities: tuple[RatingIdentity, ...],
) -> tuple[tuple[Path, ...], str]:
    files, walk_issues = _walk_rating_tree(candidate_root)
    if walk_issues:
        raise ValueError(
            "candidate traversal failed: "
            + "; ".join(issue.message for issue in walk_issues)
        )
    seen: set[RatingIdentity] = set()
    canonical_files: list[Path] = []
    path_hashes: list[tuple[Path, str]] = []
    for path in files:
        snapshot, issue = _snapshot_one(candidate_root, path)
        if issue is not None:
            raise ValueError(f"candidate validation failed at {path}: {issue.message}")
        if snapshot is None or snapshot.filename_format != "canonical":
            raise ValueError(f"candidate contains a non-canonical rating: {path}")
        identity = snapshot.identity
        if identity in seen:
            raise ValueError(f"candidate duplicates rating identity: {identity}")
        seen.add(identity)
        if path != canonical_rating_path(candidate_root, identity):
            raise ValueError(f"candidate path is not canonical: {path}")
        canonical_files.append(path)
        path_hashes.append((path, snapshot.sha256))
    ordered_identities = tuple(
        sorted(
            seen,
            key=lambda identity: (
                identity.module_name,
                identity.rater,
                identity.ezqcid,
            ),
        )
    )
    if ordered_identities != expected_identities:
        raise ValueError(
            "candidate identity set differs from reviewed source identities"
        )
    paths = tuple(sorted(canonical_files, key=str))
    return paths, _manifest_digest(candidate_root, path_hashes)


def _verify_active_snapshots(plan: MigrationPlan) -> None:
    files, walk_issues = _walk_rating_tree(plan.rating_root)
    if walk_issues:
        raise ValueError(
            "active source traversal changed: "
            + "; ".join(issue.message for issue in walk_issues)
        )
    current_manifest = _manifest_digest(
        plan.rating_root,
        ((path, _sha256_file(path)) for path in files),
    )
    if current_manifest != plan.source_manifest_sha256:
        raise ValueError("active source tree manifest changed")


def _complete_marker_payload(
    plan: MigrationPlan,
    candidate_manifest_sha256: str,
    record_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "manifest_format_version": _MANIFEST_FORMAT_VERSION,
        "status": "verified_candidate_not_activated",
        "source_rating_root": str(plan.rating_root),
        "source_manifest_sha256": plan.source_manifest_sha256,
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "canonical_record_count": record_count,
        "activation_requires_separate_authorization": True,
        "activation_must_revalidate_manifests_under_writer_lock": True,
    }


def _write_complete_marker(
    candidate_root: Path,
    payload: dict[str, Any],
) -> None:
    marker = candidate_root / _COMPLETE_MARKER
    _atomic_write_text(
        marker,
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ),
    )
    observed = json.loads(marker.read_text(encoding="utf-8"))
    if observed != payload:
        raise ValueError("candidate completion marker verification failed")


def _create_candidate_root(parent: Path) -> Path:
    for _attempt in range(128):
        candidate = parent / f"{_CANDIDATE_PREFIX}{secrets.token_hex(4)}"
        if len(candidate.name) != len(_EXPECTED_ROOT_NAME):
            raise AssertionError("candidate name must match RatingFiles length")
        _validate_candidate_auxiliary_paths(candidate)
        try:
            candidate.mkdir(mode=0o700)
            return candidate
        except FileExistsError:
            continue
    raise FileExistsError("cannot allocate a unique migration candidate directory")


def _remove_incomplete_candidate(candidate_root: Path) -> None:
    if os.path.lexists(candidate_root):
        shutil.rmtree(candidate_root)


def _assert_plan_is_current(plan: MigrationPlan) -> None:
    """Verify plan freshness without reparsing every source payload."""

    root = plan.rating_root
    root_is_valid = (
        os.path.lexists(root)
        and not root.is_symlink()
        and root.is_dir()
    )
    if root_is_valid and not _candidate_sibling_issues(root):
        try:
            _verify_active_snapshots(plan)
            return
        except (OSError, ValueError):
            pass

    current = plan_legacy_migration(root)
    raise MigrationPlanStaleError(plan, current)


def _build_candidate_locked(plan: MigrationPlan) -> MigrationReport:
    _assert_plan_is_current(plan)

    candidate_root: Path | None = None
    try:
        candidate_root = _create_candidate_root(plan.rating_root.parent)
        _copy_active_tree(plan.rating_root, candidate_root)
        entries_by_source = {entry.source: entry for entry in plan.entries}
        for snapshot in plan.source_snapshots:
            copied = candidate_root / snapshot.path.relative_to(plan.rating_root)
            raw = copied.read_bytes()
            if _sha256_bytes(raw) != snapshot.sha256:
                raise ValueError(f"candidate copy differs from plan: {copied}")
            entry = entries_by_source.get(snapshot.path)
            if entry is None:
                continue
            copied_source = copied
            destination = candidate_root / entry.canonical_relative_path
            if os.path.lexists(destination):
                raise FileExistsError(
                    f"candidate destination is occupied: {destination}"
                )
            payload = _canonical_payload_from_copy(
                copied_source,
                entry.source_sha256,
                raw=raw,
            )
            _write_candidate_payload(destination, payload)
            _assert_exact_payload(destination, payload)
            copied_source.unlink()

        canonical_files, candidate_manifest = _verify_candidate_tree(
            candidate_root, plan.expected_identities
        )
        _verify_active_snapshots(plan)
        marker_payload = _complete_marker_payload(
            plan, candidate_manifest, len(canonical_files)
        )
        _write_complete_marker(candidate_root, marker_payload)
    except Exception as exc:
        cleanup_error: OSError | None = None
        if candidate_root is not None:
            try:
                _remove_incomplete_candidate(candidate_root)
            except OSError as cleanup_exc:
                cleanup_error = cleanup_exc
        if cleanup_error is not None:
            raise MigrationApplyError(
                (
                    f"candidate build failed ({exc}) and cleanup failed "
                    f"({cleanup_error})"
                ),
                cause=exc,
                candidate_root=candidate_root,
            ) from exc
        raise MigrationApplyError(
            f"candidate build failed: {exc}",
            cause=exc,
        ) from exc

    if candidate_root is None:
        raise AssertionError("candidate build completed without an output root")
    return MigrationReport(
        plan=plan,
        candidate_root=candidate_root,
        canonical_files=canonical_files,
        retained_source_root=plan.rating_root,
        candidate_manifest_sha256=candidate_manifest,
    )


def apply_legacy_migration(plan: MigrationPlan) -> MigrationReport:
    """Build one inactive candidate under the project writer lock."""

    if not isinstance(plan, MigrationPlan):
        raise TypeError("plan must be a MigrationPlan")
    if plan.has_conflicts:
        raise MigrationConflictError(plan)
    lock_probe = plan.rating_root / "_migration_candidate_probe.json"
    try:
        with rating_write_lock(lock_probe, create_lock_root=False):
            return _build_candidate_locked(plan)
    except (MigrationPlanStaleError, MigrationApplyError):
        raise
    except RatingWriteLockError as exc:
        raise MigrationApplyError(
            f"cannot acquire project rating writer lock: {exc}",
            cause=exc,
        ) from exc
    except Exception as exc:
        raise MigrationApplyError(
            f"candidate migration failed before completion: {exc}",
            cause=exc,
        ) from exc


__all__ = [
    "MigrationApplyError",
    "MigrationConflictError",
    "MigrationEntry",
    "MigrationIssue",
    "MigrationPlan",
    "MigrationPlanStaleError",
    "MigrationReport",
    "MigrationSourceSnapshot",
    "RatingMigrationError",
    "apply_legacy_migration",
    "plan_legacy_migration",
]
