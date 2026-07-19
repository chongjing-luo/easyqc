from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path

import pytest

from packaging_tools.artifact_manifest import create_artifact_manifest
from packaging_tools.component_policy_proposal import (
    PROPOSAL_REPORT_SCHEMA,
    ComponentDecision,
    ComponentPolicyProposalError,
    NoticeDecision,
    ProposalRequest,
    propose_component_policy_notice_batch,
)
from packaging_tools.contracts import canonical_json_bytes, sha256_file


COMPONENT_ID = "library:alpha@1.0.0"
NOTICE_BODY = b"Alpha exact license evidence\n"


@dataclass(frozen=True)
class SyntheticAuthority:
    packet_root: Path
    receipt_path: Path
    receipt_sha256: str
    metadata_path: Path
    ledger_path: Path
    ledger_sha256: str
    evidence_path: str


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _reference(path: Path, root: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
    }


def _authority(root: Path) -> SyntheticAuthority:
    packet = root / "packet"
    build = packet / "build"
    evidence = build / "evidence/components/python/alpha-1.0.0/LICENSE"
    evidence.parent.mkdir(parents=True)
    evidence.write_bytes(NOTICE_BODY)
    evidence_path = evidence.relative_to(packet).as_posix()

    artifact_root = packet / "artifact/EasyQC-fixture"
    artifact_root.mkdir(parents=True)
    artifact_path = artifact_root / "EasyQC"
    artifact_bytes = b"alpha-binary\n"
    artifact_path.write_bytes(artifact_bytes)
    artifact_sha256 = hashlib.sha256(artifact_bytes).hexdigest()
    artifact_manifest = create_artifact_manifest(artifact_root)
    manifest_path = build / "artifact-manifest.json"
    _write_json(manifest_path, artifact_manifest.as_json_object())
    candidate_id = artifact_manifest.candidate_id

    policy_path = build / "component-policy.json"
    _write_json(
        policy_path,
        {
            "schema": "easyqc-component-policy-v1",
            "version": 1,
            "targets": ["linux-x86_64"],
            "rules": [],
            "non_component_rules": [],
        },
    )
    toc_path = build / "pyinstaller-evidence-index.json"
    _write_json(
        toc_path,
        {
            "schema": "easyqc-release-pyinstaller-evidence-index-v1",
            "tocs": [],
            "warning": {
                "disposition": "RETAINED_NO_WARNINGS",
                "path": "build/evidence/pyinstaller/warn-easyqc.txt",
                "sha256": "0" * 64,
            },
        },
    )

    metadata_path = build / "distribution-metadata.json"
    _write_json(
        metadata_path,
        {
            "schema": "easyqc-release-distribution-metadata-v3",
            "collect_entry_count": 1,
            "component_count": 1,
            "assigned_collected_entry_count": 1,
            "unassigned_collected_entry_count": 0,
            "components": [
                {
                    "collected_files": [
                        {
                            "entry_type": "regular-file",
                            "final_path": "EasyQC",
                            "package_path": "alpha/EasyQC",
                            "raw_source_text_sha256": "1" * 64,
                            "source_identity": {
                                "entry_type": "regular-file",
                                "sha256": artifact_sha256,
                                "size": len(artifact_bytes),
                            },
                            "source_kind": "python-distribution-file",
                            "source_locator": "python-distribution/alpha/alpha/EasyQC",
                            "target_final_path": None,
                            "toc_type": "BINARY",
                        }
                    ],
                    "component_id": COMPONENT_ID,
                    "ecosystem": "python",
                    "evidence_files": [
                        {
                            "kind": "python-license",
                            "retained_path": evidence_path,
                            "sha256": sha256_file(evidence),
                            "size": len(NOTICE_BODY),
                            "source_locator": (
                                "python-distribution/alpha/"
                                "alpha-1.0.0.dist-info/LICENSE"
                            ),
                        }
                    ],
                    "license_candidates": [
                        {"field": "Classifier", "value": "BSD-3-Clause"},
                        {"field": "License-Expression", "value": "MIT"},
                    ],
                    "name": "alpha",
                    "provider_candidates": [
                        {"field": "Author", "value": "Alpha Authors"},
                        {"field": "Maintainer", "value": "Alpha Maintainers"},
                    ],
                    "purl": "pkg:pypi/alpha@1.0.0",
                    "type": "library",
                    "version": "1.0.0",
                }
            ],
            "unassigned_collected_entries": [],
        },
    )

    receipt_path = build / "build-receipt.json"
    _write_json(
        receipt_path,
        {
            "schema": "easyqc-release-build-receipt-v1",
            "status": "PASS",
            "evidence_class": "release",
            "target": "linux-x86_64",
            "candidate_id": candidate_id,
            "artifact": {
                "path": "artifact/EasyQC-fixture",
                "manifest": _reference(manifest_path, packet),
            },
            "component_policy": _reference(policy_path, packet),
            "evidence": {
                "toc_index": _reference(toc_path, packet),
                "distribution_metadata": _reference(metadata_path, packet),
            },
            "source": {
                "revision": "a" * 40,
                "archive_sha256": "b" * 64,
            },
            "runtime": {"identity_sha256": "c" * 64},
            "locks": {
                "runtime": "d" * 64,
                "build": "e" * 64,
                "test": "f" * 64,
            },
            "tools": {"python": "3.10.17", "pyinstaller": "6.21.0"},
        },
    )

    ledger_path = packet / "inventory/component-ledger.json"
    unresolved = [
        {
            "code": "unclassified-path",
            "reason": "manifest file or symlink has no exact policy owner",
            "subject": "EasyQC",
        },
        {
            "code": "unused-metadata-component",
            "reason": "distribution metadata component has no policy rule",
            "subject": COMPONENT_ID,
        },
        {
            "code": "unused-toc-entry",
            "reason": "TOC entry is not consumed by an exact component rule",
            "subject": f"{COMPONENT_ID}:EasyQC",
        },
    ]
    _write_json(
        ledger_path,
        {
            "schema": "easyqc-release-component-ledger-v1",
            "candidate_manifest_sha256": candidate_id,
            "target": "linux-x86_64",
            "artifact": {
                "manifest_schema": "easyqc-release-artifact-manifest-v1",
                "entry_count": 1,
                "classifiable_entry_count": 1,
                "regular_file_count": 1,
                "symlink_count": 0,
            },
            "components": [],
            "non_component_entries": [],
            "relationships": [],
            "classified_entry_count": 0,
            "unresolved": unresolved,
            "release_status": "FAIL",
        },
    )
    return SyntheticAuthority(
        packet_root=packet,
        receipt_path=receipt_path,
        receipt_sha256=sha256_file(receipt_path),
        metadata_path=metadata_path,
        ledger_path=ledger_path,
        ledger_sha256=sha256_file(ledger_path),
        evidence_path=evidence_path,
    )


def _decision(
    authority: SyntheticAuthority,
    *,
    provider: str | None = "Alpha Authors",
    license_declared: str | None = "MIT",
    license_concluded: str | None = "MIT",
    evidence_path: str | None = None,
    active_source_path: str = "easyqc/packaging/licenses/alpha.txt",
    proposal_path: str | None = "easyqc/packaging/licenses/alpha.txt.proposed",
) -> ComponentDecision:
    return ComponentDecision(
        component_id=COMPONENT_ID,
        provider=provider,
        license_declared=license_declared,
        license_concluded=license_concluded,
        dependencies=(),
        notice=NoticeDecision(
            retained_evidence_path=evidence_path or authority.evidence_path,
            source_path="packaging/licenses/alpha.txt",
            destination_path="THIRD_PARTY_LICENSES/alpha.txt",
            active_source_path=active_source_path,
            proposal_path=proposal_path,
        ),
    )


def _request(
    authority: SyntheticAuthority,
    output_root: Path,
    *,
    decisions: tuple[ComponentDecision, ...] | None = None,
    receipt_sha256: str | None = None,
    ledger_sha256: str | None = None,
    active_policy_path: str = "easyqc/packaging/component_policy.json",
    policy_proposal_path: str | None = (
        "easyqc/packaging/component_policy.json.proposed"
    ),
    report_path: str = "Tmp/run/proposal-report.json",
) -> ProposalRequest:
    (output_root / "easyqc/packaging/licenses").mkdir(parents=True, exist_ok=True)
    (output_root / "Tmp/run").mkdir(parents=True, exist_ok=True)
    active_policy = output_root / active_policy_path
    active_policy.parent.mkdir(parents=True, exist_ok=True)
    if not active_policy.exists():
        active_policy.write_bytes(b"active-policy-sentinel\n")
    return ProposalRequest(
        build_receipt_path=authority.receipt_path,
        build_receipt_sha256=receipt_sha256 or authority.receipt_sha256,
        component_ledger_path=authority.ledger_path,
        component_ledger_sha256=ledger_sha256 or authority.ledger_sha256,
        component_ids=(COMPONENT_ID,),
        decisions=decisions or (_decision(authority),),
        output_root=output_root,
        active_policy_path=active_policy_path,
        policy_proposal_path=policy_proposal_path,
        report_path=report_path,
    )


def _reload_receipt(authority: SyntheticAuthority) -> str:
    receipt = json.loads(authority.receipt_path.read_text(encoding="utf-8"))
    receipt["evidence"]["distribution_metadata"] = _reference(
        authority.metadata_path,
        authority.packet_root,
    )
    _write_json(authority.receipt_path, receipt)
    return sha256_file(authority.receipt_path)


def test_complete_named_batch_is_deterministic_and_exact(tmp_path: Path) -> None:
    authority = _authority(tmp_path / "authority")
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first = propose_component_policy_notice_batch(_request(authority, first_root))
    second = propose_component_policy_notice_batch(_request(authority, second_root))

    assert first.status == second.status == "READY"
    assert first.candidate_id == second.candidate_id
    assert first.output_digests == second.output_digests
    relative_outputs = (
        "easyqc/packaging/component_policy.json.proposed",
        "easyqc/packaging/licenses/alpha.txt.proposed",
        "Tmp/run/proposal-report.json",
    )
    for relative in relative_outputs:
        assert (first_root / relative).read_bytes() == (
            second_root / relative
        ).read_bytes()

    policy = json.loads(
        (first_root / relative_outputs[0]).read_text(encoding="utf-8")
    )
    rule = policy["rules"][0]
    assert policy["schema"] == "easyqc-component-policy-v1"
    assert rule["component_id"] == COMPONENT_ID
    assert rule["provider"] == "Alpha Authors"
    assert rule["license_declared"] == rule["license_concluded"] == "MIT"
    assert rule["file_paths"] == ["EasyQC"]
    assert rule["toc_sources"] == [
        {
            "entry_type": "regular-file",
            "final_path": "EasyQC",
            "source_path": "python-distribution/alpha/alpha/EasyQC",
        }
    ]
    assert rule["origin_evidence"] == [authority.evidence_path]
    assert (first_root / relative_outputs[1]).read_bytes() == NOTICE_BODY
    assert (first_root / "easyqc/packaging/component_policy.json").read_bytes() == (
        b"active-policy-sentinel\n"
    )

    report_bytes = (first_root / relative_outputs[2]).read_bytes()
    report = json.loads(report_bytes.decode("utf-8"))
    assert report["schema"] == PROPOSAL_REPORT_SCHEMA
    assert report["status"] == "READY"
    assert report["unresolved"] == []
    assert NOTICE_BODY not in report_bytes
    assert str(tmp_path).encode() not in report_bytes
    assert report_bytes == canonical_json_bytes(report)


def test_missing_or_ambiguous_decision_is_blocked_without_inference(
    tmp_path: Path,
) -> None:
    authority = _authority(tmp_path / "authority")
    decision = _decision(
        authority,
        provider=None,
        license_declared=None,
        license_concluded=None,
        proposal_path=None,
    )
    root = tmp_path / "blocked"
    result = propose_component_policy_notice_batch(
        _request(authority, root, decisions=(decision,))
    )

    assert result.status == "BLOCKED"
    assert not (root / "easyqc/packaging/component_policy.json.proposed").exists()
    assert not (root / "easyqc/packaging/licenses/alpha.txt.proposed").exists()
    report = json.loads((root / "Tmp/run/proposal-report.json").read_text())
    assert [item["code"] for item in report["unresolved"]] == [
        "license-concluded-decision-required",
        "license-declared-decision-required",
        "provider-decision-required",
    ]
    candidates = report["components"][0]["candidates"]
    assert candidates["provider"] == ["Alpha Authors", "Alpha Maintainers"]
    assert candidates["license"] == ["BSD-3-Clause", "MIT"]


@pytest.mark.parametrize("failure", ["receipt-digest", "noncurable", "unassigned"])
def test_authority_or_noncurable_ledger_mismatch_fails_before_output(
    tmp_path: Path,
    failure: str,
) -> None:
    authority = _authority(tmp_path / "authority")
    receipt_sha256 = authority.receipt_sha256
    ledger_sha256 = authority.ledger_sha256
    if failure == "receipt-digest":
        receipt_sha256 = "0" * 64
    elif failure == "noncurable":
        ledger = json.loads(authority.ledger_path.read_text())
        ledger["unresolved"].append(
            {
                "code": "duplicate-ownership",
                "reason": "manifest path has multiple policy owners",
                "subject": "EasyQC",
            }
        )
        ledger["unresolved"].sort(key=lambda item: (item["code"], item["subject"]))
        _write_json(authority.ledger_path, ledger)
        ledger_sha256 = sha256_file(authority.ledger_path)
    else:
        metadata = json.loads(authority.metadata_path.read_text())
        row = metadata["components"][0]["collected_files"].pop()
        metadata["unassigned_collected_entries"] = [row]
        metadata["assigned_collected_entry_count"] = 0
        metadata["unassigned_collected_entry_count"] = 1
        _write_json(authority.metadata_path, metadata)
        receipt_sha256 = _reload_receipt(authority)

    root = tmp_path / "failed"
    with pytest.raises(ComponentPolicyProposalError):
        propose_component_policy_notice_batch(
            _request(
                authority,
                root,
                receipt_sha256=receipt_sha256,
                ledger_sha256=ledger_sha256,
            )
        )
    assert not (root / "Tmp/run/proposal-report.json").exists()
    assert not (root / "easyqc/packaging/component_policy.json.proposed").exists()


@pytest.mark.parametrize("failure", ["provider", "notice", "escape"])
def test_forged_candidate_notice_or_unsafe_destination_fails_closed(
    tmp_path: Path,
    failure: str,
) -> None:
    authority = _authority(tmp_path / "authority")
    decision = _decision(authority)
    if failure == "provider":
        decision = replace(decision, provider="Invented Provider")
    elif failure == "notice":
        notice = replace(
            decision.notice,
            retained_evidence_path="build/evidence/components/forged/LICENSE",
        )
        decision = replace(decision, notice=notice)
    else:
        notice = replace(decision.notice, proposal_path="../escape.txt.proposed")
        decision = replace(decision, notice=notice)

    root = tmp_path / "failed"
    with pytest.raises(ComponentPolicyProposalError):
        propose_component_policy_notice_batch(
            _request(authority, root, decisions=(decision,))
        )
    assert not (root / "Tmp/run/proposal-report.json").exists()


@pytest.mark.parametrize("failure", ["existing", "active", "symlink"])
def test_existing_active_or_proposed_destination_is_never_overwritten(
    tmp_path: Path,
    failure: str,
) -> None:
    authority = _authority(tmp_path / "authority")
    root = tmp_path / "output"
    request = _request(authority, root)
    proposal = root / "easyqc/packaging/component_policy.json.proposed"
    if failure == "existing":
        proposal.write_bytes(b"existing-proposal\n")
    elif failure == "active":
        request = replace(
            request,
            policy_proposal_path="easyqc/packaging/component_policy.json",
        )
    else:
        target = tmp_path / "outside"
        target.mkdir()
        report_parent = root / "Tmp/run"
        report_parent.rmdir()
        report_parent.symlink_to(target, target_is_directory=True)

    with pytest.raises(ComponentPolicyProposalError):
        propose_component_policy_notice_batch(request)
    if failure == "existing":
        assert proposal.read_bytes() == b"existing-proposal\n"
    assert (root / "easyqc/packaging/component_policy.json").read_bytes() == (
        b"active-policy-sentinel\n"
    )
