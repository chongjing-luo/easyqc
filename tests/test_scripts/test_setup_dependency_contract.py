from __future__ import annotations

import ast
import re
from pathlib import Path


def _declared_import_names(requirements_text: str) -> set[str]:
    names: set[str] = set()
    for raw_line in requirements_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        distribution = re.split(r"[<>=!~\[]", line, maxsplit=1)[0]
        if distribution.casefold() == "pyside6-essentials":
            names.add("PySide6")
        else:
            names.add(distribution.replace("-", "_"))
    return names


def _verified_import_names(setup_text: str) -> set[str]:
    marker = "# 验证主要依赖"
    block = setup_text.split(marker, maxsplit=1)[1]
    python_source = block.split('python -c "', maxsplit=1)[1].split(
        '"; then',
        maxsplit=1,
    )[0]
    tree = ast.parse(python_source)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".", maxsplit=1)[0])
    return names


def test_setup_verifies_only_declared_python_runtime_dependencies(
    easyqc_root: Path,
) -> None:
    declared = _declared_import_names(
        (easyqc_root / "requirements.txt").read_text(encoding="utf-8")
    )
    verified = _verified_import_names(
        (easyqc_root / "setup.sh").read_text(encoding="utf-8")
    )

    assert verified <= declared
    assert {"numpy", "pandas", "PySide6"} <= verified
