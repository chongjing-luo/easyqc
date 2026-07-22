"""Verified online acquisition and read-only offline payload inspection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import errno
import hashlib
import os
from pathlib import Path
import stat
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, build_opener

from core.managed_runtime import (
    ManagedRuntimeError,
    VerifiedArtifactSet,
    verify_artifact_set,
)
from models.managed_runtime import (
    ArtifactSourceV1,
    ReleaseManifestV1,
)


OFFLINE_MANIFEST_FILENAME = "release-manifest.json"
_CHUNK_BYTES = 1024 * 1024
_DISK_MARGIN_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class OnlineAcquisitionRequest:
    """One authenticated manifest acquisition into a contained cache root."""

    manifest: ReleaseManifestV1
    destination: Path
    expected_target_id: str
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        _validate_request_fields(
            self.manifest,
            self.destination,
            self.expected_target_id,
            self.timeout_seconds,
            "online acquisition",
        )


@dataclass(frozen=True)
class OfflinePayloadRequest:
    """One authenticated manifest inspection against an existing payload."""

    manifest: ReleaseManifestV1
    payload_root: Path
    expected_target_id: str

    def __post_init__(self) -> None:
        _validate_request_fields(
            self.manifest,
            self.payload_root,
            self.expected_target_id,
            1.0,
            "offline payload",
        )


class HttpsTransport(Protocol):
    """Bounded HTTPS byte-stream boundary used by online acquisition."""

    def stream(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
        timeout_seconds: float,
        maximum_bytes: int,
    ) -> Iterable[bytes]:
        """Yield response chunks after enforcing HTTPS redirect authority."""


class _AllowlistedRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        super().__init__()
        self._allowed_hosts = allowed_hosts

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        _validated_https_url(newurl, self._allowed_hosts, "redirect")
        return super().redirect_request(
            req,
            fp,
            code,
            msg,
            headers,
            newurl,
        )


class UrllibHttpsTransport:
    """Standard-library HTTPS transport with an allowlisted redirect policy."""

    def __init__(self, *, opener: OpenerDirector | object | None = None) -> None:
        self._opener = opener

    def stream(
        self,
        url: str,
        *,
        allowed_hosts: tuple[str, ...],
        timeout_seconds: float,
        maximum_bytes: int,
    ) -> Iterable[bytes]:
        _validated_https_url(url, allowed_hosts, "source")
        opener = self._opener or build_opener(
            _AllowlistedRedirectHandler(allowed_hosts)
        )
        host = urlsplit(url).hostname or "unknown"
        try:
            response = opener.open(  # type: ignore[attr-defined]
                url,
                timeout=timeout_seconds,
            )
            with response:
                final_url = response.geturl()
                _validated_https_url(final_url, allowed_hosts, "redirect")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared = int(content_length)
                    except (TypeError, ValueError) as exc:
                        raise ManagedRuntimeError(
                            f"HTTPS response from {host} has invalid Content-Length"
                        ) from exc
                    if declared < 0 or declared > maximum_bytes:
                        raise ManagedRuntimeError(
                            f"HTTPS response from {host} exceeds expected size"
                        )
                while True:
                    chunk = response.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise ManagedRuntimeError(
                            f"HTTPS response from {host} returned non-byte data"
                        )
                    yield chunk
        except ManagedRuntimeError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ManagedRuntimeError(
                f"HTTPS source host {host} failed: {exc}"
            ) from exc


@dataclass(frozen=True)
class _PhysicalIdentity:
    kind: str
    filename: str
    size_bytes: int
    sha256: str


def acquire_online(
    request: OnlineAcquisitionRequest,
    *,
    transport: HttpsTransport | None = None,
) -> VerifiedArtifactSet:
    """Acquire manifest-named bytes and return the existing verified set.

    The authenticated manifest is an input authority; this function does not
    retrieve or authenticate manifests. Only the destination directory,
    regular ``.partial`` files and atomically promoted cache files may change.
    """

    if not isinstance(request, OnlineAcquisitionRequest):
        raise ManagedRuntimeError(
            "online acquisition request must be OnlineAcquisitionRequest"
        )
    _require_target(request.manifest, request.expected_target_id)
    destination = _canonical_absolute_path(
        request.destination,
        "online acquisition destination",
    )
    if destination.exists() or destination.is_symlink():
        _require_real_directory(destination, "online acquisition destination")

    identities = _physical_identities(request.manifest)
    cached: dict[str, bool] = {}
    for identity in identities:
        cached[identity.filename] = _cached_identity_matches(
            destination / identity.filename,
            identity,
        )
    pending = tuple(
        identity for identity in identities if not cached[identity.filename]
    )
    sources = {
        source.artifact_filename: source for source in request.manifest.sources
    }
    missing_sources = [
        identity.filename
        for identity in pending
        if identity.filename not in sources
    ]
    if missing_sources:
        raise ManagedRuntimeError(
            "source authority is missing for " + ", ".join(missing_sources)
        )
    _require_online_disk_formula(request.manifest, pending)
    if not pending:
        return verify_artifact_set(
            request.manifest,
            destination,
            expected_target_id=request.expected_target_id,
        )

    _create_real_directory(destination, "online acquisition destination")
    active_transport = transport or UrllibHttpsTransport()
    for identity in pending:
        _acquire_identity(
            destination,
            identity,
            sources[identity.filename],
            active_transport,
            request.timeout_seconds,
        )
    return verify_artifact_set(
        request.manifest,
        destination,
        expected_target_id=request.expected_target_id,
    )


def inspect_offline_payload(
    request: OfflinePayloadRequest,
) -> VerifiedArtifactSet:
    """Verify one complete payload using filesystem reads only."""

    if not isinstance(request, OfflinePayloadRequest):
        raise ManagedRuntimeError(
            "offline payload request must be OfflinePayloadRequest"
        )
    _require_target(request.manifest, request.expected_target_id)
    root = _canonical_absolute_path(request.payload_root, "offline payload root")
    _require_real_directory(root, "offline payload root")
    manifest_path = root / OFFLINE_MANIFEST_FILENAME
    manifest_bytes = _read_regular_file(
        manifest_path,
        "offline payload manifest",
        maximum_bytes=max(len(request.manifest.canonical_bytes), 1),
    )
    if manifest_bytes != request.manifest.canonical_bytes:
        raise ManagedRuntimeError(
            "offline payload manifest does not match authenticated manifest"
        )
    return verify_artifact_set(
        request.manifest,
        root,
        expected_target_id=request.expected_target_id,
    )


def _validate_request_fields(
    manifest: object,
    root: object,
    expected_target_id: object,
    timeout_seconds: object,
    label: str,
) -> None:
    if not isinstance(manifest, ReleaseManifestV1):
        raise ManagedRuntimeError(f"{label} manifest must be ReleaseManifestV1")
    if not isinstance(root, Path):
        raise ManagedRuntimeError(f"{label} root must be pathlib.Path")
    if not isinstance(expected_target_id, str) or not expected_target_id:
        raise ManagedRuntimeError(f"{label} expected target ID is required")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise ManagedRuntimeError(f"{label} timeout must be positive")


def _require_target(manifest: ReleaseManifestV1, expected_target_id: str) -> None:
    if manifest.target.target_id != expected_target_id:
        raise ManagedRuntimeError(
            "artifact acquisition target mismatch: "
            f"expected {expected_target_id}, got {manifest.target.target_id}"
        )


def _physical_identities(
    manifest: ReleaseManifestV1,
) -> tuple[_PhysicalIdentity, ...]:
    return (
        _PhysicalIdentity(
            "uv",
            manifest.uv.filename,
            manifest.uv.size_bytes,
            manifest.uv.sha256,
        ),
        _PhysicalIdentity(
            "python",
            manifest.python.filename,
            manifest.python.size_bytes,
            manifest.python.sha256,
        ),
        *(
            _PhysicalIdentity(
                item.kind,
                item.filename,
                item.size_bytes,
                item.sha256,
            )
            for item in manifest.artifacts
        ),
    )


def _require_online_disk_formula(
    manifest: ReleaseManifestV1,
    pending: tuple[_PhysicalIdentity, ...],
) -> None:
    missing_payload_bytes = sum(item.size_bytes for item in pending)
    total = missing_payload_bytes + manifest.expanded_version_bytes
    minimum = total + max(_DISK_MARGIN_BYTES, (total + 9) // 10)
    if manifest.required_free_bytes < minimum:
        raise ManagedRuntimeError(
            "manifest required_free_bytes is below the online disk formula"
        )


def _cached_identity_matches(
    path: Path,
    identity: _PhysicalIdentity,
) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ManagedRuntimeError(
            f"cannot inspect cached artifact {identity.filename}: {exc}"
        ) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ManagedRuntimeError(
            f"cached artifact is a symlink: {identity.filename}"
        )
    if not stat.S_ISREG(metadata.st_mode):
        raise ManagedRuntimeError(
            f"cached artifact is not a regular file: {identity.filename}"
        )
    if metadata.st_size != identity.size_bytes:
        return False
    return _sha256_regular_file(path, metadata, identity.filename) == identity.sha256


def _acquire_identity(
    destination: Path,
    identity: _PhysicalIdentity,
    source: ArtifactSourceV1,
    transport: HttpsTransport,
    timeout_seconds: float,
) -> None:
    partial = destination / f"{identity.filename}.partial"
    last_error: ManagedRuntimeError | None = None
    for url in source.https_urls:
        try:
            _download_partial(
                partial,
                identity,
                transport.stream(
                    url,
                    allowed_hosts=source.allowed_hosts,
                    timeout_seconds=timeout_seconds,
                    maximum_bytes=identity.size_bytes,
                ),
            )
            _promote_partial(partial, destination / identity.filename, identity)
            return
        except ManagedRuntimeError as exc:
            last_error = exc
        except (OSError, RuntimeError) as exc:
            host = urlsplit(url).hostname or "unknown"
            last_error = ManagedRuntimeError(
                f"download failed for {identity.filename} from host {host}: {exc}"
            )
    assert last_error is not None
    raise ManagedRuntimeError(
        f"all HTTPS sources failed for {identity.filename}; "
        f"partial={partial}: {last_error}"
    ) from last_error


def _download_partial(
    partial: Path,
    identity: _PhysicalIdentity,
    chunks: Iterable[bytes],
) -> None:
    descriptor = _open_partial(partial)
    digest = hashlib.sha256()
    written = 0
    try:
        for chunk in chunks:
            if not isinstance(chunk, bytes):
                raise ManagedRuntimeError(
                    f"download for {identity.filename} returned non-byte data"
                )
            written += _write_all(descriptor, chunk, identity.filename)
            digest.update(chunk)
            if written > identity.size_bytes:
                raise ManagedRuntimeError(
                    f"download size mismatch for {identity.filename}: "
                    f"expected {identity.size_bytes}, got at least {written}"
                )
        os.fsync(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    os.close(descriptor)
    if written != identity.size_bytes:
        raise ManagedRuntimeError(
            f"download size mismatch for {identity.filename}: "
            f"expected {identity.size_bytes}, got {written}"
        )
    actual_hash = digest.hexdigest()
    if actual_hash != identity.sha256:
        raise ManagedRuntimeError(
            f"download hash mismatch for {identity.filename}: "
            f"expected {identity.sha256}, got {actual_hash}"
        )


def _write_all(descriptor: int, data: bytes, label: str) -> int:
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        count = os.write(descriptor, view[offset:])
        if count <= 0 or count > len(view) - offset:
            raise ManagedRuntimeError(
                f"download write made no valid progress for {label}"
            )
        offset += count
    return offset


def _open_partial(path: Path) -> int:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        metadata = None
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect download partial: {exc}") from exc
    if metadata is not None:
        if stat.S_ISLNK(metadata.st_mode):
            raise ManagedRuntimeError("download partial must not be a symlink")
        if not stat.S_ISREG(metadata.st_mode):
            raise ManagedRuntimeError("download partial must be a regular file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    try:
        return os.open(path, flags, 0o600)
    except PermissionError as exc:
        raise ManagedRuntimeError(
            f"download partial permission denied: {exc}"
        ) from exc
    except OSError as exc:
        if exc.errno == getattr(errno, "ELOOP", -1):
            raise ManagedRuntimeError(
                "download partial must not be a symlink"
            ) from exc
        raise ManagedRuntimeError(f"cannot open download partial: {exc}") from exc


def _promote_partial(
    partial: Path,
    destination: Path,
    identity: _PhysicalIdentity,
) -> None:
    try:
        metadata = destination.lstat()
    except FileNotFoundError:
        metadata = None
    except OSError as exc:
        raise ManagedRuntimeError(
            f"cannot inspect download destination {identity.filename}: {exc}"
        ) from exc
    if metadata is not None and (
        stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
    ):
        raise ManagedRuntimeError(
            f"download destination must be a regular non-symlink file: "
            f"{identity.filename}"
        )
    try:
        os.replace(partial, destination)
        _fsync_directory(destination.parent)
    except OSError as exc:
        raise ManagedRuntimeError(
            f"cannot promote verified download {identity.filename}: {exc}"
        ) from exc


def _sha256_regular_file(
    path: Path,
    before: os.stat_result,
    label: str,
) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK_BYTES):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise ManagedRuntimeError(
            f"cannot hash cached artifact {label}: {exc}"
        ) from exc
    if (
        (before.st_dev, before.st_ino, before.st_size)
        != (after.st_dev, after.st_ino, after.st_size)
    ):
        raise ManagedRuntimeError(f"cached artifact changed while hashing: {label}")
    return digest.hexdigest()


def _read_regular_file(path: Path, label: str, *, maximum_bytes: int) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ManagedRuntimeError(f"{label} is missing") from exc
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot inspect {label}: {exc}") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must not be a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must be a regular file")
    if metadata.st_size > maximum_bytes:
        raise ManagedRuntimeError(f"{label} exceeds the expected size")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot read {label}: {exc}") from exc
    if len(data) != metadata.st_size:
        raise ManagedRuntimeError(f"{label} changed while reading")
    return data


def _canonical_absolute_path(path: Path, label: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute():
        raise ManagedRuntimeError(f"{label} must be an absolute pathlib.Path")
    if ".." in path.parts or str(path) != os.path.normpath(str(path)):
        raise ManagedRuntimeError(f"{label} must be canonical")
    if path.parent == path:
        raise ManagedRuntimeError(f"{label} must not be a filesystem root")
    return path


def _require_real_directory(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManagedRuntimeError(f"{label} must be an existing directory") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must not be a symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ManagedRuntimeError(f"{label} must be a directory")


def _create_real_directory(path: Path, label: str) -> None:
    if path.exists() or path.is_symlink():
        _require_real_directory(path, label)
        return
    try:
        path.mkdir(mode=0o700)
        _fsync_directory(path.parent)
    except PermissionError as exc:
        raise ManagedRuntimeError(f"{label} permission denied: {exc}") from exc
    except OSError as exc:
        raise ManagedRuntimeError(f"cannot create {label}: {exc}") from exc
    _require_real_directory(path, label)


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validated_https_url(
    url: str,
    allowed_hosts: tuple[str, ...],
    label: str,
) -> str:
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ManagedRuntimeError(f"{label} URL has an invalid port") from exc
    host = parsed.hostname.lower() if parsed.hostname else None
    if (
        parsed.scheme != "https"
        or host is None
        or host not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port is not None
        or parsed.fragment
    ):
        if label == "redirect":
            raise ManagedRuntimeError(
                "redirect URL is outside the explicit allowed host set"
            )
        raise ManagedRuntimeError(
            "source URL must be HTTPS on an explicit allowed host"
        )
    return host


__all__ = [
    "HttpsTransport",
    "OFFLINE_MANIFEST_FILENAME",
    "OfflinePayloadRequest",
    "OnlineAcquisitionRequest",
    "UrllibHttpsTransport",
    "acquire_online",
    "inspect_offline_payload",
]
