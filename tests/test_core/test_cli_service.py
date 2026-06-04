import json

import pytest

from core.cli_service import QCPageLaunchError, resolve_qcpage_launch


def _write_registry(path, projects, last_project):
    path.write_text(
        json.dumps({"projects": projects, "last_project": last_project}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_resolve_qcpage_launch_uses_project_service_and_cli_rater(sample_project_dir, tmp_path) -> None:
    registry_path = tmp_path / "projects.json"
    _write_registry(registry_path, {"SAMPLE": str(sample_project_dir)}, "SAMPLE")

    context = resolve_qcpage_launch(
        "SAMPLE",
        "example",
        "cli_rater",
        "SUB001",
        registry_path,
    )

    assert context.project.name == "SAMPLE"
    assert context.module_index == "1"
    assert context.module_name == "example"
    assert context.rater == "cli_rater"
    assert context.ezqcid == "SUB001"
    assert context.module["rater"] == "cli_rater"
    assert context.module_rater_dir == sample_project_dir / "RatingFiles" / "example" / "cli_rater"
    assert context.available_modules == ["example"]


def test_resolve_qcpage_launch_reports_missing_project(sample_project_dir, tmp_path) -> None:
    registry_path = tmp_path / "projects.json"
    _write_registry(registry_path, {"SAMPLE": str(sample_project_dir)}, "SAMPLE")

    with pytest.raises(QCPageLaunchError) as exc:
        resolve_qcpage_launch("MISSING", "example", "rater1", "SUB001", registry_path)

    assert "项目不存在: MISSING" in str(exc.value)
    assert "SAMPLE" in str(exc.value)


def test_resolve_qcpage_launch_reports_missing_module(sample_project_dir, tmp_path) -> None:
    registry_path = tmp_path / "projects.json"
    _write_registry(registry_path, {"SAMPLE": str(sample_project_dir)}, "SAMPLE")

    with pytest.raises(QCPageLaunchError) as exc:
        resolve_qcpage_launch("SAMPLE", "missing", "rater1", "SUB001", registry_path)

    assert "模块不存在: missing" in str(exc.value)
    assert "example" in str(exc.value)
