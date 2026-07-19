"""Exact-path component classification for one authenticated candidate.

This module owns the canonical component ledger only.  It does not build a
candidate, infer component identity from path patterns, emit SBOM/notices, or
run native commands.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Iterable

from .artifact_manifest import ArtifactManifest, create_artifact_manifest
from .contracts import (
    ReleaseContractError,
    canonical_json_bytes,
    sha256_file,
    write_canonical_json,
)


BUILD_RECEIPT_SCHEMA = "easyqc-release-build-receipt-v1"
COMPONENT_LEDGER_SCHEMA = "easyqc-release-component-ledger-v1"
COMPONENT_POLICY_SCHEMA = "easyqc-component-policy-v1"
DISTRIBUTION_METADATA_SCHEMA = "easyqc-release-distribution-metadata-v1"
DISTRIBUTION_METADATA_V2_SCHEMA = "easyqc-release-distribution-metadata-v2"
DISTRIBUTION_METADATA_V3_SCHEMA = "easyqc-release-distribution-metadata-v3"
PYINSTALLER_TOC_INDEX_SCHEMA = "easyqc-release-pyinstaller-toc-index-v1"
PYINSTALLER_EVIDENCE_INDEX_SCHEMA = (
    "easyqc-release-pyinstaller-evidence-index-v1"
)
UNRESOLVED_COMPONENTS_SCHEMA = "easyqc-release-unresolved-components-v1"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_NORMALIZED_DISTRIBUTION_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DISTRIBUTION_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+!-]*$")
_REQUIRED_PYINSTALLER_TOC_KINDS = (
    "Analysis",
    "PYZ",
    "PKG",
    "EXE",
    "COLLECT",
)
_PYINSTALLER_COLLECT_TYPES = frozenset(
    {"BINARY", "DATA", "EXECUTABLE", "EXTENSION", "SYMLINK"}
)
_PYINSTALLER_WARNING_DISPOSITIONS = frozenset(
    {"RETAINED_NO_WARNINGS", "RETAINED_REVIEW_REQUIRED"}
)
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
_MAX_RETAINED_TOC_BYTES = 16 * 1024 * 1024
_MAX_RETAINED_COMPONENT_EVIDENCE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_COMPONENT_EVIDENCE_BYTES = 256 * 1024 * 1024
_SUPPORTS_DESCRIPTOR_RELATIVE_OPEN = (
    os.open in os.supports_dir_fd
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


class ComponentInventoryError(ReleaseContractError):
    """Raised when inventory inputs or stage ownership are invalid."""


@dataclass(frozen=True)
class InventoryRequest:
    """One authenticated BuildReceipt and its new inventory stage path."""

    build_receipt_path: Path
    build_receipt_sha256: str
    output_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "build_receipt_path", Path(self.build_receipt_path))
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        _validate_sha256(self.build_receipt_sha256, "build receipt SHA-256")


@dataclass(frozen=True)
class UnresolvedRecord:
    code: str
    subject: str
    reason: str

    def as_json_object(self) -> dict[str, str]:
        return {"code": self.code, "reason": self.reason, "subject": self.subject}


@dataclass(frozen=True)
class ComponentRecord:
    component_id: str
    component_type: str
    name: str
    version: str
    purl: str
    provider: str
    file_paths: tuple[str, ...]
    origin_evidence: tuple[str, ...]
    dependencies: tuple[str, ...]
    license_declared: str
    license_concluded: str
    notice_paths: tuple[str, ...]
    resolution_status: str
    unresolved_reasons: tuple[str, ...]

    def as_json_object(self) -> dict[str, object]:
        return {
            "component_id": self.component_id,
            "type": self.component_type,
            "name": self.name,
            "version": self.version,
            "purl": self.purl,
            "provider": self.provider,
            "file_paths": list(self.file_paths),
            "origin_evidence": list(self.origin_evidence),
            "dependencies": list(self.dependencies),
            "license_declared": self.license_declared,
            "license_concluded": self.license_concluded,
            "notice_paths": list(self.notice_paths),
            "resolution_status": self.resolution_status,
            "unresolved_reasons": list(self.unresolved_reasons),
        }


@dataclass(frozen=True)
class NonComponentRecord:
    classification: str
    file_paths: tuple[str, ...]

    def as_json_object(self) -> dict[str, object]:
        return {
            "classification": self.classification,
            "file_paths": list(self.file_paths),
        }


@dataclass(frozen=True)
class ArtifactSummary:
    manifest_schema: str
    entry_count: int
    classifiable_entry_count: int
    regular_file_count: int
    symlink_count: int

    def as_json_object(self) -> dict[str, object]:
        return {
            "manifest_schema": self.manifest_schema,
            "entry_count": self.entry_count,
            "classifiable_entry_count": self.classifiable_entry_count,
            "regular_file_count": self.regular_file_count,
            "symlink_count": self.symlink_count,
        }


@dataclass(frozen=True)
class ComponentLedger:
    """Canonical inventory authority plus runtime-only projection context."""

    candidate_id: str
    target: str
    artifact: ArtifactSummary
    components: tuple[ComponentRecord, ...]
    non_component_entries: tuple[NonComponentRecord, ...]
    relationships: tuple[dict[str, str], ...]
    classified_entry_count: int
    unresolved: tuple[UnresolvedRecord, ...]
    release_status: str
    packet_root: Path
    artifact_root: Path
    build_receipt_sha256: str
    evidence_class: str
    notice_hashes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _validate_sha256(self.candidate_id, "Candidate ID")
        _validate_sha256(self.build_receipt_sha256, "build receipt SHA-256")
        if self.release_status not in {"PASS", "FAIL"}:
            raise ComponentInventoryError("ledger release_status must be PASS or FAIL")
        if self.release_status == "PASS" and self.unresolved:
            raise ComponentInventoryError("a PASS ledger cannot contain unresolved rows")
        if self.release_status == "PASS" and (
            self.classified_entry_count != self.artifact.classifiable_entry_count
        ):
            raise ComponentInventoryError(
                "a PASS ledger must classify every file and symlink exactly once"
            )
        if self.evidence_class not in {"fixture", "release"}:
            raise ComponentInventoryError("unknown evidence_class")
        object.__setattr__(self, "packet_root", Path(self.packet_root))
        object.__setattr__(self, "artifact_root", Path(self.artifact_root))

    def as_json_object(self) -> dict[str, object]:
        """Return the sole canonical ledger representation."""

        return {
            "schema": COMPONENT_LEDGER_SCHEMA,
            "candidate_manifest_sha256": self.candidate_id,
            "target": self.target,
            "artifact": self.artifact.as_json_object(),
            "components": [item.as_json_object() for item in self.components],
            "non_component_entries": [
                item.as_json_object() for item in self.non_component_entries
            ],
            "relationships": list(self.relationships),
            "classified_entry_count": self.classified_entry_count,
            "unresolved": [item.as_json_object() for item in self.unresolved],
            "release_status": self.release_status,
        }


@dataclass(frozen=True)
class _BuildContext:
    packet_root: Path
    artifact_root: Path
    manifest: ArtifactManifest
    target: str
    evidence_class: str
    policy: dict[str, object]
    toc_index: dict[str, object]
    metadata: dict[str, object]
    authenticated_origin_paths: tuple[str, ...]


@dataclass(frozen=True)
class _MetadataEvidence:
    components: dict[str, dict[str, object]]
    assigned_collected_entries: tuple[dict[str, object], ...] = ()
    unassigned_collected_entries: tuple[dict[str, object], ...] = ()
    requires_exact_collect_coverage: bool = False


def generate_component_ledger(request: InventoryRequest) -> ComponentLedger:
    """Authenticate, classify, and persist one canonical ComponentLedger.

    Input: one ``InventoryRequest`` naming an exact BuildReceipt and new
    ``inventory/`` directory.
    Output: one typed PASS or diagnostic FAIL ``ComponentLedger``.
    Side effects: creates only the inventory directory plus canonical ledger
    and unresolved JSON; candidate/build/input evidence stays read-only.
    Errors: structural/hash/path/Candidate-ID failures raise before output.
    Split trigger: SBOM/notice projection and native execution stay separate.
    """

    context = _load_build_context(request)
    ledger = _classify(context, request.build_receipt_sha256)
    post_manifest = create_artifact_manifest(context.artifact_root)
    if post_manifest.candidate_id != context.manifest.candidate_id:
        raise ComponentInventoryError(
            "candidate identity mismatch after component classification: "
            f"expected {context.manifest.candidate_id}, got {post_manifest.candidate_id}"
        )

    _create_inventory_directory(request.output_dir, context.packet_root)
    write_canonical_json(
        request.output_dir / "component-ledger.json",
        ledger.as_json_object(),
    )
    write_canonical_json(
        request.output_dir / "unresolved-components.json",
        {
            "schema": UNRESOLVED_COMPONENTS_SCHEMA,
            "candidate_manifest_sha256": ledger.candidate_id,
            "unresolved": [item.as_json_object() for item in ledger.unresolved],
        },
    )
    return ledger


def _load_build_context(request: InventoryRequest) -> _BuildContext:
    receipt_path = request.build_receipt_path
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ComponentInventoryError(
            f"build receipt is not a regular file: {receipt_path}"
        )
    actual_receipt_sha256 = sha256_file(receipt_path)
    if actual_receipt_sha256 != request.build_receipt_sha256:
        raise ComponentInventoryError(
            "build receipt SHA-256 mismatch: "
            f"expected {request.build_receipt_sha256}, got {actual_receipt_sha256}"
        )
    if receipt_path.parent.name != "build":
        raise ComponentInventoryError("build receipt must be below packet build/")
    packet_root = _real_directory(receipt_path.parent.parent, "packet root")
    if receipt_path.parent.resolve(strict=True) != packet_root / "build":
        raise ComponentInventoryError("build receipt parent escapes packet root")

    receipt = _read_canonical_object(receipt_path, "build receipt")
    _expect_fields(
        receipt,
        {
            "schema",
            "status",
            "evidence_class",
            "target",
            "candidate_id",
            "artifact",
            "component_policy",
            "evidence",
            "source",
            "runtime",
            "locks",
            "tools",
        },
        "build receipt",
    )
    if receipt["schema"] != BUILD_RECEIPT_SCHEMA:
        raise ComponentInventoryError(
            f"build receipt schema must be {BUILD_RECEIPT_SCHEMA}"
        )
    if receipt["status"] != "PASS":
        raise ComponentInventoryError("inventory requires a PASS BuildReceipt")
    evidence_class = _required_string(receipt["evidence_class"], "evidence_class")
    if evidence_class not in {"fixture", "release"}:
        raise ComponentInventoryError("unknown build receipt evidence_class")
    target = _required_string(receipt["target"], "target")
    candidate_id = _validate_sha256(receipt["candidate_id"], "Candidate ID")
    _validate_build_identity(receipt)

    artifact = _required_object(receipt["artifact"], "artifact")
    _expect_fields(artifact, {"path", "manifest"}, "artifact")
    artifact_root = _contained_path(
        packet_root,
        _required_string(artifact["path"], "artifact.path"),
        "artifact.path",
        expected="directory",
    )
    manifest_path = _reference_path(packet_root, artifact["manifest"], "artifact.manifest")
    manifest = create_artifact_manifest(artifact_root)
    if manifest.candidate_id != candidate_id:
        raise ComponentInventoryError(
            "candidate identity mismatch before component classification: "
            f"expected {candidate_id}, got {manifest.candidate_id}"
        )
    if sha256_file(manifest_path) != candidate_id:
        raise ComponentInventoryError("artifact manifest digest is not Candidate ID")
    if manifest_path.read_bytes() != manifest.canonical_bytes:
        raise ComponentInventoryError(
            "retained artifact manifest does not match candidate bytes"
        )

    policy_path = _reference_path(
        packet_root,
        receipt["component_policy"],
        "component_policy",
    )
    evidence = _required_object(receipt["evidence"], "evidence")
    _expect_fields(evidence, {"toc_index", "distribution_metadata"}, "evidence")
    toc_reference = _required_object(evidence["toc_index"], "evidence.toc_index")
    metadata_reference = _required_object(
        evidence["distribution_metadata"],
        "evidence.distribution_metadata",
    )
    toc_path = _reference_path(packet_root, toc_reference, "evidence.toc_index")
    metadata_path = _reference_path(
        packet_root,
        metadata_reference,
        "evidence.distribution_metadata",
    )
    policy = _read_canonical_object(policy_path, "component policy")
    toc_index = _read_canonical_object(toc_path, "PyInstaller TOC index")
    metadata = _read_canonical_object(metadata_path, "distribution metadata")
    return _BuildContext(
        packet_root=packet_root,
        artifact_root=artifact_root,
        manifest=manifest,
        target=target,
        evidence_class=evidence_class,
        policy=policy,
        toc_index=toc_index,
        metadata=metadata,
        authenticated_origin_paths=tuple(
            sorted(
                (
                    _canonical_path(
                        toc_reference["path"],
                        "evidence.toc_index.path",
                    ),
                    _canonical_path(
                        metadata_reference["path"],
                        "evidence.distribution_metadata.path",
                    ),
                )
            )
        ),
    )


def _validate_build_identity(receipt: dict[str, object]) -> None:
    source = _required_object(receipt["source"], "source")
    _expect_fields(source, {"revision", "archive_sha256"}, "source")
    revision = _required_string(source["revision"], "source.revision")
    if not _REVISION_PATTERN.fullmatch(revision):
        raise ComponentInventoryError("source.revision must be a full lowercase SHA")
    _validate_sha256(source["archive_sha256"], "source.archive_sha256")
    runtime = _required_object(receipt["runtime"], "runtime")
    _expect_fields(runtime, {"identity_sha256"}, "runtime")
    _validate_sha256(runtime["identity_sha256"], "runtime.identity_sha256")
    locks = _required_object(receipt["locks"], "locks")
    _expect_fields(locks, {"runtime", "build", "test"}, "locks")
    for scope in ("runtime", "build", "test"):
        _validate_sha256(locks[scope], f"locks.{scope}")
    tools = _required_object(receipt["tools"], "tools")
    _expect_fields(tools, {"python", "pyinstaller"}, "tools")
    _required_string(tools["python"], "tools.python")
    _required_string(tools["pyinstaller"], "tools.pyinstaller")


def _classify(context: _BuildContext, build_receipt_sha256: str) -> ComponentLedger:
    rules, non_component_rules = _load_policy(context.policy, context.target)
    metadata_evidence = _load_metadata(context.metadata, context.packet_root)
    metadata = metadata_evidence.components
    toc_entries = _load_toc_index(
        context.toc_index,
        packet_root=context.packet_root,
        manifest=context.manifest,
        metadata_evidence=metadata_evidence,
    )
    manifest_entries = {
        entry.path: entry
        for entry in context.manifest.entries
        if entry.entry_type != "directory"
    }
    unresolved: list[UnresolvedRecord] = []
    owners: dict[str, list[tuple[str, str]]] = {}

    for rule in rules:
        component_id = str(rule["component_id"])
        for path in rule["file_paths"]:
            owners.setdefault(str(path), []).append(("component", component_id))
    for rule in non_component_rules:
        classification = str(rule["classification"])
        for path in rule["file_paths"]:
            owners.setdefault(str(path), []).append(("non-component", classification))

    assigned_components = {str(rule["component_id"]): [] for rule in rules}
    assigned_non_components = {
        str(rule["classification"]): [] for rule in non_component_rules
    }
    for path in sorted(manifest_entries):
        path_owners = owners.get(path, [])
        if not path_owners:
            unresolved.append(
                UnresolvedRecord(
                    "unclassified-path",
                    path,
                    "manifest file or symlink has no exact policy owner",
                )
            )
        elif len(path_owners) != 1:
            unresolved.append(
                UnresolvedRecord(
                    "duplicate-ownership",
                    path,
                    "manifest path has multiple policy owners: "
                    + ", ".join(f"{kind}:{owner}" for kind, owner in path_owners),
                )
            )
        elif path_owners[0][0] == "component":
            assigned_components[path_owners[0][1]].append(path)
        else:
            assigned_non_components[path_owners[0][1]].append(path)

    for path in sorted(owners):
        if path not in manifest_entries:
            unresolved.append(
                UnresolvedRecord(
                    "unused-policy-path",
                    path,
                    "exact policy path is absent from the candidate manifest",
                )
            )
    for classification, paths in sorted(assigned_non_components.items()):
        if not paths:
            unresolved.append(
                UnresolvedRecord(
                    "unused-non-component-rule",
                    classification,
                    "non-component rule owns no uniquely classified path",
                )
            )

    components: list[ComponentRecord] = []
    notice_hashes: dict[str, str] = {}
    referenced_toc: set[tuple[str, str]] = set()
    for rule in rules:
        component_id = str(rule["component_id"])
        reasons = _component_reasons(
            rule,
            metadata.get(component_id),
            toc_entries,
            referenced_toc,
            manifest_entries,
            owners,
            context.artifact_root,
            notice_hashes,
            frozenset(context.authenticated_origin_paths),
            not metadata_evidence.requires_exact_collect_coverage,
        )
        if not assigned_components[component_id]:
            reasons.append("component rule owns no uniquely classified path")
        for reason in sorted(set(reasons)):
            unresolved.append(UnresolvedRecord("unresolved-component", component_id, reason))
        components.append(
            ComponentRecord(
                component_id=component_id,
                component_type=str(rule["type"]),
                name=str(rule["name"]),
                version=str(rule["version"]),
                purl=str(rule["purl"]),
                provider=str(rule["provider"]),
                file_paths=tuple(sorted(assigned_components[component_id])),
                origin_evidence=tuple(str(item) for item in rule["origin_evidence"]),
                dependencies=tuple(str(item) for item in rule["dependencies"]),
                license_declared=str(rule["license_declared"]),
                license_concluded=str(rule["license_concluded"]),
                notice_paths=tuple(str(item) for item in rule["notice_paths"]),
                resolution_status="unresolved" if reasons else "resolved",
                unresolved_reasons=tuple(sorted(set(reasons))),
            )
        )

    for key in sorted(set(toc_entries) - referenced_toc):
        unresolved.append(
            UnresolvedRecord(
                "unused-toc-entry",
                f"{key[0]}:{key[1]}",
                "TOC entry is not consumed by an exact component rule",
            )
        )
    rule_ids = {str(rule["component_id"]) for rule in rules}
    for component_id in sorted(set(metadata) - rule_ids):
        unresolved.append(
            UnresolvedRecord(
                "unused-metadata-component",
                component_id,
                "distribution metadata component has no policy rule",
            )
        )

    non_components = tuple(
        NonComponentRecord(
            classification=str(rule["classification"]),
            file_paths=tuple(sorted(assigned_non_components[str(rule["classification"])])),
        )
        for rule in non_component_rules
    )
    relationships = tuple(
        sorted(
            (
                {"from": component.component_id, "to": dependency, "type": "depends-on"}
                for component in components
                for dependency in component.dependencies
            ),
            key=lambda item: (item["from"], item["to"], item["type"]),
        )
    )
    component_ids = {component.component_id for component in components}
    for relationship in relationships:
        if relationship["to"] not in component_ids:
            unresolved.append(
                UnresolvedRecord(
                    "unknown-dependency",
                    relationship["from"],
                    f"dependency is absent from ledger: {relationship['to']}",
                )
            )

    classified_entry_count = sum(
        len(component.file_paths) for component in components
    ) + sum(len(record.file_paths) for record in non_components)
    unresolved_tuple = tuple(
        sorted(set(unresolved), key=lambda item: (item.code, item.subject, item.reason))
    )
    regular_count = sum(
        entry.entry_type == "regular-file" for entry in manifest_entries.values()
    )
    symlink_count = sum(
        entry.entry_type == "symlink" for entry in manifest_entries.values()
    )
    release_status = (
        "PASS"
        if not unresolved_tuple and classified_entry_count == len(manifest_entries)
        else "FAIL"
    )
    return ComponentLedger(
        candidate_id=context.manifest.candidate_id,
        target=context.target,
        artifact=ArtifactSummary(
            manifest_schema=context.manifest.schema,
            entry_count=len(context.manifest.entries),
            classifiable_entry_count=len(manifest_entries),
            regular_file_count=regular_count,
            symlink_count=symlink_count,
        ),
        components=tuple(sorted(components, key=lambda item: item.component_id)),
        non_component_entries=tuple(
            sorted(non_components, key=lambda item: item.classification)
        ),
        relationships=relationships,
        classified_entry_count=classified_entry_count,
        unresolved=unresolved_tuple,
        release_status=release_status,
        packet_root=context.packet_root,
        artifact_root=context.artifact_root,
        build_receipt_sha256=build_receipt_sha256,
        evidence_class=context.evidence_class,
        notice_hashes=tuple(sorted(notice_hashes.items())),
    )


def _load_policy(
    policy: dict[str, object],
    target: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    _expect_fields(
        policy,
        {"schema", "version", "targets", "rules", "non_component_rules"},
        "component policy",
    )
    if policy["schema"] != COMPONENT_POLICY_SCHEMA or policy["version"] != 1:
        raise ComponentInventoryError("component policy schema/version mismatch")
    targets = _sorted_strings(policy["targets"], "component policy targets")
    if target not in targets:
        raise ComponentInventoryError(f"component policy does not select target {target}")
    rules = _object_list(policy["rules"], "component policy rules")
    non_component_rules = _object_list(
        policy["non_component_rules"], "component policy non_component_rules"
    )
    component_ids = [str(rule.get("component_id", "")) for rule in rules]
    if component_ids != sorted(component_ids):
        raise ComponentInventoryError("component policy rules must be sorted")
    if not component_ids or len(component_ids) != len(set(component_ids)):
        raise ComponentInventoryError(
            "component policy component IDs must be unique and non-empty"
        )
    for index, rule in enumerate(rules):
        label = f"component policy rule {index}"
        _expect_fields(
            rule,
            {
                "component_id",
                "type",
                "name",
                "version",
                "purl",
                "provider",
                "file_paths",
                "toc_sources",
                "origin_evidence",
                "dependencies",
                "license_declared",
                "license_concluded",
                "notice_paths",
                "notice_sha256",
                "notice_sources",
            },
            label,
        )
        for field in (
            "component_id",
            "type",
            "name",
            "version",
            "purl",
            "provider",
            "license_declared",
            "license_concluded",
        ):
            _required_string(rule[field], f"{label}.{field}")
        rule["file_paths"] = _canonical_paths(rule["file_paths"], f"{label}.file_paths")
        rule["origin_evidence"] = _canonical_paths(
            rule["origin_evidence"], f"{label}.origin_evidence"
        )
        rule["dependencies"] = _sorted_strings(
            rule["dependencies"], f"{label}.dependencies"
        )
        rule["notice_paths"] = _canonical_paths(
            rule["notice_paths"], f"{label}.notice_paths"
        )
        notice_sha256 = _required_object(rule["notice_sha256"], f"{label}.notice_sha256")
        if set(notice_sha256) != set(rule["notice_paths"]):
            raise ComponentInventoryError(
                f"{label}.notice_sha256 must cover every notice path exactly"
            )
        for path, digest in notice_sha256.items():
            _validate_sha256(digest, f"{label}.notice_sha256.{path}")
        notice_sources = _object_list(
            rule["notice_sources"], f"{label}.notice_sources"
        )
        if not notice_sources:
            raise ComponentInventoryError(f"{label}.notice_sources must not be empty")
        normalized_notice_sources: list[dict[str, str]] = []
        notice_destinations: list[str] = []
        for source in notice_sources:
            _expect_fields(
                source,
                {"source_path", "destination_path", "sha256"},
                f"{label}.notice_source",
            )
            source_path = _canonical_path(
                source["source_path"], f"{label}.notice_sources.source_path"
            )
            destination_path = _canonical_path(
                source["destination_path"],
                f"{label}.notice_sources.destination_path",
            )
            if not destination_path.startswith("THIRD_PARTY_LICENSES/"):
                raise ComponentInventoryError(
                    f"{label}.notice_sources.destination_path must be below "
                    "THIRD_PARTY_LICENSES/"
                )
            digest = _validate_sha256(
                source["sha256"], f"{label}.notice_sources.sha256"
            )
            notice_destinations.append(destination_path)
            normalized_notice_sources.append(
                {
                    "source_path": source_path,
                    "destination_path": destination_path,
                    "sha256": digest,
                }
            )
        if len(notice_destinations) != len(set(notice_destinations)):
            raise ComponentInventoryError(
                f"{label} notice source destinations must be unique"
            )
        if normalized_notice_sources != sorted(
            normalized_notice_sources,
            key=lambda item: (item["destination_path"], item["source_path"]),
        ):
            raise ComponentInventoryError(f"{label}.notice_sources must be sorted")
        if set(notice_destinations) != set(rule["notice_paths"]):
            raise ComponentInventoryError(
                f"{label}.notice_sources must cover notice_paths exactly"
            )
        if any(
            notice_sha256[item["destination_path"]] != item["sha256"]
            for item in normalized_notice_sources
        ):
            raise ComponentInventoryError(
                f"{label}.notice_sources SHA-256 must match notice_sha256"
            )
        rule["notice_sources"] = normalized_notice_sources
        toc_sources = _object_list(rule["toc_sources"], f"{label}.toc_sources")
        normalized_sources: list[dict[str, str]] = []
        for source in toc_sources:
            _expect_fields(
                source,
                {"entry_type", "final_path", "source_path"},
                f"{label}.toc_source",
            )
            entry_type = _required_string(source["entry_type"], "toc entry_type")
            if entry_type not in {"regular-file", "symlink"}:
                raise ComponentInventoryError("TOC entry_type is unsupported")
            final_path = _canonical_path(source["final_path"], "toc final_path")
            source_path = _canonical_path(source["source_path"], "toc source_path")
            normalized_sources.append(
                {
                    "entry_type": entry_type,
                    "final_path": final_path,
                    "source_path": source_path,
                }
            )
        if normalized_sources != sorted(
            normalized_sources, key=lambda item: item["final_path"]
        ):
            raise ComponentInventoryError(f"{label}.toc_sources must be sorted")
        if {item["final_path"] for item in normalized_sources} != set(rule["file_paths"]):
            raise ComponentInventoryError(
                f"{label}.toc_sources must cover file_paths exactly"
            )
        rule["toc_sources"] = normalized_sources

    for index, rule in enumerate(non_component_rules):
        label = f"non-component policy rule {index}"
        _expect_fields(rule, {"classification", "file_paths"}, label)
        _required_string(rule["classification"], f"{label}.classification")
        rule["file_paths"] = _canonical_paths(rule["file_paths"], f"{label}.file_paths")
    classifications = [str(rule["classification"]) for rule in non_component_rules]
    if classifications != sorted(classifications) or len(classifications) != len(
        set(classifications)
    ):
        raise ComponentInventoryError("non-component rules must be unique and sorted")
    return rules, non_component_rules


def _load_metadata(
    metadata: dict[str, object],
    packet_root: Path,
) -> _MetadataEvidence:
    schema = metadata.get("schema")
    if schema == DISTRIBUTION_METADATA_V3_SCHEMA:
        return _load_composite_component_metadata(metadata, packet_root)
    if schema == DISTRIBUTION_METADATA_V2_SCHEMA:
        return _load_python_contributor_metadata(metadata, packet_root)
    if schema != DISTRIBUTION_METADATA_SCHEMA:
        raise ComponentInventoryError("unsupported distribution metadata schema")
    fields = set(metadata)
    if fields == {"schema", "components"}:
        return _MetadataEvidence(components=_load_normalized_metadata(metadata))
    if fields == {"schema", "distribution_count", "distributions"}:
        return _MetadataEvidence(
            components=_load_installed_distribution_metadata(metadata)
        )
    raise ComponentInventoryError(
        "distribution metadata fields do not match a normalized fixture or "
        "retained build-environment record"
    )


def _load_normalized_metadata(
    metadata: dict[str, object],
) -> dict[str, dict[str, object]]:
    records = _object_list(metadata["components"], "metadata components")
    result: dict[str, dict[str, object]] = {}
    for record in records:
        _expect_fields(
            record,
            {
                "component_id",
                "type",
                "name",
                "version",
                "purl",
                "provider",
                "license_declared",
            },
            "metadata component",
        )
        component_id = _required_string(record["component_id"], "metadata component_id")
        if component_id in result:
            raise ComponentInventoryError(f"duplicate metadata component: {component_id}")
        for field in ("type", "name", "version", "purl", "provider", "license_declared"):
            _required_string(record[field], f"metadata {component_id}.{field}")
        result[component_id] = record
    if list(result) != sorted(result):
        raise ComponentInventoryError("metadata components must be sorted")
    return result


def _load_installed_distribution_metadata(
    metadata: dict[str, object],
) -> dict[str, dict[str, object]]:
    records = _object_list(metadata["distributions"], "metadata distributions")
    count = metadata["distribution_count"]
    if isinstance(count, bool) or not isinstance(count, int) or count != len(records):
        raise ComponentInventoryError(
            "distribution_count must equal the retained distribution rows"
        )

    result: dict[str, dict[str, object]] = {}
    observed_order: list[tuple[str, str]] = []
    expected_fields = {
        "canonical_name",
        "license_classifiers",
        "license_expression",
        "license_field_first_line",
        "license_files",
        "name",
        "purl",
        "requires_python",
        "top_level_imports",
        "version",
    }
    for record in records:
        _expect_fields(record, expected_fields, "metadata distribution")
        canonical_name = _required_string(
            record["canonical_name"], "metadata canonical_name"
        )
        if not _NORMALIZED_DISTRIBUTION_PATTERN.fullmatch(canonical_name):
            raise ComponentInventoryError(
                f"metadata canonical_name is not normalized: {canonical_name}"
            )
        name = _required_string(record["name"], f"metadata {canonical_name}.name")
        version = _required_string(
            record["version"], f"metadata {canonical_name}.version"
        )
        purl = _required_string(record["purl"], f"metadata {canonical_name}.purl")
        if purl != f"pkg:pypi/{canonical_name}@{version}":
            raise ComponentInventoryError(
                f"metadata {canonical_name}.purl does not match name/version"
            )
        _sorted_strings(
            record["license_classifiers"],
            f"metadata {canonical_name}.license_classifiers",
        )
        first_line = _required_list(
            record["license_field_first_line"],
            f"metadata {canonical_name}.license_field_first_line",
        )
        if len(first_line) > 1 or any(
            not isinstance(value, str) or not value for value in first_line
        ):
            raise ComponentInventoryError(
                f"metadata {canonical_name}.license_field_first_line is invalid"
            )
        for optional_field in ("license_expression", "requires_python"):
            value = record[optional_field]
            if value is not None:
                _required_string(value, f"metadata {canonical_name}.{optional_field}")
        _sorted_strings(
            record["top_level_imports"],
            f"metadata {canonical_name}.top_level_imports",
        )

        license_files = _object_list(
            record["license_files"], f"metadata {canonical_name}.license_files"
        )
        license_paths: list[str] = []
        for license_file in license_files:
            _expect_fields(
                license_file,
                {"distribution_path", "sha256", "size"},
                f"metadata {canonical_name}.license_file",
            )
            license_path = _canonical_path(
                license_file["distribution_path"],
                f"metadata {canonical_name}.license_file.distribution_path",
            )
            _validate_sha256(
                license_file["sha256"],
                f"metadata {canonical_name}.license_file.sha256",
            )
            size = license_file["size"]
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ComponentInventoryError(
                    f"metadata {canonical_name}.license_file.size is invalid"
                )
            license_paths.append(license_path)
        if license_paths != sorted(license_paths) or len(license_paths) != len(
            set(license_paths)
        ):
            raise ComponentInventoryError(
                f"metadata {canonical_name}.license_files must be unique and sorted"
            )

        component_id = f"library:{canonical_name}@{version}"
        if component_id in result:
            raise ComponentInventoryError(
                f"duplicate metadata component: {component_id}"
            )
        result[component_id] = {
            "component_id": component_id,
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
        }
        observed_order.append((canonical_name, version))
    if observed_order != sorted(observed_order):
        raise ComponentInventoryError("metadata distributions must be sorted")
    return result


def _metadata_count(value: object, expected: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise ComponentInventoryError(f"{label} must equal {expected}")


def _nullable_metadata_string(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, label)


def _validate_source_identity(value: object, label: str) -> None:
    identity = _required_object(value, label)
    entry_type = identity.get("entry_type")
    if entry_type == "unavailable":
        _expect_fields(identity, {"entry_type"}, label)
        return
    if entry_type == "regular-file":
        _expect_fields(identity, {"entry_type", "sha256", "size"}, label)
        _validate_sha256(identity["sha256"], f"{label}.sha256")
    elif entry_type == "symlink":
        _expect_fields(
            identity,
            {"entry_type", "size", "target_text_sha256"},
            label,
        )
        _validate_sha256(
            identity["target_text_sha256"],
            f"{label}.target_text_sha256",
        )
    else:
        raise ComponentInventoryError(f"{label}.entry_type is invalid")
    size = identity["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ComponentInventoryError(f"{label}.size is invalid")


def _load_v2_collected_row(
    record: dict[str, object],
    label: str,
    *,
    assigned: bool,
) -> dict[str, object]:
    expected_fields = {
        "entry_type",
        "final_path",
        "raw_source_text_sha256",
        "source_identity",
        "source_locator",
        "toc_type",
    }
    if assigned:
        expected_fields.add("distribution_path")
    _expect_fields(record, expected_fields, label)
    entry_type = _required_string(record["entry_type"], f"{label}.entry_type")
    if entry_type not in {"regular-file", "symlink"}:
        raise ComponentInventoryError(f"{label}.entry_type is invalid")
    final_path = _canonical_path(record["final_path"], f"{label}.final_path")
    toc_type = _required_string(record["toc_type"], f"{label}.toc_type")
    if toc_type not in _PYINSTALLER_COLLECT_TYPES:
        raise ComponentInventoryError(f"{label}.toc_type is unsupported")
    if (toc_type == "SYMLINK") != (entry_type == "symlink"):
        raise ComponentInventoryError(f"{label} TOC/entry type mismatch")
    raw_digest = _validate_sha256(
        record["raw_source_text_sha256"],
        f"{label}.raw_source_text_sha256",
    )
    source_locator = _canonical_path(
        record["source_locator"],
        f"{label}.source_locator",
    )
    _validate_source_identity(record["source_identity"], f"{label}.source_identity")
    normalized: dict[str, object] = {
        "entry_type": entry_type,
        "final_path": final_path,
        "raw_source_text_sha256": raw_digest,
        "source_identity": record["source_identity"],
        "source_locator": source_locator,
        "toc_type": toc_type,
    }
    if assigned:
        normalized["distribution_path"] = _canonical_path(
            record["distribution_path"],
            f"{label}.distribution_path",
        )
    return normalized


def _load_v3_candidate_values(value: object, label: str) -> tuple[str, ...]:
    rows = _object_list(value, label)
    pairs: list[tuple[str, str]] = []
    for row in rows:
        _expect_fields(row, {"field", "value"}, f"{label} entry")
        pairs.append(
            (
                _required_string(row["field"], f"{label}.field"),
                _required_string(row["value"], f"{label}.value"),
            )
        )
    if pairs != sorted(pairs) or len(pairs) != len(set(pairs)):
        raise ComponentInventoryError(f"{label} must be unique and sorted")
    return tuple(candidate for _field, candidate in pairs)


def _load_v3_collected_row(
    record: dict[str, object],
    label: str,
) -> dict[str, object]:
    _expect_fields(
        record,
        {
            "entry_type",
            "final_path",
            "package_path",
            "raw_source_text_sha256",
            "source_identity",
            "source_kind",
            "source_locator",
            "target_final_path",
            "toc_type",
        },
        label,
    )
    entry_type = _required_string(record["entry_type"], f"{label}.entry_type")
    if entry_type not in {"regular-file", "symlink"}:
        raise ComponentInventoryError(f"{label}.entry_type is invalid")
    toc_type = _required_string(record["toc_type"], f"{label}.toc_type")
    if toc_type not in _PYINSTALLER_COLLECT_TYPES:
        raise ComponentInventoryError(f"{label}.toc_type is unsupported")
    if (toc_type == "SYMLINK") != (entry_type == "symlink"):
        raise ComponentInventoryError(f"{label} TOC/entry type mismatch")

    source_kind = _required_string(record["source_kind"], f"{label}.source_kind")
    if source_kind not in _V3_SOURCE_KINDS:
        raise ComponentInventoryError(f"{label}.source_kind is unsupported")
    if (source_kind == "python-symlink-alias") != (entry_type == "symlink"):
        raise ComponentInventoryError(
            f"{label}.python-symlink-alias must identify exactly a symlink"
        )

    source_identity = _required_object(
        record["source_identity"],
        f"{label}.source_identity",
    )
    _validate_source_identity(source_identity, f"{label}.source_identity")
    if source_identity.get("entry_type") == "unavailable":
        raise ComponentInventoryError(
            f"{label}.source_identity must be available in v3"
        )
    if source_identity.get("entry_type") != entry_type:
        raise ComponentInventoryError(
            f"{label}.source_identity entry type mismatch"
        )

    package_path_value = record["package_path"]
    package_path = None
    if package_path_value is not None:
        package_path = _canonical_path(
            package_path_value,
            f"{label}.package_path",
        )
    target_value = record["target_final_path"]
    target_final_path = None
    if target_value is not None:
        target_final_path = _canonical_path(
            target_value,
            f"{label}.target_final_path",
        )
    if (target_final_path is not None) != (entry_type == "symlink"):
        raise ComponentInventoryError(
            f"{label}.target_final_path is required only for symlinks"
        )

    return {
        "entry_type": entry_type,
        "final_path": _canonical_path(record["final_path"], f"{label}.final_path"),
        "package_path": package_path,
        "raw_source_text_sha256": _validate_sha256(
            record["raw_source_text_sha256"],
            f"{label}.raw_source_text_sha256",
        ),
        "source_identity": source_identity,
        "source_kind": source_kind,
        "source_locator": _canonical_path(
            record["source_locator"],
            f"{label}.source_locator",
        ),
        "target_final_path": target_final_path,
        "toc_type": toc_type,
    }


def _load_v3_evidence_file(
    record: dict[str, object],
    label: str,
    packet_root: Path,
) -> tuple[dict[str, object], str, int]:
    _expect_fields(
        record,
        {"kind", "retained_path", "sha256", "size", "source_locator"},
        label,
    )
    kind = _required_string(record["kind"], f"{label}.kind")
    if not _NORMALIZED_DISTRIBUTION_PATTERN.fullmatch(kind):
        raise ComponentInventoryError(f"{label}.kind is not canonical")
    retained_path = _canonical_path(
        record["retained_path"],
        f"{label}.retained_path",
    )
    if not retained_path.startswith("build/evidence/components/"):
        raise ComponentInventoryError(
            f"{label}.retained_path must be below build/evidence/components/"
        )
    expected_sha256 = _validate_sha256(
        record["sha256"],
        f"{label}.sha256",
    )
    expected_size = record["size"]
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
    ):
        raise ComponentInventoryError(f"{label}.size is invalid")
    if expected_size > _MAX_RETAINED_COMPONENT_EVIDENCE_BYTES:
        raise ComponentInventoryError(
            f"{label}.size exceeds {_MAX_RETAINED_COMPONENT_EVIDENCE_BYTES} bytes"
        )
    _authenticate_v3_evidence_file(
        packet_root,
        retained_path,
        expected_sha256,
        expected_size,
        label,
    )
    normalized = {
        "kind": kind,
        "retained_path": retained_path,
        "sha256": expected_sha256,
        "size": expected_size,
        "source_locator": _canonical_path(
            record["source_locator"],
            f"{label}.source_locator",
        ),
    }
    return normalized, retained_path, expected_size


def _open_v3_evidence_descriptor(
    packet_root: Path,
    retained_path: str,
    label: str,
) -> int:
    if not _SUPPORTS_DESCRIPTOR_RELATIVE_OPEN:
        raise ComponentInventoryError(
            "v3 nested evidence requires descriptor-relative no-follow support"
        )
    relative = PurePosixPath(retained_path)
    try:
        root_metadata = packet_root.lstat()
    except OSError as exc:
        raise ComponentInventoryError(
            f"cannot inspect v3 evidence packet root: {exc}"
        ) from exc
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(
        root_metadata.st_mode
    ):
        raise ComponentInventoryError("v3 evidence packet root is not a real directory")

    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | os.O_NOFOLLOW
    )
    directory_flags = file_flags | os.O_DIRECTORY
    current_descriptor = -1
    try:
        try:
            current_descriptor = os.open(packet_root, directory_flags)
            opened_root = os.fstat(current_descriptor)
        except OSError as exc:
            raise ComponentInventoryError(
                f"cannot open v3 evidence packet root: {exc}"
            ) from exc
        if (
            not stat.S_ISDIR(opened_root.st_mode)
            or (opened_root.st_dev, opened_root.st_ino)
            != (root_metadata.st_dev, root_metadata.st_ino)
        ):
            raise ComponentInventoryError(
                "v3 evidence packet root changed while opening"
            )
        for part in relative.parts[:-1]:
            try:
                next_descriptor = os.open(
                    part,
                    directory_flags,
                    dir_fd=current_descriptor,
                )
            except OSError as exc:
                raise ComponentInventoryError(
                    f"{label} nested evidence parent is missing, changed, or "
                    f"an unsafe symlink: {part}"
                ) from exc
            try:
                next_metadata = os.fstat(next_descriptor)
            except OSError as exc:
                os.close(next_descriptor)
                raise ComponentInventoryError(
                    f"cannot inspect {label} nested evidence parent: {exc}"
                ) from exc
            if not stat.S_ISDIR(next_metadata.st_mode):
                os.close(next_descriptor)
                raise ComponentInventoryError(
                    f"{label} nested evidence parent is not a directory"
                )
            os.close(current_descriptor)
            current_descriptor = next_descriptor
        try:
            descriptor = os.open(
                relative.parts[-1],
                file_flags,
                dir_fd=current_descriptor,
            )
        except OSError as exc:
            raise ComponentInventoryError(
                f"{label} nested evidence is missing, changed, or an unsafe symlink"
            ) from exc
    finally:
        if current_descriptor >= 0:
            os.close(current_descriptor)

    try:
        opened = os.fstat(descriptor)
    except OSError as exc:
        os.close(descriptor)
        raise ComponentInventoryError(
            f"cannot inspect {label} nested evidence: {exc}"
        ) from exc
    if not stat.S_ISREG(opened.st_mode):
        os.close(descriptor)
        raise ComponentInventoryError(f"{label} nested evidence is not a regular file")
    if opened.st_nlink != 1:
        os.close(descriptor)
        raise ComponentInventoryError(
            f"{label} nested evidence must not be hard-linked"
        )
    return descriptor


def _authenticate_v3_evidence_file(
    packet_root: Path,
    retained_path: str,
    expected_sha256: str,
    expected_size: int,
    label: str,
) -> None:
    descriptor = _open_v3_evidence_descriptor(packet_root, retained_path, label)
    try:
        before = os.fstat(descriptor)
        if before.st_size != expected_size:
            raise ComponentInventoryError(f"{label} nested evidence size mismatch")
        digest = hashlib.sha256()
        observed_size = 0
        while observed_size <= expected_size:
            try:
                chunk = os.read(
                    descriptor,
                    min(1024 * 1024, expected_size - observed_size + 1),
                )
            except OSError as exc:
                raise ComponentInventoryError(
                    f"cannot hash {label} nested evidence: {exc}"
                ) from exc
            if not chunk:
                break
            observed_size += len(chunk)
            if observed_size > expected_size:
                raise ComponentInventoryError(
                    f"{label} nested evidence size mismatch"
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
    except OSError as exc:
        raise ComponentInventoryError(
            f"cannot inspect {label} nested evidence while hashing: {exc}"
        ) from exc
    finally:
        os.close(descriptor)

    identity_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_nlink",
        "st_size",
        "st_mtime_ns",
    )
    if any(
        getattr(before, field) != getattr(after, field)
        for field in identity_fields
    ):
        raise ComponentInventoryError(
            f"{label} nested evidence changed while hashing"
        )
    if observed_size != expected_size:
        raise ComponentInventoryError(f"{label} nested evidence size mismatch")
    if digest.hexdigest() != expected_sha256:
        raise ComponentInventoryError(f"{label} nested evidence SHA-256 mismatch")

    revalidation_descriptor = _open_v3_evidence_descriptor(
        packet_root,
        retained_path,
        label,
    )
    try:
        try:
            revalidated = os.fstat(revalidation_descriptor)
        except OSError as exc:
            raise ComponentInventoryError(
                f"cannot revalidate {label} nested evidence: {exc}"
            ) from exc
    finally:
        os.close(revalidation_descriptor)
    if any(
        getattr(before, field) != getattr(revalidated, field)
        for field in identity_fields
    ):
        raise ComponentInventoryError(
            f"{label} nested evidence changed after hashing"
        )


def _load_python_contributor_metadata(
    metadata: dict[str, object],
    packet_root: Path,
) -> _MetadataEvidence:
    _expect_fields(
        metadata,
        {
            "schema",
            "collect_entry_count",
            "component_count",
            "assigned_collected_entry_count",
            "unassigned_collected_entry_count",
            "components",
            "unassigned_collected_entries",
        },
        "Python contributor metadata",
    )
    components = _object_list(metadata["components"], "Python components")
    unassigned_rows = _object_list(
        metadata["unassigned_collected_entries"],
        "unassigned collected entries",
    )
    _metadata_count(metadata["component_count"], len(components), "component_count")

    result: dict[str, dict[str, object]] = {}
    assigned: list[dict[str, object]] = []
    observed_component_order: list[str] = []
    expected_component_fields = {
        "canonical_name",
        "collected_files",
        "component_id",
        "license_candidates",
        "license_files",
        "name",
        "provider_candidates",
        "purl",
        "requires_python",
        "version",
    }
    for component_index, component in enumerate(components):
        label = f"Python component {component_index}"
        _expect_fields(component, expected_component_fields, label)
        canonical_name = _required_string(
            component["canonical_name"],
            f"{label}.canonical_name",
        )
        if not _NORMALIZED_DISTRIBUTION_PATTERN.fullmatch(canonical_name):
            raise ComponentInventoryError(
                f"{label}.canonical_name is not normalized"
            )
        version = _required_string(component["version"], f"{label}.version")
        if not _DISTRIBUTION_VERSION_PATTERN.fullmatch(version):
            raise ComponentInventoryError(f"{label}.version is unsafe")
        component_id = _required_string(
            component["component_id"],
            f"{label}.component_id",
        )
        if component_id != f"library:{canonical_name}@{version}":
            raise ComponentInventoryError(f"{label}.component_id is inconsistent")
        if component_id in result:
            raise ComponentInventoryError(
                f"duplicate metadata component: {component_id}"
            )
        name = _required_string(component["name"], f"{label}.name")
        purl = _required_string(component["purl"], f"{label}.purl")
        if purl != f"pkg:pypi/{canonical_name}@{version}":
            raise ComponentInventoryError(f"{label}.purl is inconsistent")
        _nullable_metadata_string(
            component["requires_python"],
            f"{label}.requires_python",
        )

        provider_rows = _object_list(
            component["provider_candidates"],
            f"{label}.provider_candidates",
        )
        provider_pairs: list[tuple[str, str]] = []
        for provider in provider_rows:
            _expect_fields(provider, {"field", "value"}, f"{label}.provider")
            field = _required_string(provider["field"], f"{label}.provider.field")
            if field not in {
                "Author",
                "Author-email",
                "Maintainer",
                "Maintainer-email",
            }:
                raise ComponentInventoryError(
                    f"{label}.provider field is not an accepted raw metadata field"
                )
            provider_pairs.append(
                (field, _required_string(provider["value"], f"{label}.provider.value"))
            )
        if provider_pairs != sorted(provider_pairs) or len(provider_pairs) != len(
            set(provider_pairs)
        ):
            raise ComponentInventoryError(
                f"{label}.provider_candidates must be unique and sorted"
            )

        license_candidates = _required_object(
            component["license_candidates"],
            f"{label}.license_candidates",
        )
        _expect_fields(
            license_candidates,
            {"classifiers", "expression", "field_first_line"},
            f"{label}.license_candidates",
        )
        classifiers = _sorted_strings(
            license_candidates["classifiers"],
            f"{label}.license_candidates.classifiers",
        )
        expression = _nullable_metadata_string(
            license_candidates["expression"],
            f"{label}.license_candidates.expression",
        )
        first_line = _nullable_metadata_string(
            license_candidates["field_first_line"],
            f"{label}.license_candidates.field_first_line",
        )
        exact_license_candidates = tuple(
            sorted(
                set(
                    classifiers
                    + ([expression] if expression is not None else [])
                    + ([first_line] if first_line is not None else [])
                )
            )
        )

        license_rows = _object_list(
            component["license_files"],
            f"{label}.license_files",
        )
        observed_license_order: list[str] = []
        retained_paths: set[str] = set()
        for license_row in license_rows:
            _expect_fields(
                license_row,
                {"distribution_path", "retained_path", "sha256", "size"},
                f"{label}.license_file",
            )
            distribution_path = _canonical_path(
                license_row["distribution_path"],
                f"{label}.license_file.distribution_path",
            )
            retained_path = _canonical_path(
                license_row["retained_path"],
                f"{label}.license_file.retained_path",
            )
            if not retained_path.startswith("build/evidence/python-distributions/"):
                raise ComponentInventoryError(
                    f"{label}.license_file must be retained below Python evidence"
                )
            if retained_path in retained_paths:
                raise ComponentInventoryError(
                    f"{label}.license_file retained paths must be unique"
                )
            retained_paths.add(retained_path)
            expected_sha256 = _validate_sha256(
                license_row["sha256"],
                f"{label}.license_file.sha256",
            )
            expected_size = license_row["size"]
            if (
                isinstance(expected_size, bool)
                or not isinstance(expected_size, int)
                or expected_size < 0
            ):
                raise ComponentInventoryError(f"{label}.license_file.size is invalid")
            retained_file = _contained_path(
                packet_root,
                retained_path,
                f"{label}.retained license",
                expected="file",
            )
            actual_sha256 = sha256_file(retained_file)
            if actual_sha256 != expected_sha256:
                raise ComponentInventoryError(
                    f"{label} retained license SHA-256 mismatch"
                )
            if retained_file.stat().st_size != expected_size:
                raise ComponentInventoryError(
                    f"{label} retained license size mismatch"
                )
            observed_license_order.append(distribution_path)
        if observed_license_order != sorted(observed_license_order) or len(
            observed_license_order
        ) != len(set(observed_license_order)):
            raise ComponentInventoryError(
                f"{label}.license_files must be unique and sorted"
            )

        collected_rows = _object_list(
            component["collected_files"],
            f"{label}.collected_files",
        )
        if not collected_rows:
            raise ComponentInventoryError(
                f"{label} must contain at least one collected contributor"
            )
        normalized_collected = [
            {
                **_load_v2_collected_row(
                    row,
                    f"{label}.collected_file",
                    assigned=True,
                ),
                "component_id": component_id,
            }
            for row in collected_rows
        ]
        for row in normalized_collected:
            expected_locator = (
                f"python-distribution/{canonical_name}/{row['distribution_path']}"
            )
            if row["source_locator"] != expected_locator:
                raise ComponentInventoryError(
                    f"{label}.collected_file source_locator is inconsistent"
                )
        collected_order = [str(row["final_path"]) for row in normalized_collected]
        if collected_order != sorted(collected_order) or len(collected_order) != len(
            set(collected_order)
        ):
            raise ComponentInventoryError(
                f"{label}.collected_files must be unique and sorted"
            )
        assigned.extend(normalized_collected)
        result[component_id] = {
            "_evidence_schema": DISTRIBUTION_METADATA_V2_SCHEMA,
            "component_id": component_id,
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "provider_candidates": tuple(value for _field, value in provider_pairs),
            "license_declared_candidates": exact_license_candidates,
        }
        observed_component_order.append(component_id)

    if observed_component_order != sorted(observed_component_order):
        raise ComponentInventoryError("Python components must be sorted")

    normalized_unassigned = [
        _load_v2_collected_row(
            row,
            "unassigned collected entry",
            assigned=False,
        )
        for row in unassigned_rows
    ]
    for row in normalized_unassigned:
        expected_locator = (
            "unassigned-source/" + str(row["raw_source_text_sha256"])
        )
        if row["source_locator"] != expected_locator:
            raise ComponentInventoryError(
                "unassigned collected source_locator is inconsistent"
            )
    unassigned_order = [str(row["final_path"]) for row in normalized_unassigned]
    if unassigned_order != sorted(unassigned_order) or len(unassigned_order) != len(
        set(unassigned_order)
    ):
        raise ComponentInventoryError(
            "unassigned collected entries must be unique and sorted"
        )
    assigned_paths = [str(row["final_path"]) for row in assigned]
    if len(assigned_paths) != len(set(assigned_paths)) or set(assigned_paths).intersection(
        unassigned_order
    ):
        raise ComponentInventoryError(
            "v2 assigned/unassigned collected paths must be globally unique"
        )
    _metadata_count(
        metadata["assigned_collected_entry_count"],
        len(assigned),
        "assigned_collected_entry_count",
    )
    _metadata_count(
        metadata["unassigned_collected_entry_count"],
        len(normalized_unassigned),
        "unassigned_collected_entry_count",
    )
    _metadata_count(
        metadata["collect_entry_count"],
        len(assigned) + len(normalized_unassigned),
        "collect_entry_count",
    )
    return _MetadataEvidence(
        components=result,
        assigned_collected_entries=tuple(assigned),
        unassigned_collected_entries=tuple(normalized_unassigned),
        requires_exact_collect_coverage=True,
    )


def _load_composite_component_metadata(
    metadata: dict[str, object],
    packet_root: Path,
) -> _MetadataEvidence:
    _expect_fields(
        metadata,
        {
            "schema",
            "collect_entry_count",
            "component_count",
            "assigned_collected_entry_count",
            "unassigned_collected_entry_count",
            "components",
            "unassigned_collected_entries",
        },
        "composite component metadata",
    )
    components = _object_list(metadata["components"], "v3 components")
    unassigned_rows = _object_list(
        metadata["unassigned_collected_entries"],
        "v3 unassigned collected entries",
    )
    _metadata_count(metadata["component_count"], len(components), "component_count")

    result: dict[str, dict[str, object]] = {}
    assigned: list[dict[str, object]] = []
    component_order: list[str] = []
    retained_paths: set[str] = set()
    aggregate_evidence_size = 0
    component_fields = {
        "collected_files",
        "component_id",
        "ecosystem",
        "evidence_files",
        "license_candidates",
        "name",
        "provider_candidates",
        "purl",
        "type",
        "version",
    }
    for component_index, component in enumerate(components):
        label = f"v3 component {component_index}"
        _expect_fields(component, component_fields, label)
        component_id = _required_string(
            component["component_id"],
            f"{label}.component_id",
        )
        if component_id in result:
            raise ComponentInventoryError(
                f"duplicate metadata component: {component_id}"
            )
        ecosystem = _required_string(component["ecosystem"], f"{label}.ecosystem")
        component_type = _required_string(component["type"], f"{label}.type")
        name = _required_string(component["name"], f"{label}.name")
        version = _required_string(component["version"], f"{label}.version")
        purl = _required_string(component["purl"], f"{label}.purl")
        provider_candidates = _load_v3_candidate_values(
            component["provider_candidates"],
            f"{label}.provider_candidates",
        )
        license_candidates = _load_v3_candidate_values(
            component["license_candidates"],
            f"{label}.license_candidates",
        )

        evidence_rows = _object_list(
            component["evidence_files"],
            f"{label}.evidence_files",
        )
        normalized_evidence: list[dict[str, object]] = []
        component_origin_paths: list[str] = []
        for evidence_index, evidence_row in enumerate(evidence_rows):
            normalized, retained_path, evidence_size = _load_v3_evidence_file(
                evidence_row,
                f"{label}.evidence_file {evidence_index}",
                packet_root,
            )
            if retained_path in retained_paths:
                raise ComponentInventoryError(
                    "v3 nested evidence retained paths must be globally unique"
                )
            retained_paths.add(retained_path)
            aggregate_evidence_size += evidence_size
            if aggregate_evidence_size > _MAX_TOTAL_COMPONENT_EVIDENCE_BYTES:
                raise ComponentInventoryError(
                    "v3 nested evidence exceeds aggregate size limit"
                )
            normalized_evidence.append(normalized)
            component_origin_paths.append(retained_path)
        evidence_order = [
            (
                str(row["kind"]),
                str(row["source_locator"]),
                str(row["retained_path"]),
            )
            for row in normalized_evidence
        ]
        if evidence_order != sorted(evidence_order) or len(evidence_order) != len(
            set(evidence_order)
        ):
            raise ComponentInventoryError(
                f"{label}.evidence_files must be unique and sorted"
            )

        collected_rows = _object_list(
            component["collected_files"],
            f"{label}.collected_files",
        )
        if not collected_rows:
            raise ComponentInventoryError(
                f"{label} must contain at least one collected contributor"
            )
        normalized_collected = [
            {
                **_load_v3_collected_row(
                    row,
                    f"{label}.collected_file {row_index}",
                ),
                "component_id": component_id,
            }
            for row_index, row in enumerate(collected_rows)
        ]
        collected_order = [str(row["final_path"]) for row in normalized_collected]
        if collected_order != sorted(collected_order) or len(collected_order) != len(
            set(collected_order)
        ):
            raise ComponentInventoryError(
                f"{label}.collected_files must be unique and sorted"
            )
        collected_by_path = {
            str(row["final_path"]): row for row in normalized_collected
        }
        for row in normalized_collected:
            if row["entry_type"] != "symlink":
                continue
            target = collected_by_path.get(str(row["target_final_path"]))
            if target is None or target["entry_type"] != "regular-file":
                raise ComponentInventoryError(
                    f"{label} symlink target must be a regular row in the "
                    "same component"
                )
        assigned.extend(normalized_collected)
        result[component_id] = {
            "_evidence_schema": DISTRIBUTION_METADATA_V3_SCHEMA,
            "component_id": component_id,
            "ecosystem": ecosystem,
            "license_declared_candidates": license_candidates,
            "name": name,
            "origin_evidence_paths": tuple(component_origin_paths),
            "provider_candidates": provider_candidates,
            "purl": purl,
            "type": component_type,
            "version": version,
        }
        component_order.append(component_id)

    if component_order != sorted(component_order):
        raise ComponentInventoryError("v3 components must be sorted")

    normalized_unassigned = [
        _load_v3_collected_row(row, f"v3 unassigned entry {index}")
        for index, row in enumerate(unassigned_rows)
    ]
    unassigned_order = [str(row["final_path"]) for row in normalized_unassigned]
    if unassigned_order != sorted(unassigned_order) or len(unassigned_order) != len(
        set(unassigned_order)
    ):
        raise ComponentInventoryError(
            "v3 unassigned collected entries must be unique and sorted"
        )

    all_rows = assigned + normalized_unassigned
    final_paths = [str(row["final_path"]) for row in all_rows]
    identities = [
        (
            str(row["final_path"]),
            str(row["raw_source_text_sha256"]),
            str(row["toc_type"]),
        )
        for row in all_rows
    ]
    if len(final_paths) != len(set(final_paths)) or len(identities) != len(
        set(identities)
    ):
        raise ComponentInventoryError(
            "v3 assigned/unassigned COLLECT rows must be globally unique"
        )
    _metadata_count(
        metadata["assigned_collected_entry_count"],
        len(assigned),
        "assigned_collected_entry_count",
    )
    _metadata_count(
        metadata["unassigned_collected_entry_count"],
        len(normalized_unassigned),
        "unassigned_collected_entry_count",
    )
    _metadata_count(
        metadata["collect_entry_count"],
        len(all_rows),
        "collect_entry_count",
    )
    return _MetadataEvidence(
        components=result,
        assigned_collected_entries=tuple(assigned),
        unassigned_collected_entries=tuple(normalized_unassigned),
        requires_exact_collect_coverage=True,
    )


def _load_toc_index(
    toc: dict[str, object],
    *,
    packet_root: Path,
    manifest: ArtifactManifest,
    metadata_evidence: _MetadataEvidence,
) -> dict[tuple[str, str], dict[str, str]]:
    schema = toc.get("schema")
    if schema == PYINSTALLER_TOC_INDEX_SCHEMA:
        if metadata_evidence.requires_exact_collect_coverage:
            raise ComponentInventoryError(
                "exact contributor metadata requires retained raw COLLECT evidence"
            )
        return _load_normalized_toc_index(toc)
    if schema == PYINSTALLER_EVIDENCE_INDEX_SCHEMA:
        raw_entries = _load_retained_pyinstaller_evidence(toc, packet_root, manifest)
        if metadata_evidence.requires_exact_collect_coverage:
            return _apply_exact_collect_ownership(raw_entries, metadata_evidence)
        return raw_entries
    raise ComponentInventoryError(
        "unsupported PyInstaller TOC/evidence index schema"
    )


def _load_normalized_toc_index(
    toc: dict[str, object],
) -> dict[tuple[str, str], dict[str, str]]:
    _expect_fields(toc, {"schema", "entries"}, "PyInstaller TOC index")
    records = _object_list(toc["entries"], "TOC entries")
    result: dict[tuple[str, str], dict[str, str]] = {}
    order: list[tuple[str, str]] = []
    for record in records:
        _expect_fields(
            record,
            {"component_id", "entry_type", "final_path", "source_path"},
            "TOC entry",
        )
        component_id = _required_string(record["component_id"], "TOC component_id")
        entry_type = _required_string(record["entry_type"], "TOC entry_type")
        if entry_type not in {"regular-file", "symlink"}:
            raise ComponentInventoryError("TOC entry_type is unsupported")
        final_path = _canonical_path(record["final_path"], "TOC final_path")
        source_path = _canonical_path(record["source_path"], "TOC source_path")
        key = (component_id, final_path)
        if key in result:
            raise ComponentInventoryError(f"duplicate TOC entry: {component_id}:{final_path}")
        result[key] = {
            "component_id": component_id,
            "entry_type": entry_type,
            "final_path": final_path,
            "source_path": source_path,
        }
        order.append((final_path, component_id))
    if order != sorted(order):
        raise ComponentInventoryError("TOC entries must be sorted by final path/component")
    return result


def _load_retained_pyinstaller_evidence(
    index: dict[str, object],
    packet_root: Path,
    manifest: ArtifactManifest,
) -> dict[tuple[str, str], dict[str, str]]:
    _expect_fields(
        index,
        {"schema", "tocs", "warning"},
        "PyInstaller evidence index",
    )
    rows = _object_list(index["tocs"], "PyInstaller evidence TOCs")
    if len(rows) != len(_REQUIRED_PYINSTALLER_TOC_KINDS):
        raise ComponentInventoryError(
            "PyInstaller evidence must contain five retained TOCs"
        )
    toc_paths: dict[str, Path] = {}
    observed_kinds: list[str] = []
    for row in rows:
        _expect_fields(
            row,
            {"kind", "path", "sha256"},
            "PyInstaller evidence TOC",
        )
        kind = _required_string(row["kind"], "PyInstaller evidence TOC kind")
        observed_kinds.append(kind)
        toc_path = _reference_path(
            packet_root,
            {"path": row["path"], "sha256": row["sha256"]},
            f"{kind} TOC",
        )
        if (
            toc_path.parent != packet_root / "build/evidence/pyinstaller"
            or toc_path.suffix != ".toc"
        ):
            raise ComponentInventoryError(
                f"{kind} TOC must be retained below build/evidence/pyinstaller/"
            )
        toc_paths[kind] = toc_path
    if observed_kinds != list(_REQUIRED_PYINSTALLER_TOC_KINDS) or len(
        toc_paths
    ) != len(observed_kinds):
        raise ComponentInventoryError(
            "PyInstaller evidence TOC kinds/order are invalid"
        )

    warning = _required_object(index["warning"], "PyInstaller warning evidence")
    _expect_fields(
        warning,
        {"disposition", "path", "sha256"},
        "PyInstaller warning evidence",
    )
    if warning["disposition"] not in _PYINSTALLER_WARNING_DISPOSITIONS:
        raise ComponentInventoryError("PyInstaller warning disposition is invalid")
    warning_path = _reference_path(
        packet_root,
        {"path": warning["path"], "sha256": warning["sha256"]},
        "PyInstaller warning evidence",
    )
    if warning_path.parent != packet_root / "build/evidence/pyinstaller":
        raise ComponentInventoryError(
            "PyInstaller warning must be retained below build/evidence/pyinstaller/"
        )
    warning_has_bytes = warning_path.stat().st_size > 0
    if warning_has_bytes != (
        warning["disposition"] == "RETAINED_REVIEW_REQUIRED"
    ):
        raise ComponentInventoryError(
            "PyInstaller warning disposition disagrees with retained bytes"
        )
    return _load_collect_toc(toc_paths["COLLECT"], manifest)


def _apply_exact_collect_ownership(
    raw_entries: dict[tuple[str, str], dict[str, str]],
    metadata: _MetadataEvidence,
) -> dict[tuple[str, str], dict[str, str]]:
    observed_by_key: dict[tuple[str, str, str], dict[str, str]] = {}
    observed_by_path_type: dict[tuple[str, str], dict[str, str]] = {}
    for row in raw_entries.values():
        key = (
            row["final_path"],
            row["raw_source_text_sha256"],
            row["toc_type"],
        )
        if key in observed_by_key:
            raise ComponentInventoryError("duplicate retained raw COLLECT identity")
        observed_by_key[key] = row
        observed_by_path_type[(row["final_path"], row["toc_type"])] = row

    evidence_rows = (
        metadata.assigned_collected_entries + metadata.unassigned_collected_entries
    )
    evidence_keys = {
        (
            str(row["final_path"]),
            str(row["raw_source_text_sha256"]),
            str(row["toc_type"]),
        )
        for row in evidence_rows
    }
    if evidence_keys != set(observed_by_key):
        for row in evidence_rows:
            observed = observed_by_path_type.get(
                (str(row["final_path"]), str(row["toc_type"]))
            )
            if (
                observed is not None
                and observed["raw_source_text_sha256"]
                != row["raw_source_text_sha256"]
            ):
                raise ComponentInventoryError(
                    "raw source digest mismatch for " + str(row["final_path"])
                )
        raise ComponentInventoryError(
            "contributor evidence does not exactly cover retained COLLECT"
        )

    result: dict[tuple[str, str], dict[str, str]] = {}
    for row in metadata.assigned_collected_entries:
        observed = observed_by_key[
            (
                str(row["final_path"]),
                str(row["raw_source_text_sha256"]),
                str(row["toc_type"]),
            )
        ]
        if observed["entry_type"] != row["entry_type"]:
            raise ComponentInventoryError(
                "contributor entry type mismatch for " + str(row["final_path"])
            )
        component_id = str(row["component_id"])
        key = (component_id, str(row["final_path"]))
        if key in result:
            raise ComponentInventoryError("duplicate assigned COLLECT entry")
        result[key] = {
            "component_id": component_id,
            "entry_type": observed["entry_type"],
            "final_path": observed["final_path"],
            "source_path": str(row["source_locator"]),
            "raw_source_text_sha256": observed["raw_source_text_sha256"],
            "toc_type": observed["toc_type"],
        }
    for row in metadata.unassigned_collected_entries:
        observed = observed_by_key[
            (
                str(row["final_path"]),
                str(row["raw_source_text_sha256"]),
                str(row["toc_type"]),
            )
        ]
        if observed["entry_type"] != row["entry_type"]:
            raise ComponentInventoryError(
                "unassigned entry type mismatch for " + str(row["final_path"])
            )
        key = ("unassigned", str(row["final_path"]))
        result[key] = {
            "component_id": "unassigned",
            "entry_type": observed["entry_type"],
            "final_path": observed["final_path"],
            "source_path": str(row["source_locator"]),
            "raw_source_text_sha256": observed["raw_source_text_sha256"],
            "toc_type": observed["toc_type"],
        }
    return result


def _load_collect_toc(
    path: Path,
    manifest: ArtifactManifest,
) -> dict[tuple[str, str], dict[str, str]]:
    try:
        size = path.stat().st_size
        if size > _MAX_RETAINED_TOC_BYTES:
            raise ComponentInventoryError(
                f"COLLECT TOC exceeds {_MAX_RETAINED_TOC_BYTES} bytes"
            )
        raw = path.read_text(encoding="utf-8")
        value = ast.literal_eval(raw)
    except ComponentInventoryError:
        raise
    except (OSError, UnicodeDecodeError) as exc:
        raise ComponentInventoryError(f"cannot read COLLECT TOC: {exc}") from exc
    except (MemoryError, RecursionError, SyntaxError, ValueError) as exc:
        raise ComponentInventoryError(
            "COLLECT TOC is not a safe Python literal"
        ) from exc
    if (
        not isinstance(value, tuple)
        or len(value) != 1
        or not isinstance(value[0], list)
    ):
        raise ComponentInventoryError("COLLECT TOC root shape is invalid")

    manifest_entries = {
        entry.path: entry
        for entry in manifest.entries
        if entry.entry_type != "directory"
    }
    result: dict[tuple[str, str], dict[str, str]] = {}
    for raw_row in value[0]:
        if (
            not isinstance(raw_row, (tuple, list))
            or len(raw_row) != 3
            or any(not isinstance(item, str) or not item for item in raw_row)
        ):
            raise ComponentInventoryError("COLLECT TOC entry is invalid")
        destination, raw_source, toc_type = raw_row
        if toc_type not in _PYINSTALLER_COLLECT_TYPES:
            raise ComponentInventoryError(
                f"unsupported COLLECT TOC type: {toc_type}"
            )
        destination = _canonical_path(destination, "COLLECT destination")
        direct_path = destination
        internal_path = f"_internal/{destination}"
        candidates = [
            candidate
            for candidate in (direct_path, internal_path)
            if candidate in manifest_entries
        ]
        if len(candidates) != 1:
            raise ComponentInventoryError(
                f"COLLECT destination has no unique candidate path: {destination}"
            )
        final_path = candidates[0]
        entry_type = "symlink" if toc_type == "SYMLINK" else "regular-file"
        if manifest_entries[final_path].entry_type != entry_type:
            raise ComponentInventoryError(
                f"COLLECT entry type disagrees with candidate: {final_path}"
            )
        key = ("unassigned", final_path)
        if key in result:
            raise ComponentInventoryError(
                f"duplicate COLLECT final path: {final_path}"
            )
        result[key] = {
            "component_id": "unassigned",
            "entry_type": entry_type,
            "final_path": final_path,
            "source_path": f"artifact/{final_path}",
            "raw_source_text_sha256": hashlib.sha256(
                raw_source.encode("utf-8")
            ).hexdigest(),
            "toc_type": toc_type,
        }
    return result


def _component_reasons(
    rule: dict[str, object],
    metadata: dict[str, object] | None,
    toc_entries: dict[tuple[str, str], dict[str, str]],
    referenced_toc: set[tuple[str, str]],
    manifest_entries: dict[str, object],
    owners: dict[str, list[tuple[str, str]]],
    artifact_root: Path,
    notice_hashes: dict[str, str],
    authenticated_origin_paths: frozenset[str],
    allow_unassigned_toc_fallback: bool,
) -> list[str]:
    component_id = str(rule["component_id"])
    reasons: list[str] = []
    if metadata is None:
        reasons.append("exact distribution metadata is missing")
    elif metadata.get("_evidence_schema") in {
        DISTRIBUTION_METADATA_V2_SCHEMA,
        DISTRIBUTION_METADATA_V3_SCHEMA,
    }:
        for field in ("type", "name", "version", "purl"):
            if metadata[field] != rule[field]:
                reasons.append(f"metadata disagrees with policy field {field}")
        if rule["provider"] not in metadata["provider_candidates"]:
            reasons.append("distribution metadata does not establish provider")
        if rule["license_declared"] not in metadata["license_declared_candidates"]:
            reasons.append("distribution metadata does not establish license_declared")
        if rule["license_concluded"] not in metadata["license_declared_candidates"]:
            reasons.append("distribution metadata does not establish license_concluded")
    else:
        for field in (
            "type",
            "name",
            "version",
            "purl",
            "provider",
            "license_declared",
        ):
            if field not in metadata:
                reasons.append(f"distribution metadata does not establish {field}")
            elif metadata[field] != rule[field]:
                reasons.append(f"metadata disagrees with policy field {field}")
    origin_evidence = tuple(str(item) for item in rule["origin_evidence"])
    accepted_origin_paths = set(authenticated_origin_paths)
    if (
        metadata is not None
        and metadata.get("_evidence_schema") == DISTRIBUTION_METADATA_V3_SCHEMA
    ):
        accepted_origin_paths.update(
            str(item) for item in metadata["origin_evidence_paths"]
        )
    if not origin_evidence:
        reasons.append("policy origin_evidence is empty")
    for origin_path in origin_evidence:
        if origin_path not in accepted_origin_paths:
            reasons.append(
                f"origin_evidence is not receipt-authenticated: {origin_path}"
            )
    if rule["license_concluded"] == "NOASSERTION":
        reasons.append("license_concluded cannot be NOASSERTION")
    for source in rule["toc_sources"]:
        final_path = str(source["final_path"])
        key = (component_id, final_path)
        observed_key = key
        if key not in toc_entries and allow_unassigned_toc_fallback:
            observed_key = ("unassigned", final_path)
        observed = toc_entries.get(observed_key)
        expected = {
            "entry_type": source["entry_type"],
            "final_path": source["final_path"],
            "source_path": source["source_path"],
        }
        if observed is None or any(
            observed[field] != value for field, value in expected.items()
        ):
            reasons.append(f"TOC evidence mismatch for {final_path}")
        else:
            referenced_toc.add(observed_key)
        manifest_entry = manifest_entries.get(final_path)
        if (
            manifest_entry is not None
            and getattr(manifest_entry, "entry_type") != source["entry_type"]
        ):
            reasons.append(f"manifest entry type disagrees for {final_path}")
    notice_sha256 = rule["notice_sha256"]
    for notice_path in rule["notice_paths"]:
        path = str(notice_path)
        entry = manifest_entries.get(path)
        if entry is None or getattr(entry, "entry_type", None) != "regular-file":
            reasons.append(f"notice is not a regular candidate file: {path}")
            continue
        if owners.get(path) != [("non-component", "release-notice")]:
            reasons.append(f"notice lacks unique release-notice ownership: {path}")
        actual_sha256 = sha256_file(artifact_root / PurePosixPath(path))
        expected_sha256 = str(notice_sha256[path])
        if actual_sha256 != expected_sha256:
            reasons.append(f"notice SHA-256 mismatch: {path}")
        else:
            previous = notice_hashes.get(path)
            if previous is not None and previous != actual_sha256:
                reasons.append(f"notice hash conflict: {path}")
            notice_hashes[path] = actual_sha256
    return reasons


def _read_canonical_object(path: Path, label: str) -> dict[str, object]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComponentInventoryError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ComponentInventoryError(f"{label} must be a JSON object")
    if raw != canonical_json_bytes(value):
        raise ComponentInventoryError(f"{label} is not canonical JSON")
    return value


def _reference_path(root: Path, value: object, label: str) -> Path:
    reference = _required_object(value, label)
    _expect_fields(reference, {"path", "sha256"}, label)
    path = _contained_path(
        root,
        _required_string(reference["path"], f"{label}.path"),
        f"{label}.path",
        expected="file",
    )
    expected_sha256 = _validate_sha256(reference["sha256"], f"{label}.sha256")
    actual_sha256 = sha256_file(path)
    if actual_sha256 != expected_sha256:
        raise ComponentInventoryError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    return path


def _contained_path(root: Path, relative: str, label: str, *, expected: str) -> Path:
    canonical = _canonical_path(relative, label)
    current = root
    for part in PurePosixPath(canonical).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError as exc:
            raise ComponentInventoryError(f"{label} cannot be opened: {exc}") from exc
        if os.path.islink(current):
            raise ComponentInventoryError(f"{label} cannot traverse a symlink: {relative}")
        if part != PurePosixPath(canonical).parts[-1] and not current.is_dir():
            raise ComponentInventoryError(f"{label} parent is not a directory: {relative}")
        if metadata.st_nlink < 1:
            raise ComponentInventoryError(f"{label} has invalid metadata: {relative}")
    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ComponentInventoryError(f"{label} escapes packet root: {relative}") from exc
    if expected == "file" and not current.is_file():
        raise ComponentInventoryError(f"{label} is not a regular file: {relative}")
    if expected == "directory" and not current.is_dir():
        raise ComponentInventoryError(f"{label} is not a directory: {relative}")
    return current


def _create_inventory_directory(output: Path, packet_root: Path) -> None:
    if output.exists() or output.is_symlink():
        raise ComponentInventoryError(f"inventory output already exists: {output}")
    try:
        parent = output.parent.resolve(strict=True)
    except OSError as exc:
        raise ComponentInventoryError(f"inventory output parent is invalid: {exc}") from exc
    if parent != packet_root or output.name != "inventory":
        raise ComponentInventoryError("inventory output must be the packet inventory/ child")
    try:
        output.mkdir(mode=0o755)
    except OSError as exc:
        raise ComponentInventoryError(f"cannot create inventory output {output}: {exc}") from exc


def _real_directory(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ComponentInventoryError(f"{label} is not a real directory: {path}")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ComponentInventoryError(f"cannot resolve {label}: {path}: {exc}") from exc


def _canonical_paths(value: object, label: str) -> list[str]:
    paths = [_canonical_path(item, label) for item in _required_list(value, label)]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ComponentInventoryError(f"{label} must be unique and sorted")
    return paths


def _canonical_path(value: object, label: str) -> str:
    text = _required_string(value, label)
    pure = PurePosixPath(text)
    if (
        pure.is_absolute()
        or text != pure.as_posix()
        or "\\" in text
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ComponentInventoryError(f"{label} is not a canonical relative path: {text}")
    return text


def _sorted_strings(value: object, label: str) -> list[str]:
    values = [_required_string(item, label) for item in _required_list(value, label)]
    if values != sorted(values) or len(values) != len(set(values)):
        raise ComponentInventoryError(f"{label} must be unique and sorted")
    return values


def _object_list(value: object, label: str) -> list[dict[str, object]]:
    return [_required_object(item, label) for item in _required_list(value, label)]


def _required_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ComponentInventoryError(f"{label} must be a list")
    return value


def _required_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ComponentInventoryError(f"{label} must be an object with string keys")
    return value


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ComponentInventoryError(f"{label} must be a non-empty string")
    return value


def _validate_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ComponentInventoryError(f"{label} must be a lowercase SHA-256")
    return value


def _expect_fields(value: dict[str, object], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ComponentInventoryError(
            f"{label} fields mismatch: expected {sorted(expected)}, got {sorted(actual)}"
        )
