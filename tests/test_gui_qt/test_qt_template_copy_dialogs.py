from __future__ import annotations

from PySide6.QtCore import QSettings

from core.configuration_service import ConfigurationService
from core.project_service import ProjectService
from core.project_template_service import ProjectTemplateService
from core.table_service import TableService
from core.template_service import TemplateService
from gui_qt.i18n import LanguageController
from gui_qt.template_copy_dialogs import ModuleTemplateCopyDialog
from models.qcmodule import QCModule


def _template_module(name: str, label: str | None = None) -> QCModule:
    return QCModule(
        name=name,
        label=label or name,
        code="viewer {image_path}",
    )


def _services(tmp_path):
    templates = TemplateService(tmp_path / "install")
    configuration = ConfigurationService(
        ProjectService(tmp_path / "projects.json"),
        TableService(),
    )
    configuration.create_project("SAMPLE", tmp_path)
    language = LanguageController(
        settings=QSettings(str(tmp_path / "language.ini"), QSettings.IniFormat)
    )
    return templates, ProjectTemplateService(templates), configuration, language


def test_module_copy_dialog_suggests_free_name_on_collision_and_copies_after_confirm(
    qtbot,
    tmp_path,
) -> None:
    templates, copier, configuration, language = _services(tmp_path)
    source = templates.add_module(_template_module("AnatQC"), display_order=10)
    # 模板已按原名复制过一次，再次添加必然同名冲突
    copier.copy_module_to_project(source.module_id, configuration)
    assert [m.name for m in configuration.modules()] == ["example", "AnatQC"]

    dialog = ModuleTemplateCopyDialog(
        templates,
        copier,
        configuration,
        language,
    )
    qtbot.addWidget(dialog)

    dialog._copy()
    assert not dialog.result()
    assert dialog.copied_name is None
    assert dialog.editor.module_name.text() == "AnatQC_2"
    assert dialog.error_label.text() != ""
    assert [m.name for m in configuration.modules()] == ["example", "AnatQC"]

    dialog._copy()
    assert dialog.copied_name == "AnatQC_2"
    assert [m.name for m in configuration.modules()] == [
        "example",
        "AnatQC",
        "AnatQC_2",
    ]
    # 原模块与安装模板都未被改动
    assert next(
        m for m in configuration.modules() if m.name == "AnatQC"
    ).code == "viewer {image_path}"
    assert templates.module(source.module_id).module.name == "AnatQC"


def test_module_copy_dialog_copies_edited_name_and_label_directly(
    qtbot,
    tmp_path,
) -> None:
    templates, copier, configuration, language = _services(tmp_path)
    source = templates.add_module(
        _template_module("AnatQC", "模板标签"), display_order=10
    )

    dialog = ModuleTemplateCopyDialog(
        templates,
        copier,
        configuration,
        language,
    )
    qtbot.addWidget(dialog)
    dialog.editor.module_name.setText("AnatQC_r2")
    dialog.editor.module_label.setText("第二轮解剖质控")

    dialog._copy()

    assert dialog.copied_name == "AnatQC_r2"
    copied = next(
        m for m in configuration.modules() if m.name == "AnatQC_r2"
    )
    assert copied.label == "第二轮解剖质控"
    assert copied.code == "viewer {image_path}"
    assert templates.module(source.module_id).module.label == "模板标签"
