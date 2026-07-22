from __future__ import annotations

import ast
from pathlib import Path


PROJECT_MODULE_ROOTS = {
    "core",
    "gui",
    "gui_qt",
    "models",
    "packaging_tools",
    "scripts",
    "utils",
}


def _internal_imports(path: Path) -> tuple[tuple[int, str], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules = (node.module or "",)
        else:
            continue
        found.extend(
            (node.lineno, module)
            for module in modules
            if module.split(".", 1)[0] in PROJECT_MODULE_ROOTS
        )
    return tuple(found)


def test_model_implementation_modules_import_no_project_internal_module(
    easyqc_root: Path,
) -> None:
    violations: dict[str, tuple[tuple[int, str], ...]] = {}
    for path in sorted((easyqc_root / "models").glob("*.py")):
        if path.name == "__init__.py":
            continue
        imports = _internal_imports(path)
        if imports:
            violations[path.relative_to(easyqc_root).as_posix()] = imports

    assert violations == {}


def test_core_never_imports_a_gui_layer(easyqc_root: Path) -> None:
    violations: dict[str, tuple[tuple[int, str], ...]] = {}
    for path in sorted((easyqc_root / "core").glob("*.py")):
        gui_imports = tuple(
            (line, module)
            for line, module in _internal_imports(path)
            if module.split(".", 1)[0] in {"gui", "gui_qt"}
        )
        if gui_imports:
            violations[path.relative_to(easyqc_root).as_posix()] = gui_imports

    assert violations == {}
