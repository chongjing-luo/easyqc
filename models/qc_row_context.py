"""Immutable facts rendered by QC row context menus."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}不能为空")
    return value.strip()


@dataclass(frozen=True, slots=True)
class QcModuleMenuEntry:
    """One configured module and its applicability to the clicked identity."""

    module_name: str
    label: str
    enabled: bool
    disabled_reason: str = ""
    read_only: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "module_name",
            _required_text(self.module_name, "质控模块名"),
        )
        object.__setattr__(self, "label", _required_text(self.label, "质控模块标签"))
        reason = str(self.disabled_reason).strip()
        if not self.enabled and not reason:
            raise ValueError("禁用的质控模块菜单项必须说明原因")
        object.__setattr__(self, "disabled_reason", reason)


@dataclass(frozen=True, slots=True)
class QcRecordMenuEntry:
    """An exact historical record key available in the accepted snapshot."""

    ezqcid: str
    module_name: str
    module_label: str
    rater: str
    recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ezqcid", _required_text(self.ezqcid, "ezqcid"))
        object.__setattr__(
            self,
            "module_name",
            _required_text(self.module_name, "质控模块名"),
        )
        object.__setattr__(
            self,
            "module_label",
            _required_text(self.module_label, "质控模块标签"),
        )
        object.__setattr__(self, "rater", _required_text(self.rater, "评分者"))
        if self.recorded_at is not None and not isinstance(
            self.recorded_at,
            datetime,
        ):
            raise TypeError("质控记录时间必须是 datetime 或空值")

    @property
    def key(self) -> tuple[str, str, str]:
        return self.ezqcid, self.module_name, self.rater


@dataclass(frozen=True, slots=True)
class QcRowContext:
    """All menu facts for one exact row identity."""

    ezqcid: str
    modules: tuple[QcModuleMenuEntry, ...]
    records: tuple[QcRecordMenuEntry, ...]

    def __post_init__(self) -> None:
        identity = _required_text(self.ezqcid, "ezqcid")
        object.__setattr__(self, "ezqcid", identity)
        if any(not isinstance(entry, QcModuleMenuEntry) for entry in self.modules):
            raise TypeError("质控模块菜单必须由 QcModuleMenuEntry 组成")
        if any(not isinstance(entry, QcRecordMenuEntry) for entry in self.records):
            raise TypeError("质控记录菜单必须由 QcRecordMenuEntry 组成")
        if any(entry.ezqcid != identity for entry in self.records):
            raise ValueError("质控记录与右键行 ezqcid 不一致")
        module_names = [entry.module_name for entry in self.modules]
        if len(module_names) != len(set(module_names)):
            raise ValueError("质控模块菜单包含重复模块")
        record_keys = [entry.key for entry in self.records]
        if len(record_keys) != len(set(record_keys)):
            raise ValueError("质控记录菜单包含重复记录")


__all__ = [
    "QcModuleMenuEntry",
    "QcRecordMenuEntry",
    "QcRowContext",
]
