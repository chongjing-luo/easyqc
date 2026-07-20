"""Typed, GUI-independent state for the read-only table workspace."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

import numpy as np
import pandas as pd


class ColumnKind(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"


@dataclass(frozen=True)
class FilterCondition:
    column: str
    operator: str
    value: Any = None
    condition_id: str = ""
    enabled: bool = True


@dataclass(frozen=True)
class SortRule:
    column: str
    ascending: bool = True


@dataclass(frozen=True)
class ColumnViewState:
    order: tuple[str, ...]
    hidden: tuple[str, ...] = ()
    widths: tuple[tuple[str, int], ...] = ()
    pinned: tuple[str, ...] = ()

    @property
    def visible_columns(self) -> tuple[str, ...]:
        hidden = set(self.hidden)
        return tuple(column for column in self.order if column not in hidden)

    def width_for(self, column: str, default: int = 120) -> int:
        return dict(self.widths).get(column, default)

    def with_width(self, column: str, width: int) -> "ColumnViewState":
        widths = dict(self.widths)
        widths[column] = width
        return replace(self, widths=tuple((name, widths[name]) for name in self.order if name in widths))


@dataclass(frozen=True)
class TableViewState:
    columns: ColumnViewState
    conditions: tuple[FilterCondition, ...] = ()
    sort_rules: tuple[SortRule, ...] = ()
    density: str = "compact"
    page_size: int = 200
    revision: int = 0

    def with_conditions(self, conditions: tuple[FilterCondition, ...]) -> "TableViewState":
        return replace(self, conditions=tuple(conditions))

    def with_sort_rules(self, sort_rules: tuple[SortRule, ...]) -> "TableViewState":
        return replace(self, sort_rules=tuple(sort_rules))

    def next_revision(self) -> "TableViewState":
        return replace(self, revision=self.revision + 1)


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    kind: ColumnKind
    nullable: bool
    distinct_count: int
    values: tuple[Any, ...] = ()


@dataclass(frozen=True)
class TableViewResult:
    source_positions: np.ndarray
    source_total: int
    matched_total: int
    state: TableViewState

    def __post_init__(self) -> None:
        if self.source_total < 0:
            raise ValueError("source_total must be greater than or equal to zero")
        positions = np.asarray(self.source_positions)
        if positions.ndim != 1:
            raise ValueError("source_positions must be a one-dimensional array")
        positions = np.array(positions, dtype=np.int64, order="C", copy=True)
        if len(positions) != self.matched_total:
            raise ValueError("matched_total must equal the number of source positions")
        if self.matched_total < 0:
            raise ValueError("matched_total must be greater than or equal to zero")
        if len(positions) and (
            int(positions.min()) < 0 or int(positions.max()) >= self.source_total
        ):
            raise ValueError("source position is outside source_total")
        if len(np.unique(positions)) != len(positions):
            raise ValueError("source positions must not contain duplicates")
        positions.setflags(write=False)
        object.__setattr__(self, "source_positions", positions)


@dataclass
class RowWindow:
    dataframe: pd.DataFrame
    source_positions: tuple[int, ...]
    offset: int
    limit: int
    matched_total: int


__all__ = [
    "ColumnKind",
    "ColumnProfile",
    "ColumnViewState",
    "FilterCondition",
    "RowWindow",
    "SortRule",
    "TableViewResult",
    "TableViewState",
]
