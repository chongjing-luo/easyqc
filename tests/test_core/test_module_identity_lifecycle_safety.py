from __future__ import annotations

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
