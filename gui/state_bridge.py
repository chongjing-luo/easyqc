"""GUIStateBridge — service-backed replacement for LegacyGUIStateAdapter (P2 step 3).

Implements the SAME interface (method names, signatures, qcindex-based module
identity) as LegacyGUIStateAdapter, but delegates entirely to ProjectService +
SessionState + TableService. Zero ProjectManager / DataContainer dependency.

dialogs.py and main_window.py are unchanged: only the source of ``gui_state``
swaps from LegacyGUIStateAdapter to GUIStateBridge (one line in main_window
construction). This lets us remove the ProjectManager dependency from the GUI
state layer without touching the 95 call sites.

The bridge provides a ``dt`` compat shim (settings/tab/var/output_dir) so that
``QCPageRuntimeContext.from_gui_state`` keeps working for the GUI QC-page path
until that path is also migrated.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

import pandas as pd

from core.session_state import SessionState
from core.table_service import TABLE_QCTABLE
from models.project import Project


class _DTCompat:
    """Minimal shim exposing settings/tab/var/output_dir/dir_module_rater for
    QCPageRuntimeContext.from_legacy_dt compatibility."""

    def __init__(self, bridge: "GUIStateBridge") -> None:
        self._bridge = bridge
        self.dir_module_rater = None

    @property
    def settings(self):
        return self._bridge.settings()

    @property
    def tab(self):
        return self._bridge.session_state._results

    @property
    def var(self):
        return self._bridge.session_state._variables

    @property
    def output_dir(self):
        cp = self._bridge.project_service.current_project
        return str(cp.path) if cp is not None else None

    @property
    def rating_dict(self):
        return self._bridge.session_state.rating_dict

    @rating_dict.setter
    def rating_dict(self, value):
        self._bridge.session_state.rating_dict = value


class GUIStateBridge:
    """Service-backed gui_state. Same interface as LegacyGUIStateAdapter."""

    def __init__(
        self,
        project_service: Any,
        session_state: SessionState | None = None,
        table_service: Any = None,
    ) -> None:
        self.project_service = project_service
        self.session_state = session_state or SessionState()
        self.table_service = table_service
        self.dt = _DTCompat(self)
        # accept project_manager kwarg for drop-in compat (ignored)
        self.project_manager = None

    # ---- registry / project lifecycle ----

    def has_project_registry(self) -> bool:
        return True

    def project_names(self) -> list[str]:
        return list(self.project_service.registry.projects.keys())

    def has_project(self, name: str) -> bool:
        return self.project_service.has_project(name)

    def project_display_rows(self) -> list[str]:
        rows = []
        for name, proj in self.project_service.registry.projects.items():
            path = proj.path if isinstance(proj, Project) else proj
            rows.append(f"{name} - {path}")
        return rows

    def current_project_name(self) -> str | None:
        return self.project_service.current_project_name()

    def current_project_model(self) -> Project | None:
        return self.project_service.current_project

    def create_and_load_project(self, name: str, path: str) -> None:
        self.project_service.create(name, Path(path))

    def load_project(self, project: str | None = None, output_dir: str | None = None) -> None:
        if output_dir is not None:
            self.project_service.import_project_from_dir(output_dir)
        else:
            self.project_service.load(project)

    def import_project_from_dir(self, path: str) -> None:
        self.project_service.import_project_from_dir(path)

    def remove_project(self, project_name: str) -> None:
        self.project_service.remove(project_name)

    def change_project(self, project_name: str) -> None:
        self.project_service.load(project_name)

    def system_name(self) -> str:
        return platform.system()

    # ---- settings / constants ----

    def settings(self) -> dict[str, Any]:
        return self.project_service.settings

    def qcmodule(self) -> dict[str, dict[str, Any]]:
        return self.settings().get("qcmodule", {}) or {}

    def constants(self) -> dict[str, Any]:
        return self.project_service.constants()

    def constant_items(self):
        return self.constants().items()

    def has_constant(self, name: str) -> bool:
        return self.project_service.has_constant(name)

    def set_constant(self, name: str, value: Any) -> None:
        self.project_service.set_constant(name, value)
        self.project_service.save()

    def rename_constant(self, old_name: str, new_name: str, value: Any) -> None:
        self.project_service.rename_constant(old_name, new_name, value)
        self.project_service.save()

    def delete_constant(self, name: str) -> None:
        self.project_service.delete_constant(name)
        self.project_service.save()

    def save_settings(self) -> None:
        self.project_service.save()

    def save_project_state(self) -> None:
        self.project_service.save()
        # persist session tables via TableService if available
        if self.table_service is not None:
            cp = self.project_service.current_project
            if cp is not None:
                for name, df in self.session_state._variables.items():
                    if df is not None:
                        self.table_service.save_table(cp, name, df)
                for name, df in self.session_state._results.items():
                    if df is not None:
                        self.table_service.save_table(cp, name, df)

    def load_ratings(self) -> None:
        # delegate to RatingService via the main_window sync path; bridge itself
        # does not own a RatingService (main_window calls _load_ratings_from_service)
        pass

    # ---- score / tag (qcindex-based, matches adapter signature) ----

    def add_score(self, qcindex: str, idx: int) -> None:
        module = self.qcmodule()[qcindex]
        name = module.get("name")
        self.project_service.add_score(name, idx)
        self.project_service.save()

    def delete_score(self, qcindex: str, idx: int) -> None:
        module = self.qcmodule()[qcindex]
        name = module.get("name")
        self.project_service.delete_score(name, idx)
        self.project_service.save()

    def add_tag(self, qcindex: str, idx: int) -> None:
        module = self.qcmodule()[qcindex]
        name = module.get("name")
        self.project_service.add_tag(name, idx)
        self.project_service.save()

    def delete_tag(self, qcindex: str, idx: int) -> None:
        module = self.qcmodule()[qcindex]
        name = module.get("name")
        self.project_service.delete_tag(name, idx)
        self.project_service.save()

    # ---- module CRUD ----

    def module_keys(self) -> list[str]:
        keys = []
        for key in self.qcmodule().keys():
            try:
                keys.append(int(key))
            except (TypeError, ValueError):
                continue
        return [str(key) for key in sorted(keys)]

    def module_by_key(self, key: str) -> dict[str, Any]:
        return self.qcmodule()[str(key)]

    def update_module_field(self, qcindex: str, field: str, value: Any) -> None:
        self.module_by_key(qcindex)[field] = value

    def update_score_fields(self, qcindex: str, score_key: str, **fields: Any) -> None:
        self.module_by_key(qcindex)["scores"][score_key].update(fields)

    def update_tag_fields(self, qcindex: str, tag_key: str, **fields: Any) -> None:
        self.module_by_key(qcindex)["tags"][tag_key].update(fields)

    def module_index_by_name(self, module_name: str) -> str | None:
        return self.project_service.module_index_by_name(module_name)

    def module_names(self) -> list[str]:
        return [m.get("name") for m in self.qcmodule().values() if m.get("name")]

    def module_table_rows(self) -> list[tuple[str, Any, Any]]:
        return [(index, m.get("name"), m.get("label")) for index, m in self.qcmodule().items()]

    def next_module_index(self) -> str:
        modules = self.qcmodule()
        if not modules:
            return "1"
        return str(max(int(i) for i in modules.keys()) + 1)

    def module_name_exists(self, name: str, exclude_name: str | None = None) -> bool:
        return self.project_service.module_name_exists(name, exclude_name=exclude_name)

    def check_module(self, module: dict[str, Any]) -> bool:
        """Validate required module fields. Returns True if OK."""
        if not module.get("name"):
            return False
        if not module.get("label"):
            return False
        return True

    def add_module(self, name: str, label: str, index: str | int) -> None:
        self.project_service.add_module(name, label, index=int(index))
        self.reorder_modules()
        self.project_service.save()

    def modify_module(self, index: str, name: str, label: str, new_index: str | int) -> None:
        # rename + relabel + reorder
        qcidx = str(index)
        module = self.module_by_key(qcidx)
        module["name"] = name
        module["label"] = label
        self.reorder_modules()
        self.project_service.save()

    def insert_module(self, index: str | int, module: dict[str, Any]) -> None:
        self.project_service.insert_module(int(index), module)
        self.project_service.save()

    def delete_module(self, index: str | int) -> None:
        self.project_service.delete_module(int(index))

    def can_delete_module(self) -> bool:
        return self.project_service.can_delete_module()

    def export_module(self, module_name: str, path: str) -> None:
        self.project_service.export_module(module_name, path)

    def reorder_modules(self) -> None:
        sorted_modules = sorted(self.qcmodule().items(), key=lambda item: int(item[0]))
        self.project_service._settings["qcmodule"] = {
            str(i): mod for i, (_, mod) in enumerate(sorted_modules, 1)
        }

    # ---- rating dict (session) ----

    def rating_menu_items(self, ezqcid: str) -> list[dict[str, str]]:
        rd = self.session_state.rating_dict
        if not rd or ezqcid not in rd:
            return []
        items = []
        for key, rating_data in rd[ezqcid].items():
            if not isinstance(rating_data, dict):
                continue
            name = rating_data.get("name")
            rater = rating_data.get("rater")
            if name and rater:
                items.append({"label": f"打开评分结果: {key}", "name": name, "rater": rater})
        return items

    def has_rating_data(self) -> bool:
        return bool(self.session_state.rating_dict)

    # ---- variable / table session state (delegate to SessionState) ----

    def var_table(self, name: str):
        return self.session_state.var_table(name)

    def set_new_variable_table(self, df: pd.DataFrame | None) -> None:
        self.session_state.set_new_variable_table(df)

    def new_variable_table(self) -> pd.DataFrame | None:
        return self.session_state.new_variable_table()

    def prepare_new_variable_table(self, varname: str) -> pd.DataFrame | None:
        return self.session_state.prepare_new_variable_table(varname)

    def new_variable_merge_source(self) -> pd.DataFrame | None:
        return self.session_state.new_variable_merge_source()

    def filtered_variable_table(self) -> pd.DataFrame | None:
        return self.session_state.filtered_variable_table()

    def set_filtered_variable_table(self, df: pd.DataFrame | None) -> None:
        self.session_state.set_filtered_variable_table(df)

    def all_variable_table(self) -> pd.DataFrame | None:
        return self.session_state.all_variable_table()

    def has_all_variable_rows(self) -> bool:
        return self.session_state.has_all_variable_rows()

    def set_all_variable_table(self, df: pd.DataFrame | None) -> None:
        self.session_state.set_all_variable_table(df)

    def merge_all_variables_as_rows(self, df: pd.DataFrame) -> None:
        self.session_state.merge_all_variables_as_rows(df)

    def merge_all_variables_as_columns(self, df: pd.DataFrame) -> None:
        self.session_state.merge_all_variables_as_columns(df)

    def save_all_variable_table(self) -> None:
        if self.table_service is not None:
            cp = self.project_service.current_project
            if cp is not None:
                df = self.session_state.all_variable_table()
                if df is not None:
                    self.table_service.save_table(cp, "ezqc_all", df)

    def refresh_project_after_variable_merge(self) -> None:
        cp = self.project_service.current_project
        if cp is None or self.table_service is None:
            return
        df = self.session_state.all_variable_table()
        if df is not None:
            self.table_service.save_table(cp, "ezqc_all", df)
        self.project_service.load(self.current_project_name())

    def result_table(self, name: str):
        return self.session_state.result_table(name)

    def apply_loaded_tables(self, loaded_tables: Any) -> None:
        self.session_state.apply_loaded_tables(loaded_tables)

    def apply_loaded_ratings(self, loaded_ratings: Any) -> None:
        self.session_state.apply_loaded_ratings(loaded_ratings)
        self.dt.dir_module_rater = None  # reset

    def qctable_for_display(self):
        return self.session_state.qctable_for_display()

    def resolve_filter_source(self, result_type=None, df: pd.DataFrame | None = None):
        select_filter = None
        if result_type is None:
            return df.copy(), select_filter
        if df is not None:
            raise ValueError("缺乏必要参数")
        settings = self.settings()
        if result_type == "new":
            source = self.var_table("ezqc_new")
            return (source.copy() if source is not None else None), select_filter
        if result_type == "all":
            source = self.var_table("ezqc_all")
            return source.copy(), settings.get("var_select_filter")
        if result_type == "qctable":
            rt = self.result_table("ezqc_qctable")
            return (rt.copy() if rt is not None else None), settings.get("select_filter")
        index = self.module_index_by_name(result_type)
        select_filter = self.qcmodule()[index].get("select_filter") if index is not None else None
        source = self.result_table("ezqc_qctable")
        if source is None:
            source = self.var_table("ezqc_all")
        return (source.copy() if source is not None else None), select_filter

    def save_filter_result(self, result_type, df_output: pd.DataFrame, query: str) -> None:
        if result_type == "new":
            self.session_state._variables["ezqc_filter"] = df_output.copy()
            return
        if result_type == "all":
            self.session_state._variables["ezqc_all"] = df_output.copy()
            return
        cp = self.project_service.current_project
        if result_type == "qctable":
            self.session_state._results["ezqc_qctable_filter"] = df_output.copy()
            self.project_service._settings["select_filter"] = query
            self.project_service.save()
            if self.table_service is not None and cp is not None:
                self.table_service.save_table(cp, "ezqc_qctable_filter", df_output)
            return
        self.session_state._results[result_type] = df_output.copy()
        index = self.module_index_by_name(result_type)
        if index is not None:
            self.project_service._settings["qcmodule"][index]["select_filter"] = query
        if self.table_service is not None and cp is not None:
            self.table_service.save_table(cp, result_type, df_output)
        self.project_service.save()

    def restore_filter_source(self, result_type, df: pd.DataFrame):
        return self.session_state.restore_filter_source(result_type, df)


__all__ = ["GUIStateBridge"]
