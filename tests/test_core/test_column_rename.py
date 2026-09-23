from dataclasses import replace

import pandas as pd
import pytest

from core.configuration_service import ConfigurationError, ConfigurationService
from core.project_service import ProjectService
from core.table_service import TableService
from core.table_view_service import TableViewService
from models.table_view_state import ColumnViewState, FilterCondition, SortRule


def config_for(tmp_path):
    config = ConfigurationService(ProjectService(tmp_path / "projects.json"), TableService())
    config.create_project("SAMPLE", tmp_path / "project")
    config.replace_subjects(pd.DataFrame({"easyqcid": ["A", "B"], "site": ["01", "02"], "age": [20, 30]}))
    config.add_constant("ROOT", "/data")
    return config


def test_rename_only_changes_master_header_and_preserves_text_and_settings(tmp_path):
    config = config_for(tmp_path)
    before = config.subjects()
    settings = config.current_project.settings_path.read_bytes()
    ratings = config.current_project.rating_dir / "synthetic-sentinel.json"
    ratings.write_text('{"sentinel":"not a parsed rating"}', encoding="utf-8")
    assert config.rename_subject_column("site", "session") == 1
    pd.testing.assert_frame_equal(config.subjects(), before.rename(columns={"site": "session"}))
    assert config.subjects()["session"].tolist() == ["01", "02"]
    assert config.current_project.settings_path.read_bytes() == settings
    assert ratings.read_text(encoding="utf-8") == '{"sentinel":"not a parsed rating"}'


@pytest.mark.parametrize("old,new", [("easyqcid", "id"), ("site", "easyqcid"), ("site", "age"), ("site", "ROOT"), ("missing", "new"), ("site", ""), ("site", "   "), ("site", "site")])
def test_rename_rejects_protected_missing_and_conflicting_names(tmp_path, old, new):
    config = config_for(tmp_path)
    before = config.subjects()
    with pytest.raises((ConfigurationError, ValueError)):
        config.rename_subject_column(old, new)
    pd.testing.assert_frame_equal(config.subjects(), before)


def test_rename_write_failure_leaves_original_table(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    before = config.subjects()
    def fail(*args, **kwargs):
        raise OSError("synthetic write failure")
    monkeypatch.setattr(config.table_service, "save_table", fail)
    with pytest.raises(OSError, match="synthetic"):
        config.rename_subject_column("site", "session")
    pd.testing.assert_frame_equal(config.subjects(), before)


def test_view_column_rename_preserves_filter_sort_order_hidden_width_and_pinning():
    source = pd.DataFrame({"easyqcid": ["A", "B"], "site": ["01", "02"], "age": [20, 30]})
    state = TableViewService(source).default_state().with_conditions((FilterCondition("site", "==", "02"),)).with_sort_rules((SortRule("site", False),))
    state = replace(state, columns=ColumnViewState(("easyqcid", "site", "age"), ("age",), (("site", 240),), ("easyqcid", "site")))
    renamed = state.rename_column("site", "session")
    assert renamed.columns.order == ("easyqcid", "session", "age")
    assert renamed.columns.hidden == ("age",)
    assert renamed.columns.pinned == ("easyqcid", "session")
    assert renamed.columns.width_for("session") == 240
    assert renamed.conditions[0].column == "session"
    assert renamed.sort_rules == (SortRule("session", False),)
    assert TableViewService(source.rename(columns={"site": "session"})).apply_state(renamed).matched_total == 1
    assert state.conditions[0].column == "site"
