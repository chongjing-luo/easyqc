"""Tests for core.session_state — P2-A2: GUI session-state buffer (no ProjectManager).

Mirrors the LegacyGUIStateAdapter variable/result-table contracts so SessionState
can replace dt.var/dt.tab session-state without behavior change.
"""

import pandas as pd
from types import SimpleNamespace

from core.session_state import SessionState
from core.table_service import LoadedProjectTables


def _session() -> SessionState:
    s = SessionState()
    s._variables["easyqc_all"] = pd.DataFrame({"easyqcid": ["SUB_ALL"]})
    return s


# ---- variable getters/setters ----

def test_new_variable_table_returns_copy_or_none() -> None:
    s = SessionState()
    assert s.new_variable_table() is None
    s.set_new_variable_table(pd.DataFrame({0: ["A", "B"]}))
    got = s.new_variable_table()
    assert got.equals(pd.DataFrame({0: ["A", "B"]}))
    got.loc[0, 0] = "X"
    assert s._variables["easyqc_new"].loc[0, 0] == "A"  # copy isolation


def test_all_variable_table_returns_copy() -> None:
    s = _session()
    got = s.all_variable_table()
    assert got.equals(pd.DataFrame({"easyqcid": ["SUB_ALL"]}))


def test_filtered_variable_table_returns_copy() -> None:
    s = SessionState()
    s.set_filtered_variable_table(pd.DataFrame({"easyqcid": ["F"]}))
    assert s.filtered_variable_table().equals(pd.DataFrame({"easyqcid": ["F"]}))


def test_has_all_variable_rows() -> None:
    s = SessionState()
    assert not s.has_all_variable_rows()
    s.set_all_variable_table(pd.DataFrame({"easyqcid": ["A"]}))
    assert s.has_all_variable_rows()


# ---- prepare_new_variable_table (derived logic) ----

def test_prepare_new_variable_table_sorts_and_syncs_filter() -> None:
    s = SessionState()
    s.set_new_variable_table(pd.DataFrame({0: ["SUB002", "SUB001"]}))

    result = s.prepare_new_variable_table("easyqcid")

    expected = pd.DataFrame({"easyqcid": ["SUB001", "SUB002"]}, index=[1, 0])
    assert s._variables["easyqc_new"].equals(expected)
    assert s._variables["easyqc_filter"].equals(expected)
    assert result.equals(expected)


def test_prepare_new_variable_table_empty_columns_creates_named_column() -> None:
    s = SessionState()
    s.set_new_variable_table(pd.DataFrame())

    result = s.prepare_new_variable_table("ccs_dir")

    expected = pd.DataFrame(columns=["ccs_dir"])
    assert s._variables["easyqc_new"].equals(expected)
    assert s._variables["easyqc_filter"].equals(expected)
    assert result.equals(expected)


def test_prepare_new_variable_table_multicolumn_no_varname_keeps_source() -> None:
    s = SessionState()
    source = pd.DataFrame({"subject": ["SUB001"], "age": [12]})
    s.set_new_variable_table(source.copy())

    result = s.prepare_new_variable_table("ccs_dir")

    assert s._variables["easyqc_new"].equals(source)
    assert s._variables["easyqc_filter"].equals(source)
    assert result.equals(source)


# ---- merge source selection ----

def test_new_variable_merge_source_prefers_filter_falls_back_to_new() -> None:
    s = SessionState()
    s._variables["easyqc_new"] = pd.DataFrame({"easyqcid": ["SUB_NEW"]})
    s.set_filtered_variable_table(pd.DataFrame({"easyqcid": ["FILTER"]}))

    result = s.new_variable_merge_source()
    assert result.equals(pd.DataFrame({"easyqcid": ["FILTER"]}))
    result.loc[0, "easyqcid"] = "X"
    assert s._variables["easyqc_filter"].loc[0, "easyqcid"] == "FILTER"

    s._variables["easyqc_filter"] = None
    assert s.new_variable_merge_source().equals(pd.DataFrame({"easyqcid": ["SUB_NEW"]}))


# ---- merge into easyqc_all ----

def test_merge_all_variables_as_rows_concat() -> None:
    s = _session()
    s.merge_all_variables_as_rows(pd.DataFrame({"easyqcid": ["SUB_NEW"]}))
    assert s._variables["easyqc_all"]["easyqcid"].tolist() == ["SUB_ALL", "SUB_NEW"]


def test_merge_all_variables_as_columns_on_easyqcid() -> None:
    s = SessionState()
    s.set_all_variable_table(pd.DataFrame({"easyqcid": ["SUB_ALL"], "age": [1]}))
    s.merge_all_variables_as_columns(pd.DataFrame({"easyqcid": ["SUB_ALL"], "sex": ["F"]}))
    assert s._variables["easyqc_all"].to_dict("records") == [
        {"easyqcid": "SUB_ALL", "age": 1, "sex": "F"}
    ]


# ---- result table (tab) ----

def test_result_table_and_qctable_for_display_priority() -> None:
    s = SessionState()
    s._variables["easyqc_all"] = pd.DataFrame({"easyqcid": ["A"]})
    # no qctable -> falls back to easyqc_all
    assert s.qctable_for_display().equals(pd.DataFrame({"easyqcid": ["A"]}))

    s._results["easyqc_qctable"] = pd.DataFrame({"easyqcid": ["A"], "score": [1]})
    assert s.qctable_for_display().equals(pd.DataFrame({"easyqcid": ["A"], "score": [1]}))

    s._results["easyqc_qctable_filter"] = pd.DataFrame({"easyqcid": ["A"], "score": [2]})
    assert s.qctable_for_display().equals(pd.DataFrame({"easyqcid": ["A"], "score": [2]}))


# ---- apply_loaded_* (service injection) ----

def test_apply_loaded_tables_injects_variables_and_results() -> None:
    s = SessionState()
    loaded = LoadedProjectTables(
        variables={"easyqc_all": pd.DataFrame({"easyqcid": ["SUB"]})},
        results={
            "easyqc_qctable": pd.DataFrame({"easyqcid": ["Q"]}),
            "Mod": None,
        },
    )
    s.apply_loaded_tables(loaded)
    assert s._variables["easyqc_all"].equals(pd.DataFrame({"easyqcid": ["SUB"]}))
    assert s._results["easyqc_qctable"].equals(pd.DataFrame({"easyqcid": ["Q"]}))
    assert s._results["Mod"] is None


def test_apply_loaded_ratings_deep_copies_rating_dict_and_qctable() -> None:
    s = SessionState()
    qctable = pd.DataFrame({"easyqcid": ["S1"], "m.r.score1": ["Good"]})
    rating_dict = {"S1": {"m-r": {"scores": {"1": {"value": "Good"}}}}}

    s.apply_loaded_ratings(SimpleNamespace(rating_dict=rating_dict, qctable=qctable))

    assert s.rating_dict == rating_dict
    assert s._results["easyqc_qctable"].equals(qctable)

    rating_dict["S1"]["m-r"]["scores"]["1"]["value"] = "Changed"
    qctable.loc[0, "m.r.score1"] = "Changed"
    assert s.rating_dict["S1"]["m-r"]["scores"]["1"]["value"] == "Good"
    assert s._results["easyqc_qctable"].loc[0, "m.r.score1"] == "Good"


# ---- restore_filter_source (pure memory undo) ----

def test_restore_filter_source_writes_back_memory() -> None:
    s = SessionState()
    original = pd.DataFrame({"easyqcid": ["A", "B"]})
    # restore 'all' branch
    ret = s.restore_filter_source("all", original)
    assert ret is None
    assert s._variables["easyqc_all"].equals(original)
