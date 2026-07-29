"""SubjectTable — a thin validated wrapper over the subjects DataFrame (P3-C).

F-IMP-5 makes ``easyqcid`` THE join key across the subject table, every rating,
and the wide pivot. A bare ``pd.DataFrame`` has no guard that the column exists,
is non-null, or is string-typed — so a malformed easyqc_all.csv would silently
break joins (NaN ratings, ValueError on merge). SubjectTable asserts those
invariants at the boundary so the failure is loud and early.

Layer: models. Depends only on pandas and the standard library. It imports no
project-internal module.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

import pandas as pd


_EASYQCID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


@dataclass
class SubjectTable:
    """Validated subjects table. ``dataframe.easyqcid`` is always string-typed."""

    dataframe: pd.DataFrame

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> "SubjectTable":
        if "easyqcid" not in df.columns:
            raise ValueError(
                f"受试者表缺少必需列 'easyqcid';现有列: {list(df.columns)}"
            )
        normalized = df.copy()
        identities = normalized["easyqcid"].tolist()
        if not identities:
            raise ValueError("受试者表的 'easyqcid' 列为空,无法用作 join key")
        for row, value in enumerate(identities, start=1):
            if not isinstance(value, str):
                raise ValueError(
                    f"受试者表第 {row} 行 easyqcid 必须是字符串: {value!r}"
                )
            if (
                _EASYQCID_PATTERN.fullmatch(value) is None
                or value in {".", ".."}
            ):
                raise ValueError(
                    f"受试者表第 {row} 行 easyqcid 不合法: {value!r}"
                )

        duplicates = sorted(
            normalized.loc[
                normalized["easyqcid"].duplicated(keep=False),
                "easyqcid",
            ].unique()
        )
        if duplicates:
            raise ValueError(f"受试者表包含重复 easyqcid: {duplicates}")

        casefolded: dict[str, str] = {}
        collisions: set[tuple[str, str]] = set()
        for identity in identities:
            prior = casefolded.setdefault(identity.casefold(), identity)
            if prior != identity:
                collisions.add(tuple(sorted((prior, identity))))
        if collisions:
            raise ValueError(
                "受试者表包含 case-only easyqcid 冲突: "
                f"{sorted(collisions)}"
            )

        return cls(dataframe=normalized)

    @classmethod
    def from_csv(cls, path) -> "SubjectTable":
        df = pd.read_csv(
            path,
            encoding="utf-8",
            converters={"easyqcid": lambda value: value},
        )
        return cls.from_dataframe(df)


__all__ = ["SubjectTable"]
