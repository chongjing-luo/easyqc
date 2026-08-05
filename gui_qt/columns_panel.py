"""Draft-only visible-column, order and pin editor for the Qt Table."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui_qt.i18n import LanguageController, translate_ui_text
from models.table_view_state import ColumnViewState


COLUMN_ROLE = int(Qt.ItemDataRole.UserRole)
PINNED_ROLE = COLUMN_ROLE + 1


class ColumnsPanel(QWidget):
    """Own one complete column-layout draft without applying it."""

    def __init__(
        self,
        initial: ColumnViewState,
        parent: QWidget | None = None,
        *,
        language: LanguageController | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("columnsPanel")
        self.language = language
        self._widths = initial.widths
        self._build_ui()
        self.set_state(initial)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        heading = QLabel("列设置", self)
        layout.addWidget(heading)
        hint = QLabel(
            "勾选要显示的列；固定列始终位于表格最前面。", self
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("搜索列")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("搜索表格列")
        self.search_label = QLabel("搜索列", self)
        self.search_label.setBuddy(self.search_edit)
        layout.addWidget(self.search_label)
        layout.addWidget(self.search_edit)

        visibility_actions = QHBoxLayout()
        self.select_all_button = QPushButton("全选", self)
        self.select_all_button.setAccessibleName("显示全部非固定列")
        self.deselect_all_button = QPushButton("取消全选", self)
        self.deselect_all_button.setAccessibleName("隐藏全部非固定列")
        visibility_actions.addWidget(self.select_all_button)
        visibility_actions.addWidget(self.deselect_all_button)
        visibility_actions.addStretch(1)
        layout.addLayout(visibility_actions)

        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("columnList")
        self.list_widget.setAccessibleName("表格列顺序与可见性")
        self.list_widget.setAlternatingRowColors(True)
        layout.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        self.move_up_button = QPushButton("上移", self)
        self.move_down_button = QPushButton("下移", self)
        self.pin_button = QPushButton("固定", self)
        self.unpin_button = QPushButton("取消固定", self)
        for button, name in (
            (self.move_up_button, "上移所选列"),
            (self.move_down_button, "下移所选列"),
            (self.pin_button, "固定所选列"),
            (self.unpin_button, "取消固定所选列"),
        ):
            button.setAccessibleName(name)
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("columnsError")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.setAccessibleName("列设置草稿错误")
        layout.addWidget(self.error_label)

        self.search_edit.textChanged.connect(self._refresh_search)
        self.list_widget.itemSelectionChanged.connect(self._refresh_controls)
        self.select_all_button.clicked.connect(
            lambda _checked=False: self.set_all_visible(True)
        )
        self.deselect_all_button.clicked.connect(
            lambda _checked=False: self.set_all_visible(False)
        )
        self.move_up_button.clicked.connect(
            lambda _checked=False: self.move_selected(-1)
        )
        self.move_down_button.clicked.connect(
            lambda _checked=False: self.move_selected(1)
        )
        self.pin_button.clicked.connect(
            lambda _checked=False: self.pin_selected()
        )
        self.unpin_button.clicked.connect(
            lambda _checked=False: self.unpin_selected()
        )

    @property
    def error_text(self) -> str:
        return self.error_label.text()

    def set_error(self, message: str) -> None:
        self.error_label.setText(str(message))
        self.error_label.setVisible(bool(message))

    def set_state(self, state: ColumnViewState) -> None:
        order = tuple(state.order)
        hidden = tuple(state.hidden)
        pinned = tuple(state.pinned)
        order_set = set(order)
        hidden_set = set(hidden)
        pinned_set = set(pinned)
        if len(order_set) != len(order):
            raise ValueError("列设置中包含重复列")
        if len(hidden_set) != len(hidden) or len(pinned_set) != len(pinned):
            raise ValueError("隐藏列或固定列列表中包含重复项")
        unknown = (hidden_set | pinned_set) - order_set
        if unknown:
            raise ValueError(f"列设置中包含未知列：{sorted(unknown)}")
        hidden_pinned = hidden_set & pinned_set
        if hidden_pinned:
            raise ValueError(
                "列不能同时隐藏和固定："
                + ", ".join(sorted(hidden_pinned))
            )
        if "easyqcid" in order and (
            order[0] != "easyqcid"
            or "easyqcid" not in pinned_set
            or "easyqcid" in hidden_set
        ):
            raise ValueError("easyqcid 必须保持在第一列、可见且固定")
        if set(order[: len(pinned_set)]) != pinned_set:
            raise ValueError("固定列必须组成连续的前置列区")
        self._widths = state.widths
        self.list_widget.clear()
        for column in order:
            item = QListWidgetItem(self.list_widget)
            self._configure_item(item, column, pinned=column in pinned_set)
            item.setCheckState(
                Qt.CheckState.Unchecked if column in hidden_set else Qt.CheckState.Checked
            )
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self.set_error("")
        self._refresh_search(self.search_edit.text())
        self._refresh_controls()

    def state(self) -> ColumnViewState:
        order: list[str] = []
        hidden: list[str] = []
        pinned: list[str] = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            column = str(item.data(COLUMN_ROLE))
            order.append(column)
            if bool(item.data(PINNED_ROLE)):
                pinned.append(column)
            elif item.checkState() == Qt.CheckState.Unchecked:
                hidden.append(column)
        return ColumnViewState(
            order=tuple(order),
            hidden=tuple(hidden),
            widths=self._widths,
            pinned=tuple(pinned),
        )

    def set_all_visible(self, visible: bool) -> None:
        """Update every non-pinned column in the current visibility draft."""

        if type(visible) is not bool:
            raise TypeError("visible must be bool")
        check_state = (
            Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked
        )
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if not bool(item.data(PINNED_ROLE)):
                item.setCheckState(check_state)

    def _configure_item(
        self,
        item: QListWidgetItem,
        column: str,
        *,
        pinned: bool,
    ) -> None:
        item.setData(COLUMN_ROLE, column)
        item.setData(PINNED_ROLE, pinned)
        item.setText(
            self._ui_text(f"{column}   · 已固定")
            if pinned
            else column
        )
        item.setData(Qt.ItemDataRole.AccessibleTextRole, item.text())
        flags = Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled
        if not pinned:
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        item.setFlags(flags)
        if pinned:
            item.setCheckState(Qt.CheckState.Checked)

    def _ui_text(self, source: str) -> str:
        if self.language is not None:
            return self.language.translate_source(source)
        return translate_ui_text(source)

    def _pinned_count(self) -> int:
        return sum(
            bool(self.list_widget.item(row).data(PINNED_ROLE))
            for row in range(self.list_widget.count())
        )

    def _target_row(self, row: int, delta: int) -> int | None:
        item = self.list_widget.item(row)
        pinned = bool(item.data(PINNED_ROLE))
        minimum = (1 if self._has_identity() else 0) if pinned else self._pinned_count()
        maximum = self._pinned_count() - 1 if pinned else self.list_widget.count() - 1
        target = row + delta
        while minimum <= target <= maximum:
            if not self.list_widget.item(target).isHidden():
                return target
            target += delta
        return None

    def move_selected(self, delta: int) -> bool:
        row = self.list_widget.currentRow()
        if row < 0 or delta not in {-1, 1}:
            return False
        column = str(self.list_widget.item(row).data(COLUMN_ROLE))
        if column == "easyqcid":
            self.set_error("easyqcid 必须保持为第一列")
            return False
        target = self._target_row(row, delta)
        if target is None:
            return False
        item = self.list_widget.takeItem(row)
        self.list_widget.insertItem(target, item)
        self.list_widget.setCurrentRow(target)
        self.set_error("")
        self._refresh_controls()
        return True

    def pin_selected(self) -> bool:
        row = self.list_widget.currentRow()
        if row < 0 or bool(self.list_widget.item(row).data(PINNED_ROLE)):
            return False
        item = self.list_widget.takeItem(row)
        column = str(item.data(COLUMN_ROLE))
        item.setCheckState(Qt.CheckState.Checked)
        self._configure_item(item, column, pinned=True)
        target = self._pinned_count()
        self.list_widget.insertItem(target, item)
        self.list_widget.setCurrentRow(target)
        self.set_error("")
        self._refresh_search(self.search_edit.text())
        self._refresh_controls()
        return True

    def unpin_selected(self) -> bool:
        row = self.list_widget.currentRow()
        if row < 0 or not bool(self.list_widget.item(row).data(PINNED_ROLE)):
            return False
        item = self.list_widget.item(row)
        column = str(item.data(COLUMN_ROLE))
        if column == "easyqcid":
            self.set_error("easyqcid must remain pinned")
            return False
        item = self.list_widget.takeItem(row)
        self._configure_item(item, column, pinned=False)
        item.setCheckState(Qt.CheckState.Checked)
        target = self._pinned_count()
        self.list_widget.insertItem(target, item)
        self.list_widget.setCurrentRow(target)
        self.set_error("")
        self._refresh_search(self.search_edit.text())
        self._refresh_controls()
        return True

    def _has_identity(self) -> bool:
        return bool(
            self.list_widget.count()
            and self.list_widget.item(0).data(COLUMN_ROLE) == "easyqcid"
        )

    def _refresh_search(self, query: str) -> None:
        needle = str(query).strip().casefold()
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            item.setHidden(needle not in str(item.data(COLUMN_ROLE)).casefold())
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        row = self.list_widget.currentRow()
        if row < 0:
            for button in (
                self.move_up_button,
                self.move_down_button,
                self.pin_button,
                self.unpin_button,
            ):
                button.setEnabled(False)
            return
        item = self.list_widget.item(row)
        column = str(item.data(COLUMN_ROLE))
        pinned = bool(item.data(PINNED_ROLE))
        self.move_up_button.setEnabled(
            column != "easyqcid" and self._target_row(row, -1) is not None
        )
        self.move_down_button.setEnabled(
            column != "easyqcid" and self._target_row(row, 1) is not None
        )
        self.pin_button.setEnabled(not pinned)
        self.unpin_button.setEnabled(pinned and column != "easyqcid")


__all__ = ["ColumnsPanel"]
