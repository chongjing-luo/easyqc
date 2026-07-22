#!/usr/bin/env python3
"""Run the isolated S6-RT-03 real managed-runtime transaction campaign."""

from __future__ import annotations

import argparse
from dataclasses import replace
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile


PRODUCT_ROOT = Path(__file__).resolve().parents[1]
if str(PRODUCT_ROOT) not in sys.path:
    sys.path.insert(0, str(PRODUCT_ROOT))

from core.managed_runtime import (  # noqa: E402
    ManagedRuntimeError,
    launch_active,
    read_activation_pointer,
    verify_artifact_set,
)
from core.managed_runtime_platform import (  # noqa: E402
    inspect_host,
    resolve_runtime_paths,
)
from core.managed_runtime_transaction import (  # noqa: E402
    RuntimeRollbackRequest,
    RuntimeSmokeCommandResult,
    RuntimeTransactionController,
    RuntimeTransactionRequest,
    SubprocessRuntimeSmokeRunner,
)
from core.managed_runtime_uv import RealUvAdapter  # noqa: E402
from models.managed_runtime import ReleaseManifestV1  # noqa: E402
from utils.file_utils import FileUtils  # noqa: E402


VERSIONS = ("1.0.0", "2.0.0", "3.0.0")
RELEASE_PREFIX = "easyqc-s6-rt-03-real"


class FailSecondSmoke:
    """Run every real command, then inject one deterministic v2 mismatch."""

    def __init__(self) -> None:
        self._delegate = SubprocessRuntimeSmokeRunner()
        self.calls = 0

    def run(
        self,
        python_executable: Path,
        entrypoint: Path,
        expected_version: str,
        timeout_seconds: float,
    ) -> tuple[RuntimeSmokeCommandResult, ...]:
        self.calls += 1
        results = self._delegate.run(
            python_executable,
            entrypoint,
            expected_version,
            timeout_seconds,
        )
        if self.calls != 2:
            return results
        version, qt = results
        return (
            replace(version, stdout="INJECTED_S6_RT_03_VERSION_MISMATCH\n"),
            qt,
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_source_archive(path: Path, version: str) -> None:
    source = (
        "import sys\n"
        f"VERSION = {version!r}\n"
        "if '--version' in sys.argv:\n"
        "    print(VERSION)\n"
        "    raise SystemExit(0)\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication.instance() or QApplication([])\n"
    ).encode("utf-8")
    info = tarfile.TarInfo("easyqc.py")
    info.size = len(source)
    info.mode = 0o644
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w") as archive:
                from io import BytesIO

                archive.addfile(info, BytesIO(source))


def _link_payload(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _prepare_release(
    fixture_root: Path,
    work_root: Path,
    template: dict[str, object],
    version: str,
) -> tuple[ReleaseManifestV1, Path]:
    payload_root = work_root / f"payload-{version}"
    payload_root.mkdir()
    fixture_payload = fixture_root / "payload"
    for source in fixture_payload.iterdir():
        if source.name != "easyqc-source.tar.gz":
            _link_payload(source, payload_root / source.name)
    source_archive = payload_root / "easyqc-source.tar.gz"
    _write_source_archive(source_archive, version)
    source_size = source_archive.stat().st_size
    source_hash = _sha256(source_archive)

    record = json.loads(json.dumps(template))
    record["release_id"] = f"{RELEASE_PREFIX}-{version}-linux"
    easyqc = record["easyqc"]
    if not isinstance(easyqc, dict):
        raise RuntimeError("fixture manifest has invalid easyqc identity")
    easyqc["version"] = version
    easyqc["source_sha256"] = source_hash
    artifacts = record["artifacts"]
    if not isinstance(artifacts, list):
        raise RuntimeError("fixture manifest has invalid artifacts")
    source_rows = [
        row
        for row in artifacts
        if isinstance(row, dict)
        and row.get("filename") == "easyqc-source.tar.gz"
    ]
    if len(source_rows) != 1:
        raise RuntimeError("fixture manifest has no unique source artifact")
    source_rows[0]["size_bytes"] = source_size
    source_rows[0]["sha256"] = source_hash
    manifest = ReleaseManifestV1.from_json_object(record)
    FileUtils.atomic_write(
        payload_root / "release-manifest.json",
        manifest.canonical_bytes.decode("utf-8"),
    )
    return manifest, payload_root


def _request(
    install_root: Path,
    manifest: ReleaseManifestV1,
    payload_root: Path,
    source_revision: str,
) -> RuntimeTransactionRequest:
    paths = resolve_runtime_paths(
        manifest.target,
        "user",
        user_data_root=str(install_root),
    )
    return RuntimeTransactionRequest(
        paths=paths,
        manifest=manifest,
        verified_artifacts=verify_artifact_set(
            manifest,
            payload_root,
            expected_target_id=manifest.target.target_id,
        ),
        source_revision=source_revision,
        preflight_snapshot=inspect_host(paths),
        smoke_timeout_seconds=60.0,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    fixture_root = args.fixture_root.resolve(strict=True)
    work_root = args.work_root.resolve(strict=False)
    if not work_root.is_absolute() or work_root.exists() or work_root.is_symlink():
        raise ManagedRuntimeError(
            "probe work root must be an absent explicit absolute path"
        )
    manifest_path = fixture_root / "release-manifest.json"
    payload_root = fixture_root / "payload"
    if not manifest_path.is_file() or not payload_root.is_dir():
        raise ManagedRuntimeError("probe fixture is incomplete")
    work_root.mkdir(mode=0o700)
    template = json.loads(manifest_path.read_text(encoding="utf-8"))
    prepared = [
        _prepare_release(fixture_root, work_root, template, version)
        for version in VERSIONS
    ]
    install_root = work_root / "candidate root with spaces"
    requests = [
        _request(
            install_root,
            manifest,
            release_payload,
            args.source_revision,
        )
        for manifest, release_payload in prepared
    ]
    ids = iter(f"{index:032x}" for index in range(1, 7))
    smoke = FailSecondSmoke()
    controller = RuntimeTransactionController(
        adapter=RealUvAdapter(timeout_seconds=300.0),
        smoke_runner=smoke,
        transaction_id_factory=lambda: next(ids),
    )

    installed = controller.install(requests[0])
    pointer_path = install_root / "state" / "activation.txt"
    pointer_before_failure = pointer_path.read_bytes()
    failed_update_report = (
        install_root / "state" / "logs" / f"transaction-{2:032x}.json"
    )
    try:
        controller.update(requests[1])
    except ManagedRuntimeError as exc:
        if "smoke" not in str(exc):
            raise
        failed_update_error = str(exc)
    else:
        raise ManagedRuntimeError("real probe expected the v2 update to fail")
    if pointer_path.read_bytes() != pointer_before_failure:
        raise ManagedRuntimeError("failed update changed activation bytes")
    if not failed_update_report.is_file():
        raise ManagedRuntimeError("failed update did not write its report")

    repaired = controller.repair(requests[1])
    updated = controller.update(requests[2])
    rolled_back = controller.rollback(
        RuntimeRollbackRequest(requests[0].paths, args.source_revision)
    )
    if rolled_back.pointer.active_release_id != requests[1].manifest.release_id:
        raise ManagedRuntimeError("rollback did not restore the v2 receipt")
    fresh_version = launch_active(install_root, ("--version",), timeout_seconds=60.0)
    active_pointer = read_activation_pointer(install_root)
    active_root = install_root / "versions" / active_pointer.active_release_id
    fresh_smoke = SubprocessRuntimeSmokeRunner().run(
        active_root / "env" / "bin" / "python",
        active_root / "app" / requests[1].manifest.easyqc.entrypoint,
        requests[1].manifest.easyqc.version,
        60.0,
    )
    if (
        fresh_version.stdout.strip() != requests[1].manifest.easyqc.version
        or any(item.returncode != 0 for item in fresh_smoke)
        or not fresh_smoke[1].stdout.strip().startswith("QT_OFFSCREEN_OK ")
    ):
        raise ManagedRuntimeError("fresh-process version/Qt verification failed")

    summary = {
        "schema_version": 1,
        "result": "passed",
        "work_root": str(work_root),
        "install_root": str(install_root),
        "source_revision": args.source_revision,
        "offline_controls": [
            "uv --offline",
            "uv --no-python-downloads",
            "uv pip sync --no-index",
            "verified local artifacts",
        ],
        "install_release": installed.receipt.release_id,
        "failed_update_report": str(failed_update_report),
        "failed_update_error_sha256": hashlib.sha256(
            failed_update_error.encode("utf-8")
        ).hexdigest(),
        "repair_release": repaired.receipt.release_id,
        "quarantined_root": str(repaired.quarantined_root),
        "update_release": updated.receipt.release_id,
        "rollback_release": rolled_back.receipt.release_id,
        "pointer_generation": rolled_back.pointer.generation,
        "fresh_version": fresh_version.stdout.strip(),
        "fresh_qt": fresh_smoke[1].stdout.strip(),
        "real_smoke_calls": smoke.calls,
    }
    summary_path = work_root / "probe-summary.json"
    FileUtils.atomic_write(
        summary_path,
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps({"result": "passed", "summary": str(summary_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
