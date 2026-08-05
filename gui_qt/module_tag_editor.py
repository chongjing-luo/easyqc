"""Shared draft-only Qt editor for ordered QC-module tag labels."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from typing import Iterable

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui_qt.i18n import LanguageController, protect_user_text
from gui_qt.theme import CONTROL_SPACING, set_button_role


@dataclass(frozen=True)
class _TagDraft:
    token: int
    label: str


class ModuleTagEditor(QWidget):
    """Own one ordered, non-persistent draft of editable tag labels."""

    def __init__(
        self,
        language: LanguageController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(language, LanguageController):
            raise TypeError("ModuleTagEditor requires LanguageController")
        self.language = language
        self._token_source = count(1)
        self._drafts: list[_TagDraft] = []
        self._error_key: str | None = None
        self.setObjectName("moduleTagEditor")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CONTROL_SPACING)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("moduleTagScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._chip_container = QWidget(self.scroll_area)
        self._chip_layout = QHBoxLayout(self._chip_container)
        self._chip_layout.setContentsMargins(0, 0, 0, 0)
        self._chip_layout.setSpacing(CONTROL_SPACING)
        self.scroll_area.setWidget(self._chip_container)
        layout.addWidget(self.scroll_area)

        self.add_button = QPushButton(self._chip_container)
        self.add_button.setObjectName("moduleTagAdd")
        set_button_role(self.add_button)
        self.add_button.clicked.connect(self._prompt_add)

        self.error_label = QLabel(self)
        self.error_label.setObjectName("moduleTagError")
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.language.languageChanged.connect(self.retranslate_ui)
        self._rebuild_chips()
        self.retranslate_ui()

    def set_tags(self, labels: Iterable[str]) -> None:
        """Replace the ordered draft after validating the complete input."""

        if isinstance(labels, (str, bytes)):
            raise TypeError("ModuleTagEditor labels must be an iterable of strings")
        try:
            normalized = tuple(labels)
        except TypeError as exc:
            raise TypeError(
                "ModuleTagEditor labels must be an iterable of strings"
            ) from exc
        if any(not isinstance(label, str) for label in normalized):
            raise TypeError("ModuleTagEditor labels must contain only strings")
        if any(not label.strip() for label in normalized):
            self._set_error("module_tags.blank_error")
            raise ValueError(self.language.tr("module_tags.blank_error"))

        self._drafts = [
            _TagDraft(next(self._token_source), label)
            for label in normalized
        ]
        self._set_error(None)
        self._rebuild_chips()

    def tags(self) -> tuple[str, ...]:
        """Return the exact ordered draft, including duplicate labels."""

        return tuple(draft.label for draft in self._drafts)

    @Slot()
    def _prompt_add(self) -> None:
        label, accepted = QInputDialog.getText(
            self,
            self.language.tr("module_tags.add_title"),
            self.language.tr("module_tags.label_prompt"),
        )
        if accepted:
            self._append_entered_label(label)

    def _append_entered_label(self, label: str) -> None:
        normalized = label.strip()
        if not normalized:
            self._set_error("module_tags.blank_error")
            return
        self._drafts.append(_TagDraft(next(self._token_source), normalized))
        self._set_error(None)
        self._rebuild_chips()

    def _prompt_edit(self, token: int) -> None:
        index = self._index_for_token(token)
        current = self._drafts[index].label
        label, accepted = QInputDialog.getText(
            self,
            self.language.tr("module_tags.edit_title"),
            self.language.tr("module_tags.label_prompt"),
            QLineEdit.Normal,
            current,
        )
        if not accepted:
            return
        normalized = label.strip()
        if not normalized:
            self._set_error("module_tags.blank_error")
            return
        self._drafts[index] = _TagDraft(token, normalized)
        self._set_error(None)
        self._rebuild_chips()

    def _remove_tag(self, token: int) -> None:
        self._drafts.pop(self._index_for_token(token))
        self._set_error(None)
        self._rebuild_chips()

    def _index_for_token(self, token: int) -> int:
        for index, draft in enumerate(self._drafts):
            if draft.token == token:
                return index
        raise RuntimeError(f"Module tag draft token is stale: {token}")

    def _rebuild_chips(self) -> None:
        while self._chip_layout.count():
            item = self._chip_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self.add_button:
                widget.setParent(None)
                widget.deleteLater()

        for draft in self._drafts:
            self._chip_layout.addWidget(self._make_chip(draft))
        self._chip_layout.addWidget(self.add_button)
        self._chip_layout.addStretch(1)
        self._sync_scroll_height()

    def _make_chip(self, draft: _TagDraft) -> QFrame:
        chip = QFrame(self._chip_container)
        chip.setObjectName("moduleTagChip")
        chip.setProperty("surface", "subtle")
        chip.setProperty("tagToken", draft.token)
        layout = QHBoxLayout(chip)
        layout.setContentsMargins(CONTROL_SPACING // 2, 0, 0, 0)
        layout.setSpacing(0)

        edit_button = QPushButton(draft.label, chip)
        edit_button.setObjectName("moduleTagText")
        edit_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        set_button_role(edit_button, "quiet")
        protect_user_text(edit_button, "text", "accessibleName")
        edit_button.clicked.connect(
            lambda _checked=False, token=draft.token: self._prompt_edit(token)
        )
        layout.addWidget(edit_button)

        remove_button = QToolButton(chip)
        remove_button.setObjectName("moduleTagRemove")
        remove_button.setText("×")
        remove_button.setAutoRaise(False)
        remove_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        set_button_role(remove_button, "quiet")
        protect_user_text(remove_button, "accessibleName", "toolTip")
        remove_button.clicked.connect(
            lambda _checked=False, token=draft.token: self._remove_tag(token)
        )
        layout.addWidget(remove_button)
        self._translate_chip(draft, edit_button, remove_button)
        return chip

    def _sync_scroll_height(self) -> None:
        content_height = max(
            self._chip_container.sizeHint().height(),
            self.add_button.sizeHint().height(),
        )
        scrollbar_height = self.scroll_area.horizontalScrollBar().sizeHint().height()
        self.scroll_area.setFixedHeight(
            content_height + scrollbar_height + self.scroll_area.frameWidth() * 2
        )

    def _set_error(self, key: str | None) -> None:
        self._error_key = key
        text = self.language.tr(key) if key is not None else ""
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))

    def _translate_chip(
        self,
        draft: _TagDraft,
        edit_button: QPushButton,
        remove_button: QToolButton,
    ) -> None:
        edit_button.setAccessibleName(
            self.language.tr("module_tags.edit_accessible", label=draft.label)
        )
        remove_text = self.language.tr(
            "module_tags.remove_accessible",
            label=draft.label,
        )
        remove_button.setAccessibleName(remove_text)
        remove_button.setToolTip(remove_text)

    @Slot()
    def retranslate_ui(self) -> None:
        """Translate component chrome while retaining every user label."""

        self.add_button.setText(self.language.tr("cross.add_tag"))
        self.add_button.setAccessibleName(self.language.tr("cross.add_tag"))
        if self._error_key is not None:
            self._set_error(self._error_key)
        for chip in self.findChildren(QFrame, "moduleTagChip"):
            token = int(chip.property("tagToken"))
            draft = self._drafts[self._index_for_token(token)]
            edit_button = chip.findChild(QPushButton, "moduleTagText")
            remove_button = chip.findChild(QToolButton, "moduleTagRemove")
            if edit_button is not None and remove_button is not None:
                self._translate_chip(draft, edit_button, remove_button)
        self._sync_scroll_height()


def normalize_stored_tag_labels(
    labels: Iterable[str | None],
) -> tuple[str, ...]:
    """Map only the schema-v3 default one-empty-row sentinel to no tag chips."""

    stored = tuple(labels)
    if len(stored) == 1 and (stored[0] is None or stored[0] == ""):
        # ProjectService.default_module() persists exactly one empty tag row as
        # the legacy editor sentinel. It is not a user tag and has no chip.
        return ()
    if any(label is not None and not isinstance(label, str) for label in stored):
        raise TypeError("Stored module tag labels must be strings or null")
    # Nulls outside the exact sentinel remain blank inputs so set_tags() fails
    # visibly; mixed/multiple malformed rows are never silently discarded.
    return tuple("" if label is None else label for label in stored)


def sync_score_table_height(table: QTableWidget) -> int:
    """Apply native header/row/frame height to one score table and return it."""

    if not isinstance(table, QTableWidget):
        raise TypeError("score table height requires QTableWidget")
    table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.resizeRowsToContents()
    header_height = table.horizontalHeader().height()
    if header_height <= 0:
        header_height = table.horizontalHeader().sizeHint().height()
    height = (
        header_height
        + sum(table.rowHeight(row) for row in range(table.rowCount()))
        + table.frameWidth() * 2
    )
    table.setFixedHeight(height)
    policy = table.sizePolicy()
    policy.setVerticalPolicy(QSizePolicy.Fixed)
    table.setSizePolicy(policy)
    return height


__all__ = [
    "ModuleTagEditor",
    "normalize_stored_tag_labels",
    "sync_score_table_height",
]
