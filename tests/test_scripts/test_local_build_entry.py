"""The local build wrapper must use the workspace build folder, not Tmp."""

from pathlib import Path
import json
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux-only local build entry")


def _layout(tmp_path):
    workspace = tmp_path / "workspace with spaces"
    source = workspace / "easyqc"
    source.mkdir(parents=True)
    entry = Path(__file__).resolve().parents[2] / "build_linux.sh"
    assert entry.is_file(), "missing permanent local build entry"
    shutil.copyfile(entry, source / "build_linux.sh")
    return source, workspace / "build" / "linux-x86_64"


def test_local_entry_preserves_arguments_and_uses_sibling_build(tmp_path):
    source, environment = _layout(tmp_path)
    python = environment / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    deb = environment / "deps" / "libxcb-cursor0_0.1.1-4ubuntu1_amd64.deb"
    deb.parent.mkdir()
    deb.write_bytes(b"synthetic input: wrapper does not unpack it")
    (source / "build.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(), "
        "'mplconfig': os.environ.get('MPLCONFIGDIR')}))\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(source / "build_linux.sh"), "--version", "1.0.0", "--help"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["args"][0] == "--linux-cursor-deb"
    assert Path(observed["args"][1]).resolve() == deb
    assert observed["args"][2:] == ["--version", "1.0.0", "--help"]
    assert Path(observed["cwd"]) == source
    assert Path(observed["mplconfig"]).resolve() == environment / "mplconfig"
    assert "Tmp" not in result.stdout
    assert "--clean" not in observed["args"]
    assert "--skip-smoke" not in observed["args"]


@pytest.mark.parametrize("missing", ["venv", "deps"])
def test_local_entry_fails_clearly_when_build_input_is_missing(tmp_path, missing):
    source, environment = _layout(tmp_path)
    if missing == "deps":
        python = environment / "venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)

    result = subprocess.run(
        ["bash", str(source / "build_linux.sh")],
        capture_output=True, text=True, check=False,
    )

    assert result.returncode != 0
    assert missing in result.stderr
    assert "build/linux-x86_64" in result.stderr
    assert not (source / "dist").exists()
