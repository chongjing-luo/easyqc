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
from gui.state_adapter import LegacyGUIStateAdapter
from models.project import Project
from utils.projects_manager import ProjectManager


def _legacy_project_manager_for(project_dir) -> ProjectManager:
    pm = ProjectManager()
    pm.dt = pm.DataContainer()
    pm.dt.project = "CCNPPEKI_COMPAT"
    pm.dt.output_dir = str(project_dir)
    pm.dt.projects = {"CCNPPEKI_COMPAT": str(project_dir)}
    pm.dt.projects_info = {"projects": pm.dt.projects, "last_project": "CCNPPEKI_COMPAT"}
    pm.dt.settings = json.loads((project_dir / "settings_CCNPPEKI_COMPAT.json").read_text(encoding="utf-8"))
    return pm


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


def test_project_service_loads_sanitized_ccnppeki_compat_fixture(ccnppeki_compat_project_dir, tmp_path) -> None:
    registry_path = tmp_path / "projects.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": {"CCNPPEKI_COMPAT": str(ccnppeki_compat_project_dir)},
                "last_project": "CCNPPEKI_COMPAT",
            }
        ),
        encoding="utf-8",
    )
    service = ProjectService(registry_path)

    project = service.load("CCNPPEKI_COMPAT")
    modules = service.get_modules()

    assert project.path == ccnppeki_compat_project_dir
    assert service.settings["constants"]["subjects_dir"] == "/compat/SUBJECTS_DIRs/CCNPPEKI"
    assert modules["3"].name == "openHCP_DIR"
    assert modules["4"].name == "AnatRestAll"
    assert modules["4"].scores["1"].label == "headmotion"
    assert modules["4"].tags["3"].label == "restfailed"


def test_rating_service_loads_and_aggregates_ccnppeki_compat_fixture(ccnppeki_compat_project_dir) -> None:
    service = RatingService(Project("CCNPPEKI_COMPAT", ccnppeki_compat_project_dir))
    subjects = pd.read_csv(ccnppeki_compat_project_dir / "Table" / "ezqc_all.csv")
    sample = (
        ccnppeki_compat_project_dir
        / "RatingFiles"
        / "AnatRestAll"
        / "rf"
        / "AnatRestAll._.CCNPPEK0001_01_rest01._.rf._.3._.False.json"
    )

    files = service.scan_rating_files()
    ratings = service.load_all_ratings()
    rating = service.load_rating(sample)
    wide = service.aggregate_to_wide(ratings, subjects)

    assert len(files) == 10
    assert len(ratings) == 10
    assert service.validate_rating_file(sample)
    assert rating.module_name == "AnatRestAll"
    assert rating.rater == "rf"
    assert rating.ezqcid == "CCNPPEK0001_01_rest01"
    assert rating.scores["1"] == "3"
    assert rating.scores["4"] == "2"
    assert rating.tags["3"] is False
    assert "皮层重建" in rating.notes

    assert len(wide) == 3
    assert wide["ezqcid"].tolist() == [
        "CCNPPEK0001_01_anat",
        "CCNPPEK0001_01_rest01",
        "CCNPPEK0001_01_rest02",
    ]
    assert "AnatRestAll.rf.score1" in wide.columns
    assert "AnatRestAll.zhuyan.score1" in wide.columns
    assert "hcpall.lcj.score1" in wide.columns
    assert "openHCP_DIR.lcj.tag1" in wide.columns


def test_rating_service_rebuilds_legacy_qctable_snapshot(ccnppeki_compat_project_dir) -> None:
    service = RatingService(Project("CCNPPEKI_COMPAT", ccnppeki_compat_project_dir))
    subjects = pd.read_csv(ccnppeki_compat_project_dir / "Table" / "ezqc_all.csv")
    expected = pd.read_csv(ccnppeki_compat_project_dir / "Table" / "ezqc_qctable.csv")

    actual = service.load_legacy_state(subjects).qctable

    assert actual.shape == expected.shape
    assert actual.columns.tolist() == expected.columns.tolist()
    pdt.assert_frame_equal(
        _normalize_legacy_qctable(actual),
        _normalize_legacy_qctable(expected),
        check_dtype=False,
    )


def test_cli_launch_context_resolves_ccnppeki_compat_fixture(ccnppeki_compat_project_dir, tmp_path) -> None:
    registry_path = tmp_path / "projects.json"
    registry_path.write_text(
        json.dumps(
            {
                "projects": {"CCNPPEKI_COMPAT": str(ccnppeki_compat_project_dir)},
                "last_project": "CCNPPEKI_COMPAT",
            }
        ),
        encoding="utf-8",
    )

    context = resolve_qcpage_launch(
        "CCNPPEKI_COMPAT",
        "AnatRestAll",
        "rf",
        "CCNPPEK0001_01_rest01",
        registry_path,
    )

    assert context.project.path == ccnppeki_compat_project_dir
    assert context.module_index == "4"
    assert context.module_name == "AnatRestAll"
    assert context.module["rater"] == "rf"
    assert context.module_rater_dir == ccnppeki_compat_project_dir / "RatingFiles" / "AnatRestAll" / "rf"


def test_legacy_project_manager_loads_ccnppeki_compat_tables_and_ratings(ccnppeki_compat_project_dir) -> None:
    pm = _legacy_project_manager_for(ccnppeki_compat_project_dir)

    pm.load_table()
    pm.load_ratings()

    assert len(pm.dt.var["ezqc_all"]) == 3
    assert len(pm.dt.tab["AnatRestAll"]) == 3
    assert len(pm.dt.tab["openHCP_DIR"]) == 3
    assert pm.dt.tab["ezqc_qctable"] is not None
    assert len(pm.dt.tab["ezqc_qctable"]) == 3
    assert pm.dt.rating_dict["CCNPPEK0001_01_rest01"]["AnatRestAll-rf"]["scores"]["1"]["value"] == "3"
    assert "AnatRestAll.rf.score1" in pm.dt.tab["ezqc_qctable"].columns
    assert "openHCP_DIR.lcj.tag1" in pm.dt.tab["ezqc_qctable"].columns


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


def test_gui_state_adapter_applies_ccnppeki_compat_service_loaded_tables(ccnppeki_compat_project_dir) -> None:
    service = TableService()
    project = Project("CCNPPEKI_COMPAT", ccnppeki_compat_project_dir)
    loaded_tables = service.load_legacy_state_tables(project, module_names=["AnatRestAll", "openCCS_DIR"])
    dt = _legacy_project_manager_for(ccnppeki_compat_project_dir).dt
    adapter = LegacyGUIStateAdapter(dt=dt)

    adapter.apply_loaded_tables(loaded_tables)

    assert len(dt.var[TABLE_ALL]) == 3
    assert len(dt.tab[TABLE_QCTABLE]) == 3
    assert len(dt.tab["AnatRestAll"]) == 3
    assert dt.tab["openCCS_DIR"] is None
