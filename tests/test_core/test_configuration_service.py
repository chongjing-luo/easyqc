from __future__ import annotations

from copy import deepcopy
import json

import pandas as pd
import pytest

from core.configuration_service import ConfigurationError, ConfigurationService
from core.event_bus import EventType
from core.module_filter import resolve_module_filter_identities
from core.project_service import ProjectService
from core.table_service import TableService, TableStateConflictError
from core.table_view_service import TableViewService
from models.derived_formula import DerivedColumnFormula
from models.table_view_state import (
    FilterCondition,
    FilterExpression,
    FilterGroup,
)
from utils.file_utils import FileUtils


def _service(tmp_path):
    projects = ProjectService(tmp_path / "projects.json")
    service = ConfigurationService(projects, TableService())
    service.create_project("SAMPLE", tmp_path)
    return service, projects


def _subjects():
    return pd.DataFrame(
        {"easyqcid": ["SUB001", "SUB002"], "site": ["A", "B"], "age": [29, 31]}
    )


def _filter_expression(
    column: str,
    operator: str,
    value,
    *,
    condition_id: str = "condition-1",
) -> FilterExpression:
    return FilterExpression(
        group_join="all",
        groups=(
            FilterGroup(
                group_id="group-1",
                join="all",
                conditions=(
                    FilterCondition(
                        column,
                        operator,
                        value,
                        condition_id,
                        True,
                    ),
                ),
            ),
        ),
    )


def test_project_and_subject_configuration_use_temporary_atomic_files(tmp_path) -> None:
    service, projects = _service(tmp_path)

    service.replace_subjects(_subjects())

    result = service.subjects()
    pd.testing.assert_frame_equal(result, _subjects())
    assert projects.current_project.name == "SAMPLE"
    assert projects.current_project.settings_path.exists()
    assert (projects.current_project.table_dir / "easyqc_all.csv").exists()


def test_replace_subjects_rejects_a_concurrent_newer_table(
    tmp_path,
    monkeypatch,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    real_validate = service.validate_subjects
    raced = False
    newer = pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB002"],
            "site": ["NEW", "NEW"],
            "age": [40, 41],
        }
    )

    def validate_during_concurrent_write(frame):
        nonlocal raced
        validated = real_validate(frame)
        if not raced:
            raced = True
            service.table_service.save_table(
                projects.current_project,
                "easyqc_all",
                newer,
            )
        return validated

    monkeypatch.setattr(service, "validate_subjects", validate_during_concurrent_write)

    with pytest.raises(TableStateConflictError, match="stale"):
        service.replace_subjects(_subjects().assign(site=["OLD", "OLD"]))

    pd.testing.assert_frame_equal(service.subjects(), newer)


def test_derive_subject_column_persists_values_once_and_publishes_change(
    tmp_path,
    monkeypatch,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    save_calls = []
    original_save = service.table_service.save_table

    def tracked_save(*args, **kwargs):
        save_calls.append((args, kwargs))
        return original_save(*args, **kwargs)

    monkeypatch.setattr(service.table_service, "save_table", tracked_save)
    events = []
    service.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        events.append,
    )
    request = DerivedColumnFormula("age_next", "[age] + 1")

    result = service.derive_subject_column(request)

    assert result == "age_next"
    assert len(save_calls) == 1
    assert service.subjects()["age_next"].tolist() == [30, 32]
    assert events and events[-1].source == "ConfigurationService"
    csv_text = (
        projects.current_project.table_dir / "easyqc_all.csv"
    ).read_text(encoding="utf-8")
    assert "age_next" in csv_text.splitlines()[0]
    assert "age + 1" not in csv_text


@pytest.mark.parametrize(
    ("formula_request", "match"),
    [
        (
            DerivedColumnFormula("age", "[age]"),
            "已存在",
        ),
        (
            DerivedColumnFormula("unsafe", 'PYTHON("[age]")'),
            "未知函数",
        ),
        (
            DerivedColumnFormula("easyqcid", "[age]"),
            "easyqcid",
        ),
        (None, "DerivedColumnFormula"),
    ],
)
def test_derive_subject_column_rejects_invalid_request_without_writing(
    tmp_path,
    formula_request,
    match,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    table_path = projects.current_project.table_dir / "easyqc_all.csv"
    before = table_path.read_bytes()

    with pytest.raises(ConfigurationError, match=match):
        service.derive_subject_column(formula_request)

    assert table_path.read_bytes() == before
    pd.testing.assert_frame_equal(service.subjects(), _subjects())


def test_derive_subject_column_failed_row_policy_keeps_csv_byte_identical(
    tmp_path,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(
        pd.DataFrame(
            {
                "easyqcid": ["SUB001", "SUB002"],
                "label": ["4", "bad"],
            }
        ),
        notify=False,
    )
    table_path = projects.current_project.table_dir / "easyqc_all.csv"
    before = table_path.read_bytes()
    request = DerivedColumnFormula(
        "identifier",
        "VALUE([label])",
    )

    with pytest.raises(ConfigurationError, match=r"1 行"):
        service.derive_subject_column(request)

    assert table_path.read_bytes() == before


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame({"easyqcid": ["SUB001", "SUB001"]}),
        pd.DataFrame({"easyqcid": ["SUB001", " "]}),
        pd.DataFrame({"subject": ["SUB001"]}),
    ],
)
def test_subject_configuration_rejects_unsafe_identity(tmp_path, frame) -> None:
    service, _ = _service(tmp_path)

    with pytest.raises(ConfigurationError):
        service.replace_subjects(frame)


def test_subject_merge_and_constant_column_collisions_fail_loud(tmp_path) -> None:
    service, _ = _service(tmp_path)
    service.replace_subjects(_subjects())
    service.set_constant("DATA_ROOT", "/data")

    with pytest.raises(ConfigurationError, match="overlap"):
        service.merge_subjects(
            pd.DataFrame({"easyqcid": ["SUB001"], "site": ["changed"]}),
            mode="columns",
        )
    with pytest.raises(ConfigurationError, match="column"):
        service.set_constant("site", "bad")
    with pytest.raises(ConfigurationError, match="常量"):
        service.replace_subjects(
            pd.DataFrame({"easyqcid": ["SUB001"], "DATA_ROOT": ["shadow"]})
        )


def test_project_constant_add_and_update_use_first_name_rule(tmp_path) -> None:
    service, _ = _service(tmp_path)
    service.replace_subjects(_subjects())
    service.add_constant("DATA_ROOT", "/first")

    with pytest.raises(ConfigurationError, match="DATA_ROOT"):
        service.add_constant("DATA_ROOT", "/second")

    service.update_constant("DATA_ROOT", "DATA_ROOT", "/edited")
    service.add_constant("OUTPUT_ROOT", "/output")
    with pytest.raises(ConfigurationError, match="OUTPUT_ROOT"):
        service.update_constant("DATA_ROOT", "OUTPUT_ROOT", "/renamed")

    assert service.constants() == {
        "DATA_ROOT": "/edited",
        "OUTPUT_ROOT": "/output",
    }


def test_failed_settings_commit_restores_in_memory_and_disk(monkeypatch, tmp_path) -> None:
    service, projects = _service(tmp_path)
    before = projects.current_project.settings_path.read_text(encoding="utf-8")
    real_save = FileUtils.safe_json_save

    def fail_settings(path, data, indent=4):
        if path == projects.current_project.settings_path:
            raise OSError("settings replace failed")
        return real_save(path, data, indent)

    monkeypatch.setattr(FileUtils, "safe_json_save", fail_settings)

    with pytest.raises(OSError, match="settings replace failed"):
        service.set_constant("DATA_ROOT", "/data")
    assert "DATA_ROOT" not in service.constants()
    assert projects.current_project.settings_path.read_text(encoding="utf-8") == before


def test_module_add_move_import_collision_and_atomic_export(tmp_path) -> None:
    service, _ = _service(tmp_path)
    service.add_module("AnatQC", "Anatomical QC")
    service.add_module("FuncQC", "Functional QC")

    assert service.move_module("FuncQC", -1)
    assert [module.name for module in service.modules()][1] == "FuncQC"

    imported = {
        "name": "ImportedQC",
        "label": "Imported QC",
        "rater": "external",
        "easyqcid": "SUB999",
        "watch_mode": False,
        "scores": {"1": {"label": "Quality", "num": "A,B", "num_": "A,B", "value": "A"}},
        "tags": {"1": {"label": "Artifact", "value": True}},
        "code": "freeview {image}",
        "interper": "shell",
        "control": True,
        "select_filter": None,
        "showing": True,
        "code_exe": {"0": "old"},
        "time": "2026-01-01 00:00:00",
        "notes": "old",
        "button": {},
    }
    service.import_module_payload(imported)
    module = next(item for item in service.modules() if item.name == "ImportedQC")
    assert module.easyqcid is None
    assert module.notes is None
    assert module.scores["1"].value is None
    assert module.tags["1"].value is False

    with pytest.raises(ConfigurationError, match="already exists"):
        service.import_module_payload(imported)

    output = tmp_path / "exports" / "module.json"
    service.export_module("ImportedQC", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["rater"] is None
    assert payload["easyqcid"] is None


def test_remove_project_only_unregisters_it(tmp_path) -> None:
    service, projects = _service(tmp_path)
    project_path = projects.current_project.path

    service.remove_project("SAMPLE")

    assert service.projects() == ()
    assert project_path.exists()


def test_project_entries_are_detached_and_include_path_and_open_state(tmp_path) -> None:
    service, projects = _service(tmp_path)
    service.create_project("SECOND", tmp_path)
    service.load_project("SAMPLE")

    entries = service.project_entries()

    assert tuple(entry.name for entry in entries) == ("SAMPLE", "SECOND")
    assert entries[0].path == projects.registry.projects["SAMPLE"].path
    assert entries[0].is_current
    assert entries[0].is_most_recent
    assert not entries[1].is_current
    assert not entries[1].is_most_recent
    assert service.current_project.name == "SAMPLE"
    assert service.projects() == ("SAMPLE", "SECOND")


def test_configuration_snapshot_is_detached_from_authoritative_subjects(tmp_path) -> None:
    service, _ = _service(tmp_path)
    service.replace_subjects(_subjects())

    snapshot = service.snapshot()
    snapshot.subjects.loc[0, "site"] = "changed-only-in-snapshot"

    assert snapshot.current_project_name == "SAMPLE"
    assert snapshot.projects == ("SAMPLE",)
    assert len(snapshot.modules) == 1
    assert service.subjects().loc[0, "site"] == "A"


def test_list_import_draft_readers_do_not_mutate_the_active_table(tmp_path) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects())
    table_path = projects.current_project.table_dir / "easyqc_all.csv"
    before_bytes = table_path.read_bytes()

    folder = tmp_path / "incoming-folders"
    (folder / "SUB004").mkdir(parents=True)
    (folder / "SUB003").mkdir()
    (folder / "ignored.txt").write_text("not a directory", encoding="utf-8")
    csv_path = tmp_path / "incoming.csv"
    pd.DataFrame(
        {"easyqcid": ["SUB005"], "site": ["C"], "scanner_model": ["Prisma"]}
    ).to_csv(csv_path, index=False)

    folder_draft = service.draft_from_folder(folder, "easyqcid")
    file_draft = service.draft_from_file(csv_path)
    text_draft = service.draft_from_text("SUB006, SUB007\nSUB008", "easyqcid")

    assert folder_draft.to_dict("list") == {"easyqcid": ["SUB003", "SUB004"]}
    assert file_draft.to_dict("records") == [
        {"easyqcid": "SUB005", "site": "C", "scanner_model": "Prisma"}
    ]
    assert text_draft["easyqcid"].tolist() == ["SUB006", "SUB007", "SUB008"]
    pd.testing.assert_frame_equal(service.subjects(), _subjects())
    assert table_path.read_bytes() == before_bytes


def test_file_import_drafts_preserve_text_easyqcid_for_csv_and_excel(
    tmp_path,
    monkeypatch,
) -> None:
    service, _projects = _service(tmp_path)
    csv_path = tmp_path / "incoming.csv"
    csv_path.write_text(
        "easyqcid,visit\n001,1\n01-A,2\n",
        encoding="utf-8",
    )
    excel_path = tmp_path / "incoming.xlsx"
    excel_path.write_bytes(b"synthetic workbook placeholder")
    read_excel_calls = []

    def fake_read_excel(path, **kwargs):
        read_excel_calls.append((path, kwargs))
        return pd.DataFrame(
            {"easyqcid": ["001", "01-A"], "visit": [1, 2]}
        )

    monkeypatch.setattr(
        "core.configuration_service.pd.read_excel",
        fake_read_excel,
    )

    csv_draft = service.draft_from_file(csv_path)
    excel_draft = service.draft_from_file(excel_path)

    assert csv_draft["easyqcid"].tolist() == ["001", "01-A"]
    assert csv_draft["visit"].tolist() == [1, 2]
    assert excel_draft["easyqcid"].tolist() == ["001", "01-A"]
    assert excel_draft["visit"].tolist() == [1, 2]
    assert read_excel_calls[0][0] == excel_path
    converter = read_excel_calls[0][1]["converters"]["easyqcid"]
    assert converter("NA") == "NA"
    assert converter("001") == "001"
    assert converter(7) == 7


def test_import_subject_csv_preserves_leading_zero_easyqcid(tmp_path) -> None:
    service, _projects = _service(tmp_path)
    source = tmp_path / "subjects.csv"
    source.write_text(
        "easyqcid,visit\n001,1\n01-A,2\n",
        encoding="utf-8",
    )

    service.import_subject_csv(source)

    result = service.subjects()
    assert result["easyqcid"].tolist() == ["001", "01-A"]
    assert result["visit"].tolist() == [1, 2]


def test_list_import_draft_readers_fail_loud_on_invalid_sources(tmp_path) -> None:
    service, _projects = _service(tmp_path)
    unsupported = tmp_path / "incoming.json"
    unsupported.write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="目录"):
        service.draft_from_folder(tmp_path / "missing", "easyqcid")
    with pytest.raises(ConfigurationError, match="格式"):
        service.draft_from_file(unsupported)
    with pytest.raises(ConfigurationError, match="为空"):
        service.draft_from_text("  , \n", "easyqcid")
    with pytest.raises(ConfigurationError, match="字段名"):
        service.draft_from_text("SUB001", "not a valid field")


def _folder_match_tree(tmp_path):
    root = tmp_path / "pattern-root"
    (root / "direct_T1").mkdir(parents=True)
    (root / "direct_report.txt").write_text("direct", encoding="utf-8")
    (root / "siteA" / "sub01").mkdir(parents=True)
    (root / "siteA" / "sub01" / "scan_T1.nii.gz").write_text(
        "scan",
        encoding="utf-8",
    )
    (root / "siteA" / "sub01" / "notes.txt").write_text(
        "notes",
        encoding="utf-8",
    )
    (root / "siteB" / "sub02" / "QC_T1_folder").mkdir(parents=True)
    (root / "siteB" / "sub02" / "scan_T2.nii").write_text(
        "scan",
        encoding="utf-8",
    )
    return root


def _folder_request(**changes):
    from models.folder_match import FolderMatchRequest

    values = {
        "target_kind": "both",
        "match_kind": "contains",
        "pattern": "T1",
        "scope": "all",
        "exact_depth": 1,
        "item_column": "matched_item",
        "parent_column": "relative_parent",
    }
    values.update(changes)
    return FolderMatchRequest(**values)


@pytest.mark.parametrize(
    ("match_kind", "pattern", "target_kind", "expected"),
    [
        (
            "starts_with",
            "scan_",
            "file",
            {"scan_T1.nii.gz", "scan_T2.nii"},
        ),
        ("ends_with", ".nii.gz", "file", {"scan_T1.nii.gz"}),
        (
            "contains",
            "T1",
            "both",
            {"direct_T1", "scan_T1.nii.gz", "QC_T1_folder"},
        ),
        (
            "wildcard",
            "scan_T?.nii*",
            "file",
            {"scan_T1.nii.gz", "scan_T2.nii"},
        ),
        ("regex", r"^QC_.*_folder$", "directory", {"QC_T1_folder"}),
    ],
)
def test_folder_pattern_reader_supports_all_match_and_target_kinds(
    tmp_path,
    match_kind,
    pattern,
    target_kind,
    expected,
) -> None:
    service, _projects = _service(tmp_path)
    root = _folder_match_tree(tmp_path)
    request = _folder_request(
        match_kind=match_kind,
        pattern=pattern,
        target_kind=target_kind,
    )

    result = service.draft_from_folder_matches(root, request)

    assert set(result["matched_item"]) == expected


def test_folder_pattern_reader_supports_direct_exact_all_and_optional_parent(
    tmp_path,
) -> None:
    service, _projects = _service(tmp_path)
    root = _folder_match_tree(tmp_path)

    direct = service.draft_from_folder_matches(
        root,
        _folder_request(
            scope="direct",
            match_kind="starts_with",
            pattern="direct_",
        ),
    )
    exact = service.draft_from_folder_matches(
        root,
        _folder_request(
            target_kind="file",
            scope="exact",
            exact_depth=3,
            match_kind="ends_with",
            pattern=".nii",
        ),
    )
    all_without_parent = service.draft_from_folder_matches(
        root,
        _folder_request(
            target_kind="file",
            match_kind="starts_with",
            pattern="scan_",
            parent_column=None,
        ),
    )

    assert direct.to_dict("records") == [
        {"relative_parent": "", "matched_item": "direct_T1"},
        {"relative_parent": "", "matched_item": "direct_report.txt"},
    ]
    assert exact.to_dict("records") == [
        {"relative_parent": "siteB/sub02", "matched_item": "scan_T2.nii"}
    ]
    assert all_without_parent.to_dict("records") == [
        {"matched_item": "scan_T1.nii.gz"},
        {"matched_item": "scan_T2.nii"},
    ]


def test_folder_pattern_reader_rejects_invalid_regex_and_empty_result(
    tmp_path,
) -> None:
    service, _projects = _service(tmp_path)
    root = _folder_match_tree(tmp_path)

    with pytest.raises(ConfigurationError, match="正则"):
        service.draft_from_folder_matches(
            root,
            _folder_request(match_kind="regex", pattern="("),
        )
    with pytest.raises(ConfigurationError, match="匹配"):
        service.draft_from_folder_matches(
            root,
            _folder_request(pattern="NEVER_MATCHES"),
        )


def test_folder_pattern_reader_skips_symbolic_links(tmp_path) -> None:
    service, _projects = _service(tmp_path)
    root = _folder_match_tree(tmp_path)
    link = root / "linked_T1"
    try:
        link.symlink_to(root / "siteA", target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    result = service.draft_from_folder_matches(
        root,
        _folder_request(pattern="T1"),
    )

    assert "linked_T1" not in set(result["matched_item"])
    assert result["matched_item"].tolist().count("scan_T1.nii.gz") == 1


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"target_kind": "link"}, "目标类型"),
        ({"match_kind": "python"}, "匹配方式"),
        ({"pattern": ""}, "匹配模式"),
        ({"scope": "nearby"}, "查找范围"),
        ({"exact_depth": 0}, "精确层级"),
        ({"item_column": "not a field"}, "字段名"),
        (
            {
                "item_column": "same_name",
                "parent_column": "same_name",
            },
            "不能相同",
        ),
    ],
)
def test_folder_match_request_rejects_ambiguous_or_unsafe_rules(
    changes,
    match,
) -> None:
    with pytest.raises((TypeError, ValueError), match=match):
        _folder_request(**changes)


def test_list_import_merge_columns_and_append_rows_use_exact_validated_easyqcid(
    tmp_path,
) -> None:
    service, _projects = _service(tmp_path)
    service.replace_subjects(
        pd.DataFrame({"easyqcid": ["SUB001", "SUB002"], "site": ["A", "B"]})
    )

    service.merge_subjects(
        pd.DataFrame({"easyqcid": ["SUB001", "SUB003"], "batch": ["X", "Y"]}),
        mode="columns",
    )
    merged = service.subjects().set_index("easyqcid")
    assert list(merged.index) == ["SUB001", "SUB002", "SUB003"]
    assert merged.loc["SUB001", "batch"] == "X"
    assert merged.loc["SUB003", "batch"] == "Y"

    service.merge_subjects(
        pd.DataFrame(
            {
                "easyqcid": ["SUB004"],
                "site": ["D"],
                "batch": ["Z"],
            }
        ),
        mode="rows",
    )
    assert service.subjects()["easyqcid"].tolist() == [
        "SUB001",
        "SUB002",
        "SUB003",
        "SUB004",
    ]


@pytest.mark.parametrize(
    "incoming, mode, match",
    [
        (pd.DataFrame({"easyqcid": ["", "SUB003"], "batch": ["X", "Y"]}), "columns", "空白"),
        (
            pd.DataFrame({"easyqcid": ["SUB003", "SUB003"], "batch": ["X", "Y"]}),
            "columns",
            "重复",
        ),
        (pd.DataFrame({"easyqcid": ["SUB001"], "site": ["changed"]}), "columns", "overlap"),
        (pd.DataFrame({"easyqcid": ["SUB003"], "other": ["X"]}), "rows", "same columns"),
    ],
)
def test_failed_list_import_preserves_memory_and_atomic_table(
    tmp_path,
    incoming,
    mode,
    match,
) -> None:
    service, projects = _service(tmp_path)
    current = pd.DataFrame({"easyqcid": ["SUB001", "SUB002"], "site": ["A", "B"]})
    service.replace_subjects(current)
    table_path = projects.current_project.table_dir / "easyqc_all.csv"
    before_bytes = table_path.read_bytes()

    with pytest.raises(ConfigurationError, match=match):
        service.merge_subjects(incoming, mode=mode)

    pd.testing.assert_frame_equal(service.subjects(), current)
    assert table_path.read_bytes() == before_bytes


def test_module_filter_resolution_matches_table_positions_in_source_order() -> None:
    subjects = pd.DataFrame(
        {
            "easyqcid": ["SUB003", "SUB001", "SUB002"],
            "site": ["C", "A", "B"],
            "age": [27, 29, 31],
        }
    )
    expression = FilterExpression(
        group_join="any",
        groups=(
            FilterGroup(
                group_id="site-a",
                join="all",
                conditions=(
                    FilterCondition("site", "==", "A", "site-a-condition"),
                ),
            ),
            FilterGroup(
                group_id="older",
                join="all",
                conditions=(
                    FilterCondition("age", ">=", 31, "older-condition"),
                ),
            ),
        ),
    )
    table_service = TableViewService(subjects)
    expected_result = table_service.apply_state(
        table_service.default_state().with_filter(expression)
    )
    expected = tuple(
        subjects.iloc[expected_result.source_positions]["easyqcid"].tolist()
    )

    assert resolve_module_filter_identities(subjects, expression) == expected
    assert expected == ("SUB001", "SUB002")
    assert resolve_module_filter_identities(
        subjects,
        _filter_expression("site", "==", "missing"),
    ) == ()


def test_module_filter_does_not_profile_unreferenced_complete_list_columns() -> None:
    subjects = pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB002", "SUB003"],
            "site": ["A", "B", "A"],
            "unused_object_payload": [["not"], ["hashable"], ["values"]],
        }
    )
    original = subjects.copy(deep=True)

    assert resolve_module_filter_identities(
        subjects,
        _filter_expression("site", "==", "A"),
    ) == ("SUB001", "SUB003")
    pd.testing.assert_frame_equal(subjects, original)


def test_save_module_filters_are_independent_and_clear_only_selected_legacy(
    tmp_path,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    service.add_module("FuncQC", "Functional QC")
    first = next(module for module in service.modules() if module.name == "example")
    first.select_filter = "SELECT * FROM df WHERE site = 'B'"
    service.save_module(first, original_name="example")
    second = next(module for module in service.modules() if module.name == "FuncQC")
    second.select_filter = "SELECT * FROM df WHERE age >= 31"
    service.save_module(second, original_name="FuncQC")
    before = deepcopy(projects.settings["qcmodule"])

    first_matches = service.save_module_filter(
        "example",
        _filter_expression("site", "==", "A"),
    )

    after_first = projects.settings["qcmodule"]
    assert first_matches == ("SUB001",)
    assert after_first["1"]["qc_filter"] == {
        "schema_version": 1,
        "group_join": "all",
        "groups": [
            {
                "group_id": "group-1",
                "join": "all",
                "conditions": [
                    {
                        "column": "site",
                        "operator": "==",
                        "value": "A",
                        "condition_id": "condition-1",
                        "enabled": True,
                    }
                ],
            }
        ],
    }
    assert after_first["1"]["select_filter"] is None
    assert {
        key: value
        for key, value in after_first["1"].items()
        if key not in {"qc_filter", "select_filter"}
    } == {
        key: value
        for key, value in before["1"].items()
        if key not in {"qc_filter", "select_filter"}
    }
    assert after_first["2"] == before["2"]
    first_after_save = deepcopy(after_first["1"])

    second_matches = service.save_module_filter(
        "FuncQC",
        _filter_expression("age", ">=", 31),
    )

    assert second_matches == ("SUB002",)
    assert projects.settings["qcmodule"]["1"] == first_after_save
    assert projects.settings["qcmodule"]["2"]["select_filter"] is None
    assert projects.settings["qcmodule"]["2"]["qc_filter"] != first_after_save[
        "qc_filter"
    ]
    persisted = json.loads(
        projects.current_project.settings_path.read_text(encoding="utf-8")
    )
    assert persisted["qcmodule"] == projects.settings["qcmodule"]


def test_save_module_filter_can_defer_events_until_gui_thread_publication(
    tmp_path,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    events = []
    projects.event_bus.subscribe(EventType.SETTINGS_SAVED, events.append)
    projects.event_bus.subscribe(EventType.MODULES_CHANGED, events.append)

    matches = service.save_module_filter(
        "example",
        _filter_expression("site", "==", "A"),
        notify=False,
    )

    assert matches == ("SUB001",)
    assert events == []

    service.publish_modules_changed()

    assert [event.type for event in events] == [
        EventType.SETTINGS_SAVED,
        EventType.MODULES_CHANGED,
    ]


def test_save_module_filter_rejects_project_switch_after_state_capture(
    tmp_path,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    captured = service.capture_settings_state()
    sample_path = projects.current_project.settings_path
    sample_before = sample_path.read_bytes()
    service.create_project("SECOND", tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    second_path = projects.current_project.settings_path
    second_before = second_path.read_bytes()

    with pytest.raises(ConfigurationError, match="project.*changed"):
        service.save_module_filter(
            "example",
            _filter_expression("site", "==", "A"),
            notify=False,
            expected_state=captured,
        )

    assert sample_path.read_bytes() == sample_before
    assert second_path.read_bytes() == second_before
    assert service.modules()[0].qc_filter is None


def test_save_module_filter_rejects_settings_change_after_state_capture(
    tmp_path,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    captured = service.capture_settings_state()
    service.set_constant("newer_value", "preserve-me")
    settings_path = projects.current_project.settings_path
    settings_before = settings_path.read_bytes()

    with pytest.raises(ConfigurationError, match="settings.*changed"):
        service.save_module_filter(
            "example",
            _filter_expression("site", "==", "A"),
            notify=False,
            expected_state=captured,
        )

    assert settings_path.read_bytes() == settings_before
    assert service.constants()["newer_value"] == "preserve-me"
    assert service.modules()[0].qc_filter is None


@pytest.mark.parametrize(
    "expression",
    [
        _filter_expression("missing_column", "==", "A"),
        _filter_expression("age", ">=", "not-a-number"),
    ],
)
def test_save_module_filter_rejects_invalid_condition_without_writing(
    tmp_path,
    expression,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    before_settings = deepcopy(dict(projects.settings))
    before_bytes = projects.current_project.settings_path.read_bytes()

    with pytest.raises(ConfigurationError):
        service.save_module_filter("example", expression)

    assert dict(projects.settings) == before_settings
    assert projects.current_project.settings_path.read_bytes() == before_bytes


def test_failed_module_filter_commit_restores_memory_and_disk(
    monkeypatch,
    tmp_path,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    before_settings = deepcopy(dict(projects.settings))
    before_bytes = projects.current_project.settings_path.read_bytes()
    real_save = FileUtils.safe_json_save

    def fail_settings(path, data, indent=4):
        if path == projects.current_project.settings_path:
            raise OSError("module filter replace failed")
        return real_save(path, data, indent)

    monkeypatch.setattr(FileUtils, "safe_json_save", fail_settings)

    with pytest.raises(OSError, match="module filter replace failed"):
        service.save_module_filter(
            "example",
            _filter_expression("site", "==", "A"),
        )

    assert dict(projects.settings) == before_settings
    assert projects.current_project.settings_path.read_bytes() == before_bytes


def test_subject_row_deletion_atomically_persists_and_retains_ratings(
    tmp_path,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    rating_path = (
        projects.current_project.path
        / "RatingFiles"
        / "AnatQC"
        / "rater1"
        / "rating.json"
    )
    rating_path.parent.mkdir(parents=True)
    rating_path.write_bytes(b'{"easyqcid":"SUB001","score":"Good"}')
    rating_before = rating_path.read_bytes()
    events = []
    service.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        events.append,
    )

    removed = service.delete_subject_rows(("SUB001",))

    assert removed == 1
    assert service.subjects()["easyqcid"].tolist() == ["SUB002"]
    assert rating_path.read_bytes() == rating_before
    assert events and events[-1].type is EventType.SUBJECTS_CHANGED
    table_after = (
        projects.current_project.table_dir / "easyqc_all.csv"
    ).read_bytes()

    with pytest.raises(ConfigurationError, match="不存在"):
        service.delete_subject_rows(("MISSING",))

    assert (
        projects.current_project.table_dir / "easyqc_all.csv"
    ).read_bytes() == table_after
    assert rating_path.read_bytes() == rating_before


def test_subject_column_deletion_protects_identity_and_retains_ratings(
    tmp_path,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    table_path = projects.current_project.table_dir / "easyqc_all.csv"
    rating_path = (
        projects.current_project.path
        / "RatingFiles"
        / "AnatQC"
        / "rater1"
        / "rating.json"
    )
    rating_path.parent.mkdir(parents=True)
    rating_path.write_bytes(b'{"easyqcid":"SUB001","notes":"retain"}')
    rating_before = rating_path.read_bytes()

    removed = service.delete_subject_columns(("site", "age"))

    assert removed == 2
    assert service.subjects().columns.tolist() == ["easyqcid"]
    assert rating_path.read_bytes() == rating_before
    table_before = table_path.read_bytes()

    with pytest.raises(ConfigurationError, match="easyqcid"):
        service.delete_subject_columns(("easyqcid",))
    with pytest.raises(ConfigurationError, match="不存在"):
        service.delete_subject_columns(("missing",))

    assert table_path.read_bytes() == table_before
    assert rating_path.read_bytes() == rating_before


def test_subject_deletion_save_failure_leaves_table_ratings_and_events_unchanged(
    tmp_path,
    monkeypatch,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    table_path = projects.current_project.table_dir / "easyqc_all.csv"
    rating_path = (
        projects.current_project.path
        / "RatingFiles"
        / "AnatQC"
        / "rater1"
        / "rating.json"
    )
    rating_path.parent.mkdir(parents=True)
    rating_path.write_bytes(b'{"easyqcid":"SUB001","tag":true}')
    table_before = table_path.read_bytes()
    rating_before = rating_path.read_bytes()
    events = []
    service.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        events.append,
    )

    def fail_save(*_args, **_kwargs):
        raise OSError("synthetic table replace failure")

    monkeypatch.setattr(service.table_service, "save_table", fail_save)

    with pytest.raises(OSError, match="synthetic table replace failure"):
        service.delete_subject_rows(("SUB001",))

    assert table_path.read_bytes() == table_before
    assert rating_path.read_bytes() == rating_before
    assert events == []


def test_explicit_subject_import_append_policies_preserve_order_and_report(
    tmp_path,
) -> None:
    service, _projects = _service(tmp_path)
    current = pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB002"],
            "site": ["A", "B"],
            "age": [20, 30],
        }
    )
    incoming = pd.DataFrame(
        {
            "age": [31, 40],
            "site": ["B2", "C"],
            "easyqcid": ["SUB002", "SUB003"],
        }
    )
    service.replace_subjects(current, notify=False)

    preview = service.preview_subject_import(
        incoming,
        mode="append",
        conflict_policy="deduplicate",
    )
    assert preview.mode == "append"
    assert preview.conflict_policy == "deduplicate"
    assert preview.matching_identities == 1
    assert preview.new_identities == 1
    assert preview.result_rows == 3

    result = service.import_subjects(
        incoming,
        mode="append",
        conflict_policy="deduplicate",
        notify=False,
    )
    assert result == preview
    pd.testing.assert_frame_equal(
        service.subjects(),
        pd.DataFrame(
            {
                "easyqcid": ["SUB001", "SUB002", "SUB003"],
                "site": ["A", "B", "C"],
                "age": [20, 30, 40],
            }
        ),
        check_dtype=False,
    )

    service.replace_subjects(current, notify=False)
    service.import_subjects(
        incoming,
        mode="append",
        conflict_policy="replace",
        notify=False,
    )
    pd.testing.assert_frame_equal(
        service.subjects(),
        pd.DataFrame(
            {
                "easyqcid": ["SUB001", "SUB002", "SUB003"],
                "site": ["A", "B2", "C"],
                "age": [20, 31, 40],
            }
        ),
        check_dtype=False,
    )


def test_explicit_subject_import_merge_policies_avoid_suffixes_and_blank_clears(
    tmp_path,
) -> None:
    service, _projects = _service(tmp_path)
    current = pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB002"],
            "site": ["A", "B"],
            "age": [20, 30],
            "passed": [True, True],
        }
    )
    incoming = pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB002", "SUB003"],
            "site": ["  ", "B2", "C"],
            "age": [0, pd.NA, 40],
            "passed": [False, pd.NA, True],
            "batch": ["X", "Y", "Z"],
        }
    )
    service.replace_subjects(current, notify=False)

    preview = service.preview_subject_import(
        incoming,
        mode="merge_columns",
        conflict_policy="preserve",
    )
    assert preview.overlapping_columns == ("site", "age", "passed")
    service.import_subjects(
        incoming,
        mode="merge_columns",
        conflict_policy="preserve",
        notify=False,
    )
    preserved = service.subjects()
    assert preserved.columns.tolist() == [
        "easyqcid",
        "site",
        "age",
        "passed",
        "batch",
    ]
    assert not any(
        str(column).endswith(("_x", "_y")) for column in preserved.columns
    )
    preserved_by_id = preserved.set_index("easyqcid")
    assert preserved_by_id.loc["SUB001", "site"] == "A"
    assert preserved_by_id.loc["SUB001", "age"] == 20
    assert bool(preserved_by_id.loc["SUB001", "passed"]) is True
    assert preserved_by_id.loc["SUB003", "site"] == "C"
    assert preserved_by_id.loc["SUB003", "batch"] == "Z"

    service.replace_subjects(current, notify=False)
    service.import_subjects(
        incoming,
        mode="merge_columns",
        conflict_policy="update",
        notify=False,
    )
    updated = service.subjects().set_index("easyqcid")
    assert updated.loc["SUB001", "site"] == "A"
    assert updated.loc["SUB001", "age"] == 0
    assert bool(updated.loc["SUB001", "passed"]) is False
    assert updated.loc["SUB002", "site"] == "B2"
    assert updated.loc["SUB002", "age"] == 30
    assert bool(updated.loc["SUB002", "passed"]) is True
    assert updated.loc["SUB003", "site"] == "C"


def test_explicit_subject_import_replace_is_atomic_and_retains_ratings(
    tmp_path,
    monkeypatch,
) -> None:
    service, projects = _service(tmp_path)
    current = pd.DataFrame(
        {"easyqcid": ["SUB001", "SUB002"], "site": ["A", "B"]}
    )
    replacement = pd.DataFrame(
        {"easyqcid": ["NEW001"], "batch": ["X"]}
    )
    service.replace_subjects(current, notify=False)
    table_path = projects.current_project.table_dir / "easyqc_all.csv"
    rating_path = (
        projects.current_project.path
        / "RatingFiles"
        / "AnatQC"
        / "rater1"
        / "rating.json"
    )
    rating_path.parent.mkdir(parents=True)
    rating_path.write_bytes(b'{"easyqcid":"SUB001","score":"Good"}')
    rating_before = rating_path.read_bytes()
    events = []
    service.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        events.append,
    )

    summary = service.import_subjects(
        replacement,
        mode="replace",
        conflict_policy=None,
    )
    assert summary.current_rows == 2
    assert summary.incoming_rows == 1
    assert summary.result_rows == 1
    pd.testing.assert_frame_equal(service.subjects(), replacement)
    assert rating_path.read_bytes() == rating_before
    assert len(events) == 1

    before_table = table_path.read_bytes()

    def fail_save(*_args, **_kwargs):
        raise OSError("synthetic explicit import failure")

    monkeypatch.setattr(service.table_service, "save_table", fail_save)
    with pytest.raises(OSError, match="synthetic explicit import failure"):
        service.import_subjects(
            current,
            mode="replace",
            conflict_policy=None,
        )

    assert table_path.read_bytes() == before_table
    assert rating_path.read_bytes() == rating_before
    assert len(events) == 1


@pytest.mark.parametrize(
    "incoming, mode, policy, match",
    [
        (
            pd.DataFrame(
                {"easyqcid": ["SUB003", "SUB003"], "site": ["C", "D"]}
            ),
            "append",
            "deduplicate",
            "重复",
        ),
        (
            pd.DataFrame(
                [["SUB003", "C", "D"]],
                columns=["easyqcid", "site", "site"],
            ),
            "replace",
            None,
            "重复字段",
        ),
        (
            pd.DataFrame({"easyqcid": ["SUB003"], "other": ["C"]}),
            "append",
            "replace",
            "相同字段",
        ),
        (
            pd.DataFrame({"easyqcid": ["SUB003"], "site": ["C"]}),
            "append",
            "update",
            "冲突策略",
        ),
        (
            pd.DataFrame({"easyqcid": ["SUB003"], "site": ["C"]}),
            "replace",
            "deduplicate",
            "冲突策略",
        ),
    ],
)
def test_explicit_subject_import_rejects_ambiguous_inputs_before_write(
    tmp_path,
    incoming,
    mode,
    policy,
    match,
) -> None:
    service, projects = _service(tmp_path)
    current = pd.DataFrame(
        {"easyqcid": ["SUB001", "SUB002"], "site": ["A", "B"]}
    )
    service.replace_subjects(current, notify=False)
    table_path = projects.current_project.table_dir / "easyqc_all.csv"
    before_bytes = table_path.read_bytes()

    with pytest.raises(ConfigurationError, match=match):
        service.import_subjects(
            incoming,
            mode=mode,
            conflict_policy=policy,
        )

    assert table_path.read_bytes() == before_bytes
    pd.testing.assert_frame_equal(service.subjects(), current)
