from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QComboBox, QPushButton, QWidget

from gui_qt.theme import (
    COMBO_POPUP_MAX_VISIBLE_ITEMS,
    COMBO_POPUP_MIN_VISIBLE_ITEMS,
    CONTROL_HEIGHT,
    CONTENT_MARGIN,
    NAVIGATION_ROW_HEIGHT,
    apply_easyqc_theme,
    set_button_role,
)


def test_theme_uses_stable_logical_metrics():
    assert CONTENT_MARGIN == 20
    assert CONTROL_HEIGHT >= 32
    assert NAVIGATION_ROW_HEIGHT >= 42


def test_theme_combo_popups_show_all_short_lists_and_a_useful_long_window(qapp):
    apply_easyqc_theme(qapp)
    short_combo = QComboBox()
    short_combo.addItems(
        (
            "按 easyqcid 合并列",
            "追加行",
            "替换现有名单",
        )
    )
    long_combo = QComboBox()
    long_combo.addItems(tuple(f"Option {index}" for index in range(12)))

    try:
        short_combo.show()
        long_combo.show()
        qapp.processEvents()

        short_row_height = max(
            short_combo.view().sizeHintForRow(0),
            short_combo.fontMetrics().lineSpacing(),
        )
        long_row_height = max(
            long_combo.view().sizeHintForRow(0),
            long_combo.fontMetrics().lineSpacing(),
        )
        longest_short_text = max(
            short_combo.fontMetrics().horizontalAdvance(short_combo.itemText(index))
            for index in range(short_combo.count())
        )

        assert short_combo.maxVisibleItems() == short_combo.count()
        assert short_combo.view().minimumHeight() >= (
            short_row_height * short_combo.count()
        )
        assert short_combo.view().minimumWidth() >= longest_short_text
        assert long_combo.maxVisibleItems() == COMBO_POPUP_MAX_VISIBLE_ITEMS
        assert long_combo.maxVisibleItems() >= COMBO_POPUP_MIN_VISIBLE_ITEMS
        assert long_combo.view().minimumHeight() >= (
            long_row_height * COMBO_POPUP_MIN_VISIBLE_ITEMS
        )
    finally:
        short_combo.close()
        long_combo.close()


def test_semantic_button_roles_are_properties_not_absolute_geometry(qapp):
    original_stylesheet = qapp.styleSheet()
    button = QPushButton("Open")
    root = QWidget()
    button.setParent(root)

    try:
        apply_easyqc_theme(qapp)
        set_button_role(button, "primary")

        assert button.property("role") == "primary"
        assert button.minimumHeight() == CONTROL_HEIGHT
        assert "position:" not in qapp.styleSheet()
        assert "QPushButton[role=\"primary\"]" in qapp.styleSheet()
    finally:
        qapp.setStyleSheet(original_stylesheet)


def test_reapplying_identical_theme_does_not_repolish_live_widgets(qapp):
    class StyleProbe(QWidget):
        def __init__(self):
            super().__init__()
            self.style_changes = 0

        def event(self, event):
            if event.type() == QEvent.Type.StyleChange:
                self.style_changes += 1
            return super().event(event)

    probe = StyleProbe()
    probe.show()
    apply_easyqc_theme(qapp)
    qapp.processEvents()
    after_first_apply = probe.style_changes

    apply_easyqc_theme(qapp)
    qapp.processEvents()

    assert probe.style_changes == after_first_apply
    probe.close()


def test_reapplying_theme_after_host_suffix_preserves_host_rules_without_duplication(
    qapp,
):
    class StyleProbe(QWidget):
        def __init__(self):
            super().__init__()
            self.style_changes = 0

        def event(self, event):
            if event.type() == QEvent.Type.StyleChange:
                self.style_changes += 1
            return super().event(event)

    original_stylesheet = qapp.styleSheet()
    original_theme = qapp.property("_easyqc_theme_stylesheet")
    host_prefix = "QWidget#hostPrefix { background: #abcdef; }"
    host_suffix = "QLabel#hostSuffix { color: #123456; }"

    try:
        qapp.setStyleSheet(host_prefix)
        apply_easyqc_theme(qapp)
        easyqc_theme = qapp.property("_easyqc_theme_stylesheet")
        assert qapp.styleSheet().count(
            "/* EasyQC managed theme: start */"
        ) == 1
        qapp.setStyleSheet(f"{qapp.styleSheet()}\n{host_suffix}")

        apply_easyqc_theme(qapp)

        assert qapp.styleSheet().count(easyqc_theme) == 1
        assert qapp.styleSheet().count(
            "/* EasyQC managed theme: start */"
        ) == 1
        assert host_prefix in qapp.styleSheet()
        assert host_suffix in qapp.styleSheet()

        stale_managed_theme = (
            "/* EasyQC managed theme: start */\n"
            "QWidget#staleManagedTheme { color: #fedcba; }\n"
            "/* EasyQC managed theme: end */"
        )
        qapp.setStyleSheet(
            f"{qapp.styleSheet()}\n{stale_managed_theme}"
        )
        apply_easyqc_theme(qapp)

        assert qapp.styleSheet().count(
            "/* EasyQC managed theme: start */"
        ) == 1
        assert "staleManagedTheme" not in qapp.styleSheet()
        assert host_prefix in qapp.styleSheet()
        assert host_suffix in qapp.styleSheet()

        probe = StyleProbe()
        probe.show()
        qapp.processEvents()
        before_stale_property = probe.style_changes
        qapp.setProperty(
            "_easyqc_theme_stylesheet",
            easyqc_theme[: len(easyqc_theme) // 2],
        )
        apply_easyqc_theme(qapp)
        qapp.processEvents()

        assert qapp.styleSheet().count(easyqc_theme) == 1
        assert qapp.styleSheet().count(
            "/* EasyQC managed theme: start */"
        ) == 1
        assert host_prefix in qapp.styleSheet()
        assert host_suffix in qapp.styleSheet()
        assert probe.style_changes == before_stale_property
        probe.close()
    finally:
        qapp.setStyleSheet(original_stylesheet)
        qapp.setProperty("_easyqc_theme_stylesheet", original_theme)
