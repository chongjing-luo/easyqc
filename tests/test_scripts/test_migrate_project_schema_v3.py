from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.project_schema_v3_migration import (
    MigrationError,
    convert_project_tree,
    create_verified_backup,
    migrate_registered_project,
    validate_schema3_project,
    verify_backup,
)


MODULE_ID = "11111111-1111-4111-8111-111111111111"


def test_script_entrypoint_bootstraps_repository_imports(tmp_path: Path) -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "migrate_project_schema_v3.py"
    )
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--registry" in completed.stdout


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _legacy_module(*, rater: str | None, ezqcid: str | None) -> dict[str, object]:
    return {
        "name": "Brain",
        "label": "Brain QC",
        "rater": rater,
        "ezqcid": ezqcid,
        "watch_mode": False,
        "tags": {"1": {"label": "artifact", "value": False}},
        "scores": {
            "1": {
                "label": "quality",
                "num": "1-3",
                "num_": "1,2,3",
                "value": "2" if rater else None,
            }
        },
        "code": None,
        "interper": "shell",
        "control": False,
        "select_filter": None,
        "qc_filter": None,
        "showing": True,
        "code_exe": None,
        "time": "2026-07-29 12:00:00" if rater else None,
        "notes": "clear" if rater else None,
        "button": {},
    }


def _make_legacy_project(parent: Path, project_name: str = "DEMO") -> Path:
    project = parent / f"easyqc_{project_name}"
    module = _legacy_module(rater=None, ezqcid=None)
    settings = {
        "schema_version": 1,
        "constants": {"ROOT": "/data"},
        "variables": {},
        "var_select_filter": None,
        "select_filter": json.dumps(
            {"filters": [{"column": "ezqcid", "operator": "contains", "value": "ID"}]}
        ),
        "qcmodule": {"1": module},
    }
    _write_json(project / f"settings_{project_name}.json", settings)
    _write_json(
        project / "modules" / f"{MODULE_ID}.json",
        {
            "schema_version": 1,
            "module_id": MODULE_ID,
            "scope": "project",
            "display_order": 1,
            "module": module,
        },
    )

    rows = [["ID-1", "batch-a", "/data/a"], ["ID_2", "batch-b", "/data/b"]]
    for name in (
        "ezqc_all.csv",
        "ezqc_qctable.csv",
        "ezqc_qctable_filter.csv",
        "ezqc_ezqc_qctable.csv",
    ):
        _write_csv(
            project / "Table" / name,
            ["ezqcid", "ezqcbatch", "path"],
            rows,
        )

    rating = _legacy_module(rater="alice", ezqcid="ID-1")
    rating["schema_version"] = 1
    _write_json(
        project
        / "RatingFiles"
        / "Brain"
        / "alice"
        / "Brain._.ID-1._.alice._.None._.False.json",
        rating,
    )
    return project


def _tree_digest(root: Path) -> dict[str, tuple[int, str]]:
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        data = path.read_bytes()
        result[path.relative_to(root).as_posix()] = (
            len(data),
            hashlib.sha256(data).hexdigest(),
        )
    return result


def _registry(path: Path, project_name: str, project_path: Path) -> Path:
    registry = path / "projects.json"
    _write_json(
        registry,
        {
            "projects": {project_name: str(project_path)},
            "last_project": project_name,
        },
    )
    return registry


def test_convert_project_tree_is_lossless_and_core_valid(tmp_path: Path) -> None:
    source = _make_legacy_project(tmp_path / "source")
    before = _tree_digest(source)
    output = tmp_path / "stage" / source.name

    summary = convert_project_tree(source, output, "DEMO")

    assert _tree_digest(source) == before
    assert summary.rating_files == 1
    assert summary.module_files == 1
    assert summary.csv_files == 4
    assert summary.csv_rows == 8
    assert summary.rating_identities == 1

    settings = json.loads((output / "settings_DEMO.json").read_text())
    assert settings["schema_version"] == 3
    assert "ezqcid" not in settings["qcmodule"]["1"]
    assert settings["qcmodule"]["1"]["easyqcid"] is None
    assert "easyqcid" in settings["select_filter"]
    assert "ezqcid" not in settings["select_filter"]

    module_file = json.loads((output / "modules" / f"{MODULE_ID}.json").read_text())
    assert module_file["schema_version"] == 1
    assert module_file["module"]["easyqcid"] is None

    assert sorted(path.name for path in (output / "Table").glob("*.csv")) == [
        "easyqc_all.csv",
        "easyqc_easyqc_qctable.csv",
        "easyqc_qctable.csv",
        "easyqc_qctable_filter.csv",
    ]
    with (output / "Table" / "easyqc_all.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.reader(handle))
    assert b"\r\n" not in (output / "Table" / "easyqc_all.csv").read_bytes()
    assert rows == [
        ["easyqcid", "easyqcbatch", "path"],
        ["ID-1", "batch-a", "/data/a"],
        ["ID_2", "batch-b", "/data/b"],
    ]

    rating_path = (
        output / "RatingFiles" / "Brain" / "alice" / "Brain-alice-ID-1.json"
    )
    rating = json.loads(rating_path.read_text())
    assert rating["schema_version"] == 3
    assert rating["easyqcid"] == "ID-1"
    assert "ezqcid" not in rating
    assert rating["scores"]["1"]["value"] == "2"
    assert rating["notes"] == "clear"

    validation = validate_schema3_project(
        output,
        "DEMO",
        expected_ratings=1,
        expected_modules=1,
        expected_csv_files=4,
    )
    assert validation.rating_files == 1
    assert validation.rating_unique_easyqcids == 1
    assert validation.subject_rows == 2
    assert validation.aggregation_rows == 2


def test_conversion_fails_closed_without_overwrite_or_partial_output(
    tmp_path: Path,
) -> None:
    source = _make_legacy_project(tmp_path / "source")
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(MigrationError, match="already exists"):
        convert_project_tree(source, output, "DEMO")

    assert sentinel.read_text(encoding="utf-8") == "keep"

    bad_source = _make_legacy_project(tmp_path / "bad")
    _write_csv(
        bad_source / "Table" / "ezqc_all.csv",
        ["ezqcid", "easyqcid"],
        [["ID-1", "ID-1"]],
    )
    absent_output = tmp_path / "absent-output"
    with pytest.raises(MigrationError, match="duplicate columns"):
        convert_project_tree(bad_source, absent_output, "DEMO")
    assert not absent_output.exists()

    nested_output = source / "nested-output"
    with pytest.raises(MigrationError, match="outside the easyqc_DEMO project"):
        convert_project_tree(source, nested_output, "DEMO")
    assert not nested_output.exists()


def test_conversion_rejects_rating_identity_disagreement(tmp_path: Path) -> None:
    source = _make_legacy_project(tmp_path / "source")
    rating_path = next((source / "RatingFiles").glob("*/*/*.json"))
    payload = json.loads(rating_path.read_text())
    payload["name"] = "Other"
    _write_json(rating_path, payload)

    output = tmp_path / "output"
    with pytest.raises(MigrationError, match="directory identity"):
        convert_project_tree(source, output, "DEMO")
    assert not output.exists()


def test_validation_rejects_settings_module_repository_drift(
    tmp_path: Path,
) -> None:
    source = _make_legacy_project(tmp_path / "source")
    output = tmp_path / "output"
    convert_project_tree(source, output, "DEMO")
    settings_path = output / "settings_DEMO.json"
    settings = json.loads(settings_path.read_text())
    settings["qcmodule"]["1"]["label"] = "stale settings copy"
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(MigrationError, match="qcmodule disagrees"):
        validate_schema3_project(output, "DEMO")


def test_verified_backup_is_no_overwrite_and_detects_tampering(
    tmp_path: Path,
) -> None:
    project = _make_legacy_project(tmp_path / "live")
    registry = _registry(tmp_path / "live", "DEMO", project)
    backup = tmp_path / "backups" / "demo-pre-schema3"

    summary = create_verified_backup(project, registry, backup)

    assert summary.file_count == len(_tree_digest(project)) + 1
    verified = verify_backup(backup)
    assert verified.file_count == summary.file_count
    assert (
        backup / "project" / "easyqc_DEMO" / "settings_DEMO.json"
    ).exists()
    assert (backup / "registry" / "projects.json").exists()

    with pytest.raises(MigrationError, match="already exists"):
        create_verified_backup(project, registry, backup)

    nested_backup = project / "nested-backup"
    with pytest.raises(MigrationError, match="outside the easyqc_DEMO project"):
        create_verified_backup(project, registry, nested_backup)
    assert not nested_backup.exists()

    backed_up_settings = (
        backup / "project" / "easyqc_DEMO" / "settings_DEMO.json"
    )
    backed_up_settings.write_text("tampered", encoding="utf-8")
    with pytest.raises(MigrationError, match="checksum"):
        verify_backup(backup)


def test_registered_migration_switches_only_after_verified_backup(
    tmp_path: Path,
) -> None:
    live_parent = tmp_path / "live"
    project = _make_legacy_project(live_parent)
    registry = _registry(live_parent, "DEMO", project)
    registry_before = registry.read_bytes()
    source_before = _tree_digest(project)

    result = migrate_registered_project(
        registry_path=registry,
        project_name="DEMO",
        backup_parent=tmp_path / "backups",
        timestamp="20260729_010203",
    )

    assert result.backup_dir.name == "easyqc_DEMO_pre_schema3_20260729_010203"
    assert registry.read_bytes() == registry_before
    assert result.validation.rating_files == 1
    assert result.validation.module_files == 1
    assert result.validation.csv_files == 4
    assert result.removed_old_live is True
    assert not result.stage_path.exists()
    assert not result.old_live_path.exists()

    live_settings = json.loads((project / "settings_DEMO.json").read_text())
    assert live_settings["schema_version"] == 3
    assert (project / "Table" / "easyqc_all.csv").exists()
    assert not (project / "Table" / "ezqc_all.csv").exists()

    backup_project = result.backup_dir / "project" / project.name
    assert _tree_digest(backup_project) == source_before
    assert verify_backup(result.backup_dir).file_count == len(source_before) + 1
    report = json.loads(
        (result.backup_dir / "migration_report.json").read_text(encoding="utf-8")
    )
    assert report["status"] == "completed"
    assert report["project_name"] == "DEMO"
    assert (result.backup_dir / "ROLLBACK.md").exists()
