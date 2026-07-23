from __future__ import annotations

import pytest
import pandas as pd
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.table_view_service import TableViewService
from gui_qt.columns_dialog import ColumnsDialog
from gui_qt.columns_panel import ColumnsPanel
from gui_qt.derived_column_dialog import DerivedColumnDialog
from gui_qt.filter_dialog import FilterDialog
from gui_qt.i18n import (
    DEFAULT_LANGUAGE,
    LanguageController,
    SUPPORTED_LANGUAGES,
)
from gui_qt.qc_results_page import QtQcResultsPage
from gui_qt.sort_dialog import SortDialog
from gui_qt.table_workspace import QtTableWorkspace
from models.table_view_state import (
    ColumnViewState,
    FilterCondition,
    FilterExpression,
    FilterGroup,
    SortRule,
)


def _settings(tmp_path, name: str = "language.ini") -> QSettings:
    return QSettings(str(tmp_path / name), QSettings.IniFormat)


def test_language_controller_defaults_to_chinese_and_exposes_two_languages(tmp_path):
    controller = LanguageController(settings=_settings(tmp_path))

    assert controller.language == DEFAULT_LANGUAGE == "zh_CN"
    assert controller.supported_languages == SUPPORTED_LANGUAGES == ("zh_CN", "en")
    assert controller.tr("nav.projects") == "项目选择"


def test_language_change_is_immediate_signalled_once_and_persisted(tmp_path, qtbot):
    settings = _settings(tmp_path)
    controller = LanguageController(settings=settings)
    changed = []
    controller.languageChanged.connect(changed.append)

    controller.set_language("en")
    controller.set_language("en")

    assert controller.language == "en"
    assert controller.tr("nav.projects") == "Project selection"
    assert changed == ["en"]
    settings.sync()
    assert LanguageController(settings=_settings(tmp_path)).language == "en"


def test_invalid_language_fails_at_public_api_but_settings_boundary_is_safe(tmp_path):
    settings = _settings(tmp_path)
    settings.setValue("ui/language", "xx_INVALID")
    settings.sync()

    controller = LanguageController(settings=_settings(tmp_path))

    assert controller.language == "zh_CN"
    with pytest.raises(ValueError, match="Unsupported EasyQC UI language"):
        controller.set_language("xx_INVALID")


def test_translation_formatting_is_strict_and_keeps_business_identifiers(tmp_path):
    controller = LanguageController(settings=_settings(tmp_path), language="en")

    assert (
        controller.tr("status.project_loaded", project_name="MY_PROJECT")
        == "Project loaded: MY_PROJECT"
    )
    assert controller.tr("field.ezqcid") == "ezqcid"
    with pytest.raises(KeyError):
        controller.tr("status.project_loaded")
    with pytest.raises(KeyError):
        controller.tr("missing.catalog.key")


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("是", "Yes"),
        ("否", "No"),
        ("数值", "Number"),
        ("EasyQC 功能导航", "EasyQC feature navigation"),
        ("界面语言", "Interface language"),
        ("筛选组", "Filter group"),
        ("配置任务失败", "Configuration task failed"),
        ("正在取消导出…", "Cancelling export…"),
        (
            "列名 'visit age' 包含空格或标点，不能直接用于表达式",
            "Column name 'visit age' contains spaces or punctuation and "
            "cannot be used directly in an expression.",
        ),
        (
            "已读取 12 条、5 列；尚未写入",
            "Read 12 records and 5 columns; nothing has been written yet.",
        ),
        (
            "匹配 12 · 新增 3 · 冲突 1",
            "Matched 12 · new 3 · conflicts 1",
        ),
        (
            "质控模块不存在: 常量设置",
            "QC module does not exist: 常量设置",
        ),
    ),
)
def test_compatibility_translations_cover_dynamic_ui_without_rewriting_business_text(
    tmp_path,
    source,
    expected,
):
    controller = LanguageController(settings=_settings(tmp_path), language="en")

    assert controller.translate_source(source) == expected


def test_registered_widget_tree_switches_without_reconstruction(qtbot, tmp_path):
    controller = LanguageController(settings=_settings(tmp_path))
    root = QWidget()
    root.setObjectName("localizedRoot")
    qtbot.addWidget(root)
    layout = QVBoxLayout(root)
    label = QLabel("项目选择", root)
    label.setAccessibleName("项目状态")
    button = QPushButton("打开项目", root)
    layout.addWidget(label)
    layout.addWidget(button)
    controller.register_root(root)
    root.show()

    identity = id(label)
    controller.set_language("en")

    assert id(label) == identity
    assert label.text() == "Project selection"
    assert label.accessibleName() == "Project status"
    assert button.text() == "Open project"

    label.setText("正在加载项目…")
    controller.localize_widget_tree(root)
    assert label.text() == "Loading project…"

    controller.set_language("zh_CN")
    assert label.text() == "正在加载项目…"
    assert button.text() == "打开项目"


def test_user_owned_text_is_not_translated_when_it_matches_ui_vocabulary(
    qtbot,
    tmp_path,
):
    controller = LanguageController(settings=_settings(tmp_path))
    root = QWidget()
    qtbot.addWidget(root)
    layout = QVBoxLayout(root)
    module_name = QLabel("质控模块", root)
    module_name.setProperty(
        "_easyqc_user_text_properties",
        ("text",),
    )
    column_selector = QComboBox(root)
    column_selector.addItem("评分", "评分")
    tagged_value = QListWidget(root)
    tagged_value.addItem("常量设置")
    tagged_value.setProperty(
        "_easyqc_user_text_properties",
        ("items",),
    )
    payload_value = QListWidget(root)
    payload_value.addItem("常量设置")
    payload_item = payload_value.item(0)
    payload_item.setData(Qt.ItemDataRole.UserRole, "常量设置")
    payload_item.setData(Qt.ItemDataRole.AccessibleTextRole, "常量设置")
    open_button = QPushButton("打开项目", root)
    for widget in (
        module_name,
        column_selector,
        tagged_value,
        payload_value,
        open_button,
    ):
        layout.addWidget(widget)
    controller.register_root(root)

    controller.set_language("en")

    assert module_name.text() == "质控模块"
    assert column_selector.itemText(0) == "评分"
    assert tagged_value.item(0).text() == "常量设置"
    assert payload_item.text() == "常量设置"
    assert (
        payload_item.data(Qt.ItemDataRole.AccessibleTextRole)
        == "常量设置"
    )
    assert open_button.text() == "Open project"


def test_column_item_accessible_text_switches_with_visible_pinned_label(
    qtbot,
    tmp_path,
):
    controller = LanguageController(settings=_settings(tmp_path))
    panel = ColumnsPanel(
        ColumnViewState(
            order=("ezqcid", "site"),
            pinned=("ezqcid",),
        )
    )
    qtbot.addWidget(panel)
    controller.register_root(panel)

    controller.set_language("en")

    pinned_item = panel.list_widget.item(0)
    assert pinned_item.text() == "ezqcid   · pinned"
    assert (
        pinned_item.data(Qt.ItemDataRole.AccessibleTextRole)
        == "ezqcid   · pinned"
    )


def test_pinning_a_column_after_switching_to_english_localizes_visible_and_accessible_text(
    qtbot,
    tmp_path,
):
    controller = LanguageController(settings=_settings(tmp_path))
    panel = ColumnsPanel(
        ColumnViewState(
            order=("ezqcid", "site"),
            pinned=("ezqcid",),
        ),
        language=controller,
    )
    qtbot.addWidget(panel)
    controller.register_root(panel)
    controller.set_language("en")
    site = panel.list_widget.item(1)
    panel.list_widget.setCurrentItem(site)

    assert panel.pin_selected()

    pinned_site = panel.list_widget.item(1)
    assert pinned_site.text() == "site   · pinned"
    assert (
        pinned_site.data(Qt.ItemDataRole.AccessibleTextRole)
        == "site   · pinned"
    )


def test_unknown_mixed_text_is_left_intact_instead_of_rewriting_business_values(
    tmp_path,
):
    controller = LanguageController(settings=_settings(tmp_path), language="en")
    source = "未登记的自定义列: 常量设置"

    assert controller.translate_source(source) == source


def test_pinned_column_template_preserves_multiline_business_name(tmp_path):
    controller = LanguageController(
        settings=_settings(tmp_path),
        language="en",
    )

    for column in ("line\nbreak", "site ", "", "   "):
        assert (
            controller.translate_source(f"{column}   · 已固定")
            == f"{column}   · pinned"
        )


def test_language_display_names_are_native_and_stable(tmp_path):
    controller = LanguageController(settings=_settings(tmp_path))

    assert controller.language_display_name("zh_CN") == "中文"
    assert controller.language_display_name("en") == "English"
    with pytest.raises(ValueError):
        controller.language_display_name("fr")


def _widget_presentation_texts(root: QWidget) -> tuple[str, ...]:
    texts: list[str] = []
    for obj in (root, *root.findChildren(QWidget)):
        for getter_name in (
            "accessibleName",
            "accessibleDescription",
            "toolTip",
            "windowTitle",
        ):
            text = getattr(obj, getter_name)()
            if text:
                texts.append(text)
        if isinstance(obj, QLabel):
            texts.append(obj.text())
        elif isinstance(obj, QAbstractButton):
            texts.append(obj.text())
        if isinstance(obj, QGroupBox):
            texts.append(obj.title())
        if isinstance(obj, (QLineEdit, QPlainTextEdit, QTextEdit)):
            texts.append(obj.placeholderText())
        if isinstance(obj, QComboBox):
            texts.extend(obj.itemText(index) for index in range(obj.count()))
    return tuple(text for text in texts if text)


def test_open_table_dialogs_switch_language_without_losing_drafts(qtbot, tmp_path):
    source = pd.DataFrame(
        {
            "ezqcid": ["A", "B"],
            "site": ["north", "south"],
            "age": [29, 31],
            "approved": [True, False],
            "评分": ["合格", "复核"],
        }
    )
    profiles = TableViewService(source).profiles
    columns = ColumnViewState(
        order=("ezqcid", "site", "age", "approved", "评分"),
        pinned=("ezqcid",),
    )
    filter_expression = FilterExpression(
        groups=(
            FilterGroup(
                group_id="group-1",
                join="all",
                conditions=(
                    FilterCondition(
                        "site",
                        "==",
                        "north",
                        "condition-1",
                    ),
                ),
            ),
        )
    )
    controller = LanguageController(settings=_settings(tmp_path))
    filter_dialog = FilterDialog(profiles, filter_expression)
    sort_dialog = SortDialog(columns.order, (SortRule("评分", True),))
    columns_dialog = ColumnsDialog(columns, columns)
    derived_dialog = DerivedColumnDialog(source, lambda name, _expression: name)
    derived_dialog.name_edit.setText("age_next")
    derived_dialog.expression_edit.setPlainText("age + 1")
    dialogs = (filter_dialog, sort_dialog, columns_dialog, derived_dialog)
    for dialog in dialogs:
        qtbot.addWidget(dialog)
        controller.register_root(dialog)
        dialog.show()

    controller.set_language("en")

    untranslated = {
        text
        for dialog in dialogs
        for text in _widget_presentation_texts(dialog)
        if any("\u3400" <= char <= "\u9fff" for char in text)
        and text not in {"评分", "合格", "复核"}
    }
    assert untranslated == set()
    assert filter_dialog.editor.expression() == filter_expression
    assert sort_dialog.editor.rules() == (SortRule("评分", True),)
    assert sort_dialog.editor.rule_rows[0].column_combo.currentText() == "评分"
    filter_column_combo = filter_dialog.editor.condition_rows[0].column_combo
    assert filter_column_combo.itemText(filter_column_combo.findData("评分")) == "评分"
    assert any(
        derived_dialog.columns_list.item(row).text() == "评分"
        for row in range(derived_dialog.columns_list.count())
    )
    assert columns_dialog.editor.state() == columns
    assert derived_dialog.name_edit.text() == "age_next"
    assert derived_dialog.expression_edit.toPlainText() == "age + 1"
    assert "Sort priority" in _widget_presentation_texts(sort_dialog)


def test_table_accessible_descriptions_switch_to_english_without_rebuilding_pages(
    qtbot,
    tmp_path,
):
    controller = LanguageController(settings=_settings(tmp_path))
    source = pd.DataFrame(
        {
            "ezqcid": ["A", "B"],
            "site": ["north", "south"],
        }
    )
    workspace = QtTableWorkspace(source, language=controller)
    results = QtQcResultsPage(
        source,
        refresh_callback=lambda: True,
        language=controller,
    )
    qtbot.addWidget(workspace)
    qtbot.addWidget(results)
    controller.register_root(workspace)
    controller.register_root(results)
    pre_qc_table = workspace.table_view
    results_table = results.table_workspace.table_view

    controller.set_language("en")

    assert workspace.table_view is pre_qc_table
    assert results.table_workspace.table_view is results_table
    assert (
        pre_qc_table.accessibleDescription()
        == "Read-only list. Filters and sorting apply to the complete result."
    )
    assert (
        results_table.accessibleDescription()
        == "Read-only QC results. Filters, sorting and column settings apply "
        "to the complete result."
    )
    assert not {
        text
        for root in (workspace, results)
        for text in _widget_presentation_texts(root)
        if any("\u3400" <= char <= "\u9fff" for char in text)
    }
