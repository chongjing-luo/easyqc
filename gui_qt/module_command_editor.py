"""Shared editor for per-group viewer commands with individual shell modes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QFrame,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.code_executor import (
    split_multicmd_segments,
    split_multicmd_template,
)
from gui_qt.i18n import LanguageController
from gui_qt.theme import CONTROL_SPACING, set_button_role
from models.qcmodule import CommandGroup, QCModule, ordered_command_groups


class _CommandGroupCard(QFrame):
    """One command-group card: mode radios + monospace command editor."""

    def __init__(
        self,
        language: LanguageController,
        index: int,
        code: str,
        interper: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.language = language
        self.setObjectName("moduleCommandCard")
        self.setProperty("surface", "true")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(CONTROL_SPACING)
        self.index_label = QLabel(self)
        self.index_label.setProperty("role", "secondary")
        header.addWidget(self.index_label)
        header.addStretch(1)
        self.direct_radio = QRadioButton(self)
        self.shell_radio = QRadioButton(self)
        self.direct_radio.setChecked(interper != "shell")
        self.shell_radio.setChecked(interper == "shell")
        header.addWidget(self.direct_radio)
        header.addWidget(self.shell_radio)
        self.up_button = QToolButton(self)
        self.down_button = QToolButton(self)
        self.remove_button = QToolButton(self)
        for button in (
            self.up_button,
            self.down_button,
            self.remove_button,
        ):
            set_button_role(button, "quiet")
        header.addWidget(self.up_button)
        header.addWidget(self.down_button)
        header.addWidget(self.remove_button)
        layout.addLayout(header)

        self.code_edit = QPlainTextEdit(self)
        self.code_edit.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self.code_edit.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.NoWrap
        )
        self.code_edit.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.code_edit.setMinimumHeight(
            self.code_edit.fontMetrics().lineSpacing() * 3 + 8
        )
        self.code_edit.setPlainText(code)
        layout.addWidget(self.code_edit)
        self.retranslate(index)

    def values(self) -> tuple[str, str]:
        """Return this card's stripped command and chosen mode."""

        return (
            self.code_edit.toPlainText().strip(),
            "shell" if self.shell_radio.isChecked() else "direct",
        )

    def retranslate(self, index: int) -> None:
        tr = self.language.tr
        self.index_label.setText(tr("cross.command_group", index=index))
        self.direct_radio.setText(tr("settings.direct_title"))
        self.shell_radio.setText(tr("settings.shell_title"))
        for button, key, glyph in (
            (self.up_button, "cross.move_command_group_up", "↑"),
            (self.down_button, "cross.move_command_group_down", "↓"),
            (self.remove_button, "cross.remove_command_group", "×"),
        ):
            text = tr(key)
            button.setText(glyph)
            button.setAccessibleName(f"{text} {index}")
            button.setToolTip(text)
        self.code_edit.setPlaceholderText(tr("cross.viewer_placeholder"))


class ModuleCommandGroupsEditor(QWidget):
    """Edit an ordered list of (command, mode) groups for one module.

    界面形态与评分项/标签一致：每命令组一张卡片（编号 + 直接/Shell 单选
    对 + 上移/下移/删除），底部整宽「添加命令组」按钮。旧 MULTICMD 单文本
    在 ``set_module`` 时自动按 ``;|`` 拆组（前缀写法保持一组）；保存端
    的 0/1/N 归一由 core ``_normalized_module_payload`` 完成。
    """

    def __init__(
        self,
        language: LanguageController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(language, LanguageController):
            raise TypeError(
                "ModuleCommandGroupsEditor requires LanguageController"
            )
        self.language = language
        self.setObjectName("moduleCommandGroupsEditor")
        self._drafts: list[list] = []
        self.cards: list[_CommandGroupCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CONTROL_SPACING)
        self.cards_layout = QVBoxLayout()
        self.cards_layout.setSpacing(CONTROL_SPACING)
        layout.addLayout(self.cards_layout)
        self.empty_label = QLabel(self)
        self.empty_label.setProperty("role", "secondary")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        self.add_button = QPushButton(self)
        set_button_role(self.add_button, "secondary")
        self.add_button.clicked.connect(self.add_group)
        layout.addWidget(self.add_button)

        self.language.languageChanged.connect(self.retranslate_ui)
        self.set_groups([("", "direct")])
        self.retranslate_ui()

    # ---- public API -------------------------------------------------

    def set_module(self, module: QCModule) -> None:
        """Present one module's commands as editable groups."""

        if not isinstance(module, QCModule):
            raise TypeError("command editor requires QCModule")
        self.set_groups(_legacy_module_groups(module))

    def set_groups(self, groups) -> None:
        """Install ``[(code, interper), …]`` drafts and rebuild cards."""

        drafts: list[list] = []
        for code, interper in groups:
            if not isinstance(code, str):
                raise TypeError("command group code must be str")
            if interper not in ("direct", "shell"):
                raise ValueError(
                    f"invalid command group interper: {interper!r}"
                )
            drafts.append([code, interper])
        self._drafts = drafts
        self._rebuild()

    def groups(self) -> tuple[tuple[str, str], ...]:
        """Return the current groups in order, blank commands included."""

        self._sync_drafts()
        return tuple((code, mode) for code, mode in self._drafts)

    def command_groups_payload(self) -> dict[str, CommandGroup]:
        """Return sequentially keyed groups with blank commands dropped."""

        kept = [group for group in self.groups() if group[0].strip()]
        return {
            str(index): CommandGroup(str(index), code, mode)
            for index, (code, mode) in enumerate(kept, start=1)
        }

    @Slot()
    def add_group(self) -> None:
        self._sync_drafts()
        self._drafts.append(["", "direct"])
        self._rebuild()
        if self.cards:
            self.cards[-1].code_edit.setFocus()

    # ---- internals --------------------------------------------------

    def _sync_drafts(self) -> None:
        for draft, card in zip(self._drafts, self.cards):
            draft[0], draft[1] = card.values()

    def _rebuild(self) -> None:
        for card in self.cards:
            card.setParent(None)
            card.deleteLater()
        self.cards = []
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for index, (code, interper) in enumerate(self._drafts, start=1):
            card = _CommandGroupCard(
                self.language, index, code, interper, self
            )
            card.up_button.clicked.connect(
                lambda _checked=False, card_ref=card: self._move_card(
                    card_ref, -1
                )
            )
            card.down_button.clicked.connect(
                lambda _checked=False, card_ref=card: self._move_card(
                    card_ref, 1
                )
            )
            card.remove_button.clicked.connect(
                lambda _checked=False, card_ref=card: self._remove_card(
                    card_ref
                )
            )
            self.cards_layout.addWidget(card)
            self.cards.append(card)
        self.empty_label.setVisible(not self.cards)
        self._update_card_actions()

    def _card_index(self, card: _CommandGroupCard) -> int | None:
        for position, item in enumerate(self.cards):
            if item is card:
                return position
        return None

    def _move_card(self, card: _CommandGroupCard, delta: int) -> None:
        self._sync_drafts()
        index = self._card_index(card)
        if index is None:
            return
        target = index + delta
        if target < 0 or target >= len(self._drafts):
            return
        self._drafts.insert(target, self._drafts.pop(index))
        self._rebuild()
        if self.cards:
            self.cards[target].code_edit.setFocus()

    def _remove_card(self, card: _CommandGroupCard) -> None:
        self._sync_drafts()
        index = self._card_index(card)
        if index is None:
            return
        del self._drafts[index]
        self._rebuild()

    def _update_card_actions(self) -> None:
        for position, card in enumerate(self.cards):
            card.up_button.setEnabled(position > 0)
            card.down_button.setEnabled(position < len(self.cards) - 1)

    @Slot()
    def retranslate_ui(self) -> None:
        tr = self.language.tr
        self.add_button.setText(tr("cross.add_command_group"))
        self.empty_label.setText(tr("cross.no_command_groups"))
        for index, card in enumerate(self.cards, start=1):
            card.retranslate(index)


def _legacy_module_groups(module: QCModule) -> list[tuple[str, str]]:
    """Split one module into editor groups (new format wins, else MULTICMD)."""

    if module.commands:
        return [
            (group.code, group.interper)
            for group in ordered_command_groups(module.commands)
        ]
    code = module.code or ""
    if not code.strip():
        return [("", "direct")]
    parts = split_multicmd_template(code)
    if parts is None or parts[0].strip():
        # 普通文本、SCRIPT 或带前缀的 MULTICMD（前缀必须 Shell，不拆）
        return [(code, module.interper)]
    segments = split_multicmd_segments(parts[1])
    if not segments:
        return [(code, module.interper)]
    return [(segment, module.interper) for segment in segments]


__all__ = ["ModuleCommandGroupsEditor"]
