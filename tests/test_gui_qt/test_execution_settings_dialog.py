from __future__ import annotations

from PySide6.QtCore import QSettings, Qt

from core.code_executor import CodeExecutor
from gui_qt.execution_settings_dialog import (
    VIEWER_SHELL_SETTING_KEY,
    ViewerExecutionSettingsDialog,
    apply_persisted_viewer_execution_setting,
)
from gui_qt.i18n import LanguageController


def _settings(tmp_path) -> QSettings:
    return QSettings(
        str(tmp_path / "execution-settings.ini"),
        QSettings.IniFormat,
    )


def test_persisted_viewer_shell_mode_defaults_false_and_recovers_invalid_value(
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    executor = CodeExecutor(shell_enabled=True)

    assert apply_persisted_viewer_execution_setting(executor, settings) is False
    assert executor.shell_enabled is False

    settings.setValue(VIEWER_SHELL_SETTING_KEY, "not-a-boolean")
    settings.sync()
    executor.set_shell_enabled(True)

    assert apply_persisted_viewer_execution_setting(executor, settings) is False
    assert executor.shell_enabled is False


def test_settings_dialog_saves_shell_mode_and_cancel_has_no_side_effect(
    qtbot,
    tmp_path,
) -> None:
    settings = _settings(tmp_path)
    language = LanguageController(settings=settings)
    executor = CodeExecutor()
    dialog = ViewerExecutionSettingsDialog(
        executor,
        settings,
        language,
    )
    qtbot.addWidget(dialog)
    dialog.show()

    assert dialog.direct_radio.isChecked()
    dialog.shell_radio.click()
    qtbot.mouseClick(dialog.save_button, Qt.LeftButton)

    settings.sync()
    assert executor.shell_enabled is True
    assert settings.value(VIEWER_SHELL_SETTING_KEY, type=bool) is True

    cancel_dialog = ViewerExecutionSettingsDialog(
        executor,
        settings,
        language,
    )
    qtbot.addWidget(cancel_dialog)
    cancel_dialog.show()
    cancel_dialog.direct_radio.click()
    qtbot.mouseClick(cancel_dialog.cancel_button, Qt.LeftButton)

    assert executor.shell_enabled is True
    assert settings.value(VIEWER_SHELL_SETTING_KEY, type=bool) is True


def test_settings_dialog_uses_current_interface_language(qtbot, tmp_path) -> None:
    settings = _settings(tmp_path)
    language = LanguageController(settings=settings, language="en")
    dialog = ViewerExecutionSettingsDialog(
        CodeExecutor(),
        settings,
        language,
    )
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Settings"
    assert dialog.direct_radio.text() == "Direct execution (shell=False)"
    assert dialog.shell_radio.text() == "Shell execution (shell=True)"
