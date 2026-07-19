"""Shared fail-closed file I/O for build-time component evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat

from .contracts import ReleaseContractError


_COLLECT_TYPES = frozenset(
    {"BINARY", "DATA", "EXECUTABLE", "EXTENSION", "SYMLINK"}
)
_MAX_EVIDENCE_FILE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_EVIDENCE_BYTES = 256 * 1024 * 1024
_MAX_SYMLINKS = 64
_MAX_SYMLINK_TARGET_BYTES = 4096
_SUPPORTS_OPENAT = os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY")
_IDENTITY_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")


@dataclass(frozen=True)
class _ResolvedRegularFile:
    descriptor: int
    relative: PurePosixPath
    metadata: os.stat_result


def _canonical_relative_path(value: object, label: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
    ):
        raise ReleaseContractError(f"{label} must be a canonical relative path")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReleaseContractError(f"{label} must be valid UTF-8") from exc
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ReleaseContractError(f"{label} must be a canonical relative path")
    return path


def _canonical_absolute_path(value: object, label: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
    ):
        raise ReleaseContractError(f"{label} must be a canonical absolute path")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReleaseContractError(f"{label} must be valid UTF-8") from exc
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if (
        not path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts[1:])
    ):
        raise ReleaseContractError(f"{label} must be a canonical absolute path")
    return path


def _real_directory(path: Path, label: str) -> os.stat_result:
    try:
        metadata = Path(path).lstat()
    except OSError as exc:
        raise ReleaseContractError(f"cannot inspect {label}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseContractError(f"{label} is not a real directory")
    return metadata


def _open_root_directory(root: Path, relative: PurePosixPath, label: str) -> int:
    if not _SUPPORTS_OPENAT:
        raise ReleaseContractError(
            "component evidence capture requires descriptor-relative no-follow I/O"
        )
    root_metadata = _real_directory(root, f"{label} root")
    flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(root, flags)
    except OSError as exc:
        raise ReleaseContractError(f"cannot open {label} root: {exc}") from exc
    try:
        opened_root = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or (opened_root.st_dev, opened_root.st_ino)
            != (root_metadata.st_dev, root_metadata.st_ino)
        ):
            raise ReleaseContractError(f"{label} root changed while opening")
        for part in relative.parts:
            try:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
            except OSError as exc:
                raise ReleaseContractError(
                    f"{label} parent is missing, changed, or an unsafe symlink: {part}"
                ) from exc
            opened = os.fstat(next_descriptor)
            if not stat.S_ISDIR(opened.st_mode):
                os.close(next_descriptor)
                raise ReleaseContractError(f"{label} parent is not a directory")
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_root_regular_file(root: Path, relative: PurePosixPath, label: str) -> int:
    parent = PurePosixPath(*relative.parts[:-1])
    parent_descriptor = _open_root_directory(root, parent, label)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        try:
            descriptor = os.open(relative.parts[-1], flags, dir_fd=parent_descriptor)
        except OSError as exc:
            raise ReleaseContractError(
                f"{label} is missing, changed, or an unsafe symlink"
            ) from exc
    finally:
        os.close(parent_descriptor)
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode):
        os.close(descriptor)
        raise ReleaseContractError(f"{label} is not a regular file")
    return descriptor


def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return all(
        getattr(first, field) == getattr(second, field)
        for field in _IDENTITY_FIELDS
    )


def _read_descriptor(
    descriptor: int,
    label: str,
    *,
    maximum_bytes: int,
) -> tuple[bytes, os.stat_result]:
    before = os.fstat(descriptor)
    if before.st_size > maximum_bytes:
        raise ReleaseContractError(f"{label} exceeds {maximum_bytes} bytes")
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                raise ReleaseContractError(f"{label} exceeds {maximum_bytes} bytes")
    except OSError as exc:
        raise ReleaseContractError(f"cannot read {label}: {exc}") from exc
    after = os.fstat(descriptor)
    if not _same_identity(before, after):
        raise ReleaseContractError(f"{label} changed while it was read")
    return b"".join(chunks), before


def _read_root_bytes(
    root: Path,
    relative: PurePosixPath,
    label: str,
) -> tuple[bytes, dict[str, object]]:
    first_descriptor = _open_root_regular_file(root, relative, label)
    try:
        first_bytes, first_stat = _read_descriptor(
            first_descriptor,
            label,
            maximum_bytes=_MAX_EVIDENCE_FILE_BYTES,
        )
    finally:
        os.close(first_descriptor)
    second_descriptor = _open_root_regular_file(root, relative, label)
    try:
        second_bytes, second_stat = _read_descriptor(
            second_descriptor,
            label,
            maximum_bytes=_MAX_EVIDENCE_FILE_BYTES,
        )
    finally:
        os.close(second_descriptor)
    if not _same_identity(first_stat, second_stat) or first_bytes != second_bytes:
        raise ReleaseContractError(f"{label} changed after it was read")
    return first_bytes, _bytes_identity(first_bytes)


def _hash_root_file(
    root: Path,
    relative: PurePosixPath,
    label: str,
) -> dict[str, object]:
    descriptor = _open_root_regular_file(root, relative, label)
    try:
        before = os.fstat(descriptor)
        digest = hashlib.sha256()
        try:
            for chunk in iter(lambda: os.read(descriptor, 1024 * 1024), b""):
                digest.update(chunk)
        except OSError as exc:
            raise ReleaseContractError(f"cannot hash {label}: {exc}") from exc
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if not _same_identity(before, after):
        raise ReleaseContractError(f"{label} changed while it was hashed")
    reopened = _open_root_regular_file(root, relative, label)
    try:
        reopened_stat = os.fstat(reopened)
    finally:
        os.close(reopened)
    if not _same_identity(before, reopened_stat):
        raise ReleaseContractError(f"{label} changed after it was hashed")
    return {
        "entry_type": "regular-file",
        "sha256": digest.hexdigest(),
        "size": before.st_size,
    }


def _normalized_symlink_parts(
    resolved: list[str],
    target: str,
    remaining: list[str],
    label: str,
) -> list[str]:
    if not target or "\x00" in target or "\\" in target:
        raise ReleaseContractError(f"{label} has an unsafe symlink target")
    try:
        target_bytes = target.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReleaseContractError(
            f"{label} symlink target is not valid UTF-8"
        ) from exc
    if len(target_bytes) > _MAX_SYMLINK_TARGET_BYTES:
        raise ReleaseContractError(f"{label} symlink target exceeds path limit")
    target_path = PurePosixPath(target)
    windows_target = PureWindowsPath(target)
    if windows_target.is_absolute() or windows_target.drive:
        raise ReleaseContractError(f"{label} has an unsafe symlink target")
    combined = [] if target_path.is_absolute() else list(resolved)
    for part in target_path.parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            if not combined:
                raise ReleaseContractError(f"{label} symlink target escapes root")
            combined.pop()
        else:
            combined.append(part)
    return combined + remaining


def _open_resolved_root_regular(
    root: Path,
    logical_path: PurePosixPath,
    label: str,
) -> _ResolvedRegularFile:
    if not _SUPPORTS_OPENAT:
        raise ReleaseContractError(
            "component evidence capture requires descriptor-relative no-follow I/O"
        )
    root_metadata = _real_directory(root, f"{label} root")
    root_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    pending = list(logical_path.parts[1:] if logical_path.is_absolute() else logical_path.parts)
    if not pending:
        raise ReleaseContractError(f"{label} is not a regular file")
    resolved: list[str] = []
    visited: set[tuple[int, int]] = set()
    followed = 0

    while pending:
        parent_descriptor = _open_root_directory(
            root,
            PurePosixPath(*resolved),
            label,
        )
        part = pending.pop(0)
        try:
            try:
                before = os.stat(part, dir_fd=parent_descriptor, follow_symlinks=False)
            except OSError as exc:
                raise ReleaseContractError(
                    f"{label} is missing or changed: {part}"
                ) from exc
            if stat.S_ISLNK(before.st_mode):
                followed += 1
                marker = (before.st_dev, before.st_ino)
                if followed > _MAX_SYMLINKS or marker in visited:
                    raise ReleaseContractError(f"{label} contains a symlink cycle")
                visited.add(marker)
                try:
                    target = os.readlink(part, dir_fd=parent_descriptor)
                    after = os.stat(
                        part,
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except OSError as exc:
                    raise ReleaseContractError(
                        f"cannot inspect {label} symlink: {part}"
                    ) from exc
                if not _same_identity(before, after):
                    raise ReleaseContractError(f"{label} symlink changed while read")
                pending = _normalized_symlink_parts(
                    resolved,
                    target,
                    pending,
                    label,
                )
                resolved = []
                continue
            if pending:
                if not stat.S_ISDIR(before.st_mode):
                    raise ReleaseContractError(f"{label} parent is not a directory")
                resolved.append(part)
                continue
            if not stat.S_ISREG(before.st_mode):
                raise ReleaseContractError(f"{label} is not a regular file")
            try:
                descriptor = os.open(part, file_flags, dir_fd=parent_descriptor)
            except OSError as exc:
                raise ReleaseContractError(
                    f"{label} is missing, changed, or an unsafe symlink"
                ) from exc
            opened = os.fstat(descriptor)
            if not _same_identity(before, opened):
                os.close(descriptor)
                raise ReleaseContractError(f"{label} changed while opening")
            opened_root = os.fstat(parent_descriptor)
            if not stat.S_ISDIR(opened_root.st_mode):
                os.close(descriptor)
                raise ReleaseContractError(f"{label} parent changed while opening")
            try:
                root_now = root.lstat()
            except OSError as exc:
                os.close(descriptor)
                raise ReleaseContractError(
                    f"{label} root changed while opening"
                ) from exc
            if (root_now.st_dev, root_now.st_ino) != (
                root_metadata.st_dev,
                root_metadata.st_ino,
            ):
                os.close(descriptor)
                raise ReleaseContractError(f"{label} root changed while opening")
            return _ResolvedRegularFile(
                descriptor=descriptor,
                relative=PurePosixPath(*resolved, part),
                metadata=opened,
            )
        finally:
            os.close(parent_descriptor)
    raise ReleaseContractError(f"{label} did not resolve to a regular file")


def _hash_resolved_root_file(
    root: Path,
    logical_path: PurePosixPath,
    label: str,
) -> tuple[dict[str, object], tuple[int, int]]:
    first = _open_resolved_root_regular(root, logical_path, label)
    digest = hashlib.sha256()
    try:
        before = first.metadata
        try:
            for chunk in iter(lambda: os.read(first.descriptor, 1024 * 1024), b""):
                digest.update(chunk)
        except OSError as exc:
            raise ReleaseContractError(f"cannot hash {label}: {exc}") from exc
        after = os.fstat(first.descriptor)
    finally:
        os.close(first.descriptor)
    if not _same_identity(before, after):
        raise ReleaseContractError(f"{label} changed while it was hashed")
    second = _open_resolved_root_regular(root, logical_path, label)
    try:
        if first.relative != second.relative or not _same_identity(before, second.metadata):
            raise ReleaseContractError(f"{label} changed after it was hashed")
    finally:
        os.close(second.descriptor)
    return (
        {
            "entry_type": "regular-file",
            "sha256": digest.hexdigest(),
            "size": before.st_size,
        },
        (before.st_dev, before.st_ino),
    )


def _resolved_root_inode(
    root: Path,
    logical_path: PurePosixPath,
    label: str,
) -> tuple[int, int]:
    first = _open_resolved_root_regular(root, logical_path, label)
    try:
        first_identity = (first.metadata.st_dev, first.metadata.st_ino)
        first_relative = first.relative
    finally:
        os.close(first.descriptor)
    second = _open_resolved_root_regular(root, logical_path, label)
    try:
        second_identity = (second.metadata.st_dev, second.metadata.st_ino)
        if first_identity != second_identity or first_relative != second.relative:
            raise ReleaseContractError(f"{label} changed after it was resolved")
    finally:
        os.close(second.descriptor)
    return first_identity


def _read_resolved_root_bytes(
    root: Path,
    logical_path: PurePosixPath,
    label: str,
) -> tuple[bytes, dict[str, object]]:
    first = _open_resolved_root_regular(root, logical_path, label)
    try:
        first_bytes, first_stat = _read_descriptor(
            first.descriptor,
            label,
            maximum_bytes=_MAX_EVIDENCE_FILE_BYTES,
        )
        first_relative = first.relative
    finally:
        os.close(first.descriptor)
    second = _open_resolved_root_regular(root, logical_path, label)
    try:
        second_bytes, second_stat = _read_descriptor(
            second.descriptor,
            label,
            maximum_bytes=_MAX_EVIDENCE_FILE_BYTES,
        )
        second_relative = second.relative
    finally:
        os.close(second.descriptor)
    if (
        first_relative != second_relative
        or not _same_identity(first_stat, second_stat)
        or first_bytes != second_bytes
    ):
        raise ReleaseContractError(f"{label} changed after it was read")
    return first_bytes, _bytes_identity(first_bytes)


def _bytes_identity(data: bytes) -> dict[str, object]:
    return {
        "entry_type": "regular-file",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def _collect_index(
    collect_rows: list[dict[str, object]],
    *,
    context: str,
) -> dict[str, dict[str, object]]:
    if not isinstance(collect_rows, list):
        raise ReleaseContractError("COLLECT rows must be a list")
    expected_fields = {
        "entry_type",
        "final_path",
        "raw_source",
        "raw_source_text_sha256",
        "toc_type",
    }
    result: dict[str, dict[str, object]] = {}
    for row in collect_rows:
        if not isinstance(row, dict) or set(row) != expected_fields:
            raise ReleaseContractError(f"COLLECT {context} row fields are invalid")
        final_path = _canonical_relative_path(
            row["final_path"],
            f"COLLECT {context} final path",
        ).as_posix()
        if final_path in result:
            raise ReleaseContractError(f"duplicate COLLECT final path: {final_path}")
        raw_source = row["raw_source"]
        if not isinstance(raw_source, str) or not raw_source:
            raise ReleaseContractError(f"COLLECT source is invalid: {final_path}")
        try:
            raw_source_bytes = raw_source.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ReleaseContractError(
                f"COLLECT source is not valid UTF-8: {final_path}"
            ) from exc
        expected_digest = hashlib.sha256(raw_source_bytes).hexdigest()
        if row["raw_source_text_sha256"] != expected_digest:
            raise ReleaseContractError(f"COLLECT source digest mismatch: {final_path}")
        toc_type = row["toc_type"]
        entry_type = row["entry_type"]
        if not isinstance(toc_type, str) or toc_type not in _COLLECT_TYPES:
            raise ReleaseContractError(f"COLLECT type is invalid: {final_path}")
        if (toc_type == "SYMLINK") != (entry_type == "symlink") or (
            entry_type not in {"regular-file", "symlink"}
        ):
            raise ReleaseContractError(f"COLLECT entry type mismatch: {final_path}")
        result[final_path] = row
    return result


def _existing_owner_paths(
    components: dict[str, dict[str, object]],
) -> set[str]:
    if not isinstance(components, dict):
        raise ReleaseContractError("existing components must be an object")
    result: set[str] = set()
    for component_id in sorted(components):
        component = components[component_id]
        if (
            not isinstance(component_id, str)
            or not component_id
            or not isinstance(component, dict)
            or component.get("component_id") != component_id
            or not isinstance(component.get("collected_files"), list)
        ):
            raise ReleaseContractError("existing component identity is invalid")
        for row in component["collected_files"]:
            if not isinstance(row, dict):
                raise ReleaseContractError("existing collected row is invalid")
            final_path = _canonical_relative_path(
                row.get("final_path"),
                f"{component_id} final path",
            ).as_posix()
            if final_path in result:
                raise ReleaseContractError(
                    f"duplicate existing component owner: {final_path}"
                )
            result.add(final_path)
    return result


def _validate_build_dir(
    build_dir: Path,
    *,
    ecosystem: str,
) -> tuple[Path, Path]:
    build = Path(os.path.abspath(os.fspath(build_dir)))
    label = ecosystem.capitalize()
    if build.name != "build":
        raise ReleaseContractError(f"{label} evidence build directory must be named build")
    _real_directory(build, f"{label} evidence build directory")
    components_root = build / "evidence/components"
    _real_directory(components_root, "component evidence directory")
    ecosystem_root = components_root / ecosystem
    if ecosystem_root.exists() or ecosystem_root.is_symlink():
        raise ReleaseContractError(f"{label} component evidence already exists")
    return build, ecosystem_root


def _exclusive_output_path(root: Path, relative: str, label: str) -> Path:
    canonical = _canonical_relative_path(relative, label)
    current = root
    for part in canonical.parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise ReleaseContractError(f"{label} parent contains a symlink")
        if current.exists():
            if not current.is_dir():
                raise ReleaseContractError(f"{label} parent is not a directory")
        else:
            try:
                current.mkdir()
            except OSError as exc:
                raise ReleaseContractError(f"cannot create {label} parent: {exc}") from exc
    destination = root.joinpath(*canonical.parts)
    if destination.exists() or destination.is_symlink():
        raise ReleaseContractError(f"{label} already exists")
    return destination


def _write_evidence(path: Path, data: bytes, label: str) -> dict[str, object]:
    if len(data) > _MAX_EVIDENCE_FILE_BYTES:
        raise ReleaseContractError(f"{label} exceeds {_MAX_EVIDENCE_FILE_BYTES} bytes")
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise ReleaseContractError(f"cannot retain {label}: {exc}") from exc
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseContractError(f"cannot inspect retained {label}: {exc}") from exc
    digest = hashlib.sha256(data).hexdigest()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size != len(data)
    ):
        raise ReleaseContractError(f"retained {label} identity is invalid")
    try:
        with path.open("rb") as stream:
            retained = stream.read(_MAX_EVIDENCE_FILE_BYTES + 1)
    except OSError as exc:
        raise ReleaseContractError(f"cannot verify retained {label}: {exc}") from exc
    if retained != data or hashlib.sha256(retained).hexdigest() != digest:
        raise ReleaseContractError(f"retained {label} changed after write")
    return {"entry_type": "regular-file", "sha256": digest, "size": len(data)}
