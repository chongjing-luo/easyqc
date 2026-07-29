from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


class _ModuleLike(Protocol):
    name: str
    rater: str | None
    easyqcid: str | None
    scores: dict[str, Any]
    tags: dict[str, Any]
    notes: str | None
    time: datetime | None
    code_exe: dict[str, str] | None

    def to_legacy_dict(self) -> dict[str, Any]: ...


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


def _parse_tag_value(value: Any) -> bool:
    if type(value) is not bool:
        raise ValueError("rating tag value must be a JSON Boolean")
    return value


@dataclass
class Rating:
    module_name: str
    rater: str
    easyqcid: str
    scores: dict[str, Any]
    tags: dict[str, bool]
    notes: str | None = None
    time: datetime | None = None
    code_exe: dict[str, str] | None = None
    module_payload: dict[str, Any] | None = None

    @property
    def filename(self) -> str:
        """Stable basename; Core validates identities and complete paths."""

        return f"{self.module_name}-{self.rater}-{self.easyqcid}.json"

    @classmethod
    def from_module(cls, module: _ModuleLike) -> "Rating":
        return cls(
            module_name=module.name,
            rater=module.rater or "",
            easyqcid=module.easyqcid or "",
            scores={key: score.value for key, score in module.scores.items()},
            tags={key: tag.value for key, tag in module.tags.items()},
            notes=module.notes,
            time=module.time,
            code_exe=module.code_exe,
            module_payload=module.to_legacy_dict(),
        )

    @classmethod
    def from_legacy_dict(cls, data: dict[str, Any]) -> "Rating":
        return cls(
            module_name=data["name"],
            rater=data["rater"],
            easyqcid=data["easyqcid"],
            scores={
                str(key): value.get("value") if isinstance(value, dict) else value
                for key, value in data.get("scores", {}).items()
            },
            tags={
                str(key): _parse_tag_value(
                    value.get("value", False)
                    if isinstance(value, dict)
                    else value
                )
                for key, value in data.get("tags", {}).items()
            },
            notes=data.get("notes"),
            time=_parse_datetime(data.get("time")),
            code_exe={str(key): value for key, value in data.get("code_exe", {}).items()}
            if isinstance(data.get("code_exe"), dict)
            else data.get("code_exe"),
            module_payload=data.copy(),
        )

    def to_legacy_dict(
        self,
        legacy_module: _ModuleLike | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if legacy_module is None:
            data = self.module_payload.copy() if self.module_payload else {}
        elif isinstance(legacy_module, dict):
            data = legacy_module.copy()
        else:
            to_legacy_dict = getattr(legacy_module, "to_legacy_dict", None)
            if not callable(to_legacy_dict):
                raise TypeError("legacy_module must be a mapping or typed module")
            converted = to_legacy_dict()
            if not isinstance(converted, dict):
                raise TypeError("typed module legacy payload must be a mapping")
            data = converted.copy()

        data["name"] = self.module_name
        data["rater"] = self.rater
        data["easyqcid"] = self.easyqcid
        data["notes"] = self.notes
        data["time"] = _format_datetime(self.time)
        data["code_exe"] = self.code_exe
        data.setdefault("scores", {})
        data.setdefault("tags", {})

        for key, value in self.scores.items():
            data["scores"].setdefault(key, {})
            if isinstance(data["scores"][key], dict):
                data["scores"][key]["value"] = value
            else:
                data["scores"][key] = value

        for key, value in self.tags.items():
            data["tags"].setdefault(key, {})
            if isinstance(data["tags"][key], dict):
                data["tags"][key]["value"] = value
            else:
                data["tags"][key] = value

        return data

    def apply_to_module(self, module: _ModuleLike) -> None:
        module.easyqcid = self.easyqcid
        module.rater = self.rater
        module.notes = self.notes
        module.time = self.time
        module.code_exe = self.code_exe

        for key, value in self.scores.items():
            if key in module.scores:
                module.scores[key].value = value
        for key, value in self.tags.items():
            if key in module.tags:
                module.tags[key].value = value

__all__ = ["Rating"]
