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
    s._variables["ezqc_all"] = pd.DataFrame({"ezqcid": ["SUB_ALL"]})
    return s


# ---- variable getters/setters ----

def test_new_variable_table_returns_copy_or_none() -> None:
    s = SessionState()
    assert s.new_variable_table() is None
    s.set_new_variable_table(pd.DataFrame({0: ["A", "B"]}))
    got = s.new_variable_table()
    assert got.equals(pd.DataFrame({0: ["A", "B"]}))
    got.loc[0, 0] = "X"
    assert s._variables["ezqc_new"].loc[0, 0] == "A"  # copy isolation


def test_all_variable_table_returns_copy() -> None:
    s = _session()
    got = s.all_variable_table()
    assert got.equals(pd.DataFrame({"ezqcid": ["SUB_ALL"]}))


def test_filtered_variable_table_returns_copy() -> None:
    s = SessionState()
    s.set_filtered_variable_table(pd.DataFrame({"ezqcid": ["F"]}))
    assert s.filtered_variable_table().equals(pd.DataFrame({"ezqcid": ["F"]}))


def test_has_all_variable_rows() -> None:
    s = SessionState()
    assert not s.has_all_variable_rows()
    s.set_all_variable_table(pd.DataFrame({"ezqcid": ["A"]}))
    assert s.has_all_variable_rows()


# ---- prepare_new_variable_table (derived logic) ----

def test_prepare_new_variable_table_sorts_and_syncs_filter() -> None:
    s = SessionState()
    s.set_new_variable_table(pd.DataFrame({0: ["SUB002", "SUB001"]}))

    result = s.prepare_new_variable_table("ezqcid")

    expected = pd.DataFrame({"ezqcid": ["SUB001", "SUB002"]}, index=[1, 0])
    assert s._variables["ezqc_new"].equals(expected)
    assert s._variables["ezqc_filter"].equals(expected)
    assert result.equals(expected)


def test_prepare_new_variable_table_empty_columns_creates_named_column() -> None:
    s = SessionState()
    s.set_new_variable_table(pd.DataFrame())

    result = s.prepare_new_variable_table("ccs_dir")

    expected = pd.DataFrame(columns=["ccs_dir"])
    assert s._variables["ezqc_new"].equals(expected)
    assert s._variables["ezqc_filter"].equals(expected)
    assert result.equals(expected)


def test_prepare_new_variable_table_multicolumn_no_varname_keeps_source() -> None:
    s = SessionState()
    source = pd.DataFrame({"subject": ["SUB001"], "age": [12]})
    s.set_new_variable_table(source.copy())

    result = s.prepare_new_variable_table("ccs_dir")

    assert s._variables["ezqc_new"].equals(source)
    assert s._variables["ezqc_filter"].equals(source)
    assert result.equals(source)


# ---- merge source selection ----

def test_new_variable_merge_source_prefers_filter_falls_back_to_new() -> None:
    s = SessionState()
    s._variables["ezqc_new"] = pd.DataFrame({"ezqcid": ["SUB_NEW"]})
    s.set_filtered_variable_table(pd.DataFrame({"ezqcid": ["FILTER"]}))

    result = s.new_variable_merge_source()
    assert result.equals(pd.DataFrame({"ezqcid": ["FILTER"]}))
    result.loc[0, "ezqcid"] = "X"
    assert s._variables["ezqc_filter"].loc[0, "ezqcid"] == "FILTER"

    s._variables["ezqc_filter"] = None
    assert s.new_variable_merge_source().equals(pd.DataFrame({"ezqcid": ["SUB_NEW"]}))


# ---- merge into ezqc_all ----

def test_merge_all_variables_as_rows_concat() -> None:
    s = _session()
    s.merge_all_variables_as_rows(pd.DataFrame({"ezqcid": ["SUB_NEW"]}))
    assert s._variables["ezqc_all"]["ezqcid"].tolist() == ["SUB_ALL", "SUB_NEW"]


def test_merge_all_variables_as_columns_on_ezqcid() -> None:
    s = SessionState()
    s.set_all_variable_table(pd.DataFrame({"ezqcid": ["SUB_ALL"], "age": [1]}))
    s.merge_all_variables_as_columns(pd.DataFrame({"ezqcid": ["SUB_ALL"], "sex": ["F"]}))
    assert s._variables["ezqc_all"].to_dict("records") == [
        {"ezqcid": "SUB_ALL", "age": 1, "sex": "F"}
    ]


# ---- result table (tab) ----

def test_result_table_and_qctable_for_display_priority() -> None:
    s = SessionState()
    s._variables["ezqc_all"] = pd.DataFrame({"ezqcid": ["A"]})
    # no qctable -> falls back to ezqc_all
    assert s.qctable_for_display().equals(pd.DataFrame({"ezqcid": ["A"]}))

    s._results["ezqc_qctable"] = pd.DataFrame({"ezqcid": ["A"], "score": [1]})
    assert s.qctable_for_display().equals(pd.DataFrame({"ezqcid": ["A"], "score": [1]}))

    s._results["ezqc_qctable_filter"] = pd.DataFrame({"ezqcid": ["A"], "score": [2]})
    assert s.qctable_for_display().equals(pd.DataFrame({"ezqcid": ["A"], "score": [2]}))


# ---- apply_loaded_* (service injection) ----

def test_apply_loaded_tables_injects_variables_and_results() -> None:
    s = SessionState()
    loaded = LoadedProjectTables(
        variables={"ezqc_all": pd.DataFrame({"ezqcid": ["SUB"]})},
        results={
            "ezqc_qctable": pd.DataFrame({"ezqcid": ["Q"]}),
            "Mod": None,
        },
    )
    s.apply_loaded_tables(loaded)
    assert s._variables["ezqc_all"].equals(pd.DataFrame({"ezqcid": ["SUB"]}))
    assert s._results["ezqc_qctable"].equals(pd.DataFrame({"ezqcid": ["Q"]}))
    assert s._results["Mod"] is None


def test_apply_loaded_ratings_deep_copies_rating_dict_and_qctable() -> None:
    s = SessionState()
    qctable = pd.DataFrame({"ezqcid": ["S1"], "m.r.score1": ["Good"]})
    rating_dict = {"S1": {"m-r": {"scores": {"1": {"value": "Good"}}}}}

    s.apply_loaded_ratings(SimpleNamespace(rating_dict=rating_dict, qctable=qctable))

    assert s.rating_dict == rating_dict
    assert s._results["ezqc_qctable"].equals(qctable)

    rating_dict["S1"]["m-r"]["scores"]["1"]["value"] = "Changed"
    qctable.loc[0, "m.r.score1"] = "Changed"
    assert s.rating_dict["S1"]["m-r"]["scores"]["1"]["value"] == "Good"
    assert s._results["ezqc_qctable"].loc[0, "m.r.score1"] == "Good"


# ---- restore_filter_source (pure memory undo) ----

def test_restore_filter_source_writes_back_memory() -> None:
    s = SessionState()
    original = pd.DataFrame({"ezqcid": ["A", "B"]})
    # restore 'all' branch
    ret = s.restore_filter_source("all", original)
    assert ret is None
    assert s._variables["ezqc_all"].equals(original)
