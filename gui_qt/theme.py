"""Small semantic Qt theme that keeps system fonts and native window chrome."""

from __future__ import annotations

import re

from PySide6.QtWidgets import QAbstractButton, QApplication

CONTENT_MARGIN = 20
SECTION_SPACING = 16
CONTROL_SPACING = 8
CONTROL_HEIGHT = 34
NAVIGATION_ROW_HEIGHT = 44
_THEME_BLOCK_START = "/* EasyQC managed theme: start */"
_THEME_BLOCK_END = "/* EasyQC managed theme: end */"
_THEME_BLOCK_PATTERN = re.compile(
    rf"{re.escape(_THEME_BLOCK_START)}.*?"
    rf"{re.escape(_THEME_BLOCK_END)}",
    re.DOTALL,
)

THEME_STYLESHEET = """
QMainWindow, QWidget#qtPreviewRoot {
    background: #F4F6F8;
    color: #172033;
}
QWidget#primaryNavigationPanel {
    background: #172235;
    border: none;
}
QLabel#productName {
    color: #FFFFFF;
    font-size: 22px;
    font-weight: 650;
}
QLabel#productTagline, QLabel#navigationSectionLabel {
    color: #A9B5C7;
}
QPushButton#navigationToggle {
    background: transparent;
    color: #DCE3EC;
    border: 1px solid #43526A;
    border-radius: 6px;
    padding: 0;
    font-size: 18px;
}
QPushButton#navigationToggle:hover,
QPushButton#navigationToggle:focus {
    background: #24334A;
    color: #FFFFFF;
    border-color: #6F829E;
}
QListWidget#primaryNavigation {
    background: transparent;
    border: none;
    outline: none;
    color: #DCE3EC;
}
QListWidget#primaryNavigation::item {
    border: 0;
    border-radius: 7px;
    margin: 2px 0;
    padding: 8px 10px;
}
QListWidget#primaryNavigation::item:hover {
    background: #24334A;
    color: #FFFFFF;
}
QListWidget#primaryNavigation::item:selected {
    background: #2E5F91;
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
    color: #B9C5D4;
}
QWidget#languageBar {
    border-top: 1px solid #314056;
}
QPushButton#languageToggle {
    background: #24334A;
    color: #F4F7FB;
    border: 1px solid #43526A;
}
QPushButton#languageToggle:hover,
QPushButton#languageToggle:focus {
    background: #2D4260;
    border-color: #7186A3;
}
QStackedWidget#workspaceStack {
    background: #F4F6F8;
    border: none;
}
QFrame[surface="true"], QWidget[surface="true"] {
    background: #FFFFFF;
    border: 1px solid #D9E0E8;
    border-radius: 9px;
}
QFrame[surface="subtle"], QWidget[surface="subtle"] {
    background: #F8FAFC;
    border: 1px solid #D9E0E8;
    border-radius: 8px;
}
QLabel[role="sectionTitle"] {
    color: #172033;
    font-weight: 650;
}
QLabel[role="secondary"] {
    color: #657187;
}
QLabel[role="error"] {
    color: #A83232;
}
QLineEdit, QComboBox, QSpinBox, QDateEdit, QDateTimeEdit,
QPlainTextEdit, QTextEdit {
    background: #FFFFFF;
    color: #172033;
    border: 1px solid #C8D1DC;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: #2E6FAE;
    selection-color: #FFFFFF;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus,
QDateTimeEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {
    border: 2px solid #2E6FAE;
    padding: 4px 7px;
}
QPushButton, QToolButton {
    background: #FFFFFF;
    color: #243147;
    border: 1px solid #B9C4D1;
    border-radius: 6px;
    padding: 5px 11px;
}
QPushButton:hover, QToolButton:hover {
    background: #F0F4F8;
    border-color: #8FA0B5;
}
QPushButton:pressed, QToolButton:pressed, QPushButton:checked,
QToolButton:checked {
    background: #DCE8F4;
    border-color: #2E6FAE;
}
QPushButton:disabled, QToolButton:disabled {
    background: #F1F3F5;
    color: #98A2B2;
    border-color: #D9DEE5;
}
QPushButton[role="primary"], QToolButton[role="primary"] {
    background: #2E6FAE;
    color: #FFFFFF;
    border-color: #2E6FAE;
    font-weight: 600;
}
QPushButton[role="primary"]:hover, QToolButton[role="primary"]:hover {
    background: #285F94;
    border-color: #285F94;
}
QPushButton[role="primary"]:pressed, QToolButton[role="primary"]:pressed {
    background: #214F7B;
    border-color: #214F7B;
}
QPushButton[role="danger"], QToolButton[role="danger"] {
    color: #A83232;
    border-color: #D8A9A9;
    background: #FFFFFF;
}
QPushButton[role="danger"]:hover, QToolButton[role="danger"]:hover {
    background: #FFF2F2;
    border-color: #C77777;
}
QPushButton[role="quiet"], QToolButton[role="quiet"] {
    background: transparent;
    border-color: transparent;
}
QTableView, QTableWidget, QListView, QTreeView {
    background: #FFFFFF;
    alternate-background-color: #F7F9FB;
    color: #172033;
    border: 1px solid #D9E0E8;
    border-radius: 6px;
    gridline-color: #E5EAF0;
    selection-background-color: #D9E9F8;
    selection-color: #172033;
}
QHeaderView::section {
    background: #EEF2F6;
    color: #38465B;
    border: none;
    border-right: 1px solid #D9E0E8;
    border-bottom: 1px solid #D9E0E8;
    padding: 7px 8px;
    font-weight: 600;
}
QGroupBox {
    color: #26344A;
    border: 1px solid #D9E0E8;
    border-radius: 7px;
    margin-top: 10px;
    padding-top: 8px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}
QToolBar {
    background: transparent;
    border: none;
    spacing: 6px;
}
QToolBar[compact="true"] QToolButton {
    padding-left: 7px;
    padding-right: 7px;
}
QToolBar#configModuleListToolbar QToolButton {
    padding-left: 2px;
    padding-right: 2px;
}
QScrollBar:vertical {
    background: transparent;
    width: 12px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: #B8C3D0;
    min-height: 28px;
    border-radius: 5px;
}
QScrollBar:horizontal {
    background: transparent;
    height: 12px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #B8C3D0;
    min-width: 28px;
    border-radius: 5px;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0;
    height: 0;
}
QProgressBar {
    background: #E5EAF0;
    border: none;
    border-radius: 4px;
    min-height: 8px;
    max-height: 8px;
}
QProgressBar::chunk {
    background: #2E6FAE;
    border-radius: 4px;
}
QWidget#startupCard {
    background: #FFFFFF;
    border: 1px solid #D5DDE7;
    border-radius: 12px;
}
QLabel#startupProductName {
    color: #18324F;
    font-size: 30px;
    font-weight: 700;
}
QLabel#startupSubtitle {
    color: #657187;
}
QLabel#startupStatus {
    color: #40516A;
}
"""


def configure_application_identity(app: QApplication) -> None:
    """Set stable metadata without changing the host font or native window frame."""

    app.setApplicationName("EasyQC")
    app.setOrganizationName("EasyQC")


def apply_easyqc_theme(app: QApplication) -> None:
    """Apply the focused EasyQC widget theme once per application."""

    theme = THEME_STYLESHEET.strip()
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


__all__ = [
    "CONTROL_HEIGHT",
    "CONTROL_SPACING",
    "CONTENT_MARGIN",
    "NAVIGATION_ROW_HEIGHT",
    "SECTION_SPACING",
    "THEME_STYLESHEET",
    "apply_easyqc_theme",
    "configure_application_identity",
    "set_button_role",
]
