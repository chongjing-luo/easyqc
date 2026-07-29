from __future__ import annotations

import pytest

from core.module_repository import ModuleRepository, ModuleRepositoryError
from models.qcmodule import QCModule


def _legacy_modules(name: str) -> dict[str, dict]:
    return {
        "1": QCModule(name=name, label="Legacy module").to_legacy_dict(),
    }


def test_first_module_file_migration_claims_exact_legacy_rating_owner(
    tmp_path,
) -> None:
    project_root = tmp_path / "easyqc_SAMPLE"
    repository = ModuleRepository(project_root / "modules", scope="project")
    legacy_rating = (
        project_root
        / "RatingFiles"
        / "AnatQC"
        / "rater_1"
        / "AnatQC._.SUB001._.rater_1._.Good._.False.json"
    )
    legacy_rating.parent.mkdir(parents=True)
    legacy_rating.write_text("{}", encoding="utf-8")

    records = repository.migrate_legacy(_legacy_modules("AnatQC"))

    assert [record.module.name for record in records] == ["AnatQC"]
    assert legacy_rating.exists()
    assert len(list(repository.root.glob("*.json"))) == 1


def test_first_module_file_migration_rejects_case_only_rating_owner(
    tmp_path,
) -> None:
    project_root = tmp_path / "easyqc_SAMPLE"
    repository = ModuleRepository(project_root / "modules", scope="project")
    rating = (
        project_root
        / "RatingFiles"
        / "anatqc"
        / "rater_1"
        / "anatqc-rater_1-SUB001.json"
    )
    rating.parent.mkdir(parents=True)
    rating.write_text("{}", encoding="utf-8")

    with pytest.raises(ModuleRepositoryError, match="case"):
        repository.migrate_legacy(_legacy_modules("AnatQC"))

    assert rating.exists()
    assert not repository.root.exists()
