"""Data-only contracts for read-only, explicitly configured result links."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any


_RATING_FIELD = re.compile(r"(?:score|tag)[0-9]+\Z")
_WIDE_RATING_FIELD = re.compile(
    r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.(?:score[0-9]+|tag[0-9]+|notes|time)\Z"
)
_LONG_MASTER_FIELD = re.compile(
    r"(?:master\.)+(?:easyqcid|module_name|rater|score[0-9]+|tag[0-9]+|notes|time)\Z"
)
_RESERVED_OUTPUTS = frozenset({"easyqcid", "module_name", "rater", "notes", "time"})


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be nonblank text without surrounding whitespace")
    return value


@dataclass(frozen=True, slots=True)
class ResultAssociationRule:
    """One source module/rater projected through exact ordinary-column keys.

    Construction and JSON conversion are side-effect free. Invalid structure,
    names or rating-field mappings raise ValueError; Core validates filter
    semantics and columns. Nested input/output filter dictionaries are copied.
    """

    rule_id: str
    name: str
    source_module: str
    source_rater: str
    source_key: str
    target_key: str
    fields: tuple[tuple[str, str], ...]
    source_filter: dict[str, Any] | None = None
    target_filter: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        for label in ("rule_id", "name", "source_module", "source_rater", "source_key", "target_key"):
            _text(getattr(self, label), label)
        if not isinstance(self.fields, (list, tuple)) or not self.fields:
            raise ValueError("fields must contain at least one source/output pair")
        normalized = []
        outputs = set()
        for pair in self.fields:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError("fields must contain source/output pairs")
            source, output = pair
            _text(source, "source field")
            _text(output, "output name")
            if source != "notes" and _RATING_FIELD.fullmatch(source) is None:
                raise ValueError(f"unsupported source field: {source!r}; use scoreN, tagN or notes")
            if (
                output in _RESERVED_OUTPUTS
                or _RATING_FIELD.fullmatch(output)
                or _WIDE_RATING_FIELD.fullmatch(output)
                or _LONG_MASTER_FIELD.fullmatch(output)
            ):
                raise ValueError(f"reserved output name: {output!r}")
            if output in outputs:
                raise ValueError(f"duplicate output name: {output!r}")
            outputs.add(output)
            normalized.append((source, output))
        object.__setattr__(self, "fields", tuple(normalized))
        for label in ("source_filter", "target_filter"):
            value = getattr(self, label)
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"{label} must be a filter JSON object or null")
            object.__setattr__(self, label, deepcopy(value))

    def to_dict(self) -> dict[str, Any]:
        """Return a detached settings JSON object without changing this rule."""

        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "source_module": self.source_module,
            "source_rater": self.source_rater,
            "source_key": self.source_key,
            "target_key": self.target_key,
            "fields": [list(pair) for pair in self.fields],
            "source_filter": deepcopy(self.source_filter),
            "target_filter": deepcopy(self.target_filter),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ResultAssociationRule:
        """Read one rule object, rejecting unknown/missing fields; no mutation."""

        if not isinstance(payload, Mapping):
            raise ValueError("association rule must be an object")
        required = {"rule_id", "name", "source_module", "source_rater", "source_key", "target_key", "fields"}
        allowed = required | {"source_filter", "target_filter"}
        if required - payload.keys():
            raise ValueError(f"association rule missing fields: {sorted(required - payload.keys())}")
        if payload.keys() - allowed:
            raise ValueError(f"association rule has unexpected fields: {list(payload.keys() - allowed)}")
        return cls(**dict(payload))


@dataclass(frozen=True, slots=True)
class AssociationSource:
    """Exact source identity retained for deliberate read-only navigation."""

    rule_id: str
    rule_name: str
    easyqcid: str
    module_name: str
    rater: str
    has_rating: bool


__all__ = ["AssociationSource", "ResultAssociationRule"]
