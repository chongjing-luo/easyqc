"""Closed-world composition helpers for canonical component evidence v3.

The build entry point owns ecosystem precedence and the frozen public function.
This module owns authenticated EasyQC/cursor objects plus final structural,
coverage, evidence, and privacy postconditions.  It never builds a candidate,
runs a command, chooses policy, or emits inventory/support claims.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat

from .artifact_manifest import ArtifactManifest
from .component_evidence_io import (
    _MAX_EVIDENCE_FILE_BYTES,
    _MAX_SYMLINK_TARGET_BYTES,
    _MAX_TOTAL_EVIDENCE_BYTES,
    _collect_index,
    _exclusive_output_path,
    _open_root_directory,
    _read_descriptor,
    _read_root_bytes,
    _real_directory,
    _same_identity,
    _write_evidence,
)
from .component_inventory import _load_composite_component_metadata
from .contracts import (
    ReleaseContractError,
    canonical_json_bytes,
)


_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_V3_SOURCE_KINDS = frozenset(
    {
        "conda-package-file",
        "debian-package-file",
        "easyqc-source-file",
        "pyinstaller-generated",
        "python-distribution-file",
        "python-symlink-alias",
        "verified-cursor-deb",
    }
)


def _ensure_ecosystem_root(
    build_dir: Path,
    ecosystem: str,
    *,
    allow_existing: bool,
) -> Path:
    components_root = Path(build_dir) / "evidence/components"
    _real_directory(components_root, "component evidence directory")
    root = components_root / ecosystem
    if root.exists() or root.is_symlink():
        if not allow_existing or root.is_symlink() or not root.is_dir():
            raise ReleaseContractError(
                f"{ecosystem} component evidence already exists or is unsafe"
            )
        return root
    try:
        root.mkdir()
    except OSError as exc:
        raise ReleaseContractError(
            f"cannot create {ecosystem} component evidence: {exc}"
        ) from exc
    return root


def _retain(
    ecosystem_root: Path,
    packet_root: Path,
    relative_path: str,
    data: bytes,
    *,
    kind: str,
    source_locator: str,
    label: str,
) -> dict[str, object]:
    destination = _exclusive_output_path(
        ecosystem_root,
        relative_path,
        label,
    )
    identity = _write_evidence(destination, data, label)
    return {
        "kind": kind,
        "retained_path": destination.relative_to(packet_root).as_posix(),
        "sha256": identity["sha256"],
        "size": identity["size"],
        "source_locator": source_locator,
    }


def _cursor_target_basename(target: object, label: str) -> str:
    if (
        not isinstance(target, str)
        or not target
        or "\\" in target
        or "\x00" in target
    ):
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
    if (
        target_path.is_absolute()
        or windows_target.is_absolute()
        or bool(windows_target.drive)
        or target_path.as_posix() != target
        or len(target_path.parts) != 1
        or target_path.parts[0] in {".", ".."}
    ):
        raise ReleaseContractError(
            f"{label} symlink target must be one canonical relative basename"
        )
    return target


def _read_cursor_leaf_symlink_once(
    root: Path,
    logical_path: PurePosixPath,
    label: str,
) -> tuple[bytes, os.stat_result, str, os.stat_result]:
    if logical_path.is_absolute() or not logical_path.parts or any(
        part in {"", ".", ".."} or "\\" in part or "\x00" in part
        for part in logical_path.parts
    ):
        raise ReleaseContractError(f"{label} path is not safe for no-follow I/O")
    if not hasattr(os, "O_NOFOLLOW"):
        raise ReleaseContractError(f"{label} requires no-follow file I/O")
    parent = PurePosixPath(*logical_path.parts[:-1])
    leaf = logical_path.parts[-1]
    parent_descriptor = _open_root_directory(root, parent, label)
    target_descriptor: int | None = None
    try:
        try:
            link_before = os.stat(
                leaf,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise ReleaseContractError(f"{label} symlink is missing or changed") from exc
        if not stat.S_ISLNK(link_before.st_mode):
            raise ReleaseContractError(f"{label} must be a leaf symlink")
        try:
            target = _cursor_target_basename(
                os.readlink(leaf, dir_fd=parent_descriptor),
                label,
            )
            link_after_read = os.stat(
                leaf,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise ReleaseContractError(
                f"cannot inspect {label} symlink target"
            ) from exc
        if not _same_identity(link_before, link_after_read):
            raise ReleaseContractError(f"{label} symlink changed while read")

        try:
            target_before = os.stat(
                target,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise ReleaseContractError(f"{label} target is missing or changed") from exc
        if not stat.S_ISREG(target_before.st_mode):
            raise ReleaseContractError(f"{label} target is not a regular file")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | os.O_NOFOLLOW
        )
        try:
            target_descriptor = os.open(
                target,
                flags,
                dir_fd=parent_descriptor,
            )
        except OSError as exc:
            raise ReleaseContractError(
                f"{label} target is missing, changed, or an unsafe symlink"
            ) from exc
        try:
            target_opened = os.fstat(target_descriptor)
        except OSError as exc:
            raise ReleaseContractError(
                f"cannot inspect opened {label} target"
            ) from exc
        if (
            not stat.S_ISREG(target_opened.st_mode)
            or not _same_identity(target_before, target_opened)
        ):
            raise ReleaseContractError(f"{label} target changed while opening")
        target_bytes, target_read = _read_descriptor(
            target_descriptor,
            label,
            maximum_bytes=_MAX_EVIDENCE_FILE_BYTES,
        )
        if not _same_identity(target_opened, target_read):
            raise ReleaseContractError(f"{label} target changed while read")
        try:
            target_after = os.stat(
                target,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            link_after = os.stat(
                leaf,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise ReleaseContractError(f"{label} changed after it was read") from exc
        if (
            not _same_identity(target_before, target_after)
            or not _same_identity(link_before, link_after)
        ):
            raise ReleaseContractError(f"{label} changed after it was read")
        return target_bytes, link_before, target, target_before
    finally:
        if target_descriptor is not None:
            os.close(target_descriptor)
        os.close(parent_descriptor)


def _read_cursor_leaf_symlink(
    root: Path,
    logical_path: PurePosixPath,
    label: str,
) -> tuple[bytes, dict[str, object]]:
    first_bytes, first_link, first_target, first_target_stat = (
        _read_cursor_leaf_symlink_once(root, logical_path, label)
    )
    second_bytes, second_link, second_target, second_target_stat = (
        _read_cursor_leaf_symlink_once(root, logical_path, label)
    )
    if (
        first_target != second_target
        or not _same_identity(first_link, second_link)
        or not _same_identity(first_target_stat, second_target_stat)
        or first_bytes != second_bytes
    ):
        raise ReleaseContractError(f"{label} changed after it was read")
    return first_bytes, {
        "entry_type": "regular-file",
        "sha256": hashlib.sha256(first_bytes).hexdigest(),
        "size": len(first_bytes),
    }


def capture_easyqc_component(
    source_root: Path,
    build_dir: Path,
    manifest: ArtifactManifest,
    release_version: str,
    source_revision: str,
) -> dict[str, object]:
    """Authenticate source evidence before creating the EasyQC component."""

    if not _VERSION_PATTERN.fullmatch(release_version):
        raise ReleaseContractError("EasyQC release version is invalid")
    if not _REVISION_PATTERN.fullmatch(source_revision):
        raise ReleaseContractError("EasyQC source revision is invalid")
    source_root = Path(source_root)
    build_dir = Path(build_dir)
    _real_directory(source_root, "authenticated EasyQC source root")
    try:
        if source_root.resolve(strict=True) != (build_dir / "source").resolve(
            strict=True
        ):
            raise ReleaseContractError(
                "authenticated EasyQC source root is outside the build packet"
            )
    except OSError as exc:
        raise ReleaseContractError(f"cannot resolve EasyQC source root: {exc}") from exc

    sources = (
        ("easyqc-build-spec", "easyqc.spec", "build-spec/easyqc.spec"),
        ("easyqc-license", "LICENSE", "LICENSE"),
        (
            "easyqc-source-template",
            "template/hcpall_template.scene",
            "template/hcpall_template.scene",
        ),
    )
    planned: list[tuple[str, str, str, bytes, dict[str, object]]] = []
    total = 0
    for kind, source_relative, retained_relative in sources:
        data, identity = _read_root_bytes(
            source_root,
            PurePosixPath(source_relative),
            f"EasyQC {kind}",
        )
        total += len(data)
        if total > _MAX_TOTAL_EVIDENCE_BYTES:
            raise ReleaseContractError("EasyQC component evidence exceeds limit")
        planned.append(
            (kind, source_relative, retained_relative, data, identity)
        )

    template_identity = next(
        identity
        for kind, _source, _retained, _data, identity in planned
        if kind == "easyqc-source-template"
    )
    candidate_template = next(
        (
            entry
            for entry in manifest.entries
            if entry.path == "_internal/template/hcpall_template.scene"
        ),
        None,
    )
    if (
        candidate_template is None
        or candidate_template.entry_type != "regular-file"
        or candidate_template.sha256 != template_identity["sha256"]
        or candidate_template.size != template_identity["size"]
    ):
        raise ReleaseContractError(
            "EasyQC source template does not match the finalized candidate"
        )

    identity_bytes = canonical_json_bytes(
        {
            "schema": "easyqc-source-identity-v1",
            "revision": source_revision,
            "version": release_version,
        }
    )
    planned.append(
        (
            "easyqc-source-identity",
            f"source-revision/{source_revision}",
            "source-identity.json",
            identity_bytes,
            {
                "entry_type": "regular-file",
                "sha256": hashlib.sha256(identity_bytes).hexdigest(),
                "size": len(identity_bytes),
            },
        )
    )
    planned.sort(key=lambda row: (row[0], row[1], row[2]))

    easyqc_root = _ensure_ecosystem_root(
        build_dir,
        "easyqc",
        allow_existing=False,
    )
    packet_root = build_dir.parent.resolve(strict=True)
    evidence: list[dict[str, object]] = []
    for kind, source_locator, retained_relative, data, expected in planned:
        row = _retain(
            easyqc_root,
            packet_root,
            f"easyqc-{release_version}/{retained_relative}",
            data,
            kind=kind,
            source_locator=f"easyqc-source/{source_locator}",
            label=f"EasyQC {kind}",
        )
        if row["sha256"] != expected["sha256"] or row["size"] != expected["size"]:
            raise ReleaseContractError(f"EasyQC {kind} changed before retention")
        evidence.append(row)
    for kind, source_relative, _retained, expected_data, expected_identity in planned:
        if kind == "easyqc-source-identity":
            continue
        current_data, current_identity = _read_root_bytes(
            source_root,
            PurePosixPath(source_relative),
            f"final EasyQC {kind}",
        )
        if current_data != expected_data or current_identity != expected_identity:
            raise ReleaseContractError(f"EasyQC {kind} changed before publication")

    component_id = f"application:easyqc@{release_version}"
    return {
        "collected_files": [],
        "component_id": component_id,
        "ecosystem": "easyqc",
        "evidence_files": evidence,
        "license_candidates": [
            {"field": "tracked-license", "value": "MIT License"}
        ],
        "name": "EasyQC",
        "provider_candidates": [],
        "purl": f"pkg:generic/easyqc@{release_version}",
        "type": "application",
        "version": release_version,
    }


def capture_cursor_component(
    cursor_runtime: object,
    build_dir: Path,
    manifest: ArtifactManifest,
    *,
    soname: str,
    library_sha256: str,
    reviewed_notice_path: Path,
    package: str,
    version: str,
    architecture: str,
    deb_sha256: str,
    provenance: bytes,
) -> dict[str, object]:
    """Create one component from the already verified input-deb runtime."""

    library_path = getattr(cursor_runtime, "library_path", None)
    copyright_path = getattr(cursor_runtime, "copyright_path", None)
    if not isinstance(library_path, Path) or not isinstance(copyright_path, Path):
        raise ReleaseContractError("verified cursor runtime is invalid")
    build_dir = Path(build_dir)
    sysroot = build_dir / "linux-cursor-sysroot"
    _real_directory(sysroot, "verified cursor sysroot")
    sysroot_absolute = Path(os.path.abspath(os.fspath(sysroot)))
    sysroot_resolved = sysroot.resolve(strict=True)
    verified_relative: dict[str, PurePosixPath] = {}
    for path, label in (
        (library_path, "cursor library"),
        (copyright_path, "cursor copyright"),
    ):
        try:
            absolute = Path(os.path.abspath(os.fspath(path)))
            relative = absolute.relative_to(sysroot_absolute)
        except ValueError as exc:
            raise ReleaseContractError(f"verified {label} escapes cursor sysroot") from exc
        verified_relative[label] = PurePosixPath(relative.as_posix())

    library_relative = verified_relative["cursor library"]
    copyright_relative = verified_relative["cursor copyright"]
    library_verified = _read_cursor_leaf_symlink(
        sysroot,
        library_relative,
        "verified cursor library",
    )
    try:
        copyright_resolved = Path(
            os.path.abspath(os.fspath(copyright_path))
        ).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ReleaseContractError(
            f"cannot resolve verified cursor copyright: {exc}"
        ) from exc
    if not copyright_resolved.is_relative_to(sysroot_resolved):
        raise ReleaseContractError("verified cursor copyright escapes cursor sysroot")
    copyright_verified = _read_root_bytes(
        sysroot,
        copyright_relative,
        "verified cursor copyright",
    )
    library_bytes, library_identity = library_verified
    if library_path.name != soname or library_identity["sha256"] != library_sha256:
        raise ReleaseContractError("verified cursor library identity mismatch")
    candidate_cursor = next(
        (entry for entry in manifest.entries if entry.path == f"_internal/{soname}"),
        None,
    )
    if (
        candidate_cursor is None
        or candidate_cursor.entry_type != "regular-file"
        or candidate_cursor.sha256 != hashlib.sha256(library_bytes).hexdigest()
        or candidate_cursor.size != len(library_bytes)
    ):
        raise ReleaseContractError(
            "verified cursor library does not match the finalized candidate"
        )
    notice_path = Path(reviewed_notice_path)
    _real_directory(notice_path.parent, "reviewed cursor notice parent")
    reviewed_notice, reviewed_identity = _read_root_bytes(
        notice_path.parent,
        PurePosixPath(notice_path.name),
        "reviewed cursor notice",
    )
    copyright_bytes, copyright_identity = copyright_verified
    if copyright_bytes != reviewed_notice or copyright_identity != reviewed_identity:
        raise ReleaseContractError("verified cursor copyright does not match notice")
    if len(copyright_bytes) + len(provenance) > _MAX_TOTAL_EVIDENCE_BYTES:
        raise ReleaseContractError("cursor component evidence exceeds limit")

    debian_root = _ensure_ecosystem_root(
        build_dir,
        "debian",
        allow_existing=True,
    )
    packet_root = build_dir.parent.resolve(strict=True)
    component_directory = f"{package}-{version}"
    evidence = [
        _retain(
            debian_root,
            packet_root,
            f"{component_directory}/copyright",
            copyright_bytes,
            kind="cursor-copyright",
            source_locator="verified-cursor-deb/usr/share/doc/copyright",
            label="verified cursor copyright",
        ),
        _retain(
            debian_root,
            packet_root,
            f"{component_directory}/provenance.txt",
            provenance,
            kind="cursor-provenance",
            source_locator=f"verified-cursor-deb/{deb_sha256}",
            label="verified cursor provenance",
        ),
    ]
    current_library = _read_cursor_leaf_symlink(
        sysroot,
        library_relative,
        "final verified cursor library",
    )
    if current_library != library_verified:
        raise ReleaseContractError(
            "verified cursor library changed before publication"
        )
    current_copyright = _read_root_bytes(
        sysroot,
        copyright_relative,
        "final verified cursor copyright",
    )
    if current_copyright != copyright_verified:
        raise ReleaseContractError(
            "verified cursor copyright changed before publication"
        )
    component_id = f"library:{package}@{version}"
    return {
        "collected_files": [],
        "component_id": component_id,
        "ecosystem": "debian",
        "evidence_files": evidence,
        "license_candidates": [
            {"field": "verified-license", "value": "MIT/X Consortium License"}
        ],
        "name": package,
        "provider_candidates": [
            {"field": "distribution", "value": "Ubuntu 22.04 x86_64"},
            {"field": "source-package-sha256", "value": deb_sha256},
        ],
        "purl": f"pkg:deb/ubuntu/{package}@{version}?arch={architecture}",
        "type": "library",
        "version": version,
    }


def merge_component_maps(
    existing: dict[str, dict[str, object]],
    incoming: dict[str, dict[str, object]],
    label: str,
) -> None:
    if not isinstance(incoming, dict):
        raise ReleaseContractError(f"{label} components must be an object")
    for component_id in sorted(incoming):
        component = incoming[component_id]
        if (
            not isinstance(component_id, str)
            or not component_id
            or not isinstance(component, dict)
            or component.get("component_id") != component_id
        ):
            raise ReleaseContractError(f"{label} component identity is invalid")
        if component_id in existing:
            raise ReleaseContractError(f"duplicate component ID: {component_id}")
        existing[component_id] = component


def append_component_assignments(
    components: dict[str, dict[str, object]],
    assignments: list[dict[str, object]],
    label: str,
) -> None:
    for assignment in assignments:
        if not isinstance(assignment, dict) or set(assignment) != {
            "collected_file",
            "component_id",
        }:
            raise ReleaseContractError(f"{label} assignment fields are invalid")
        component_id = assignment["component_id"]
        component = components.get(component_id)
        if component is None:
            raise ReleaseContractError(
                f"{label} assignment component is missing: {component_id}"
            )
        collected = component.get("collected_files")
        if not isinstance(collected, list) or not isinstance(
            assignment["collected_file"], dict
        ):
            raise ReleaseContractError(f"{label} collected assignment is invalid")
        collected.append(assignment["collected_file"])
        collected.sort(key=lambda row: str(row.get("final_path")))


def _iter_strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _iter_strings(key)
            yield from _iter_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_strings(item)


def validate_component_evidence_v3(
    metadata: dict[str, object],
    collect_rows: list[dict[str, object]],
    build_dir: Path,
    artifact: Path,
    source_root: Path,
    runtime_artifact: Path,
) -> None:
    """Enforce producer-only coverage/privacy after the strict consumer check."""

    build_dir = Path(build_dir)
    packet_root = build_dir.parent.resolve(strict=True)
    loaded = _load_composite_component_metadata(metadata, packet_root)
    collect_by_path = _collect_index(collect_rows, context="composite v3")
    assigned = loaded.assigned_collected_entries
    if loaded.unassigned_collected_entries:
        raise ReleaseContractError("composite v3 closed-world output must be assigned")
    assigned_paths = {str(row["final_path"]) for row in assigned}
    if assigned_paths != set(collect_by_path) or len(assigned) != len(collect_rows):
        raise ReleaseContractError("composite v3 COLLECT coverage is incomplete")
    for row in assigned:
        original = collect_by_path[str(row["final_path"])]
        if any(
            row[field] != original[field]
            for field in ("entry_type", "raw_source_text_sha256", "toc_type")
        ):
            raise ReleaseContractError(
                "composite v3 row disagrees with retained COLLECT"
            )
    source_kinds = {
        str(row["source_kind"])
        for component in metadata["components"]
        for row in component["collected_files"]
    }
    if source_kinds != set(_V3_SOURCE_KINDS):
        raise ReleaseContractError(
            "composite v3 must cover all seven authenticated source kinds"
        )
    if any(not component["evidence_files"] for component in metadata["components"]):
        raise ReleaseContractError("composite v3 component has no evidence")

    for value in _iter_strings(metadata):
        posix = PurePosixPath(value)
        windows = PureWindowsPath(value)
        if posix.is_absolute() or windows.is_absolute() or bool(windows.drive):
            raise ReleaseContractError("composite v3 contains a host-private path")
    canonical = canonical_json_bytes(metadata)
    try:
        runtime_path = Path(runtime_artifact).resolve(strict=True)
    except OSError as exc:
        raise ReleaseContractError(f"cannot resolve runtime artifact: {exc}") from exc
    private_values = {
        str(Path.home()),
        str(Path(artifact).resolve(strict=True)),
        str(build_dir.resolve(strict=True)),
        str(packet_root),
        str(packet_root.parent.resolve(strict=True)),
        str(Path(source_root).resolve(strict=True)),
        str(runtime_path),
        str(runtime_path.parent),
        "/var/lib/dpkg",
        "/.cache/",
    }
    if any(value and value.encode("utf-8") in canonical for value in private_values):
        raise ReleaseContractError("composite v3 privacy validation failed")
