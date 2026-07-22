from __future__ import annotations

from dataclasses import replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import textwrap

import pytest

from models.platform_verification import (
    AutomatedCheckPlanV1,
    VerificationRequestV1,
)


EXPECTED_MATRIX = {
    "Ubuntu 22.04 x86_64": {
        "runner": "ubuntu-22.04",
        "row_id": "ci-ubuntu-22.04-x86_64",
        "lock_path": "packaging/locks/python-3.10.17/linux-x86_64/test.txt",
    },
    "Ubuntu 24.04 x86_64": {
        "runner": "ubuntu-24.04",
        "row_id": "ci-ubuntu-24.04-x86_64",
        "lock_path": "packaging/locks/python-3.10.17/linux-x86_64/test.txt",
    },
    "Windows Server 2022 x64": {
        "runner": "windows-2022",
        "row_id": "ci-windows-2022-x86_64",
        "lock_path": "packaging/locks/python-3.10.17/windows-x86_64/test.txt",
    },
    "macOS 15 arm64": {
        "runner": "macos-15",
        "row_id": "ci-macos-15-arm64",
        "lock_path": "packaging/locks/python-3.10.17/macos-arm64/test.txt",
    },
}
EXPECTED_ACTIONS = {
    (
        "actions/checkout",
        "11d5960a326750d5838078e36cf38b85af677262",
    ),
    (
        "actions/setup-python",
        "a26af69be951a213d495a4c3e4e4022e16d87065",
    ),
    (
        "astral-sh/setup-uv",
        "d0cc045d04ccac9d8b7881df0226f9e82c39688e",
    ),
    (
        "actions/upload-artifact",
        "ea165f8d65b6e75b540449e92b4886f43607fa02",
    ),
}
NATIVE_ROW_IDS = {
    "native-ubuntu-22.04-x86_64",
    "native-ubuntu-24.04-x86_64",
    "native-windows-11-x86_64",
    "native-macos-13-arm64",
}


def _workflow_text(easyqc_root: Path) -> str:
    return (easyqc_root / ".github" / "workflows" / "qt-platform.yml").read_text(
        encoding="utf-8"
    )


def _selected_matrix(text: str, platform: str, tmp_path: Path) -> dict[str, object]:
    match = re.search(
        r"(?ms)^      - name: Select CI matrix\n"
        r".*?^        run: \|\n"
        r"(?P<script>(?:(?:          [^\n]*)?\n)+?)"
        r"(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        text,
    )
    assert match is not None, "workflow must contain the matrix selector step"
    output_path = tmp_path / f"github-output-{platform}.txt"
    environment = os.environ.copy()
    environment.update(
        {
            "SELECTED_PLATFORM": platform,
            "GITHUB_OUTPUT": str(output_path),
        }
    )

    result = subprocess.run(
        ["bash", "-eu", "-o", "pipefail", "-c", textwrap.dedent(match.group("script"))],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    output = output_path.read_text(encoding="utf-8").strip()
    assert output.startswith("matrix=")
    return json.loads(output.removeprefix("matrix="))


def _preparation_module():
    return importlib.import_module("scripts.prepare_ci_verification")


def _fixture_input(module: object, tmp_path: Path):
    source_root = tmp_path / "source"
    relative_lock = Path(EXPECTED_MATRIX["Ubuntu 22.04 x86_64"]["lock_path"])
    lock_path = source_root / relative_lock
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        "numpy==2.2.6 --hash=sha256:" + "a" * 64 + "\n"
        "pandas==2.3.3 --hash=sha256:" + "b" * 64 + "\n"
        "pyside6-essentials==6.11.1 --hash=sha256:" + "c" * 64 + "\n",
        encoding="utf-8",
    )
    config = module.CiPreparationInput(
        matrix_row_id="ci-ubuntu-22.04-x86_64",
        run_id="ci-123-1-ubuntu-22",
        runner_label="ubuntu-22.04",
        source_root=source_root,
        lock_path=lock_path,
        release_id=None,
        manifest_sha256=None,
    )
    snapshot = module.CiEnvironmentSnapshot(
        source_revision="a" * 40,
        source_dirty=False,
        python_executable="/opt/python/3.10.17/bin/python",
        uv="0.11.29",
        python="3.10.17",
        qt="6.11.1",
        pyside="6.11.1",
        pandas="2.3.3",
        numpy="2.2.6",
        os="linux",
        version="22.04",
        arch="x86_64",
        kernel="6.8.0",
        runner_image="ubuntu22:20260720.1",
        cpu="fixture-cpu",
        ram_bytes=16 * 1024 * 1024 * 1024,
        gpu_or_renderer="offscreen",
    )
    return config, snapshot


def test_workflow_has_exact_four_pinned_ci_rows_and_no_native_rows(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    text = _workflow_text(easyqc_root)
    selected = _selected_matrix(text, "all", tmp_path)
    rows = selected["include"]
    assert isinstance(rows, list)
    matrix = {
        row["name"]: {
            "runner": row["runner"],
            "row_id": row["row_id"],
            "lock_path": row["lock_path"],
        }
        for row in rows
    }

    assert matrix == EXPECTED_MATRIX
    assert "needs: select-matrix" in text
    assert "matrix: ${{ fromJSON(needs.select-matrix.outputs.matrix) }}" in text
    assert "runs-on: ${{ matrix.runner }}" in text
    assert "-latest" not in text
    assert all(row_id not in text for row_id in NATIVE_ROW_IDS)


@pytest.mark.parametrize(
    ("platform", "expected_names"),
    [
        ("ubuntu", {"Ubuntu 22.04 x86_64", "Ubuntu 24.04 x86_64"}),
        ("windows", {"Windows Server 2022 x64"}),
        ("macos", {"macOS 15 arm64"}),
        ("all", set(EXPECTED_MATRIX)),
    ],
)
def test_manual_dispatch_selects_only_the_requested_platform_rows(
    easyqc_root: Path,
    tmp_path: Path,
    platform: str,
    expected_names: set[str],
) -> None:
    text = _workflow_text(easyqc_root)

    assert re.search(
        r"(?m)^      platform:\n"
        r"        description: Select the hosted platform family\n"
        r"        required: true\n"
        r"        default: ubuntu\n"
        r"        type: choice\n"
        r"        options:\n"
        r"          - ubuntu\n"
        r"          - windows\n"
        r"          - macos\n"
        r"          - all$",
        text,
    )
    selected = _selected_matrix(text, platform, tmp_path)
    rows = selected["include"]
    assert isinstance(rows, list)
    assert {row["name"] for row in rows} == expected_names


def test_verification_push_branches_route_one_platform_without_narrowing_main(
    easyqc_root: Path,
) -> None:
    text = _workflow_text(easyqc_root)

    assert re.search(
        r"(?m)^  push:\n"
        r"    branches:\n"
        r"      - main\n"
        r"      - 'verification/ubuntu/\*\*'\n"
        r"      - 'verification/windows/\*\*'\n"
        r"      - 'verification/macos/\*\*'$",
        text,
    )
    assert "startsWith(github.ref_name, 'verification/ubuntu/')" in text
    assert "startsWith(github.ref_name, 'verification/windows/')" in text
    assert "startsWith(github.ref_name, 'verification/macos/')" in text
    assert "inputs.platform" in text
    assert "|| 'all'" in text


def test_workflow_pins_actions_runtime_permissions_and_failure_artifacts(
    easyqc_root: Path,
) -> None:
    text = _workflow_text(easyqc_root)
    action_refs = set(
        re.findall(
            r"(?m)^\s+- uses: ([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([0-9a-f]{40})$",
            text,
        )
    )

    assert action_refs == EXPECTED_ACTIONS
    assert len(re.findall(r"(?m)^\s+- uses:", text)) == len(EXPECTED_ACTIONS)
    assert "pull_request_target" not in text
    assert re.search(r"(?m)^permissions:\n\s+contents: read$", text)
    assert "pull_request:" in text
    assert "push:" in text
    assert "workflow_dispatch:" in text
    assert 'python-version: "3.10.17"' in text
    assert 'version: "0.11.29"' in text
    assert "enable-cache: false" in text
    assert "persist-credentials: false" in text
    assert "uv pip sync --system --require-hashes" in text
    assert "uv pip check --system" in text
    assert "if: ${{ always() }}" in text
    assert "if-no-files-found: error" in text
    assert "retention-days: 30" in text


def test_workflow_routes_three_reports_through_existing_runner(
    easyqc_root: Path,
) -> None:
    text = _workflow_text(easyqc_root)

    assert "python scripts/prepare_ci_verification.py" in text
    assert "python scripts/run_platform_verification.py" in text
    assert "--request" in text and "verification-request.json" in text
    assert "--check-plan" in text and "automated-check-plan.json" in text
    assert "--attempt-root" in text
    assert "QT_QPA_PLATFORM: offscreen" in text
    assert "EASYQC_LOG_DIR:" in text
    assert "EASYQC_CI_RELEASE_ID: ${{ inputs.release_id }}" in text
    assert "EASYQC_CI_MANIFEST_SHA256: ${{ inputs.manifest_sha256 }}" in text


def test_workflow_uses_runner_context_only_after_the_job_is_allocated(
    easyqc_root: Path,
) -> None:
    text = _workflow_text(easyqc_root)
    job_environment = text.split("    env:\n", 1)[1].split("    steps:\n", 1)[0]

    assert "runner.temp" not in job_environment
    assert re.search(
        r"Prepare exact CI request\n"
        r"\s+env:\n"
        r"\s+EASYQC_LOG_DIR: \$\{\{ runner\.temp \}\}/easyqc-logs-"
        r"\$\{\{ matrix\.row_id \}\}",
        text,
    )
    assert re.search(
        r"Run and normalize CI verification\n"
        r"\s+env:\n"
        r"\s+EASYQC_LOG_DIR: \$\{\{ runner\.temp \}\}/easyqc-logs-"
        r"\$\{\{ matrix\.row_id \}\}",
        text,
    )


def test_preparation_builds_canonical_candidate_ci_request_and_three_reports(
    tmp_path: Path,
) -> None:
    module = _preparation_module()
    config, snapshot = _fixture_input(module, tmp_path)

    prepared = module.build_ci_verification_inputs(config, snapshot)
    request = VerificationRequestV1.from_canonical_bytes(prepared.request_bytes)
    plan = AutomatedCheckPlanV1.from_canonical_bytes(prepared.plan_bytes)

    assert request.matrix_row_id == "ci-ubuntu-22.04-x86_64"
    assert request.evidence_class == "ci"
    assert request.source.revision == snapshot.source_revision
    assert request.source.dirty is False
    assert request.release.release_id.startswith("ci-candidate-")
    assert request.release.manifest_sha256 == hashlib.sha256(
        prepared.release_input_bytes
    ).hexdigest()
    assert request.runtime.lock_sha256 == hashlib.sha256(
        config.lock_path.read_bytes()
    ).hexdigest()
    assert request.runtime.python == "3.10.17"
    assert request.runtime.uv == "0.11.29"
    assert request.platform.runner_label == config.runner_label
    assert request.platform.runner_image == snapshot.runner_image
    assert tuple(check.name for check in plan.checks) == (
        "core-suite",
        "qt-offscreen-suite",
        "managed-runtime-suite",
    )
    assert tuple(check.report_path for check in plan.checks) == (
        "reports/core-suite.json",
        "reports/qt-offscreen-suite.json",
        "reports/managed-runtime-suite.json",
    )
    core_argv = plan.checks[0].argv
    assert "tests/test_models" in core_argv
    assert "tests/test_core" in core_argv
    assert "tests/test_integration" in core_argv
    assert "tests/test_packaging_tools" not in core_argv


def test_preparation_binds_external_release_pair_and_rejects_native_or_wrong_lock(
    tmp_path: Path,
) -> None:
    module = _preparation_module()
    config, snapshot = _fixture_input(module, tmp_path)

    external = replace(
        config,
        release_id="easyqc-1.0.0",
        manifest_sha256="b" * 64,
    )
    prepared = module.build_ci_verification_inputs(external, snapshot)
    request = VerificationRequestV1.from_canonical_bytes(prepared.request_bytes)
    assert request.release.release_id == "easyqc-1.0.0"
    assert request.release.manifest_sha256 == "b" * 64
    assert b'external-release-manifest-reference' in prepared.release_input_bytes

    with pytest.raises(module.CiPreparationError, match="both.*release"):
        module.build_ci_verification_inputs(
            replace(config, release_id="easyqc-1.0.0"), snapshot
        )
    with pytest.raises(module.CiPreparationError, match="CI matrix row"):
        module.build_ci_verification_inputs(
            replace(
                config,
                matrix_row_id="native-ubuntu-22.04-x86_64",
            ),
            snapshot,
        )
    wrong_lock = config.source_root / "wrong" / "test.txt"
    wrong_lock.parent.mkdir()
    wrong_lock.write_text("wrong lock", encoding="utf-8")
    with pytest.raises(module.CiPreparationError, match="lock path"):
        module.build_ci_verification_inputs(
            replace(config, lock_path=wrong_lock), snapshot
        )


def test_preparation_rejects_runtime_versions_that_do_not_match_the_lock(
    tmp_path: Path,
) -> None:
    module = _preparation_module()
    config, snapshot = _fixture_input(module, tmp_path)

    with pytest.raises(module.CiPreparationError, match="pandas.*lock"):
        module.build_ci_verification_inputs(
            config,
            replace(snapshot, pandas="2.3.2"),
        )
    with pytest.raises(module.CiPreparationError, match="PySide.*lock"):
        module.build_ci_verification_inputs(
            config,
            replace(snapshot, pyside="6.10.0", qt="6.10.0"),
        )


@pytest.mark.parametrize(
    ("snapshot_changes", "message"),
    (
        ({"source_dirty": True}, "clean"),
        ({"python": "3.10.16"}, "Python identity"),
        ({"uv": "0.11.28"}, "uv identity"),
        ({"os": "windows"}, "platform OS"),
        ({"version": "24.04"}, "platform version"),
        ({"arch": "arm64"}, "platform architecture"),
        ({"runner_image": "ubuntu24:20260720.1"}, "runner image"),
    ),
)
def test_preparation_rejects_mismatched_source_runtime_and_runner_identity(
    tmp_path: Path,
    snapshot_changes: dict[str, object],
    message: str,
) -> None:
    module = _preparation_module()
    config, snapshot = _fixture_input(module, tmp_path)

    with pytest.raises(module.CiPreparationError, match=message):
        module.build_ci_verification_inputs(
            config,
            replace(snapshot, **snapshot_changes),
        )


def test_preparation_rejects_runner_label_that_disagrees_with_the_row(
    tmp_path: Path,
) -> None:
    module = _preparation_module()
    config, snapshot = _fixture_input(module, tmp_path)

    with pytest.raises(module.CiPreparationError, match="runner label"):
        module.build_ci_verification_inputs(
            replace(config, runner_label="ubuntu-24.04"),
            snapshot,
        )


def test_preparation_writes_new_bundle_and_rejects_output_collision(
    tmp_path: Path,
) -> None:
    module = _preparation_module()
    config, snapshot = _fixture_input(module, tmp_path)
    prepared = module.build_ci_verification_inputs(config, snapshot)
    output_root = tmp_path / "ci-inputs"

    result = module.write_ci_verification_inputs(prepared, output_root)

    assert result == output_root
    assert VerificationRequestV1.from_canonical_bytes(
        (output_root / "verification-request.json").read_bytes()
    )
    assert AutomatedCheckPlanV1.from_canonical_bytes(
        (output_root / "automated-check-plan.json").read_bytes()
    )
    assert (output_root / "release-input.json").read_bytes() == (
        prepared.release_input_bytes
    )
    with pytest.raises(module.CiPreparationError, match="already exists"):
        module.write_ci_verification_inputs(prepared, output_root)


def test_clean_git_status_probe_accepts_its_expected_blank_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _preparation_module()

    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    assert module._run_identity_command(
        ("git", "status", "--porcelain"),
        "source status",
        allow_blank=True,
    ) == ""
    with pytest.raises(module.CiPreparationError, match="blank output"):
        module._run_identity_command(("uv", "--version"), "uv")


def test_uv_identity_parser_accepts_official_target_qualified_output() -> None:
    module = _preparation_module()

    assert module._parse_uv_version(
        "uv 0.11.29 (x86_64-unknown-linux-gnu)"
    ) == "0.11.29"
    assert module._parse_uv_version("uv 0.11.29") == "0.11.29"
    with pytest.raises(module.CiPreparationError, match="uv version output"):
        module._parse_uv_version("uv latest untrusted trailing text")


def test_source_identity_probe_does_not_hide_untracked_files(
    easyqc_root: Path,
) -> None:
    source = (easyqc_root / "scripts" / "prepare_ci_verification.py").read_text(
        encoding="utf-8"
    )

    assert '"--untracked-files=all"' in source
    assert '"--untracked-files=no"' not in source
