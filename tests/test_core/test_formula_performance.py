from __future__ import annotations

from time import perf_counter

import numpy as np
import pandas as pd

from core.table_transform import TableTransformEngine
from models.derived_formula import DerivedColumnFormula
from scripts.benchmark_table_query import _peak_rss


def test_100k_by_300_formula_stays_within_normal_envelope() -> None:
    """Guard representative formula work at EasyQC's supported normal scale."""

    row_count = 100_000
    source: dict[str, object] = {
        "ezqcid": [f"ROW{index:06d}" for index in range(row_count)],
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
    request = DerivedColumnFormula(
        "scan_key",
        (
            'IF([site] = "S0", '
            'UPPER(STEM(PATHNAME([image_path]))) & "-" & [site], '
            'TEXTBEFORE(PATHNAME([image_path]), "_") & "-" & LOWER([site]))'
        ),
    )

    started = perf_counter()
    result = TableTransformEngine().derive_column_from_formula(frame, request)
    elapsed = perf_counter() - started
    memory = _peak_rss()
    peak_rss_mib = memory["peak_rss_mib"]
    print(
        f"formula_100k_x_300 elapsed_seconds={elapsed:.3f} "
        f"peak_rss_mib={peak_rss_mib} method={memory['method']}"
    )

    assert frame.shape == (100_000, 300)
    assert result.shape == (100_000, 301)
    assert result.index.equals(frame.index)
    assert "scan_key" not in frame.columns
    assert result["scan_key"].iloc[0] == "SUB000000_T1.NII-S0"
    assert result["scan_key"].iloc[-1] == "SUB099999-s3"
    assert elapsed < 10.0
    assert peak_rss_mib is not None
    assert peak_rss_mib < 4096
