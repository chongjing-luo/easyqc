from __future__ import annotations

from copy import deepcopy
import json

import pandas as pd
import pytest

from core.configuration_service import ConfigurationError, ConfigurationService
from core.event_bus import EventType
from core.module_filter import resolve_module_filter_identities
from core.project_service import ProjectService
from core.table_service import TableService
from core.table_view_service import TableViewService
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
        {"ezqcid": ["SUB001", "SUB002"], "site": ["A", "B"], "age": [29, 31]}
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
    assert (projects.current_project.table_dir / "ezqc_all.csv").exists()


def test_derive_subject_column_persists_values_once_and_publishes_change(tmp_path) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    events = []
    service.project_service.event_bus.subscribe(
        EventType.SUBJECTS_CHANGED,
        events.append,
    )

    result = service.derive_subject_column("age_next", "age + 1")

    assert result == "age_next"
    assert service.subjects()["age_next"].tolist() == [30, 32]
    assert events and events[-1].source == "ConfigurationService"
    csv_text = (
        projects.current_project.table_dir / "ezqc_all.csv"
    ).read_text(encoding="utf-8")
    assert "age_next" in csv_text.splitlines()[0]
    assert "age + 1" not in csv_text


@pytest.mark.parametrize(
    ("name", "expression", "match"),
    [
        ("age", "age + 1", "已存在"),
        ("unsafe", "__import__('os')", "白名单"),
        ("ezqcid", "age + 1", "已存在"),
        ("class", "age + 1", "有效字段名"),
        (None, "age + 1", "必须是文本"),
    ],
)
def test_derive_subject_column_rejects_invalid_request_without_writing(
    tmp_path,
    name,
    expression,
    match,
) -> None:
    service, projects = _service(tmp_path)
    service.replace_subjects(_subjects(), notify=False)
    table_path = projects.current_project.table_dir / "ezqc_all.csv"
    before = table_path.read_bytes()

    with pytest.raises(ConfigurationError, match=match):
        service.derive_subject_column(name, expression)

    assert table_path.read_bytes() == before
    pd.testing.assert_frame_equal(service.subjects(), _subjects())


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame({"ezqcid": ["SUB001", "SUB001"]}),
        pd.DataFrame({"ezqcid": ["SUB001", " "]}),
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
            pd.DataFrame({"ezqcid": ["SUB001"], "site": ["changed"]}),
            mode="columns",
        )
    with pytest.raises(ConfigurationError, match="column"):
        service.set_constant("site", "bad")
    with pytest.raises(ConfigurationError, match="常量"):
        service.replace_subjects(
            pd.DataFrame({"ezqcid": ["SUB001"], "DATA_ROOT": ["shadow"]})
        )


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
        "ezqcid": "SUB999",
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
    assert module.ezqcid is None
    assert module.notes is None
    assert module.scores["1"].value is None
    assert module.tags["1"].value is False

    with pytest.raises(ConfigurationError, match="already exists"):
        service.import_module_payload(imported)

    output = tmp_path / "exports" / "module.json"
    service.export_module("ImportedQC", output)
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["rater"] is None
    assert payload["ezqcid"] is None


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
    table_path = projects.current_project.table_dir / "ezqc_all.csv"
    before_bytes = table_path.read_bytes()

    folder = tmp_path / "incoming-folders"
    (folder / "SUB004").mkdir(parents=True)
    (folder / "SUB003").mkdir()
    (folder / "ignored.txt").write_text("not a directory", encoding="utf-8")
    csv_path = tmp_path / "incoming.csv"
    pd.DataFrame(
        {"ezqcid": ["SUB005"], "site": ["C"], "scanner_model": ["Prisma"]}
    ).to_csv(csv_path, index=False)

    folder_draft = service.draft_from_folder(folder, "ezqcid")
    file_draft = service.draft_from_file(csv_path)
    text_draft = service.draft_from_text("SUB006, SUB007\nSUB008", "ezqcid")

    assert folder_draft.to_dict("list") == {"ezqcid": ["SUB003", "SUB004"]}
    assert file_draft.to_dict("records") == [
        {"ezqcid": "SUB005", "site": "C", "scanner_model": "Prisma"}
    ]
    assert text_draft["ezqcid"].tolist() == ["SUB006", "SUB007", "SUB008"]
    pd.testing.assert_frame_equal(service.subjects(), _subjects())
    assert table_path.read_bytes() == before_bytes


def test_list_import_draft_readers_fail_loud_on_invalid_sources(tmp_path) -> None:
    service, _projects = _service(tmp_path)
    unsupported = tmp_path / "incoming.json"
    unsupported.write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="目录"):
        service.draft_from_folder(tmp_path / "missing", "ezqcid")
    with pytest.raises(ConfigurationError, match="格式"):
        service.draft_from_file(unsupported)
    with pytest.raises(ConfigurationError, match="为空"):
        service.draft_from_text("  , \n", "ezqcid")
    with pytest.raises(ConfigurationError, match="字段名"):
        service.draft_from_text("SUB001", "not a valid field")


def test_list_import_merge_columns_and_append_rows_use_exact_normalized_ezqcid(
    tmp_path,
) -> None:
    service, _projects = _service(tmp_path)
    service.replace_subjects(
        pd.DataFrame({"ezqcid": ["SUB001", "SUB002"], "site": ["A", "B"]})
    )

    service.merge_subjects(
        pd.DataFrame({"ezqcid": [" SUB001 ", "SUB003"], "batch": ["X", "Y"]}),
        mode="columns",
    )
    merged = service.subjects().set_index("ezqcid")
    assert list(merged.index) == ["SUB001", "SUB002", "SUB003"]
    assert merged.loc["SUB001", "batch"] == "X"
    assert merged.loc["SUB003", "batch"] == "Y"

    service.merge_subjects(
        pd.DataFrame(
            {
                "ezqcid": ["SUB004"],
                "site": ["D"],
                "batch": ["Z"],
            }
        ),
        mode="rows",
    )
    assert service.subjects()["ezqcid"].tolist() == [
        "SUB001",
        "SUB002",
        "SUB003",
        "SUB004",
    ]


@pytest.mark.parametrize(
    "incoming, mode, match",
    [
        (pd.DataFrame({"ezqcid": ["", "SUB003"], "batch": ["X", "Y"]}), "columns", "空白"),
        (
            pd.DataFrame({"ezqcid": ["SUB003", "SUB003"], "batch": ["X", "Y"]}),
            "columns",
            "重复",
        ),
        (pd.DataFrame({"ezqcid": ["SUB001"], "site": ["changed"]}), "columns", "overlap"),
        (pd.DataFrame({"ezqcid": ["SUB003"], "other": ["X"]}), "rows", "same columns"),
    ],
)
def test_failed_list_import_preserves_memory_and_atomic_table(
    tmp_path,
    incoming,
    mode,
    match,
) -> None:
    service, projects = _service(tmp_path)
    current = pd.DataFrame({"ezqcid": ["SUB001", "SUB002"], "site": ["A", "B"]})
    service.replace_subjects(current)
    table_path = projects.current_project.table_dir / "ezqc_all.csv"
    before_bytes = table_path.read_bytes()

    with pytest.raises(ConfigurationError, match=match):
        service.merge_subjects(incoming, mode=mode)

    pd.testing.assert_frame_equal(service.subjects(), current)
    assert table_path.read_bytes() == before_bytes


def test_module_filter_resolution_matches_table_positions_in_source_order() -> None:
    subjects = pd.DataFrame(
        {
            "ezqcid": ["SUB003", "SUB001", "SUB002"],
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
        subjects.iloc[expected_result.source_positions]["ezqcid"].tolist()
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
            "ezqcid": ["SUB001", "SUB002", "SUB003"],
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
