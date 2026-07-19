"""Stable application-content styling for the Qt adapter."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class ThemeTokens:
    background: str = "#F4F7FB"
    surface: str = "#FFFFFF"
    surface_subtle: str = "#F8FAFC"
    text: str = "#172033"
    text_muted: str = "#607089"
    border: str = "#D8E0EB"
    primary: str = "#246BFD"
    primary_hover: str = "#1757D8"
    selection: str = "#DCE8FF"
    radius: int = 8


TOKENS = ThemeTokens()


def apply_application_theme(app: QApplication) -> None:
    """Apply one deterministic Qt style and EasyQC content token set."""

    app.setApplicationName("EasyQC")
    app.setOrganizationName("EasyQC")
    app.setStyle("Fusion")
    app.setProperty("easyqcBaseStyle", "Fusion")
    app.setStyleSheet(
        f"""
        QWidget {{
            color: {TOKENS.text};
            font-size: 13px;
        }}
        QMainWindow, QWidget#qtPreviewRoot {{
            background: {TOKENS.background};
        }}
        QLabel {{
            background: transparent;
        }}
        QFrame#contentSurface, QTableView {{
            background: {TOKENS.surface};
        }}
        QFrame#tableToolbar, QFrame#tableFooter, QFrame#filterConditionRow,
        QFrame#sortRuleRow, QFrame#qcHeader, QFrame#qcFooter,
        QFrame#configHeader {{
            background: {TOKENS.surface_subtle};
            border: 1px solid {TOKENS.border};
            border-radius: 6px;
        }}
        QFrame#previewBanner {{
            background: {TOKENS.surface_subtle};
            border: 1px solid {TOKENS.border};
            border-radius: {TOKENS.radius}px;
        }}
        QLabel#previewTitle {{
            font-size: 18px;
            font-weight: 600;
        }}
        QLabel#previewMessage, QLabel#tableStatus, QLabel#previewEmptyState {{
            color: {TOKENS.text_muted};
        }}
        QLabel#panelHint, QLabel#tableError, QLabel#filterError,
        QLabel#sortError, QLabel#columnsError {{
            color: {TOKENS.text_muted};
        }}
        QLabel#tableError, QLabel#filterError, QLabel#sortError,
        QLabel#columnsError, QLabel#qcError, QLabel#configError {{
            color: #B42318;
        }}
        QLabel#tableTitle, QLabel#panelHeading, QLabel#qcTitle,
        QLabel#configTitle {{
            font-size: 15px;
            font-weight: 600;
        }}
        QLabel#previewEmptyState {{
            background: {TOKENS.surface_subtle};
            border: 1px dashed {TOKENS.border};
            border-radius: {TOKENS.radius}px;
            padding: 12px;
        }}
        QTableView {{
            border: 1px solid {TOKENS.border};
            border-radius: {TOKENS.radius}px;
            gridline-color: {TOKENS.border};
            selection-background-color: {TOKENS.selection};
            selection-color: {TOKENS.text};
            alternate-background-color: {TOKENS.surface_subtle};
        }}
        QPushButton, QToolButton, QComboBox, QLineEdit {{
            min-height: 28px;
            border: 1px solid {TOKENS.border};
            border-radius: 6px;
            background: {TOKENS.surface};
            padding: 0 9px;
        }}
        QPushButton:hover, QToolButton:hover {{
            border-color: {TOKENS.primary};
        }}
        QPushButton#primaryAction {{
            color: #FFFFFF;
            background: {TOKENS.primary};
            border-color: {TOKENS.primary};
            font-weight: 600;
        }}
        QPushButton#primaryAction:hover {{
            background: {TOKENS.primary_hover};
        }}
        QPushButton#primaryAction:disabled {{
            color: #F8FAFC;
            background: #A8B4C7;
            border-color: #A8B4C7;
        }}
        QToolButton#filterChip {{
            color: {TOKENS.primary};
            background: {TOKENS.selection};
            border-color: #B8CEFF;
        }}
        QLabel#watchBadge {{
            color: #067647;
            background: #ECFDF3;
            border: 1px solid #ABEFC6;
            border-radius: 6px;
            padding: 6px 9px;
        }}
        QLabel#watchBadge[readOnly="true"] {{
            color: #9A3412;
            background: #FFF7ED;
            border-color: #FED7AA;
        }}
        QTabWidget::pane {{
            border: 1px solid {TOKENS.border};
            background: {TOKENS.surface};
        }}
        QTabBar::tab {{
            background: {TOKENS.surface_subtle};
            border: 1px solid {TOKENS.border};
            padding: 7px 12px;
        }}
        QTabBar::tab:selected {{
            background: {TOKENS.surface};
            color: {TOKENS.primary};
        }}
        QHeaderView::section {{
            background: {TOKENS.surface_subtle};
            color: {TOKENS.text};
            border: 0;
            border-right: 1px solid {TOKENS.border};
            border-bottom: 1px solid {TOKENS.border};
            padding: 8px 10px;
            font-weight: 600;
        }}
        QScrollBar:vertical, QScrollBar:horizontal {{
            background: {TOKENS.surface_subtle};
            border: 0;
        }}
        """
    )


__all__ = ["TOKENS", "ThemeTokens", "apply_application_theme"]
