from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pandas as pd

from models.project import Project


TABLE_ALL: Final = "ezqc_all"
TABLE_QCTABLE: Final = "ezqc_qctable"
TABLE_QCTABLE_FILTER: Final = "ezqc_qctable_filter"


@dataclass
class LoadedProjectTables:
    variables: dict[str, pd.DataFrame | None]
    results: dict[str, pd.DataFrame | None]


class TableService:
    def table_path(self, project: Project, table_type: str) -> Path:
        return project.table_dir / f"{table_type}.csv"

    def load_table(self, project: Project, table_type: str) -> pd.DataFrame | None:
        path = self.table_path(project, table_type)
        if not path.exists():
            return None
        return pd.read_csv(path, encoding="utf-8")

    def save_table(
        self,
        project: Project,
        table_type: str,
        df: pd.DataFrame | None,
        delete: bool = False,
    ) -> None:
        path = self.table_path(project, table_type)
        project.table_dir.mkdir(parents=True, exist_ok=True)

        if delete:
            if path.exists():
                path.unlink()
            return

        if df is None:
            return

        temp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}")
        try:
            df.to_csv(temp_path, index=False, encoding="utf-8")
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def load_all_tables(self, project: Project) -> dict[str, pd.DataFrame]:
        tables: dict[str, pd.DataFrame] = {}
        if not project.table_dir.exists():
            return tables

        for path in project.table_dir.glob("ezqc_*.csv"):
            table = self.load_table(project, path.stem)
            if table is not None:
                tables[path.stem] = table
        return tables

    def load_legacy_state_tables(
        self,
        project: Project,
        module_names: list[str] | tuple[str, ...] | None = None,
    ) -> LoadedProjectTables:
        """Load project tables in the shape expected by the legacy GUI state."""

        variables: dict[str, pd.DataFrame | None] = {
            TABLE_ALL: self.load_table(project, TABLE_ALL),
        }
        results: dict[str, pd.DataFrame | None] = {
            TABLE_QCTABLE: self.load_table(project, TABLE_QCTABLE),
            TABLE_QCTABLE_FILTER: self.load_table(project, TABLE_QCTABLE_FILTER),
        }

        if project.table_dir.exists():
            for path in sorted(project.table_dir.glob("ezqc_*.csv")):
                table_type = path.stem
                if table_type in {TABLE_ALL, TABLE_QCTABLE, TABLE_QCTABLE_FILTER}:
                    continue
                results[self.module_name_from_table_type(table_type)] = self.load_table(project, table_type)

        for module_name in module_names or ():
            results.setdefault(module_name, None)

        return LoadedProjectTables(variables=variables, results=results)

    @staticmethod
    def module_name_from_table_type(table_type: str) -> str:
        return table_type.removeprefix("ezqc_")


__all__ = [
    "TABLE_ALL",
    "TABLE_QCTABLE",
    "TABLE_QCTABLE_FILTER",
    "LoadedProjectTables",
    "TableService",
]
