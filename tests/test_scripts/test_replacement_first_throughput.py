from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from models.managed_runtime import (
    ActivationPointerV1,
    InstallReceiptV1,
    ReleaseManifestV1,
)
from models.platform_verification import (
    REQUIRED_MATRIX_ROWS,
    ReleaseDecisionV1,
    VerificationRunV1,
)


def _command(easyqc_root: Path, output_root: Path | None = None) -> list[str]:
    command = [
        sys.executable,
        str(easyqc_root / "scripts" / "run_replacement_first_throughput.py"),
    ]
    if output_root is not None:
        command.extend(("--output-root", str(output_root)))
    return command


def _run(
    easyqc_root: Path,
    cwd: Path,
    output_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment.pop("EASYQC_LOG_DIR", None)
    environment["HOME"] = str(cwd / "fake-home")
    environment["XDG_STATE_HOME"] = str(cwd / "external-state")
    return subprocess.run(
        _command(easyqc_root, output_root),
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=180,
        check=False,
    )


def test_integrated_command_emits_rereadable_bound_authority(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    output_root = tmp_path / "集成 output with spaces"

    completed = _run(easyqc_root, tmp_path, output_root)

    assert completed.returncode == 0, completed.stderr
    assert "PASS" in completed.stdout
    assert "decision=NO-GO" in completed.stdout
    assert not (tmp_path / "external-state").exists()
    assert tuple((output_root / "logs").glob("easyqc_*.log"))

    runtime_root = output_root / "runtime"
    version_root = runtime_root / "versions" / "easyqc-1.0.0-linux"
    manifest_path = version_root / "release-manifest.json"
    receipt_path = version_root / "install-receipt.json"
    pointer_path = runtime_root / "state" / "activation.txt"
    report_path = output_root / "reports" / "integrated-seam-report.json"
    run_path = output_root / "verification" / "verification-run.json"
    decision_path = output_root / "verification" / "release-decision.json"
    summary_path = output_root / "summary.json"

    manifest = ReleaseManifestV1.from_canonical_bytes(manifest_path.read_bytes())
    receipt = InstallReceiptV1.from_canonical_bytes(receipt_path.read_bytes())
    pointer = ActivationPointerV1.from_bytes(pointer_path.read_bytes())
    run = VerificationRunV1.from_canonical_bytes(run_path.read_bytes())
    decision = ReleaseDecisionV1.from_canonical_bytes(decision_path.read_bytes())
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert manifest.sha256 == receipt.manifest_sha256 == run.release.manifest_sha256
    assert manifest.release_id == receipt.release_id == pointer.active_release_id
    assert run.release.release_id == decision.release_id == manifest.release_id
    assert run.release.target_id == manifest.target.target_id
    assert decision.manifest_sha256 == manifest.sha256
    assert decision.source_revision == run.source.revision
    assert decision.evaluated_run_ids == (run.run_id,)

    assert pointer.previous_release_id == "easyqc-2.0.0-linux"
    assert pointer.generation == 3
    assert report["runtime"]["failed_update_pointer_preserved"] is True
    assert report["runtime"]["failed_stage_receipt_absent"] is True
    assert report["runtime"]["updated_active_release_id"] == "easyqc-2.0.0-linux"
    assert report["runtime"]["rollback_active_release_id"] == manifest.release_id
    assert report["runtime"]["launched_version"] == "1.0.0"
    failed_stages = tuple(
        (runtime_root / "versions").glob(".staging-easyqc-2.0.0-linux-*")
    )
    assert len(failed_stages) == 1
    assert not (failed_stages[0] / "install-receipt.json").exists()

    table = report["table"]
    assert table["source_rows"] == 6
    assert table["matched_rows"] == 3
    assert table["page_size"] == 2
    assert table["first_window_ezqcids"] == ["SUB001", "SUB003"]
    assert table["target_ezqcid"] == "SUB005"
    assert table["target_result_position"] == 2
    assert table["target_source_position"] == 4
    assert table["target_page_offset"] == 2
    assert table["target_window_ezqcids"] == ["SUB005"]
    assert table["qc_callback_ezqcids"] == ["SUB005"]

    report_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
    assert run.tests[0].report_path == "reports/integrated-seam-report.json"
    assert run.tests[0].report_sha256 == report_sha256
    assert run.artifacts[0].path == run.tests[0].report_path
    assert run.artifacts[0].sha256 == report_sha256
    assert run.limitations == (
        "Stage 5 synthetic integration fixture; not real CI or native evidence.",
    )

    expected_blockers = tuple(
        sorted(
            row.matrix_row_id
            for row in REQUIRED_MATRIX_ROWS
            if row.matrix_row_id != run.matrix_row_id
        )
    )
    assert decision.decision == "NO-GO"
    assert (
        tuple(blocker.matrix_row_id for blocker in decision.blockers)
        == expected_blockers
    )
    assert {blocker.status for blocker in decision.blockers} == {"MISSING"}
    assert summary["schema_version"] == 1
    assert summary["sample_classification"] == "synthetic"
    assert summary["authoritative_runtime_data_used"] is False
    assert summary["release_id"] == manifest.release_id
    assert summary["manifest_sha256"] == manifest.sha256
    assert summary["decision"] == "NO-GO"
    assert summary["blocker_row_ids"] == list(expected_blockers)


def test_command_requires_new_explicit_output_root(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    missing = _run(easyqc_root, tmp_path)
    assert missing.returncode == 2
    assert "--output-root" in missing.stderr
    assert not (tmp_path / "external-state").exists()

    existing = tmp_path / "existing-output"
    existing.mkdir()
    sentinel = existing / "sentinel.bin"
    sentinel.write_bytes(b"preserve-me")

    rejected = _run(easyqc_root, tmp_path, existing)

    assert rejected.returncode != 0
    assert "output root must not exist" in rejected.stderr
    assert sentinel.read_bytes() == b"preserve-me"
    assert tuple(existing.iterdir()) == (sentinel,)
