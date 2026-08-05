"""Shared draft-only Qt editor for ordered QC-module tag labels."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from typing import Iterable

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, Slot
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QPushButton,
    QSizePolicy,
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


class _FlowLayout(QLayout):
    """Small native height-for-width flow layout for complete tag chips."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        spacing: int = CONTROL_SPACING,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, max(0, width), 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        left, top, right, bottom = self.getContentsMargins()
        return size + QSize(left + right, top + bottom)

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective = rect.adjusted(left, top, -right, -bottom)
        available_width = max(0, effective.width())
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = max(0, self.spacing())

        for item in self._items:
            hint = item.sizeHint()
            item_width = min(max(0, hint.width()), available_width)
            item_height = max(0, hint.height())
            if (
                line_height > 0
                and x + item_width > effective.right() + 1
            ):
                x = effective.x()
                y += line_height + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(
                    QRect(QPoint(x, y), QSize(item_width, item_height))
                )
            x += item_width + spacing
            line_height = max(line_height, item_height)

        content_height = (
            0
            if not self._items
            else y + line_height - effective.y()
        )
        return top + content_height + bottom


class ModuleTagEditor(QWidget):
    """Own one ordered, non-persistent draft of editable tag labels."""

    _MAX_TAG_TEXT_WIDTH = 240

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
        self._last_flow_width = -1
        self.setObjectName("moduleTagEditor")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CONTROL_SPACING)

        self._chip_container = QWidget(self)
        self._chip_container.setObjectName("moduleTagFlowContainer")
        self._chip_layout = _FlowLayout(self._chip_container)
        container_policy = QSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Preferred,
        )
        container_policy.setHeightForWidth(True)
        self._chip_container.setSizePolicy(container_policy)
        layout.addWidget(self._chip_container)

        editor_policy = self.sizePolicy()
        editor_policy.setHorizontalPolicy(QSizePolicy.Expanding)
        editor_policy.setVerticalPolicy(QSizePolicy.Preferred)
        editor_policy.setHeightForWidth(True)
        self.setSizePolicy(editor_policy)

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
        self._sync_flow_geometry()

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
        edit_button.setMinimumWidth(0)
        edit_button.setMaximumWidth(self._MAX_TAG_TEXT_WIDTH)
        edit_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        set_button_role(edit_button, "quiet")
        protect_user_text(
            edit_button,
            "text",
            "toolTip",
            "accessibleName",
        )
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
        self._set_visible_tag_text(edit_button, draft.label)
        self._translate_chip(draft, edit_button, remove_button)
        return chip

    def _set_visible_tag_text(
        self,
        edit_button: QPushButton,
        full_label: str,
    ) -> None:
        visible = edit_button.fontMetrics().elidedText(
            full_label,
            Qt.ElideRight,
            self._MAX_TAG_TEXT_WIDTH,
        )
        edit_button.setText(visible)
        edit_button.setToolTip(full_label)

    def _refresh_visible_tag_texts(self) -> None:
        for chip in self.findChildren(QFrame, "moduleTagChip"):
            token = int(chip.property("tagToken"))
            draft = self._drafts[self._index_for_token(token)]
            edit_button = chip.findChild(QPushButton, "moduleTagText")
            if edit_button is not None:
                self._set_visible_tag_text(edit_button, draft.label)

    def _sync_flow_geometry(self) -> None:
        self._chip_layout.invalidate()
        self._chip_container.updateGeometry()
        self.updateGeometry()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self.layout().heightForWidth(max(0, width))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = event.size().width()
        if width != self._last_flow_width:
            self._last_flow_width = width
            self._sync_flow_geometry()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() in {
            QEvent.FontChange,
            QEvent.ApplicationFontChange,
            QEvent.StyleChange,
        } and hasattr(self, "_chip_layout"):
            self._refresh_visible_tag_texts()
            self._sync_flow_geometry()

    def _set_error(self, key: str | None) -> None:
        self._error_key = key
        text = self.language.tr(key) if key is not None else ""
        self.error_label.setText(text)
        self.error_label.setVisible(bool(text))
        self._sync_flow_geometry()

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
        self._refresh_visible_tag_texts()
        self._sync_flow_geometry()


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


__all__ = [
    "ModuleTagEditor",
    "normalize_stored_tag_labels",
]
