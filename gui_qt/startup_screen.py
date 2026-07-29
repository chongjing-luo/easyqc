"""A lightweight, localized startup surface for the Qt preview."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QGuiApplication, QScreen
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from gui_qt.i18n import LanguageController


class QtStartupScreen(QWidget):
    """Show honest indeterminate startup state before the product shell."""

    def __init__(self, language: LanguageController, parent=None) -> None:
        if not isinstance(language, LanguageController):
            raise TypeError("QtStartupScreen requires a LanguageController")
        super().__init__(parent, Qt.WindowType.SplashScreen)
        self.language = language
        self._status_key = "startup.preparing"
        self.setObjectName("startupScreen")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setFixedSize(520, 252)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setObjectName("startupCard")
        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(40, 34, 40, 32)
        card_layout.setSpacing(12)

        self.product_label = QLabel("EasyQC", self.card)
        self.product_label.setObjectName("startupProductName")
        self.subtitle_label = QLabel("", self.card)
        self.subtitle_label.setObjectName("startupSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.progress = QProgressBar(self.card)
        self.progress.setObjectName("startupProgress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.status_label = QLabel("", self.card)
        self.status_label.setObjectName("startupStatus")

        card_layout.addWidget(self.product_label)
        card_layout.addWidget(self.subtitle_label)
        card_layout.addStretch(1)
        card_layout.addWidget(self.progress)
        card_layout.addWidget(self.status_label)
        outer.addWidget(self.card)

        self.language.languageChanged.connect(self.retranslate_ui)
        self.language.register_root(self)
        self.retranslate_ui()

    def set_status(self, message_key: str) -> None:
        # tr() validates the key before it becomes the retained state.
        localized = self.language.tr(message_key)
        self._status_key = message_key
        self.status_label.setText(localized)

    def retranslate_ui(self, _language: str | None = None) -> None:
        self.setAccessibleName(self.language.tr("startup.accessible"))
        self.subtitle_label.setText(self.language.tr("startup.subtitle"))
        self.status_label.setText(self.language.tr(self._status_key))

    def center_on_screen(self, screen: QScreen | None = None) -> None:
        """Center the splash inside one screen's usable desktop rectangle."""

        target = screen or QGuiApplication.screenAt(QCursor.pos())
        if target is None:
            target = QGuiApplication.primaryScreen()
        if target is None:
            raise RuntimeError("EasyQC 启动时没有可用屏幕")
        available = target.availableGeometry()
        if not available.isValid() or available.isEmpty():
            raise RuntimeError("EasyQC 启动屏幕没有有效的可用区域")
        self.move(available.center() - self.rect().center())

    def showEvent(self, event) -> None:
        self.center_on_screen()
        super().showEvent(event)

    def closeEvent(self, event) -> None:
        self.language.unregister_root(self)
        super().closeEvent(event)


__all__ = ["QtStartupScreen"]
