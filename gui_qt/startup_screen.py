"""A lightweight, localized startup surface for the Qt product."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QGuiApplication, QScreen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from gui_qt.i18n import LanguageController, protect_user_text
from gui_qt.icons import brand_pixmap
from utils import __version__ as EASYQC_VERSION


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
        card_outer = QVBoxLayout(self.card)
        card_outer.setContentsMargins(1, 1, 1, 1)
        card_outer.setSpacing(0)
        self.accent = QWidget(self.card)
        self.accent.setObjectName("startupAccent")
        card_outer.addWidget(self.accent)
        content = QWidget(self.card)
        card_layout = QVBoxLayout(content)
        card_layout.setContentsMargins(39, 28, 39, 26)
        card_layout.setSpacing(12)
        card_outer.addWidget(content, 1)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(16)
        self.brand_mark = QLabel(content)
        self.brand_mark.setObjectName("brandMark")
        self.brand_mark.setFixedSize(56, 56)
        self.brand_mark.setPixmap(brand_pixmap(56))
        brand_row.addWidget(self.brand_mark, 0, Qt.AlignTop)
        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        self.product_label = QLabel("EasyQC", content)
        self.product_label.setObjectName("startupProductName")
        self.subtitle_label = QLabel("", content)
        self.subtitle_label.setObjectName("startupSubtitle")
        self.subtitle_label.setWordWrap(True)
        title_column.addWidget(self.product_label)
        title_column.addWidget(self.subtitle_label)
        brand_row.addLayout(title_column, 1)

        self.progress = QProgressBar(content)
        self.progress.setObjectName("startupProgress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        status_row = QHBoxLayout()
        self.status_label = QLabel("", content)
        self.status_label.setObjectName("startupStatus")
        self.version_label = QLabel("", content)
        self.version_label.setObjectName("startupMeta")
        protect_user_text(self.version_label, "text")
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.version_label)

        card_layout.addLayout(brand_row)
        card_layout.addStretch(1)
        card_layout.addWidget(self.progress)
        card_layout.addLayout(status_row)
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
        self.version_label.setText(
            self.language.tr("startup.version", version=EASYQC_VERSION)
        )

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
