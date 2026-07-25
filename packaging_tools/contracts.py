"""Canonical, fail-loud contracts for one self-contained release input.

This module performs no dependency resolution, build, inventory, smoke test,
or project-data access. It authenticates the exact inputs that later release
stages are allowed to consume.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from types import MappingProxyType
from typing import Mapping


RELEASE_INPUT_SCHEMA = "easyqc-release-input-v1"
BUILD_RECEIPT_SCHEMA = "easyqc-release-build-receipt-v1"
APPROVED_RUNTIME_IMPLEMENTATION = "CPython"
APPROVED_RUNTIME_VERSION = "3.10.17"

TARGET_TRIPLES: Mapping[str, str] = MappingProxyType(
    {
        "linux-x86_64": "x86_64-manylinux_2_34",
        "windows-x86_64": "x86_64-pc-windows-msvc",
        "macos-arm64": "aarch64-apple-darwin",
    }
)

APPROVED_TARGET_LOCK_SHA256: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "linux-x86_64": MappingProxyType(
            {
                "runtime": "430b04a9a9311de9241fb914765e9ff1480c8d6c1377b2137ecb9e27973561b0",
                "build": "cce2a707233ba5eb4b97af405fa4c6e3a7c6ba9e39a2317bdafc5c15494122c6",
                "test": "72ed47ed6b911f3f9951c491f68bb452feb8d3926e8dba3137b9186300fce05b",
            }
        ),
        "windows-x86_64": MappingProxyType(
            {
                "runtime": "430b04a9a9311de9241fb914765e9ff1480c8d6c1377b2137ecb9e27973561b0",
                "build": "4f7c11404475759d0ba83144203ba7bc62027627b31d7bb29530408920baac96",
                "test": "25a8a04d4c34c935391d53d5b648f0d3e8c932b1a6054ef2835e487d32fa760b",
            }
        ),
        "macos-arm64": MappingProxyType(
            {
                "runtime": "430b04a9a9311de9241fb914765e9ff1480c8d6c1377b2137ecb9e27973561b0",
                "build": "f67ddf95fa08831a7d704de569794be9e21eb56d6be9948ee31102b974068d6f",
                "test": "72ed47ed6b911f3f9951c491f68bb452feb8d3926e8dba3137b9186300fce05b",
            }
        ),
    }
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]*$")
_LOCK_SCOPES = ("runtime", "build", "test")


class ReleaseContractError(ValueError):
    """Raised when release input is not exact, canonical, or self-contained."""


@dataclass(frozen=True)
class HashedFile:
    """One canonical path below the release-input root plus its expected hash."""

    label: str
    relative_path: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class RuntimeIdentity:
    """Authenticated identity of the target runtime artifact."""

    implementation: str
    version: str
    target: str
    target_triple: str
    provider: str
    source_provenance: str
    artifact: HashedFile
    identity_file: HashedFile


@dataclass(frozen=True)
class ReleaseInputBundle:
    """Parsed release input whose referenced file bytes are not yet trusted."""

    root: Path
    document_path: Path
    document_sha256: str
    target: str
    version: str
    source_revision: str
    source_archive: HashedFile
    runtime_identity_file: HashedFile
    lock_files: Mapping[str, HashedFile]
    component_policy: HashedFile
    target_extension: Mapping[str, HashedFile]


@dataclass(frozen=True)
class ValidatedReleaseInputs:
    """Release inputs authenticated before any build or network operation."""

    bundle: ReleaseInputBundle
    runtime_identity: RuntimeIdentity
    source_archive: Path
    lock_files: Mapping[str, Path]
    component_policy: Path
    linux_cursor_deb: Path | None


@dataclass(frozen=True)
class BuildReceipt:
    """Canonical PASS evidence for one finalized release candidate.

    The record binds exact build inputs and retained evidence to one Candidate
    ID.  It deliberately has no inventory, smoke, signing, publication, or
    target-support field.
    """

    status: str
    evidence_class: str
    target: str
    candidate_id: str
    artifact_path: str
    artifact_manifest_path: str
    artifact_manifest_sha256: str
    component_policy_path: str
    component_policy_sha256: str
    toc_index_path: str
    toc_index_sha256: str
    distribution_metadata_path: str
    distribution_metadata_sha256: str
    source_revision: str
    source_archive_sha256: str
    runtime_identity_sha256: str
    locks: Mapping[str, str]
    tools: Mapping[str, str]
    schema: str = BUILD_RECEIPT_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != BUILD_RECEIPT_SCHEMA:
            raise ReleaseContractError(
                f"BuildReceipt schema must be {BUILD_RECEIPT_SCHEMA}"
            )
        if self.status != "PASS":
            raise ReleaseContractError("BuildReceipt status must be PASS")
        if self.evidence_class not in {"fixture", "release"}:
            raise ReleaseContractError("BuildReceipt evidence_class is invalid")
        if self.target not in TARGET_TRIPLES:
            raise ReleaseContractError(f"unsupported BuildReceipt target: {self.target}")
        _validate_sha256(self.candidate_id, "BuildReceipt Candidate ID")
        if self.artifact_manifest_sha256 != self.candidate_id:
            raise ReleaseContractError(
                "BuildReceipt manifest digest must equal Candidate ID"
            )
        for value, label, prefix in (
            (self.artifact_path, "artifact.path", "artifact/"),
            (self.artifact_manifest_path, "artifact.manifest.path", "build/"),
            (self.component_policy_path, "component_policy.path", "build/"),
            (self.toc_index_path, "evidence.toc_index.path", "build/"),
            (
                self.distribution_metadata_path,
                "evidence.distribution_metadata.path",
                "build/",
            ),
        ):
            _validate_contained_relative_path(value, label, prefix=prefix)
        for value, label in (
            (self.artifact_manifest_sha256, "artifact.manifest.sha256"),
            (self.component_policy_sha256, "component_policy.sha256"),
            (self.toc_index_sha256, "evidence.toc_index.sha256"),
            (
                self.distribution_metadata_sha256,
                "evidence.distribution_metadata.sha256",
            ),
            (self.source_archive_sha256, "source.archive_sha256"),
            (self.runtime_identity_sha256, "runtime.identity_sha256"),
        ):
            _validate_sha256(value, label)
        if not _REVISION_PATTERN.fullmatch(self.source_revision):
            raise ReleaseContractError(
                "BuildReceipt source revision must be a lowercase full git SHA"
            )
        if set(self.locks) != set(_LOCK_SCOPES):
            raise ReleaseContractError(
                "BuildReceipt locks must contain runtime, build, and test"
            )
        for scope in _LOCK_SCOPES:
            _validate_sha256(self.locks[scope], f"BuildReceipt locks.{scope}")
        if set(self.tools) != {"python", "pyinstaller"}:
            raise ReleaseContractError(
                "BuildReceipt tools must contain python and pyinstaller"
            )
        for name in ("python", "pyinstaller"):
            if not isinstance(self.tools[name], str) or not self.tools[name]:
                raise ReleaseContractError(f"BuildReceipt tools.{name} is invalid")
        object.__setattr__(self, "locks", MappingProxyType(dict(self.locks)))
        object.__setattr__(self, "tools", MappingProxyType(dict(self.tools)))

    def as_json_object(self) -> dict[str, object]:
        """Return the sole canonical JSON representation."""

        def reference(path: str, digest: str) -> dict[str, str]:
            return {"path": path, "sha256": digest}

        return {
            "schema": self.schema,
            "status": self.status,
            "evidence_class": self.evidence_class,
            "target": self.target,
            "candidate_id": self.candidate_id,
            "artifact": {
                "path": self.artifact_path,
                "manifest": reference(
                    self.artifact_manifest_path,
                    self.artifact_manifest_sha256,
                ),
            },
            "component_policy": reference(
                self.component_policy_path,
                self.component_policy_sha256,
            ),
            "evidence": {
                "toc_index": reference(
                    self.toc_index_path,
                    self.toc_index_sha256,
                ),
                "distribution_metadata": reference(
                    self.distribution_metadata_path,
                    self.distribution_metadata_sha256,
                ),
            },
            "source": {
                "revision": self.source_revision,
                "archive_sha256": self.source_archive_sha256,
            },
            "runtime": {"identity_sha256": self.runtime_identity_sha256},
            "locks": {scope: self.locks[scope] for scope in _LOCK_SCOPES},
            "tools": {name: self.tools[name] for name in ("python", "pyinstaller")},
        }


def canonical_json_bytes(value: object) -> bytes:
    """Serialize one JSON value into the approved canonical byte form.

    Input: one JSON-compatible value.
    Output: UTF-8 JSON with sorted keys, compact separators, and one newline.
    Side effects: none.
    Errors: non-JSON values and NaN/Infinity raise ``ReleaseContractError``.
    Split trigger: file creation remains ``write_canonical_json``.
    """

    try:
        text = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ReleaseContractError(f"value is not finite JSON data: {exc}") from exc
    return f"{text}\n".encode("utf-8")


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of one regular, non-symlink file."""

    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ReleaseContractError(f"not a regular file: {candidate}")
    digest = hashlib.sha256()
    try:
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseContractError(f"cannot hash file {candidate}: {exc}") from exc
    return digest.hexdigest()


def write_canonical_json(path: Path, value: object) -> str:
    """Atomically create one canonical JSON file and return its SHA-256.

    Input: a new destination path and one JSON-compatible value.
    Output: SHA-256 of the exact bytes written.
    Side effects: creates exactly one file; existing paths are never replaced.
    Errors: invalid values, parents, destinations, or writes fail loud.
    Split trigger: directory creation and multi-file receipts belong elsewhere.
    """

    destination = Path(path)
    data = canonical_json_bytes(value)
    parent = destination.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ReleaseContractError(f"output parent is not a real directory: {parent}")
    if destination.exists() or destination.is_symlink():
        raise ReleaseContractError(f"output already exists: {destination}")

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    except FileExistsError as exc:
        if temporary is not None:
            _remove_temporary(temporary, destination, exc)
        raise ReleaseContractError(f"output already exists: {destination}") from exc
    except OSError as exc:
        if temporary is not None:
            _remove_temporary(temporary, destination, exc)
        raise ReleaseContractError(
            f"cannot atomically write canonical JSON {destination}: {exc}"
        ) from exc

    if temporary is None:
        raise ReleaseContractError(
            f"canonical JSON temporary file was not created for {destination}"
        )
    _remove_temporary(temporary, destination, None)

    return hashlib.sha256(data).hexdigest()


def _remove_temporary(
    temporary: Path,
    destination: Path,
    primary_error: OSError | None,
) -> None:
    try:
        temporary.unlink()
    except OSError as cleanup_error:
        if primary_error is None:
            message = (
                f"created canonical JSON {destination}, but could not remove "
                f"temporary file {temporary}: {cleanup_error}"
            )
        else:
            message = (
                f"canonical JSON write failed for {destination}: {primary_error}; "
                f"temporary cleanup also failed for {temporary}: {cleanup_error}"
            )
        raise ReleaseContractError(message) from cleanup_error


def load_release_input(path: Path, expected_sha256: str) -> ReleaseInputBundle:
    """Authenticate and parse one canonical ``easyqc-release-input-v1`` file.

    Input: one release-input path and expected SHA-256.
    Output: immutable ``ReleaseInputBundle`` with contained path identities.
    Side effects: read only.
    Errors: file/hash/canonical/schema/field/path errors fail before validation.
    Split trigger: referenced file authentication is ``validate_release_inputs``.
    """

    document_path = Path(path)
    _validate_sha256(expected_sha256, "release-input SHA-256")
    if document_path.is_symlink() or not document_path.is_file():
        raise ReleaseContractError(
            f"release-input is not a regular file: {document_path}"
        )
    actual_sha256 = sha256_file(document_path)
    if actual_sha256 != expected_sha256:
        raise ReleaseContractError(
            "release-input SHA-256 mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )

    try:
        root = document_path.parent.resolve(strict=True)
        resolved_document_path = document_path.resolve(strict=True)
    except OSError as exc:
        raise ReleaseContractError(
            f"release-input path cannot be resolved: {document_path}: {exc}"
        ) from exc
    payload = _read_canonical_object(document_path, "release-input")
    _expect_fields(
        payload,
        {
            "schema",
            "target",
            "version",
            "source",
            "runtime_identity",
            "locks",
            "component_policy",
            "target_extension",
        },
        "release-input",
    )
    if payload["schema"] != RELEASE_INPUT_SCHEMA:
        raise ReleaseContractError(
            f"release-input schema must be {RELEASE_INPUT_SCHEMA}"
        )

    target = _required_string(payload["target"], "target")
    if target not in TARGET_TRIPLES:
        raise ReleaseContractError(f"unsupported release target: {target}")
    version = _required_string(payload["version"], "version")
    if not _VERSION_PATTERN.fullmatch(version):
        raise ReleaseContractError(f"version is not canonical: {version}")

    source = _required_object(payload["source"], "source")
    _expect_fields(source, {"revision", "archive"}, "source")
    source_revision = _required_string(source["revision"], "source.revision")
    if not _REVISION_PATTERN.fullmatch(source_revision):
        raise ReleaseContractError("source.revision must be a lowercase full git SHA")

    locks = _required_object(payload["locks"], "locks")
    _expect_fields(locks, set(_LOCK_SCOPES), "locks")
    lock_files = {
        scope: _parse_file_reference(root, locks[scope], f"locks.{scope}")
        for scope in _LOCK_SCOPES
    }

    extension = _required_object(payload["target_extension"], "target_extension")
    target_extension: dict[str, HashedFile] = {}
    if target == "linux-x86_64":
        _expect_fields(extension, {"linux_cursor_deb"}, "target_extension")
        target_extension["linux_cursor_deb"] = _parse_file_reference(
            root,
            extension["linux_cursor_deb"],
            "target_extension.linux_cursor_deb",
        )
    else:
        _expect_fields(extension, set(), "target_extension")

    return ReleaseInputBundle(
        root=root,
        document_path=resolved_document_path,
        document_sha256=actual_sha256,
        target=target,
        version=version,
        source_revision=source_revision,
        source_archive=_parse_file_reference(root, source["archive"], "source.archive"),
        runtime_identity_file=_parse_file_reference(
            root,
            payload["runtime_identity"],
            "runtime_identity",
        ),
        lock_files=MappingProxyType(lock_files),
        component_policy=_parse_file_reference(
            root,
            payload["component_policy"],
            "component_policy",
        ),
        target_extension=MappingProxyType(target_extension),
    )


def validate_release_inputs(bundle: ReleaseInputBundle) -> ValidatedReleaseInputs:
    """Authenticate every referenced input before build or network activity.

    Input: one parsed ``ReleaseInputBundle``.
    Output: immutable ``ValidatedReleaseInputs`` with authenticated paths.
    Side effects: read only; no network, subprocess, or writes.
    Errors: first path/hash/runtime/target/lock mismatch raises explicitly.
    Split trigger: build, manifest, inventory, and native checks are separate tasks.
    """

    _verify_file(bundle.root, bundle.source_archive)
    runtime_identity = _load_runtime_identity(bundle)

    validated_locks: dict[str, Path] = {}
    for scope in _LOCK_SCOPES:
        reference = bundle.lock_files[scope]
        _verify_file(bundle.root, reference)
        approved_sha256 = APPROVED_TARGET_LOCK_SHA256[bundle.target][scope]
        if reference.sha256 != approved_sha256:
            raise ReleaseContractError(
                f"locks.{scope} is not the approved {bundle.target} lock: "
                f"expected {approved_sha256}, got {reference.sha256}"
            )
        _validate_lock_text(reference, scope)
        validated_locks[scope] = reference.path

    _verify_file(bundle.root, bundle.component_policy)

    linux_cursor_deb: Path | None = None
    if bundle.target == "linux-x86_64":
        cursor_reference = bundle.target_extension["linux_cursor_deb"]
        _verify_file(bundle.root, cursor_reference)
        linux_cursor_deb = cursor_reference.path
    elif bundle.target_extension:
        raise ReleaseContractError(
            f"target_extension must be empty for {bundle.target}"
        )

    return ValidatedReleaseInputs(
        bundle=bundle,
        runtime_identity=runtime_identity,
        source_archive=bundle.source_archive.path,
        lock_files=MappingProxyType(validated_locks),
        component_policy=bundle.component_policy.path,
        linux_cursor_deb=linux_cursor_deb,
    )


def _read_canonical_object(path: Path, label: str) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseContractError(f"{label} must be a JSON object")
    if raw != canonical_json_bytes(payload):
        raise ReleaseContractError(f"{label} is not canonical JSON")
    return payload


def _load_runtime_identity(bundle: ReleaseInputBundle) -> RuntimeIdentity:
    reference = bundle.runtime_identity_file
    _verify_file(bundle.root, reference)
    payload = _read_canonical_object(reference.path, "runtime_identity")
    _expect_fields(
        payload,
        {
            "implementation",
            "version",
            "target",
            "target_triple",
            "provider",
            "source_provenance",
            "artifact",
        },
        "runtime_identity",
    )

    implementation = _required_string(
        payload["implementation"],
        "runtime_identity.implementation",
    )
    version = _required_string(payload["version"], "runtime_identity.version")
    target = _required_string(payload["target"], "runtime_identity.target")
    target_triple = _required_string(
        payload["target_triple"],
        "runtime_identity.target_triple",
    )
    provider = _required_string(payload["provider"], "runtime_identity.provider")
    source_provenance = _required_string(
        payload["source_provenance"],
        "runtime_identity.source_provenance",
    )

    if implementation != APPROVED_RUNTIME_IMPLEMENTATION:
        raise ReleaseContractError(
            f"runtime_identity.implementation must be {APPROVED_RUNTIME_IMPLEMENTATION}"
        )
    if version != APPROVED_RUNTIME_VERSION:
        raise ReleaseContractError(
            f"runtime_identity.version must be {APPROVED_RUNTIME_VERSION}"
        )
    if target != bundle.target:
        raise ReleaseContractError(
            f"runtime_identity.target must match release target {bundle.target}"
        )
    if target_triple != TARGET_TRIPLES[bundle.target]:
        raise ReleaseContractError(
            "runtime_identity.target_triple must be "
            f"{TARGET_TRIPLES[bundle.target]} for {bundle.target}"
        )

    artifact = _parse_file_reference(
        bundle.root,
        payload["artifact"],
        "runtime_identity.artifact",
    )
    _verify_file(bundle.root, artifact)
    return RuntimeIdentity(
        implementation=implementation,
        version=version,
        target=target,
        target_triple=target_triple,
        provider=provider,
        source_provenance=source_provenance,
        artifact=artifact,
        identity_file=reference,
    )


def _parse_file_reference(
    root: Path,
    value: object,
    label: str,
) -> HashedFile:
    payload = _required_object(value, label)
    _expect_fields(payload, {"path", "sha256"}, label)
    relative_path = _required_string(payload["path"], f"{label}.path")
    if "\\" in relative_path:
        raise ReleaseContractError(f"{label}.path must use POSIX separators")
    pure_path = PurePosixPath(relative_path)
    if (
        pure_path.is_absolute()
        or pure_path.as_posix() != relative_path
        or relative_path == "."
        or any(part in {"", ".", ".."} for part in pure_path.parts)
    ):
        raise ReleaseContractError(
            f"{label}.path must be a canonical contained relative path"
        )
    expected_sha256 = _validate_sha256(payload["sha256"], f"{label}.sha256")
    return HashedFile(
        label=label,
        relative_path=relative_path,
        path=root.joinpath(*pure_path.parts),
        sha256=expected_sha256,
    )


def _verify_file(root: Path, reference: HashedFile) -> None:
    current = root
    for part in PurePosixPath(reference.relative_path).parts:
        current = current / part
        if current.is_symlink():
            raise ReleaseContractError(
                f"{reference.label}.path contains a symlink: {reference.relative_path}"
            )
    if not current.is_file():
        raise ReleaseContractError(
            f"{reference.label}.path is not a regular file: {reference.relative_path}"
        )
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise ReleaseContractError(
            f"{reference.label}.path cannot be resolved: {exc}"
        ) from exc
    if not resolved.is_relative_to(root):
        raise ReleaseContractError(
            f"{reference.label}.path escapes the release-input root"
        )
    actual_sha256 = sha256_file(current)
    if actual_sha256 != reference.sha256:
        raise ReleaseContractError(
            f"{reference.label} SHA-256 mismatch: "
            f"expected {reference.sha256}, got {actual_sha256}"
        )


def _validate_lock_text(reference: HashedFile, scope: str) -> None:
    try:
        text = reference.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseContractError(f"locks.{scope} is not UTF-8 text: {exc}") from exc
    required_prefix = (
        "--index-url https://pypi.org/simple\n"
        "--only-binary :all:\n"
    )
    if not text.startswith(required_prefix):
        raise ReleaseContractError(
            f"locks.{scope} must use official PyPI and wheel-only mode"
        )
    if "--hash=sha256:" not in text:
        raise ReleaseContractError(f"locks.{scope} has no package hashes")
    forbidden = ("--extra-index-url", "--trusted-host", "git+", "http://")
    if any(token in text for token in forbidden):
        raise ReleaseContractError(f"locks.{scope} contains a forbidden source")


def _expect_fields(
    payload: Mapping[str, object],
    expected: set[str],
    label: str,
) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ReleaseContractError(
            f"{label} fields mismatch: missing={missing}, extra={extra}"
        )


def _required_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ReleaseContractError(f"{label} must be a JSON object")
    return value


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseContractError(f"{label} must be a non-empty string")
    return value


def _validate_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ReleaseContractError(f"{label} must be a lowercase SHA-256")
    return value


def _validate_contained_relative_path(
    value: object,
    label: str,
    *,
    prefix: str,
) -> str:
    path = _required_string(value, label)
    if "\\" in path:
        raise ReleaseContractError(f"{label} must use POSIX separators")
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or pure.as_posix() != path
        or any(part in {"", ".", ".."} for part in pure.parts)
        or not path.startswith(prefix)
    ):
        raise ReleaseContractError(
            f"{label} must be a canonical contained path below {prefix}"
        )
    return path


__all__ = [
    "APPROVED_RUNTIME_IMPLEMENTATION",
    "APPROVED_RUNTIME_VERSION",
    "APPROVED_TARGET_LOCK_SHA256",
    "BUILD_RECEIPT_SCHEMA",
    "BuildReceipt",
    "HashedFile",
    "RELEASE_INPUT_SCHEMA",
    "ReleaseContractError",
    "ReleaseInputBundle",
    "RuntimeIdentity",
    "TARGET_TRIPLES",
    "ValidatedReleaseInputs",
    "canonical_json_bytes",
    "load_release_input",
    "sha256_file",
    "validate_release_inputs",
    "write_canonical_json",
]
