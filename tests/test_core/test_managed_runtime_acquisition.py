from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import os
from pathlib import Path
import socket

import pytest

import core.managed_runtime_acquisition as acquisition
from core.managed_runtime import ManagedRuntimeError, verify_artifact_set
from core.managed_runtime_acquisition import (
    OFFLINE_MANIFEST_FILENAME,
    OfflinePayloadRequest,
    OnlineAcquisitionRequest,
    UrllibHttpsTransport,
    acquire_online,
    inspect_offline_payload,
)
from models.managed_runtime import ReleaseManifestV1


MIB = 1024 * 1024
TARGET_ID = "ubuntu-22.04-x86_64"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _payloads() -> dict[str, bytes]:
    return {
        "uv": b"pinned-uv-binary",
        "python.tar.gz": b"exact-python-payload",
        "easyqc-source.tar.gz": b"easyqc-source",
        "requirements.lock": b"package==1 --hash=sha256:" + b"1" * 64,
        "dependency-1-py3-none-any.whl": b"wheel-bytes",
        "NOTICE.txt": b"notice-bytes",
    }


def _manifest(
    payloads: dict[str, bytes],
    *,
    source_names: set[str] | None = None,
    required_free_bytes: int | None = None,
) -> ReleaseManifestV1:
    if source_names is None:
        source_names = set(payloads)

    def artifact(kind: str, filename: str) -> dict[str, object]:
        data = payloads[filename]
        return {
            "kind": kind,
            "filename": filename,
            "size_bytes": len(data),
            "sha256": _sha256(data),
        }

    expanded = 4096
    missing = sum(len(data) for data in payloads.values())
    required = required_free_bytes or expanded + missing + 256 * MIB
    return ReleaseManifestV1.from_json_object(
        {
            "schema_version": 1,
            "release_id": "easyqc-1.0.0-linux",
            "channel": "candidate",
            "target": {
                "os": "linux",
                "os_minimum": "22.04",
                "arch": "x86_64",
            },
            "easyqc": {
                "version": "1.0.0",
                "source_filename": "easyqc-source.tar.gz",
                "source_sha256": _sha256(payloads["easyqc-source.tar.gz"]),
                "entrypoint": "easyqc.py",
            },
            "uv": {
                "version": "0.11.29",
                "filename": "uv",
                "size_bytes": len(payloads["uv"]),
                "sha256": _sha256(payloads["uv"]),
            },
            "python": {
                "version": "3.13.13",
                "build": "20260720",
                "key": "cpython-3.13.13-linux-x86_64-gnu",
                "filename": "python.tar.gz",
                "size_bytes": len(payloads["python.tar.gz"]),
                "sha256": _sha256(payloads["python.tar.gz"]),
            },
            "lock": {
                "filename": "requirements.lock",
                "sha256": _sha256(payloads["requirements.lock"]),
                "require_hashes": True,
            },
            "artifacts": [
                artifact("easyqc-source", "easyqc-source.tar.gz"),
                artifact("lock", "requirements.lock"),
                artifact("wheel", "dependency-1-py3-none-any.whl"),
                artifact("notice", "NOTICE.txt"),
            ],
            "sources": [
                {
                    "artifact_filename": filename,
                    "https_urls": [f"https://files.example.org/{filename}"],
                    "allowed_hosts": ["files.example.org"],
                }
                for filename in sorted(source_names)
            ],
            "notices": [
                {
                    "filename": "NOTICE.txt",
                    "sha256": _sha256(payloads["NOTICE.txt"]),
                }
            ],
            "expanded_version_bytes": expanded,
            "required_free_bytes": required,
            "smoke_contract_version": 1,
        }
    )


class FakeTransport:
    def __init__(self, responses: dict[str, bytes | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, tuple[str, ...], float, int]] = []

    def stream(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
        timeout_seconds: float,
        maximum_bytes: int,
    ):
        self.calls.append((url, allowed_hosts, timeout_seconds, maximum_bytes))
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        midpoint = max(1, len(response) // 2)
        yield response[:midpoint]
        yield response[midpoint:]


def _online_request(
    manifest: ReleaseManifestV1,
    destination: Path,
) -> OnlineAcquisitionRequest:
    return OnlineAcquisitionRequest(
        manifest=manifest,
        destination=destination,
        expected_target_id=TARGET_ID,
        timeout_seconds=5.0,
    )


def _responses(payloads: dict[str, bytes]) -> dict[str, bytes]:
    return {
        f"https://files.example.org/{filename}": data
        for filename, data in payloads.items()
    }


def test_online_acquisition_promotes_only_complete_verified_files(
    tmp_path: Path,
) -> None:
    payloads = _payloads()
    manifest = _manifest(payloads)
    destination = tmp_path / "下载 root with spaces"
    transport = FakeTransport(_responses(payloads))

    verified = acquire_online(
        _online_request(manifest, destination),
        transport=transport,
    )

    assert verified == verify_artifact_set(
        manifest,
        destination,
        expected_target_id=TARGET_ID,
    )
    assert {item.filename for item in verified.artifacts} == set(payloads)
    assert len(transport.calls) == len(payloads)
    assert not list(destination.glob("*.partial"))
    assert all(
        (destination / name).read_bytes() == data
        for name, data in payloads.items()
    )


def test_online_acquisition_retries_short_os_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payloads = _payloads()
    manifest = _manifest(payloads)
    destination = tmp_path / "downloads"
    real_write = os.write
    short_write_count = 0

    def write_part(descriptor: int, data: bytes) -> int:
        nonlocal short_write_count
        maximum = max(1, len(data) // 2)
        if maximum < len(data):
            short_write_count += 1
        return real_write(descriptor, data[:maximum])

    monkeypatch.setattr(acquisition.os, "write", write_part)

    acquire_online(
        _online_request(manifest, destination),
        transport=FakeTransport(_responses(payloads)),
    )

    assert short_write_count > 0
    assert all(
        (destination / name).read_bytes() == data
        for name, data in payloads.items()
    )


def test_online_acquisition_reuses_verified_cache_and_repairs_corrupt_regular_file(
    tmp_path: Path,
) -> None:
    payloads = _payloads()
    manifest = _manifest(payloads)
    destination = tmp_path / "downloads"
    destination.mkdir()
    for filename, data in payloads.items():
        (destination / filename).write_bytes(data)
    corrupt_name = "dependency-1-py3-none-any.whl"
    (destination / corrupt_name).write_bytes(b"x" * len(payloads[corrupt_name]))
    (destination / f"{corrupt_name}.partial").write_bytes(b"old partial")
    transport = FakeTransport(_responses(payloads))

    acquire_online(_online_request(manifest, destination), transport=transport)

    assert [call[0] for call in transport.calls] == [
        f"https://files.example.org/{corrupt_name}"
    ]
    assert (destination / corrupt_name).read_bytes() == payloads[corrupt_name]
    assert not (destination / f"{corrupt_name}.partial").exists()


@pytest.mark.parametrize("failure", ("short", "oversize", "hash"))
def test_failed_download_remains_partial_and_never_becomes_authority(
    failure: str,
    tmp_path: Path,
) -> None:
    payloads = _payloads()
    manifest = _manifest(payloads)
    filename = "uv"
    if failure == "short":
        bad = payloads[filename][:-1]
    elif failure == "oversize":
        bad = payloads[filename] + b"x"
    else:
        bad = b"x" * len(payloads[filename])
    responses = _responses(payloads)
    responses[f"https://files.example.org/{filename}"] = bad
    destination = tmp_path / "downloads"

    with pytest.raises(ManagedRuntimeError, match="size|hash"):
        acquire_online(
            _online_request(manifest, destination),
            transport=FakeTransport(responses),
        )

    assert not (destination / filename).exists()
    assert (destination / f"{filename}.partial").read_bytes() == bad


def test_online_preflight_rejects_missing_source_and_disk_formula_before_writes(
    tmp_path: Path,
) -> None:
    payloads = _payloads()
    no_python_source = _manifest(
        payloads,
        source_names=set(payloads) - {"python.tar.gz"},
    )
    transport = FakeTransport(_responses(payloads))
    destination = tmp_path / "missing-source"

    with pytest.raises(ManagedRuntimeError, match="source authority.*python"):
        acquire_online(
            _online_request(no_python_source, destination),
            transport=transport,
        )
    assert not transport.calls
    assert not destination.exists()

    insufficient = _manifest(
        payloads,
        required_free_bytes=256 * MIB + 4096,
    )
    with pytest.raises(ManagedRuntimeError, match="disk formula"):
        acquire_online(
            _online_request(insufficient, tmp_path / "low-disk-record"),
            transport=transport,
        )
    assert not transport.calls
    assert not (tmp_path / "low-disk-record").exists()


def test_online_acquisition_rejects_symlink_cache_authority(
    tmp_path: Path,
) -> None:
    payloads = _payloads()
    manifest = _manifest(payloads)
    destination = tmp_path / "downloads"
    outside = tmp_path / "outside"
    destination.mkdir()
    outside.write_bytes(payloads["uv"])
    try:
        (destination / "uv").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ManagedRuntimeError, match="symlink"):
        acquire_online(
            _online_request(manifest, destination),
            transport=FakeTransport(_responses(payloads)),
        )


def test_online_acquisition_rejects_symlink_partial_without_touching_target(
    tmp_path: Path,
) -> None:
    payloads = _payloads()
    manifest = _manifest(payloads)
    destination = tmp_path / "downloads"
    outside = tmp_path / "outside"
    destination.mkdir()
    outside.write_bytes(b"protected")
    partial = destination / "uv.partial"
    try:
        partial.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(ManagedRuntimeError, match="partial.*symlink"):
        acquire_online(
            _online_request(manifest, destination),
            transport=FakeTransport(_responses(payloads)),
        )

    assert partial.is_symlink()
    assert outside.read_bytes() == b"protected"
    assert not (destination / "uv").exists()


class _RedirectedResponse(io.BytesIO):
    headers: dict[str, str] = {}

    def __init__(self, data: bytes, final_url: str) -> None:
        super().__init__(data)
        self._final_url = final_url

    def geturl(self) -> str:
        return self._final_url

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _RedirectingOpener:
    def open(self, *_args: object, **_kwargs: object) -> _RedirectedResponse:
        return _RedirectedResponse(b"data", "https://evil.example/data")


def test_default_https_transport_rejects_redirect_outside_allowlist() -> None:
    transport = UrllibHttpsTransport(opener=_RedirectingOpener())

    with pytest.raises(ManagedRuntimeError, match="redirect.*allowed host"):
        tuple(
            transport.stream(
                "https://files.example.org/data",
                allowed_hosts=("files.example.org",),
                timeout_seconds=1.0,
                maximum_bytes=4,
            )
        )


def _write_offline_payload(
    root: Path,
    manifest: ReleaseManifestV1,
    payloads: dict[str, bytes],
) -> None:
    root.mkdir()
    (root / OFFLINE_MANIFEST_FILENAME).write_bytes(manifest.canonical_bytes)
    for filename, data in payloads.items():
        (root / filename).write_bytes(data)


def test_offline_payload_is_verified_read_only_with_network_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payloads = _payloads()
    manifest = _manifest(payloads)
    root = tmp_path / "离线 payload with spaces"
    _write_offline_payload(root, manifest, payloads)
    before = {path.name: path.read_bytes() for path in root.iterdir()}

    def forbid_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("offline inspection attempted network access")

    monkeypatch.setattr(socket, "socket", forbid_network)
    verified = inspect_offline_payload(
        OfflinePayloadRequest(manifest, root, TARGET_ID)
    )

    assert verified.manifest_sha256 == manifest.sha256
    assert {path.name: path.read_bytes() for path in root.iterdir()} == before


@pytest.mark.parametrize("failure", ("missing", "hash", "manifest", "symlink"))
def test_offline_payload_fails_before_materialization(
    failure: str,
    tmp_path: Path,
) -> None:
    payloads = _payloads()
    manifest = _manifest(payloads)
    root = tmp_path / "payload"
    _write_offline_payload(root, manifest, payloads)
    wheel = root / "dependency-1-py3-none-any.whl"
    if failure == "missing":
        wheel.unlink()
    elif failure == "hash":
        wheel.write_bytes(b"x" * len(payloads[wheel.name]))
    elif failure == "manifest":
        other = replace(manifest, release_id="easyqc-2.0.0-linux")
        (root / OFFLINE_MANIFEST_FILENAME).write_bytes(other.canonical_bytes)
    else:
        outside = tmp_path / "outside.whl"
        outside.write_bytes(payloads[wheel.name])
        wheel.unlink()
        try:
            wheel.symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation is unavailable")

    with pytest.raises(ManagedRuntimeError, match="missing|hash|manifest|symlink"):
        inspect_offline_payload(OfflinePayloadRequest(manifest, root, TARGET_ID))
    assert not (tmp_path / "versions").exists()
