from __future__ import annotations

from io import StringIO

import pandas as pd
import pytest
from PySide6.QtWidgets import QComboBox, QLineEdit

from core.table_view_service import TableViewService
from gui_qt.filter_panel import FilterPanel
from models.table_view_state import FilterCondition


def _profiles():
    source = pd.DataFrame(
        {
            "easyqcid": ["SUB001", "SUB002"],
            "age": [29, 31],
            "passed": [True, False],
            "visit": pd.to_datetime(["2026-01-01", "2026-02-01"]),
        }
    )
    return TableViewService(source).profiles


def test_filter_panel_is_typed_visual_builder_without_json_editor(qtbot):
    panel = FilterPanel(_profiles())
    qtbot.addWidget(panel)
    row = panel.condition_rows[0]

    row.set_column("age")
    assert {">", ">=", "<", "<=", "between"}.issubset(row.operator_codes)
    row.set_operator("between")
    row.set_value(("28", "32"))

    assert row.value_control_kind == "range"
    assert row.condition().value == ("28", "32")
    assert panel.findChildren(QComboBox)
    assert panel.findChildren(QLineEdit)
    assert not hasattr(panel, "json_editor")


def test_filter_panel_round_trips_multiple_draft_conditions(qtbot):
    panel = FilterPanel(_profiles())
    qtbot.addWidget(panel)
    conditions = (
        FilterCondition("passed", "==", True, condition_id="filter-1"),
        FilterCondition("easyqcid", "contains", "002", condition_id="filter-2"),
    )

    panel.set_conditions(conditions)

    assert panel.conditions() == conditions
    assert len(panel.condition_rows) == 2


@pytest.mark.parametrize(
    ("operator", "value", "expected"),
    [("==", True, ["A"]), ("==", False, ["B"]),
     ("in", ("true",), ["A"]), ("not_in", ("false",), ["A"]),
     ("isna", None, ["C"])],
)
def test_filter_panel_applies_nullable_csv_boolean_choices(
    qtbot, operator, value, expected,
):
    source = pd.read_csv(StringIO("easyqcid,flag\nA,True\nB,False\nC,\n"))
    original = source.copy(deep=True)
    service = TableViewService(source)
    panel = FilterPanel(service.profiles)
    qtbot.addWidget(panel)
    row = panel.condition_rows[0]
    row.set_column("flag")
    row.set_operator(operator)
    row.set_value(value)

    state = service.default_state().with_conditions(panel.conditions())
    result = service.apply_state(state)
    actual = service.get_window(result, 0, max(1, result.matched_total)).dataframe

    assert actual["easyqcid"].tolist() == expected
    pd.testing.assert_frame_equal(source, original)
