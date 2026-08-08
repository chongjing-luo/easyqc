from __future__ import annotations

from pathlib import Path
import re


def test_native_installer_workflow_has_exact_three_platform_release_matrix() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = root / ".github" / "workflows" / "native-installers.yml"

    text = workflow.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "tags:" in text
    assert "ubuntu-22.04" in text
    assert "windows-2022" in text
    assert "macos-15" in text
    assert "linux-x86_64" in text
    assert "windows-x86_64" in text
    assert "macos-arm64" in text
    assert "uv pip sync --require-hashes" in text
    assert "scripts/build_native_installer.py" in text
    assert "read_product_version" in text
    assert "artifact-manifest.json" in text
    assert "SHA256SUMS" in text
    assert "continue-on-error" not in text

    uses = re.findall(r"^\s*-?\s*uses:\s*([^\s]+)\s*$", text, flags=re.MULTILINE)
    assert uses
    for action in uses:
        assert re.search(r"@[0-9a-f]{40}$", action), action


def test_native_installer_workflow_does_not_claim_signing_without_secrets() -> None:
    root = Path(__file__).resolve().parents[2]
    text = (
        root / ".github" / "workflows" / "native-installers.yml"
    ).read_text(encoding="utf-8")

    assert "codesign" not in text
    assert "notarytool" not in text
    assert "signtool" not in text
    assert "signed: false" in text
