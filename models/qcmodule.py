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


def _parse_interper(value: Any) -> str:
    interper = "direct" if value is None else str(value)
    if interper not in ("direct", "shell"):
        raise ValueError(
            f"module command interper must be 'direct' or 'shell': {interper!r}"
        )
    return interper


def _parse_command_groups(
    value: Any,
) -> dict[str, CommandGroup] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("module commands must be an object")
    groups: dict[str, CommandGroup] = {}
    for key, item in value.items():
        if not isinstance(item, dict):
            raise ValueError("module command groups must be objects")
        groups[str(key)] = CommandGroup.from_legacy_dict(str(key), item)
    return groups


def ordered_command_groups(
    groups: dict[str, "CommandGroup"] | None,
) -> list["CommandGroup"]:
    """Return command groups in execution order.

    数字键按数值升序（GUI 保存时总是顺序重编号）；任何非数字键则整体
    保持插入顺序，与 from_legacy_dict 的保序语义一致。
    """

    if not groups:
        return []
    items = list(groups.items())
    try:
        items.sort(key=lambda item: int(item[0]))
    except ValueError:
        pass
    return [group for _key, group in items]


def normalize_module_commands(module: "QCModule") -> None:
    """Collapse blank command groups and single-group payloads in place.

    空白组丢弃；0 组 → code/commands 置空；1 组 → 折叠回 code/interper
    （与旧格式字节级同构，不写新键）；≥2 组 → 保留原键 commands、code
    置空、interper 取首组模式。core 保存门与模板编辑器共用此规则。
    """

    if not module.commands:
        return
    kept = [
        group
        for group in ordered_command_groups(module.commands)
        if isinstance(group.code, str) and group.code.strip()
    ]
    if not kept:
        module.commands = None
        module.code = None
    elif len(kept) == 1:
        module.code = kept[0].code
        module.interper = kept[0].interper
        module.commands = None
    else:
        kept_ids = {id(group) for group in kept}
        module.commands = {
            key: group
            for key, group in module.commands.items()
            if id(group) in kept_ids
        }
        module.code = None
        module.interper = kept[0].interper


@dataclass
class Score:
    key: str
    label: str
    num: str
    num_: str
    value: str | None = None

    PATTERNS: ClassVar[dict[str, str]] = {
        "range": r"^\s*(\d+)\s*-\s*(\d+)\s*$",
        "labels": r"^[^,\r\n]+(?:,[^,\r\n]+)+$",
        "single": r"^\s*(\d+)\s*$",
    }

    @staticmethod
    def parse_num(raw: str) -> list[str] | str | None:
        value = raw.strip()
        if not value:
            return None

        if "," in value:
            if re.fullmatch(Score.PATTERNS["labels"], value) is None:
                return None
            labels = [label.strip() for label in value.split(",")]
            if any(not label for label in labels):
                return None
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
class CommandGroup:
    """One viewer command with its own execution mode (shell/direct)."""

    key: str
    code: str | None = None
    interper: str = "direct"

    @classmethod
    def from_legacy_dict(cls, key: str, data: dict[str, Any]) -> "CommandGroup":
        code = data.get("code")
        if not isinstance(code, str):
            raise ValueError("module command group code must be a string")
        return cls(
            key=str(key),
            code=code,
            interper=_parse_interper(data.get("interper", "direct")),
        )

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "interper": self.interper,
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
    interper: str = "direct"
    commands: dict[str, CommandGroup] | None = None
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
            interper=data.get("interper", "direct"),
            commands=_parse_command_groups(data.get("commands")),
            control=bool(data.get("control", False)),
            showing=bool(data.get("showing", True)),
            select_filter=data.get("select_filter"),
            qc_filter=deepcopy(data.get("qc_filter")),
            button=data.get("button", {}),
        )

    def to_legacy_dict(self) -> dict[str, Any]:
        payload = {
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
        # 单组命令仍落在 code/interper（旧格式字节级同构）；仅多组才写新键。
        if self.commands:
            payload["commands"] = {
                key: group.to_legacy_dict()
                for key, group in self.commands.items()
            }
        return payload


__all__ = [
    "CommandGroup",
    "QCModule",
    "Score",
    "Tag",
    "_format_datetime",
    "_parse_datetime",
    "normalize_module_commands",
    "ordered_command_groups",
]
