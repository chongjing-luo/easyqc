from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

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
from scripts.run_replacement_first_throughput import (
    FirstThroughputError,
    _console_safe,
    _python_relative_path,
    _synthetic_host_profile,
    _write_synthetic_payload,
)


@pytest.mark.parametrize(
    (
        "host_platform",
        "target_id",
        "matrix_row_id",
        "release_suffix",
        "platform_identity",
        "uv_filename",
        "python_key",
        "python_relative_path",
    ),
    (
        (
            "linux",
            "ubuntu-22.04-x86_64",
            "ci-ubuntu-22.04-x86_64",
            "linux",
            ("linux", "22.04", "x86_64"),
            "uv",
            "cpython-3.13.13-linux-x86_64-gnu",
            "env/bin/python",
        ),
        (
            "win32",
            "windows-11-x86_64",
            "ci-windows-2022-x86_64",
            "windows",
            ("windows", "2022", "x86_64"),
            "uv.exe",
            "cpython-3.13.13-windows-x86_64-none",
            "env/Scripts/python.exe",
        ),
        (
            "darwin",
            "macos-13-arm64",
            "ci-macos-15-arm64",
            "macos",
            ("macos", "15", "arm64"),
            "uv",
            "cpython-3.13.13-darwin-aarch64-none",
            "env/bin/python",
        ),
    ),
)
def test_synthetic_host_profile_binds_target_row_identity_and_layout(
    host_platform: str,
    target_id: str,
    matrix_row_id: str,
    release_suffix: str,
    platform_identity: tuple[str, str, str],
    uv_filename: str,
    python_key: str,
    python_relative_path: str,
) -> None:
    profile = _synthetic_host_profile(host_platform)

    assert profile.target.target_id == target_id
    assert profile.matrix_row_id == matrix_row_id
    assert profile.release_suffix == release_suffix
    assert (
        profile.platform_os,
        profile.platform_version,
        profile.platform_arch,
    ) == platform_identity
    assert profile.uv_filename == uv_filename
    assert profile.python_key == python_key
    assert _python_relative_path(profile.target.os).as_posix() == python_relative_path


def test_synthetic_host_profile_rejects_unknown_host() -> None:
    with pytest.raises(FirstThroughputError, match="unsupported synthetic host"):
        _synthetic_host_profile("plan9")


def test_console_safe_escapes_non_ascii_diagnostic_text() -> None:
    rendered = _console_safe(r"C:\质控 output")

    assert rendered == r"C:\\u8d28\u63a7 output"
    assert rendered.encode("cp1252", errors="strict")


@pytest.mark.parametrize("host_platform", ("linux", "win32", "darwin"))
def test_synthetic_payload_identity_follows_host_profile(
    host_platform: str,
    tmp_path: Path,
) -> None:
    profile = _synthetic_host_profile(host_platform)

    manifest = _write_synthetic_payload(
        tmp_path / profile.release_suffix,
        "1.0.0",
        profile,
    )

    assert manifest.release_id == f"easyqc-1.0.0-{profile.release_suffix}"
    assert manifest.target == profile.target
    assert manifest.uv.filename == profile.uv_filename
    assert manifest.python.key == profile.python_key


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
    profile = _synthetic_host_profile()

    completed = _run(easyqc_root, tmp_path, output_root)

    assert completed.returncode == 0, completed.stderr
    assert "PASS" in completed.stdout
    assert "decision=NO-GO" in completed.stdout
    assert not (tmp_path / "external-state").exists()
    assert tuple((output_root / "logs").glob("easyqc_*.log"))

    runtime_root = output_root / "runtime"
    pointer_path = runtime_root / "state" / "activation.txt"
    pointer = ActivationPointerV1.from_bytes(pointer_path.read_bytes())
    version_root = runtime_root / "versions" / pointer.active_release_id
    manifest_path = version_root / "release-manifest.json"
    receipt_path = version_root / "install-receipt.json"
    report_path = output_root / "reports" / "integrated-seam-report.json"
    run_path = output_root / "verification" / "verification-run.json"
    decision_path = output_root / "verification" / "release-decision.json"
    summary_path = output_root / "summary.json"

    manifest = ReleaseManifestV1.from_canonical_bytes(manifest_path.read_bytes())
    receipt = InstallReceiptV1.from_canonical_bytes(receipt_path.read_bytes())
    run = VerificationRunV1.from_canonical_bytes(run_path.read_bytes())
    decision = ReleaseDecisionV1.from_canonical_bytes(decision_path.read_bytes())
    report = json.loads(report_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    expected_v2_release_id = f"easyqc-2.0.0-{profile.release_suffix}"

    assert manifest.sha256 == receipt.manifest_sha256 == run.release.manifest_sha256
    assert manifest.release_id == receipt.release_id == pointer.active_release_id
    assert run.release.release_id == decision.release_id == manifest.release_id
    assert run.release.target_id == manifest.target.target_id
    assert manifest.target == profile.target
    assert manifest.uv.filename == profile.uv_filename
    assert manifest.python.key == profile.python_key
    assert run.matrix_row_id == profile.matrix_row_id
    assert (
        run.platform.os,
        run.platform.version,
        run.platform.arch,
    ) == (
        profile.platform_os,
        profile.platform_version,
        profile.platform_arch,
    )
    assert decision.manifest_sha256 == manifest.sha256
    assert decision.source_revision == run.source.revision
    assert decision.evaluated_run_ids == (run.run_id,)

    assert (
        pointer.previous_release_id
        == report["runtime"]["updated_active_release_id"]
    )
    assert pointer.generation == 3
    assert report["runtime"]["failed_update_pointer_preserved"] is True
    assert report["runtime"]["failed_stage_receipt_absent"] is True
    assert report["runtime"]["updated_active_release_id"] == expected_v2_release_id
    assert report["runtime"]["rollback_active_release_id"] == manifest.release_id
    assert report["runtime"]["launched_version"] == "1.0.0"
    failed_stages = tuple(
        (runtime_root / "versions").glob(
            f".staging-{expected_v2_release_id}-*"
        )
    )
    assert len(failed_stages) == 1
    assert not (failed_stages[0] / "install-receipt.json").exists()

    table = report["table"]
    assert table["source_rows"] == 6
    assert table["matched_rows"] == 3
    assert table["page_size"] == 2
    assert table["first_window_easyqcids"] == ["SUB001", "SUB003"]
    assert table["target_easyqcid"] == "SUB005"
    assert table["target_result_position"] == 2
    assert table["target_source_position"] == 4
    assert table["target_page_offset"] == 2
    assert table["target_window_easyqcids"] == ["SUB005"]
    assert table["qc_callback_easyqcids"] == ["SUB005"]

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
    assert summary["artifacts"]["manifest"] == manifest_path.relative_to(
        output_root
    ).as_posix()
    assert summary["artifacts"]["receipt"] == receipt_path.relative_to(
        output_root
    ).as_posix()


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
