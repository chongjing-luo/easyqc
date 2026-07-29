from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, ClassVar


def _parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _normalize_num_list(value: Any) -> str:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _parse_tag_value(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("module tag value must be a JSON Boolean")
    return value


@dataclass
class Score:
    key: str
    label: str
    num: str
    num_: str
    value: str | None = None

    PATTERNS: ClassVar[dict[str, str]] = {
        "range": r"^\s*(\d+)\s*-\s*(\d+)\s*$",
        "labels": r"^\s*[a-zA-Z0-9_ ]+\s*(,\s*[a-zA-Z0-9_ ]+\s*)*,?\s*$",
        "single": r"^\s*(\d+)\s*$",
    }

    @staticmethod
    def parse_num(raw: str) -> list[str] | str | None:
        value = raw.strip()
        if not value:
            return None

        if re.fullmatch(Score.PATTERNS["labels"], value) and "," in value:
            labels = [label.strip() for label in value.split(",")]
            if len(labels) != len(set(labels)):
                return None
            return labels

        range_match = re.fullmatch(Score.PATTERNS["range"], value)
        if range_match is not None:
            start = int(range_match.group(1))
            end = int(range_match.group(2))
            if start > end:
                return None
            return ",".join(str(number) for number in range(start, end + 1))

        single_match = re.fullmatch(Score.PATTERNS["single"], value)
        if single_match is not None:
            maximum = int(single_match.group(1))
            if maximum <= 0:
                return None
            return ",".join(str(number) for number in range(1, maximum + 1))

        return None

    @property
    def allowed_values(self) -> list[str]:
        return [value.strip() for value in self.num_.split(",") if value.strip()]

    @classmethod
    def from_legacy_dict(cls, key: str, data: dict[str, Any]) -> "Score":
        return cls(
            key=str(key),
            label=data.get("label", ""),
            num=data.get("num", ""),
            num_=_normalize_num_list(data.get("num_", "")),
            value=data.get("value"),
        )

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "num": self.num,
            "num_": self.num_,
            "value": self.value,
        }


@dataclass
class Tag:
    key: str
    label: str
    value: bool = False

    @classmethod
    def from_legacy_dict(cls, key: str, data: dict[str, Any]) -> "Tag":
        return cls(
            key=str(key),
            label=data.get("label", ""),
            value=_parse_tag_value(data.get("value", False)),
        )

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": self.value,
        }


@dataclass
class QCModule:
    name: str
    label: str
    rater: str | None = None
    easyqcid: str | None = None
    watch_mode: bool = False
    scores: dict[str, Score] = field(default_factory=dict)
    tags: dict[str, Tag] = field(default_factory=dict)
    code: str | None = None
    code_exe: dict[str, str] | None = None
    notes: str | None = None
    time: datetime | None = None
    interper: str = "shell"
    control: bool = False
    showing: bool = True
    select_filter: str | None = None
    qc_filter: dict[str, Any] | None = None
    button: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_legacy_dict(cls, data: dict[str, Any]) -> "QCModule":
        return cls(
            name=data["name"],
            label=data.get("label", data["name"]),
            rater=data.get("rater"),
            easyqcid=data.get("easyqcid"),
            watch_mode=bool(data.get("watch_mode", False)),
            scores={
                str(key): Score.from_legacy_dict(str(key), value)
                for key, value in data.get("scores", {}).items()
            },
            tags={
                str(key): Tag.from_legacy_dict(str(key), value)
                for key, value in data.get("tags", {}).items()
            },
            code=data.get("code"),
            code_exe={str(key): value for key, value in data.get("code_exe", {}).items()}
            if isinstance(data.get("code_exe"), dict)
            else data.get("code_exe"),
            notes=data.get("notes"),
            time=_parse_datetime(data.get("time")),
            interper=data.get("interper", "shell"),
            control=bool(data.get("control", False)),
            showing=bool(data.get("showing", True)),
            select_filter=data.get("select_filter"),
            qc_filter=deepcopy(data.get("qc_filter")),
            button=data.get("button", {}),
        )

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "rater": self.rater,
            "easyqcid": self.easyqcid,
            "watch_mode": self.watch_mode,
            "tags": {key: tag.to_legacy_dict() for key, tag in self.tags.items()},
            "scores": {key: score.to_legacy_dict() for key, score in self.scores.items()},
            "code": self.code,
            "interper": self.interper,
            "control": self.control,
            "select_filter": self.select_filter,
            "qc_filter": deepcopy(self.qc_filter),
            "showing": self.showing,
            "code_exe": self.code_exe,
            "time": _format_datetime(self.time),
            "notes": self.notes,
            "button": self.button,
        }


__all__ = ["QCModule", "Score", "Tag", "_format_datetime", "_parse_datetime"]
