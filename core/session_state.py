"""SessionState — pure in-memory GUI session-state buffer (P2-A2).

Replaces the dt.var/dt.tab session-state role of LegacyGUIStateAdapter /
DataContainer WITHOUT depending on ProjectManager. Holds two DataFrame
dictionaries (variable drafts + result tables) and a rating dict.

Layer: core. Depends only on pandas + utils.logger. MUST NOT import tkinter
or ProjectManager. Persistence (CSV writes) is NOT done here — that is the
caller's job (TableService.save_table). This class only manages the in-memory
session buffers so the GUI can work with draft/intermediate tables.

The contracts mirror LegacyGUIStateAdapter's variable/result methods so the GUI
can migrate gui_state.X → session_state.X with unchanged behavior.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from utils.logger import log_warning


class SessionState:
    """In-memory session-state buffer. Two DataFrame dicts + a rating dict.

    ``_variables`` keys: ezqc_new (imported draft), ezqc_filter (named, ready
    to merge), ezqc_all (master subject table).
    ``_results`` keys: ezqc_qctable (aggregated QC), ezqc_qctable_filter
    (filtered QC), <module_name> (per-module result tables).
    """

    def __init__(self) -> None:
        self._variables: dict[str, pd.DataFrame | None] = {
            "ezqc_new": None,
            "ezqc_filter": None,
            "ezqc_all": None,
        }
        self._results: dict[str, pd.DataFrame | None] = {
            "ezqc_qctable": None,
            "ezqc_qctable_filter": None,
        }
        self.rating_dict: dict[str, dict[str, Any]] = {}

    # ---- variable getters (return copies for isolation) ----

    def var_table(self, name: str) -> pd.DataFrame | None:
        return self._variables.get(name)

    def new_variable_table(self) -> pd.DataFrame | None:
        df = self._variables.get("ezqc_new")
        return df.copy() if df is not None else None

    def all_variable_table(self) -> pd.DataFrame | None:
        df = self._variables.get("ezqc_all")
        return df.copy() if df is not None else None

    def filtered_variable_table(self) -> pd.DataFrame | None:
        df = self._variables.get("ezqc_filter")
        return df.copy() if df is not None else None

    def has_all_variable_rows(self) -> bool:
        df = self._variables.get("ezqc_all")
        return df is not None and len(df) > 0

    def new_variable_merge_source(self) -> pd.DataFrame | None:
        """Prefer ezqc_filter, fall back to ezqc_new. Returns a copy."""
        df = self._variables.get("ezqc_filter")
        if df is None:
            df = self._variables.get("ezqc_new")
        return df.copy() if df is not None else None

    # ---- variable setters ----

    def set_new_variable_table(self, df: pd.DataFrame | None) -> None:
        self._variables["ezqc_new"] = df  # store reference (matches legacy)

    def set_filtered_variable_table(self, df: pd.DataFrame | None) -> None:
        self._variables["ezqc_filter"] = df.copy() if df is not None else None

    def set_all_variable_table(self, df: pd.DataFrame | None) -> None:
        self._variables["ezqc_all"] = df.copy() if df is not None else None

    # ---- prepare_new_variable_table (derived: rename + sort + sync filter) ----

    def prepare_new_variable_table(self, varname: str) -> pd.DataFrame | None:
        """Normalize ezqc_new: ensure varname column, sort by it, sync ezqc_filter.
        Returns a copy of the prepared table."""
        df = self._variables.get("ezqc_new")
        if df is None:
            return None
        df = df.copy()
        if varname not in df.columns and len(df.columns) == 0:
            df = pd.DataFrame(columns=[varname])
        elif varname not in df.columns and len(df.columns) == 1:
            df.columns = [varname]
        elif varname not in df.columns:
            # multicolumn and varname absent: keep as-is, just sync filter
            self._variables["ezqc_new"] = df
            self._variables["ezqc_filter"] = df.copy()
            return df.copy()
        df = df.sort_values(by=varname, ascending=True)
        self._variables["ezqc_new"] = df
        self._variables["ezqc_filter"] = df.copy()
        return df.copy()

    # ---- merge into ezqc_all ----

    def merge_all_variables_as_rows(self, df: pd.DataFrame) -> None:
        current = self._variables.get("ezqc_all")
        self._variables["ezqc_all"] = (
            pd.concat([current, df]) if current is not None else df.copy()
        )

    def merge_all_variables_as_columns(self, df: pd.DataFrame) -> None:
        current = self._variables.get("ezqc_all")
        self._variables["ezqc_all"] = (
            pd.merge(current, df, on="ezqcid", how="outer")
            if current is not None
            else df.copy()
        )

    # ---- result table (tab) getters ----

    def result_table(self, name: str) -> pd.DataFrame | None:
        return self._results.get(name)

    def qctable_for_display(self) -> pd.DataFrame | None:
        """Display priority: ezqc_qctable_filter > ezqc_qctable > ezqc_all."""
        if self._results.get("ezqc_qctable_filter") is not None:
            return self._results["ezqc_qctable_filter"]
        if self._results.get("ezqc_qctable") is not None:
            return self._results["ezqc_qctable"]
        return self._variables.get("ezqc_all")

    # ---- service injection (apply_loaded_*) ----

    def apply_loaded_tables(self, loaded_tables: Any) -> None:
        """Inject tables loaded by TableService (variables + results). Copies."""
        for name, df in loaded_tables.variables.items():
            self._variables[name] = df.copy() if df is not None else None
        for name, df in loaded_tables.results.items():
            self._results[name] = df.copy() if df is not None else None

    def apply_loaded_ratings(self, loaded_ratings: Any) -> None:
        """Inject ratings loaded by RatingService. Deep-copies to isolate."""
        from copy import deepcopy

        self.rating_dict = deepcopy(loaded_ratings.rating_dict)
        qctable = getattr(loaded_ratings, "qctable", None)
        if qctable is not None:
            self._results["ezqc_qctable"] = qctable.copy()

    # ---- filter undo (pure memory) ----

    def restore_filter_source(self, result_type: str | None, df: pd.DataFrame) -> pd.DataFrame | None:
        """Write the original (pre-filter) source back to its slot. Pure memory.
        Returns a copy when result_type is None; otherwise None."""
        if result_type is None:
            return df.copy()
        if result_type == "new":
            self._variables["ezqc_filter"] = df.copy()
        elif result_type == "all":
            self._variables["ezqc_all"] = df.copy()
        elif result_type == "qctable":
            return None  # qctable not restorable
        else:
            self._results[result_type] = df.copy()
        return None


__all__ = ["SessionState"]
