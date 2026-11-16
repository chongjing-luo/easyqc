"""Behaviour of the visual-polish layer: theme assets, navigation rail, QC header."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QListWidget, QMainWindow

from gui_qt.empty_state import EmptyStateOverlay
from gui_qt.i18n import LanguageController
from gui_qt.icons import GLYPHS, application_icon, brand_pixmap, render_glyph_image
from gui_qt.theme import (
    apply_easyqc_theme,
    build_theme_stylesheet,
    configure_application_identity,
    theme_asset_paths,
)
from tests.test_gui_qt.test_qt_product_shell import _window
from tests.test_gui_qt.test_qt_qc_workspace import (
    _controller_class,
    _module,
    _rating_dir,
    _workflow,
)


def _language(tmp_path: Path) -> LanguageController:
    return LanguageController(
        settings=QSettings(str(tmp_path / "polish-language.ini"), QSettings.IniFormat)
    )


def test_every_glyph_renders_visible_pixels(qapp):
    for name in GLYPHS:
        image = render_glyph_image(name, "#000000", 24)
        assert image.width() == image.height() == 24
        assert any(
            image.pixelColor(x, y).alpha() > 0
            for x in range(24)
            for y in range(24)
        ), name
    assert not brand_pixmap(32, dpr=1.0).isNull()
    assert {size.width() for size in application_icon().availableSizes()} >= {16, 32, 256}


def test_theme_indicator_assets_are_cached_pngs_and_need_no_svg_module(qapp):
    paths = theme_asset_paths()

    assert {"chevron-down", "check", "dot"} <= set(paths)
    for path in paths.values():
        source = Path(path)
        assert source.suffix == ".png"
        assert source.is_file()
        assert source.with_name(f"{source.stem}@2x.png").is_file()
    stylesheet = build_theme_stylesheet()
    assert "$" not in stylesheet
    assert "position:" not in stylesheet
    assert paths["check"] in stylesheet
    assert "PySide6.QtSvg" not in sys.modules


def test_application_identity_adds_window_icon_without_restyling(qapp):
    original_stylesheet = qapp.styleSheet()
    configure_application_identity(qapp)

    assert not qapp.windowIcon().isNull()
    assert qapp.styleSheet() == original_stylesheet


def test_collapsed_navigation_rail_still_switches_every_destination(
    qtbot,
    tmp_path,
) -> None:
    window, _services = _window(qtbot, tmp_path)
    assert not window.navigation_rail.isVisible()

    window.set_navigation_collapsed(True)

    assert window.navigation_rail.isVisible()
    assert not window.navigation.isVisible()
    assert not window.brand_mark_label.isVisible()
    assert len(window.navigation_rail_buttons) == window.navigation.count()
    for row, button in enumerate(window.navigation_rail_buttons):
        assert button.toolTip() == window.language.tr(window.NAVIGATION_KEYS[row])
        qtbot.mouseClick(button, Qt.LeftButton)
        assert window.navigation.currentRow() == row
        assert window.workspace_stack.currentIndex() == row
        assert button.isChecked()

    qtbot.mouseClick(window.navigation_rail_settings_button, Qt.LeftButton)
    assert window.workspace_stack.currentWidget() is window.cross_project_settings_page
    assert window.settings_button.isChecked()
    assert window.navigation_rail_settings_button.isChecked()

    window.set_navigation_collapsed(False)
    assert not window.navigation_rail.isVisible()
    window.navigation.setCurrentRow(window.results_page_index)
    assert not window.settings_button.isChecked()
    assert window.navigation_rail_buttons[window.results_page_index].isChecked()


def test_navigation_rail_tooltips_follow_language(qtbot, tmp_path) -> None:
    window, _services = _window(qtbot, tmp_path)

    window.language.set_language("en")

    assert [button.toolTip() for button in window.navigation_rail_buttons] == [
        "Project management",
        "QC list import",
        "Master QC list",
        "Constants",
        "QC modules",
        "QC results",
    ]
    assert window.product_tagline_label.text() == "QC WORKSPACE"


def test_qc_header_shows_item_position_progress_and_draft_state(
    qtbot,
    tmp_path,
) -> None:
    controller = _controller_class()(_workflow(tmp_path))
    qtbot.addWidget(controller)
    controller.resize(900, 760)
    controller.show()
    workspace = controller.workspace

    assert workspace.current_item_label.full_text() == "SUB001"
    assert workspace.session_meta_label.full_text() == (
        "Anatomical quality · rater1 · 第 1 / 2 项"
    )
    assert workspace.progress_label.full_text() == "已评 0 / 2"
    assert workspace.progress_bar.value() == 0
    assert workspace.session_state_label.text() == "未评"

    workspace.score_buttons["1"]["Good"].click()
    assert workspace.session_state_label.text() == "未保存"
    assert workspace.session_state_label.property("tone") == "warning"
    assert workspace.progress_label.full_text() == "已评 1 / 2"

    workspace.save_button.click()
    assert list(_rating_dir(tmp_path).glob("*.json"))
    assert workspace.session_state_label.text() == "已评"
    assert workspace.session_state_label.property("tone") == "success"

    workspace.next_button.click()
    assert workspace.current_item_label.full_text() == "SUB002"
    assert workspace.session_meta_label.full_text().endswith("第 2 / 2 项")
    assert workspace.progress_label.full_text() == "已评 1 / 2"
    assert workspace.session_state_label.text() == "未评"


def test_qc_header_keeps_identity_when_window_is_narrow(qtbot, tmp_path) -> None:
    subjects = __import__("pandas").DataFrame(
        {"easyqcid": ["CCNPPEK0001_01_anat_long_identity"], "image": ["a.nii"]}
    )
    controller = _controller_class()(_workflow(tmp_path, subjects=subjects))
    qtbot.addWidget(controller)
    controller.resize(560, 720)
    controller.show()
    qtbot.wait(0)
    workspace = controller.workspace

    assert workspace.current_item_label.toolTip() == "CCNPPEK0001_01_anat_long_identity"
    assert workspace.current_item_label.width() >= (
        workspace.session_meta_label.width()
    )
    assert controller.minimumSizeHint().width() <= 640


def test_positional_digit_keys_rate_single_score_module_but_not_notes(
    qtbot,
    tmp_path,
) -> None:
    controller = _controller_class()(_workflow(tmp_path))
    qtbot.addWidget(controller)
    controller.show()
    workspace = controller.workspace
    assert workspace.score_shortcut_keys == ("1", "2", "3")

    workspace.queue_table.setFocus()
    qtbot.waitUntil(workspace.queue_table.hasFocus)
    qtbot.keyClick(workspace.queue_table, Qt.Key.Key_2)
    assert workspace.score_buttons["1"]["Fair"].isChecked()
    assert workspace.workflow.current_module.scores["1"].value == "Fair"

    workspace.notes_edit.setFocus()
    qtbot.waitUntil(workspace.notes_edit.hasFocus)
    qtbot.keyClick(workspace.notes_edit, Qt.Key.Key_3)
    assert workspace.notes_edit.toPlainText() == "3"
    assert workspace.workflow.current_module.scores["1"].value == "Fair"
    workspace.workflow.discard_changes()


def test_numeric_options_use_their_own_digits(qtbot, tmp_path) -> None:
    module = _module()
    module["scores"]["1"]["num"] = "0,1,2,3,4"
    module["scores"]["1"]["num_"] = "0,1,2,3,4"
    controller = _controller_class()(_workflow(tmp_path, module=module))
    qtbot.addWidget(controller)
    controller.show()
    workspace = controller.workspace
    assert workspace.score_shortcut_keys == ("0", "1", "2", "3", "4")
    assert workspace.score_key_hint_label.full_text() == "键盘 0–4"

    workspace.queue_table.setFocus()
    qtbot.waitUntil(workspace.queue_table.hasFocus)
    qtbot.keyClick(workspace.queue_table, Qt.Key.Key_0)
    assert workspace.workflow.current_module.scores["1"].value == "0"
    workspace.workflow.discard_changes()


def test_digit_keys_are_disabled_for_multi_score_and_read_only_modules(
    qtbot,
    tmp_path,
) -> None:
    module = _module()
    module["scores"]["2"] = {
        "label": "Motion",
        "num": "Low,High",
        "num_": "Low,High",
        "value": None,
    }
    multi = _controller_class()(_workflow(tmp_path / "multi", module=module))
    qtbot.addWidget(multi)
    multi.show()
    assert multi.workspace.score_shortcut_actions == []
    assert multi.workspace.score_key_hint_label is None

    read_only = _controller_class()(
        _workflow(tmp_path / "readonly", module=_module(rater=None))
    )
    qtbot.addWidget(read_only)
    read_only.show()
    workspace = read_only.workspace
    assert workspace.session_state_label.text() == "只读"
    workspace.queue_table.setFocus()
    qtbot.waitUntil(workspace.queue_table.hasFocus)
    qtbot.keyClick(workspace.queue_table, Qt.Key.Key_1)
    assert workspace.workflow.current_module.scores["1"].value is None


def test_qc_action_tooltips_show_shortcuts_in_both_languages(qtbot, tmp_path) -> None:
    language = _language(tmp_path)
    controller = _controller_class()(_workflow(tmp_path), language=language)
    qtbot.addWidget(controller)
    controller.show()
    workspace = controller.workspace

    assert workspace.save_next_button.toolTip().startswith("保存并下一个 · ")
    if sys.platform != "darwin":
        assert workspace.save_next_button.toolTip() == "保存并下一个 · Ctrl+Enter"
        assert workspace.previous_button.toolTip() == "上一个 · Alt+←"
    language.set_language("en")
    assert workspace.next_button.toolTip().startswith("Next · ")
    assert workspace.progress_label.full_text() == "Rated 0 / 2"
    assert workspace.session_state_label.text() == "Not rated"
    assert "Press 1–3 to rate" in workspace.session_header.toolTip()
    assert workspace.score_key_hint_label.full_text() == "Keys 1–3"


def test_empty_state_overlay_follows_rows_and_language(qtbot, tmp_path) -> None:
    language = _language(tmp_path)
    window = QMainWindow()
    view = QListWidget(window)
    window.setCentralWidget(view)
    qtbot.addWidget(window)
    overlay = EmptyStateOverlay(
        view,
        glyph="folder",
        title_key="empty.projects.title",
        hint_key="empty.projects.hint",
        language=language,
    )
    window.show()

    assert overlay.isVisible()
    assert overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert overlay.geometry() == view.viewport().rect()
    view.addItem("SAMPLE")
    assert not overlay.isVisible()
    view.clear()
    assert overlay.isVisible()
    language.set_language("en")
    assert overlay.title == "No projects yet"
    assert overlay.accessibleName() == "No projects yet"


def test_theme_can_be_applied_before_any_window_exists(qapp, tmp_path):
    original_stylesheet = qapp.styleSheet()
    try:
        apply_easyqc_theme(qapp)
        assert "QListWidget#primaryNavigation::item:selected" in qapp.styleSheet()
        assert "QWidget#qcSessionHeader" in qapp.styleSheet()
    finally:
        qapp.setStyleSheet(original_stylesheet)


def test_theme_still_builds_when_no_asset_location_is_writable(qapp, monkeypatch):
    from gui_qt import theme as theme_module

    def unwritable(_directory):
        raise OSError("read-only")

    monkeypatch.setattr(theme_module, "_ASSET_CACHE", None)
    monkeypatch.setattr(theme_module, "_write_assets", unwritable)
    stylesheet = theme_module.build_theme_stylesheet()

    assert theme_module.theme_asset_paths() == {}
    assert 'image: url("")' in stylesheet
    assert "QPushButton[role=\"primary\"]" in stylesheet
    monkeypatch.setattr(theme_module, "_ASSET_CACHE", None)
