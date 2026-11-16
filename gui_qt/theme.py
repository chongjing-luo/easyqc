"""Semantic EasyQC Qt theme: calm clinical palette, native fonts and window chrome.

The theme is one application stylesheet managed between two marker comments so
host rules survive re-application.  Indicator images (combo arrows, check marks,
radio dots) are painted with QtGui at runtime and cached as PNG files in the
per-user cache directory, so packaged builds need no image assets, no QtSvg and
no image-format plugins.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from pathlib import Path
from string import Template

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QStyle,
)

CONTENT_MARGIN = 20
SECTION_SPACING = 16
CONTROL_SPACING = 8
CONTROL_HEIGHT = 34
NAVIGATION_ROW_HEIGHT = 44
COMBO_POPUP_MIN_VISIBLE_ITEMS = 5
COMBO_POPUP_MAX_VISIBLE_ITEMS = 8
_THEME_BLOCK_START = "/* EasyQC managed theme: start */"
_THEME_BLOCK_END = "/* EasyQC managed theme: end */"
_THEME_BLOCK_PATTERN = re.compile(
    rf"{re.escape(_THEME_BLOCK_START)}.*?"
    rf"{re.escape(_THEME_BLOCK_END)}",
    re.DOTALL,
)

# Design tokens.  Names describe intent, not hue, so pages stay consistent.
PALETTE = {
    "canvas": "#F3F5F9",
    "surface": "#FFFFFF",
    "surface_subtle": "#F8FAFC",
    "surface_sunken": "#EEF2F7",
    "border": "#E0E6EE",
    "border_strong": "#CAD3DF",
    "border_hover": "#9DAABD",
    "text": "#142033",
    "text_secondary": "#5B6678",
    "text_tertiary": "#8A94A6",
    "text_disabled": "#A8B1BF",
    "primary": "#2D6BC4",
    "primary_hover": "#255DAE",
    "primary_pressed": "#1E4E93",
    "primary_soft": "#E8F0FC",
    "primary_soft_strong": "#D6E4F9",
    "accent": "#1FC2AE",
    "success": "#1E8E63",
    "success_soft": "#E3F5EC",
    "warning": "#B7731A",
    "warning_soft": "#FDF1DE",
    "danger": "#C23B3B",
    "danger_soft": "#FDECEC",
    "danger_border": "#E7B4B4",
    "nav_top": "#14223A",
    "nav_bottom": "#0D1727",
    "nav_text": "#C3CEDD",
    "nav_text_muted": "#8796AD",
    "nav_rule": "#22324B",
    "tooltip": "#1A2436",
}

_INDICATOR_SPECS = {
    # file stem: (glyph, color, logical size, stroke)
    "chevron-down": ("chevron-down", PALETTE["text_secondary"], 12, 2.2),
    "chevron-down-disabled": ("chevron-down", PALETTE["text_disabled"], 12, 2.2),
    "chevron-up": ("chevron-up", PALETTE["text_secondary"], 10, 2.4),
    "chevron-up-disabled": ("chevron-up", PALETTE["text_disabled"], 10, 2.4),
    "chevron-right": ("chevron-right", PALETTE["text_secondary"], 12, 2.2),
    "check": ("check", "#FFFFFF", 12, 3.0),
    "check-disabled": ("check", "#F4F6F9", 12, 3.0),
    "dash": ("dash", "#FFFFFF", 12, 3.0),
    "dot": ("dot", "#FFFFFF", 12, 0.1),
}

THEME_TEMPLATE = Template(
    """
QMainWindow, QWidget#qtPreviewRoot, QDialog {
    background: $canvas;
    color: $text;
}
QWidget#primaryNavigationPanel {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 $nav_top, stop:1 $nav_bottom);
    border: none;
}
QLabel#productName {
    color: #FFFFFF;
    font-size: 20px;
    font-weight: 700;
}
QLabel#productTagline, QLabel#navigationSectionLabel {
    color: $nav_text_muted;
    font-size: 11px;
    font-weight: 600;
    padding: 2px 4px 0 2px;
}
QLabel#brandMark, QLabel#navigationIcon {
    background: transparent;
}
QPushButton#navigationToggle, QToolButton#navigationRailButton {
    background: transparent;
    color: $nav_text;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 0;
}
QPushButton#navigationToggle:hover, QPushButton#navigationToggle:focus,
QToolButton#navigationRailButton:hover {
    background: rgba(255, 255, 255, 0.08);
    border-color: rgba(255, 255, 255, 0.10);
}
QToolButton#navigationRailButton:checked {
    background: rgba(96, 165, 250, 0.20);
    border-color: rgba(96, 165, 250, 0.35);
}
QWidget#navigationRail {
    background: transparent;
}
QListWidget#primaryNavigation {
    background: transparent;
    border: none;
    outline: none;
    color: $nav_text;
}
QListWidget#primaryNavigation::item {
    border: 0;
    border-left: 3px solid transparent;
    border-radius: 8px;
    margin: 1px 0;
    padding: 8px 10px 8px 9px;
}
QListWidget#primaryNavigation::item:hover {
    background: rgba(255, 255, 255, 0.06);
    color: #FFFFFF;
}
QListWidget#primaryNavigation::item:selected {
    background: rgba(96, 165, 250, 0.18);
    border-left: 3px solid $accent;
    color: #FFFFFF;
}
QWidget#projectNavigationContent {
    background: transparent;
}
QLabel#projectNavigationLabel {
    background: transparent;
    color: #F4F7FB;
    font-weight: 600;
}
QLabel#projectNavigationContext {
    background: transparent;
    color: $accent;
    font-size: 11px;
}
QWidget#settingsBar {
    border-top: 1px solid $nav_rule;
}
QPushButton#settingsButton {
    background: transparent;
    color: $nav_text;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 7px 10px;
    text-align: left;
}
QPushButton#settingsButton:hover,
QPushButton#settingsButton:focus {
    background: rgba(255, 255, 255, 0.07);
    color: #FFFFFF;
}
QPushButton#settingsButton:checked {
    background: rgba(96, 165, 250, 0.18);
    color: #FFFFFF;
}
QStackedWidget#workspaceStack {
    background: $canvas;
    border: none;
}
QFrame[surface="true"], QWidget[surface="true"] {
    background: $surface;
    border: 1px solid $border;
    border-radius: 12px;
}
QFrame[surface="subtle"], QWidget[surface="subtle"] {
    background: $surface_subtle;
    border: 1px solid $border;
    border-radius: 10px;
}
QFrame[frameShape="4"], QFrame[frameShape="5"] {
    border: none;
    background: $border;
    max-height: 1px;
    min-height: 1px;
}
QFrame[frameShape="5"] {
    max-height: 16777215px;
    max-width: 1px;
    min-width: 1px;
}
QScrollArea#qcListImportScroll, QScrollArea#qcListImportScroll > QWidget,
QScrollArea#qcListImportScroll > QWidget > QWidget#qtQcListImportPage {
    background: transparent;
}
QLabel[role="sectionTitle"] {
    color: $text;
    font-weight: 650;
}
QLabel[role="secondary"] {
    color: $text_secondary;
}
QLabel[role="tertiary"] {
    color: $text_tertiary;
}
QLabel[role="error"] {
    color: $danger;
}
QLabel[state="empty"] {
    color: $text_tertiary;
}
QLabel[role="pill"] {
    border-radius: 10px;
    padding: 2px 10px;
    font-weight: 600;
    background: $surface_sunken;
    color: $text_secondary;
}
QLabel[role="pill"][tone="success"] {
    background: $success_soft;
    color: $success;
}
QLabel[role="pill"][tone="warning"] {
    background: $warning_soft;
    color: $warning;
}
QLabel[role="pill"][tone="accent"] {
    background: $primary_soft;
    color: $primary;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit,
QPlainTextEdit, QTextEdit {
    background: $surface;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 5px 9px;
    selection-background-color: $primary;
    selection-color: #FFFFFF;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover,
QDateEdit:hover, QDateTimeEdit:hover, QPlainTextEdit:hover, QTextEdit:hover {
    border-color: $border_hover;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QDateTimeEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border: 2px solid $primary;
    padding: 4px 8px;
}
QLineEdit:read-only, QPlainTextEdit[readOnly="true"], QTextEdit[readOnly="true"] {
    background: $surface_subtle;
    color: $text_secondary;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled,
QDoubleSpinBox:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {
    background: $surface_sunken;
    color: $text_disabled;
    border-color: $border;
}
QComboBox {
    padding-right: 30px;
}
QComboBox:focus {
    padding-right: 29px;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    width: 26px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: url("$asset_chevron_down");
    width: 12px;
    height: 12px;
}
QComboBox::down-arrow:disabled {
    image: url("$asset_chevron_down_disabled");
}
QComboBox QAbstractItemView {
    background: $surface;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: $primary_soft;
    selection-color: $text;
}
QSpinBox, QDoubleSpinBox {
    padding-right: 24px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    width: 22px;
    border: none;
    background: transparent;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url("$asset_chevron_up");
    width: 10px;
    height: 10px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url("$asset_chevron_down");
    width: 10px;
    height: 10px;
}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {
    image: url("$asset_chevron_up_disabled");
}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {
    image: url("$asset_chevron_down_disabled");
}
QCheckBox, QRadioButton {
    color: $text;
    spacing: 8px;
    background: transparent;
}
QCheckBox:disabled, QRadioButton:disabled {
    color: $text_disabled;
}
QCheckBox::indicator, QRadioButton::indicator,
QTableView::indicator, QListView::indicator, QTreeView::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid $border_hover;
    background: $surface;
}
QCheckBox::indicator, QTableView::indicator, QListView::indicator,
QTreeView::indicator {
    border-radius: 5px;
}
QRadioButton::indicator {
    border-radius: 9px;
}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {
    border-color: $primary;
}
QCheckBox::indicator:checked, QTableView::indicator:checked,
QListView::indicator:checked, QTreeView::indicator:checked {
    background: $primary;
    border-color: $primary;
    image: url("$asset_check");
}
QCheckBox::indicator:indeterminate {
    background: $primary;
    border-color: $primary;
    image: url("$asset_dash");
}
QRadioButton::indicator:checked {
    background: $primary;
    border-color: $primary;
    image: url("$asset_dot");
}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {
    background: $surface_sunken;
    border-color: $border;
}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {
    background: $border_hover;
    border-color: $border_hover;
    image: url("$asset_check_disabled");
}
QRadioButton::indicator:checked:disabled {
    image: url("$asset_dot");
}
QPushButton, QToolButton {
    background: $surface;
    color: #243147;
    border: 1px solid $border_strong;
    border-radius: 8px;
    padding: 5px 12px;
}
QPushButton:hover, QToolButton:hover {
    background: #F5F8FC;
    border-color: $border_hover;
    color: $text;
}
QPushButton:focus, QToolButton:focus {
    border-color: $primary;
}
QPushButton:pressed, QToolButton:pressed {
    background: $surface_sunken;
    border-color: $border_hover;
}
QPushButton:checked, QToolButton:checked {
    background: $primary_soft;
    border-color: $primary;
    color: $primary_pressed;
}
QPushButton:disabled, QToolButton:disabled {
    background: #F2F4F7;
    color: $text_disabled;
    border-color: #E1E5EB;
}
QPushButton[role="primary"], QToolButton[role="primary"] {
    background: $primary;
    color: #FFFFFF;
    border-color: $primary;
    font-weight: 600;
}
QPushButton[role="primary"]:hover, QToolButton[role="primary"]:hover {
    background: $primary_hover;
    border-color: $primary_hover;
    color: #FFFFFF;
}
QPushButton[role="primary"]:focus, QToolButton[role="primary"]:focus {
    border-color: $primary_pressed;
}
QPushButton[role="primary"]:pressed, QToolButton[role="primary"]:pressed {
    background: $primary_pressed;
    border-color: $primary_pressed;
}
QPushButton[role="primary"]:disabled, QToolButton[role="primary"]:disabled {
    background: #B9CCE8;
    border-color: #B9CCE8;
    color: #F4F7FB;
}
QPushButton[role="danger"], QToolButton[role="danger"] {
    color: $danger;
    border-color: $danger_border;
    background: $surface;
}
QPushButton[role="danger"]:hover, QToolButton[role="danger"]:hover {
    background: $danger_soft;
    border-color: #D58A8A;
    color: $danger;
}
QPushButton[role="danger"]:disabled, QToolButton[role="danger"]:disabled {
    color: #D9A9A9;
    border-color: #EED7D7;
    background: #FBF6F6;
}
QPushButton[role="quiet"], QToolButton[role="quiet"] {
    background: transparent;
    border-color: transparent;
    color: $text_secondary;
}
QPushButton[role="quiet"]:hover, QToolButton[role="quiet"]:hover {
    background: $surface_sunken;
    color: $text;
}
QPushButton[role="quiet"]:disabled, QToolButton[role="quiet"]:disabled {
    background: transparent;
    border-color: transparent;
    color: $text_disabled;
}
QToolButton::menu-indicator, QPushButton::menu-indicator {
    image: url("$asset_chevron_down");
    width: 10px;
    height: 10px;
    subcontrol-origin: padding;
    right: 6px;
}
QToolButton[popupMode="1"], QToolButton[popupMode="2"] {
    padding-right: 22px;
}
QTableWidget#constantsTable QPushButton,
QTableWidget#constantTemplatesTable QPushButton {
    min-height: 26px;
    max-height: 28px;
    padding: 2px 9px;
    border-radius: 6px;
}
QTableWidget#constantsTable QPushButton[role="danger"],
QTableWidget#constantTemplatesTable QPushButton[role="danger"] {
    border-color: transparent;
    background: transparent;
}
QTableWidget#constantsTable QPushButton[role="danger"]:hover,
QTableWidget#constantTemplatesTable QPushButton[role="danger"]:hover {
    background: $danger_soft;
}
QTableView, QTableWidget, QListView, QListWidget, QTreeView, QTreeWidget {
    background: $surface;
    alternate-background-color: #F9FAFC;
    color: $text;
    border: 1px solid $border;
    border-radius: 10px;
    gridline-color: #EDF1F6;
    selection-background-color: $primary_soft_strong;
    selection-color: $text;
    outline: none;
}
QTableView::item:hover, QListView::item:hover, QTreeView::item:hover {
    background: #F1F6FD;
}
QTableView::item:selected, QListView::item:selected, QTreeView::item:selected {
    background: $primary_soft_strong;
    color: $text;
}
QListWidget#projectList::item, QListWidget#moduleTemplatesList::item {
    padding: 8px 10px;
    border-radius: 7px;
    margin: 1px 2px;
}
QHeaderView {
    background: transparent;
    border: none;
}
QHeaderView::section {
    background: #F5F7FA;
    color: #3D4A5E;
    border: none;
    border-right: 1px solid #EBEFF4;
    border-bottom: 1px solid $border;
    padding: 7px 8px;
    font-weight: 600;
}
QHeaderView::section:vertical {
    background: #FAFBFD;
    color: $text_tertiary;
    font-weight: 500;
    border-right: 1px solid $border;
    border-bottom: 1px solid #F0F3F7;
    padding: 0 6px;
}
QHeaderView::section:hover {
    background: #EDF2F8;
}
QTableCornerButton::section {
    background: #F5F7FA;
    border: none;
    border-right: 1px solid #EBEFF4;
    border-bottom: 1px solid $border;
}
QHeaderView::up-arrow {
    image: url("$asset_chevron_up");
    width: 9px;
    height: 9px;
}
QHeaderView::down-arrow {
    image: url("$asset_chevron_down");
    width: 9px;
    height: 9px;
}
QTabWidget::pane {
    border: none;
    border-top: 1px solid $border;
    top: -1px;
    background: transparent;
}
QTabWidget::tab-bar {
    left: 4px;
}
QTabBar {
    background: transparent;
    qproperty-drawBase: 0;
}
QTabBar::tab {
    background: transparent;
    color: $text_secondary;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 9px 16px 8px 16px;
    margin-right: 4px;
}
QTabBar::tab:hover {
    color: $text;
    border-bottom-color: $border_strong;
}
QTabBar::tab:selected {
    color: $primary;
    border-bottom-color: $primary;
}
QTabBar::tab:disabled {
    color: $text_disabled;
}
QGroupBox {
    background: $surface;
    color: #26344A;
    border: 1px solid $border;
    border-radius: 10px;
    margin-top: 18px;
    padding: 2px 2px 0 2px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 4px;
    top: 0;
    padding: 0 4px;
    color: #26344A;
}
QGroupBox:disabled, QGroupBox::title:disabled {
    color: $text_disabled;
}
QToolBar {
    background: transparent;
    border: none;
    spacing: 6px;
}
QToolBar::separator {
    background: $border;
    width: 1px;
    margin: 6px 4px;
}
QToolBar[compact="true"] QToolButton {
    padding-left: 8px;
    padding-right: 8px;
}
QToolBar#configModuleListToolbar QToolButton {
    padding-left: 4px;
    padding-right: 4px;
}
QToolButton#qt_toolbar_ext_button {
    border: none;
    background: transparent;
    padding: 0;
}
QSplitter::handle {
    background: transparent;
}
QSplitter::handle:hover {
    background: $primary_soft_strong;
}
QSplitter::handle:horizontal {
    width: 6px;
}
QSplitter::handle:vertical {
    height: 6px;
}
QScrollArea {
    background: transparent;
}
QScrollArea > QWidget#qt_scrollarea_viewport,
QScrollArea > QWidget#qt_scrollarea_viewport > QWidget {
    background: transparent;
}
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #C3CCD8;
    min-height: 32px;
    border-radius: 4px;
    margin: 0 2px;
}
QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #C3CCD8;
    min-width: 32px;
    border-radius: 4px;
    margin: 2px 0;
}
QScrollBar::handle:hover {
    background: #9FACBD;
}
QScrollBar::handle:pressed {
    background: #8494A9;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: transparent;
}
QProgressBar {
    background: #E4E9F0;
    border: none;
    border-radius: 3px;
    min-height: 6px;
    max-height: 6px;
    text-align: center;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 $accent, stop:1 $primary);
    border-radius: 3px;
}
QMenu {
    background: $surface;
    color: $text;
    border: 1px solid $border_strong;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    background: transparent;
    padding: 7px 28px 7px 12px;
    border-radius: 6px;
}
QMenu::item:selected {
    background: $primary_soft;
    color: $text;
}
QMenu::item:disabled {
    color: $text_disabled;
}
QMenu::separator {
    height: 1px;
    background: $border;
    margin: 5px 8px;
}
QMenu::right-arrow {
    image: url("$asset_chevron_right");
    width: 10px;
    height: 10px;
}
QMenu::indicator {
    width: 14px;
    height: 14px;
}
QToolTip {
    background: $tooltip;
    color: #F2F5F9;
    border: 1px solid $tooltip;
    border-radius: 6px;
    padding: 5px 8px;
}
QMessageBox QLabel {
    color: $text;
}
QWidget#startupCard {
    background: $surface;
    border: 1px solid #D3DBE6;
    border-radius: 0;
}
QWidget#startupAccent {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 $accent, stop:1 $primary);
    border: none;
    min-height: 4px;
    max-height: 4px;
}
QLabel#startupProductName {
    color: #14263F;
    font-size: 30px;
    font-weight: 700;
}
QLabel#startupSubtitle {
    color: $text_secondary;
}
QLabel#startupStatus {
    color: #40516A;
}
QLabel#startupMeta {
    color: $text_tertiary;
    font-size: 11px;
}
QWidget#qcSessionHeader {
    background: transparent;
}
QLabel#qcCurrentItem {
    color: $text;
    font-size: 16px;
    font-weight: 700;
}
QLabel#qcSessionMeta, QLabel#qcProgressText {
    color: $text_secondary;
}
QLabel#qcShortcutHint {
    color: $text_tertiary;
    font-size: 11px;
}
QGroupBox#scoreGroup QPushButton {
    min-width: 48px;
    border-radius: 8px;
    font-weight: 600;
    padding: 5px 14px;
}
QGroupBox#scoreGroup QPushButton:checked {
    background: $primary;
    border-color: $primary;
    color: #FFFFFF;
}
QGroupBox#scoreGroup QPushButton:checked:disabled {
    background: #B9CCE8;
    border-color: #B9CCE8;
    color: #FFFFFF;
}
QGroupBox#scoreGroup QPushButton[scoreChoice="unrated"] {
    font-weight: 500;
    color: $text_secondary;
}
QGroupBox#scoreGroup QPushButton[scoreChoice="unrated"]:checked {
    background: $surface_sunken;
    border-color: $border_hover;
    color: #3B4658;
}
QWidget#qcActionColumn QPushButton {
    font-weight: 600;
}
QWidget#commandOutputHeader {
    background: transparent;
}
"""
)


def _asset_digest() -> str:
    return hashlib.sha256(
        repr(sorted(_INDICATOR_SPECS.items())).encode("utf-8")
    ).hexdigest()[:12]


def _asset_directory_candidates():
    """Yield writable-looking per-user locations for generated theme images."""

    digest = _asset_digest()
    try:
        from platformdirs import PlatformDirs

        yield (
            Path(PlatformDirs("EasyQC", appauthor=False).user_cache_path)
            / "theme"
            / digest
        )
    except Exception:  # pragma: no cover - platformdirs is a hard dependency
        pass
    try:
        yield Path(tempfile.mkdtemp(prefix=f"easyqc-theme-{digest}-"))
    except OSError:  # pragma: no cover - no writable temporary directory
        return


def _write_assets(directory: Path) -> dict[str, str]:
    from gui_qt.icons import render_glyph_image

    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    for stem, (glyph, color, size, stroke) in _INDICATOR_SPECS.items():
        for suffix, scale in (("", 1), ("@2x", 2), ("@3x", 3)):
            target = directory / f"{stem}{suffix}.png"
            if target.is_file() and target.stat().st_size > 0:
                continue
            image = render_glyph_image(glyph, color, size * scale, stroke=stroke)
            partial = directory / f".{stem}{suffix}.png.partial"
            if not image.save(str(partial), "PNG"):
                raise OSError(f"EasyQC could not write theme asset: {target}")
            partial.replace(target)
        paths[stem] = (directory / f"{stem}.png").as_posix()
    return paths


_ASSET_CACHE: dict[str, str] | None = None


def theme_asset_paths() -> dict[str, str]:
    """Render indicator PNGs once per process and return their URL paths.

    Theme images are cosmetic: when no location is writable the theme still
    applies, with Qt's default arrows and check marks.
    """

    global _ASSET_CACHE
    if _ASSET_CACHE is not None:
        return _ASSET_CACHE
    paths: dict[str, str] = {}
    for directory in _asset_directory_candidates():
        try:
            paths = _write_assets(directory)
            break
        except OSError:
            continue
    _ASSET_CACHE = paths
    return paths


def build_theme_stylesheet() -> str:
    available = theme_asset_paths()
    assets = {
        f"asset_{stem.replace('-', '_')}": available.get(stem, "")
        for stem in _INDICATOR_SPECS
    }
    return THEME_TEMPLATE.substitute(**PALETTE, **assets).strip()


THEME_STYLESHEET = THEME_TEMPLATE.template


def configure_application_identity(app: QApplication) -> None:
    """Set stable metadata and the window icon without touching fonts or chrome."""

    app.setApplicationName("EasyQC")
    app.setOrganizationName("EasyQC")
    if isinstance(app, QGuiApplication) and app.windowIcon().isNull():
        from gui_qt.icons import application_icon

        app.setWindowIcon(application_icon())


def configure_combo_box_presentation(combo: QComboBox) -> None:
    """Give one combo a readable, platform-derived popup viewport."""

    if not isinstance(combo, QComboBox):
        raise TypeError("configure_combo_box_presentation requires QComboBox")
    count = combo.count()
    if count <= 0:
        return

    visible_items = (
        count
        if count < COMBO_POPUP_MIN_VISIBLE_ITEMS
        else min(count, COMBO_POPUP_MAX_VISIBLE_ITEMS)
    )
    combo.setMaxVisibleItems(visible_items)
    view = combo.view()
    row_height = max(
        (
            view.sizeHintForRow(index)
            for index in range(min(count, visible_items))
        ),
        default=-1,
    )
    row_height = max(
        row_height,
        view.fontMetrics().lineSpacing() + CONTROL_SPACING,
    )
    frame_width = view.style().pixelMetric(
        QStyle.PixelMetric.PM_DefaultFrameWidth,
        None,
        view,
    )
    view.setMinimumHeight(
        visible_items * row_height + 2 * max(0, frame_width)
    )

    text_width = max(
        view.fontMetrics().horizontalAdvance(combo.itemText(index))
        for index in range(count)
    )
    icon_width = max(
        (
            combo.iconSize().width()
            for index in range(count)
            if not combo.itemIcon(index).isNull()
        ),
        default=0,
    )
    scrollbar_width = (
        view.style().pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent,
            None,
            view,
        )
        if count > visible_items
        else 0
    )
    desired_width = (
        text_width
        + icon_width
        + scrollbar_width
        + 2 * CONTROL_SPACING
        + 2 * max(0, frame_width)
    )
    screen = combo.screen()
    if screen is not None:
        desired_width = min(
            desired_width,
            max(
                combo.width(),
                screen.availableGeometry().width() - 2 * CONTENT_MARGIN,
            ),
        )
    view.setMinimumWidth(max(combo.width(), desired_width))


class _ComboBoxPresentationFilter(QObject):
    """Apply the shared popup policy to existing and future combo boxes."""

    _CONFIGURE_EVENTS = frozenset(
        {
            QEvent.Type.Polish,
            QEvent.Type.Show,
            QEvent.Type.FocusIn,
            QEvent.Type.MouseButtonPress,
            QEvent.Type.KeyPress,
        }
    )

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            isinstance(watched, QComboBox)
            and event.type() in self._CONFIGURE_EVENTS
        ):
            configure_combo_box_presentation(watched)
        return False


def _install_combo_box_presentation(app: QApplication) -> None:
    existing = getattr(app, "_easyqc_combo_box_presentation_filter", None)
    if not isinstance(existing, _ComboBoxPresentationFilter):
        existing = _ComboBoxPresentationFilter(app)
        app.installEventFilter(existing)
        setattr(app, "_easyqc_combo_box_presentation_filter", existing)
    for widget in app.allWidgets():
        if isinstance(widget, QComboBox):
            configure_combo_box_presentation(widget)


def apply_easyqc_theme(app: QApplication) -> None:
    """Apply the focused EasyQC widget theme once per application."""

    _install_combo_box_presentation(app)
    theme = build_theme_stylesheet()
    managed_theme = (
        f"{_THEME_BLOCK_START}\n{theme}\n{_THEME_BLOCK_END}"
    )
    existing = app.styleSheet()
    previous = app.property("_easyqc_theme_stylesheet")
    managed_blocks = _THEME_BLOCK_PATTERN.findall(existing)
    if managed_blocks == [managed_theme]:
        if previous != theme:
            app.setProperty("_easyqc_theme_stylesheet", theme)
        return
    existing = _THEME_BLOCK_PATTERN.sub("", existing).strip()
    combined = f"{existing}\n{managed_theme}".strip()
    app.setStyleSheet(combined)
    app.setProperty("_easyqc_theme_stylesheet", theme)


def set_button_role(button: QAbstractButton, role: str = "secondary") -> None:
    """Assign one visual action role while retaining native button behavior."""

    if role not in {"primary", "secondary", "danger", "quiet"}:
        raise ValueError(f"Unsupported EasyQC button role: {role!r}")
    button.setProperty("role", role)
    button.setMinimumHeight(CONTROL_HEIGHT)
    button.style().unpolish(button)
    button.style().polish(button)


def set_label_tone(label, role: str, tone: str | None = None) -> None:
    """Assign a semantic label role/tone and repolish only when it changed."""

    changed = label.property("role") != role or label.property("tone") != tone
    if not changed:
        return
    label.setProperty("role", role)
    label.setProperty("tone", tone)
    label.style().unpolish(label)
    label.style().polish(label)


__all__ = [
    "COMBO_POPUP_MAX_VISIBLE_ITEMS",
    "COMBO_POPUP_MIN_VISIBLE_ITEMS",
    "CONTROL_HEIGHT",
    "CONTROL_SPACING",
    "CONTENT_MARGIN",
    "NAVIGATION_ROW_HEIGHT",
    "PALETTE",
    "SECTION_SPACING",
    "THEME_STYLESHEET",
    "apply_easyqc_theme",
    "build_theme_stylesheet",
    "configure_combo_box_presentation",
    "configure_application_identity",
    "set_button_role",
    "set_label_tone",
    "theme_asset_paths",
]
