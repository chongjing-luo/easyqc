"""Exact build-time contributor evidence for installed Debian packages."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import quote

from .component_evidence_io import (
    _MAX_TOTAL_EVIDENCE_BYTES,
    _bytes_identity,
    _canonical_absolute_path,
    _canonical_relative_path,
    _collect_index,
    _exclusive_output_path,
    _existing_owner_paths,
    _hash_resolved_root_file,
    _read_resolved_root_bytes,
    _real_directory,
    _resolved_root_inode,
    _validate_build_dir,
    _write_evidence,
)
from .contracts import ReleaseContractError


_SYSTEM_ROOT = Path("/")
_DPKG_QUERY = "/usr/bin/dpkg-query"
_DPKG_ENV = {
    "LC_ALL": "C.UTF-8",
    "LANG": "C.UTF-8",
    "LANGUAGE": "C",
}
_DPKG_STATUS_FORMAT = (
    "${Package}\\t${binary:Package}\\t${db:Status-Abbrev}\\t${Version}\\t"
    "${Architecture}\\t${source:Package}\\t${source:Version}\\t${Maintainer}\\t"
    "${Original-Maintainer}\\n"
)
_MAX_SUBPROCESS_BYTES = 16 * 1024 * 1024
_MAX_DPKG_ARGV_BYTES = 1024 * 1024
_MAX_SYSTEM_PATH_BYTES = 4096
_DPKG_TIMEOUT_SECONDS = 30
_PACKAGE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
_OWNER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.-]*(?::[a-z0-9][a-z0-9-]*)?$")
_ARCH_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9.+:~_-]+$")
_VERSION_LINE_PATTERN = re.compile(
    rb"Debian dpkg-query package management program query tool version "
    rb"[0-9]+(?:\.[0-9]+)+ \([A-Za-z0-9_-]+\)\.\n"
)
_QUERY_CONTROL_PATTERN = re.compile(r"[*?\[\]\x00-\x1f\x7f]")
_SYSTEM_PREFIXES = frozenset(
    {
        ("bin",),
        ("etc",),
        ("lib",),
        ("lib64",),
        ("sbin",),
        ("usr", "bin"),
        ("usr", "lib"),
        ("usr", "lib64"),
        ("usr", "sbin"),
    }
)
_MERGED_USR_PAIRS = (
    (("lib",), ("usr", "lib")),
    (("lib64",), ("usr", "lib64")),
    (("bin",), ("usr", "bin")),
    (("sbin",), ("usr", "sbin")),
)


@dataclass(frozen=True)
class _Candidate:
    final_path: str
    logical_path: PurePosixPath
    query_paths: tuple[str, ...]
    row: dict[str, object]
    source_identity: dict[str, object]
    source_inode: tuple[int, int]


@dataclass(frozen=True)
class _DebianPackage:
    owner: str
    package: str
    binary_package: str
    status: str
    version: str
    architecture: str
    source_package: str
    source_version: str
    maintainer: str
    original_maintainer: str
    status_line: bytes

    @property
    def component_id(self) -> str:
        return f"library:{self.package}@{self.version}"

    @property
    def package_id(self) -> str:
        return f"{self.package}-{self.version}-{self.architecture}"


def _run_dpkg(command: list[str], label: str) -> subprocess.CompletedProcess[bytes]:
    if not command or command[0] != _DPKG_QUERY or not os.path.isabs(command[0]):
        raise ReleaseContractError(f"{label} command is not absolute")
    try:
        command_bytes = sum(
            len(argument.encode("utf-8")) + 1 for argument in command
        )
    except (AttributeError, UnicodeEncodeError) as exc:
        raise ReleaseContractError(f"{label} command is not valid UTF-8") from exc
    if any("\x00" in argument for argument in command) or (
        command_bytes > _MAX_DPKG_ARGV_BYTES
    ):
        raise ReleaseContractError(f"{label} command exceeds argument limit")
    try:
        result = subprocess.run(
            command,
            check=False,
            env=_DPKG_ENV,
            shell=False,
            stderr=subprocess.PIPE,
            stdout=subprocess.PIPE,
            timeout=_DPKG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ReleaseContractError(f"{label} timed out") from exc
    except OSError as exc:
        raise ReleaseContractError(f"cannot run {label}: {exc}") from exc
    if not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes):
        raise ReleaseContractError(f"{label} did not return byte evidence")
    if (
        len(result.stdout) > _MAX_SUBPROCESS_BYTES
        or len(result.stderr) > _MAX_SUBPROCESS_BYTES
    ):
        raise ReleaseContractError(f"{label} output exceeds evidence limit")
    return result


def _dpkg_version() -> bytes:
    result = _run_dpkg([_DPKG_QUERY, "--version"], "dpkg-query version")
    if result.returncode != 0 or result.stderr:
        raise ReleaseContractError("dpkg-query version evidence is invalid")
    if not result.stdout.endswith(b"\n") or b"\x00" in result.stdout:
        raise ReleaseContractError("dpkg-query version output is malformed")
    first_line = result.stdout.splitlines(keepends=True)[0] if result.stdout else b""
    if _VERSION_LINE_PATTERN.fullmatch(first_line) is None:
        raise ReleaseContractError("dpkg-query version output is malformed")
    try:
        result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseContractError("dpkg-query version output is not UTF-8") from exc
    return result.stdout


def _logical_source_path(root: Path, raw_source: str) -> PurePosixPath | None:
    if not isinstance(raw_source, str):
        return None
    try:
        raw_source_bytes = raw_source.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ReleaseContractError("Debian COLLECT source is not valid UTF-8") from exc
    if len(raw_source_bytes) > _MAX_SYSTEM_PATH_BYTES:
        raise ReleaseContractError("Debian COLLECT source exceeds path limit")
    source_path = PurePosixPath(raw_source)
    if not source_path.is_absolute():
        return None
    if (
        "\x00" in raw_source
        or "\\" in raw_source
        or source_path.as_posix() != raw_source
    ):
        raise ReleaseContractError("Debian COLLECT source path is malformed")
    source = Path(raw_source)
    try:
        relative = source.relative_to(root)
    except ValueError:
        return None
    if not relative.parts:
        return None
    prefix_matches = any(
        relative.parts[: len(prefix)] == prefix for prefix in _SYSTEM_PREFIXES
    )
    if not prefix_matches:
        return None
    logical = PurePosixPath("/", *relative.parts)
    canonical = _canonical_absolute_path(
        logical.as_posix(),
        "Debian COLLECT source",
    )
    if _QUERY_CONTROL_PATTERN.search(canonical.as_posix()):
        raise ReleaseContractError("Debian COLLECT source contains query syntax")
    return canonical


def _query_paths(logical: PurePosixPath) -> tuple[str, ...]:
    relative = logical.parts[1:]
    candidates = {logical.as_posix()}
    for first, second in _MERGED_USR_PAIRS:
        if relative[: len(first)] == first:
            candidates.add(PurePosixPath("/", *second, *relative[len(first) :]).as_posix())
        elif relative[: len(second)] == second:
            candidates.add(PurePosixPath("/", *first, *relative[len(second) :]).as_posix())
    for candidate in candidates:
        _canonical_absolute_path(candidate, "dpkg-query owner path")
    return tuple(sorted(candidates))


def _owner_records(
    query_paths: tuple[str, ...],
) -> dict[str, list[tuple[tuple[str, ...], bytes]]]:
    result = _run_dpkg(
        [_DPKG_QUERY, "-S", *query_paths],
        "dpkg-query owner search",
    )
    if result.returncode not in {0, 1}:
        raise ReleaseContractError("dpkg-query owner search failed")
    records = {path: [] for path in query_paths}
    for raw_line in result.stdout.splitlines(keepends=True):
        if not raw_line.endswith(b"\n") or raw_line.endswith(b"\r\n"):
            raise ReleaseContractError("dpkg-query owner output is malformed")
        try:
            line = raw_line[:-1].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseContractError("dpkg-query owner output is not UTF-8") from exc
        if "\x00" in line or ": " not in line:
            raise ReleaseContractError("dpkg-query owner output is malformed")
        owner_text, matched_path = line.rsplit(": ", 1)
        if matched_path not in records:
            raise ReleaseContractError("dpkg-query returned an unexpected owner path")
        owners = tuple(owner_text.split(", "))
        if (
            not owners
            or len(owners) != len(set(owners))
            or any(_OWNER_PATTERN.fullmatch(owner) is None for owner in owners)
        ):
            raise ReleaseContractError("dpkg-query owner output is malformed")
        records[matched_path].append((owners, raw_line))
    if result.stdout and not result.stdout.endswith(b"\n"):
        raise ReleaseContractError("dpkg-query owner output is malformed")
    return records


def _status_records(owners: tuple[str, ...]) -> dict[str, _DebianPackage]:
    result = _run_dpkg(
        [_DPKG_QUERY, "-W", f"--showformat={_DPKG_STATUS_FORMAT}", *owners],
        "dpkg-query installed status",
    )
    if result.returncode != 0 or result.stderr:
        raise ReleaseContractError("dpkg-query installed status failed")
    requested = set(owners)
    parsed: dict[str, _DebianPackage] = {}
    for raw_line in result.stdout.splitlines(keepends=True):
        if not raw_line.endswith(b"\n") or raw_line.endswith(b"\r\n"):
            raise ReleaseContractError("dpkg-query installed status is malformed")
        try:
            fields = raw_line[:-1].decode("utf-8").split("\t")
        except UnicodeDecodeError as exc:
            raise ReleaseContractError("dpkg-query installed status is not UTF-8") from exc
        if len(fields) != 9 or any("\x00" in field for field in fields):
            raise ReleaseContractError("dpkg-query installed status is malformed")
        (
            package,
            binary_package,
            status,
            version,
            architecture,
            source_package,
            source_version,
            maintainer,
            original_maintainer,
        ) = fields
        matching = requested.intersection({package, binary_package})
        if len(matching) != 1:
            raise ReleaseContractError("dpkg-query status owner mismatch")
        owner = next(iter(matching))
        if owner in parsed:
            raise ReleaseContractError("duplicate dpkg-query installed status")
        if (
            _PACKAGE_PATTERN.fullmatch(package) is None
            or _OWNER_PATTERN.fullmatch(binary_package) is None
            or status != "ii "
            or _VERSION_PATTERN.fullmatch(version) is None
            or _ARCH_PATTERN.fullmatch(architecture) is None
            or (source_package and _PACKAGE_PATTERN.fullmatch(source_package) is None)
            or (source_version and _VERSION_PATTERN.fullmatch(source_version) is None)
            or bool(source_package) != bool(source_version)
            or not maintainer
        ):
            raise ReleaseContractError("dpkg-query installed status is invalid")
        parsed[owner] = _DebianPackage(
            owner=owner,
            package=package,
            binary_package=binary_package,
            status=status,
            version=version,
            architecture=architecture,
            source_package=source_package,
            source_version=source_version,
            maintainer=maintainer,
            original_maintainer=original_maintainer,
            status_line=raw_line,
        )
    if set(parsed) != requested:
        raise ReleaseContractError("dpkg-query status owner mismatch")
    return parsed


def _license_candidates(copyright_bytes: bytes) -> list[dict[str, str]]:
    try:
        text = copyright_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseContractError("Debian copyright is not UTF-8") from exc
    values = {
        line[len("License:") :].strip()
        for line in text.splitlines()
        if line.startswith("License:") and line[len("License:") :].strip()
    }
    return [
        {"field": "copyright-License", "value": value}
        for value in sorted(values)
    ]


def _provider_candidates(package: _DebianPackage) -> list[dict[str, str]]:
    result = [{"field": "Maintainer", "value": package.maintainer}]
    if package.original_maintainer:
        result.append(
            {
                "field": "Original-Maintainer",
                "value": package.original_maintainer,
            }
        )
    if package.source_package:
        result.append(
            {
                "field": "Source",
                "value": f"{package.source_package} ({package.source_version})",
            }
        )
    return result


def capture_debian_component_evidence(
    collect_rows: list[dict[str, object]],
    existing_components: dict[str, dict[str, object]],
    build_dir: Path,
) -> dict[str, dict[str, object]]:
    """Capture exact installed Debian owners for unassigned system sources.

    Only canonical sources in the approved system path domains participate.
    All ownership, installed-state, merged-/usr identity, source bytes and
    copyright bytes are authenticated before exclusive evidence writes begin.
    """

    build, debian_root = _validate_build_dir(build_dir, ecosystem="debian")
    system_root = Path(os.path.abspath(os.fspath(_SYSTEM_ROOT)))
    _real_directory(system_root, "Debian system root")
    collect_by_path = _collect_index(collect_rows, context="Debian")
    already_owned = _existing_owner_paths(existing_components)
    if any(path not in collect_by_path for path in already_owned):
        raise ReleaseContractError(
            "existing component owns a path outside retained COLLECT"
        )

    candidates: list[_Candidate] = []
    for final_path in sorted(collect_by_path):
        if final_path in already_owned:
            continue
        row = collect_by_path[final_path]
        if row["entry_type"] != "regular-file":
            continue
        logical = _logical_source_path(system_root, str(row["raw_source"]))
        if logical is None:
            continue
        source_identity, source_inode = _hash_resolved_root_file(
            system_root,
            logical,
            f"Debian contributor {logical.as_posix()}",
        )
        candidates.append(
            _Candidate(
                final_path=final_path,
                logical_path=logical,
                query_paths=_query_paths(logical),
                row=row,
                source_identity=source_identity,
                source_inode=source_inode,
            )
        )

    if not candidates:
        return {}

    version_bytes = _dpkg_version()
    all_query_paths = tuple(
        sorted({path for candidate in candidates for path in candidate.query_paths})
    )
    owner_records = _owner_records(all_query_paths)
    candidate_owners: dict[str, str] = {}
    candidate_owner_lines: dict[str, tuple[bytes, ...]] = {}
    for candidate in candidates:
        matching_records: list[tuple[tuple[str, ...], bytes]] = []
        owners: set[str] = set()
        for query_path in candidate.query_paths:
            for record_owners, raw_line in owner_records[query_path]:
                query_inode = _resolved_root_inode(
                    system_root,
                    PurePosixPath(query_path),
                    f"dpkg-query matched path {query_path}",
                )
                if query_inode != candidate.source_inode:
                    raise ReleaseContractError(
                        "merged-/usr owner path is not the same installed file"
                    )
                matching_records.append((record_owners, raw_line))
                owners.update(record_owners)
        if len(owners) != 1:
            raise ReleaseContractError(
                f"Debian source has no unique installed owner: {candidate.logical_path}"
            )
        owner = next(iter(owners))
        candidate_owners[candidate.final_path] = owner
        candidate_owner_lines[candidate.final_path] = tuple(
            sorted({raw_line for _record_owners, raw_line in matching_records})
        )

    packages_by_owner = _status_records(tuple(sorted(set(candidate_owners.values()))))
    assignments: dict[str, list[dict[str, object]]] = {}
    contributing: dict[str, _DebianPackage] = {}
    owner_lines_by_component: dict[str, set[bytes]] = {}
    candidate_by_final = {candidate.final_path: candidate for candidate in candidates}
    for final_path in sorted(candidate_owners):
        candidate = candidate_by_final[final_path]
        owner = candidate_owners[final_path]
        package = packages_by_owner[owner]
        previous = contributing.get(package.component_id)
        if previous is not None and previous != package:
            raise ReleaseContractError(
                f"duplicate Debian component identity: {package.component_id}"
            )
        matched_paths = sorted(
            path
            for path in candidate.query_paths
            if owner_records[path]
        )
        if not matched_paths:
            raise ReleaseContractError("Debian owner evidence lost its matched path")
        package_path = _canonical_relative_path(
            matched_paths[0].removeprefix("/"),
            "Debian package member",
        ).as_posix()
        assignments.setdefault(package.component_id, []).append(
            {
                "entry_type": "regular-file",
                "final_path": final_path,
                "package_path": package_path,
                "raw_source_text_sha256": candidate.row["raw_source_text_sha256"],
                "source_identity": candidate.source_identity,
                "source_kind": "debian-package-file",
                "source_locator": (
                    f"debian-package/{package.package_id}/{package_path}"
                ),
                "target_final_path": None,
                "toc_type": candidate.row["toc_type"],
            }
        )
        contributing[package.component_id] = package
        owner_lines_by_component.setdefault(package.component_id, set()).update(
            candidate_owner_lines[final_path]
        )

    planned_evidence: dict[
        str,
        list[tuple[str, str, str, bytes, dict[str, object]]],
    ] = {}
    license_candidates: dict[str, list[dict[str, str]]] = {}
    total_evidence_bytes = 0
    for component_id in sorted(contributing):
        package = contributing[component_id]
        copyright_logical = PurePosixPath(
            "/usr/share/doc",
            package.package,
            "copyright",
        )
        copyright_bytes, copyright_identity = _read_resolved_root_bytes(
            system_root,
            copyright_logical,
            f"Debian copyright {package.component_id}",
        )
        license_candidates[component_id] = _license_candidates(copyright_bytes)
        owner_bytes = b"".join(sorted(owner_lines_by_component[component_id]))
        rows = [
            (
                "debian-copyright",
                f"debian-system/{copyright_logical.as_posix().removeprefix('/')}",
                f"{package.package_id}/copyright",
                copyright_bytes,
                copyright_identity,
            ),
            (
                "debian-installed-status",
                f"dpkg-query/-W/{package.owner}",
                f"{package.package_id}/installed-status.txt",
                package.status_line,
                _bytes_identity(package.status_line),
            ),
            (
                "debian-owner-query",
                f"dpkg-query/-S/{package.package_id}",
                f"{package.package_id}/owner-query.txt",
                owner_bytes,
                _bytes_identity(owner_bytes),
            ),
            (
                "dpkg-query-version",
                "dpkg-query/--version",
                f"{package.package_id}/dpkg-query-version.txt",
                version_bytes,
                _bytes_identity(version_bytes),
            ),
        ]
        rows.sort(key=lambda item: (item[0], item[1], item[2]))
        total_evidence_bytes += sum(len(item[3]) for item in rows)
        if total_evidence_bytes > _MAX_TOTAL_EVIDENCE_BYTES:
            raise ReleaseContractError("Debian component evidence exceeds aggregate limit")
        planned_evidence[component_id] = rows

    for candidate in candidates:
        final_identity, final_inode = _hash_resolved_root_file(
            system_root,
            candidate.logical_path,
            f"final Debian contributor {candidate.logical_path.as_posix()}",
        )
        if final_identity != candidate.source_identity or final_inode != candidate.source_inode:
            raise ReleaseContractError("Debian contributor changed before evidence write")

    try:
        debian_root.mkdir()
    except OSError as exc:
        raise ReleaseContractError(f"cannot create Debian evidence directory: {exc}") from exc

    evidence_by_component: dict[str, list[dict[str, object]]] = {}
    for component_id in sorted(planned_evidence):
        evidence_rows: list[dict[str, object]] = []
        for kind, source_locator, retained_relative, data, source_identity in (
            planned_evidence[component_id]
        ):
            destination = _exclusive_output_path(
                debian_root,
                retained_relative,
                f"{component_id} {kind} evidence",
            )
            retained_identity = _write_evidence(
                destination,
                data,
                f"{component_id} {kind} evidence",
            )
            if retained_identity != source_identity:
                raise ReleaseContractError(f"retained {component_id} {kind} changed")
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
    for component_id in sorted(contributing):
        package = contributing[component_id]
        result[component_id] = {
            "collected_files": sorted(
                assignments[component_id],
                key=lambda row: str(row["final_path"]),
            ),
            "component_id": component_id,
            "ecosystem": "debian",
            "evidence_files": evidence_by_component[component_id],
            "license_candidates": license_candidates[component_id],
            "name": package.package,
            "provider_candidates": _provider_candidates(package),
            "purl": (
                f"pkg:deb/ubuntu/{quote(package.package, safe='')}@"
                f"{quote(package.version, safe='')}?arch="
                f"{quote(package.architecture, safe='')}"
            ),
            "type": "library",
            "version": package.version,
        }
    return result
