"""Deterministic, review-only component policy and notice proposals.

This module authenticates one completed build evidence set and projects only
explicit human decisions for a named component subset.  It never selects legal
facts, mutates the active policy/notice corpus, runs inventory, or promotes a
proposal.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from types import MappingProxyType
from typing import Mapping

from .component_inventory import (
    DISTRIBUTION_METADATA_V3_SCHEMA,
    InventoryRequest,
    _expect_fields,
    _load_build_context,
    _load_metadata,
    _object_list,
    _required_object as _object,
    _required_string as _string,
    _sorted_strings,
    _validate_sha256 as _digest_text,
)
from .component_evidence_io import (
    _canonical_relative_path,
    _exclusive_output_path,
    _read_root_bytes,
    _real_directory,
)
from .contracts import (
    BUILD_RECEIPT_SCHEMA,
    BuildReceipt,
    ReleaseContractError,
    canonical_json_bytes,
    write_canonical_json,
)


PROPOSAL_REPORT_SCHEMA = "easyqc-component-policy-proposal-report-v1"

_COMPONENT_LEDGER_SCHEMA = "easyqc-release-component-ledger-v1"
_COMPONENT_POLICY_SCHEMA = "easyqc-component-policy-v1"
_PYINSTALLER_EVIDENCE_INDEX_SCHEMA = (
    "easyqc-release-pyinstaller-evidence-index-v1"
)
_CURABLE_LEDGER_REASONS: Mapping[str, frozenset[str] | None] = MappingProxyType(
    {
        "unclassified-path": frozenset(
            {"manifest file or symlink has no exact policy owner"}
        ),
        "unused-metadata-component": frozenset(
            {"distribution metadata component has no policy rule"}
        ),
        "unused-toc-entry": frozenset(
            {"TOC entry is not consumed by an exact component rule"}
        ),
        "unresolved-component": frozenset(
            {
                "component rule owns no uniquely classified path",
                "distribution metadata does not establish license_concluded",
                "distribution metadata does not establish license_declared",
                "distribution metadata does not establish provider",
                "policy origin_evidence is empty",
            }
        ),
    }
)


class ComponentPolicyProposalError(ReleaseContractError):
    """Raised when proposal authority or an output boundary is invalid."""


@dataclass(frozen=True)
class NoticeDecision:
    """One explicit notice selection and its review-only destination."""

    retained_evidence_path: str
    source_path: str
    destination_path: str
    active_source_path: str
    proposal_path: str | None


@dataclass(frozen=True)
class ComponentDecision:
    """Human decisions for exactly one authenticated component."""

    component_id: str
    provider: str | None
    license_declared: str | None
    license_concluded: str | None
    dependencies: tuple[str, ...]
    notice: NoticeDecision | None


@dataclass(frozen=True)
class ProposalRequest:
    """One authority set, named batch, and isolated proposal destinations."""

    build_receipt_path: Path
    build_receipt_sha256: str
    component_ledger_path: Path
    component_ledger_sha256: str
    component_ids: tuple[str, ...]
    decisions: tuple[ComponentDecision, ...]
    output_root: Path
    active_policy_path: str
    policy_proposal_path: str | None
    report_path: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "build_receipt_path", Path(self.build_receipt_path))
        object.__setattr__(
            self,
            "component_ledger_path",
            Path(self.component_ledger_path),
        )
        object.__setattr__(self, "output_root", Path(self.output_root))
        object.__setattr__(self, "component_ids", tuple(self.component_ids))
        object.__setattr__(self, "decisions", tuple(self.decisions))


@dataclass(frozen=True)
class ProposalResult:
    """Deterministic proposal outcome without host-local absolute paths."""

    status: str
    candidate_id: str
    target: str
    component_ids: tuple[str, ...]
    output_digests: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.status not in {"READY", "BLOCKED"}:
            raise ComponentPolicyProposalError("proposal result status is invalid")
        object.__setattr__(
            self,
            "output_digests",
            MappingProxyType(dict(sorted(self.output_digests.items()))),
        )


@dataclass(frozen=True)
class _Authority:
    packet_root: Path
    receipt_model: BuildReceipt
    metadata: dict[str, object]
    input_digests: Mapping[str, str]


@dataclass(frozen=True)
class _ProjectedComponent:
    rule: dict[str, object]
    report: dict[str, object]
    notice_output: tuple[str, bytes] | None


def propose_component_policy_notice_batch(request: ProposalRequest) -> ProposalResult:
    """Authenticate one evidence set and create one isolated proposal batch.

    Input: a typed request naming exact receipt/ledger hashes, a sorted component
    subset, explicit candidate decisions, and new relative destinations.
    Output: a typed READY or BLOCKED result plus one canonical report; READY also
    creates one canonical policy proposal and explicitly requested exact notices.
    Side effects: creates only new, exclusive files below ``output_root``.
    Errors: authority, candidate, path, and destination conflicts fail before
    any output file is written.  Missing human decisions produce BLOCKED.
    Split trigger: selection, promotion, build, and inventory belong elsewhere.
    """

    try:
        return _propose(request)
    except ComponentPolicyProposalError:
        raise
    except ReleaseContractError as exc:
        raise ComponentPolicyProposalError(str(exc)) from exc


def _propose(request: ProposalRequest) -> ProposalResult:
    authority = _authenticate_authority(request)
    component_ids = _named_component_ids(request.component_ids)
    decisions = _decision_map(request.decisions, component_ids)
    components = _component_map(authority.metadata)

    unknown = sorted(set(component_ids) - set(components))
    if unknown:
        raise ComponentPolicyProposalError(
            "named components are absent from authenticated v3: " + ", ".join(unknown)
        )

    active_policy = _relative_text(request.active_policy_path, "active policy path")
    policy_proposal = _optional_proposal_path(
        request.policy_proposal_path,
        "policy proposal path",
    )
    report_path = _relative_text(request.report_path, "proposal report path")

    unresolved: list[dict[str, str]] = []
    projected: list[_ProjectedComponent] = []
    active_paths = {active_policy}
    declared_proposal_paths: list[str] = []
    for component_id in component_ids:
        item = _project_component(
            authority,
            components[component_id],
            decisions[component_id],
            unresolved,
        )
        projected.append(item)
        notice = decisions[component_id].notice
        if notice is not None:
            active_paths.add(
                _relative_text(
                    notice.active_source_path,
                    f"{component_id} active notice path",
                )
            )
            if notice.proposal_path is not None:
                declared_proposal_paths.append(
                    _optional_proposal_path(
                        notice.proposal_path,
                        f"{component_id} notice proposal path",
                    )
                    or ""
                )

    if policy_proposal is None:
        unresolved.append(
            {
                "code": "policy-proposal-destination-required",
                "component_id": "batch",
                "reason": "an explicit new policy proposal destination is required",
            }
        )
    else:
        declared_proposal_paths.append(policy_proposal)

    _validate_declared_destinations(
        report_path,
        tuple(declared_proposal_paths),
        frozenset(active_paths),
    )
    unresolved.sort(
        key=lambda row: (row["code"], row["component_id"], row["reason"])
    )
    status = "BLOCKED" if unresolved else "READY"

    policy: dict[str, object] | None = None
    proposal_outputs: dict[str, bytes] = {}
    if status == "READY":
        if policy_proposal is None:  # guarded by unresolved, retained for typing
            raise ComponentPolicyProposalError(
                "ready proposal has no policy destination"
            )
        policy = {
            "schema": _COMPONENT_POLICY_SCHEMA,
            "version": 1,
            "targets": [authority.receipt_model.target],
            "rules": [item.rule for item in projected],
            "non_component_rules": [],
        }
        proposal_outputs[policy_proposal] = canonical_json_bytes(policy)
        for item in projected:
            if item.notice_output is not None:
                path, data = item.notice_output
                if path in proposal_outputs:
                    raise ComponentPolicyProposalError(
                        f"duplicate proposal destination: {path}"
                    )
                proposal_outputs[path] = data
        _authenticate_active_notice_sources(request.output_root, projected, decisions)

    proposal_digests = {
        path: hashlib.sha256(data).hexdigest()
        for path, data in sorted(proposal_outputs.items())
    }
    report = {
        "schema": PROPOSAL_REPORT_SCHEMA,
        "status": status,
        "candidate_id": authority.receipt_model.candidate_id,
        "target": authority.receipt_model.target,
        "component_ids": list(component_ids),
        "input_digests": dict(authority.input_digests),
        "components": [item.report for item in projected],
        "unresolved": unresolved,
        "proposal_output_digests": proposal_digests,
    }

    actual_outputs = dict(proposal_outputs)
    actual_outputs[report_path] = canonical_json_bytes(report)
    destinations = _preflight_output_paths(request.output_root, actual_outputs)
    written_digests: dict[str, str] = {}
    for relative in sorted(proposal_outputs):
        destination = destinations[relative]
        data = proposal_outputs[relative]
        if relative == policy_proposal:
            if policy is None:
                raise ComponentPolicyProposalError("policy projection is missing")
            digest = write_canonical_json(destination, policy)
        else:
            digest = _write_atomic_bytes(destination, data, "notice proposal")
        if digest != hashlib.sha256(data).hexdigest():
            raise ComponentPolicyProposalError(
                f"written proposal digest mismatch: {relative}"
            )
        written_digests[relative] = digest

    report_digest = write_canonical_json(destinations[report_path], report)
    if report_digest != hashlib.sha256(actual_outputs[report_path]).hexdigest():
        raise ComponentPolicyProposalError("written proposal report digest mismatch")
    written_digests[report_path] = report_digest
    return ProposalResult(
        status=status,
        candidate_id=authority.receipt_model.candidate_id,
        target=authority.receipt_model.target,
        component_ids=component_ids,
        output_digests=written_digests,
    )


def _authenticate_authority(request: ProposalRequest) -> _Authority:
    _digest_text(request.build_receipt_sha256, "build receipt SHA-256")
    _digest_text(request.component_ledger_sha256, "component ledger SHA-256")
    receipt_path = request.build_receipt_path
    if receipt_path.name == "" or receipt_path.parent.name != "build":
        raise ComponentPolicyProposalError("build receipt must be below packet build/")
    packet_root = receipt_path.parent.parent
    _real_directory(packet_root, "proposal packet root")
    _real_directory(receipt_path.parent, "proposal packet build directory")
    receipt_relative = _canonical_relative_path(
        f"build/{receipt_path.name}",
        "build receipt path",
    )
    receipt_bytes, receipt_identity = _read_root_bytes(
        packet_root,
        receipt_relative,
        "build receipt",
    )
    if receipt_identity["sha256"] != request.build_receipt_sha256:
        raise ComponentPolicyProposalError("build receipt SHA-256 mismatch")
    receipt = _canonical_object(receipt_bytes, "build receipt")
    receipt_model = _build_receipt(receipt)
    context = _load_build_context(
        InventoryRequest(
            build_receipt_path=receipt_path,
            build_receipt_sha256=request.build_receipt_sha256,
            output_dir=packet_root / "unused-proposal-inventory",
        )
    )
    if context.manifest.candidate_id != receipt_model.candidate_id:
        raise ComponentPolicyProposalError("build context Candidate ID mismatch")
    _validate_policy_authority(context.policy, receipt_model.target)
    _expect_fields(
        context.toc_index,
        {"schema", "tocs", "warning"},
        "PyInstaller evidence index",
    )
    if context.toc_index["schema"] != _PYINSTALLER_EVIDENCE_INDEX_SCHEMA:
        raise ComponentPolicyProposalError(
            "PyInstaller evidence index schema is invalid"
        )
    _validate_metadata(context.metadata, context.packet_root)

    ledger_bytes, ledger_digest = _read_external_authority(
        request.component_ledger_path,
        request.component_ledger_sha256,
        "component ledger",
    )
    ledger = _canonical_object(ledger_bytes, "component ledger")
    _validate_ledger(ledger, receipt_model)
    return _Authority(
        packet_root=context.packet_root,
        receipt_model=receipt_model,
        metadata=context.metadata,
        input_digests=MappingProxyType(
            {
                "artifact_manifest": receipt_model.artifact_manifest_sha256,
                "build_receipt": request.build_receipt_sha256,
                "component_ledger": ledger_digest,
                "component_policy": receipt_model.component_policy_sha256,
                "distribution_metadata": (
                    receipt_model.distribution_metadata_sha256
                ),
                "pyinstaller_evidence_index": receipt_model.toc_index_sha256,
            }
        ),
    )


def _build_receipt(receipt: dict[str, object]) -> BuildReceipt:
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
    artifact = _object(receipt["artifact"], "artifact")
    _expect_fields(artifact, {"path", "manifest"}, "artifact")
    manifest = _reference(artifact["manifest"], "artifact.manifest")
    component_policy = _reference(receipt["component_policy"], "component_policy")
    evidence = _object(receipt["evidence"], "evidence")
    _expect_fields(evidence, {"toc_index", "distribution_metadata"}, "evidence")
    toc_index = _reference(evidence["toc_index"], "evidence.toc_index")
    metadata = _reference(
        evidence["distribution_metadata"],
        "evidence.distribution_metadata",
    )
    source = _object(receipt["source"], "source")
    _expect_fields(source, {"revision", "archive_sha256"}, "source")
    runtime = _object(receipt["runtime"], "runtime")
    _expect_fields(runtime, {"identity_sha256"}, "runtime")
    locks = _object(receipt["locks"], "locks")
    tools = _object(receipt["tools"], "tools")
    model = BuildReceipt(
        schema=_string(receipt["schema"], "schema"),
        status=_string(receipt["status"], "status"),
        evidence_class=_string(receipt["evidence_class"], "evidence_class"),
        target=_string(receipt["target"], "target"),
        candidate_id=_string(receipt["candidate_id"], "candidate_id"),
        artifact_path=_string(artifact["path"], "artifact.path"),
        artifact_manifest_path=manifest["path"],
        artifact_manifest_sha256=manifest["sha256"],
        component_policy_path=component_policy["path"],
        component_policy_sha256=component_policy["sha256"],
        toc_index_path=toc_index["path"],
        toc_index_sha256=toc_index["sha256"],
        distribution_metadata_path=metadata["path"],
        distribution_metadata_sha256=metadata["sha256"],
        source_revision=_string(source["revision"], "source.revision"),
        source_archive_sha256=_string(
            source["archive_sha256"],
            "source.archive_sha256",
        ),
        runtime_identity_sha256=_string(
            runtime["identity_sha256"],
            "runtime.identity_sha256",
        ),
        locks={str(key): value for key, value in locks.items()},
        tools={str(key): value for key, value in tools.items()},
    )
    if model.schema != BUILD_RECEIPT_SCHEMA or model.as_json_object() != receipt:
        raise ComponentPolicyProposalError("build receipt representation is invalid")
    return model


def _read_external_authority(
    path: Path,
    expected_digest: str,
    label: str,
) -> tuple[bytes, str]:
    candidate = Path(path)
    if candidate.name == "":
        raise ComponentPolicyProposalError(f"{label} path is invalid")
    parent = candidate.parent
    _real_directory(parent, f"{label} parent")
    raw, identity = _read_root_bytes(
        parent,
        _canonical_relative_path(candidate.name, f"{label} filename"),
        label,
    )
    digest = str(identity["sha256"])
    if digest != expected_digest:
        raise ComponentPolicyProposalError(f"{label} SHA-256 mismatch")
    return raw, digest


def _validate_policy_authority(policy: dict[str, object], target: str) -> None:
    _expect_fields(
        policy,
        {"schema", "version", "targets", "rules", "non_component_rules"},
        "component policy",
    )
    if policy["schema"] != _COMPONENT_POLICY_SCHEMA or policy["version"] != 1:
        raise ComponentPolicyProposalError("component policy schema/version mismatch")
    targets = _sorted_strings(policy["targets"], "component policy targets")
    if target not in targets:
        raise ComponentPolicyProposalError("component policy target mismatch")
    _object_list(policy["rules"], "component policy rules")
    _object_list(
        policy["non_component_rules"],
        "component policy non-component rules",
    )


def _validate_metadata(metadata: dict[str, object], packet_root: Path) -> None:
    if metadata.get("schema") != DISTRIBUTION_METADATA_V3_SCHEMA:
        raise ComponentPolicyProposalError("distribution metadata must be composite v3")
    validated = _load_metadata(metadata, packet_root)
    if validated.unassigned_collected_entries:
        raise ComponentPolicyProposalError(
            "distribution metadata has unassigned entries"
        )


def _validate_ledger(ledger: dict[str, object], receipt: BuildReceipt) -> None:
    _expect_fields(
        ledger,
        {
            "schema",
            "candidate_manifest_sha256",
            "target",
            "artifact",
            "components",
            "non_component_entries",
            "relationships",
            "classified_entry_count",
            "unresolved",
            "release_status",
        },
        "component ledger",
    )
    if ledger["schema"] != _COMPONENT_LEDGER_SCHEMA:
        raise ComponentPolicyProposalError("component ledger schema is invalid")
    if ledger["release_status"] != "FAIL":
        raise ComponentPolicyProposalError("proposal requires a diagnostic FAIL ledger")
    if ledger["candidate_manifest_sha256"] != receipt.candidate_id:
        raise ComponentPolicyProposalError("component ledger Candidate ID mismatch")
    if ledger["target"] != receipt.target:
        raise ComponentPolicyProposalError("component ledger target mismatch")
    unresolved = _object_list(ledger["unresolved"], "component ledger unresolved")
    if not unresolved:
        raise ComponentPolicyProposalError(
            "diagnostic FAIL ledger has no unresolved rows"
        )
    ordering: list[tuple[str, str, str]] = []
    for index, row in enumerate(unresolved):
        label = f"component ledger unresolved {index}"
        _expect_fields(row, {"code", "reason", "subject"}, label)
        code = _string(row["code"], f"{label}.code")
        reason = _string(row["reason"], f"{label}.reason")
        subject = _string(row["subject"], f"{label}.subject")
        allowed_reasons = _CURABLE_LEDGER_REASONS.get(code)
        if allowed_reasons is None or (
            code != "unresolved-component"
            and reason not in allowed_reasons
        ):
            raise ComponentPolicyProposalError(
                f"component ledger contains non-proposal-curable row: {code}"
            )
        if code == "unresolved-component" and not _curable_component_reason(reason):
            raise ComponentPolicyProposalError(
                "component ledger contains non-proposal-curable component reason"
            )
        ordering.append((code, subject, reason))
    if ordering != sorted(ordering) or len(ordering) != len(set(ordering)):
        raise ComponentPolicyProposalError(
            "component ledger unresolved rows must be unique and sorted"
        )


def _curable_component_reason(reason: str) -> bool:
    allowed = _CURABLE_LEDGER_REASONS["unresolved-component"]
    if allowed is not None and reason in allowed:
        return True
    return reason.startswith("notice lacks unique release-notice ownership: ")


def _named_component_ids(values: tuple[str, ...]) -> tuple[str, ...]:
    result = tuple(_string(value, "named component ID") for value in values)
    if not result or result != tuple(sorted(result)) or len(result) != len(set(result)):
        raise ComponentPolicyProposalError(
            "named component IDs must be non-empty, unique, and sorted"
        )
    return result


def _decision_map(
    decisions: tuple[ComponentDecision, ...],
    component_ids: tuple[str, ...],
) -> Mapping[str, ComponentDecision]:
    identifiers = tuple(decision.component_id for decision in decisions)
    if identifiers != component_ids:
        raise ComponentPolicyProposalError(
            "component decisions must exactly match named IDs in sorted order"
        )
    return MappingProxyType({decision.component_id: decision for decision in decisions})


def _component_map(metadata: dict[str, object]) -> Mapping[str, dict[str, object]]:
    components = _object_list(metadata["components"], "v3 components")
    return MappingProxyType(
        {_string(row["component_id"], "v3 component ID"): row for row in components}
    )


def _project_component(
    authority: _Authority,
    component: dict[str, object],
    decision: ComponentDecision,
    unresolved: list[dict[str, str]],
) -> _ProjectedComponent:
    component_id = decision.component_id
    provider_candidates = sorted(
        set(_candidate_values(component["provider_candidates"], "provider candidates"))
    )
    license_candidates = sorted(
        set(_candidate_values(component["license_candidates"], "license candidates"))
    )
    _selected_or_blocked(
        decision.provider,
        provider_candidates,
        "provider",
        component_id,
        unresolved,
    )
    _selected_or_blocked(
        decision.license_declared,
        license_candidates,
        "license-declared",
        component_id,
        unresolved,
    )
    _selected_or_blocked(
        decision.license_concluded,
        license_candidates,
        "license-concluded",
        component_id,
        unresolved,
    )
    if decision.license_concluded == "NOASSERTION":
        raise ComponentPolicyProposalError("license_concluded cannot be NOASSERTION")

    dependencies = tuple(decision.dependencies)
    if dependencies != tuple(sorted(dependencies)) or len(dependencies) != len(
        set(dependencies)
    ):
        raise ComponentPolicyProposalError(
            f"{component_id} dependencies must be unique and sorted"
        )
    for dependency in dependencies:
        _string(dependency, f"{component_id} dependency")
        if dependency == component_id:
            raise ComponentPolicyProposalError(
                f"{component_id} cannot depend on itself"
            )

    collected = _object_list(component["collected_files"], "collected files")
    file_paths = [str(row["final_path"]) for row in collected]
    toc_sources = [
        {
            "entry_type": row["entry_type"],
            "final_path": row["final_path"],
            "source_path": row["source_locator"],
        }
        for row in collected
    ]
    evidence_rows = _object_list(component["evidence_files"], "evidence files")
    origin_evidence = [str(row["retained_path"]) for row in evidence_rows]
    notice_output: tuple[str, bytes] | None = None
    selected_notice: dict[str, object] | None = None
    notice = decision.notice
    if notice is None:
        unresolved.append(
            {
                "code": "notice-decision-required",
                "component_id": component_id,
                "reason": "an explicit authenticated notice decision is required",
            }
        )
    else:
        retained = _relative_text(
            notice.retained_evidence_path,
            f"{component_id} retained notice evidence",
        )
        matching = [row for row in evidence_rows if row["retained_path"] == retained]
        if len(matching) != 1:
            raise ComponentPolicyProposalError(
                f"{component_id} notice evidence is not an exact v3 candidate"
            )
        evidence = matching[0]
        raw, identity = _read_root_bytes(
            authority.packet_root,
            _canonical_relative_path(retained, f"{component_id} notice evidence"),
            f"{component_id} notice evidence",
        )
        if (
            identity["sha256"] != evidence["sha256"]
            or len(raw) != evidence["size"]
        ):
            raise ComponentPolicyProposalError(
                f"{component_id} notice evidence identity mismatch"
            )
        source_path = _relative_text(
            notice.source_path,
            f"{component_id} notice source",
        )
        destination_path = _relative_text(
            notice.destination_path,
            f"{component_id} notice destination",
        )
        if not destination_path.startswith("THIRD_PARTY_LICENSES/"):
            raise ComponentPolicyProposalError(
                f"{component_id} notice destination is outside THIRD_PARTY_LICENSES/"
            )
        active_source_path = _relative_text(
            notice.active_source_path,
            f"{component_id} active notice path",
        )
        proposal_path = _optional_proposal_path(
            notice.proposal_path,
            f"{component_id} notice proposal path",
        )
        if proposal_path is not None:
            notice_output = (proposal_path, raw)
        selected_notice = {
            "retained_evidence_path": retained,
            "source_locator": evidence["source_locator"],
            "source_path": source_path,
            "destination_path": destination_path,
            "active_source_path": active_source_path,
            "proposal_path": proposal_path,
            "sha256": evidence["sha256"],
            "size": evidence["size"],
        }

    report = {
        "component_id": component_id,
        "candidates": {
            "provider": provider_candidates,
            "license": license_candidates,
            "notice_evidence": [
                {
                    "kind": row["kind"],
                    "retained_path": row["retained_path"],
                    "sha256": row["sha256"],
                    "size": row["size"],
                    "source_locator": row["source_locator"],
                }
                for row in evidence_rows
            ],
        },
        "decision": {
            "provider": decision.provider,
            "license_declared": decision.license_declared,
            "license_concluded": decision.license_concluded,
            "dependencies": list(dependencies),
            "notice": selected_notice,
        },
        "projection": {
            "file_paths": file_paths,
            "toc_sources": toc_sources,
            "origin_evidence": origin_evidence,
        },
    }
    digest = str(selected_notice["sha256"]) if selected_notice is not None else ""
    destination = (
        str(selected_notice["destination_path"])
        if selected_notice is not None
        else ""
    )
    source = str(selected_notice["source_path"]) if selected_notice is not None else ""
    rule = {
        "component_id": component_id,
        "type": component["type"],
        "name": component["name"],
        "version": component["version"],
        "purl": component["purl"],
        "provider": decision.provider,
        "file_paths": file_paths,
        "toc_sources": toc_sources,
        "origin_evidence": origin_evidence,
        "dependencies": list(dependencies),
        "license_declared": decision.license_declared,
        "license_concluded": decision.license_concluded,
        "notice_paths": [destination] if destination else [],
        "notice_sha256": {destination: digest} if destination else {},
        "notice_sources": (
            [{"source_path": source, "destination_path": destination, "sha256": digest}]
            if destination
            else []
        ),
    }
    return _ProjectedComponent(rule=rule, report=report, notice_output=notice_output)


def _selected_or_blocked(
    selected: str | None,
    candidates: list[str],
    field: str,
    component_id: str,
    unresolved: list[dict[str, str]],
) -> None:
    if selected is None:
        unresolved.append(
            {
                "code": f"{field}-decision-required",
                "component_id": component_id,
                "reason": "an explicit authenticated candidate decision is required",
            }
        )
        return
    _string(selected, f"{component_id} {field} decision")
    if selected not in candidates:
        raise ComponentPolicyProposalError(
            f"{component_id} {field} decision is not an authenticated candidate"
        )


def _authenticate_active_notice_sources(
    output_root: Path,
    projected: list[_ProjectedComponent],
    decisions: Mapping[str, ComponentDecision],
) -> None:
    for item in projected:
        component_id = str(item.rule["component_id"])
        notice = decisions[component_id].notice
        if notice is None or notice.proposal_path is not None:
            continue
        selected = item.report["decision"]
        if not isinstance(selected, dict) or not isinstance(
            selected.get("notice"),
            dict,
        ):
            raise ComponentPolicyProposalError("selected notice report is invalid")
        notice_report = selected["notice"]
        relative = _canonical_relative_path(
            notice.active_source_path,
            f"{component_id} active notice path",
        )
        raw, identity = _read_root_bytes(
            output_root,
            relative,
            f"{component_id} active notice",
        )
        if (
            identity["sha256"] != notice_report["sha256"]
            or len(raw) != notice_report["size"]
        ):
            raise ComponentPolicyProposalError(
                f"{component_id} active notice differs from selected evidence"
            )


def _validate_declared_destinations(
    report_path: str,
    proposal_paths: tuple[str, ...],
    active_paths: frozenset[str],
) -> None:
    all_outputs = (report_path,) + proposal_paths
    if len(all_outputs) != len(set(all_outputs)):
        raise ComponentPolicyProposalError(
            "proposal output destinations must be unique"
        )
    conflicts = sorted(set(all_outputs) & active_paths)
    if conflicts:
        raise ComponentPolicyProposalError(
            "active paths cannot be proposal outputs: " + ", ".join(conflicts)
        )


def _preflight_output_paths(
    output_root: Path,
    outputs: Mapping[str, bytes],
) -> Mapping[str, Path]:
    _real_directory(output_root, "proposal output root")
    destinations: dict[str, Path] = {}
    for relative in sorted(outputs):
        destinations[relative] = _exclusive_output_path(
            output_root,
            relative,
            f"proposal output {relative}",
        )
    return MappingProxyType(destinations)


def _write_atomic_bytes(path: Path, data: bytes, label: str) -> str:
    parent = path.parent
    _real_directory(parent, f"{label} parent")
    if path.exists() or path.is_symlink():
        raise ComponentPolicyProposalError(f"{label} already exists")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except FileExistsError as exc:
        raise ComponentPolicyProposalError(f"{label} already exists") from exc
    except OSError as exc:
        raise ComponentPolicyProposalError(
            f"cannot atomically write {label}: {exc}"
        ) from exc
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError as exc:
                raise ComponentPolicyProposalError(
                    f"cannot remove {label} temporary file: {exc}"
                ) from exc
    return hashlib.sha256(data).hexdigest()


def _canonical_object(raw: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComponentPolicyProposalError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ComponentPolicyProposalError(f"{label} must be a JSON object")
    if raw != canonical_json_bytes(value):
        raise ComponentPolicyProposalError(f"{label} is not canonical JSON")
    return value


def _reference(value: object, label: str) -> dict[str, str]:
    record = _object(value, label)
    _expect_fields(record, {"path", "sha256"}, label)
    path = _relative_text(record["path"], f"{label}.path")
    digest = _digest_text(record["sha256"], f"{label}.sha256")
    return {"path": path, "sha256": digest}


def _candidate_values(value: object, label: str) -> tuple[str, ...]:
    rows = _object_list(value, label)
    pairs: list[tuple[str, str]] = []
    for index, row in enumerate(rows):
        _expect_fields(row, {"field", "value"}, f"{label} {index}")
        pairs.append(
            (
                _string(row["field"], f"{label} {index}.field"),
                _string(row["value"], f"{label} {index}.value"),
            )
        )
    if pairs != sorted(pairs) or len(pairs) != len(set(pairs)):
        raise ComponentPolicyProposalError(f"{label} must be unique and sorted")
    return tuple(value for _field, value in pairs)


def _relative_text(value: object, label: str) -> str:
    return _canonical_relative_path(value, label).as_posix()


def _optional_proposal_path(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    path = _relative_text(value, label)
    if not path.endswith(".proposed"):
        raise ComponentPolicyProposalError(f"{label} must end in .proposed")
    return path


__all__ = [
    "PROPOSAL_REPORT_SCHEMA",
    "ComponentDecision",
    "ComponentPolicyProposalError",
    "NoticeDecision",
    "ProposalRequest",
    "ProposalResult",
    "propose_component_policy_notice_batch",
]
