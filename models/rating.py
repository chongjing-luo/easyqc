from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from models.qcmodule import QCModule, _format_datetime, _parse_datetime
from utils.file_utils import FileUtils


@dataclass
class Rating:
    module_name: str
    rater: str
    ezqcid: str
    scores: dict[str, Any]
    tags: dict[str, bool]
    notes: str | None = None
    time: datetime | None = None
    code_exe: dict[str, str] | None = None
    legacy_payload: dict[str, Any] | None = None

    @property
    def filename(self) -> str:
        score1 = self.scores.get("1", "None")
        tag1 = self.tags.get("1", False)
        return f"{self.module_name}._.{self.ezqcid}._.{self.rater}._.{score1}._.{tag1}.json"

    @classmethod
    def from_module(cls, module: QCModule) -> "Rating":
        return cls(
            module_name=module.name,
            rater=module.rater or "",
            ezqcid=module.ezqcid or "",
            scores={key: score.value for key, score in module.scores.items()},
            tags={key: tag.value for key, tag in module.tags.items()},
            notes=module.notes,
            time=module.time,
            code_exe=module.code_exe,
            legacy_payload=module.to_legacy_dict(),
        )

    @classmethod
    def from_legacy_dict(cls, data: dict[str, Any]) -> "Rating":
        return cls(
            module_name=data["name"],
            rater=data["rater"],
            ezqcid=data["ezqcid"],
            scores={
                str(key): value.get("value") if isinstance(value, dict) else value
                for key, value in data.get("scores", {}).items()
            },
            tags={
                str(key): bool(value.get("value", False)) if isinstance(value, dict) else bool(value)
                for key, value in data.get("tags", {}).items()
            },
            notes=data.get("notes"),
            time=_parse_datetime(data.get("time")),
            code_exe={str(key): value for key, value in data.get("code_exe", {}).items()}
            if isinstance(data.get("code_exe"), dict)
            else data.get("code_exe"),
            legacy_payload=data.copy(),
        )

    def to_legacy_dict(self, legacy_module: QCModule | dict[str, Any] | None = None) -> dict[str, Any]:
        if legacy_module is None:
            data = self.legacy_payload.copy() if self.legacy_payload else {}
        elif isinstance(legacy_module, QCModule):
            data = legacy_module.to_legacy_dict()
        else:
            data = legacy_module.copy()

        data["name"] = self.module_name
        data["rater"] = self.rater
        data["ezqcid"] = self.ezqcid
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

    def apply_to_module(self, module: QCModule) -> None:
        module.ezqcid = self.ezqcid
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

    @classmethod
    def from_json_file(cls, path: Path) -> "Rating":
        return cls.from_legacy_dict(FileUtils.safe_json_load(path))

    def to_json_file(self, path: Path, legacy_module: QCModule | dict[str, Any] | None = None) -> None:
        FileUtils.safe_json_save(path, self.to_legacy_dict(legacy_module))


__all__ = ["Rating"]
