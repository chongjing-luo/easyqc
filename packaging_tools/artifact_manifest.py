"""Canonical no-follow identity for one finalized release artifact."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat

from .contracts import (
    ReleaseContractError,
    canonical_json_bytes,
    sha256_file,
    write_canonical_json,
)


ARTIFACT_MANIFEST_SCHEMA = "easyqc-release-artifact-manifest-v1"


class ArtifactManifestError(ReleaseContractError):
    """Raised when an artifact cannot produce one exact no-follow identity."""


@dataclass(frozen=True)
class ArtifactEntry:
    """One canonical directory, regular-file, or symlink record."""

    path: str
    entry_type: str
    mode: str
    size: int | None = None
    sha256: str | None = None
    target: str | None = None

    def __post_init__(self) -> None:
        common_valid = (
            _is_canonical_relative_text(self.path)
            and self.entry_type in {"directory", "regular-file", "symlink"}
            and len(self.mode) == 4
            and all(character in "01234567" for character in self.mode)
        )
        if not common_valid:
            raise ArtifactManifestError(f"invalid artifact entry: {self.path!r}")
        if self.entry_type == "directory":
            valid = self.size is None and self.sha256 is None and self.target is None
        elif self.entry_type == "regular-file":
            valid = (
                isinstance(self.size, int)
                and self.size >= 0
                and isinstance(self.sha256, str)
                and len(self.sha256) == 64
                and all(
                    character in "0123456789abcdef" for character in self.sha256
                )
                and self.target is None
            )
        else:
            valid = (
                isinstance(self.size, int)
                and self.size >= 0
                and self.sha256 is None
                and isinstance(self.target, str)
                and bool(self.target)
                and not _invalid_symlink_target(self.path, self.target)
            )
        if not valid:
            raise ArtifactManifestError(
                f"artifact entry fields do not match {self.entry_type}: {self.path}"
            )

    def as_json_object(self) -> dict[str, object]:
        record: dict[str, object] = {
            "entry_type": self.entry_type,
            "mode": self.mode,
            "path": self.path,
        }
        if self.entry_type in {"regular-file", "symlink"}:
            record["size"] = self.size
        if self.entry_type == "regular-file":
            record["sha256"] = self.sha256
        elif self.entry_type == "symlink":
            record["target"] = self.target
        return record


@dataclass(frozen=True)
class ArtifactManifest:
    """Immutable typed manifest whose canonical-byte hash is Candidate ID."""

    artifact_root: Path
    entries: tuple[ArtifactEntry, ...]
    schema: str = ARTIFACT_MANIFEST_SCHEMA

    def __post_init__(self) -> None:
        root = Path(self.artifact_root)
        object.__setattr__(self, "artifact_root", root)
        if self.schema != ARTIFACT_MANIFEST_SCHEMA:
            raise ArtifactManifestError(
                f"artifact manifest schema must be {ARTIFACT_MANIFEST_SCHEMA}"
            )
        paths = tuple(entry.path for entry in self.entries)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ArtifactManifestError(
                "artifact manifest entries must be unique and sorted"
            )
        casefold_paths: dict[str, str] = {}
        for path in paths:
            collision_key = path.casefold()
            previous = casefold_paths.get(collision_key)
            if previous is not None and previous != path:
                raise ArtifactManifestError(
                    "Windows case collision in artifact paths: "
                    f"{previous!r} and {path!r}"
                )
            casefold_paths[collision_key] = path

    def as_json_object(self) -> dict[str, object]:
        """Return the sole versioned JSON representation; root is excluded."""

        return {
            "schema": self.schema,
            "entries": [entry.as_json_object() for entry in self.entries],
        }

    @property
    def canonical_bytes(self) -> bytes:
        """Return canonical manifest bytes without touching the filesystem."""

        return canonical_json_bytes(self.as_json_object())

    @property
    def candidate_id(self) -> str:
        """Return SHA-256 of canonical manifest bytes."""

        return hashlib.sha256(self.canonical_bytes).hexdigest()


def create_artifact_manifest(artifact_root: Path) -> ArtifactManifest:
    """Create one no-follow manifest for a finalized artifact directory.

    Input: one regular artifact directory containing only directories, regular
    files, and contained relative symlinks.
    Output: one sorted ``ArtifactManifest`` and its derived Candidate ID.
    Side effects: read/lstat/hash only; no writes, subprocesses, or network.
    Errors: invalid roots, paths, links, types, collisions, and observed
    concurrent replacements raise ``ArtifactManifestError``.
    Split trigger: build, notice, inventory, and native work stay elsewhere.
    """

    root = Path(artifact_root)
    root_metadata = _lstat(root, "artifact root")
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise ArtifactManifestError(
            f"artifact root must be a regular directory: {root}"
        )
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise ArtifactManifestError(
            f"artifact root cannot be resolved: {root}: {exc}"
        ) from exc

    records: list[ArtifactEntry] = []
    casefold_paths: dict[str, str] = {}
    pending: list[tuple[Path, os.stat_result]] = [(root, root_metadata)]
    while pending:
        directory, expected_metadata = pending.pop()
        _require_unchanged(directory, expected_metadata, "directory")
        try:
            with os.scandir(directory) as iterator:
                discovered = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise ArtifactManifestError(
                f"cannot scan artifact directory {directory}: {exc}"
            ) from exc
        _require_unchanged(directory, expected_metadata, "directory")

        for discovered_entry in discovered:
            path = Path(discovered_entry.path)
            relative_path = _canonical_relative_path(path, root)
            collision_key = relative_path.casefold()
            previous = casefold_paths.get(collision_key)
            if previous is not None and previous != relative_path:
                raise ArtifactManifestError(
                    "Windows case collision in artifact paths: "
                    f"{previous!r} and {relative_path!r}"
                )
            casefold_paths[collision_key] = relative_path

            metadata = _lstat(path, relative_path)
            mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
            if stat.S_ISLNK(metadata.st_mode):
                target = _read_contained_symlink(path, relative_path, metadata)
                records.append(
                    ArtifactEntry(
                        path=relative_path,
                        entry_type="symlink",
                        mode=mode,
                        size=metadata.st_size,
                        target=target,
                    )
                )
            elif _is_reparse_point(metadata):
                raise ArtifactManifestError(
                    f"unsupported entry type at {relative_path}: reparse-point"
                )
            elif stat.S_ISDIR(metadata.st_mode):
                records.append(
                    ArtifactEntry(
                        path=relative_path,
                        entry_type="directory",
                        mode=mode,
                    )
                )
                pending.append((path, metadata))
            elif stat.S_ISREG(metadata.st_mode):
                try:
                    digest = sha256_file(path)
                except ReleaseContractError as exc:
                    raise ArtifactManifestError(
                        f"cannot hash artifact file {relative_path}: {exc}"
                    ) from exc
                _require_unchanged(path, metadata, relative_path)
                records.append(
                    ArtifactEntry(
                        path=relative_path,
                        entry_type="regular-file",
                        mode=mode,
                        size=metadata.st_size,
                        sha256=digest,
                    )
                )
            else:
                raise ArtifactManifestError(
                    f"unsupported entry type at {relative_path}"
                )

    records.sort(key=lambda entry: entry.path)
    return ArtifactManifest(
        artifact_root=resolved_root,
        entries=tuple(records),
    )


def write_artifact_manifest(path: Path, manifest: ArtifactManifest) -> str:
    """Atomically create one manifest outside its candidate and return its ID.

    Input: one new output path plus an ``ArtifactManifest``.
    Output: Candidate ID, equal to the written file SHA-256.
    Side effects: creates exactly one canonical JSON file outside the artifact.
    Errors: in-artifact/existing/invalid destinations and writes fail loud.
    Split trigger: receipt and multi-file stage output belong to later packets.
    """

    destination = Path(path)
    try:
        resolved_parent = destination.parent.resolve(strict=True)
        resolved_root = manifest.artifact_root.resolve(strict=True)
    except OSError as exc:
        raise ArtifactManifestError(
            f"manifest output path cannot be resolved: {destination}: {exc}"
        ) from exc
    resolved_destination = resolved_parent / destination.name
    if resolved_destination == resolved_root or resolved_destination.is_relative_to(
        resolved_root
    ):
        raise ArtifactManifestError(
            f"artifact manifest output must be outside the artifact root: {destination}"
        )

    try:
        digest = write_canonical_json(destination, manifest.as_json_object())
    except ReleaseContractError as exc:
        raise ArtifactManifestError(str(exc)) from exc
    if digest != manifest.candidate_id:
        raise ArtifactManifestError(
            "written artifact manifest digest does not match Candidate ID"
        )
    return digest


def _canonical_relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ArtifactManifestError(f"artifact path escapes root: {path}") from exc
    if not _is_canonical_relative_text(relative):
        raise ArtifactManifestError(
            f"artifact path is not canonical POSIX relative: {relative!r}"
        )
    return relative


def _read_contained_symlink(
    path: Path,
    relative_path: str,
    expected_metadata: os.stat_result,
) -> str:
    try:
        raw_target = os.readlink(path)
    except OSError as exc:
        raise ArtifactManifestError(
            f"cannot read artifact symlink {relative_path}: {exc}"
        ) from exc
    _require_unchanged(path, expected_metadata, relative_path)
    target = raw_target.replace("\\", "/") if os.name == "nt" else raw_target
    if _invalid_symlink_target(relative_path, target):
        raise ArtifactManifestError(
            "symlink target must stay within the artifact root: "
            f"{relative_path} -> {raw_target}"
        )
    return target


def _is_canonical_relative_text(relative: str) -> bool:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return False
    try:
        relative.encode("utf-8")
    except UnicodeEncodeError:
        return False
    pure_path = PurePosixPath(relative)
    return (
        not pure_path.is_absolute()
        and pure_path.as_posix() == relative
        and all(part not in {"", ".", ".."} for part in pure_path.parts)
    )


def _invalid_symlink_target(relative_path: str, target: str) -> bool:
    if not target or "\\" in target:
        return True
    try:
        target.encode("utf-8")
    except UnicodeEncodeError:
        return True
    windows_target = PureWindowsPath(target)
    pure_target = PurePosixPath(target)
    return (
        pure_target.is_absolute()
        or windows_target.is_absolute()
        or bool(windows_target.drive)
        or _target_escapes_root(relative_path, pure_target)
    )


def _target_escapes_root(relative_path: str, target: PurePosixPath) -> bool:
    depth = len(PurePosixPath(relative_path).parent.parts)
    for part in target.parts:
        if part == "..":
            if depth == 0:
                return True
            depth -= 1
        elif part not in {"", "."}:
            depth += 1
    return False


def _lstat(path: Path, label: str) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise ArtifactManifestError(f"cannot lstat {label}: {path}: {exc}") from exc


def _require_unchanged(
    path: Path,
    expected: os.stat_result,
    label: str,
) -> None:
    current = _lstat(path, label)
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_size",
        "st_mtime_ns",
        "st_ctime_ns",
    )
    if any(getattr(current, field) != getattr(expected, field) for field in stable_fields):
        raise ArtifactManifestError(f"artifact entry changed while manifesting: {label}")


def _is_reparse_point(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


__all__ = [
    "ARTIFACT_MANIFEST_SCHEMA",
    "ArtifactEntry",
    "ArtifactManifest",
    "ArtifactManifestError",
    "create_artifact_manifest",
    "write_artifact_manifest",
]
