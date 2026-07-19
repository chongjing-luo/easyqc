from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import tarfile

import pytest

import packaging_tools.native_packet as native_packet_module
from packaging_tools.artifact_manifest import (
    ArtifactManifest,
    create_artifact_manifest,
    write_artifact_manifest,
)
from packaging_tools.component_inventory import (
    BUILD_RECEIPT_SCHEMA,
    COMPONENT_LEDGER_SCHEMA,
    COMPONENT_POLICY_SCHEMA,
    DISTRIBUTION_METADATA_SCHEMA,
    PYINSTALLER_TOC_INDEX_SCHEMA,
    ComponentInventoryError,
    InventoryRequest,
    generate_component_ledger,
)
from packaging_tools.contracts import (
    canonical_json_bytes,
    sha256_file,
)
from packaging_tools.inventory_outputs import (
    CYCLONEDX_SCHEMA_URL,
    INVENTORY_RECEIPT_SCHEMA,
    InventoryOutputError,
    emit_inventory_outputs,
    validate_cyclonedx_document,
)
from packaging_tools.native_packet import (
    NATIVE_EVIDENCE_PACKET_SCHEMA,
    NativePacketError,
    NativeVerificationRequest,
    assert_support_claim_allowed,
    create_fixture_native_packet,
)


COMPONENT_ID = "application:easyqc-fixture@1.0.0"
NOTICE_PATH = "THIRD_PARTY_LICENSES/easyqc-fixture.txt"
BUILD_HASHES = {
    "runtime": "d" * 64,
    "build": "e" * 64,
    "test": "f" * 64,
}


@dataclass(frozen=True)
class SyntheticPacket:
    root: Path
    artifact: Path
    manifest: ArtifactManifest
    build_receipt: Path
    build_receipt_sha256: str

    @property
    def inventory_dir(self) -> Path:
        return self.root / "inventory"

    @property
    def native_dir(self) -> Path:
        return self.root / "native"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))


def _reference(path: Path, root: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": sha256_file(path),
    }


def _component_rule(
    *,
    component_id: str = COMPONENT_ID,
    file_paths: list[str] | None = None,
    license_concluded: str = "MIT",
) -> dict[str, object]:
    owned_paths = file_paths or ["EasyQC", "EasyQC-link"]
    return {
        "component_id": component_id,
        "type": "application",
        "name": "EasyQC fixture",
        "version": "1.0.0",
        "purl": "pkg:generic/easyqc-fixture@1.0.0",
        "provider": "synthetic exact policy",
        "file_paths": owned_paths,
        "toc_sources": [
            {
                "entry_type": (
                    "regular-file" if final_path == "EasyQC" else "symlink"
                ),
                "final_path": final_path,
                "source_path": "fixture/EasyQC",
            }
            for final_path in owned_paths
        ],
        "origin_evidence": [
            "build/distribution-metadata.json",
            "build/pyinstaller-toc-index.json",
        ],
        "dependencies": [],
        "license_declared": "MIT",
        "license_concluded": license_concluded,
        "notice_paths": [NOTICE_PATH],
    }


def _metadata_component(component_id: str = COMPONENT_ID) -> dict[str, str]:
    return {
        "component_id": component_id,
        "type": "application",
        "name": "EasyQC fixture",
        "version": "1.0.0",
        "purl": "pkg:generic/easyqc-fixture@1.0.0",
        "provider": "synthetic exact policy",
        "license_declared": "MIT",
    }


def _build_synthetic_packet(
    root: Path,
    *,
    add_unclassified: bool = False,
    duplicate_ownership: bool = False,
    unresolved_license: bool = False,
) -> SyntheticPacket:
    artifact = root / "artifact/EasyQC-fixture"
    notice = artifact / NOTICE_PATH
    notice.parent.mkdir(parents=True)
    executable = artifact / "EasyQC"
    executable.write_bytes(b"fixture-app\n")
    executable.chmod(0o755)
    notice.write_bytes(b"EasyQC fixture MIT notice\n")
    notice.chmod(0o644)
    try:
        (artifact / "EasyQC-link").symlink_to("EasyQC")
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks are unavailable on this platform: {exc}")
    if add_unclassified:
        (artifact / "unclassified.bin").write_bytes(b"unclassified\n")

    build_dir = root / "build"
    build_dir.mkdir(parents=True)
    manifest = create_artifact_manifest(artifact)
    manifest_path = build_dir / "artifact-manifest.json"
    assert write_artifact_manifest(manifest_path, manifest) == manifest.candidate_id

    toc_entries: list[dict[str, str]] = [
        {
            "component_id": COMPONENT_ID,
            "entry_type": "regular-file",
            "final_path": "EasyQC",
            "source_path": "fixture/EasyQC",
        },
        {
            "component_id": COMPONENT_ID,
            "entry_type": "symlink",
            "final_path": "EasyQC-link",
            "source_path": "fixture/EasyQC",
        },
    ]
    metadata_components = [_metadata_component()]
    rules = [
        _component_rule(
            license_concluded=("NOASSERTION" if unresolved_license else "MIT")
        )
    ]
    if duplicate_ownership:
        duplicate_id = "application:duplicate-fixture@1.0.0"
        rules.append(_component_rule(component_id=duplicate_id, file_paths=["EasyQC"]))
        toc_entries.append(
            {
                "component_id": duplicate_id,
                "entry_type": "regular-file",
                "final_path": "EasyQC",
                "source_path": "fixture/EasyQC",
            }
        )
        metadata_components.append(_metadata_component(duplicate_id))

    toc_path = build_dir / "pyinstaller-toc-index.json"
    _write_json(
        toc_path,
        {
            "schema": PYINSTALLER_TOC_INDEX_SCHEMA,
            "entries": sorted(
                toc_entries,
                key=lambda item: (item["final_path"], item["component_id"]),
            ),
        },
    )
    metadata_path = build_dir / "distribution-metadata.json"
    _write_json(
        metadata_path,
        {
            "schema": DISTRIBUTION_METADATA_SCHEMA,
            "components": sorted(
                metadata_components,
                key=lambda item: item["component_id"],
            ),
        },
    )
    policy_path = root / "inputs/component-policy.json"
    for rule in rules:
        notice_digest = sha256_file(notice)
        rule["notice_sha256"] = {NOTICE_PATH: notice_digest}
        rule["notice_sources"] = [
            {
                "source_path": "packaging/licenses/easyqc-fixture.txt",
                "destination_path": NOTICE_PATH,
                "sha256": notice_digest,
            }
        ]
    _write_json(
        policy_path,
        {
            "schema": COMPONENT_POLICY_SCHEMA,
            "version": 1,
            "targets": ["linux-x86_64"],
            "rules": sorted(rules, key=lambda item: str(item["component_id"])),
            "non_component_rules": [
                {
                    "classification": "release-notice",
                    "file_paths": [NOTICE_PATH],
                }
            ],
        },
    )

    build_receipt = build_dir / "build-receipt.json"
    _write_json(
        build_receipt,
        {
            "schema": BUILD_RECEIPT_SCHEMA,
            "status": "PASS",
            "evidence_class": "fixture",
            "target": "linux-x86_64",
            "candidate_id": manifest.candidate_id,
            "artifact": {
                "path": artifact.relative_to(root).as_posix(),
                "manifest": _reference(manifest_path, root),
            },
            "component_policy": _reference(policy_path, root),
            "evidence": {
                "toc_index": _reference(toc_path, root),
                "distribution_metadata": _reference(metadata_path, root),
            },
            "source": {
                "revision": "a" * 40,
                "archive_sha256": "b" * 64,
            },
            "runtime": {"identity_sha256": "c" * 64},
            "locks": BUILD_HASHES,
            "tools": {"python": "3.10.17", "pyinstaller": "6.21.0"},
        },
    )
    return SyntheticPacket(
        root=root,
        artifact=artifact,
        manifest=manifest,
        build_receipt=build_receipt,
        build_receipt_sha256=sha256_file(build_receipt),
    )


def _inventory_request(packet: SyntheticPacket) -> InventoryRequest:
    return InventoryRequest(
        build_receipt_path=packet.build_receipt,
        build_receipt_sha256=packet.build_receipt_sha256,
        output_dir=packet.inventory_dir,
    )


def _complete_inventory(packet: SyntheticPacket):
    ledger = generate_component_ledger(_inventory_request(packet))
    receipt = emit_inventory_outputs(ledger, packet.inventory_dir)
    return ledger, receipt


def _native_request(packet: SyntheticPacket, inventory_receipt: Path) -> NativeVerificationRequest:
    return NativeVerificationRequest(
        build_receipt_path=packet.build_receipt,
        build_receipt_sha256=packet.build_receipt_sha256,
        inventory_receipt_path=inventory_receipt,
        inventory_receipt_sha256=sha256_file(inventory_receipt),
        output_dir=packet.native_dir,
    )


def _rewrite_policy_and_receipt(
    packet: SyntheticPacket,
    policy: dict[str, object],
) -> InventoryRequest:
    policy_path = packet.root / "inputs/component-policy.json"
    _write_json(policy_path, policy)
    receipt = json.loads(packet.build_receipt.read_text(encoding="utf-8"))
    receipt["component_policy"]["sha256"] = sha256_file(policy_path)
    _write_json(packet.build_receipt, receipt)
    return replace(
        _inventory_request(packet),
        build_receipt_sha256=sha256_file(packet.build_receipt),
    )


def _classified_paths(ledger_payload: dict[str, object]) -> list[str]:
    component_paths = [
        path
        for component in ledger_payload["components"]
        for path in component["file_paths"]
    ]
    non_component_paths = [
        path
        for record in ledger_payload["non_component_entries"]
        for path in record["file_paths"]
    ]
    return component_paths + non_component_paths


def test_complete_fixture_ledger_classifies_every_file_and_symlink_once(
    tmp_path: Path,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")

    ledger = generate_component_ledger(_inventory_request(packet))
    payload = ledger.as_json_object()
    manifest_paths = sorted(
        entry.path
        for entry in packet.manifest.entries
        if entry.entry_type != "directory"
    )

    assert payload["schema"] == COMPONENT_LEDGER_SCHEMA
    assert payload["candidate_manifest_sha256"] == packet.manifest.candidate_id
    assert payload["release_status"] == "PASS"
    assert payload["unresolved"] == []
    assert payload["classified_entry_count"] == len(manifest_paths)
    assert sorted(_classified_paths(payload)) == manifest_paths
    assert len(_classified_paths(payload)) == len(set(_classified_paths(payload)))
    assert (packet.inventory_dir / "component-ledger.json").read_bytes() == (
        canonical_json_bytes(payload)
    )


def test_full_fixture_throughput_is_byte_deterministic_and_never_claims_support(
    tmp_path: Path,
) -> None:
    first = _build_synthetic_packet(tmp_path / "first/packet")
    second = _build_synthetic_packet(tmp_path / "second/packet")

    first_ledger, first_receipt = _complete_inventory(first)
    second_ledger, second_receipt = _complete_inventory(second)
    first_native = create_fixture_native_packet(
        _native_request(first, first_receipt.path)
    )
    second_native = create_fixture_native_packet(
        _native_request(second, second_receipt.path)
    )

    assert first_ledger.candidate_id == second_ledger.candidate_id
    assert first_receipt.status == "PASS"
    assert first_receipt.as_json_object()["schema"] == INVENTORY_RECEIPT_SCHEMA
    assert first_native.as_json_object()["schema"] == NATIVE_EVIDENCE_PACKET_SCHEMA
    assert first_native.evidence_class == "fixture"
    assert first_native.support_claim is False
    assert first_native.support_matrix_status == "NOT RUN — fixture evidence"
    assert first_native.overall_status == "FIXTURE-ONLY"
    assert create_artifact_manifest(first.artifact).candidate_id == first.manifest.candidate_id

    for relative_path in (
        "component-ledger.json",
        "unresolved-components.json",
        "bom.cdx.json",
        "NOTICE_INDEX.md",
        "NOTICE_BUNDLE.tar",
        "inventory-receipt.json",
    ):
        first_bytes = (first.inventory_dir / relative_path).read_bytes()
        second_bytes = (second.inventory_dir / relative_path).read_bytes()
        assert first_bytes == second_bytes
        assert str(tmp_path).encode() not in first_bytes
    assert (first.native_dir / "evidence-packet.json").read_bytes() == (
        second.native_dir / "evidence-packet.json"
    ).read_bytes()
    assert str(tmp_path).encode() not in (
        first.native_dir / "evidence-packet.json"
    ).read_bytes()

    with tarfile.open(first.inventory_dir / "NOTICE_BUNDLE.tar", mode="r:") as archive:
        members = archive.getmembers()
    assert [member.name for member in members] == sorted(member.name for member in members)
    assert all(member.uid == 0 and member.gid == 0 for member in members)
    assert all(member.uname == "" and member.gname == "" for member in members)
    assert all(member.mtime == 0 for member in members)
    assert all(member.mode == (0o755 if member.isdir() else 0o644) for member in members)

    with pytest.raises(NativePacketError, match="fixture evidence cannot claim support"):
        replace(first_native, support_claim=True)
    with pytest.raises(NativePacketError, match="fixture evidence cannot be overall PASS"):
        replace(first_native, overall_status="PASS")
    with pytest.raises(NativePacketError, match="fixture evidence cannot claim support"):
        assert_support_claim_allowed(first_native)
    forged_rows = tuple(
        ({**row, "status": "PASS"} if row["name"] == "binary_closure" else row)
        for row in first_native.rows
    )
    with pytest.raises(NativePacketError, match="fixture evidence rows"):
        replace(first_native, rows=forged_rows)


@pytest.mark.parametrize(
    "fixture_options",
    [
        {"add_unclassified": True},
        {"duplicate_ownership": True},
        {"unresolved_license": True},
    ],
)
def test_unresolved_or_ambiguous_ledger_never_emits_a_pass_receipt(
    tmp_path: Path,
    fixture_options: dict[str, bool],
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet", **fixture_options)

    ledger = generate_component_ledger(_inventory_request(packet))

    assert ledger.release_status == "FAIL"
    assert ledger.unresolved
    assert (packet.inventory_dir / "component-ledger.json").is_file()
    assert (packet.inventory_dir / "unresolved-components.json").is_file()
    with pytest.raises(InventoryOutputError, match="complete PASS ledger"):
        emit_inventory_outputs(ledger, packet.inventory_dir)
    assert not (packet.inventory_dir / "inventory-receipt.json").exists()


def test_tracked_metadata_capture_seed_cannot_emit_pass_inventory(
    tmp_path: Path,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")
    policy_path = (
        Path(__file__).resolve().parents[2] / "packaging/component_policy.json"
    )
    seed_policy = json.loads(policy_path.read_text(encoding="utf-8"))

    ledger = generate_component_ledger(
        _rewrite_policy_and_receipt(packet, seed_policy)
    )

    assert ledger.release_status == "FAIL"
    assert ledger.unresolved
    with pytest.raises(InventoryOutputError, match="complete PASS ledger"):
        emit_inventory_outputs(ledger, packet.inventory_dir)
    assert not (packet.inventory_dir / "inventory-receipt.json").exists()


def test_changed_notice_or_candidate_is_rejected_without_a_receipt(
    tmp_path: Path,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")
    ledger = generate_component_ledger(_inventory_request(packet))
    (packet.artifact / NOTICE_PATH).write_bytes(b"changed notice bytes\n")

    with pytest.raises(InventoryOutputError, match="candidate identity mismatch"):
        emit_inventory_outputs(ledger, packet.inventory_dir)

    assert not (packet.inventory_dir / "inventory-receipt.json").exists()
    assert not (packet.inventory_dir / "NOTICE_BUNDLE.tar").exists()


def test_inventory_projection_rejects_notice_path_outside_candidate(
    tmp_path: Path,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")
    ledger = generate_component_ledger(_inventory_request(packet))
    outside_notice = tmp_path / "outside-notice.txt"
    outside_notice.write_bytes(b"must not enter the candidate notice bundle\n")
    outside_path = outside_notice.resolve().as_posix()
    escaped_component = replace(
        ledger.components[0],
        notice_paths=(outside_path,),
    )
    escaped_ledger = replace(
        ledger,
        components=(escaped_component,),
        notice_hashes=((outside_path, sha256_file(outside_notice)),),
    )
    _write_json(
        packet.inventory_dir / "component-ledger.json",
        escaped_ledger.as_json_object(),
    )

    with pytest.raises(InventoryOutputError, match="candidate-relative"):
        emit_inventory_outputs(escaped_ledger, packet.inventory_dir)
    assert not (packet.inventory_dir / "inventory-receipt.json").exists()


def test_inventory_projection_stays_below_the_originating_packet(
    tmp_path: Path,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")
    ledger = generate_component_ledger(_inventory_request(packet))
    unrelated_inventory = tmp_path / "unrelated/inventory"
    unrelated_inventory.mkdir(parents=True)
    _write_json(
        unrelated_inventory / "component-ledger.json",
        ledger.as_json_object(),
    )

    with pytest.raises(InventoryOutputError, match="originating packet"):
        emit_inventory_outputs(ledger, unrelated_inventory)
    assert not (unrelated_inventory / "inventory-receipt.json").exists()


def test_multicomponent_cyclonedx_dependency_order_does_not_depend_on_root_id(
    tmp_path: Path,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")
    ledger = generate_component_ledger(_inventory_request(packet))
    original = ledger.components[0]
    library_id = "a-library:fixture@1.0.0"
    application_id = "z-application:fixture@1.0.0"
    library = replace(
        original,
        component_id=library_id,
        component_type="library",
        name="Fixture library",
        purl="pkg:generic/fixture-library@1.0.0",
        file_paths=("EasyQC-link",),
        dependencies=(),
        notice_paths=(),
    )
    application = replace(
        original,
        component_id=application_id,
        file_paths=("EasyQC",),
        dependencies=(library_id,),
    )
    multicomponent_ledger = replace(
        ledger,
        components=(library, application),
        relationships=(
            {"from": application_id, "to": library_id, "type": "depends-on"},
        ),
    )
    _write_json(
        packet.inventory_dir / "component-ledger.json",
        multicomponent_ledger.as_json_object(),
    )

    receipt = emit_inventory_outputs(multicomponent_ledger, packet.inventory_dir)

    assert receipt.status == "PASS"
    validate_cyclonedx_document(
        json.loads(
            (packet.inventory_dir / "bom.cdx.json").read_text(encoding="utf-8")
        )
    )


def test_cyclonedx_profile_is_strict_and_output_is_valid_16(
    tmp_path: Path,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")
    _ledger, _receipt = _complete_inventory(packet)
    document = json.loads(
        (packet.inventory_dir / "bom.cdx.json").read_text(encoding="utf-8")
    )

    validate_cyclonedx_document(document)
    assert document["$schema"] == CYCLONEDX_SCHEMA_URL
    assert document["specVersion"] == "1.6"

    document["specVersion"] = "1.5"
    with pytest.raises(InventoryOutputError, match="CycloneDX 1.6"):
        validate_cyclonedx_document(document)


def test_receipt_hash_and_candidate_id_are_authenticated_before_inventory(
    tmp_path: Path,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")

    with pytest.raises(ComponentInventoryError, match="build receipt SHA-256"):
        generate_component_ledger(
            replace(_inventory_request(packet), build_receipt_sha256="0" * 64)
        )

    (packet.artifact / "EasyQC").write_bytes(b"mutated-app\n")
    with pytest.raises(ComponentInventoryError, match="candidate identity mismatch"):
        generate_component_ledger(_inventory_request(packet))
    assert not packet.inventory_dir.exists()


def test_candidate_mutation_or_inventory_hash_mismatch_blocks_native_packet(
    tmp_path: Path,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")
    _ledger, receipt = _complete_inventory(packet)

    bad_request = replace(
        _native_request(packet, receipt.path),
        inventory_receipt_sha256="0" * 64,
    )
    with pytest.raises(NativePacketError, match="inventory receipt SHA-256"):
        create_fixture_native_packet(bad_request)

    (packet.artifact / "EasyQC").write_bytes(b"mutated-app\n")
    with pytest.raises(NativePacketError, match="candidate identity mismatch"):
        create_fixture_native_packet(_native_request(packet, receipt.path))
    assert not packet.native_dir.exists()


def test_candidate_mutation_during_native_fixture_verification_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")
    _ledger, receipt = _complete_inventory(packet)
    authenticate_outputs = native_packet_module._authenticate_inventory_outputs

    def authenticate_then_mutate(*args, **kwargs):
        hashes = authenticate_outputs(*args, **kwargs)
        (packet.artifact / "EasyQC").write_bytes(b"mutated-during-verification\n")
        return hashes

    monkeypatch.setattr(
        native_packet_module,
        "_authenticate_inventory_outputs",
        authenticate_then_mutate,
    )

    with pytest.raises(NativePacketError, match="after fixture verification"):
        create_fixture_native_packet(_native_request(packet, receipt.path))
    assert not packet.native_dir.exists()


def test_native_packet_rejects_build_receipt_reached_through_parent_symlink(
    tmp_path: Path,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")
    _ledger, receipt = _complete_inventory(packet)
    external_build = tmp_path / "external-build"
    packet.build_receipt.parent.rename(external_build)
    try:
        packet.build_receipt.parent.symlink_to(external_build, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks are unavailable on this platform: {exc}")

    with pytest.raises(NativePacketError, match="real packet build"):
        create_fixture_native_packet(_native_request(packet, receipt.path))
    assert not packet.native_dir.exists()


def test_policy_rejects_duplicate_component_ids_and_ledgers_unused_rules(
    tmp_path: Path,
) -> None:
    duplicate_packet = _build_synthetic_packet(tmp_path / "duplicate/packet")
    duplicate_policy_path = duplicate_packet.root / "inputs/component-policy.json"
    duplicate_policy = json.loads(duplicate_policy_path.read_text(encoding="utf-8"))
    duplicate_rule = dict(duplicate_policy["rules"][0])
    duplicate_rule["file_paths"] = []
    duplicate_rule["toc_sources"] = []
    duplicate_policy["rules"].append(duplicate_rule)

    with pytest.raises(ComponentInventoryError, match="component IDs must be unique"):
        generate_component_ledger(
            _rewrite_policy_and_receipt(duplicate_packet, duplicate_policy)
        )

    unused_packet = _build_synthetic_packet(tmp_path / "unused/packet")
    unused_policy_path = unused_packet.root / "inputs/component-policy.json"
    unused_policy = json.loads(unused_policy_path.read_text(encoding="utf-8"))
    unused_policy["non_component_rules"].append(
        {"classification": "zzz-unused", "file_paths": []}
    )

    unused_ledger = generate_component_ledger(
        _rewrite_policy_and_receipt(unused_packet, unused_policy)
    )

    assert unused_ledger.release_status == "FAIL"
    assert any(
        item.code == "unused-non-component-rule" for item in unused_ledger.unresolved
    )


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("duplicate", "notice source destinations must be unique"),
        ("source-escape", "notice_sources.source_path"),
        ("destination-escape", "notice_sources.destination_path"),
        ("unused", "notice_sources must cover notice_paths exactly"),
        ("digest-mismatch", "notice_sources SHA-256 must match notice_sha256"),
    ],
)
def test_shared_policy_rejects_invalid_or_unused_notice_sources(
    tmp_path: Path,
    mutation: str,
    expected_error: str,
) -> None:
    packet = _build_synthetic_packet(tmp_path / "packet")
    policy_path = packet.root / "inputs/component-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    sources = policy["rules"][0]["notice_sources"]

    if mutation == "duplicate":
        sources.append(dict(sources[0]))
    elif mutation == "source-escape":
        sources[0]["source_path"] = "../outside-license.txt"
    elif mutation == "destination-escape":
        sources[0]["destination_path"] = "../outside-license.txt"
    elif mutation == "unused":
        sources[0]["destination_path"] = "THIRD_PARTY_LICENSES/unused.txt"
    elif mutation == "digest-mismatch":
        sources[0]["sha256"] = "0" * 64
    else:  # pragma: no cover - parametrization is the closed mutation contract.
        raise AssertionError(f"unknown mutation: {mutation}")

    with pytest.raises(ComponentInventoryError, match=expected_error):
        generate_component_ledger(_rewrite_policy_and_receipt(packet, policy))
