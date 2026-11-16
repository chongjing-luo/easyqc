"""Painted empty-state guidance for item views that have no rows yet."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QAbstractItemView, QWidget

from gui_qt.i18n import LanguageController
from gui_qt.icons import glyph_pixmap
from gui_qt.theme import PALETTE

_GLYPH_SIZE = 30
_TEXT_WIDTH_RATIO = 0.82


class EmptyStateOverlay(QWidget):
    """Centered glyph, title and hint drawn over an empty view's viewport.

    The overlay is transparent to the mouse, so the view keeps its context
    menus and drops.  Visibility follows the model row count; text is pulled
    from translation keys at paint time, so a language switch only repaints.
    """

    def __init__(
        self,
        view: QAbstractItemView,
        *,
        glyph: str,
        title_key: str,
        hint_key: str,
        language: LanguageController,
        should_show: Callable[[], bool] | None = None,
    ) -> None:
        if not isinstance(view, QAbstractItemView):
            raise TypeError("EmptyStateOverlay requires an item view")
        super().__init__(view.viewport())
        self.setObjectName("emptyStateOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self._view = view
        self._glyph = glyph
        self._title_key = title_key
        self._hint_key = hint_key
        self._language = language
        self._should_show = should_show
        view.viewport().installEventFilter(self)
        model = view.model()
        for signal in (
            model.rowsInserted,
            model.rowsRemoved,
            model.modelReset,
            model.layoutChanged,
        ):
            signal.connect(self.sync)
        language.languageChanged.connect(self._retranslate)
        self.setGeometry(view.viewport().rect())
        self._retranslate()
        self.sync()

    @property
    def title(self) -> str:
        return self._language.tr(self._title_key)

    @property
    def hint(self) -> str:
        return self._language.tr(self._hint_key)

    def _retranslate(self, _language: str | None = None) -> None:
        self.setAccessibleName(self.title)
        self.setAccessibleDescription(self.hint)
        self.update()

    def sync(self, *_args) -> None:
        empty = self._view.model().rowCount() == 0
        allowed = self._should_show() if self._should_show is not None else True
        self.setVisible(bool(empty and allowed))
        if self.isVisible():
            self.raise_()
            self.update()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._view.viewport() and event.type() == QEvent.Type.Resize:
            self.setGeometry(self._view.viewport().rect())
        return False

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = self.rect()
            text_width = max(80, int(rect.width() * _TEXT_WIDTH_RATIO))
            title_font = QFont(self.font())
            title_font.setBold(True)
            hint_font = QFont(self.font())
            painter.setFont(title_font)
            title_rect = painter.boundingRect(
                QRect(0, 0, text_width, 1000),
                Qt.AlignHCenter | Qt.TextWordWrap,
                self.title,
            )
            painter.setFont(hint_font)
            hint_rect = painter.boundingRect(
                QRect(0, 0, text_width, 1000),
                Qt.AlignHCenter | Qt.TextWordWrap,
                self.hint,
            )
            spacing = 8
            total = _GLYPH_SIZE + spacing + title_rect.height() + 4 + hint_rect.height()
            top = max(12, (rect.height() - total) // 2)
            left = (rect.width() - text_width) // 2
            pixmap = glyph_pixmap(
                self._glyph,
                PALETTE["text_tertiary"],
                _GLYPH_SIZE,
                dpr=self.devicePixelRatioF(),
                stroke=1.5,
            )
            painter.drawPixmap((rect.width() - _GLYPH_SIZE) // 2, top, pixmap)
            y = top + _GLYPH_SIZE + spacing
            painter.setFont(title_font)
            painter.setPen(QColor(PALETTE["text_secondary"]))
            painter.drawText(
                QRect(left, y, text_width, title_rect.height()),
                Qt.AlignHCenter | Qt.TextWordWrap,
                self.title,
            )
            y += title_rect.height() + 4
            painter.setFont(hint_font)
            painter.setPen(QColor(PALETTE["text_tertiary"]))
            painter.drawText(
                QRect(left, y, text_width, hint_rect.height()),
                Qt.AlignHCenter | Qt.TextWordWrap,
                self.hint,
            )
        finally:
            painter.end()


__all__ = ["EmptyStateOverlay"]
