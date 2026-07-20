"""Strict authority contracts for the EasyQC managed runtime.

Models in this module are immutable, use only the Python standard library, and
perform no file or process I/O.  Canonical bytes are part of each authority's
contract rather than a presentation detail.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Mapping, Sequence
from urllib.parse import urlsplit


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_MAX_COLLECTION_ITEMS = 4096
_MIN_DISK_MARGIN_BYTES = 256 * 1024 * 1024

_TARGET_IDS = {
    ("linux", "22.04", "x86_64"): "ubuntu-22.04-x86_64",
    ("linux", "24.04", "x86_64"): "ubuntu-24.04-x86_64",
    ("windows", "11", "x86_64"): "windows-11-x86_64",
    ("macos", "13", "arm64"): "macos-13-arm64",
}
_PYTHON_KEYS = {
    "ubuntu-22.04-x86_64": "cpython-3.13.13-linux-x86_64-gnu",
    "ubuntu-24.04-x86_64": "cpython-3.13.13-linux-x86_64-gnu",
    "windows-11-x86_64": "cpython-3.13.13-windows-x86_64-none",
    "macos-13-arm64": "cpython-3.13.13-darwin-aarch64-none",
}


class ManagedRuntimeContractError(ValueError):
    """Raised when managed-runtime authority is malformed or inconsistent."""


def canonical_json_bytes(value: object) -> bytes:
    """Return the sole canonical UTF-8 JSON representation of ``value``."""

    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ManagedRuntimeContractError(
            f"value is not finite JSON data: {exc}"
        ) from exc
    return f"{encoded}\n".encode("utf-8")


def _parse_canonical_object(data: bytes, label: str) -> dict[str, object]:
    if not isinstance(data, bytes):
        raise ManagedRuntimeContractError(f"{label} must be bytes")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ManagedRuntimeContractError(f"{label} must be UTF-8") from exc

    def reject_duplicate_keys(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ManagedRuntimeContractError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ManagedRuntimeContractError(f"invalid JSON number: {token}")
            ),
        )
    except ManagedRuntimeContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ManagedRuntimeContractError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManagedRuntimeContractError(f"{label} must contain one JSON object")
    if canonical_json_bytes(value) != data:
        raise ManagedRuntimeContractError(f"{label} is not canonical JSON")
    return value


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ManagedRuntimeContractError(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ManagedRuntimeContractError(f"{label} must be an array")
    if len(value) > _MAX_COLLECTION_ITEMS:
        raise ManagedRuntimeContractError(f"{label} contains too many items")
    return value


def _expect_fields(
    value: Mapping[str, object], expected: set[str], label: str
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing fields {missing}")
        if unknown:
            details.append(f"unknown fields {unknown}")
        raise ManagedRuntimeContractError(f"{label}: {'; '.join(details)}")


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ManagedRuntimeContractError(f"{label} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ManagedRuntimeContractError(f"{label} contains a control character")
    return value


def _token(value: object, label: str) -> str:
    result = _text(value, label)
    if not _TOKEN_PATTERN.fullmatch(result):
        raise ManagedRuntimeContractError(f"{label} is invalid")
    return result


def _release_id(value: object, label: str = "release_id") -> str:
    result = _text(value, label)
    if not _RELEASE_ID_PATTERN.fullmatch(result):
        raise ManagedRuntimeContractError(f"{label} is invalid")
    return result


def _sha256(value: object, label: str) -> str:
    result = _text(value, label)
    if not _SHA256_PATTERN.fullmatch(result):
        raise ManagedRuntimeContractError(f"{label} must be a lowercase SHA-256")
    return result


def _integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if minimum == 1 else "nonnegative"
        raise ManagedRuntimeContractError(f"{label} must be a {qualifier} integer")
    return value


def _basename(value: object, label: str) -> str:
    result = _text(value, label)
    if (
        result in {".", ".."}
        or len(result) > 255
        or "/" in result
        or "\\" in result
        or ":" in result
    ):
        raise ManagedRuntimeContractError(f"{label} filename must be one safe basename")
    return result


def _entrypoint(value: object) -> str:
    result = _text(value, "easyqc.entrypoint")
    if "\\" in result or ":" in result:
        raise ManagedRuntimeContractError(
            "easyqc.entrypoint must be a safe relative POSIX path"
        )
    path = PurePosixPath(result)
    if (
        path.is_absolute()
        or not path.parts
        or path.as_posix() != result
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ManagedRuntimeContractError(
            "easyqc.entrypoint must be a safe relative POSIX path"
        )
    return path.as_posix()


def _timestamp(value: object, label: str) -> str:
    result = _text(value, label)
    if not _UTC_TIMESTAMP_PATTERN.fullmatch(result):
        raise ManagedRuntimeContractError(
            f"{label} must be a complete canonical UTC timestamp ending in Z"
        )
    try:
        parsed = datetime.fromisoformat(f"{result[:-1]}+00:00")
    except ValueError as exc:
        raise ManagedRuntimeContractError(f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ManagedRuntimeContractError(f"{label} must be UTC")
    return result


def _install_root(value: object, target_os: str) -> str:
    result = _text(value, "install_root")
    path = PureWindowsPath(result) if target_os == "windows" else PurePosixPath(result)
    canonical = str(path) if target_os == "windows" else path.as_posix()
    if not path.is_absolute() or ".." in path.parts or canonical != result:
        raise ManagedRuntimeContractError("install_root must be an absolute canonical path")
    return result


@dataclass(frozen=True)
class RuntimeTargetV1:
    os: str
    os_minimum: str
    arch: str

    def __post_init__(self) -> None:
        if (self.os, self.os_minimum, self.arch) not in _TARGET_IDS:
            raise ManagedRuntimeContractError("unsupported target identity")

    @property
    def target_id(self) -> str:
        return _TARGET_IDS[(self.os, self.os_minimum, self.arch)]

    def to_json_object(self) -> dict[str, str]:
        return {"os": self.os, "os_minimum": self.os_minimum, "arch": self.arch}

    @classmethod
    def from_json_object(cls, value: object) -> "RuntimeTargetV1":
        record = _object(value, "target")
        _expect_fields(record, {"os", "os_minimum", "arch"}, "target")
        return cls(
            os=_text(record["os"], "target.os"),
            os_minimum=_text(record["os_minimum"], "target.os_minimum"),
            arch=_text(record["arch"], "target.arch"),
        )


@dataclass(frozen=True)
class EasyQCIdentityV1:
    version: str
    source_filename: str
    source_sha256: str
    entrypoint: str

    def __post_init__(self) -> None:
        _token(self.version, "easyqc.version")
        _basename(self.source_filename, "easyqc.source_filename")
        _sha256(self.source_sha256, "easyqc.source_sha256")
        _entrypoint(self.entrypoint)

    def to_json_object(self) -> dict[str, object]:
        return {
            "version": self.version,
            "source_filename": self.source_filename,
            "source_sha256": self.source_sha256,
            "entrypoint": self.entrypoint,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "EasyQCIdentityV1":
        record = _object(value, "easyqc")
        _expect_fields(
            record,
            {"version", "source_filename", "source_sha256", "entrypoint"},
            "easyqc",
        )
        return cls(
            version=_token(record["version"], "easyqc.version"),
            source_filename=_basename(
                record["source_filename"], "easyqc.source_filename"
            ),
            source_sha256=_sha256(
                record["source_sha256"], "easyqc.source_sha256"
            ),
            entrypoint=_entrypoint(record["entrypoint"]),
        )


@dataclass(frozen=True)
class UvIdentityV1:
    version: str
    filename: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if self.version != "0.11.29":
            raise ManagedRuntimeContractError("uv.version must be 0.11.29")
        _basename(self.filename, "uv.filename")
        _integer(self.size_bytes, "uv.size_bytes", minimum=1)
        _sha256(self.sha256, "uv.sha256")

    def to_json_object(self) -> dict[str, object]:
        return {
            "version": self.version,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "UvIdentityV1":
        record = _object(value, "uv")
        _expect_fields(record, {"version", "filename", "size_bytes", "sha256"}, "uv")
        return cls(
            version=_text(record["version"], "uv.version"),
            filename=_basename(record["filename"], "uv.filename"),
            size_bytes=_integer(record["size_bytes"], "uv.size_bytes", minimum=1),
            sha256=_sha256(record["sha256"], "uv.sha256"),
        )


@dataclass(frozen=True)
class PythonIdentityV1:
    version: str
    build: str
    key: str
    filename: str
    size_bytes: int
    sha256: str

    def validate_for(self, target: RuntimeTargetV1) -> None:
        if self.version != "3.13.13":
            raise ManagedRuntimeContractError("python.version must be 3.13.13")
        if self.key != _PYTHON_KEYS[target.target_id]:
            raise ManagedRuntimeContractError("python.key does not match target")

    def __post_init__(self) -> None:
        if self.version != "3.13.13":
            raise ManagedRuntimeContractError("python.version must be 3.13.13")
        _token(self.build, "python.build")
        _token(self.key, "python.key")
        _basename(self.filename, "python.filename")
        _integer(self.size_bytes, "python.size_bytes", minimum=1)
        _sha256(self.sha256, "python.sha256")

    def to_json_object(self) -> dict[str, object]:
        return {
            "version": self.version,
            "build": self.build,
            "key": self.key,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "PythonIdentityV1":
        record = _object(value, "python")
        _expect_fields(
            record,
            {"version", "build", "key", "filename", "size_bytes", "sha256"},
            "python",
        )
        return cls(
            version=_text(record["version"], "python.version"),
            build=_token(record["build"], "python.build"),
            key=_token(record["key"], "python.key"),
            filename=_basename(record["filename"], "python.filename"),
            size_bytes=_integer(
                record["size_bytes"], "python.size_bytes", minimum=1
            ),
            sha256=_sha256(record["sha256"], "python.sha256"),
        )


@dataclass(frozen=True)
class LockIdentityV1:
    filename: str
    sha256: str
    require_hashes: bool

    def __post_init__(self) -> None:
        _basename(self.filename, "lock.filename")
        _sha256(self.sha256, "lock.sha256")
        if self.require_hashes is not True:
            raise ManagedRuntimeContractError("lock.require_hashes must be true")

    def to_json_object(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "require_hashes": self.require_hashes,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "LockIdentityV1":
        record = _object(value, "lock")
        _expect_fields(record, {"filename", "sha256", "require_hashes"}, "lock")
        return cls(
            filename=_basename(record["filename"], "lock.filename"),
            sha256=_sha256(record["sha256"], "lock.sha256"),
            require_hashes=record["require_hashes"],  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class ArtifactIdentityV1:
    kind: str
    filename: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if not _KIND_PATTERN.fullmatch(self.kind):
            raise ManagedRuntimeContractError("artifact.kind is invalid")
        _basename(self.filename, "artifact.filename")
        _integer(self.size_bytes, "artifact.size_bytes", minimum=1)
        _sha256(self.sha256, "artifact.sha256")

    def to_json_object(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "filename": self.filename,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_json_object(cls, value: object, index: int) -> "ArtifactIdentityV1":
        label = f"artifacts[{index}]"
        record = _object(value, label)
        _expect_fields(record, {"kind", "filename", "size_bytes", "sha256"}, label)
        kind = _text(record["kind"], f"{label}.kind")
        if not _KIND_PATTERN.fullmatch(kind):
            raise ManagedRuntimeContractError(f"{label}.kind is invalid")
        return cls(
            kind=kind,
            filename=_basename(record["filename"], f"{label}.filename"),
            size_bytes=_integer(
                record["size_bytes"], f"{label}.size_bytes", minimum=1
            ),
            sha256=_sha256(record["sha256"], f"{label}.sha256"),
        )


@dataclass(frozen=True)
class ArtifactSourceV1:
    artifact_filename: str
    https_urls: tuple[str, ...]
    allowed_hosts: tuple[str, ...]

    def __post_init__(self) -> None:
        _basename(self.artifact_filename, "source.artifact_filename")
        if not self.https_urls or not self.allowed_hosts:
            raise ManagedRuntimeContractError(
                "source https_urls and allowed_hosts must be non-empty"
            )
        if len(set(self.https_urls)) != len(self.https_urls):
            raise ManagedRuntimeContractError("source has duplicate HTTPS URLs")
        if len(set(self.allowed_hosts)) != len(self.allowed_hosts):
            raise ManagedRuntimeContractError("source has duplicate allowed hosts")
        for host in self.allowed_hosts:
            if host != host.lower() or not _HOST_PATTERN.fullmatch(host):
                raise ManagedRuntimeContractError("source allowed host is invalid")
        allowed = set(self.allowed_hosts)
        for raw_url in self.https_urls:
            parsed = urlsplit(raw_url)
            try:
                port = parsed.port
            except ValueError as exc:
                raise ManagedRuntimeContractError("source HTTPS URL has invalid port") from exc
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.hostname.lower() not in allowed
                or parsed.username is not None
                or parsed.password is not None
                or port is not None
                or parsed.fragment
            ):
                raise ManagedRuntimeContractError(
                    "source URL must be HTTPS on an explicit allowed host"
                )

    def to_json_object(self) -> dict[str, object]:
        return {
            "artifact_filename": self.artifact_filename,
            "https_urls": list(self.https_urls),
            "allowed_hosts": list(self.allowed_hosts),
        }

    @classmethod
    def from_json_object(cls, value: object, index: int) -> "ArtifactSourceV1":
        label = f"sources[{index}]"
        record = _object(value, label)
        _expect_fields(
            record, {"artifact_filename", "https_urls", "allowed_hosts"}, label
        )
        urls = tuple(
            _text(item, f"{label}.https_urls")
            for item in _array(record["https_urls"], f"{label}.https_urls")
        )
        hosts = tuple(
            _text(item, f"{label}.allowed_hosts")
            for item in _array(record["allowed_hosts"], f"{label}.allowed_hosts")
        )
        return cls(
            artifact_filename=_basename(
                record["artifact_filename"], f"{label}.artifact_filename"
            ),
            https_urls=urls,
            allowed_hosts=hosts,
        )


@dataclass(frozen=True)
class NoticeIdentityV1:
    filename: str
    sha256: str

    def __post_init__(self) -> None:
        _basename(self.filename, "notice.filename")
        _sha256(self.sha256, "notice.sha256")

    def to_json_object(self) -> dict[str, str]:
        return {"filename": self.filename, "sha256": self.sha256}

    @classmethod
    def from_json_object(cls, value: object, index: int) -> "NoticeIdentityV1":
        label = f"notices[{index}]"
        record = _object(value, label)
        _expect_fields(record, {"filename", "sha256"}, label)
        return cls(
            filename=_basename(record["filename"], f"{label}.filename"),
            sha256=_sha256(record["sha256"], f"{label}.sha256"),
        )


@dataclass(frozen=True)
class ReleaseManifestV1:
    schema_version: int
    release_id: str
    channel: str
    target: RuntimeTargetV1
    easyqc: EasyQCIdentityV1
    uv: UvIdentityV1
    python: PythonIdentityV1
    lock: LockIdentityV1
    artifacts: tuple[ArtifactIdentityV1, ...]
    sources: tuple[ArtifactSourceV1, ...]
    notices: tuple[NoticeIdentityV1, ...]
    expanded_version_bytes: int
    required_free_bytes: int
    smoke_contract_version: int

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ManagedRuntimeContractError("schema_version must be 1")
        _release_id(self.release_id)
        if self.channel not in {"stable", "candidate"}:
            raise ManagedRuntimeContractError("channel must be stable or candidate")
        self.python.validate_for(self.target)
        _integer(
            self.expanded_version_bytes, "expanded_version_bytes", minimum=1
        )
        _integer(self.required_free_bytes, "required_free_bytes", minimum=1)
        _integer(
            self.smoke_contract_version, "smoke_contract_version", minimum=1
        )
        minimum_free = self.expanded_version_bytes + max(
            _MIN_DISK_MARGIN_BYTES,
            (self.expanded_version_bytes + 9) // 10,
        )
        if self.required_free_bytes < minimum_free:
            raise ManagedRuntimeContractError(
                "required_free_bytes is below the offline disk formula"
            )

        semantic_names = [
            self.easyqc.source_filename,
            self.uv.filename,
            self.python.filename,
            self.lock.filename,
            *(notice.filename for notice in self.notices),
        ]
        if len(set(semantic_names)) != len(semantic_names):
            raise ManagedRuntimeContractError("duplicate semantic artifact filename")

        artifact_by_name: dict[str, ArtifactIdentityV1] = {}
        for artifact in self.artifacts:
            if artifact.filename in artifact_by_name:
                raise ManagedRuntimeContractError(
                    f"duplicate artifact filename: {artifact.filename}"
                )
            artifact_by_name[artifact.filename] = artifact
        if self.uv.filename in artifact_by_name or self.python.filename in artifact_by_name:
            raise ManagedRuntimeContractError(
                "uv/python filenames must not duplicate artifacts entries"
            )

        required_references = {
            self.easyqc.source_filename: (self.easyqc.source_sha256, "EasyQC source"),
            self.lock.filename: (self.lock.sha256, "lock"),
            **{
                notice.filename: (notice.sha256, f"NOTICE {notice.filename}")
                for notice in self.notices
            },
        }
        for filename, (digest, label) in required_references.items():
            artifact = artifact_by_name.get(filename)
            if artifact is None:
                raise ManagedRuntimeContractError(
                    f"{label} requires a size-bearing artifacts entry"
                )
            if artifact.sha256 != digest:
                raise ManagedRuntimeContractError(
                    f"{label} SHA-256 conflicts with artifacts entry"
                )

        physical_names = set(artifact_by_name) | {self.uv.filename, self.python.filename}
        source_names: set[str] = set()
        for source in self.sources:
            if source.artifact_filename in source_names:
                raise ManagedRuntimeContractError(
                    f"duplicate source authority for {source.artifact_filename}"
                )
            if source.artifact_filename not in physical_names:
                raise ManagedRuntimeContractError(
                    "source references an unknown artifact filename"
                )
            source_names.add(source.artifact_filename)

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_object())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "channel": self.channel,
            "target": self.target.to_json_object(),
            "easyqc": self.easyqc.to_json_object(),
            "uv": self.uv.to_json_object(),
            "python": self.python.to_json_object(),
            "lock": self.lock.to_json_object(),
            "artifacts": [item.to_json_object() for item in self.artifacts],
            "sources": [item.to_json_object() for item in self.sources],
            "notices": [item.to_json_object() for item in self.notices],
            "expanded_version_bytes": self.expanded_version_bytes,
            "required_free_bytes": self.required_free_bytes,
            "smoke_contract_version": self.smoke_contract_version,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "ReleaseManifestV1":
        record = _object(value, "ReleaseManifestV1")
        _expect_fields(
            record,
            {
                "schema_version",
                "release_id",
                "channel",
                "target",
                "easyqc",
                "uv",
                "python",
                "lock",
                "artifacts",
                "sources",
                "notices",
                "expanded_version_bytes",
                "required_free_bytes",
                "smoke_contract_version",
            },
            "ReleaseManifestV1",
        )
        artifacts = tuple(
            ArtifactIdentityV1.from_json_object(item, index)
            for index, item in enumerate(_array(record["artifacts"], "artifacts"))
        )
        sources = tuple(
            ArtifactSourceV1.from_json_object(item, index)
            for index, item in enumerate(_array(record["sources"], "sources"))
        )
        notices = tuple(
            NoticeIdentityV1.from_json_object(item, index)
            for index, item in enumerate(_array(record["notices"], "notices"))
        )
        return cls(
            schema_version=_integer(
                record["schema_version"], "schema_version", minimum=1
            ),
            release_id=_release_id(record["release_id"]),
            channel=_text(record["channel"], "channel"),
            target=RuntimeTargetV1.from_json_object(record["target"]),
            easyqc=EasyQCIdentityV1.from_json_object(record["easyqc"]),
            uv=UvIdentityV1.from_json_object(record["uv"]),
            python=PythonIdentityV1.from_json_object(record["python"]),
            lock=LockIdentityV1.from_json_object(record["lock"]),
            artifacts=artifacts,
            sources=sources,
            notices=notices,
            expanded_version_bytes=_integer(
                record["expanded_version_bytes"],
                "expanded_version_bytes",
                minimum=1,
            ),
            required_free_bytes=_integer(
                record["required_free_bytes"], "required_free_bytes", minimum=1
            ),
            smoke_contract_version=_integer(
                record["smoke_contract_version"],
                "smoke_contract_version",
                minimum=1,
            ),
        )

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> "ReleaseManifestV1":
        return cls.from_json_object(_parse_canonical_object(data, "ReleaseManifestV1"))


@dataclass(frozen=True)
class ReceiptUvV1:
    version: str
    sha256: str

    def __post_init__(self) -> None:
        if self.version != "0.11.29":
            raise ManagedRuntimeContractError("receipt uv.version must be 0.11.29")
        _sha256(self.sha256, "receipt uv.sha256")

    def to_json_object(self) -> dict[str, str]:
        return {"version": self.version, "sha256": self.sha256}

    @classmethod
    def from_json_object(cls, value: object) -> "ReceiptUvV1":
        record = _object(value, "receipt.uv")
        _expect_fields(record, {"version", "sha256"}, "receipt.uv")
        return cls(
            version=_text(record["version"], "receipt uv.version"),
            sha256=_sha256(record["sha256"], "receipt uv.sha256"),
        )


@dataclass(frozen=True)
class ReceiptPythonV1:
    version: str
    build: str
    key: str
    payload_sha256: str

    def __post_init__(self) -> None:
        if self.version != "3.13.13":
            raise ManagedRuntimeContractError(
                "receipt python.version must be 3.13.13"
            )
        _token(self.build, "receipt python.build")
        _token(self.key, "receipt python.key")
        _sha256(self.payload_sha256, "receipt python.payload_sha256")

    def to_json_object(self) -> dict[str, str]:
        return {
            "version": self.version,
            "build": self.build,
            "key": self.key,
            "payload_sha256": self.payload_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "ReceiptPythonV1":
        record = _object(value, "receipt.python")
        _expect_fields(
            record, {"version", "build", "key", "payload_sha256"}, "receipt.python"
        )
        return cls(
            version=_text(record["version"], "receipt python.version"),
            build=_token(record["build"], "receipt python.build"),
            key=_token(record["key"], "receipt python.key"),
            payload_sha256=_sha256(
                record["payload_sha256"], "receipt python.payload_sha256"
            ),
        )


@dataclass(frozen=True)
class SmokeReceiptV1:
    contract_version: int
    passed: bool
    completed_at_utc: str
    report_sha256: str

    def __post_init__(self) -> None:
        _integer(self.contract_version, "smoke.contract_version", minimum=1)
        if self.passed is not True:
            raise ManagedRuntimeContractError("receipt smoke must be PASS")
        _timestamp(self.completed_at_utc, "smoke.completed_at_utc")
        _sha256(self.report_sha256, "smoke.report_sha256")

    def to_json_object(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "passed": self.passed,
            "completed_at_utc": self.completed_at_utc,
            "report_sha256": self.report_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "SmokeReceiptV1":
        record = _object(value, "smoke")
        _expect_fields(
            record,
            {"contract_version", "passed", "completed_at_utc", "report_sha256"},
            "smoke",
        )
        return cls(
            contract_version=_integer(
                record["contract_version"], "smoke.contract_version", minimum=1
            ),
            passed=record["passed"],  # type: ignore[arg-type]
            completed_at_utc=_timestamp(
                record["completed_at_utc"], "smoke.completed_at_utc"
            ),
            report_sha256=_sha256(record["report_sha256"], "smoke.report_sha256"),
        )


@dataclass(frozen=True)
class InstallReceiptV1:
    schema_version: int
    release_id: str
    target: RuntimeTargetV1
    install_scope: str
    install_root: str
    manifest_sha256: str
    source_sha256: str
    uv: ReceiptUvV1
    python: ReceiptPythonV1
    lock_sha256: str
    artifact_manifest_sha256: str
    installed_file_set_sha256: str
    smoke: SmokeReceiptV1
    installed_at_utc: str
    controller_version: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ManagedRuntimeContractError("receipt schema_version must be 1")
        _release_id(self.release_id, "receipt release_id")
        if self.install_scope not in {"user", "system"}:
            raise ManagedRuntimeContractError(
                "receipt install_scope must be user or system"
            )
        _install_root(self.install_root, self.target.os)
        for digest, label in (
            (self.manifest_sha256, "manifest_sha256"),
            (self.source_sha256, "source_sha256"),
            (self.lock_sha256, "lock_sha256"),
            (self.artifact_manifest_sha256, "artifact_manifest_sha256"),
            (self.installed_file_set_sha256, "installed_file_set_sha256"),
        ):
            _sha256(digest, f"receipt {label}")
        _timestamp(self.installed_at_utc, "installed_at_utc")
        _token(self.controller_version, "controller_version")

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_object())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    def validate_for(
        self, manifest: ReleaseManifestV1, install_root: str
    ) -> None:
        expected_root = _install_root(install_root, manifest.target.os)
        checks = (
            (self.release_id == manifest.release_id, "release ID"),
            (self.target == manifest.target, "target"),
            (self.install_root == expected_root, "install root"),
            (self.manifest_sha256 == manifest.sha256, "manifest SHA-256"),
            (self.source_sha256 == manifest.easyqc.source_sha256, "source SHA-256"),
            (self.uv.version == manifest.uv.version, "uv version"),
            (self.uv.sha256 == manifest.uv.sha256, "uv SHA-256"),
            (self.python.version == manifest.python.version, "Python version"),
            (self.python.build == manifest.python.build, "Python build"),
            (self.python.key == manifest.python.key, "Python key"),
            (
                self.python.payload_sha256 == manifest.python.sha256,
                "Python payload SHA-256",
            ),
            (self.lock_sha256 == manifest.lock.sha256, "lock SHA-256"),
            (
                self.smoke.contract_version == manifest.smoke_contract_version,
                "smoke contract version",
            ),
        )
        for valid, label in checks:
            if not valid:
                raise ManagedRuntimeContractError(
                    f"receipt {label} does not match manifest/install authority"
                )

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "release_id": self.release_id,
            "target": self.target.to_json_object(),
            "install_scope": self.install_scope,
            "install_root": self.install_root,
            "manifest_sha256": self.manifest_sha256,
            "source_sha256": self.source_sha256,
            "uv": self.uv.to_json_object(),
            "python": self.python.to_json_object(),
            "lock_sha256": self.lock_sha256,
            "artifact_manifest_sha256": self.artifact_manifest_sha256,
            "installed_file_set_sha256": self.installed_file_set_sha256,
            "smoke": self.smoke.to_json_object(),
            "installed_at_utc": self.installed_at_utc,
            "controller_version": self.controller_version,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "InstallReceiptV1":
        record = _object(value, "InstallReceiptV1")
        _expect_fields(
            record,
            {
                "schema_version",
                "release_id",
                "target",
                "install_scope",
                "install_root",
                "manifest_sha256",
                "source_sha256",
                "uv",
                "python",
                "lock_sha256",
                "artifact_manifest_sha256",
                "installed_file_set_sha256",
                "smoke",
                "installed_at_utc",
                "controller_version",
            },
            "InstallReceiptV1",
        )
        target = RuntimeTargetV1.from_json_object(record["target"])
        return cls(
            schema_version=_integer(
                record["schema_version"], "receipt schema_version", minimum=1
            ),
            release_id=_release_id(record["release_id"], "receipt release_id"),
            target=target,
            install_scope=_text(record["install_scope"], "install_scope"),
            install_root=_install_root(record["install_root"], target.os),
            manifest_sha256=_sha256(
                record["manifest_sha256"], "receipt manifest_sha256"
            ),
            source_sha256=_sha256(
                record["source_sha256"], "receipt source_sha256"
            ),
            uv=ReceiptUvV1.from_json_object(record["uv"]),
            python=ReceiptPythonV1.from_json_object(record["python"]),
            lock_sha256=_sha256(record["lock_sha256"], "receipt lock_sha256"),
            artifact_manifest_sha256=_sha256(
                record["artifact_manifest_sha256"],
                "receipt artifact_manifest_sha256",
            ),
            installed_file_set_sha256=_sha256(
                record["installed_file_set_sha256"],
                "receipt installed_file_set_sha256",
            ),
            smoke=SmokeReceiptV1.from_json_object(record["smoke"]),
            installed_at_utc=_timestamp(
                record["installed_at_utc"], "installed_at_utc"
            ),
            controller_version=_token(
                record["controller_version"], "controller_version"
            ),
        )

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> "InstallReceiptV1":
        return cls.from_json_object(_parse_canonical_object(data, "InstallReceiptV1"))


@dataclass(frozen=True)
class ActivationPointerV1:
    active_release_id: str
    previous_release_id: str | None
    generation: int

    def __post_init__(self) -> None:
        _release_id(self.active_release_id, "active release ID")
        if self.previous_release_id is not None:
            _release_id(self.previous_release_id, "previous release ID")
            if self.previous_release_id == self.active_release_id:
                raise ManagedRuntimeContractError(
                    "active and previous release IDs must differ"
                )
        _integer(self.generation, "generation", minimum=0)

    def to_bytes(self) -> bytes:
        previous = self.previous_release_id or "-"
        return (
            f"{self.active_release_id}\n{previous}\n{self.generation}\n".encode(
                "ascii"
            )
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "ActivationPointerV1":
        if not isinstance(data, bytes):
            raise ManagedRuntimeContractError("activation pointer must be bytes")
        try:
            text = data.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise ManagedRuntimeContractError("activation pointer must be ASCII") from exc
        lines = text.splitlines(keepends=True)
        if len(lines) != 3 or any(not line.endswith("\n") for line in lines):
            raise ManagedRuntimeContractError(
                "activation pointer must contain exactly three LF-terminated lines"
            )
        if any(line.endswith("\r\n") for line in lines):
            raise ManagedRuntimeContractError("activation pointer requires LF line endings")
        active, previous, generation = (line[:-1] for line in lines)
        if not generation.isascii() or not generation.isdecimal():
            raise ManagedRuntimeContractError(
                "activation pointer generation must be nonnegative decimal"
            )
        if len(generation) > 20:
            raise ManagedRuntimeContractError(
                "activation pointer generation exceeds the supported range"
            )
        if len(generation) > 1 and generation.startswith("0"):
            raise ManagedRuntimeContractError(
                "activation pointer generation must be canonical decimal"
            )
        return cls(
            active_release_id=_release_id(active, "active release ID"),
            previous_release_id=(
                None
                if previous == "-"
                else _release_id(previous, "previous release ID")
            ),
            generation=int(generation),
        )


__all__ = [
    "ActivationPointerV1",
    "ArtifactIdentityV1",
    "ArtifactSourceV1",
    "EasyQCIdentityV1",
    "InstallReceiptV1",
    "LockIdentityV1",
    "ManagedRuntimeContractError",
    "NoticeIdentityV1",
    "PythonIdentityV1",
    "ReceiptPythonV1",
    "ReceiptUvV1",
    "ReleaseManifestV1",
    "RuntimeTargetV1",
    "SmokeReceiptV1",
    "UvIdentityV1",
    "canonical_json_bytes",
]
