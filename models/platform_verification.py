"""Strict, immutable platform verification and release-decision contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from types import MappingProxyType
from typing import Mapping, Sequence


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,159}$")
_VIEWPORT_PATTERN = re.compile(r"^[1-9][0-9]{2,4}x[1-9][0-9]{2,4}$")
_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_MAX_COLLECTION_ITEMS = 4096
_MAX_AUTHORITY_BYTES = 4 * 1024 * 1024

_TARGET_IDS = {
    "ubuntu-22.04-x86_64",
    "ubuntu-24.04-x86_64",
    "windows-11-x86_64",
    "macos-13-arm64",
}
_STATUS_REASONS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "FAIL": frozenset(
            {
                "test-failure",
                "smoke-failure",
                "identity-mismatch",
                "performance-gate-failure",
                "native-checklist-failure",
            }
        ),
        "NOT_RUN": frozenset(
            {
                "runner-not-provided",
                "session-not-available",
                "iso-license-not-provided",
                "minimum-platform-not-provided",
            }
        ),
        "BLOCKED": frozenset(
            {
                "environment-blocked",
                "dependency-missing",
                "permission-blocked",
            }
        ),
    }
)
_TEST_STATUSES = {"PASS", "FAIL", "SKIP"}
_ARTIFACT_CLASSIFICATIONS = {
    "test-report",
    "smoke-report",
    "benchmark-report",
    "native-checklist",
    "screenshot",
    "log",
}


class PlatformVerificationContractError(ValueError):
    """Raised when verification authority is malformed or inconsistent."""


@dataclass(frozen=True)
class MatrixRowV1:
    matrix_row_id: str
    evidence_class: str
    target_id: str

    def __post_init__(self) -> None:
        _identifier(self.matrix_row_id, "matrix_row_id")
        if self.evidence_class not in {"ci", "native"}:
            raise PlatformVerificationContractError(
                "matrix evidence_class must be ci or native"
            )
        if not self.matrix_row_id.startswith(f"{self.evidence_class}-"):
            raise PlatformVerificationContractError(
                "matrix_row_id must preserve the evidence class"
            )
        if self.target_id not in _TARGET_IDS:
            raise PlatformVerificationContractError("matrix target_id is unsupported")


def canonical_json_bytes(value: object) -> bytes:
    """Return sorted, compact, finite UTF-8 JSON with one terminal LF."""

    try:
        text = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise PlatformVerificationContractError(
            f"value is not finite JSON data: {exc}"
        ) from exc
    encoded = f"{text}\n".encode("utf-8")
    if len(encoded) > _MAX_AUTHORITY_BYTES:
        raise PlatformVerificationContractError(
            "canonical JSON exceeds maximum authority size"
        )
    return encoded


def _parse_canonical_object(data: bytes, label: str) -> dict[str, object]:
    if not isinstance(data, bytes):
        raise PlatformVerificationContractError(f"{label} must be bytes")
    if len(data) > _MAX_AUTHORITY_BYTES:
        raise PlatformVerificationContractError(
            f"{label} exceeds maximum authority size"
        )
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PlatformVerificationContractError(f"{label} must be UTF-8") from exc

    def reject_duplicates(pairs: Sequence[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise PlatformVerificationContractError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(
                PlatformVerificationContractError(f"invalid JSON number: {token}")
            ),
        )
    except PlatformVerificationContractError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise PlatformVerificationContractError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise PlatformVerificationContractError(f"{label} must be one JSON object")
    if canonical_json_bytes(value) != data:
        raise PlatformVerificationContractError(f"{label} is not canonical JSON")
    return value


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise PlatformVerificationContractError(f"{label} must be an object")
    return value


def _array(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise PlatformVerificationContractError(f"{label} must be an array")
    if len(value) > _MAX_COLLECTION_ITEMS:
        raise PlatformVerificationContractError(f"{label} has too many items")
    return value


def _expect_fields(
    record: Mapping[str, object], expected: set[str], label: str
) -> None:
    actual = set(record)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append(f"missing fields {missing}")
        if unknown:
            details.append(f"unknown fields {unknown}")
        raise PlatformVerificationContractError(f"{label}: {'; '.join(details)}")


def _text(value: object, label: str, *, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value:
        raise PlatformVerificationContractError(f"{label} must be a non-empty string")
    if len(value) > maximum or any(ord(character) < 32 for character in value):
        raise PlatformVerificationContractError(f"{label} is too long or contains control data")
    return value


def _identifier(value: object, label: str) -> str:
    result = _text(value, label, maximum=160)
    if not _ID_PATTERN.fullmatch(result):
        raise PlatformVerificationContractError(f"{label} is invalid")
    return result


def _token(value: object, label: str) -> str:
    result = _text(value, label, maximum=160)
    if not _TOKEN_PATTERN.fullmatch(result):
        raise PlatformVerificationContractError(f"{label} is invalid")
    return result


def _sha256(value: object, label: str) -> str:
    result = _text(value, label, maximum=64)
    if not _SHA256_PATTERN.fullmatch(result):
        raise PlatformVerificationContractError(f"{label} must be a lowercase SHA-256")
    return result


def _revision(value: object, label: str = "source revision") -> str:
    result = _text(value, label, maximum=40)
    if not _REVISION_PATTERN.fullmatch(result):
        raise PlatformVerificationContractError(
            f"{label} must be a lowercase full git revision"
        )
    return result


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise PlatformVerificationContractError(f"{label} must be boolean")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PlatformVerificationContractError(
            f"{label} must be an integer >= {minimum}"
        )
    return value


def _number(value: object, label: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlatformVerificationContractError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise PlatformVerificationContractError(
            f"{label} must be a finite nonnegative number"
        )
    return result


def _timestamp(value: object, label: str) -> str:
    result = _text(value, label, maximum=40)
    if not _UTC_TIMESTAMP_PATTERN.fullmatch(result):
        raise PlatformVerificationContractError(
            f"{label} must be a canonical UTC timestamp ending in Z"
        )
    try:
        datetime.fromisoformat(f"{result[:-1]}+00:00")
    except ValueError as exc:
        raise PlatformVerificationContractError(f"{label} is invalid") from exc
    return result


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(f"{value[:-1]}+00:00")


def _relative_path(value: object, label: str) -> str:
    result = _text(value, label, maximum=1024)
    if "\\" in result or ":" in result:
        raise PlatformVerificationContractError(
            f"{label} must be a canonical relative POSIX path"
        )
    path = PurePosixPath(result)
    if (
        path.is_absolute()
        or not path.parts
        or path.as_posix() != result
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PlatformVerificationContractError(
            f"{label} must be a canonical relative POSIX path"
        )
    return result


REQUIRED_MATRIX_ROWS = (
    MatrixRowV1(
        "ci-ubuntu-22.04-x86_64", "ci", "ubuntu-22.04-x86_64"
    ),
    MatrixRowV1(
        "ci-ubuntu-24.04-x86_64", "ci", "ubuntu-24.04-x86_64"
    ),
    MatrixRowV1(
        "ci-windows-2022-x86_64", "ci", "windows-11-x86_64"
    ),
    MatrixRowV1("ci-macos-15-arm64", "ci", "macos-13-arm64"),
    MatrixRowV1(
        "native-ubuntu-22.04-x86_64", "native", "ubuntu-22.04-x86_64"
    ),
    MatrixRowV1(
        "native-ubuntu-24.04-x86_64", "native", "ubuntu-24.04-x86_64"
    ),
    MatrixRowV1(
        "native-windows-11-x86_64", "native", "windows-11-x86_64"
    ),
    MatrixRowV1("native-macos-13-arm64", "native", "macos-13-arm64"),
)
MATRIX_ROW_BY_ID: Mapping[str, MatrixRowV1] = MappingProxyType(
    {row.matrix_row_id: row for row in REQUIRED_MATRIX_ROWS}
)


@dataclass(frozen=True)
class SourceIdentityV1:
    revision: str
    dirty: bool

    def __post_init__(self) -> None:
        _revision(self.revision)
        _boolean(self.dirty, "source dirty")

    def to_json_object(self) -> dict[str, object]:
        return {"revision": self.revision, "dirty": self.dirty}

    @classmethod
    def from_json_object(cls, value: object) -> "SourceIdentityV1":
        record = _object(value, "source")
        _expect_fields(record, {"revision", "dirty"}, "source")
        return cls(
            revision=_revision(record["revision"]),
            dirty=_boolean(record["dirty"], "source dirty"),
        )


@dataclass(frozen=True)
class ReleaseIdentityV1:
    release_id: str
    manifest_sha256: str
    target_id: str

    def __post_init__(self) -> None:
        _identifier(self.release_id, "release_id")
        _sha256(self.manifest_sha256, "release manifest_sha256")
        if self.target_id not in _TARGET_IDS:
            raise PlatformVerificationContractError("release target_id is unsupported")

    def to_json_object(self) -> dict[str, str]:
        return {
            "release_id": self.release_id,
            "manifest_sha256": self.manifest_sha256,
            "target_id": self.target_id,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "ReleaseIdentityV1":
        record = _object(value, "release")
        _expect_fields(record, {"release_id", "manifest_sha256", "target_id"}, "release")
        return cls(
            release_id=_identifier(record["release_id"], "release_id"),
            manifest_sha256=_sha256(
                record["manifest_sha256"], "release manifest_sha256"
            ),
            target_id=_text(record["target_id"], "release target_id", maximum=64),
        )


@dataclass(frozen=True)
class RuntimeIdentityV1:
    uv: str
    python: str
    qt: str
    pyside: str
    pandas: str
    numpy: str
    lock_sha256: str

    def __post_init__(self) -> None:
        for name in ("uv", "python", "qt", "pyside", "pandas", "numpy"):
            _token(getattr(self, name), f"runtime {name}")
        _sha256(self.lock_sha256, "runtime lock_sha256")

    def to_json_object(self) -> dict[str, str]:
        return {
            "uv": self.uv,
            "python": self.python,
            "qt": self.qt,
            "pyside": self.pyside,
            "pandas": self.pandas,
            "numpy": self.numpy,
            "lock_sha256": self.lock_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "RuntimeIdentityV1":
        record = _object(value, "runtime")
        fields = {"uv", "python", "qt", "pyside", "pandas", "numpy", "lock_sha256"}
        _expect_fields(record, fields, "runtime")
        return cls(
            uv=_token(record["uv"], "runtime uv"),
            python=_token(record["python"], "runtime python"),
            qt=_token(record["qt"], "runtime qt"),
            pyside=_token(record["pyside"], "runtime pyside"),
            pandas=_token(record["pandas"], "runtime pandas"),
            numpy=_token(record["numpy"], "runtime numpy"),
            lock_sha256=_sha256(record["lock_sha256"], "runtime lock_sha256"),
        )


@dataclass(frozen=True)
class PlatformIdentityV1:
    os: str
    version: str
    arch: str
    kernel: str
    runner_label: str
    runner_image: str

    def __post_init__(self) -> None:
        for name in ("os", "version", "arch"):
            _token(getattr(self, name), f"platform {name}")
        for name in ("kernel", "runner_label", "runner_image"):
            _text(getattr(self, name), f"platform {name}", maximum=256)

    def to_json_object(self) -> dict[str, str]:
        return {
            "os": self.os,
            "version": self.version,
            "arch": self.arch,
            "kernel": self.kernel,
            "runner_label": self.runner_label,
            "runner_image": self.runner_image,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "PlatformIdentityV1":
        record = _object(value, "platform")
        fields = {"os", "version", "arch", "kernel", "runner_label", "runner_image"}
        _expect_fields(record, fields, "platform")
        return cls(
            os=_token(record["os"], "platform os"),
            version=_token(record["version"], "platform version"),
            arch=_token(record["arch"], "platform arch"),
            kernel=_text(record["kernel"], "platform kernel", maximum=256),
            runner_label=_text(
                record["runner_label"], "platform runner_label", maximum=256
            ),
            runner_image=_text(
                record["runner_image"], "platform runner_image", maximum=256
            ),
        )


@dataclass(frozen=True)
class DisplayIdentityV1:
    session: str
    scale_percent: int
    logical_viewport: str
    color_scheme: str
    remote: bool

    def __post_init__(self) -> None:
        _token(self.session, "display session")
        if not 50 <= _integer(self.scale_percent, "display scale_percent") <= 400:
            raise PlatformVerificationContractError(
                "display scale_percent must be between 50 and 400"
            )
        if not _VIEWPORT_PATTERN.fullmatch(self.logical_viewport):
            raise PlatformVerificationContractError("display viewport is invalid")
        if self.color_scheme not in {"light", "dark", "system"}:
            raise PlatformVerificationContractError("display color_scheme is invalid")
        _boolean(self.remote, "display remote")

    def to_json_object(self) -> dict[str, object]:
        return {
            "session": self.session,
            "scale_percent": self.scale_percent,
            "logical_viewport": self.logical_viewport,
            "color_scheme": self.color_scheme,
            "remote": self.remote,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "DisplayIdentityV1":
        record = _object(value, "display")
        fields = {"session", "scale_percent", "logical_viewport", "color_scheme", "remote"}
        _expect_fields(record, fields, "display")
        viewport = _text(record["logical_viewport"], "display viewport", maximum=16)
        if not _VIEWPORT_PATTERN.fullmatch(viewport):
            raise PlatformVerificationContractError("display viewport is invalid")
        return cls(
            session=_token(record["session"], "display session"),
            scale_percent=_integer(record["scale_percent"], "display scale_percent"),
            logical_viewport=viewport,
            color_scheme=_text(record["color_scheme"], "display color_scheme", maximum=16),
            remote=_boolean(record["remote"], "display remote"),
        )


@dataclass(frozen=True)
class HardwareIdentityV1:
    cpu: str
    ram_bytes: int
    gpu_or_renderer: str

    def __post_init__(self) -> None:
        _text(self.cpu, "hardware cpu", maximum=256)
        _integer(self.ram_bytes, "hardware ram_bytes")
        _text(self.gpu_or_renderer, "hardware gpu_or_renderer", maximum=256)

    def to_json_object(self) -> dict[str, object]:
        return {
            "cpu": self.cpu,
            "ram_bytes": self.ram_bytes,
            "gpu_or_renderer": self.gpu_or_renderer,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "HardwareIdentityV1":
        record = _object(value, "hardware")
        _expect_fields(record, {"cpu", "ram_bytes", "gpu_or_renderer"}, "hardware")
        return cls(
            cpu=_text(record["cpu"], "hardware cpu", maximum=256),
            ram_bytes=_integer(record["ram_bytes"], "hardware ram_bytes"),
            gpu_or_renderer=_text(
                record["gpu_or_renderer"], "hardware gpu_or_renderer", maximum=256
            ),
        )


@dataclass(frozen=True)
class TestEvidenceV1:
    name: str
    status: str
    duration: float
    report_path: str
    report_sha256: str

    def __post_init__(self) -> None:
        _text(self.name, "test name", maximum=256)
        if self.status not in _TEST_STATUSES:
            raise PlatformVerificationContractError("test status is invalid")
        _number(self.duration, "test duration")
        _relative_path(self.report_path, "test report_path")
        _sha256(self.report_sha256, "test report_sha256")

    def to_json_object(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "duration": self.duration,
            "report_path": self.report_path,
            "report_sha256": self.report_sha256,
        }

    @classmethod
    def from_json_object(cls, value: object, index: int) -> "TestEvidenceV1":
        label = f"tests[{index}]"
        record = _object(value, label)
        fields = {"name", "status", "duration", "report_path", "report_sha256"}
        _expect_fields(record, fields, label)
        duration = _number(record["duration"], f"{label}.duration")
        assert duration is not None
        return cls(
            name=_text(record["name"], f"{label}.name", maximum=256),
            status=_text(record["status"], f"{label}.status", maximum=16),
            duration=duration,
            report_path=_relative_path(record["report_path"], f"{label}.report_path"),
            report_sha256=_sha256(
                record["report_sha256"], f"{label}.report_sha256"
            ),
        )


@dataclass(frozen=True)
class MetricsV1:
    query_p50_ms: float | None
    query_p95_ms: float | None
    peak_rss_bytes: int | None
    max_event_loop_delay_ms: float | None

    def __post_init__(self) -> None:
        _number(self.query_p50_ms, "metrics query_p50_ms", optional=True)
        _number(self.query_p95_ms, "metrics query_p95_ms", optional=True)
        if self.peak_rss_bytes is not None:
            _integer(self.peak_rss_bytes, "metrics peak_rss_bytes")
        _number(
            self.max_event_loop_delay_ms,
            "metrics max_event_loop_delay_ms",
            optional=True,
        )
        if (
            self.query_p50_ms is not None
            and self.query_p95_ms is not None
            and self.query_p95_ms < self.query_p50_ms
        ):
            raise PlatformVerificationContractError(
                "metrics query p95 cannot be below p50"
            )

    def to_json_object(self) -> dict[str, object]:
        return {
            "query_p50_ms": self.query_p50_ms,
            "query_p95_ms": self.query_p95_ms,
            "peak_rss_bytes": self.peak_rss_bytes,
            "max_event_loop_delay_ms": self.max_event_loop_delay_ms,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "MetricsV1":
        record = _object(value, "metrics")
        fields = {
            "query_p50_ms",
            "query_p95_ms",
            "peak_rss_bytes",
            "max_event_loop_delay_ms",
        }
        _expect_fields(record, fields, "metrics")
        peak = record["peak_rss_bytes"]
        return cls(
            query_p50_ms=_number(
                record["query_p50_ms"], "metrics query_p50_ms", optional=True
            ),
            query_p95_ms=_number(
                record["query_p95_ms"], "metrics query_p95_ms", optional=True
            ),
            peak_rss_bytes=(
                None
                if peak is None
                else _integer(peak, "metrics peak_rss_bytes")
            ),
            max_event_loop_delay_ms=_number(
                record["max_event_loop_delay_ms"],
                "metrics max_event_loop_delay_ms",
                optional=True,
            ),
        )


@dataclass(frozen=True)
class EvidenceArtifactV1:
    path: str
    sha256: str
    classification: str

    def __post_init__(self) -> None:
        _relative_path(self.path, "artifact path")
        _sha256(self.sha256, "artifact sha256")
        if self.classification not in _ARTIFACT_CLASSIFICATIONS:
            raise PlatformVerificationContractError(
                "artifact classification is invalid"
            )

    def to_json_object(self) -> dict[str, str]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "classification": self.classification,
        }

    @classmethod
    def from_json_object(cls, value: object, index: int) -> "EvidenceArtifactV1":
        label = f"artifacts[{index}]"
        record = _object(value, label)
        _expect_fields(record, {"path", "sha256", "classification"}, label)
        return cls(
            path=_relative_path(record["path"], f"{label}.path"),
            sha256=_sha256(record["sha256"], f"{label}.sha256"),
            classification=_text(
                record["classification"], f"{label}.classification", maximum=32
            ),
        )


@dataclass(frozen=True)
class VerificationRunV1:
    schema_version: int
    run_id: str
    matrix_row_id: str
    status: str
    reason_code: str | None
    started_at_utc: str
    completed_at_utc: str
    source: SourceIdentityV1
    release: ReleaseIdentityV1
    runtime: RuntimeIdentityV1
    platform: PlatformIdentityV1
    display: DisplayIdentityV1
    hardware: HardwareIdentityV1
    command_or_checklist_version: str
    tests: tuple[TestEvidenceV1, ...]
    metrics: MetricsV1
    artifacts: tuple[EvidenceArtifactV1, ...]
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise PlatformVerificationContractError(
                "VerificationRunV1 schema_version must be 1"
            )
        _identifier(self.run_id, "run_id")
        row = MATRIX_ROW_BY_ID.get(self.matrix_row_id)
        if row is None:
            raise PlatformVerificationContractError("matrix_row_id is unknown")
        if self.status not in {"PASS", *tuple(_STATUS_REASONS)}:
            raise PlatformVerificationContractError("verification status is invalid")
        if self.status == "PASS":
            if self.reason_code is not None:
                raise PlatformVerificationContractError(
                    "PASS status requires a null reason_code"
                )
        else:
            if self.reason_code is None:
                raise PlatformVerificationContractError(
                    f"{self.status} status requires a reason_code"
                )
            if self.reason_code not in _STATUS_REASONS[self.status]:
                raise PlatformVerificationContractError(
                    f"reason_code is not valid for {self.status}"
                )
        _timestamp(self.started_at_utc, "started_at_utc")
        _timestamp(self.completed_at_utc, "completed_at_utc")
        if _timestamp_value(self.completed_at_utc) < _timestamp_value(
            self.started_at_utc
        ):
            raise PlatformVerificationContractError(
                "completed_at_utc cannot precede start"
            )
        if self.release.target_id != row.target_id:
            raise PlatformVerificationContractError(
                "release target does not match matrix row target"
            )
        _token(
            self.command_or_checklist_version,
            "command_or_checklist_version",
        )
        if len({test.name for test in self.tests}) != len(self.tests):
            raise PlatformVerificationContractError("duplicate test name")
        if len({artifact.path for artifact in self.artifacts}) != len(self.artifacts):
            raise PlatformVerificationContractError("duplicate artifact path")
        artifact_identities = {
            (artifact.path, artifact.sha256) for artifact in self.artifacts
        }
        for test in self.tests:
            if (test.report_path, test.report_sha256) not in artifact_identities:
                raise PlatformVerificationContractError(
                    "test report identity is absent from artifacts"
                )
        if self.status == "PASS":
            if not self.tests or any(test.status != "PASS" for test in self.tests):
                raise PlatformVerificationContractError(
                    "PASS verification requires all recorded tests to PASS"
                )
            if not any(
                artifact.classification != "screenshot"
                for artifact in self.artifacts
            ):
                raise PlatformVerificationContractError(
                    "PASS verification requires non-screenshot evidence"
                )
            if self.hardware.ram_bytes <= 0:
                raise PlatformVerificationContractError(
                    "PASS verification requires known hardware ram_bytes"
                )
        elif not self.limitations:
            raise PlatformVerificationContractError(
                "non-PASS verification requires a limitation"
            )
        for limitation in self.limitations:
            _text(limitation, "limitation", maximum=1024)

    @property
    def evidence_class(self) -> str:
        return MATRIX_ROW_BY_ID[self.matrix_row_id].evidence_class

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_object())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "matrix_row_id": self.matrix_row_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "started_at_utc": self.started_at_utc,
            "completed_at_utc": self.completed_at_utc,
            "source": self.source.to_json_object(),
            "release": self.release.to_json_object(),
            "runtime": self.runtime.to_json_object(),
            "platform": self.platform.to_json_object(),
            "display": self.display.to_json_object(),
            "hardware": self.hardware.to_json_object(),
            "command_or_checklist_version": self.command_or_checklist_version,
            "tests": [test.to_json_object() for test in self.tests],
            "metrics": self.metrics.to_json_object(),
            "artifacts": [artifact.to_json_object() for artifact in self.artifacts],
            "limitations": list(self.limitations),
        }

    @classmethod
    def from_json_object(cls, value: object) -> "VerificationRunV1":
        record = _object(value, "VerificationRunV1")
        fields = {
            "schema_version",
            "run_id",
            "matrix_row_id",
            "status",
            "reason_code",
            "started_at_utc",
            "completed_at_utc",
            "source",
            "release",
            "runtime",
            "platform",
            "display",
            "hardware",
            "command_or_checklist_version",
            "tests",
            "metrics",
            "artifacts",
            "limitations",
        }
        _expect_fields(record, fields, "VerificationRunV1")
        reason = record["reason_code"]
        if reason is not None:
            reason = _text(reason, "reason_code", maximum=64)
        return cls(
            schema_version=_integer(
                record["schema_version"], "schema_version", minimum=1
            ),
            run_id=_identifier(record["run_id"], "run_id"),
            matrix_row_id=_identifier(record["matrix_row_id"], "matrix_row_id"),
            status=_text(record["status"], "status", maximum=16),
            reason_code=reason,  # type: ignore[arg-type]
            started_at_utc=_timestamp(record["started_at_utc"], "started_at_utc"),
            completed_at_utc=_timestamp(
                record["completed_at_utc"], "completed_at_utc"
            ),
            source=SourceIdentityV1.from_json_object(record["source"]),
            release=ReleaseIdentityV1.from_json_object(record["release"]),
            runtime=RuntimeIdentityV1.from_json_object(record["runtime"]),
            platform=PlatformIdentityV1.from_json_object(record["platform"]),
            display=DisplayIdentityV1.from_json_object(record["display"]),
            hardware=HardwareIdentityV1.from_json_object(record["hardware"]),
            command_or_checklist_version=_token(
                record["command_or_checklist_version"],
                "command_or_checklist_version",
            ),
            tests=tuple(
                TestEvidenceV1.from_json_object(item, index)
                for index, item in enumerate(_array(record["tests"], "tests"))
            ),
            metrics=MetricsV1.from_json_object(record["metrics"]),
            artifacts=tuple(
                EvidenceArtifactV1.from_json_object(item, index)
                for index, item in enumerate(
                    _array(record["artifacts"], "artifacts")
                )
            ),
            limitations=tuple(
                _text(item, "limitation", maximum=1024)
                for item in _array(record["limitations"], "limitations")
            ),
        )

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> "VerificationRunV1":
        result = cls.from_json_object(
            _parse_canonical_object(data, "VerificationRunV1")
        )
        if result.canonical_bytes != data:
            raise PlatformVerificationContractError(
                "VerificationRunV1 is not semantically canonical JSON"
            )
        return result


@dataclass(frozen=True)
class ReleaseGateRequestV1:
    schema_version: int
    source: SourceIdentityV1
    release_id: str
    manifest_sha256: str
    runtime: RuntimeIdentityV1
    command_or_checklist_version: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise PlatformVerificationContractError(
                "ReleaseGateRequestV1 schema_version must be 1"
            )
        if self.source.dirty:
            raise PlatformVerificationContractError(
                "release gate requires a clean source dirty state"
            )
        _identifier(self.release_id, "release_id")
        _sha256(self.manifest_sha256, "manifest_sha256")
        _token(
            self.command_or_checklist_version,
            "command_or_checklist_version",
        )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_object())

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source": self.source.to_json_object(),
            "release_id": self.release_id,
            "manifest_sha256": self.manifest_sha256,
            "runtime": self.runtime.to_json_object(),
            "command_or_checklist_version": self.command_or_checklist_version,
        }

    @classmethod
    def from_json_object(cls, value: object) -> "ReleaseGateRequestV1":
        record = _object(value, "ReleaseGateRequestV1")
        fields = {
            "schema_version",
            "source",
            "release_id",
            "manifest_sha256",
            "runtime",
            "command_or_checklist_version",
        }
        _expect_fields(record, fields, "ReleaseGateRequestV1")
        return cls(
            schema_version=_integer(
                record["schema_version"], "schema_version", minimum=1
            ),
            source=SourceIdentityV1.from_json_object(record["source"]),
            release_id=_identifier(record["release_id"], "release_id"),
            manifest_sha256=_sha256(record["manifest_sha256"], "manifest_sha256"),
            runtime=RuntimeIdentityV1.from_json_object(record["runtime"]),
            command_or_checklist_version=_token(
                record["command_or_checklist_version"],
                "command_or_checklist_version",
            ),
        )

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> "ReleaseGateRequestV1":
        result = cls.from_json_object(
            _parse_canonical_object(data, "ReleaseGateRequestV1")
        )
        if result.canonical_bytes != data:
            raise PlatformVerificationContractError(
                "ReleaseGateRequestV1 is not semantically canonical JSON"
            )
        return result


@dataclass(frozen=True)
class ReleaseBlockerV1:
    matrix_row_id: str
    status: str
    reason_code: str
    run_id: str | None

    def __post_init__(self) -> None:
        if self.matrix_row_id not in MATRIX_ROW_BY_ID:
            raise PlatformVerificationContractError("blocker matrix_row_id is unknown")
        if self.status == "MISSING":
            if self.reason_code != "missing-required-row" or self.run_id is not None:
                raise PlatformVerificationContractError(
                    "MISSING blocker requires missing-required-row and null run_id"
                )
            return
        if self.status not in _STATUS_REASONS:
            raise PlatformVerificationContractError("blocker status is invalid")
        if self.reason_code not in _STATUS_REASONS[self.status]:
            raise PlatformVerificationContractError(
                "blocker reason_code does not match status"
            )
        if self.run_id is None:
            raise PlatformVerificationContractError(
                "non-missing blocker requires run_id"
            )
        _identifier(self.run_id, "blocker run_id")

    def to_json_object(self) -> dict[str, object]:
        return {
            "matrix_row_id": self.matrix_row_id,
            "status": self.status,
            "reason_code": self.reason_code,
            "run_id": self.run_id,
        }

    @classmethod
    def from_json_object(cls, value: object, index: int) -> "ReleaseBlockerV1":
        label = f"blockers[{index}]"
        record = _object(value, label)
        _expect_fields(
            record, {"matrix_row_id", "status", "reason_code", "run_id"}, label
        )
        run_id = record["run_id"]
        if run_id is not None:
            run_id = _identifier(run_id, f"{label}.run_id")
        return cls(
            matrix_row_id=_identifier(
                record["matrix_row_id"], f"{label}.matrix_row_id"
            ),
            status=_text(record["status"], f"{label}.status", maximum=16),
            reason_code=_text(
                record["reason_code"], f"{label}.reason_code", maximum=64
            ),
            run_id=run_id,  # type: ignore[arg-type]
        )


@dataclass(frozen=True)
class ReleaseDecisionV1:
    schema_version: int
    decision: str
    source_revision: str
    release_id: str
    manifest_sha256: str
    evaluated_run_ids: tuple[str, ...]
    blockers: tuple[ReleaseBlockerV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise PlatformVerificationContractError(
                "ReleaseDecisionV1 schema_version must be 1"
            )
        if self.decision not in {"GO", "NO-GO"}:
            raise PlatformVerificationContractError("release decision is invalid")
        _revision(self.source_revision, "source_revision")
        _identifier(self.release_id, "release_id")
        _sha256(self.manifest_sha256, "manifest_sha256")
        for run_id in self.evaluated_run_ids:
            _identifier(run_id, "evaluated run_id")
        if len(set(self.evaluated_run_ids)) != len(self.evaluated_run_ids):
            raise PlatformVerificationContractError("duplicate evaluated run_id")
        blocker_ids = [blocker.matrix_row_id for blocker in self.blockers]
        if blocker_ids != sorted(blocker_ids):
            raise PlatformVerificationContractError(
                "release decision blockers must be sorted by matrix_row_id"
            )
        if len(set(blocker_ids)) != len(blocker_ids):
            raise PlatformVerificationContractError("duplicate blocker matrix row")
        if self.decision == "GO" and self.blockers:
            raise PlatformVerificationContractError("GO decision cannot contain blockers")
        if self.decision == "NO-GO" and not self.blockers:
            raise PlatformVerificationContractError("NO-GO decision requires blockers")

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json_object())

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes).hexdigest()

    def to_json_object(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "decision": self.decision,
            "source_revision": self.source_revision,
            "release_id": self.release_id,
            "manifest_sha256": self.manifest_sha256,
            "evaluated_run_ids": list(self.evaluated_run_ids),
            "blockers": [blocker.to_json_object() for blocker in self.blockers],
        }

    @classmethod
    def from_json_object(cls, value: object) -> "ReleaseDecisionV1":
        record = _object(value, "ReleaseDecisionV1")
        fields = {
            "schema_version",
            "decision",
            "source_revision",
            "release_id",
            "manifest_sha256",
            "evaluated_run_ids",
            "blockers",
        }
        _expect_fields(record, fields, "ReleaseDecisionV1")
        return cls(
            schema_version=_integer(
                record["schema_version"], "schema_version", minimum=1
            ),
            decision=_text(record["decision"], "decision", maximum=16),
            source_revision=_revision(record["source_revision"], "source_revision"),
            release_id=_identifier(record["release_id"], "release_id"),
            manifest_sha256=_sha256(record["manifest_sha256"], "manifest_sha256"),
            evaluated_run_ids=tuple(
                _identifier(item, "evaluated run_id")
                for item in _array(
                    record["evaluated_run_ids"], "evaluated_run_ids"
                )
            ),
            blockers=tuple(
                ReleaseBlockerV1.from_json_object(item, index)
                for index, item in enumerate(
                    _array(record["blockers"], "blockers")
                )
            ),
        )

    @classmethod
    def from_canonical_bytes(cls, data: bytes) -> "ReleaseDecisionV1":
        result = cls.from_json_object(
            _parse_canonical_object(data, "ReleaseDecisionV1")
        )
        if result.canonical_bytes != data:
            raise PlatformVerificationContractError(
                "ReleaseDecisionV1 is not semantically canonical JSON"
            )
        return result


__all__ = [
    "EvidenceArtifactV1",
    "MATRIX_ROW_BY_ID",
    "MatrixRowV1",
    "MetricsV1",
    "PlatformVerificationContractError",
    "REQUIRED_MATRIX_ROWS",
    "ReleaseBlockerV1",
    "ReleaseDecisionV1",
    "ReleaseGateRequestV1",
    "ReleaseIdentityV1",
    "RuntimeIdentityV1",
    "SourceIdentityV1",
    "TestEvidenceV1",
    "VerificationRunV1",
    "canonical_json_bytes",
]
