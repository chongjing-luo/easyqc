from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any

from models.qcmodule import Score


_SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


def validate_score(value: str) -> list[str] | str | None:
    return Score.parse_num(value)


def validate_filename(name: str) -> bool:
    if not name or name in {".", ".."}:
        return False
    path = PurePath(name)
    if len(path.parts) != 1:
        return False
    return bool(_SAFE_NAME_PATTERN.match(name))


def validate_project_name(name: str) -> bool:
    return validate_filename(name)


def validate_module_name(name: str) -> bool:
    return validate_filename(name)


def validate_transform_operation(op: dict[str, Any]) -> bool:
    if not isinstance(op, dict):
        return False

    operation = op.get("operation") or op.get("type")
    if operation not in {
        "select_columns",
        "filter_rows",
        "sort_rows",
        "derive_column",
        "rename_columns",
        "drop_columns",
        "merge_tables",
        "aggregate",
    }:
        return False

    if operation == "select_columns":
        return _is_string_list(op.get("columns"))
    if operation == "filter_rows":
        conditions = op.get("conditions")
        return isinstance(conditions, list) and all(isinstance(condition, dict) for condition in conditions)
    if operation == "sort_rows":
        sort_keys = op.get("sort_keys")
        return isinstance(sort_keys, list) and all(
            isinstance(sort_key, dict) and isinstance(sort_key.get("column"), str)
            for sort_key in sort_keys
        )
    if operation == "derive_column":
        name = op.get("name")
        expression = op.get("expression")
        return bool(name) and isinstance(name, str) and bool(expression) and isinstance(expression, str)
    if operation == "rename_columns":
        mapping = op.get("mapping")
        return isinstance(mapping, dict) and all(
            isinstance(old_name, str) and isinstance(new_name, str) and bool(new_name)
            for old_name, new_name in mapping.items()
        )
    if operation == "drop_columns":
        return _is_string_list(op.get("columns"))
    if operation == "merge_tables":
        return (
            "right" in op
            and _is_string_list(op.get("on"))
            and op.get("how", "left") in {"left", "right", "inner", "outer"}
            and op.get("relationship")
            in {
                None,
                "one_to_one",
                "one_to_many",
                "many_to_one",
                "many_to_many",
            }
        )
    if operation == "aggregate":
        metrics = op.get("metrics")
        return _is_string_list(op.get("group_by")) and isinstance(metrics, dict) and all(
            isinstance(column, str)
            and isinstance(functions, list)
            and all(isinstance(function, str) for function in functions)
            for column, functions in metrics.items()
        )

    return False


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
