from __future__ import annotations

import platform
from copy import deepcopy
from pathlib import Path
from typing import Any

import pandas as pd

from core.table_service import TABLE_QCTABLE
from models.project import Project


class LegacyGUIStateAdapter:
    """Compatibility boundary for GUI access to the legacy ProjectManager state."""

    def __init__(self, project_manager: Any = None, dt: Any = None) -> None:
        self.project_manager = project_manager
        self.dt = dt if dt is not None else getattr(project_manager, "dt", None)

    def has_project_registry(self) -> bool:
        return self.dt is not None and hasattr(self.dt, "projects")

    def project_names(self) -> list[str]:
        if not self.has_project_registry():
            return []
        return list(self.dt.projects.keys())

    def has_project(self, name: str) -> bool:
        return self.has_project_registry() and name in self.dt.projects

    def project_display_rows(self) -> list[str]:
        if not self.has_project_registry():
            return []

        rows = []
        for project, info in self.dt.projects.items():
            display_text = f"{project} - {info['path']}" if isinstance(info, dict) and "path" in info else f"{project} - {info}"
            rows.append(display_text)
        return rows

    def current_project_name(self) -> str | None:
        return getattr(self.dt, "project", None)

    def current_project_model(self) -> Project | None:
        name = self.current_project_name()
        output_dir = getattr(self.dt, "output_dir", None)
        if not name or not output_dir:
            return None
        return Project(name=name, path=Path(output_dir))

    def create_and_load_project(self, name: str, path: str) -> None:
        self.project_manager.create_project(name, path)
        self.project_manager.load_project(name)

    def load_project(self, project: str | None = None, output_dir: str | None = None) -> None:
        self.project_manager.load_project(project, output_dir=output_dir)

    def import_project_from_dir(self, path: str) -> None:
        self.project_manager.load_project(output_dir=path)

    def remove_project(self, project_name: str) -> None:
        self.project_manager.rm_project(project_name)

    def change_project(self, project_name: str) -> None:
        if self.project_manager is not None:
            self.project_manager.change_project(project_name)

    def system_name(self) -> str:
        return getattr(self.dt, "system", platform.system())

    def settings(self) -> dict[str, Any]:
        return getattr(self.dt, "settings", {}) or {}

    def qcmodule(self) -> dict[str, dict[str, Any]]:
        return self.settings().get("qcmodule", {}) or {}

    def constants(self) -> dict[str, Any]:
        return self.settings().setdefault("constants", {})

    def constant_items(self):
        return self.constants().items()

    def has_constant(self, name: str) -> bool:
        return name in self.constants()

    def set_constant(self, name: str, value: Any) -> None:
        self.constants()[name] = value
        self.save_settings()

    def rename_constant(self, old_name: str, new_name: str, value: Any) -> None:
        if old_name != new_name:
            del self.constants()[old_name]
        self.constants()[new_name] = value
        self.save_settings()

    def delete_constant(self, name: str) -> None:
        del self.constants()[name]
        self.save_settings()

    def save_settings(self) -> None:
        if self.project_manager is not None:
            self.project_manager.save_settings()

    def save_project_state(self) -> None:
        if self.project_manager is None:
            return
        self.project_manager.save_settings()
        self.project_manager.save_table()

    def load_ratings(self) -> None:
        if self.project_manager is not None:
            self.project_manager.load_ratings()

    def add_score(self, qcindex: str, idx: int) -> None:
        module = {"label": None, "num": None, "num_": None, "value": None}
        scores = self.qcmodule()[qcindex]["scores"]
        self.qcmodule()[qcindex]["scores"] = self.project_manager.add_key(scores, idx, module)
        self.save_settings()

    def delete_score(self, qcindex: str, idx: int) -> None:
        scores = self.qcmodule()[qcindex]["scores"]
        self.qcmodule()[qcindex]["scores"] = self.project_manager.add_key(scores, idx)
        self.save_settings()

    def add_tag(self, qcindex: str, idx: int) -> None:
        module = {"label": None, "value": None}
        tags = self.qcmodule()[qcindex]["tags"]
        self.qcmodule()[qcindex]["tags"] = self.project_manager.add_key(tags, idx, module)
        self.save_settings()

    def delete_tag(self, qcindex: str, idx: int) -> None:
        tags = self.qcmodule()[qcindex]["tags"]
        self.qcmodule()[qcindex]["tags"] = self.project_manager.add_key(tags, idx)
        self.save_settings()

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
        return next((key for key, module in self.qcmodule().items() if module.get("name") == module_name), None)

    def module_names(self) -> list[str]:
        return [module.get("name") for module in self.qcmodule().values() if module.get("name")]

    def module_table_rows(self) -> list[tuple[str, Any, Any]]:
        return [(index, module.get("name"), module.get("label")) for index, module in self.qcmodule().items()]

    def next_module_index(self) -> str:
        modules = self.qcmodule()
        if not modules:
            return "1"
        return str(max(int(index) for index in modules.keys()) + 1)

    def module_name_exists(self, name: str, exclude_name: str | None = None) -> bool:
        return any(
            module.get("name") == name
            for module in self.qcmodule().values()
            if module.get("name") != exclude_name
        )

    def check_module(self, module: dict[str, Any]) -> bool:
        return self.project_manager.check_module(module)

    def add_module(self, name: str, label: str, index: str | int) -> None:
        self.dt.settings["qcmodule"] = self.project_manager.add_qcmodule(
            self.qcmodule(),
            index,
            name,
            label,
        )
        self.reorder_modules()
        self.save_settings()

    def modify_module(self, index: str, name: str, label: str, new_index: str | int) -> None:
        self.project_manager.modify_qcmodule(index, name_=name, label_=label, index_=new_index)
        self.reorder_modules()
        self.save_settings()

    def insert_module(self, index: str | int, module: dict[str, Any]) -> None:
        self.dt.settings["qcmodule"] = self.project_manager.add_key(self.qcmodule(), int(index), module)
        self.save_settings()

    def delete_module(self, index: str | int) -> None:
        self.dt.settings["qcmodule"] = self.project_manager.add_key(self.qcmodule(), int(index))
        self.save_settings()

    def can_delete_module(self) -> bool:
        return len(self.qcmodule()) > 1

    def export_module(self, module_name: str, path: str) -> None:
        self.project_manager.export_module(module_name, path)

    def reorder_modules(self) -> None:
        sorted_modules = sorted(self.qcmodule().items(), key=lambda item: int(item[0]))
        self.dt.settings["qcmodule"] = {
            str(index): module_data
            for index, (_, module_data) in enumerate(sorted_modules, 1)
        }

    def rating_menu_items(self, ezqcid: str) -> list[dict[str, str]]:
        rating_dict = getattr(self.dt, "rating_dict", None)
        if not rating_dict or ezqcid not in rating_dict:
            return []

        items = []
        for key, rating_data in rating_dict[ezqcid].items():
            if not isinstance(rating_data, dict):
                continue
            name = rating_data.get("name")
            rater = rating_data.get("rater")
            if name and rater:
                items.append({
                    "label": f"打开评分结果: {key}",
                    "name": name,
                    "rater": rater,
                })
        return items

    def has_rating_data(self) -> bool:
        return hasattr(self.dt, "rating_dict")

    def var_table(self, name: str):
        return getattr(self.dt, "var", {}).get(name)

    def set_new_variable_table(self, df: pd.DataFrame | None) -> None:
        self.dt.var["ezqc_new"] = df

    def new_variable_table(self) -> pd.DataFrame | None:
        df = self.var_table("ezqc_new")
        return df.copy() if df is not None else None

    def prepare_new_variable_table(self, varname: str) -> pd.DataFrame | None:
        df = self.var_table("ezqc_new")
        if df is None:
            return None

        df = df.copy()
        if varname not in df.columns and len(df.columns) == 0:
            df = pd.DataFrame(columns=[varname])
        elif varname not in df.columns and len(df.columns) == 1:
            df.columns = [varname]
        elif varname not in df.columns:
            self.dt.var["ezqc_new"] = df
            self.dt.var["ezqc_filter"] = df.copy()
            return df.copy()

        df = df.sort_values(by=varname, ascending=True)
        self.dt.var["ezqc_new"] = df
        self.dt.var["ezqc_filter"] = df.copy()
        return df.copy()

    def new_variable_merge_source(self) -> pd.DataFrame | None:
        df = self.var_table("ezqc_filter")
        if df is None:
            df = self.var_table("ezqc_new")
        return df.copy() if df is not None else None

    def filtered_variable_table(self) -> pd.DataFrame | None:
        df = self.var_table("ezqc_filter")
        return df.copy() if df is not None else None

    def set_filtered_variable_table(self, df: pd.DataFrame | None) -> None:
        self.dt.var["ezqc_filter"] = df.copy() if df is not None else None

    def all_variable_table(self) -> pd.DataFrame | None:
        df = self.var_table("ezqc_all")
        return df.copy() if df is not None else None

    def has_all_variable_rows(self) -> bool:
        df = self.var_table("ezqc_all")
        return df is not None and len(df) > 0

    def set_all_variable_table(self, df: pd.DataFrame | None) -> None:
        self.dt.var["ezqc_all"] = df.copy() if df is not None else None

    def merge_all_variables_as_rows(self, df: pd.DataFrame) -> None:
        current = self.var_table("ezqc_all")
        self.dt.var["ezqc_all"] = pd.concat([current, df]) if current is not None else df.copy()

    def merge_all_variables_as_columns(self, df: pd.DataFrame) -> None:
        current = self.var_table("ezqc_all")
        self.dt.var["ezqc_all"] = pd.merge(current, df, on="ezqcid", how="outer") if current is not None else df.copy()

    def save_all_variable_table(self) -> None:
        if self.project_manager is not None:
            self.project_manager.save_table("ezqc_all")

    def refresh_project_after_variable_merge(self) -> None:
        if self.project_manager is None:
            return
        self.project_manager.save_table("ezqc_all")
        self.project_manager.save_table("table", delete=True)
        self.project_manager.load_project(self.current_project_name())

    def result_table(self, name: str):
        return getattr(self.dt, "tab", {}).get(name)

    def apply_loaded_tables(self, loaded_tables: Any) -> None:
        var = getattr(self.dt, "var", None)
        tab = getattr(self.dt, "tab", None)
        if var is None:
            self.dt.var = {}
            var = self.dt.var
        if tab is None:
            self.dt.tab = {}
            tab = self.dt.tab

        for name, df in loaded_tables.variables.items():
            var[name] = df.copy() if df is not None else None
        for name, df in loaded_tables.results.items():
            tab[name] = df.copy() if df is not None else None

    def apply_loaded_ratings(self, loaded_ratings: Any) -> None:
        tab = getattr(self.dt, "tab", None)
        if tab is None:
            self.dt.tab = {}
            tab = self.dt.tab

        self.dt.rating_dict = deepcopy(loaded_ratings.rating_dict)
        tab[TABLE_QCTABLE] = loaded_ratings.qctable.copy()

    def qctable_for_display(self):
        tab = getattr(self.dt, "tab", {})
        var = getattr(self.dt, "var", {})
        if "ezqc_qctable_filter" in tab:
            return tab["ezqc_qctable_filter"]
        if "ezqc_qctable" in tab:
            return tab["ezqc_qctable"]
        return var.get("ezqc_all")

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
            return self.result_table("ezqc_qctable").copy(), settings.get("select_filter")

        index = self.module_index_by_name(result_type)
        select_filter = self.qcmodule()[index].get("select_filter") if index is not None else None
        source = self.result_table("ezqc_qctable")
        if source is None:
            source = self.var_table("ezqc_all")
        return (source.copy() if source is not None else None), select_filter

    def save_filter_result(self, result_type, df_output: pd.DataFrame, query: str) -> None:
        if result_type == "new":
            self.dt.var["ezqc_filter"] = df_output.copy()
            return
        if result_type == "all":
            self.dt.var["ezqc_all"] = df_output.copy()
            return
        if result_type == "qctable":
            self.dt.tab["ezqc_qctable_filter"] = df_output.copy()
            self.dt.settings["select_filter"] = query
            self.project_manager.save_settings()
            self.project_manager.save_table("ezqc_qctable_filter")
            return

        self.dt.tab[result_type] = df_output.copy()
        index = self.module_index_by_name(result_type)
        self.dt.settings["qcmodule"][index]["select_filter"] = query
        self.project_manager.save_table(result_type)
        self.project_manager.save_settings()

    def restore_filter_source(self, result_type, df: pd.DataFrame):
        if result_type is None:
            return df.copy()
        if result_type == "new":
            self.dt.var["ezqc_filter"] = df.copy()
        elif result_type == "all":
            self.dt.var["ezqc_all"] = df.copy()
        elif result_type == "qctable":
            return None
        else:
            self.dt.tab[result_type] = df.copy()
        return None


__all__ = ["LegacyGUIStateAdapter"]
