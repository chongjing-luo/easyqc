from __future__ import annotations

import json

import pytest

from core.module_repository import (
    ModuleRecord,
    ModuleRepository,
    ModuleRepositoryError,
)
from models.qcmodule import QCModule


def test_removed_module_name_stays_reserved_by_case_variant_json_suffix(
    tmp_path,
) -> None:
    project_root = tmp_path / "easyqc_SAMPLE"
    repository = ModuleRepository(project_root / "modules", scope="project")
    rating = (
        project_root
        / "RatingFiles"
        / "AnatQC"
        / "rater_1"
        / "AnatQC-rater_1-SUB001.JSON"
    )
    rating.parent.mkdir(parents=True)
    rating.write_text("{}", encoding="utf-8")

    with pytest.raises(ModuleRepositoryError, match="reserved"):
        repository.save(
            ModuleRecord.create(
                QCModule(name="anatqc", label="Anatomical"),
                scope="project",
                display_order=10,
            )
        )

    assert rating.exists()
    assert not repository.root.exists()


@pytest.mark.parametrize("scope", ["project", "template"])
@pytest.mark.parametrize("entry", ["uppercase_json", "mixed_case_json", "unknown_file", "mac_metadata", "unknown_directory"])
def test_catalog_reports_unrecognized_entries_and_refuses_lossy_replace(tmp_path, scope, entry) -> None:
    """EQC-D004: a read snapshot cannot silently omit data later deleted by save."""
    repository = ModuleRepository(tmp_path / "modules", scope=scope)
    first = repository.save(
        ModuleRecord.create(QCModule(name="FirstQC", label="First"), scope=scope, display_order=10)
    )
    second = ModuleRecord.create(
        QCModule(name="SecondQC", label="Second"), scope=scope, display_order=20
    )
    if entry in {"uppercase_json", "mixed_case_json"}:
        suffix = ".JSON" if entry == "uppercase_json" else ".Json"
        extra = repository.root / f"{second.module_id}{suffix}"
        extra.write_text(json.dumps(second.to_json_object()), encoding="utf-8")
    elif entry in {"unknown_file", "mac_metadata"}:
        extra = repository.root / (".DS_Store" if entry == "mac_metadata" else "notes.txt")
        extra.write_text("user-owned material", encoding="utf-8")
    else:
        extra = repository.root / "additional-material"
        extra.mkdir()
        (extra / "notes.txt").write_text("user-owned material", encoding="utf-8")
    original = {
        str(path.relative_to(repository.root)): path.read_bytes()
        for path in repository.root.rglob("*") if path.is_file()
    }

    snapshot = repository.snapshot()

    assert [record.module.name for record in snapshot.records] == ["FirstQC"]
    assert any(error.path == extra for error in snapshot.errors)
    with pytest.raises(ModuleRepositoryError, match="catalog contains errors"):
        repository.replace_records([first])
    assert {
        str(path.relative_to(repository.root)): path.read_bytes()
        for path in repository.root.rglob("*") if path.is_file()
    } == original
