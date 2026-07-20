"""Run the synthetic offline replacement first-throughput path end to end."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING, Sequence
import venv

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if TYPE_CHECKING:
    from core.managed_runtime import (
        CandidateInstallRequest,
        MaterializationRequest,
        MaterializationResult,
    )
    from models.managed_runtime import (
        ActivationPointerV1,
        InstallReceiptV1,
        ReleaseManifestV1,
    )
    from models.platform_verification import ReleaseDecisionV1, VerificationRunV1


MIB = 1024 * 1024
GIB = 1024 * 1024 * 1024
TARGET_ID = "ubuntu-22.04-x86_64"
FIXTURE_SOURCE_REVISION = "f" * 40
COMMAND_VERSION = "replacement-first-throughput-v1"
RUN_ID = "ft-r4-ci-ubuntu-22.04"
RUN_ROW_ID = "ci-ubuntu-22.04-x86_64"
REPORT_RELATIVE_PATH = "reports/integrated-seam-report.json"


class FirstThroughputError(RuntimeError):
    """Raised when an approved FT-R4 seam does not produce the exact contract."""


class _SyntheticUvAdapter:
    """scaffold-simple: stdlib venv plus a deterministic version stub.

    limit=no real uv/Python-3.13/native launcher; trigger=passed FT-R4 evidence;
    follow-up=managed-runtime architecture section 14 items 3-7.
    """

    def __init__(self, *, fail_smoke: bool = False) -> None:
        self.fail_smoke = fail_smoke

    def materialize(
        self,
        request: MaterializationRequest,
    ) -> MaterializationResult:
        from core.managed_runtime import MaterializationResult
        from utils.file_utils import FileUtils

        environment = request.version_root / "env"
        venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
        python_executable = environment / "bin" / "python"
        app_root = request.version_root / "app"
        app_root.mkdir()
        entrypoint = app_root / request.manifest.easyqc.entrypoint
        source = (
            "raise SystemExit(23)\n"
            if self.fail_smoke
            else f"print({request.manifest.easyqc.version!r})\n"
        )
        FileUtils.atomic_write(entrypoint, source)
        return MaterializationResult(
            python_executable=python_executable,
            entrypoint=entrypoint,
        )


def run_first_throughput(output_root: Path) -> dict[str, object]:
    """Carry one synthetic fixture through the approved FT-R1–R3 seams.

    Input: one explicit nonexistent output-root path with an existing parent.
    Output: one JSON-compatible summary. Side effects: points EASYQC_LOG_DIR at
    the root, writes only beneath it, creates three private stdlib venv staging
    trees, runs bounded version subprocesses and creates one offscreen
    QApplication. Real acquisition, native execution and release approval are
    separate contracts.
    """

    root = _create_output_root(output_root)
    os.environ["EASYQC_LOG_DIR"] = str(root / "logs")
    from core.platform_verification import evaluate_release_gate
    from models.managed_runtime import (
        ActivationPointerV1,
        InstallReceiptV1,
        ReleaseManifestV1,
    )
    from models.platform_verification import (
        ReleaseDecisionV1,
        ReleaseGateRequestV1,
        VerificationRunV1,
        canonical_json_bytes,
    )

    started_at = _utc_now()
    started = time.perf_counter()

    runtime_result = _run_runtime_transaction(root)
    table_result = _run_table_seam()
    completed_at = _utc_now()
    duration_seconds = max(0.0, time.perf_counter() - started)

    seam_report = {
        "schema_version": 1,
        "sample_classification": "synthetic",
        "authoritative_runtime_data_used": False,
        "started_at_utc": started_at,
        "completed_at_utc": completed_at,
        "runtime": runtime_result["report"],
        "table": table_result,
    }
    report_path = root / REPORT_RELATIVE_PATH
    report_bytes = canonical_json_bytes(seam_report)
    _atomic_write_bytes(report_path, report_bytes)
    report_sha256 = hashlib.sha256(report_bytes).hexdigest()

    manifest = runtime_result["manifest"]
    receipt = runtime_result["receipt"]
    pointer = runtime_result["pointer"]
    _require(isinstance(manifest, ReleaseManifestV1), "runtime manifest is invalid")
    _require(isinstance(receipt, InstallReceiptV1), "runtime receipt is invalid")
    _require(isinstance(pointer, ActivationPointerV1), "runtime pointer is invalid")

    run = _build_verification_run(
        manifest=manifest,
        report_sha256=report_sha256,
        started_at=started_at,
        completed_at=completed_at,
        duration_seconds=duration_seconds,
    )
    request = ReleaseGateRequestV1.from_json_object(
        {
            "schema_version": 1,
            "source": {"revision": FIXTURE_SOURCE_REVISION, "dirty": False},
            "release_id": manifest.release_id,
            "manifest_sha256": manifest.sha256,
            "runtime": run.runtime.to_json_object(),
            "command_or_checklist_version": COMMAND_VERSION,
        }
    )
    decision = evaluate_release_gate(request, (run,))

    run_path = root / "verification" / "verification-run.json"
    decision_path = root / "verification" / "release-decision.json"
    _atomic_write_bytes(run_path, run.canonical_bytes)
    _atomic_write_bytes(decision_path, decision.canonical_bytes)
    reread_run = VerificationRunV1.from_canonical_bytes(run_path.read_bytes())
    reread_decision = ReleaseDecisionV1.from_canonical_bytes(
        decision_path.read_bytes()
    )
    _validate_authority_chain(
        root=root,
        manifest=manifest,
        receipt=receipt,
        pointer=pointer,
        run=reread_run,
        decision=reread_decision,
    )

    blocker_row_ids = [
        blocker.matrix_row_id for blocker in reread_decision.blockers
    ]
    summary: dict[str, object] = {
        "schema_version": 1,
        "sample_classification": "synthetic",
        "authoritative_runtime_data_used": False,
        "release_id": manifest.release_id,
        "manifest_sha256": manifest.sha256,
        "active_release_id": pointer.active_release_id,
        "run_id": reread_run.run_id,
        "decision": reread_decision.decision,
        "blocker_row_ids": blocker_row_ids,
        "artifacts": {
            "manifest": (
                "runtime/versions/easyqc-1.0.0-linux/release-manifest.json"
            ),
            "receipt": (
                "runtime/versions/easyqc-1.0.0-linux/install-receipt.json"
            ),
            "activation": "runtime/state/activation.txt",
            "seam_report": REPORT_RELATIVE_PATH,
            "verification_run": "verification/verification-run.json",
            "release_decision": "verification/release-decision.json",
        },
        "artifact_sha256": {
            "manifest": manifest.sha256,
            "receipt": receipt.sha256,
            "seam_report": report_sha256,
            "verification_run": reread_run.sha256,
            "release_decision": reread_decision.sha256,
        },
        "limitations": [
            "Stage 5 synthetic fixture only; no real CI or native PASS evidence.",
            "Fake uv uses the current stdlib venv and a deterministic version stub.",
            "Release remains NO-GO until every required real matrix row passes.",
        ],
    }
    summary_path = root / "summary.json"
    _atomic_write_bytes(summary_path, canonical_json_bytes(summary))
    _require(
        canonical_json_bytes(summary) == summary_path.read_bytes(),
        "summary reread mismatch",
    )
    return summary


def _create_output_root(output_root: Path) -> Path:
    if not isinstance(output_root, Path):
        raise FirstThroughputError("output root must be a pathlib.Path")
    if output_root.exists() or output_root.is_symlink():
        raise FirstThroughputError("output root must not exist")
    try:
        parent = output_root.parent.resolve(strict=True)
    except OSError as exc:
        raise FirstThroughputError("output root parent must exist") from exc
    if not parent.is_dir():
        raise FirstThroughputError("output root parent must be a directory")
    root = parent / output_root.name
    try:
        root.mkdir(mode=0o700)
    except OSError as exc:
        raise FirstThroughputError(f"cannot create output root: {exc}") from exc
    return root


def _run_runtime_transaction(root: Path) -> dict[str, object]:
    from core.managed_runtime import (
        ManagedRuntimeError,
        install_candidate,
        launch_active,
        load_install_receipt,
        read_activation_pointer,
        rollback,
    )
    from models.managed_runtime import ActivationPointerV1, ReleaseManifestV1

    install_root = root / "runtime"
    payload_v1 = root / "payload-v1"
    payload_v2 = root / "payload-v2"
    manifest_v1 = _write_synthetic_payload(payload_v1, "1.0.0")
    manifest_v2 = _write_synthetic_payload(payload_v2, "2.0.0")

    receipt_v1 = install_candidate(
        _install_request(install_root, manifest_v1, payload_v1),
        _SyntheticUvAdapter(),
    )
    pointer_before_failure = read_activation_pointer(install_root)
    pointer_path = install_root / "state" / "activation.txt"
    pointer_bytes_before_failure = pointer_path.read_bytes()
    failure_message = ""
    try:
        install_candidate(
            _install_request(install_root, manifest_v2, payload_v2),
            _SyntheticUvAdapter(fail_smoke=True),
        )
    except ManagedRuntimeError as exc:
        failure_message = str(exc)
        _require("smoke" in failure_message.lower(), "v2 failed outside smoke")
    else:
        raise FirstThroughputError("v2 failure injection unexpectedly passed")

    pointer_bytes_after_failure = pointer_path.read_bytes()
    _require(
        pointer_bytes_after_failure == pointer_bytes_before_failure,
        "failed v2 update changed activation bytes",
    )
    failed_stages = tuple(
        sorted(
            (install_root / "versions").glob(
                f".staging-{manifest_v2.release_id}-*"
            )
        )
    )
    _require(len(failed_stages) == 1, "expected one retained failed v2 stage")
    failed_stage_receipt_absent = not (
        failed_stages[0] / "install-receipt.json"
    ).exists()
    _require(failed_stage_receipt_absent, "failed v2 stage contains a receipt")

    receipt_v2 = install_candidate(
        _install_request(install_root, manifest_v2, payload_v2),
        _SyntheticUvAdapter(),
    )
    updated_pointer = read_activation_pointer(install_root)
    _require(
        updated_pointer.active_release_id == manifest_v2.release_id,
        "passing v2 did not activate",
    )
    rolled_back = rollback(install_root)
    launched = launch_active(install_root, ("--version",))
    launched_version = launched.stdout.strip()
    _require(launched_version == "1.0.0", "rollback launch did not resolve v1")

    final_pointer = read_activation_pointer(install_root)
    reread_manifest = ReleaseManifestV1.from_canonical_bytes(
        (
            install_root
            / "versions"
            / manifest_v1.release_id
            / "release-manifest.json"
        ).read_bytes()
    )
    reread_receipt = load_install_receipt(install_root, manifest_v1.release_id)
    _require(reread_manifest == manifest_v1, "stored v1 manifest mismatch")
    _require(reread_receipt == receipt_v1, "stored v1 receipt mismatch")
    _require(final_pointer == rolled_back, "rollback pointer reread mismatch")
    _require(
        pointer_before_failure == ActivationPointerV1(
            manifest_v1.release_id,
            None,
            1,
        ),
        "v1 activation pointer is unexpected",
    )

    return {
        "manifest": reread_manifest,
        "receipt": reread_receipt,
        "pointer": final_pointer,
        "report": {
            "v1_release_id": manifest_v1.release_id,
            "v1_manifest_sha256": manifest_v1.sha256,
            "v1_receipt_sha256": receipt_v1.sha256,
            "failed_update_error": failure_message,
            "failed_update_pointer_preserved": True,
            "failed_stage_receipt_absent": failed_stage_receipt_absent,
            "v2_receipt_sha256": receipt_v2.sha256,
            "updated_active_release_id": updated_pointer.active_release_id,
            "rollback_active_release_id": rolled_back.active_release_id,
            "rollback_previous_release_id": rolled_back.previous_release_id,
            "rollback_generation": rolled_back.generation,
            "launched_version": launched_version,
        },
    }


def _write_synthetic_payload(root: Path, version: str) -> ReleaseManifestV1:
    from models.managed_runtime import ReleaseManifestV1

    root.mkdir()
    payloads = {
        "uv": f"uv-{version}".encode("utf-8"),
        "python.tar.zst": f"python-{version}".encode("utf-8"),
        "easyqc-source.tar.gz": f"source-{version}".encode("utf-8"),
        "requirements.lock": f"lock-{version}".encode("utf-8"),
        "easyqc_dep-1-py3-none-any.whl": f"wheel-{version}".encode("utf-8"),
        "NOTICE.txt": f"notice-{version}".encode("utf-8"),
    }
    for filename, data in payloads.items():
        _atomic_write_bytes(root / filename, data)

    def artifact(kind: str, filename: str) -> dict[str, object]:
        data = payloads[filename]
        return {
            "kind": kind,
            "filename": filename,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    return ReleaseManifestV1.from_json_object(
        {
            "schema_version": 1,
            "release_id": f"easyqc-{version}-linux",
            "channel": "candidate",
            "target": {"os": "linux", "os_minimum": "22.04", "arch": "x86_64"},
            "easyqc": {
                "version": version,
                "source_filename": "easyqc-source.tar.gz",
                "source_sha256": hashlib.sha256(
                    payloads["easyqc-source.tar.gz"]
                ).hexdigest(),
                "entrypoint": "easyqc_version.py",
            },
            "uv": {
                "version": "0.11.29",
                "filename": "uv",
                "size_bytes": len(payloads["uv"]),
                "sha256": hashlib.sha256(payloads["uv"]).hexdigest(),
            },
            "python": {
                "version": "3.13.13",
                "build": "20260720",
                "key": "cpython-3.13.13-linux-x86_64-gnu",
                "filename": "python.tar.zst",
                "size_bytes": len(payloads["python.tar.zst"]),
                "sha256": hashlib.sha256(payloads["python.tar.zst"]).hexdigest(),
            },
            "lock": {
                "filename": "requirements.lock",
                "sha256": hashlib.sha256(payloads["requirements.lock"]).hexdigest(),
                "require_hashes": True,
            },
            "artifacts": [
                artifact("easyqc-source", "easyqc-source.tar.gz"),
                artifact("lock", "requirements.lock"),
                artifact("wheel", "easyqc_dep-1-py3-none-any.whl"),
                artifact("notice", "NOTICE.txt"),
            ],
            "sources": [],
            "notices": [
                {
                    "filename": "NOTICE.txt",
                    "sha256": hashlib.sha256(payloads["NOTICE.txt"]).hexdigest(),
                }
            ],
            "expanded_version_bytes": 4096,
            "required_free_bytes": 256 * MIB + 4096,
            "smoke_contract_version": 1,
        }
    )


def _install_request(
    install_root: Path,
    manifest: ReleaseManifestV1,
    artifact_root: Path,
) -> CandidateInstallRequest:
    from core.managed_runtime import CandidateInstallRequest

    return CandidateInstallRequest(
        install_root=install_root,
        manifest=manifest,
        artifact_root=artifact_root,
        expected_target_id=TARGET_ID,
        smoke_timeout_seconds=30.0,
    )


def _run_table_seam() -> dict[str, object]:
    from PySide6.QtWidgets import QApplication
    from gui_qt.table_workspace import QtTableWorkspace
    from models.table_view_state import FilterCondition

    application = QApplication.instance()
    owns_application = application is None
    if application is None:
        application = QApplication(["easyqc-ft-r4"])
    _require(
        application.platformName().lower() == "offscreen",
        "FT-R4 requires QT_QPA_PLATFORM=offscreen",
    )

    source = pd.DataFrame(
        {
            "ezqcid": [f"SUB{index:03d}" for index in range(1, 7)],
            "site": ["A", "B", "A", "B", "A", "B"],
            "age": [21, 22, 23, 24, 25, 26],
        }
    )
    original = source.copy(deep=True)
    opened: list[str] = []
    workspace = QtTableWorkspace(source, on_open_qc=opened.append, page_size=2)
    try:
        workspace.begin_filter_edit()
        workspace.set_filter_draft(
            (FilterCondition("site", "==", "A", "ft-r4-site-a"),)
        )
        _require(workspace.apply_filter_draft(), "typed Qt filter did not apply")
        first_window = [
            str(value) for value in workspace.row_window.dataframe["ezqcid"]
        ]
        result_position = workspace.service.find_identity(
            workspace.result,
            "SUB005",
        )
        _require(result_position == 2, "target is not beyond the first window")
        _require(workspace.find_identity_exact("SUB005"), "exact identity not found")
        _require(workspace.open_selected_qc(), "exact identity callback did not open")
        target_window = [
            str(value) for value in workspace.row_window.dataframe["ezqcid"]
        ]
        _require(source.equals(original), "Qt table seam mutated the source frame")
        return {
            "source_rows": len(source),
            "matched_rows": workspace.result.matched_total,
            "page_size": workspace.applied_state.page_size,
            "first_window_ezqcids": first_window,
            "target_ezqcid": "SUB005",
            "target_result_position": result_position,
            "target_source_position": workspace.selected_source_position,
            "target_page_offset": workspace.page_offset,
            "target_window_ezqcids": target_window,
            "qc_callback_ezqcids": list(opened),
            "qt_platform": application.platformName(),
        }
    finally:
        workspace.close()
        workspace.deleteLater()
        application.processEvents()
        if owns_application:
            application.quit()


def _build_verification_run(
    *,
    manifest: ReleaseManifestV1,
    report_sha256: str,
    started_at: str,
    completed_at: str,
    duration_seconds: float,
) -> VerificationRunV1:
    from PySide6 import __version__ as pyside_version
    from PySide6.QtCore import qVersion
    from models.platform_verification import VerificationRunV1

    return VerificationRunV1.from_json_object(
        {
            "schema_version": 1,
            "run_id": RUN_ID,
            "matrix_row_id": RUN_ROW_ID,
            "status": "PASS",
            "reason_code": None,
            "started_at_utc": started_at,
            "completed_at_utc": completed_at,
            "source": {"revision": FIXTURE_SOURCE_REVISION, "dirty": False},
            "release": {
                "release_id": manifest.release_id,
                "manifest_sha256": manifest.sha256,
                "target_id": manifest.target.target_id,
            },
            "runtime": {
                "uv": manifest.uv.version,
                "python": manifest.python.version,
                "qt": qVersion(),
                "pyside": pyside_version,
                "pandas": pd.__version__,
                "numpy": np.__version__,
                "lock_sha256": manifest.lock.sha256,
            },
            "platform": {
                "os": "linux",
                "version": "22.04",
                "arch": "x86_64",
                "kernel": "stage5-synthetic",
                "runner_label": "local-stage5-synthetic",
                "runner_image": "fixture-only-not-real-ci",
            },
            "display": {
                "session": "offscreen",
                "scale_percent": 100,
                "logical_viewport": "1280x720",
                "color_scheme": "system",
                "remote": False,
            },
            "hardware": {
                "cpu": "stage5-synthetic",
                "ram_bytes": 16 * GIB,
                "gpu_or_renderer": "offscreen-fixture",
            },
            "command_or_checklist_version": COMMAND_VERSION,
            "tests": [
                {
                    "name": "integrated-first-throughput",
                    "status": "PASS",
                    "duration": duration_seconds,
                    "report_path": REPORT_RELATIVE_PATH,
                    "report_sha256": report_sha256,
                }
            ],
            "metrics": {
                "query_p50_ms": None,
                "query_p95_ms": None,
                "peak_rss_bytes": None,
                "max_event_loop_delay_ms": None,
            },
            "artifacts": [
                {
                    "path": REPORT_RELATIVE_PATH,
                    "sha256": report_sha256,
                    "classification": "smoke-report",
                }
            ],
            "limitations": [
                "Stage 5 synthetic integration fixture; not real CI or native evidence."
            ],
        }
    )


def _validate_authority_chain(
    *,
    root: Path,
    manifest: ReleaseManifestV1,
    receipt: InstallReceiptV1,
    pointer: ActivationPointerV1,
    run: VerificationRunV1,
    decision: ReleaseDecisionV1,
) -> None:
    from models.platform_verification import REQUIRED_MATRIX_ROWS

    _require(manifest.sha256 == receipt.manifest_sha256, "receipt manifest mismatch")
    _require(manifest.release_id == receipt.release_id, "receipt release mismatch")
    _require(
        pointer.active_release_id == manifest.release_id,
        "pointer release mismatch",
    )
    _require(run.release.release_id == manifest.release_id, "run release mismatch")
    _require(run.release.manifest_sha256 == manifest.sha256, "run manifest mismatch")
    _require(run.release.target_id == manifest.target.target_id, "run target mismatch")
    _require(decision.release_id == manifest.release_id, "decision release mismatch")
    _require(decision.manifest_sha256 == manifest.sha256, "decision manifest mismatch")
    _require(
        decision.source_revision == run.source.revision,
        "decision source mismatch",
    )
    _require(decision.evaluated_run_ids == (run.run_id,), "decision run mismatch")
    _require(decision.decision == "NO-GO", "fixture decision must remain NO-GO")
    expected_blockers = tuple(
        sorted(
            row.matrix_row_id
            for row in REQUIRED_MATRIX_ROWS
            if row.matrix_row_id != run.matrix_row_id
        )
    )
    actual_blockers = tuple(
        blocker.matrix_row_id for blocker in decision.blockers
    )
    _require(actual_blockers == expected_blockers, "decision blockers are unexpected")
    _require(
        all(
            blocker.status == "MISSING"
            and blocker.reason_code == "missing-required-row"
            for blocker in decision.blockers
        ),
        "decision blockers are not exact missing rows",
    )
    report_path = root / REPORT_RELATIVE_PATH
    report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
    _require(run.tests[0].report_sha256 == report_sha256, "test report hash mismatch")
    _require(run.artifacts[0].sha256 == report_sha256, "artifact hash mismatch")


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    from utils.file_utils import FileUtils

    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise FirstThroughputError("FT-R4 writes UTF-8 evidence only") from exc
    FileUtils.atomic_write(path, text)
    _require(path.read_bytes() == data, f"atomic write reread mismatch: {path.name}")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FirstThroughputError(message)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    summary = run_first_throughput(args.output_root)
    print(
        "PASS: replacement first throughput "
        f"release={summary['release_id']} "
        f"decision={summary['decision']} "
        f"output={args.output_root.resolve()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
