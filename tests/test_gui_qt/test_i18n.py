from __future__ import annotations

import pytest
import pandas as pd
from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence
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

from core.formula_engine import FormulaEngine
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
    assert controller.tr("field.easyqcid") == "easyqcid"
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
        ("删除行", "Delete rows"),
        ("删除列", "Delete columns"),
        ("按条件删除行", "Delete rows by condition"),
        ("删除匹配行", "Delete matching rows"),
        ("至少添加一个删除条件", "Add at least one deletion condition."),
        ("site   · 受保护", "site   · protected"),
        ("删除所选列", "Delete checked columns"),
        (
            "按条件删除质控前名单行",
            "Delete Pre-QC list rows by condition",
        ),
        (
            "选择删除质控前名单列",
            "Select Pre-QC list columns to delete",
        ),
        (
            "已从质控前名单删除 2 行；评分记录已保留",
            "Deleted 2 Pre-QC list rows; rating records were retained.",
        ),
        (
            "已从质控前名单删除 2 列：site、age；评分记录已保留",
            "Deleted 2 Pre-QC list columns: site、age; "
            "rating records were retained.",
        ),
        (
            "将从质控前名单删除列“site”。\n"
            "现有评分记录不会被删除。是否继续？",
            "Delete column “site” from the Pre-QC list.\n"
            "Existing rating records will be retained. Continue?",
        ),
        (
            "将从质控前名单删除 2 列：site、age。\n"
            "现有评分记录不会被删除。是否继续？",
            "Delete 2 columns from the Pre-QC list: site、age.\n"
            "Existing rating records will be retained. Continue?",
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
            order=("easyqcid", "site"),
            pinned=("easyqcid",),
        )
    )
    qtbot.addWidget(panel)
    controller.register_root(panel)

    controller.set_language("en")

    pinned_item = panel.list_widget.item(0)
    assert pinned_item.text() == "easyqcid   · pinned"
    assert (
        pinned_item.data(Qt.ItemDataRole.AccessibleTextRole)
        == "easyqcid   · pinned"
    )


def test_column_bulk_visibility_buttons_switch_language_immediately(
    qtbot,
    tmp_path,
):
    controller = LanguageController(settings=_settings(tmp_path))
    panel = ColumnsPanel(
        ColumnViewState(
            order=("easyqcid", "site"),
            pinned=("easyqcid",),
        )
    )
    qtbot.addWidget(panel)
    controller.register_root(panel)

    assert panel.select_all_button.text() == "全选"
    assert panel.deselect_all_button.text() == "取消全选"

    controller.set_language("en")

    assert panel.select_all_button.text() == "Select all"
    assert panel.deselect_all_button.text() == "Deselect all"


def test_pinning_a_column_after_switching_to_english_localizes_visible_and_accessible_text(
    qtbot,
    tmp_path,
):
    controller = LanguageController(settings=_settings(tmp_path))
    panel = ColumnsPanel(
        ColumnViewState(
            order=("easyqcid", "site"),
            pinned=("easyqcid",),
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
            "easyqcid": ["A", "B"],
            "site": ["north", "south"],
            "age": [29, 31],
            "approved": [True, False],
            "评分": ["合格", "复核"],
        }
    )
    profiles = TableViewService(source).profiles
    columns = ColumnViewState(
        order=("easyqcid", "site", "age", "approved", "评分"),
        pinned=("easyqcid",),
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
    derived_dialog = DerivedColumnDialog(
        source,
        lambda formula_request: formula_request.name,
    )
    derived_dialog.name_edit.setText("age next")
    derived_dialog.editor.set_formula(
        'IF([评分] = "合格", [age] + 1, [age])'
    )
    derived_dialog.editor.quick_panel.set_template("concatenate")
    quick_panel = derived_dialog.editor.quick_panel
    quick_panel.concatenate_left_combo.setCurrentIndex(
        quick_panel.concatenate_left_combo.findData("评分")
    )
    quick_panel.concatenate_separator_edit.setText("·")
    quick_panel.concatenate_right_combo.setCurrentIndex(
        quick_panel.concatenate_right_combo.findData("site")
    )
    derived_dialog.editor.tabs.setCurrentIndex(1)
    derived_dialog.editor.column_combo.setCurrentIndex(
        derived_dialog.editor.column_combo.findData("评分")
    )
    derived_dialog.editor.function_combo.setCurrentIndex(
        derived_dialog.editor.function_combo.findData("VALUE")
    )
    assert derived_dialog.preview()
    formula_before = derived_dialog.editor.formula()
    preview_before = tuple(
        tuple(
            derived_dialog.preview_table.item(row, column).text()
            for column in range(derived_dialog.preview_table.columnCount())
        )
        for row in range(derived_dialog.preview_table.rowCount())
    )
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
    assert derived_dialog.editor.column_combo.findData("评分") >= 0
    assert columns_dialog.editor.state() == columns
    assert derived_dialog.name_edit.text() == "age next"
    assert derived_dialog.editor.formula() == formula_before
    assert derived_dialog.editor.tabs.currentIndex() == 1
    assert quick_panel.template_combo.currentData() == "concatenate"
    assert quick_panel.concatenate_left_combo.currentData() == "评分"
    assert quick_panel.concatenate_separator_edit.text() == "·"
    assert quick_panel.concatenate_right_combo.currentData() == "site"
    assert derived_dialog.editor.column_combo.currentData() == "评分"
    assert derived_dialog.editor.function_combo.currentData() == "VALUE"
    assert preview_before == tuple(
        tuple(
            derived_dialog.preview_table.item(row, column).text()
            for column in range(derived_dialog.preview_table.columnCount())
        )
        for row in range(derived_dialog.preview_table.rowCount())
    )
    assert derived_dialog.preview_table.horizontalHeaderItem(1).text() == "评分"
    assert derived_dialog.preview_table.horizontalHeaderItem(4).text() == "Error"
    assert derived_dialog.editor.tabs.tabText(0) == "Quick templates"
    assert derived_dialog.editor.tabs.tabText(1) == "Advanced formula"
    value_spec = next(
        spec
        for spec in FormulaEngine.function_catalog()
        if spec.name == "VALUE"
    )
    assert derived_dialog.editor.function_description.text() == (
        value_spec.description_en
    )
    assert derived_dialog.editor.function_signature.text() == value_spec.signature
    assert derived_dialog.editor.function_example.text() == (
        f"Example: {value_spec.example}"
    )
    assert derived_dialog.editor.status_label.text() == (
        "Formula valid · 2 columns referenced"
    )
    assert "Sort priority" in _widget_presentation_texts(sort_dialog)

    quick_panel.set_template("fixed")
    quick_panel.fixed_type_combo.setCurrentIndex(
        quick_panel.fixed_type_combo.findData("integer")
    )
    quick_panel.fixed_value_edit.setText("not-an-integer")
    qtbot.mouseClick(quick_panel.generate_button, Qt.LeftButton)
    controller.localize_widget_tree(derived_dialog)
    assert derived_dialog.editor.status_label.text() == (
        "Fixed value must be an integer"
    )
    assert derived_dialog.editor.formula() == formula_before

    controller.set_language("zh_CN")
    assert derived_dialog.name_edit.text() == "age next"
    assert derived_dialog.editor.formula() == formula_before
    assert derived_dialog.editor.function_description.text() == (
        value_spec.description_zh
    )
    assert derived_dialog.preview_table.horizontalHeaderItem(1).text() == "评分"
    assert derived_dialog.preview_table.horizontalHeaderItem(4).text() == "错误"


def test_formula_row_errors_switch_to_english_without_translating_data(
    qtbot,
    tmp_path,
) -> None:
    controller = LanguageController(settings=_settings(tmp_path))
    source = pd.DataFrame(
        {
            "easyqcid": ["A"],
            "评分": ["无法转换"],
        }
    )
    dialog = DerivedColumnDialog(source, lambda request: request.name)
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("number")
    dialog.editor.set_formula("VALUE([评分])")
    assert dialog.preview()
    controller.register_root(dialog)
    dialog.show()

    controller.set_language("en")

    assert dialog.editor.formula() == "VALUE([评分])"
    assert dialog.preview_table.horizontalHeaderItem(1).text() == "评分"
    assert dialog.preview_table.item(0, 1).text() == "无法转换"
    assert dialog.preview_table.item(0, 3).text() == (
        "VALUE cannot convert the value to a number"
    )
    assert dialog.error_label.text() == (
        "Formula cannot process 1 row "
        "(index 0: VALUE cannot convert the value to a number)"
    )


def test_table_accessible_descriptions_switch_to_english_without_rebuilding_pages(
    qtbot,
    tmp_path,
    monkeypatch,
):
    def shortcut_text(_sequence, mode=QKeySequence.NativeText):
        return (
            "PORTABLE"
            if mode == QKeySequence.PortableText
            else "NATIVE"
        )

    monkeypatch.setattr(QKeySequence, "toString", shortcut_text)
    controller = LanguageController(settings=_settings(tmp_path))
    source = pd.DataFrame(
        {
            "easyqcid": ["A", "B"],
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
    assert workspace.filter_action.toolTip() == "Filter (PORTABLE)"
    assert (
        results.table_workspace.filter_action.toolTip()
        == "Filter (PORTABLE)"
    )
    assert not {
        text
        for root in (workspace, results)
        for text in _widget_presentation_texts(root)
        if any("\u3400" <= char <= "\u9fff" for char in text)
    }
