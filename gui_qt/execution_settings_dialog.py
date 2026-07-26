"""Application-local settings for external viewer command execution."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from core.code_executor import CodeExecutor
from gui_qt.i18n import LanguageController
from gui_qt.theme import CONTROL_SPACING, CONTENT_MARGIN, SECTION_SPACING


VIEWER_SHELL_SETTING_KEY = "viewer/use_shell"


def _stored_shell_enabled(settings: QSettings) -> bool:
    """Read one strict Boolean preference; malformed values recover to False."""

    value = settings.value(VIEWER_SHELL_SETTING_KEY, False)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return False


def apply_persisted_viewer_execution_setting(
    executor: CodeExecutor,
    settings: QSettings,
) -> bool:
    """Apply the persisted viewer mode to the shared executor and return it."""

    if not isinstance(executor, CodeExecutor):
        raise TypeError("viewer execution settings require CodeExecutor")
    if not isinstance(settings, QSettings):
        raise TypeError("viewer execution settings require QSettings")
    enabled = _stored_shell_enabled(settings)
    executor.set_shell_enabled(enabled)
    return enabled


class ViewerExecutionSettingsDialog(QDialog):
    """Edit one explicit command-execution preference without hidden changes."""

    def __init__(
        self,
        executor: CodeExecutor,
        settings: QSettings,
        language: LanguageController,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(executor, CodeExecutor):
            raise TypeError("ViewerExecutionSettingsDialog requires CodeExecutor")
        if not isinstance(settings, QSettings):
            raise TypeError("ViewerExecutionSettingsDialog requires QSettings")
        if not isinstance(language, LanguageController):
            raise TypeError(
                "ViewerExecutionSettingsDialog requires LanguageController"
            )
        self.executor = executor
        self.settings = settings
        self.language = language
        self.setObjectName("viewerExecutionSettingsDialog")
        self.setModal(True)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            CONTENT_MARGIN,
            CONTENT_MARGIN,
            CONTENT_MARGIN,
            CONTENT_MARGIN,
        )
        layout.setSpacing(SECTION_SPACING)

        self.intro_label = QLabel(self)
        self.intro_label.setWordWrap(True)
        self.intro_label.setProperty("role", "secondary")
        layout.addWidget(self.intro_label)

        self.mode_group = QGroupBox(self)
        mode_layout = QVBoxLayout(self.mode_group)
        mode_layout.setSpacing(CONTROL_SPACING)

        self.direct_radio = QRadioButton(self.mode_group)
        self.direct_detail_label = QLabel(self.mode_group)
        self.direct_detail_label.setWordWrap(True)
        self.direct_detail_label.setProperty("role", "secondary")
        self.direct_detail_label.setContentsMargins(
            CONTENT_MARGIN,
            0,
            0,
            CONTROL_SPACING,
        )
        mode_layout.addWidget(self.direct_radio)
        mode_layout.addWidget(self.direct_detail_label)

        self.shell_radio = QRadioButton(self.mode_group)
        self.shell_detail_label = QLabel(self.mode_group)
        self.shell_detail_label.setWordWrap(True)
        self.shell_detail_label.setProperty("role", "secondary")
        self.shell_detail_label.setContentsMargins(CONTENT_MARGIN, 0, 0, 0)
        mode_layout.addWidget(self.shell_radio)
        mode_layout.addWidget(self.shell_detail_label)
        layout.addWidget(self.mode_group)

        self.gate_label = QLabel(self)
        self.gate_label.setWordWrap(True)
        self.gate_label.setProperty("role", "secondary")
        layout.addWidget(self.gate_label)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            Qt.Orientation.Horizontal,
            self,
        )
        self.save_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Save
        )
        self.cancel_button = self.button_box.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        self.button_box.accepted.connect(self._save)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        shell_enabled = _stored_shell_enabled(settings)
        self.shell_radio.setChecked(shell_enabled)
        self.direct_radio.setChecked(not shell_enabled)
        self.language.languageChanged.connect(self.retranslate_ui)
        self.retranslate_ui()

    @Slot()
    def _save(self) -> None:
        enabled = self.shell_radio.isChecked()
        self.settings.setValue(VIEWER_SHELL_SETTING_KEY, enabled)
        self.settings.sync()
        self.executor.set_shell_enabled(enabled)
        self.accept()

    @Slot()
    def retranslate_ui(self, _language: str | None = None) -> None:
        self.setWindowTitle(self.language.tr("settings.title"))
        self.setAccessibleName(self.language.tr("settings.accessible"))
        self.intro_label.setText(self.language.tr("settings.viewer_intro"))
        self.mode_group.setTitle(self.language.tr("settings.viewer_group"))
        self.direct_radio.setText(self.language.tr("settings.direct_title"))
        self.direct_detail_label.setText(
            self.language.tr("settings.direct_detail")
        )
        self.shell_radio.setText(self.language.tr("settings.shell_title"))
        self.shell_detail_label.setText(
            self.language.tr("settings.shell_detail")
        )
        self.gate_label.setText(self.language.tr("settings.no_gate"))
        self.save_button.setText(self.language.tr("settings.save"))
        self.cancel_button.setText(self.language.tr("settings.cancel"))


__all__ = [
    "VIEWER_SHELL_SETTING_KEY",
    "ViewerExecutionSettingsDialog",
    "apply_persisted_viewer_execution_setting",
]
