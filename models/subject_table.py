"""SubjectTable — a thin validated wrapper over the subjects DataFrame (P3-C).

F-IMP-5 makes ``ezqcid`` THE join key across the subject table, every rating,
and the wide pivot. A bare ``pd.DataFrame`` has no guard that the column exists,
is non-null, or is string-typed — so a malformed ezqc_all.csv would silently
break joins (NaN ratings, ValueError on merge). SubjectTable asserts those
invariants at the boundary so the failure is loud and early.

Layer: models. Depends only on pandas + utils.logger. MUST NOT import tkinter
or any core/gui module (layering rule: models import nothing project-internal
except utils).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from utils.logger import log_warning


@dataclass
class SubjectTable:
    """Validated subjects table. ``dataframe.ezqcid`` is always string-typed."""

    dataframe: pd.DataFrame

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> "SubjectTable":
        if "ezqcid" not in df.columns:
            raise ValueError(
                f"受试者表缺少必需列 'ezqcid';现有列: {list(df.columns)}"
            )
        if df["ezqcid"].isna().all():
            raise ValueError("受试者表的 'ezqcid' 列全为空,无法用作 join key")

        normalized = df.copy()
        # Coerce to string with NaN -> "" so a partial-NaN column is still
        # joinable (the rows with empty id simply won't match any rating).
        normalized["ezqcid"] = normalized["ezqcid"].astype(str)

        dup_count = int(normalized["ezqcid"].duplicated().sum())
        if dup_count:
            log_warning(
                f"受试者表有 {dup_count} 个重复 ezqcid(可能为合法重复行,仅警告)",
                "SubjectTable",
            )

        return cls(dataframe=normalized)

    @classmethod
    def from_csv(cls, path) -> "SubjectTable":
        df = pd.read_csv(path, encoding="utf-8")
        return cls.from_dataframe(df)


__all__ = ["SubjectTable"]
