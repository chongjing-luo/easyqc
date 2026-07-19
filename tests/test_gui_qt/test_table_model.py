from __future__ import annotations

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal
from PySide6.QtCore import Qt

from core.table_view_service import TableViewService
from gui_qt.table_model import QtTableModel


def _window():
    frame = pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003"],
            "score": [1.5, None, 3.0],
            "status": ["pending", "rated", "pending"],
        }
    )
    service = TableViewService(frame)
    result = service.apply_state(service.default_state(page_size=2))
    return frame, service.get_window(result, offset=1)


def test_qt_table_model_exposes_window_roles_and_stable_row_reference(qtbot):
    source, window = _window()
    original = source.copy(deep=True)

    model = QtTableModel(window)

    assert model.rowCount() == 2
    assert model.columnCount() == 3
    assert model.headerData(0, Qt.Horizontal, Qt.DisplayRole) == "ezqcid"
    assert model.headerData(0, Qt.Vertical, Qt.DisplayRole) == "2"
    assert model.data(model.index(0, 0), Qt.DisplayRole) == "SUB002"
    assert model.data(model.index(0, 1), Qt.DisplayRole) == ""
    assert not (model.flags(model.index(0, 0)) & Qt.ItemIsEditable)

    reference = model.row_reference(0)
    assert reference.result_position == 1
    assert reference.source_position == 1
    assert reference.ezqcid == "SUB002"
    assert_frame_equal(source, original)


def test_qt_table_model_rejects_inconsistent_window_metadata():
    _, window = _window()
    window.source_positions = (1,)

    with pytest.raises(ValueError, match="source_positions"):
        QtTableModel(window)


def test_qt_table_model_returns_an_isolated_snapshot():
    source, window = _window()
    model = QtTableModel(window)

    snapshot = model.snapshot()
    snapshot.iloc[0, 0] = "CHANGED"

    assert model.data(model.index(0, 0), Qt.DisplayRole) == "SUB002"
    assert source.iloc[1, 0] == "SUB002"
