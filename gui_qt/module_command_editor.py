"""Shared editor for per-group viewer commands with individual shell modes."""

from __future__ import annotations

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QHBoxLayout,
    QBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QFrame,
    QMessageBox,
    QSizePolicy,
    QLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.code_executor import (
    direct_mode_shell_syntax_warning,
    split_multicmd_segments,
    split_multicmd_template,
)
from gui_qt.i18n import LanguageController
from gui_qt.theme import CONTROL_SPACING, CONTROL_HEIGHT, set_button_role
from models.qcmodule import CommandGroup, QCModule, ordered_command_groups


def confirm_command_group_syntax(
    parent: QWidget,
    language: LanguageController,
    groups: tuple[tuple[str, str], ...],
) -> bool | None:
    """Confirm all direct-mode warnings once; None cancels, False needs no consent.

    Input includes blank visible cards so reported numbers match the editor.
    This function only shows a dialog; it never changes or saves the draft.
    """
    warnings = []
    for index, (code, mode) in enumerate(groups, start=1):
        warning = direct_mode_shell_syntax_warning(code, mode)
        if warning:
            label = language.tr("cross.command_group", index=index)
            warnings.append(f"{label}: {language.translate_source(warning)}")
    if not warnings:
        return False
    answer = QMessageBox.question(
        parent,
        language.translate_source("Shell 语法警告"),
        "\n\n".join(warnings) + "\n\n"
        + language.translate_source("仍要保存该模块吗？"),
    )
    return True if answer == QMessageBox.Yes else None


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
        self.setProperty("surface", "subtle")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(CONTROL_SPACING)

        self.header = QGridLayout()
        self.header.setSpacing(CONTROL_SPACING)
        self._header_shape = None
        self.index_label = QLabel(self)
        self.index_label.setProperty("role", "sectionTitle")
        self.modes = QWidget(self)
        self.modes.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.mode_layout = QBoxLayout(QBoxLayout.LeftToRight, self.modes)
        self.mode_layout.setContentsMargins(0, 0, 0, 0)
        self.mode_layout.setSpacing(CONTROL_SPACING)
        self.mode_layout.setSizeConstraint(QLayout.SetNoConstraint)
        self.direct_radio = QRadioButton(self.modes)
        self.shell_radio = QRadioButton(self.modes)
        self.direct_radio.setChecked(interper != "shell")
        self.shell_radio.setChecked(interper == "shell")
        self.mode_layout.addWidget(self.direct_radio)
        self.mode_layout.addWidget(self.shell_radio)
        self.mode_layout.addStretch(1)
        self.actions = QWidget(self)
        actions = QHBoxLayout(self.actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        self.up_button = QToolButton(self.actions)
        self.down_button = QToolButton(self.actions)
        self.remove_button = QToolButton(self.actions)
        for button in (
            self.up_button,
            self.down_button,
            self.remove_button,
        ):
            set_button_role(button, "quiet")
            button.setFixedSize(CONTROL_HEIGHT, CONTROL_HEIGHT)
            actions.addWidget(button)
        layout.addLayout(self.header)

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
        self.code_edit.setPlainText(code)
        self.code_edit.textChanged.connect(self._sync_code_height)
        layout.addWidget(self.code_edit)
        self.retranslate(index)

    def _sync_code_height(self) -> None:
        """Show 4–10 script lines; keep long scripts scrollable, not page-filling."""
        lines = max(4, min(10, self.code_edit.document().blockCount()))
        padding = 2 * self.code_edit.frameWidth() + 2 * int(self.code_edit.document().documentMargin())
        scrollbar = self.code_edit.horizontalScrollBar().sizeHint().height()
        self.code_edit.setFixedHeight(
            lines * self.code_edit.fontMetrics().lineSpacing() + padding + scrollbar + 12
        )

    def _reflow_header(self) -> None:
        available = max(0, self.width() - 24)
        mode_width = sum(radio.sizeHint().width() for radio in (self.direct_radio, self.shell_radio)) + CONTROL_SPACING
        stacked = mode_width > available
        wide = (self.index_label.sizeHint().width() + mode_width
                + self.actions.sizeHint().width() + 2 * CONTROL_SPACING <= available)
        shape = (wide, stacked)
        if shape == self._header_shape:
            return
        self._header_shape = shape
        for widget in (self.index_label, self.modes, self.actions):
            self.header.removeWidget(widget)
        self.mode_layout.setDirection(QBoxLayout.TopToBottom if stacked else QBoxLayout.LeftToRight)
        self.header.setColumnStretch(0, 0 if wide else 1)
        self.header.setColumnStretch(1, 1 if wide else 0)
        self.header.addWidget(self.index_label, 0, 0)
        if wide:
            self.header.addWidget(self.modes, 0, 1)
            self.header.addWidget(self.actions, 0, 2)
        else:
            self.header.addWidget(self.actions, 0, 1, 1, 2, Qt.AlignRight)
            self.header.addWidget(self.modes, 1, 0, 1, 3)
        self.modes.setMinimumHeight(
            sum(radio.sizeHint().height() for radio in (self.direct_radio, self.shell_radio)) + CONTROL_SPACING
            if stacked else max(self.direct_radio.sizeHint().height(), self.shell_radio.sizeHint().height())
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow_header()

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
            button.setFixedSize(max(CONTROL_HEIGHT, button.minimumSizeHint().width()), CONTROL_HEIGHT)
            button.setAccessibleName(f"{text} {index}")
            button.setToolTip(text)
        self.code_edit.setPlaceholderText(tr("cross.viewer_placeholder"))
        self.code_edit.setAccessibleName(tr("cross.command_group_editor", index=index))
        self.direct_radio.setToolTip(tr("cross.command_direct_hint"))
        self.shell_radio.setToolTip(tr("cross.command_shell_hint"))
        self._header_shape = None
        self._reflow_header()
        self._sync_code_height()


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
        self.cards: list[_CommandGroupCard] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(CONTROL_SPACING)
        layout.setAlignment(Qt.AlignTop)
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

        drafts: list[tuple[str, str]] = []
        for code, interper in groups:
            if not isinstance(code, str):
                raise TypeError("command group code must be str")
            if interper not in ("direct", "shell"):
                raise ValueError(
                    f"invalid command group interper: {interper!r}"
                )
            drafts.append((code, interper))
        # Validate the entire candidate before replacing any existing draft.
        for card in self.cards:
            self.cards_layout.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.cards = []
        for code, mode in drafts:
            self._append_card(code, mode)
        self._update_card_actions()

    def groups(self) -> tuple[tuple[str, str], ...]:
        """Return the current groups in order, blank commands included."""

        return tuple(card.values() for card in self.cards)

    def command_groups_payload(self) -> dict[str, CommandGroup]:
        """Return sequentially keyed groups with blank commands dropped."""

        kept = [group for group in self.groups() if group[0].strip()]
        return {
            str(index): CommandGroup(str(index), code, mode)
            for index, (code, mode) in enumerate(kept, start=1)
        }

    @Slot()
    def add_group(self) -> None:
        self._append_card("", "direct")
        self._update_card_actions()
        self.cards[-1].code_edit.setFocus()

    # ---- internals --------------------------------------------------

    def _append_card(self, code: str, interper: str) -> None:
        card = _CommandGroupCard(self.language, len(self.cards) + 1, code, interper, self)
        card.up_button.clicked.connect(lambda: self._move_card(card, -1))
        card.down_button.clicked.connect(lambda: self._move_card(card, 1))
        card.remove_button.clicked.connect(lambda: self._remove_card(card))
        self.cards_layout.addWidget(card)
        self.cards.append(card)

    def _card_index(self, card: _CommandGroupCard) -> int | None:
        for position, item in enumerate(self.cards):
            if item is card:
                return position
        return None

    def _move_card(self, card: _CommandGroupCard, delta: int) -> None:
        index = self._card_index(card)
        if index is None:
            return
        target = index + delta
        if target < 0 or target >= len(self.cards):
            return
        self.cards.insert(target, self.cards.pop(index))
        self.cards_layout.removeWidget(card)
        self.cards_layout.insertWidget(target, card)
        self._update_card_actions()
        card.code_edit.setFocus()

    def _remove_card(self, card: _CommandGroupCard) -> None:
        index = self._card_index(card)
        if index is None:
            return
        self.cards.pop(index)
        self.cards_layout.removeWidget(card)
        card.hide()
        card.deleteLater()
        self._update_card_actions()
        if self.cards:
            self.cards[min(index, len(self.cards) - 1)].code_edit.setFocus()
        else:
            self.add_button.setFocus()

    def _update_card_actions(self) -> None:
        self.empty_label.setVisible(not self.cards)
        for position, card in enumerate(self.cards):
            card.retranslate(position + 1)
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
    if not segments or any(split_multicmd_template(segment) is not None for segment in segments):
        # Re-rendering a split segment must not reinterpret a literal MULTICMD
        # inside a path/argument as a new template. Keep this legacy form whole.
        return [(code, module.interper)]
    return [(segment, module.interper) for segment in segments]


__all__ = ["ModuleCommandGroupsEditor", "confirm_command_group_syntax"]
