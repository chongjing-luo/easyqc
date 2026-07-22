from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pytest

from core.managed_runtime import (
    ManagedRuntimeError,
    MaterializationRequest,
    MaterializationResult,
    launch_active,
    read_activation_pointer,
    verify_artifact_set,
)
from core.managed_runtime_lock import acquire_transaction_lease
from core.managed_runtime_platform import (
    HostPreflightSnapshot,
    resolve_runtime_paths,
)
import core.managed_runtime_transaction as transaction
from core.managed_runtime_transaction import (
    RuntimeRollbackRequest,
    RuntimeSmokeCommandResult,
    RuntimeTransactionController,
    RuntimeTransactionRequest,
)
from models.managed_runtime import ActivationPointerV1, ReleaseManifestV1


MIB = 1024 * 1024
TARGET_ID = "ubuntu-22.04-x86_64"
SOURCE_REVISION = "a" * 40
FIXED_TIME = datetime(2026, 7, 22, 5, 0, tzinfo=timezone.utc)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_artifacts(root: Path, version: str) -> ReleaseManifestV1:
    root.mkdir(parents=True)
    payloads = {
        "uv": b"pinned uv",
        "python.tar.gz": b"exact Python",
        "easyqc-source.tar.gz": f"source {version}".encode(),
        "requirements.lock": b"dependency==1 --hash=sha256:" + b"1" * 64,
        "dependency-1-py3-none-any.whl": b"wheel",
    }
    for filename, data in payloads.items():
        (root / filename).write_bytes(data)

    def artifact(kind: str, filename: str) -> dict[str, object]:
        data = payloads[filename]
        return {
            "kind": kind,
            "filename": filename,
            "size_bytes": len(data),
            "sha256": _sha256(data),
        }

    return ReleaseManifestV1.from_json_object(
        {
            "schema_version": 1,
            "release_id": f"easyqc-{version}-linux",
            "channel": "candidate",
            "target": {
                "os": "linux",
                "os_minimum": "22.04",
                "arch": "x86_64",
            },
            "easyqc": {
                "version": version,
                "source_filename": "easyqc-source.tar.gz",
                "source_sha256": _sha256(payloads["easyqc-source.tar.gz"]),
                "entrypoint": "easyqc_version.py",
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
            ],
            "sources": [],
            "notices": [],
            "expanded_version_bytes": 4096,
            "required_free_bytes": 256 * MIB + 4096,
            "smoke_contract_version": 1,
        }
    )


def _passing_snapshot() -> HostPreflightSnapshot:
    return HostPreflightSnapshot(
        os_name="linux",
        os_version="22.04",
        distribution_id="ubuntu",
        arch="x86_64",
        available_free_bytes=2 * 1024 * MIB,
        root_writable=True,
        privileged=False,
        root_is_safe=True,
        available_native_libraries=("libxcb-cursor.so.0",),
    )


def _request(
    install_root: Path,
    artifact_root: Path,
    manifest: ReleaseManifestV1,
    *,
    snapshot: HostPreflightSnapshot | None = None,
) -> RuntimeTransactionRequest:
    paths = resolve_runtime_paths(
        manifest.target,
        "user",
        user_data_root=str(install_root),
    )
    verified = verify_artifact_set(
        manifest,
        artifact_root,
        expected_target_id=TARGET_ID,
    )
    return RuntimeTransactionRequest(
        paths=paths,
        manifest=manifest,
        verified_artifacts=verified,
        source_revision=SOURCE_REVISION,
        preflight_snapshot=snapshot or _passing_snapshot(),
        smoke_timeout_seconds=10.0,
    )


class FakeFinalPathAdapter:
    def __init__(self, *, fail_call: int | None = None) -> None:
        self.fail_call = fail_call
        self.calls: list[MaterializationRequest] = []

    def materialize(self, request: MaterializationRequest) -> MaterializationResult:
        self.calls.append(request)
        if len(self.calls) == self.fail_call:
            raise ManagedRuntimeError("injected materialization failure")
        assert request.version_root.name == request.manifest.release_id
        assert not any(request.version_root.iterdir())
        runtime_python = request.version_root / "runtime/python"
        runtime_python.parent.mkdir()
        shutil.copy2(Path(sys.executable).resolve(), runtime_python)
        environment_python = request.version_root / "env/bin/python"
        environment_python.parent.mkdir(parents=True)
        environment_python.symlink_to(runtime_python)
        entrypoint = (
            request.version_root
            / "app"
            / request.manifest.easyqc.entrypoint
        )
        entrypoint.parent.mkdir()
        entrypoint.write_text(
            "import sys\n"
            f"print({request.manifest.easyqc.version!r})\n",
            encoding="utf-8",
        )
        return MaterializationResult(environment_python, entrypoint)


class QueueSmokeRunner:
    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[Path, Path, str, float]] = []

    def run(
        self,
        python_executable: Path,
        entrypoint: Path,
        expected_version: str,
        timeout_seconds: float,
    ) -> tuple[RuntimeSmokeCommandResult, ...]:
        self.calls.append(
            (
                python_executable,
                entrypoint,
                expected_version,
                timeout_seconds,
            )
        )
        passed = self.outcomes.pop(0)
        version = RuntimeSmokeCommandResult(
            name="version",
            argv=(str(python_executable), str(entrypoint), "--version"),
            returncode=0 if passed else 23,
            stdout=f"{expected_version}\n" if passed else "",
            stderr="" if passed else "injected failure",
        )
        qt = RuntimeSmokeCommandResult(
            name="qt-offscreen",
            argv=(str(python_executable), "-c", "<fixed-qt-smoke>"),
            returncode=0 if passed else 23,
            stdout="QT_OFFSCREEN_OK 6.11.1\n" if passed else "",
            stderr="" if passed else "injected failure",
        )
        return (version, qt)


class SequenceIds:
    def __init__(self, *values: str) -> None:
        self._values = iter(values)

    def __call__(self) -> str:
        return next(self._values)


def _controller(
    ids: SequenceIds,
    outcomes: list[bool],
    *,
    adapter: FakeFinalPathAdapter | None = None,
) -> tuple[RuntimeTransactionController, FakeFinalPathAdapter, QueueSmokeRunner]:
    active_adapter = adapter or FakeFinalPathAdapter()
    smoke_runner = QueueSmokeRunner(outcomes)
    controller = RuntimeTransactionController(
        adapter=active_adapter,
        smoke_runner=smoke_runner,
        transaction_id_factory=ids,
        clock=lambda: FIXED_TIME,
    )
    return controller, active_adapter, smoke_runner


def _load_report(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_install_creates_final_receipt_pointer_log_and_fresh_launch(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "候选 root with spaces"
    artifacts = tmp_path / "payload"
    manifest = _write_artifacts(artifacts, "1.0.0")
    request = _request(install_root, artifacts, manifest)
    controller, adapter, smoke = _controller(
        SequenceIds("1" * 32),
        [True],
    )

    result = controller.install(request)

    assert result.operation == "install"
    assert result.version_root == install_root / "versions" / manifest.release_id
    assert result.receipt.release_id == manifest.release_id
    assert result.receipt.controller_version == "s6-rt-03"
    assert result.pointer == ActivationPointerV1(manifest.release_id, None, 1)
    assert result.quarantined_root is None
    assert adapter.calls[0].version_root == result.version_root
    assert len(smoke.calls) == 1
    assert result.report_path == (
        install_root / "state" / "logs" / f"transaction-{'1' * 32}.json"
    )
    report = _load_report(result.report_path)
    assert report["operation"] == "install"
    assert report["phase"] == "complete"
    assert report["result"] == "passed"
    assert report["pointer_committed"] is True
    assert "stdout" not in report and "stderr" not in report
    assert launch_active(install_root, ("--version",)).stdout.strip() == "1.0.0"


def test_failed_update_preserves_pointer_and_repair_retains_failed_version(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "候选 root with spaces"
    artifacts_v1 = tmp_path / "payload-v1"
    artifacts_v2 = tmp_path / "payload-v2"
    manifest_v1 = _write_artifacts(artifacts_v1, "1.0.0")
    manifest_v2 = _write_artifacts(artifacts_v2, "2.0.0")
    request_v1 = _request(install_root, artifacts_v1, manifest_v1)
    request_v2 = _request(install_root, artifacts_v2, manifest_v2)
    controller, adapter, _smoke = _controller(
        SequenceIds("a" * 32, "b" * 32, "c" * 32),
        [True, False, True],
    )
    controller.install(request_v1)
    pointer_path = install_root / "state/activation.txt"
    original_pointer = pointer_path.read_bytes()

    with pytest.raises(ManagedRuntimeError, match="update.*smoke.*log="):
        controller.update(request_v2)

    assert pointer_path.read_bytes() == original_pointer
    incomplete = install_root / "versions" / manifest_v2.release_id
    assert incomplete.is_dir()
    assert (incomplete / "smoke-report.json").is_file()
    assert not (incomplete / "install-receipt.json").exists()
    failed_report = _load_report(
        install_root / f"state/logs/transaction-{'b' * 32}.json"
    )
    assert failed_report["operation"] == "update"
    assert failed_report["phase"] == "smoke"
    assert failed_report["result"] == "failed"
    assert failed_report["pointer_committed"] is False

    repaired = controller.repair(request_v2)

    expected_quarantine = (
        install_root
        / "versions"
        / f".staging-{manifest_v2.release_id}-failed-{'c' * 32}"
    )
    assert repaired.operation == "repair"
    assert repaired.quarantined_root == expected_quarantine
    assert expected_quarantine.is_dir()
    assert repaired.version_root.is_dir()
    assert repaired.pointer == ActivationPointerV1(
        manifest_v2.release_id,
        manifest_v1.release_id,
        2,
    )
    assert len(adapter.calls) == 3


def test_passing_update_and_rollback_share_lease_without_adapter_call(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "candidate"
    artifacts_v1 = tmp_path / "payload-v1"
    artifacts_v2 = tmp_path / "payload-v2"
    manifest_v1 = _write_artifacts(artifacts_v1, "1.0.0")
    manifest_v2 = _write_artifacts(artifacts_v2, "2.0.0")
    request_v1 = _request(install_root, artifacts_v1, manifest_v1)
    request_v2 = _request(install_root, artifacts_v2, manifest_v2)
    controller, adapter, _smoke = _controller(
        SequenceIds("d" * 32, "e" * 32, "f" * 32),
        [True, True],
    )

    controller.install(request_v1)
    updated = controller.update(request_v2)
    rolled_back = controller.rollback(
        RuntimeRollbackRequest(request_v1.paths, SOURCE_REVISION)
    )

    assert updated.pointer == ActivationPointerV1(
        manifest_v2.release_id,
        manifest_v1.release_id,
        2,
    )
    assert rolled_back.operation == "rollback"
    assert rolled_back.pointer == ActivationPointerV1(
        manifest_v1.release_id,
        manifest_v2.release_id,
        3,
    )
    assert rolled_back.receipt.release_id == manifest_v1.release_id
    assert len(adapter.calls) == 2
    assert launch_active(install_root, ("--version",)).stdout.strip() == "1.0.0"


def test_preflight_contention_same_release_and_active_repair_fail_closed(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "payload"
    manifest = _write_artifacts(artifacts, "1.0.0")
    bad_snapshot = replace(
        _passing_snapshot(),
        available_native_libraries=(),
    )
    bad_request = _request(
        tmp_path / "bad-preflight",
        artifacts,
        manifest,
        snapshot=bad_snapshot,
    )
    controller, _adapter, _smoke = _controller(
        SequenceIds("2" * 32),
        [],
    )

    with pytest.raises(ManagedRuntimeError, match="preflight.*log="):
        controller.install(bad_request)
    assert not (Path(bad_request.paths.install_root) / "versions").exists()

    contention_request = _request(
        tmp_path / "contended",
        artifacts,
        manifest,
    )
    with acquire_transaction_lease(contention_request.paths):
        with pytest.raises(ManagedRuntimeError, match="lease.*held"):
            controller.install(contention_request)

    active_request = _request(tmp_path / "active", artifacts, manifest)
    active_controller, adapter, _ = _controller(
        SequenceIds("3" * 32, "4" * 32, "5" * 32),
        [True],
    )
    active_controller.install(active_request)
    with pytest.raises(ManagedRuntimeError, match="update.*already active"):
        active_controller.update(active_request)
    with pytest.raises(ManagedRuntimeError, match="repair.*active"):
        active_controller.repair(active_request)
    assert len(adapter.calls) == 1


def test_activation_failure_preserves_pointer_and_retry_reuses_valid_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "candidate"
    artifacts_v1 = tmp_path / "payload-v1"
    artifacts_v2 = tmp_path / "payload-v2"
    manifest_v1 = _write_artifacts(artifacts_v1, "1.0.0")
    manifest_v2 = _write_artifacts(artifacts_v2, "2.0.0")
    request_v1 = _request(install_root, artifacts_v1, manifest_v1)
    request_v2 = _request(install_root, artifacts_v2, manifest_v2)
    controller, adapter, _smoke = _controller(
        SequenceIds("6" * 32, "7" * 32, "8" * 32),
        [True, True],
    )
    controller.install(request_v1)
    pointer_path = install_root / "state/activation.txt"
    original_pointer = pointer_path.read_bytes()
    original_activate = transaction.activate_release

    def fail_activation(_install_root: Path, _release_id: str):
        raise ManagedRuntimeError("injected activation failure")

    monkeypatch.setattr(transaction, "activate_release", fail_activation)
    with pytest.raises(ManagedRuntimeError, match="activate.*log="):
        controller.update(request_v2)

    assert pointer_path.read_bytes() == original_pointer
    completed_v2 = install_root / "versions" / manifest_v2.release_id
    assert (completed_v2 / "install-receipt.json").is_file()
    monkeypatch.setattr(transaction, "activate_release", original_activate)

    retried = controller.update(request_v2)

    assert retried.pointer.active_release_id == manifest_v2.release_id
    assert len(adapter.calls) == 2


def test_invalid_source_revision_and_initial_report_failure_write_no_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "payload"
    manifest = _write_artifacts(artifacts, "1.0.0")
    request = _request(tmp_path / "candidate", artifacts, manifest)
    with pytest.raises(ManagedRuntimeError, match="source revision"):
        replace(request, source_revision="not-a-revision")

    controller, _adapter, _smoke = _controller(
        SequenceIds("9" * 32),
        [],
    )

    def fail_report(*_args: object, **_kwargs: object) -> None:
        raise OSError("injected report failure")

    monkeypatch.setattr(transaction.FileUtils, "atomic_write", fail_report)
    with pytest.raises(ManagedRuntimeError, match="transaction report"):
        controller.install(request)

    assert not (Path(request.paths.install_root) / "versions").exists()


def test_materialization_failure_retains_no_receipt_or_pointer(
    tmp_path: Path,
) -> None:
    artifacts = tmp_path / "payload"
    manifest = _write_artifacts(artifacts, "1.0.0")
    request = _request(tmp_path / "candidate", artifacts, manifest)
    controller, _adapter, _smoke = _controller(
        SequenceIds("a" * 32),
        [],
        adapter=FakeFinalPathAdapter(fail_call=1),
    )

    with pytest.raises(ManagedRuntimeError, match="materialize.*log="):
        controller.install(request)

    final_root = (
        Path(request.paths.install_root) / "versions" / manifest.release_id
    )
    assert final_root.is_dir()
    assert not (final_root / "install-receipt.json").exists()
    assert not (Path(request.paths.state_root) / "activation.txt").exists()
    report = _load_report(
        Path(request.paths.state_root)
        / "logs"
        / f"transaction-{'a' * 32}.json"
    )
    assert report["phase"] == "materialize"
    assert report["pointer_committed"] is False


def test_transaction_report_id_collision_is_immutable(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "candidate"
    artifacts = tmp_path / "payload"
    manifest = _write_artifacts(artifacts, "1.0.0")
    failing = _request(
        install_root,
        artifacts,
        manifest,
        snapshot=replace(
            _passing_snapshot(),
            available_native_libraries=(),
        ),
    )
    passing = _request(install_root, artifacts, manifest)
    controller, adapter, _smoke = _controller(
        SequenceIds("b" * 32, "b" * 32),
        [True],
    )
    with pytest.raises(ManagedRuntimeError, match="preflight"):
        controller.install(failing)
    report_path = (
        install_root / "state" / "logs" / f"transaction-{'b' * 32}.json"
    )
    original_report = report_path.read_bytes()

    with pytest.raises(ManagedRuntimeError, match="report collision"):
        controller.install(passing)

    assert report_path.read_bytes() == original_report
    assert not (install_root / "versions").exists()
    assert adapter.calls == []
