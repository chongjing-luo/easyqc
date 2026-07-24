from pathlib import Path

import pytest


def test_real_runtime_roots_are_unreadable_inside_pytest(easyqc_root: Path) -> None:
    with pytest.raises(RuntimeError, match="protected EasyQC runtime data"):
        (easyqc_root / "projects.json").read_text(encoding="utf-8")

    with pytest.raises(RuntimeError, match="protected EasyQC runtime data"):
        list((easyqc_root / "projects").iterdir())


def test_pytest_temporary_files_keep_normal_read_write_access(tmp_path: Path) -> None:
    target = tmp_path / "projects.json"

    target.write_text('{"projects": {}}', encoding="utf-8")

    assert target.read_text(encoding="utf-8") == '{"projects": {}}'
