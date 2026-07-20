from __future__ import annotations

from copy import deepcopy
import hashlib

import pytest

from models.managed_runtime import (
    ActivationPointerV1,
    InstallReceiptV1,
    ManagedRuntimeContractError,
    ReleaseManifestV1,
)


MIB = 1024 * 1024


def _manifest_object() -> dict[str, object]:
    return {
        "schema_version": 1,
        "release_id": "easyqc-1.0.0-linux",
        "channel": "candidate",
        "target": {"os": "linux", "os_minimum": "22.04", "arch": "x86_64"},
        "easyqc": {
            "version": "1.0.0",
            "source_filename": "easyqc-source.tar.gz",
            "source_sha256": "a" * 64,
            "entrypoint": "easyqc_version.py",
        },
        "uv": {
            "version": "0.11.29",
            "filename": "uv",
            "size_bytes": 17,
            "sha256": "b" * 64,
        },
        "python": {
            "version": "3.13.13",
            "build": "20260720",
            "key": "cpython-3.13.13-linux-x86_64-gnu",
            "filename": "python.tar.zst",
            "size_bytes": 19,
            "sha256": "c" * 64,
        },
        "lock": {
            "filename": "requirements.lock",
            "sha256": "d" * 64,
            "require_hashes": True,
        },
        "artifacts": [
            {
                "kind": "easyqc-source",
                "filename": "easyqc-source.tar.gz",
                "size_bytes": 23,
                "sha256": "a" * 64,
            },
            {
                "kind": "lock",
                "filename": "requirements.lock",
                "size_bytes": 29,
                "sha256": "d" * 64,
            },
            {
                "kind": "wheel",
                "filename": "easyqc_dep-1-py3-none-any.whl",
                "size_bytes": 31,
                "sha256": "e" * 64,
            },
            {
                "kind": "notice",
                "filename": "NOTICE.txt",
                "size_bytes": 37,
                "sha256": "f" * 64,
            },
        ],
        "sources": [
            {
                "artifact_filename": "easyqc_dep-1-py3-none-any.whl",
                "https_urls": [
                    "https://files.pythonhosted.org/easyqc_dep-1-py3-none-any.whl"
                ],
                "allowed_hosts": ["files.pythonhosted.org"],
            }
        ],
        "notices": [{"filename": "NOTICE.txt", "sha256": "f" * 64}],
        "expanded_version_bytes": 1024,
        "required_free_bytes": 256 * MIB + 1024,
        "smoke_contract_version": 1,
    }


def _receipt_object(manifest: ReleaseManifestV1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "release_id": manifest.release_id,
        "target": manifest.target.to_json_object(),
        "install_scope": "user",
        "install_root": "/tmp/EasyQC root",
        "manifest_sha256": manifest.sha256,
        "source_sha256": manifest.easyqc.source_sha256,
        "uv": {"version": manifest.uv.version, "sha256": manifest.uv.sha256},
        "python": {
            "version": manifest.python.version,
            "build": manifest.python.build,
            "key": manifest.python.key,
            "payload_sha256": manifest.python.sha256,
        },
        "lock_sha256": manifest.lock.sha256,
        "artifact_manifest_sha256": "1" * 64,
        "installed_file_set_sha256": "2" * 64,
        "smoke": {
            "contract_version": manifest.smoke_contract_version,
            "passed": True,
            "completed_at_utc": "2026-07-20T01:02:03Z",
            "report_sha256": "3" * 64,
        },
        "installed_at_utc": "2026-07-20T01:02:04Z",
        "controller_version": "ft-r2",
    }


def test_release_manifest_has_one_canonical_round_trip() -> None:
    manifest = ReleaseManifestV1.from_json_object(_manifest_object())

    assert ReleaseManifestV1.from_canonical_bytes(manifest.canonical_bytes) == manifest
    assert manifest.to_json_object() == _manifest_object()
    assert manifest.sha256 == hashlib.sha256(manifest.canonical_bytes).hexdigest()
    assert manifest.target.target_id == "ubuntu-22.04-x86_64"


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("unexpected",), True, "unknown fields"),
        (("target", "unexpected"), True, "unknown fields"),
        (("channel",), "nightly", "channel"),
        (("target", "os"), "freebsd", "target"),
        (("uv", "version"), "0.11.28", "uv.version"),
        (("python", "version"), "3.13.12", "python.version"),
        (("easyqc", "source_filename"), "../source.tar.gz", "filename"),
        (("easyqc", "entrypoint"), "/tmp/entry.py", "entrypoint"),
        (("easyqc", "entrypoint"), "../entry.py", "entrypoint"),
        (("easyqc", "entrypoint"), "app//entry.py", "entrypoint"),
        (("uv", "sha256"), "BAD", "SHA-256"),
        (("uv", "size_bytes"), 0, "size_bytes"),
        (("lock", "require_hashes"), False, "require_hashes"),
        (("required_free_bytes",), 256 * MIB, "required_free_bytes"),
    ],
)
def test_release_manifest_rejects_invalid_schema_values(
    path: tuple[str, ...], value: object, message: str
) -> None:
    payload = deepcopy(_manifest_object())
    cursor: dict[str, object] = payload
    for part in path[:-1]:
        cursor = cursor[part]  # type: ignore[assignment,index]
    cursor[path[-1]] = value

    with pytest.raises(ManagedRuntimeContractError, match=message):
        ReleaseManifestV1.from_json_object(payload)


def test_release_manifest_rejects_duplicate_or_conflicting_file_authority() -> None:
    duplicate = _manifest_object()
    duplicate["artifacts"] = [
        *duplicate["artifacts"],  # type: ignore[list-item]
        deepcopy(duplicate["artifacts"][0]),  # type: ignore[index]
    ]
    with pytest.raises(ManagedRuntimeContractError, match="duplicate.*filename"):
        ReleaseManifestV1.from_json_object(duplicate)

    conflict = _manifest_object()
    conflict["notices"][0]["sha256"] = "0" * 64  # type: ignore[index]
    with pytest.raises(ManagedRuntimeContractError, match="NOTICE.*SHA-256"):
        ReleaseManifestV1.from_json_object(conflict)


@pytest.mark.parametrize(
    "url",
    [
        "http://files.pythonhosted.org/package.whl",
        "https://example.invalid/package.whl",
        "https://user@files.pythonhosted.org/package.whl",
        "https://files.pythonhosted.org:444/package.whl",
    ],
)
def test_release_manifest_rejects_unsafe_online_source_urls(url: str) -> None:
    payload = _manifest_object()
    payload["sources"][0]["https_urls"] = [url]  # type: ignore[index]

    with pytest.raises(ManagedRuntimeContractError, match="HTTPS"):
        ReleaseManifestV1.from_json_object(payload)


def test_manifest_parser_rejects_noncanonical_and_duplicate_key_json() -> None:
    manifest = ReleaseManifestV1.from_json_object(_manifest_object())
    noncanonical = manifest.canonical_bytes.replace(b'"arch":"x86_64"', b'"arch": "x86_64"')
    with pytest.raises(ManagedRuntimeContractError, match="canonical JSON"):
        ReleaseManifestV1.from_canonical_bytes(noncanonical)

    duplicate_key = manifest.canonical_bytes.replace(
        b'"schema_version":1', b'"schema_version":1,"schema_version":1', 1
    )
    with pytest.raises(ManagedRuntimeContractError, match="duplicate JSON key"):
        ReleaseManifestV1.from_canonical_bytes(duplicate_key)


def test_receipt_requires_pass_and_exact_manifest_root_target_identities() -> None:
    manifest = ReleaseManifestV1.from_json_object(_manifest_object())
    receipt = InstallReceiptV1.from_json_object(_receipt_object(manifest))

    receipt.validate_for(manifest, "/tmp/EasyQC root")
    assert InstallReceiptV1.from_canonical_bytes(receipt.canonical_bytes) == receipt

    failed = _receipt_object(manifest)
    failed["smoke"]["passed"] = False  # type: ignore[index]
    with pytest.raises(ManagedRuntimeContractError, match="smoke.*PASS"):
        InstallReceiptV1.from_json_object(failed)

    unknown = _receipt_object(manifest)
    unknown["smoke"]["detail"] = "not allowed"  # type: ignore[index]
    with pytest.raises(ManagedRuntimeContractError, match="unknown fields"):
        InstallReceiptV1.from_json_object(unknown)

    with pytest.raises(ManagedRuntimeContractError, match="install root"):
        receipt.validate_for(manifest, "/tmp/another-root")

    noncanonical_root = _receipt_object(manifest)
    noncanonical_root["install_root"] = "/tmp//EasyQC root"
    with pytest.raises(ManagedRuntimeContractError, match="canonical path"):
        InstallReceiptV1.from_json_object(noncanonical_root)

    other_manifest_object = _manifest_object()
    other_manifest_object["target"] = {
        "os": "linux",
        "os_minimum": "24.04",
        "arch": "x86_64",
    }
    other_manifest = ReleaseManifestV1.from_json_object(other_manifest_object)
    with pytest.raises(ManagedRuntimeContractError, match="target"):
        receipt.validate_for(other_manifest, "/tmp/EasyQC root")


@pytest.mark.parametrize(
    "raw",
    [
        b"v1\n-\n",
        b"v1\n-\n0\nextra\n",
        b"../v1\n-\n0\n",
        b"v1/sub\n-\n0\n",
        b"v1\n/absolute\n0\n",
        b"v1\n-\n-1\n",
        b"v1\n-\n01\n",
        b"v1\n-\n123456789012345678901\n",
        b"v1\n-\n1",  # missing final LF
        "版本1\n-\n1\n".encode("utf-8"),
    ],
)
def test_activation_pointer_rejects_malformed_authority(raw: bytes) -> None:
    with pytest.raises(ManagedRuntimeContractError):
        ActivationPointerV1.from_bytes(raw)


def test_activation_pointer_round_trip_is_strict_ascii() -> None:
    pointer = ActivationPointerV1(
        active_release_id="easyqc-2.0.0",
        previous_release_id="easyqc-1.0.0",
        generation=2,
    )

    assert pointer.to_bytes() == b"easyqc-2.0.0\neasyqc-1.0.0\n2\n"
    assert ActivationPointerV1.from_bytes(pointer.to_bytes()) == pointer
