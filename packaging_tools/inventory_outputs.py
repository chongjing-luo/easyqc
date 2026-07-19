"""Deterministic projections from one complete ComponentLedger."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
from typing import BinaryIO, Callable
import uuid

from .artifact_manifest import create_artifact_manifest
from .component_inventory import ComponentLedger
from .contracts import (
    ReleaseContractError,
    canonical_json_bytes,
    sha256_file,
    write_canonical_json,
)


INVENTORY_RECEIPT_SCHEMA = "easyqc-release-inventory-receipt-v1"
CYCLONEDX_SCHEMA_URL = "https://cyclonedx.org/schema/bom-1.6.schema.json"
CYCLONEDX_VALIDATOR_VERSION = "easyqc-cyclonedx-1.6-profile-v1"


class InventoryOutputError(ReleaseContractError):
    """Raised when a ledger cannot become one passing inventory receipt."""


@dataclass(frozen=True)
class InventoryReceipt:
    candidate_id: str
    target: str
    evidence_class: str
    build_receipt_sha256: str
    ledger_sha256: str
    cyclonedx_sha256: str
    notice_index_sha256: str
    notice_bundle_sha256: str
    validator_version: str
    status: str
    path: Path

    def __post_init__(self) -> None:
        if self.status != "PASS":
            raise InventoryOutputError("InventoryReceipt status must be PASS")
        for label, digest in (
            ("Candidate ID", self.candidate_id),
            ("BuildReceipt", self.build_receipt_sha256),
            ("ledger", self.ledger_sha256),
            ("CycloneDX", self.cyclonedx_sha256),
            ("NOTICE index", self.notice_index_sha256),
            ("NOTICE bundle", self.notice_bundle_sha256),
        ):
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise InventoryOutputError(f"{label} digest is not a SHA-256")
        object.__setattr__(self, "path", Path(self.path))

    def as_json_object(self) -> dict[str, object]:
        return {
            "schema": INVENTORY_RECEIPT_SCHEMA,
            "candidate_id": self.candidate_id,
            "target": self.target,
            "evidence_class": self.evidence_class,
            "build_receipt_sha256": self.build_receipt_sha256,
            "ledger_sha256": self.ledger_sha256,
            "cyclonedx_sha256": self.cyclonedx_sha256,
            "notice_index_sha256": self.notice_index_sha256,
            "notice_bundle_sha256": self.notice_bundle_sha256,
            "validator_version": self.validator_version,
            "status": self.status,
            "outputs": {
                "component_ledger": "component-ledger.json",
                "cyclonedx": "bom.cdx.json",
                "notice_index": "NOTICE_INDEX.md",
                "notice_bundle": "NOTICE_BUNDLE.tar",
            },
        }


def emit_inventory_outputs(
    ledger: ComponentLedger,
    output_dir: Path,
) -> InventoryReceipt:
    """Project one complete ledger into SBOM, notices, and a PASS receipt.

    Input: one complete PASS ``ComponentLedger`` and its already-owned
    ``inventory/`` directory.
    Output: one ``easyqc-release-inventory-receipt-v1``.
    Side effects: creates four projection files; the candidate is read-only.
    Errors: unresolved/changed/invalid inputs or output collisions fail loud
    and never publish a passing receipt.
    Split trigger: component classification and native checks remain separate.
    """

    destination = _inventory_directory(output_dir, ledger.packet_root)
    if ledger.release_status != "PASS" or ledger.unresolved:
        raise InventoryOutputError("inventory outputs require a complete PASS ledger")
    if ledger.classified_entry_count != ledger.artifact.classifiable_entry_count:
        raise InventoryOutputError("inventory outputs require complete path coverage")
    _assert_candidate_identity(ledger, "before inventory projection")

    ledger_path = destination / "component-ledger.json"
    if not ledger_path.is_file() or ledger_path.is_symlink():
        raise InventoryOutputError("canonical component-ledger.json is missing")
    expected_ledger_bytes = canonical_json_bytes(ledger.as_json_object())
    if ledger_path.read_bytes() != expected_ledger_bytes:
        raise InventoryOutputError("component-ledger.json is not the supplied ledger")
    ledger_sha256 = sha256_file(ledger_path)

    notice_contents = _read_notices(ledger)
    cyclonedx = _cyclonedx_document(ledger)
    validate_cyclonedx_document(cyclonedx)
    notice_index = _notice_index(ledger, notice_contents)
    projection_paths = [
        destination / "bom.cdx.json",
        destination / "NOTICE_INDEX.md",
        destination / "NOTICE_BUNDLE.tar",
        destination / "inventory-receipt.json",
    ]
    if any(path.exists() or path.is_symlink() for path in projection_paths):
        raise InventoryOutputError("inventory projection output already exists")

    cyclonedx_path, index_path, bundle_path, receipt_path = projection_paths
    cyclonedx_sha256 = write_canonical_json(cyclonedx_path, cyclonedx)
    _write_exclusive_bytes(index_path, notice_index)
    _write_notice_tar(bundle_path, notice_contents)
    _assert_candidate_identity(ledger, "after inventory projection")

    receipt = InventoryReceipt(
        candidate_id=ledger.candidate_id,
        target=ledger.target,
        evidence_class=ledger.evidence_class,
        build_receipt_sha256=ledger.build_receipt_sha256,
        ledger_sha256=ledger_sha256,
        cyclonedx_sha256=cyclonedx_sha256,
        notice_index_sha256=sha256_file(index_path),
        notice_bundle_sha256=sha256_file(bundle_path),
        validator_version=CYCLONEDX_VALIDATOR_VERSION,
        status="PASS",
        path=receipt_path,
    )
    receipt_sha256 = write_canonical_json(receipt_path, receipt.as_json_object())
    if receipt_sha256 != sha256_file(receipt_path):
        raise InventoryOutputError("InventoryReceipt digest changed after publication")
    return receipt


def validate_cyclonedx_document(document: object) -> None:
    """Validate the strict CycloneDX 1.6 profile emitted by EasyQC.

    This standard-library profile gate rejects alternate versions, unknown
    fields, duplicate references, malformed components, and dangling
    dependency references.  The packet acceptance check separately validates
    generated bytes against the retained official CycloneDX 1.6 schemas.
    """

    if not isinstance(document, dict):
        raise InventoryOutputError("CycloneDX 1.6 document must be an object")
    allowed = {
        "$schema",
        "bomFormat",
        "specVersion",
        "serialNumber",
        "version",
        "metadata",
        "components",
        "dependencies",
    }
    required = allowed - {"components"}
    if not required.issubset(document) or not set(document).issubset(allowed):
        raise InventoryOutputError("CycloneDX 1.6 document fields are invalid")
    if (
        document["$schema"] != CYCLONEDX_SCHEMA_URL
        or document["bomFormat"] != "CycloneDX"
        or document["specVersion"] != "1.6"
        or document["version"] != 1
    ):
        raise InventoryOutputError("CycloneDX 1.6 schema/version is required")
    try:
        serial = str(document["serialNumber"])
        if not serial.startswith("urn:uuid:"):
            raise ValueError("missing urn:uuid prefix")
        uuid.UUID(serial.removeprefix("urn:uuid:"))
    except (ValueError, AttributeError) as exc:
        raise InventoryOutputError("CycloneDX serialNumber is invalid") from exc

    metadata = document["metadata"]
    if not isinstance(metadata, dict) or set(metadata) != {"component"}:
        raise InventoryOutputError("CycloneDX metadata component is required")
    root_ref = _validate_cyclonedx_component(metadata["component"])
    components = document.get("components", [])
    if not isinstance(components, list):
        raise InventoryOutputError("CycloneDX components must be a list")
    refs = [root_ref]
    refs.extend(_validate_cyclonedx_component(item) for item in components)
    if len(refs) != len(set(refs)):
        raise InventoryOutputError("CycloneDX bom-ref values must be unique")
    if [str(item["bom-ref"]) for item in components] != sorted(
        str(item["bom-ref"]) for item in components
    ):
        raise InventoryOutputError("CycloneDX components must be sorted")

    dependencies = document["dependencies"]
    if not isinstance(dependencies, list):
        raise InventoryOutputError("CycloneDX dependencies must be a list")
    dependency_refs: list[str] = []
    for item in dependencies:
        if not isinstance(item, dict) or set(item) != {"ref", "dependsOn"}:
            raise InventoryOutputError("CycloneDX dependency row is invalid")
        ref = str(item["ref"])
        depends_on = item["dependsOn"]
        if ref not in refs or not isinstance(depends_on, list):
            raise InventoryOutputError("CycloneDX dependency reference is invalid")
        if depends_on != sorted(set(depends_on)) or any(
            dependency not in refs for dependency in depends_on
        ):
            raise InventoryOutputError("CycloneDX dependsOn values are invalid")
        dependency_refs.append(ref)
    if dependency_refs != refs:
        raise InventoryOutputError("CycloneDX must include one dependency row per component")


def _cyclonedx_document(ledger: ComponentLedger) -> dict[str, object]:
    components = list(ledger.components)
    if not components:
        raise InventoryOutputError("CycloneDX requires at least one component")
    roots = [component for component in components if component.component_type == "application"]
    if len(roots) != 1:
        raise InventoryOutputError("CycloneDX requires exactly one application component")
    root = roots[0]
    children = [component for component in components if component is not root]
    ordered_components = [root, *children]
    dependency_map = {
        component.component_id: list(component.dependencies) for component in components
    }
    seed = ledger.candidate_id + "|" + "|".join(
        component.component_id for component in components
    )
    document: dict[str, object] = {
        "$schema": CYCLONEDX_SCHEMA_URL,
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, seed)}",
        "version": 1,
        "metadata": {"component": _cyclonedx_component(root)},
        "dependencies": [
            {"ref": component.component_id, "dependsOn": dependency_map[component.component_id]}
            for component in ordered_components
        ],
    }
    if children:
        document["components"] = [_cyclonedx_component(item) for item in children]
    return document


def _cyclonedx_component(component: object) -> dict[str, object]:
    type_map = {"application": "application", "framework": "framework", "runtime": "framework"}
    result: dict[str, object] = {
        "bom-ref": component.component_id,
        "type": type_map.get(component.component_type, "library"),
        "name": component.name,
        "version": component.version,
        "purl": component.purl,
        "licenses": [{"license": {"name": component.license_concluded}}],
        "properties": [
            {"name": "easyqc:notice-paths", "value": "|".join(component.notice_paths)},
            {"name": "easyqc:resolution-status", "value": component.resolution_status},
            {"name": "easyqc:shipped-file-count", "value": str(len(component.file_paths))},
        ],
    }
    return result


def _validate_cyclonedx_component(value: object) -> str:
    if not isinstance(value, dict):
        raise InventoryOutputError("CycloneDX component must be an object")
    expected = {"bom-ref", "type", "name", "version", "purl", "licenses", "properties"}
    if set(value) != expected:
        raise InventoryOutputError("CycloneDX component fields are invalid")
    for field in ("bom-ref", "type", "name", "version", "purl"):
        if not isinstance(value[field], str) or not value[field]:
            raise InventoryOutputError(f"CycloneDX component {field} is invalid")
    if value["type"] not in {"application", "framework", "library"}:
        raise InventoryOutputError("CycloneDX component type is invalid")
    if not isinstance(value["licenses"], list) or not value["licenses"]:
        raise InventoryOutputError("CycloneDX component licenses are required")
    if not isinstance(value["properties"], list):
        raise InventoryOutputError("CycloneDX component properties are invalid")
    return str(value["bom-ref"])


def _read_notices(ledger: ComponentLedger) -> tuple[tuple[str, bytes, str], ...]:
    expected_hashes = dict(ledger.notice_hashes)
    referenced = sorted(
        {path for component in ledger.components for path in component.notice_paths}
    )
    if referenced != sorted(expected_hashes):
        raise InventoryOutputError("ledger notice hashes do not cover notice paths exactly")
    result: list[tuple[str, bytes, str]] = []
    for relative in referenced:
        path = _candidate_notice_path(ledger.artifact_root, relative)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise InventoryOutputError(f"cannot read notice {relative}: {exc}") from exc
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected_hashes[relative]:
            raise InventoryOutputError(f"notice SHA-256 mismatch: {relative}")
        result.append((relative, data, digest))
    return tuple(result)


def _notice_index(
    ledger: ComponentLedger,
    notices: tuple[tuple[str, bytes, str], ...],
) -> bytes:
    hashes = {path: digest for path, _data, digest in notices}
    lines = [
        "# EasyQC third-party notice index",
        "",
        f"- Candidate ID: `{ledger.candidate_id}`",
        "- Ordering: component ID, then candidate-relative notice path",
        "",
        "## Components",
        "",
    ]
    for component in ledger.components:
        lines.extend(
            [
                f"### {component.component_id}",
                "",
                f"- Name: {component.name}",
                f"- Version: {component.version}",
                f"- License: {component.license_concluded}",
            ]
        )
        for path in component.notice_paths:
            lines.append(f"- Notice: `{path}` — SHA-256 `{hashes[path]}`")
        lines.append("")
    return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _write_notice_tar(
    destination: Path,
    notices: tuple[tuple[str, bytes, str], ...],
) -> None:
    parents = sorted(
        {
            parent.as_posix()
            for path, _data, _digest in notices
            for parent in PurePosixPath(path).parents
            if parent.as_posix() != "."
        }
    )

    def write(handle: BinaryIO) -> None:
        with tarfile.open(fileobj=handle, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for parent in parents:
                info = tarfile.TarInfo(parent)
                _normalize_tar_info(info, mode=0o755)
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
            for path, data, _digest in notices:
                info = tarfile.TarInfo(path)
                _normalize_tar_info(info, mode=0o644)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    _publish_temp_file(destination, write)


def _normalize_tar_info(info: tarfile.TarInfo, *, mode: int) -> None:
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0


def _write_exclusive_bytes(destination: Path, data: bytes) -> None:
    def write(handle: BinaryIO) -> None:
        handle.write(data)

    _publish_temp_file(destination, write)


def _publish_temp_file(
    destination: Path,
    writer: Callable[[BinaryIO], None],
) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            writer(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    except (OSError, tarfile.TarError) as exc:
        if temporary is not None:
            _remove_temporary(temporary, destination, exc)
        raise InventoryOutputError(f"cannot publish {destination.name}: {exc}") from exc
    if temporary is None:
        raise InventoryOutputError(f"temporary output was not created: {destination.name}")
    _remove_temporary(temporary, destination, None)


def _remove_temporary(
    temporary: Path,
    destination: Path,
    primary_error: BaseException | None,
) -> None:
    try:
        temporary.unlink()
    except OSError as cleanup_error:
        detail = f"; primary error: {primary_error}" if primary_error else ""
        raise InventoryOutputError(
            f"cannot remove temporary file for {destination}{detail}: {cleanup_error}"
        ) from cleanup_error


def _assert_candidate_identity(ledger: ComponentLedger, stage: str) -> None:
    actual = create_artifact_manifest(ledger.artifact_root).candidate_id
    if actual != ledger.candidate_id:
        raise InventoryOutputError(
            f"candidate identity mismatch {stage}: expected {ledger.candidate_id}, got {actual}"
        )


def _candidate_notice_path(artifact_root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        pure.is_absolute()
        or relative != pure.as_posix()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise InventoryOutputError(
            f"notice path must be candidate-relative canonical POSIX: {relative}"
        )
    root = Path(artifact_root)
    if root.is_symlink() or not root.is_dir():
        raise InventoryOutputError("candidate root must be a real directory")
    try:
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise InventoryOutputError(f"cannot resolve candidate root: {exc}") from exc
    current = resolved_root
    for index, part in enumerate(pure.parts):
        current = current / part
        try:
            current.lstat()
        except OSError as exc:
            raise InventoryOutputError(f"cannot open notice {relative}: {exc}") from exc
        if current.is_symlink():
            raise InventoryOutputError(f"notice path cannot traverse a symlink: {relative}")
        if index < len(pure.parts) - 1 and not current.is_dir():
            raise InventoryOutputError(f"notice parent is not a directory: {relative}")
    try:
        current.resolve(strict=True).relative_to(resolved_root)
    except (OSError, ValueError) as exc:
        raise InventoryOutputError(f"notice escapes candidate: {relative}") from exc
    if not current.is_file():
        raise InventoryOutputError(f"notice is not a regular file: {relative}")
    return current


def _inventory_directory(path: Path, packet_root: Path) -> Path:
    destination = Path(path)
    if (
        destination.is_symlink()
        or not destination.is_dir()
        or destination.name != "inventory"
    ):
        raise InventoryOutputError(
            "inventory output must be an existing real inventory/ directory"
        )
    packet = Path(packet_root)
    if packet.is_symlink() or not packet.is_dir():
        raise InventoryOutputError("originating packet root must be a real directory")
    try:
        resolved_destination = destination.resolve(strict=True)
        resolved_packet = packet.resolve(strict=True)
    except OSError as exc:
        raise InventoryOutputError(f"cannot resolve inventory output: {exc}") from exc
    if resolved_destination.parent != resolved_packet:
        raise InventoryOutputError(
            "inventory output must stay below the originating packet"
        )
    return resolved_destination
