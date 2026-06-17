import json
from pathlib import Path
from typing import Any

import pandas as pd
import pandas.testing as pdt

from core.cli_service import resolve_qcpage_launch
from core.project_service import ProjectService
from core.rating_service import RatingService
from core.table_service import TABLE_ALL, TABLE_QCTABLE, TABLE_QCTABLE_FILTER, TableService
from gui.table_view import TableTransformDialog
from gui.state_bridge import GUIStateBridge
from models.project import Project



def _normalize_legacy_qctable_value(value: Any, *, filepath_column: bool = False) -> str:
    if pd.isna(value) or value == "":
        return ""
    if filepath_column:
        return Path(str(value)).name
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric.is_integer():
        return str(int(numeric))
    return str(value)


def _normalize_legacy_qctable(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    for column in normalized.columns:
        normalized[column] = [
            _normalize_legacy_qctable_value(value, filepath_column=".filepath" in column)
            for value in normalized[column].tolist()
        ]
    return normalized



def test_ccnppeki_compat_legacy_select_filter_converts_to_table_transform(ccnppeki_compat_project_dir) -> None:
    settings = json.loads((ccnppeki_compat_project_dir / "settings_CCNPPEKI_COMPAT.json").read_text(encoding="utf-8"))
    hcpall_filter = settings["qcmodule"]["7"]["select_filter"]
    df = pd.read_csv(ccnppeki_compat_project_dir / "Table" / "ezqc_all.csv")

    result = TableTransformDialog(None).execute_query(df, hcpall_filter)

    assert hcpall_filter == "SELECT * FROM df WHERE mod = 'rest'"
    assert result["ezqcid"].tolist() == ["CCNPPEK0001_01_rest01", "CCNPPEK0001_01_rest02"]


def test_table_service_loads_ccnppeki_compat_tables_as_legacy_state(ccnppeki_compat_project_dir) -> None:
    service = TableService()
    project = Project("CCNPPEKI_COMPAT", ccnppeki_compat_project_dir)

    tables = service.load_legacy_state_tables(
        project,
        module_names=["openCCS_DIR", "openSUBJECT_DIR", "openHCP_DIR", "AnatRestAll", "Skullstrip", "hcpall"],
    )

    assert len(tables.variables[TABLE_ALL]) == 3
    assert len(tables.results[TABLE_QCTABLE]) == 3
    assert len(tables.results[TABLE_QCTABLE_FILTER]) == 3
    assert len(tables.results["AnatRestAll"]) == 3
    assert len(tables.results["openHCP_DIR"]) == 3
    assert len(tables.results["hcpall"]) == 2
    assert tables.results["openCCS_DIR"] is None
    assert tables.results["openSUBJECT_DIR"] is None


def test_gui_state_bridge_applies_ccnppeki_compat_service_loaded_tables(ccnppeki_compat_project_dir) -> None:
    from core.session_state import SessionState
    service = TableService()
    project = Project("CCNPPEKI_COMPAT", ccnppeki_compat_project_dir)
    loaded_tables = service.load_legacy_state_tables(project, module_names=["AnatRestAll", "openCCS_DIR"])

    session = SessionState()
    session.apply_loaded_tables(loaded_tables)

    assert len(session._variables[TABLE_ALL]) == 3
    assert len(session._results[TABLE_QCTABLE]) == 3
    assert len(session._results["AnatRestAll"]) == 3
    assert session._results["openCCS_DIR"] is None

