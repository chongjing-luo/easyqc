"""Fixture-only NativeEvidencePacket v2 receipt and immutability proof."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re

from .artifact_manifest import create_artifact_manifest
from .component_inventory import BUILD_RECEIPT_SCHEMA
from .contracts import (
    ReleaseContractError,
    canonical_json_bytes,
    sha256_file,
    write_canonical_json,
)
from .inventory_outputs import (
    CYCLONEDX_VALIDATOR_VERSION,
    INVENTORY_RECEIPT_SCHEMA,
)


NATIVE_EVIDENCE_PACKET_SCHEMA = "easyqc-native-evidence-packet-v2"
FIXTURE_SUPPORT_STATUS = "NOT RUN — fixture evidence"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class NativePacketError(ReleaseContractError):
    """Raised when receipts, candidate identity, or support status disagree."""


@dataclass(frozen=True)
class NativeVerificationRequest:
    build_receipt_path: Path
    build_receipt_sha256: str
    inventory_receipt_path: Path
    inventory_receipt_sha256: str
    output_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "build_receipt_path", Path(self.build_receipt_path))
        object.__setattr__(
            self, "inventory_receipt_path", Path(self.inventory_receipt_path)
        )
        object.__setattr__(self, "output_dir", Path(self.output_dir))
        _validate_sha256(self.build_receipt_sha256, "build receipt SHA-256")
        _validate_sha256(self.inventory_receipt_sha256, "inventory receipt SHA-256")


@dataclass(frozen=True)
class NativeEvidencePacket:
    target: str
    evidence_class: str
    candidate_id: str
    build_receipt_sha256: str
    inventory_receipt_sha256: str
    source: dict[str, object]
    runtime: dict[str, object]
    locks: dict[str, object]
    tools: dict[str, object]
    rows: tuple[dict[str, str], ...]
    evidence_file_hashes: dict[str, str]
    support_claim: bool
    support_matrix_status: str
    overall_status: str
    path: Path

    def __post_init__(self) -> None:
        for label, digest in (
            ("Candidate ID", self.candidate_id),
            ("build receipt SHA-256", self.build_receipt_sha256),
            ("inventory receipt SHA-256", self.inventory_receipt_sha256),
        ):
            _validate_sha256(digest, label)
        if self.evidence_class == "fixture":
            if self.support_claim:
                raise NativePacketError("fixture evidence cannot claim support")
            if self.support_matrix_status != FIXTURE_SUPPORT_STATUS:
                raise NativePacketError(
                    "fixture evidence cannot change the support matrix status"
                )
            if self.overall_status == "PASS":
                raise NativePacketError("fixture evidence cannot be overall PASS")
            if self.overall_status != "FIXTURE-ONLY":
                raise NativePacketError("fixture evidence must be FIXTURE-ONLY")
        else:
            raise NativePacketError("this packet constructor accepts fixture evidence only")
        if self.rows != _fixture_rows():
            raise NativePacketError("fixture evidence rows must remain canonical")
        object.__setattr__(self, "path", Path(self.path))

    def as_json_object(self) -> dict[str, object]:
        rows_by_name = {row["name"]: row for row in self.rows}
        return {
            "schema": NATIVE_EVIDENCE_PACKET_SCHEMA,
            "target": self.target,
            "evidence_class": self.evidence_class,
            "runner": {
                "status": "NOT RUN",
                "reason": "fixture contract validation only",
            },
            "source": self.source,
            "runtime": self.runtime,
            "locks": self.locks,
            "tools": self.tools,
            "candidate_id": self.candidate_id,
            "build_receipt_sha256": self.build_receipt_sha256,
            "inventory_receipt_sha256": self.inventory_receipt_sha256,
            "warnings": ["fixture evidence does not establish target support"],
            "manifest": {
                "before": self.candidate_id,
                "after": self.candidate_id,
                "identical": True,
            },
            "binary_closure": rows_by_name["binary_closure"],
            "help_smoke": rows_by_name["help_smoke"],
            "offscreen_smoke": rows_by_name["offscreen_smoke"],
            "native_gui_smoke": rows_by_name["native_gui_smoke"],
            "logging": rows_by_name["logging"],
            "immutability": rows_by_name["immutability"],
            "signing": rows_by_name["signing"],
            "evidence_file_hashes": dict(sorted(self.evidence_file_hashes.items())),
            "rows": list(self.rows),
            "support_claim": self.support_claim,
            "support_matrix_status": self.support_matrix_status,
            "overall_status": self.overall_status,
        }


def create_fixture_native_packet(
    request: NativeVerificationRequest,
) -> NativeEvidencePacket:
    """Bind fixture receipts and prove pre/post candidate immutability.

    Input: one candidate's authenticated BuildReceipt/InventoryReceipt and a
    new ``native/`` output path.
    Output: one fixture-class ``easyqc-native-evidence-packet-v2``.
    Side effects: creates only ``native/evidence-packet.json``; no subprocess,
    signing, publication, or candidate write occurs.
    Errors: receipt/hash/path/status/Candidate-ID mismatch or mutation fails
    before publication.
    Split trigger: target-native commands and release PASS belong to later
    target-specific verifier packets.
    """

    packet_root = _packet_root(request.build_receipt_path)
    build_receipt = _authenticated_receipt(
        request.build_receipt_path,
        request.build_receipt_sha256,
        "build receipt",
    )
    _expect_fields(
        build_receipt,
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
    if build_receipt["schema"] != BUILD_RECEIPT_SCHEMA:
        raise NativePacketError(f"build receipt schema must be {BUILD_RECEIPT_SCHEMA}")
    if build_receipt["status"] != "PASS":
        raise NativePacketError("fixture native packet requires a PASS BuildReceipt")
    if build_receipt["evidence_class"] != "fixture":
        raise NativePacketError("fixture native packet requires fixture build evidence")

    inventory_parent = request.inventory_receipt_path.parent
    if inventory_parent.is_symlink() or not inventory_parent.is_dir():
        raise NativePacketError("packet inventory/ must be a real directory")
    try:
        resolved_inventory_parent = inventory_parent.resolve(strict=True)
    except OSError as exc:
        raise NativePacketError(f"cannot resolve packet inventory/: {exc}") from exc
    if (
        resolved_inventory_parent != packet_root / "inventory"
        or request.inventory_receipt_path.name != "inventory-receipt.json"
    ):
        raise NativePacketError("InventoryReceipt must be below the same packet inventory/")
    inventory_receipt = _authenticated_receipt(
        request.inventory_receipt_path,
        request.inventory_receipt_sha256,
        "inventory receipt",
    )
    _expect_fields(
        inventory_receipt,
        {
            "schema",
            "candidate_id",
            "target",
            "evidence_class",
            "build_receipt_sha256",
            "ledger_sha256",
            "cyclonedx_sha256",
            "notice_index_sha256",
            "notice_bundle_sha256",
            "validator_version",
            "status",
            "outputs",
        },
        "inventory receipt",
    )
    if inventory_receipt["schema"] != INVENTORY_RECEIPT_SCHEMA:
        raise NativePacketError(
            f"inventory receipt schema must be {INVENTORY_RECEIPT_SCHEMA}"
        )
    if inventory_receipt["status"] != "PASS":
        raise NativePacketError("fixture native packet requires a PASS InventoryReceipt")
    if inventory_receipt["validator_version"] != CYCLONEDX_VALIDATOR_VERSION:
        raise NativePacketError("inventory receipt validator version is unsupported")

    candidate_id = _validate_sha256(build_receipt["candidate_id"], "Candidate ID")
    target = _required_string(build_receipt["target"], "target")
    expected_pairs = {
        "candidate_id": candidate_id,
        "target": target,
        "evidence_class": "fixture",
        "build_receipt_sha256": request.build_receipt_sha256,
    }
    for field, expected in expected_pairs.items():
        if inventory_receipt[field] != expected:
            raise NativePacketError(
                f"inventory receipt {field} mismatch: expected {expected}, "
                f"got {inventory_receipt[field]}"
            )

    artifact = _required_object(build_receipt["artifact"], "artifact")
    _expect_fields(artifact, {"path", "manifest"}, "artifact")
    artifact_root = _contained_path(
        packet_root,
        _required_string(artifact["path"], "artifact.path"),
        expected="directory",
    )
    before = create_artifact_manifest(artifact_root).candidate_id
    if before != candidate_id:
        raise NativePacketError(
            f"candidate identity mismatch before fixture verification: "
            f"expected {candidate_id}, got {before}"
        )

    evidence_hashes = _authenticate_inventory_outputs(
        request.inventory_receipt_path.parent,
        inventory_receipt,
    )
    after = create_artifact_manifest(artifact_root).candidate_id
    if after != before:
        raise NativePacketError(
            f"candidate identity mismatch after fixture verification: "
            f"expected {before}, got {after}"
        )

    rows = _fixture_rows()
    output_path = request.output_dir / "evidence-packet.json"
    packet = NativeEvidencePacket(
        target=target,
        evidence_class="fixture",
        candidate_id=candidate_id,
        build_receipt_sha256=request.build_receipt_sha256,
        inventory_receipt_sha256=request.inventory_receipt_sha256,
        source=_required_object(build_receipt["source"], "source"),
        runtime=_required_object(build_receipt["runtime"], "runtime"),
        locks=_required_object(build_receipt["locks"], "locks"),
        tools=_required_object(build_receipt["tools"], "tools"),
        rows=rows,
        evidence_file_hashes=evidence_hashes,
        support_claim=False,
        support_matrix_status=FIXTURE_SUPPORT_STATUS,
        overall_status="FIXTURE-ONLY",
        path=output_path,
    )
    _create_native_directory(request.output_dir, packet_root)
    write_canonical_json(output_path, packet.as_json_object())
    return packet


def assert_support_claim_allowed(packet: NativeEvidencePacket) -> None:
    """Reject fixture evidence at the support-matrix boundary."""

    if packet.evidence_class == "fixture" or not packet.support_claim:
        raise NativePacketError("fixture evidence cannot claim support")


def _fixture_rows() -> tuple[dict[str, str], ...]:
    return tuple(
        {"name": name, "status": status, "detail": detail}
        for name, status, detail in (
            ("receipt_identity", "PASS", "BuildReceipt and InventoryReceipt hashes match"),
            ("artifact_manifest", "PASS", "Candidate ID matches before verification"),
            ("binary_closure", "WARNING", "not run for fixture evidence"),
            ("help_smoke", "WARNING", "not run for fixture evidence"),
            ("offscreen_smoke", "WARNING", "not run for fixture evidence"),
            ("native_gui_smoke", "WARNING", "not run for fixture evidence"),
            ("logging", "WARNING", "not run for fixture evidence"),
            ("immutability", "PASS", "pre/post Candidate ID is identical"),
            ("signing", "WARNING", "outside fixture and current authorization"),
        )
    )


def _authenticate_inventory_outputs(
    inventory_dir: Path,
    receipt: dict[str, object],
) -> dict[str, str]:
    outputs = _required_object(receipt["outputs"], "inventory outputs")
    expected_outputs = {
        "component_ledger": ("component-ledger.json", "ledger_sha256"),
        "cyclonedx": ("bom.cdx.json", "cyclonedx_sha256"),
        "notice_index": ("NOTICE_INDEX.md", "notice_index_sha256"),
        "notice_bundle": ("NOTICE_BUNDLE.tar", "notice_bundle_sha256"),
    }
    _expect_fields(outputs, set(expected_outputs), "inventory outputs")
    result: dict[str, str] = {}
    for label, (expected_name, digest_field) in expected_outputs.items():
        if outputs[label] != expected_name:
            raise NativePacketError(f"inventory output name mismatch for {label}")
        path = inventory_dir / expected_name
        if path.is_symlink() or not path.is_file():
            raise NativePacketError(f"inventory output is missing: {expected_name}")
        expected_digest = _validate_sha256(receipt[digest_field], digest_field)
        actual_digest = sha256_file(path)
        if actual_digest != expected_digest:
            raise NativePacketError(
                f"inventory output SHA-256 mismatch for {expected_name}"
            )
        result[expected_name] = actual_digest
    return result


def _authenticated_receipt(
    path: Path,
    expected_sha256: str,
    label: str,
) -> dict[str, object]:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise NativePacketError(f"{label} is not a regular file: {candidate}")
    actual_sha256 = sha256_file(candidate)
    if actual_sha256 != expected_sha256:
        raise NativePacketError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    try:
        raw = candidate.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativePacketError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise NativePacketError(f"{label} must be a canonical JSON object")
    return value


def _packet_root(build_receipt_path: Path) -> Path:
    path = Path(build_receipt_path)
    if path.parent.name != "build":
        raise NativePacketError("BuildReceipt must be below packet build/")
    build_dir = path.parent
    root = build_dir.parent
    if root.is_symlink() or not root.is_dir():
        raise NativePacketError("packet root must be a real directory")
    if build_dir.is_symlink() or not build_dir.is_dir():
        raise NativePacketError("BuildReceipt requires a real packet build/ directory")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_build = build_dir.resolve(strict=True)
    except OSError as exc:
        raise NativePacketError(f"cannot resolve packet build/: {exc}") from exc
    if resolved_build != resolved_root / "build":
        raise NativePacketError("BuildReceipt requires a real packet build/ directory")
    return resolved_root


def _contained_path(root: Path, relative: str, *, expected: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or relative != pure.as_posix()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise NativePacketError(f"artifact.path is not canonical: {relative}")
    current = root
    for index, part in enumerate(pure.parts):
        current = current / part
        try:
            current.lstat()
        except OSError as exc:
            raise NativePacketError(f"artifact.path cannot be opened: {exc}") from exc
        if os.path.islink(current):
            raise NativePacketError("artifact.path cannot traverse a symlink")
        if index < len(pure.parts) - 1 and not current.is_dir():
            raise NativePacketError("artifact.path parent is not a directory")
    try:
        current.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise NativePacketError("artifact.path escapes packet root") from exc
    if expected == "directory" and not current.is_dir():
        raise NativePacketError("artifact.path is not a directory")
    return current


def _create_native_directory(output: Path, packet_root: Path) -> None:
    destination = Path(output)
    if destination.exists() or destination.is_symlink():
        raise NativePacketError(f"native output already exists: {destination}")
    try:
        parent = destination.parent.resolve(strict=True)
    except OSError as exc:
        raise NativePacketError(f"native output parent is invalid: {exc}") from exc
    if parent != packet_root or destination.name != "native":
        raise NativePacketError("native output must be the packet native/ child")
    try:
        destination.mkdir(mode=0o755)
    except OSError as exc:
        raise NativePacketError(f"cannot create native output: {exc}") from exc


def _required_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise NativePacketError(f"{label} must be an object with string keys")
    return value


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise NativePacketError(f"{label} must be a non-empty string")
    return value


def _validate_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise NativePacketError(f"{label} must be a lowercase SHA-256")
    return value


def _expect_fields(value: dict[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise NativePacketError(
            f"{label} fields mismatch: expected {sorted(expected)}, got {sorted(value)}"
        )
