from utils.validators import (
    validate_filename,
    validate_module_name,
    validate_project_name,
    validate_score,
    validate_transform_operation,
)


def test_validate_score_matches_legacy_formats() -> None:
    assert validate_score("Poor,Fair,Good") == ["Poor", "Fair", "Good"]
    assert validate_score("0-3") == "0,1,2,3"
    assert validate_score("3") == "1,2,3"


def test_validate_score_rejects_invalid_values() -> None:
    assert validate_score("") is None
    assert validate_score("Good,Good") is None
    assert validate_score("3-1") is None
    assert validate_score("0") is None


def test_validate_safe_names_reject_path_traversal() -> None:
    assert validate_project_name("ADHDNORM")
    assert validate_module_name("t1-qc")
    assert validate_filename("rating_001.json")

    assert not validate_project_name("../ADHDNORM")
    assert not validate_module_name("module/name")
    assert not validate_filename("bad name with spaces")


def test_validate_transform_operation_checks_required_fields() -> None:
    assert validate_transform_operation({"operation": "select_columns", "columns": ["ezqcid"]})
    assert validate_transform_operation(
        {"operation": "derive_column", "name": "qc_pass", "expression": "score >= 3"}
    )
    assert validate_transform_operation(
        {"operation": "merge_tables", "right": object(), "on": ["ezqcid"], "how": "left"}
    )
    assert validate_transform_operation(
        {"operation": "aggregate", "group_by": ["site"], "metrics": {"score": ["mean"]}}
    )

    assert not validate_transform_operation({"operation": "select_columns"})
    assert not validate_transform_operation({"operation": "select_columns", "columns": "ezqcid"})
    assert not validate_transform_operation({"operation": "filter_rows", "conditions": ["bad"]})
    assert not validate_transform_operation({"operation": "sort_rows", "sort_keys": [{"ascending": True}]})
    assert not validate_transform_operation({"operation": "derive_column", "name": "qc_pass"})
    assert not validate_transform_operation({"operation": "rename_columns", "mapping": {"old": ""}})
    assert not validate_transform_operation({"operation": "merge_tables", "on": ["ezqcid"], "how": "left"})
    assert not validate_transform_operation({"operation": "merge_tables", "on": ["ezqcid"], "how": "cross"})
    assert not validate_transform_operation({"operation": "aggregate", "group_by": ["site"], "metrics": {"score": "mean"}})
    assert not validate_transform_operation({"operation": "unknown"})
