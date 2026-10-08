"""Grouping column + balanced random rater assignment (filter-dialog extensions).

Two derived-column helpers used by the filter dialog after the shorthand
operations run:

- ``add_group_column``: number consecutive rows into groups of ``size``
  (caller sorts first — group numbers follow the current row order).
- ``assign_raters``: expand the table so each image is assigned to ``per_image``
  random raters, one output row per (image, rater), with totals balanced
  across raters (greedy least-count pick with random tie-break). Each image
  always gets ``per_image`` DISTINCT raters.

Layer: core. Pure pandas + stdlib random. No GUI, no persistence.
"""

from __future__ import annotations

import random

import pandas as pd


GROUP_ORDERS = ("sequential", "reverse", "random")


def add_group_column(
    df: pd.DataFrame,
    size: int = 30,
    column: str = "group",
    *,
    order: str = "sequential",
    seed: int | None = None,
) -> pd.DataFrame:
    """Add a group-number column over the CURRENT row order.

    order:
      - "sequential": rows [0, size) -> 1, [size, 2*size) -> 2, ...
      - "reverse": numbering starts at the LAST row (row -1 is group 1).
      - "random": each row draws a random slot; slot // size + 1 becomes its
        group, so group members are a random draw and totals stay balanced
        (every group has exactly ``size`` rows except possibly the last).
        Deterministic when ``seed`` is given.

    Non-mutating (returns a copy with reset index).
    """
    if size is None or size < 1:
        raise ValueError(f"每组行数必须 >= 1: {size!r}")
    if order not in GROUP_ORDERS:
        raise ValueError(f"分组方式必须是 {GROUP_ORDERS} 之一: {order!r}")
    out = df.copy().reset_index(drop=True)
    total = len(out)
    if order == "sequential":
        numbers = out.index.to_numpy() // size + 1
    elif order == "reverse":
        numbers = (total - 1 - out.index.to_numpy()) // size + 1
    else:  # random
        rng = random.Random(seed)
        slots = list(range(total))
        rng.shuffle(slots)
        numbers = [slots[i] // size + 1 for i in range(total)]
    out[column] = numbers
    return out


def assign_raters(
    df: pd.DataFrame,
    raters: list[str],
    per_image: int = 1,
    seed: int | None = None,
    column: str = "rater",
) -> pd.DataFrame:
    """Expand rows so each image is reviewed by ``per_image`` random raters.

    Output has ``len(df) * per_image`` rows; each input row is duplicated once
    per assigned rater with the rater name in ``column``.

    Balance: each image draws its ``per_image`` raters from those with the
    smallest running counts (random tie-break), so totals stay balanced
    (spread <= per_image across raters) while remaining a valid random
    assignment. Every image gets ``per_image`` DISTINCT raters.

    Non-mutating. Deterministic when ``seed`` is given.
    """
    raters = [r.strip() for r in (raters or []) if r and r.strip()]
    if not raters:
        raise ValueError("评分者名单不能为空")
    if per_image is None or per_image < 1:
        raise ValueError(f"每图评分人数必须 >= 1: {per_image!r}")
    if per_image > len(raters):
        raise ValueError(f"每图人数({per_image})不能超过评分者数({len(raters)})")

    rng = random.Random(seed)
    counts = {r: 0 for r in raters}

    records: list[dict] = []
    for row in df.to_dict("records"):
        # least-count-first with random tie-break -> balanced + random
        ordered = sorted(raters, key=lambda r: (counts[r], rng.random()))
        chosen = ordered[:per_image]
        for rater in chosen:
            counts[rater] += 1
            new_row = dict(row)
            new_row[column] = rater
            records.append(new_row)

    return pd.DataFrame(records)


__all__ = ["GROUP_ORDERS", "add_group_column", "assign_raters"]
