"""Exact build-time contributor evidence for an installed Conda base runtime."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import sys

from .contracts import ReleaseContractError, RuntimeIdentity


_COLLECT_TYPES = frozenset(
    {"BINARY", "DATA", "EXECUTABLE", "EXTENSION", "SYMLINK"}
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONDA_NAME_PATTERN = re.compile(r"^[a-z0-9_][a-z0-9._-]*$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")
_BUILD_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SUBDIR_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_LICENSE_FILE_PATTERN = re.compile(
    r"(^|/)(licen[cs]e|copying|notice|authors?|copyright)([._-]|$)",
    re.IGNORECASE,
)
_MAX_EVIDENCE_FILE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_EVIDENCE_BYTES = 256 * 1024 * 1024
_SUPPORTS_OPENAT = os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY")
_IDENTITY_FIELDS = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns")


@dataclass(frozen=True)
class _CondaPackage:
    component_id: str
    name: str
    version: str
    build: str
    subdir: str
    channel: str
    url: str
    license: str
    files: tuple[str, ...]
    metadata_name: str
    metadata_bytes: bytes

    @property
    def package_id(self) -> str:
        return f"{self.name}-{self.version}-{self.build}-{self.subdir}"


def _canonical_relative_path(value: object, label: str) -> PurePosixPath:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
    ):
        raise ReleaseContractError(f"{label} must be a canonical relative path")
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
            "Conda evidence capture requires descriptor-relative no-follow I/O"
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
    return first_bytes, {
        "entry_type": "regular-file",
        "sha256": hashlib.sha256(first_bytes).hexdigest(),
        "size": len(first_bytes),
    }


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


def _metadata_names(base_prefix: Path) -> list[str]:
    conda_meta = _canonical_relative_path("conda-meta", "Conda metadata directory")
    descriptor = _open_root_directory(base_prefix, conda_meta, "Conda metadata")
    try:
        try:
            with os.scandir(descriptor) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError as exc:
            raise ReleaseContractError(f"cannot scan Conda metadata: {exc}") from exc
        names: list[str] = []
        for entry in entries:
            if not entry.name.endswith(".json"):
                continue
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                raise ReleaseContractError(
                    f"Conda metadata is not a regular file: {entry.name}"
                )
            names.append(entry.name)
        return names
    finally:
        os.close(descriptor)


def _json_object_without_duplicate_keys(raw: bytes, label: str) -> dict[str, object]:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ReleaseContractError(f"{label} has duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs)
    except ReleaseContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseContractError(f"{label} must be a JSON object")
    return value


def _required_string(
    value: object,
    label: str,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ReleaseContractError(f"{label} must be a non-empty string")
    if pattern is not None and not pattern.fullmatch(value):
        raise ReleaseContractError(f"{label} is not canonical: {value!r}")
    return value


def _load_package(
    base_prefix: Path,
    metadata_name: str,
) -> _CondaPackage:
    relative = _canonical_relative_path(
        f"conda-meta/{metadata_name}",
        "Conda metadata path",
    )
    raw, _identity = _read_root_bytes(
        base_prefix,
        relative,
        f"Conda metadata {metadata_name}",
    )
    payload = _json_object_without_duplicate_keys(raw, f"Conda metadata {metadata_name}")
    name = _required_string(payload.get("name"), "Conda package name", _CONDA_NAME_PATTERN)
    version = _required_string(payload.get("version"), "Conda package version", _VERSION_PATTERN)
    build = _required_string(payload.get("build"), "Conda package build", _BUILD_PATTERN)
    subdir = _required_string(payload.get("subdir"), "Conda package subdir", _SUBDIR_PATTERN)
    package_sha256 = _required_string(payload.get("sha256"), "Conda package SHA-256")
    if not _SHA256_PATTERN.fullmatch(package_sha256):
        raise ReleaseContractError("Conda package SHA-256 is invalid")
    channel = _required_string(payload.get("channel"), "Conda package channel")
    url = _required_string(payload.get("url"), "Conda package URL")
    if metadata_name != f"{name}-{version}-{build}.json":
        raise ReleaseContractError(
            f"Conda metadata filename disagrees with package identity: {metadata_name}"
        )
    files_value = payload.get("files")
    if not isinstance(files_value, list):
        raise ReleaseContractError(f"Conda package files are invalid: {metadata_name}")
    parsed_files = tuple(
        _canonical_relative_path(item, f"{metadata_name} file member").as_posix()
        for item in files_value
    )
    if len(parsed_files) != len(set(parsed_files)):
        raise ReleaseContractError(
            f"duplicate Conda file owner within package: {metadata_name}"
        )
    files = tuple(sorted(parsed_files))
    license_raw = payload.get("license")
    if files or license_raw not in (None, ""):
        license_value = _required_string(license_raw, "Conda package license")
    else:
        license_value = ""
    return _CondaPackage(
        component_id=f"library:{name}@{version}",
        name=name,
        version=version,
        build=build,
        subdir=subdir,
        channel=channel,
        url=url,
        license=license_value,
        files=files,
        metadata_name=metadata_name,
        metadata_bytes=raw,
    )


def _load_package_corpus(
    base_prefix: Path,
    runtime_identity: RuntimeIdentity,
) -> tuple[list[_CondaPackage], dict[str, _CondaPackage]]:
    if not isinstance(runtime_identity, RuntimeIdentity):
        raise ReleaseContractError("Conda capture requires a RuntimeIdentity")
    packages = [
        _load_package(base_prefix, name) for name in _metadata_names(base_prefix)
    ]
    component_ids: set[str] = set()
    file_owners: dict[str, _CondaPackage] = {}
    for package in packages:
        if package.component_id in component_ids:
            raise ReleaseContractError(
                f"duplicate Conda component identity: {package.component_id}"
            )
        component_ids.add(package.component_id)
        for member in package.files:
            previous = file_owners.get(member)
            if previous is not None:
                raise ReleaseContractError(
                    "duplicate Conda file owner across packages: "
                    f"{member} ({previous.component_id}, {package.component_id})"
                )
            file_owners[member] = package
    python_packages = [package for package in packages if package.name == "python"]
    if len(python_packages) != 1 or python_packages[0].version != (
        runtime_identity.version
    ):
        raise ReleaseContractError(
            "Conda Python identity does not match the authenticated RuntimeIdentity"
        )
    return packages, file_owners


def _collect_index(
    collect_rows: list[dict[str, object]],
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
            raise ReleaseContractError("COLLECT Conda row fields are invalid")
        final_path = _canonical_relative_path(
            row["final_path"],
            "COLLECT Conda final path",
        ).as_posix()
        if final_path in result:
            raise ReleaseContractError(f"duplicate COLLECT final path: {final_path}")
        raw_source = row["raw_source"]
        if not isinstance(raw_source, str) or not raw_source:
            raise ReleaseContractError(f"COLLECT source is invalid: {final_path}")
        expected_digest = hashlib.sha256(raw_source.encode("utf-8")).hexdigest()
        if row["raw_source_text_sha256"] != expected_digest:
            raise ReleaseContractError(
                f"COLLECT source digest mismatch: {final_path}"
            )
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


def _source_member(base_prefix: Path, raw_source: str) -> str | None:
    if not os.path.isabs(raw_source):
        return None
    root = Path(os.path.abspath(os.fspath(base_prefix)))
    source = Path(os.path.abspath(raw_source))
    try:
        relative = source.relative_to(root).as_posix()
    except ValueError:
        return None
    return _canonical_relative_path(relative, "Conda COLLECT source member").as_posix()


def _validate_build_dir(build_dir: Path) -> tuple[Path, Path]:
    build = Path(os.path.abspath(os.fspath(build_dir)))
    if build.name != "build":
        raise ReleaseContractError("Conda evidence build directory must be named build")
    _real_directory(build, "Conda evidence build directory")
    components_root = build / "evidence/components"
    _real_directory(components_root, "component evidence directory")
    conda_root = components_root / "conda"
    if conda_root.exists() or conda_root.is_symlink():
        raise ReleaseContractError("Conda component evidence already exists")
    return build, conda_root


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


def capture_conda_component_evidence(
    collect_rows: list[dict[str, object]],
    existing_components: dict[str, dict[str, object]],
    build_dir: Path,
    runtime_identity: RuntimeIdentity,
) -> dict[str, dict[str, object]]:
    """Capture exact contributing Conda components for one build packet.

    Input: retained raw COLLECT rows, higher-precedence ownership, one new
    packet build directory, and the authenticated running RuntimeIdentity.
    Output: component-ID-sorted composite-v3 component objects for only exact
    contributing Conda packages.
    Side effects: exclusively retains those packages' metadata/license bytes
    below ``build/evidence/components/conda``.
    Errors: malformed/ambiguous metadata, unsafe/changing installed sources,
    identity drift, evidence bounds, and existing outputs fail loud.
    Split trigger: Debian/system collection or final v3 orchestration belongs
    in a separate packet/function.
    """

    build, conda_root = _validate_build_dir(build_dir)
    base_prefix = Path(os.path.abspath(os.fspath(sys.base_prefix)))
    _real_directory(base_prefix, "Conda base prefix")
    packages, file_owners = _load_package_corpus(base_prefix, runtime_identity)
    collect_by_path = _collect_index(collect_rows)
    already_owned = _existing_owner_paths(existing_components)
    if any(path not in collect_by_path for path in already_owned):
        raise ReleaseContractError(
            "existing component owns a path outside retained COLLECT"
        )

    assignments: dict[str, list[dict[str, object]]] = {}
    contributing: dict[str, _CondaPackage] = {}
    for final_path in sorted(collect_by_path):
        if final_path in already_owned:
            continue
        row = collect_by_path[final_path]
        if row["entry_type"] != "regular-file":
            continue
        member = _source_member(base_prefix, str(row["raw_source"]))
        package = file_owners.get(member) if member is not None else None
        if package is None:
            continue
        source_identity = _hash_root_file(
            base_prefix,
            PurePosixPath(member),
            f"Conda contributor {member}",
        )
        assignments.setdefault(package.component_id, []).append(
            {
                "entry_type": "regular-file",
                "final_path": final_path,
                "package_path": member,
                "raw_source_text_sha256": row["raw_source_text_sha256"],
                "source_identity": source_identity,
                "source_kind": "conda-package-file",
                "source_locator": f"conda-package/{package.package_id}/{member}",
                "target_final_path": None,
                "toc_type": row["toc_type"],
            }
        )
        contributing[package.component_id] = package

    planned_evidence: dict[
        str,
        list[tuple[str, str, str, bytes, dict[str, object]]],
    ] = {}
    total_evidence_bytes = 0
    for component_id in sorted(contributing):
        package = contributing[component_id]
        metadata_relative = PurePosixPath("conda-meta") / package.metadata_name
        current_metadata, metadata_identity = _read_root_bytes(
            base_prefix,
            metadata_relative,
            f"contributing Conda metadata {package.metadata_name}",
        )
        if current_metadata != package.metadata_bytes:
            raise ReleaseContractError(
                f"Conda metadata changed after package map parse: {package.metadata_name}"
            )
        rows = [
            (
                "conda-meta",
                f"conda-meta/{package.metadata_name}",
                f"{package.package_id}/conda-meta.json",
                current_metadata,
                metadata_identity,
            )
        ]
        for member in package.files:
            if not _LICENSE_FILE_PATTERN.search(member):
                continue
            license_bytes, license_identity = _read_root_bytes(
                base_prefix,
                PurePosixPath(member),
                f"Conda license {package.component_id} {member}",
            )
            rows.append(
                (
                    "conda-license",
                    f"conda-package/{package.package_id}/{member}",
                    f"{package.package_id}/licenses/{member}",
                    license_bytes,
                    license_identity,
                )
            )
        rows.sort(key=lambda item: (item[0], item[1], item[2]))
        total_evidence_bytes += sum(len(item[3]) for item in rows)
        if total_evidence_bytes > _MAX_TOTAL_EVIDENCE_BYTES:
            raise ReleaseContractError("Conda component evidence exceeds aggregate limit")
        planned_evidence[component_id] = rows

    if not contributing:
        return {}
    try:
        conda_root.mkdir()
    except OSError as exc:
        raise ReleaseContractError(f"cannot create Conda evidence directory: {exc}") from exc

    evidence_by_component: dict[str, list[dict[str, object]]] = {}
    for component_id in sorted(planned_evidence):
        evidence_rows: list[dict[str, object]] = []
        for kind, source_locator, retained_relative, data, source_identity in (
            planned_evidence[component_id]
        ):
            destination = _exclusive_output_path(
                conda_root,
                retained_relative,
                f"{component_id} {kind} evidence",
            )
            retained_identity = _write_evidence(
                destination,
                data,
                f"{component_id} {kind} evidence",
            )
            if retained_identity != source_identity:
                raise ReleaseContractError(
                    f"retained {component_id} {kind} evidence changed"
                )
            evidence_rows.append(
                {
                    "kind": kind,
                    "retained_path": destination.relative_to(build.parent).as_posix(),
                    "sha256": retained_identity["sha256"],
                    "size": retained_identity["size"],
                    "source_locator": source_locator,
                }
            )
        evidence_by_component[component_id] = evidence_rows

    result: dict[str, dict[str, object]] = {}
    packages_by_id = {package.component_id: package for package in packages}
    for component_id in sorted(contributing):
        package = packages_by_id[component_id]
        result[component_id] = {
            "collected_files": sorted(
                assignments[component_id],
                key=lambda row: str(row["final_path"]),
            ),
            "component_id": component_id,
            "ecosystem": "conda",
            "evidence_files": evidence_by_component[component_id],
            "license_candidates": [
                {"field": "license", "value": package.license}
            ],
            "name": package.name,
            "provider_candidates": [
                {"field": "channel", "value": package.channel},
                {"field": "url", "value": package.url},
            ],
            "purl": (
                f"pkg:conda/{package.name}@{package.version}"
                f"?build={package.build}&subdir={package.subdir}"
            ),
            "type": "library",
            "version": package.version,
        }
    return result
