import inspect
import tkinter as tk
from types import SimpleNamespace

import pandas as pd
import pytest

from core.table_transform import TableTransformEngine
from gui import gui_table
from gui.gui_table import TableDisplay
from gui.state_bridge import GUIStateBridge
from gui.table_view import TableTransformDialog
from utils.data_manager import DataManager


def _display() -> TableDisplay:
    display = TableDisplay.__new__(TableDisplay)
    display.dt = SimpleNamespace(
        var={
            "ezqc_new": pd.DataFrame({"ezqcid": ["SUB002"]}),
            "ezqc_all": pd.DataFrame({"ezqcid": ["SUB001"]}),
        },
        tab={
            "ezqc_qctable": pd.DataFrame({"ezqcid": ["SUB003"]}),
        },
        settings={
            "var_select_filter": "var filter",
            "select_filter": "qc filter",
            "qcmodule": {
                "1": {
                    "name": "example",
                    "select_filter": "module filter",
                }
            },
        },
    )
    return display


class _FakeProjectManager:
    def __init__(self) -> None:
        self.saved_settings = 0
        self.saved_tables = []

    def save_settings(self) -> None:
        self.saved_settings += 1

    def save_table(self, name) -> None:
        self.saved_tables.append(name)








def test_open_image_from_right_menu_passes_explicit_qcpage_context() -> None:
    source = inspect.getsource(TableDisplay.open_image_from_right_menu)

    assert "state = self.state_adapter()" in source
    assert "settings = state.settings()" in source
    assert "qcpage_instance.gen_code(ezqcid, settings, module, table)" in source
    assert "qcpage_instance.dt = self.dt" not in source
    assert "self.dt.settings" not in source
    assert "qcpage_instance.module_index" not in source


class _FakeDataManager:
    def __init__(self) -> None:
        self.received_df = None
        self.received_query = None
        self.received_operations = None

    def transform_table(self, df, operations):
        self.received_df = df
        self.received_operations = operations
        return df.assign(transformed=True)


def test_execute_filter_query_rejects_non_json_text() -> None:
    display = _display()
    display.DataM = _FakeDataManager()
    df = pd.DataFrame({"ezqcid": ["SUB001"], "age": [29], "flag": [True]})

    try:
        display.execute_filter_query(df, "age >= 18")
    except ValueError as exc:
        assert "JSON 结构化表格转换操作" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
    assert display.DataM.received_df is None
    assert display.DataM.received_operations is None


def test_execute_filter_query_rejects_empty_query() -> None:
    display = _display()
    display.DataM = _FakeDataManager()

    try:
        display.execute_filter_query(pd.DataFrame({"ezqcid": ["SUB001"]}), "  ")
    except ValueError as exc:
        assert "请输入 JSON 结构化表格转换操作" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_default_transform_template_is_structured_json_and_executable() -> None:
    display = _display()
    display.DataM = DataManager()
    df = pd.DataFrame({"ezqcid": ["SUB001", "SUB002"], "score": [1, 3], "label": ["A", "B"]})

    template = display.default_transform_template(df)
    operations = display.parse_transform_operations(template)
    result = display.execute_filter_query(df, template)

    assert operations[0] == {"operation": "derive_column", "name": "score_valid", "expression": "notna(score)"}
    assert result["ezqcid"].tolist() == ["SUB002", "SUB001"]
    assert list(result.columns) == ["ezqcid", "score", "score_valid", "label"]
    assert result["score_valid"].tolist() == [True, True]


def test_default_transform_template_stays_executable_with_special_column_names() -> None:
    display = _display()
    display.DataM = DataManager()
    df = pd.DataFrame({"subject id": ["SUB001"], "score-value": [3]})

    template = display.default_transform_template(df)
    result = display.execute_filter_query(df, template)

    assert result.to_dict("records") == [{"subject id": "SUB001", "derived_flag": True, "score-value": 3}]


def test_table_transform_dialog_executes_structured_json_without_gui() -> None:
    dialog = TableTransformDialog(None)
    df = pd.DataFrame({"ezqcid": ["SUB001", "SUB002"], "age": [17, 25]})

    result = dialog.execute_query(
        df,
        '{"operations": ['
        '{"operation": "derive_column", "name": "adult", "expression": "age >= 18"},'
        '{"operation": "filter_rows", "conditions": [{"column": "adult", "operator": "==", "value": true}]},'
        '{"operation": "select_columns", "columns": ["ezqcid", "adult"]}'
        ']}',
    )

    assert result.to_dict("records") == [{"ezqcid": "SUB002", "adult": True}]


def test_table_transform_dialog_converts_simple_legacy_select_filter_without_sql_engine() -> None:
    dialog = TableTransformDialog(None)
    df = pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003"],
            "mod": ["anat", "rest", "rest"],
            "SESSION": [1, 1, 2],
        }
    )

    result = dialog.execute_query(df, "SELECT * FROM df WHERE mod = 'rest' and SESSION >= 2")

    assert result["ezqcid"].tolist() == ["SUB003"]


def test_table_transform_dialog_rejects_invalid_structured_operation() -> None:
    dialog = TableTransformDialog(None)

    try:
        dialog.parse_operations('[{"operation": "derive_column", "name": "adult"}]')
    except ValueError as exc:
        assert "无效操作" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_product_table_entrypoints_route_to_unified_workspace_not_transform_dialog() -> None:
    show_source = inspect.getsource(TableDisplay.show_df)
    filter_sorter_source = inspect.getsource(TableDisplay.filter_sorter)
    transform_dialog_source = inspect.getsource(TableTransformDialog.open_filter_dialog)

    assert "open_table_workspace" in show_source
    assert "open_table_workspace" in filter_sorter_source
    assert ".open_filter_dialog(" not in show_source
    assert ".open_filter_dialog(" not in filter_sorter_source
    assert "save_filter_result" not in filter_sorter_source
    assert "tk.Toplevel" not in filter_sorter_source
    assert "ttk.Button" not in filter_sorter_source
    assert "scrolledtext" not in filter_sorter_source
    assert "tk.Toplevel" in transform_dialog_source
    assert "scrolledtext.ScrolledText" in transform_dialog_source


@pytest.fixture
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    for child in root.winfo_children():
        if child.winfo_exists():
            child.destroy()
    root.destroy()


def _workspace_display(tk_root) -> TableDisplay:
    display = TableDisplay.__new__(TableDisplay)
    display.app = SimpleNamespace(root=tk_root)
    return display


def test_show_df_returns_the_unified_workspace_window(monkeypatch, tk_root) -> None:
    source = pd.DataFrame({"ezqcid": ["SUB001"], "score": [1]})
    calls = []

    class FakeWorkspace:
        def __init__(self, parent, frame, **kwargs):
            calls.append((parent, frame.copy(), kwargs))
            self.window = object()
            self.table = SimpleNamespace(main_tree="tree")

    monkeypatch.setattr(gui_table, "TableWorkspace", FakeWorkspace)
    display = _workspace_display(tk_root)

    window = display.show_df(source)

    assert window is display.table_workspace.window
    assert calls[0][0] is tk_root
    pd.testing.assert_frame_equal(calls[0][1], source)
    assert calls[0][2]["on_open_qc"] == display.show_right_menu
    assert display.tree_df == "tree"


def test_filter_sorter_uses_same_workspace_and_never_persists_result(monkeypatch, tk_root) -> None:
    source = pd.DataFrame({"ezqcid": ["SUB001"], "score": [1]})
    display = _workspace_display(tk_root)
    display.resolve_filter_source = lambda result_type, frame: (
        source.copy(),
        "SELECT * FROM df WHERE score >= 1",
    )
    calls = []
    display.open_table_workspace = lambda frame, legacy_filter=None: calls.append(
        (frame.copy(), legacy_filter)
    ) or "workspace-window"
    display.save_filter_result = lambda *args: pytest.fail("read-only workspace must not persist")

    result = display.filter_sorter("qctable")

    assert result == "workspace-window"
    pd.testing.assert_frame_equal(calls[0][0], source)
    assert calls[0][1] == "SELECT * FROM df WHERE score >= 1"


def test_supported_legacy_select_becomes_typed_applied_filters(tk_root) -> None:
    source = pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003"],
            "site": ["A", "B", "B"],
            "score": [1, 2, 3],
        }
    )
    display = _workspace_display(tk_root)

    display.open_table_workspace(
        source,
        "SELECT * FROM df WHERE site = 'B' and score >= 3",
    )
    workspace = display.table_workspace

    assert [(item.column, item.operator, item.value) for item in workspace.applied_state.conditions] == [
        ("site", "==", "B"),
        ("score", ">=", 3),
    ]
    assert workspace.result.dataframe["ezqcid"].tolist() == ["SUB003"]
    assert workspace.result.source_total == 3
    assert workspace.result.matched_total == 1


def test_unsupported_legacy_filter_opens_complete_source_with_specific_safe_warning(tk_root) -> None:
    source = pd.DataFrame({"ezqcid": ["SUB001", "SUB002"], "score": [1, 2]})
    display = _workspace_display(tk_root)
    raw_legacy = '{"operations": [{"operation": "drop_columns"}]}'

    display.open_table_workspace(source, raw_legacy)
    workspace = display.table_workspace

    assert workspace.result.dataframe["ezqcid"].tolist() == ["SUB001", "SUB002"]
    assert workspace.applied_state.conditions == ()
    warning = workspace.action_error_var.get()
    assert "legacy" in warning.lower() or "旧版" in warning
    assert raw_legacy not in workspace.action_error_var.get()


def test_legacy_null_comparisons_map_to_missing_value_controls() -> None:
    display = TableDisplay.__new__(TableDisplay)

    is_missing = display.legacy_filter_conditions(
        "SELECT * FROM df WHERE score = null"
    )
    is_present = display.legacy_filter_conditions(
        "SELECT * FROM df WHERE score != none"
    )

    assert [(item.operator, item.value) for item in is_missing] == [("isna", None)]
    assert [(item.operator, item.value) for item in is_present] == [("notna", None)]


def test_qc_menu_popup_coordinates_accept_a_workspace_button_anchor() -> None:
    display = TableDisplay.__new__(TableDisplay)
    anchor = SimpleNamespace(
        winfo_rootx=lambda: 120,
        winfo_rooty=lambda: 80,
        winfo_height=lambda: 24,
    )

    assert display.popup_coordinates(anchor) == (120, 104)



def test_parse_transform_operations_accepts_list_wrapper_and_single_operation() -> None:
    display = _display()

    assert display.parse_transform_operations('[{"operation": "select_columns", "columns": ["ezqcid"]}]') == [
        {"operation": "select_columns", "columns": ["ezqcid"]}
    ]
    assert display.parse_transform_operations('{"operations": [{"operation": "sort_rows", "sort_keys": []}]}') == [
        {"operation": "sort_rows", "sort_keys": []}
    ]
    assert display.parse_transform_operations('{"operation": "drop_columns", "columns": ["age"]}') == [
        {"operation": "drop_columns", "columns": ["age"]}
    ]
    try:
        display.parse_transform_operations("age >= 18")
    except ValueError as exc:
        assert "JSON 结构化表格转换操作" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_execute_filter_query_routes_json_operations_to_table_transform() -> None:
    display = _display()
    display.DataM = _FakeDataManager()
    df = pd.DataFrame({"ezqcid": ["SUB001"], "age": [29]})

    result = display.execute_filter_query(
        df,
        '[{"operation": "derive_column", "name": "adult", "expression": "age >= 18"}]',
    )

    assert result["transformed"].tolist() == [True]
    assert display.DataM.received_operations == [
        {"operation": "derive_column", "name": "adult", "expression": "age >= 18"}
    ]
    assert display.DataM.received_query is None


def test_table_display_uses_shared_table_transform_from_app_services() -> None:
    engine = TableTransformEngine(max_rows=1)
    data_manager = DataManager()
    app = SimpleNamespace(
        root=None,
        DataM=data_manager,
        ProjM=SimpleNamespace(dt=SimpleNamespace(settings={}, var={}, tab={})),
        services=SimpleNamespace(table_transform=engine),
    )

    display = TableDisplay(app)
    dialog = display.create_table_transform_dialog()

    assert display.table_transform is engine
    assert dialog.table_transform is engine
    assert data_manager.table_transform is engine



def test_execute_filter_query_rejects_invalid_json_transform_shape() -> None:
    display = _display()
    display.DataM = _FakeDataManager()

    try:
        display.execute_filter_query(pd.DataFrame({"ezqcid": ["SUB001"]}), '{"bad": true}')
    except ValueError as exc:
        assert "JSON转换操作" in str(exc)
    else:
        raise AssertionError("Expected ValueError")





def test_restore_filter_source_without_type_returns_copy() -> None:
    display = _display()
    source = pd.DataFrame({"ezqcid": ["SUB010"]})

    result = display.restore_filter_source(None, source)

    assert result.equals(source)
    result.loc[0, "ezqcid"] = "CHANGED"
    assert source.loc[0, "ezqcid"] == "SUB010"



def test_restore_filter_source_for_qctable_preserves_existing_filter_table() -> None:
    display = _display()
    existing = pd.DataFrame({"ezqcid": ["OLD"]})
    display.dt.tab["ezqc_qctable_filter"] = existing.copy()

    result = display.restore_filter_source("qctable", pd.DataFrame({"ezqcid": ["SUB010"]}))

    assert result is None
    assert display.dt.tab["ezqc_qctable_filter"].equals(existing)
