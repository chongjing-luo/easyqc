from __future__ import annotations

from time import perf_counter

import numpy as np
import pandas as pd

from core.table_transform import TableTransformEngine
from models.column_recipe import ColumnRecipe, RecipeStep, RecipeValue
from scripts.benchmark_table_query import _peak_rss


def test_100k_by_300_column_recipe_stays_within_normal_envelope() -> None:
    """Record deterministic elapsed/RSS evidence for the supported workstation."""

    row_count = 100_000
    source: dict[str, object] = {
        "easyqcid": [f"ROW{index:06d}" for index in range(row_count)],
        "image_path": [
            f"/data/site{index % 4}/SUB{index:06d}_T1.nii.gz"
            for index in range(row_count)
        ],
        "site": [f"S{index % 4}" for index in range(row_count)],
    }
    base = np.arange(row_count, dtype=np.int32)
    for index in range(297):
        source[f"measure_{index:03d}"] = base
    frame = pd.DataFrame(source)
    recipe = ColumnRecipe(
        name="scan_key",
        source_column="image_path",
        steps=(
            RecipeStep.create("path_name"),
            RecipeStep.create("remove_suffix", suffix=".nii.gz"),
            RecipeStep.create("split_take", delimiter="_", index=0),
            RecipeStep.create("prepend", value=RecipeValue.literal("scan-")),
            RecipeStep.create("append", value=RecipeValue.literal("-")),
            RecipeStep.create("append", value=RecipeValue.column("site")),
        ),
    )

    started = perf_counter()
    result = TableTransformEngine().derive_column_from_recipe(frame, recipe)
    elapsed = perf_counter() - started
    memory = _peak_rss()
    peak_rss_mib = memory["peak_rss_mib"]
    print(
        f"column_recipe_100k_x_300 elapsed_seconds={elapsed:.3f} "
        f"peak_rss_mib={peak_rss_mib} method={memory['method']}"
    )

    assert frame.shape == (100_000, 300)
    assert result.shape == (100_000, 301)
    assert result["scan_key"].iloc[0] == "scan-SUB000000-S0"
    assert result["scan_key"].iloc[-1] == "scan-SUB099999-S3"
    assert elapsed < 10.0
    assert peak_rss_mib is not None
    assert peak_rss_mib < 4096
