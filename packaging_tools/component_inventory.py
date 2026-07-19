"""Exact-path component classification for one authenticated candidate.

This module owns the canonical component ledger only.  It does not build a
candidate, infer component identity from path patterns, emit SBOM/notices, or
run native commands.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
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
PYINSTALLER_TOC_INDEX_SCHEMA = "easyqc-release-pyinstaller-toc-index-v1"
UNRESOLVED_COMPONENTS_SCHEMA = "easyqc-release-unresolved-components-v1"

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")


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
    toc_path = _reference_path(packet_root, evidence["toc_index"], "evidence.toc_index")
    metadata_path = _reference_path(
        packet_root,
        evidence["distribution_metadata"],
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
    metadata = _load_metadata(context.metadata)
    toc_entries = _load_toc_index(context.toc_index)
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


def _load_metadata(metadata: dict[str, object]) -> dict[str, dict[str, object]]:
    _expect_fields(metadata, {"schema", "components"}, "distribution metadata")
    if metadata["schema"] != DISTRIBUTION_METADATA_SCHEMA:
        raise ComponentInventoryError(
            f"distribution metadata schema must be {DISTRIBUTION_METADATA_SCHEMA}"
        )
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


def _load_toc_index(toc: dict[str, object]) -> dict[tuple[str, str], dict[str, str]]:
    _expect_fields(toc, {"schema", "entries"}, "PyInstaller TOC index")
    if toc["schema"] != PYINSTALLER_TOC_INDEX_SCHEMA:
        raise ComponentInventoryError(
            f"TOC index schema must be {PYINSTALLER_TOC_INDEX_SCHEMA}"
        )
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


def _component_reasons(
    rule: dict[str, object],
    metadata: dict[str, object] | None,
    toc_entries: dict[tuple[str, str], dict[str, str]],
    referenced_toc: set[tuple[str, str]],
    manifest_entries: dict[str, object],
    owners: dict[str, list[tuple[str, str]]],
    artifact_root: Path,
    notice_hashes: dict[str, str],
) -> list[str]:
    component_id = str(rule["component_id"])
    reasons: list[str] = []
    if metadata is None:
        reasons.append("exact distribution metadata is missing")
    else:
        for field in ("type", "name", "version", "purl", "provider", "license_declared"):
            if metadata[field] != rule[field]:
                reasons.append(f"metadata disagrees with policy field {field}")
    if rule["license_concluded"] == "NOASSERTION":
        reasons.append("license_concluded cannot be NOASSERTION")
    for source in rule["toc_sources"]:
        final_path = str(source["final_path"])
        key = (component_id, final_path)
        observed = toc_entries.get(key)
        if observed != {"component_id": component_id, **source}:
            reasons.append(f"TOC evidence mismatch for {final_path}")
        else:
            referenced_toc.add(key)
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
