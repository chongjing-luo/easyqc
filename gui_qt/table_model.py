"""Read-only Qt model over a bounded Core ``RowWindow``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from pandas.api import types as ptypes
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from gui_qt.i18n import LanguageController
from models.table_view_state import RowWindow, SortRule


@dataclass(frozen=True)
class QtTableRowReference:
    """Stable bridge from a visual row to Core result/source identity."""

    result_position: int
    source_position: int
    ezqcid: str


class QtTableModel(QAbstractTableModel):
    """Expose one immutable Core row window through Qt's model/view roles."""

    def __init__(
        self,
        window: RowWindow,
        parent=None,
        *,
        language: LanguageController | None = None,
    ) -> None:
        super().__init__(parent)
        self._language = language
        self._frame = pd.DataFrame()
        self._source_positions: tuple[int, ...] = ()
        self._offset = 0
        self._matched_total = 0
        self._sort_rules: tuple[SortRule, ...] = ()
        self.set_window(window)

    def set_window(self, window: RowWindow) -> None:
        if not isinstance(window, RowWindow):
            raise TypeError("QtTableModel requires a RowWindow")
        if len(window.dataframe) != len(window.source_positions):
            raise ValueError("RowWindow dataframe and source_positions must have equal length")
        self.beginResetModel()
        self._frame = window.dataframe.copy(deep=True).reset_index(drop=True)
        self._source_positions = tuple(int(value) for value in window.source_positions)
        self._offset = int(window.offset)
        self._matched_total = int(window.matched_total)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._frame.index)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._frame.columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        value = self._frame.iat[index.row(), index.column()]
        if role == Qt.DisplayRole:
            return self._display_value(value)
        if role == Qt.TextAlignmentRole:
            series = self._frame.iloc[:, index.column()]
            horizontal = Qt.AlignRight if ptypes.is_numeric_dtype(series.dtype) else Qt.AlignLeft
            return horizontal | Qt.AlignVCenter
        if role == Qt.UserRole:
            return value
        return None

    def headerData(self, section: int, orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if orientation == Qt.Horizontal and 0 <= section < self.columnCount():
            column = str(self._frame.columns[section])
            if role == Qt.DisplayRole:
                return column
            if role == Qt.ToolTipRole:
                for priority, rule in enumerate(self._sort_rules, start=1):
                    if rule.column == column:
                        if self._language is None:
                            direction = "升序" if rule.ascending else "降序"
                            return f"排序优先级 {priority} · {direction}"
                        direction = self._language.tr(
                            "table.sort.ascending"
                            if rule.ascending
                            else "table.sort.descending"
                        )
                        return self._language.tr(
                            "table.sort.tooltip",
                            priority=priority,
                            direction=direction,
                        )
        if orientation == Qt.Vertical and 0 <= section < self.rowCount() and role == Qt.DisplayRole:
            return str(self._offset + section + 1)
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def row_reference(self, row: int) -> QtTableRowReference:
        if row < 0 or row >= self.rowCount():
            raise IndexError("table row is outside the current window")
        ezqcid = ""
        if "ezqcid" in self._frame.columns:
            value = self._frame.iloc[row]["ezqcid"]
            ezqcid = "" if pd.isna(value) else str(value).strip()
        return QtTableRowReference(
            result_position=self._offset + row,
            source_position=self._source_positions[row],
            ezqcid=ezqcid,
        )

    def snapshot(self) -> pd.DataFrame:
        return self._frame.copy(deep=True)

    def set_sort_rules(self, rules: tuple[SortRule, ...]) -> None:
        self._sort_rules = tuple(rules)
        if self.columnCount():
            self.headerDataChanged.emit(Qt.Horizontal, 0, self.columnCount() - 1)

    def retranslate_ui(self) -> None:
        if self.columnCount():
            self.headerDataChanged.emit(Qt.Horizontal, 0, self.columnCount() - 1)

    @staticmethod
    def _display_value(value: Any) -> str:
        if pd.isna(value):
            return ""
        if isinstance(value, pd.Timestamp):
            return value.isoformat(sep=" ")
        return str(value)


__all__ = ["QtTableModel", "QtTableRowReference"]
