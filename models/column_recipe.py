"""Immutable contracts for safe, ephemeral derived-column recipes."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, TypeAlias, Union


RecipeScalar: TypeAlias = str | int | float | bool | None
RecipeParameter: TypeAlias = Union[RecipeScalar, "RecipeValue"]

_VALUE_KINDS = frozenset({"current", "column", "literal"})
_ERROR_POLICIES = frozenset({"fail", "blank", "keep"})
_SAFE_SCALAR_TYPES = (str, int, float, bool, type(None))
_UNSET = object()


def _require_nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{label}必须是非空文本")
    return value


def _validate_scalar(value: object) -> RecipeScalar:
    if not isinstance(value, _SAFE_SCALAR_TYPES):
        raise TypeError("固定值必须是文本、数值、布尔值或空值等安全标量")
    return value


@dataclass(frozen=True, slots=True)
class RecipeValue:
    """A typed reference to the current value, another column, or a scalar."""

    kind: str
    value: RecipeScalar = None

    def __post_init__(self) -> None:
        if self.kind not in _VALUE_KINDS:
            raise ValueError(f"不支持的配方值类型: {self.kind}")
        if self.kind == "current":
            if self.value is not None:
                raise ValueError("当前值引用不能携带额外内容")
            return
        if self.kind == "column":
            _require_nonblank(self.value, "列名")
            return
        _validate_scalar(self.value)

    @classmethod
    def current(cls) -> "RecipeValue":
        return cls("current")

    @classmethod
    def column(cls, name: str) -> "RecipeValue":
        return cls("column", _require_nonblank(name, "列名"))

    @classmethod
    def literal(cls, value: object) -> "RecipeValue":
        return cls("literal", _validate_scalar(value))


@dataclass(frozen=True, slots=True)
class RecipeStep:
    """One operation in an ordered derived-column recipe."""

    operation: str
    parameters: Mapping[str, RecipeParameter]
    on_error: str = "fail"

    def __post_init__(self) -> None:
        _require_nonblank(self.operation, "操作")
        if self.on_error not in _ERROR_POLICIES:
            raise ValueError(f"不支持的错误策略: {self.on_error}")
        if not isinstance(self.parameters, Mapping):
            raise TypeError("步骤参数必须是键值映射")

        detached: dict[str, RecipeParameter] = {}
        for key, value in self.parameters.items():
            _require_nonblank(key, "参数名")
            if isinstance(value, RecipeValue):
                detached[key] = value
            else:
                detached[key] = _validate_scalar(value)
        object.__setattr__(self, "parameters", MappingProxyType(detached))

    @classmethod
    def create(
        cls,
        operation: str,
        *,
        on_error: str = "fail",
        **parameters: object,
    ) -> "RecipeStep":
        return cls(
            operation=operation,
            parameters=parameters,
            on_error=on_error,
        )


@dataclass(frozen=True, slots=True, init=False)
class ColumnRecipe:
    """A row-preserving request to materialize one ordinary table column."""

    name: str
    initial_value: RecipeValue
    steps: tuple[RecipeStep, ...]

    def __init__(
        self,
        name: str,
        source_column: object = _UNSET,
        steps: tuple[RecipeStep, ...] = (),
        *,
        initial_value: object = _UNSET,
    ) -> None:
        """Build a recipe from a legacy source column or a typed start value.

        ``source_column`` remains accepted so saved callers and extensions do
        not need to migrate in lockstep. Internally every recipe has exactly
        one typed ``initial_value``.
        """

        normalized_name = _require_nonblank(name, "新列名")
        if source_column is not _UNSET and initial_value is not _UNSET:
            raise ValueError("来源列和起始值只能选择一种")
        if source_column is _UNSET and initial_value is _UNSET:
            raise ValueError("必须设置来源列或起始值")

        if initial_value is not _UNSET:
            if not isinstance(initial_value, RecipeValue):
                raise TypeError("起始值必须是列或固定值")
            normalized_start = initial_value
        else:
            normalized_start = RecipeValue.column(source_column)
        if normalized_start.kind == "current":
            raise ValueError("起始值不能引用尚不存在的当前值")

        normalized_steps = tuple(steps)
        if any(not isinstance(step, RecipeStep) for step in normalized_steps):
            raise TypeError("转换步骤必须是 RecipeStep")
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "initial_value", normalized_start)
        object.__setattr__(self, "steps", normalized_steps)

    @property
    def source_column(self) -> str | None:
        """Return the legacy source column when this recipe starts from one."""

        if self.initial_value.kind != "column":
            return None
        return str(self.initial_value.value)


__all__ = [
    "ColumnRecipe",
    "RecipeParameter",
    "RecipeScalar",
    "RecipeStep",
    "RecipeValue",
]
