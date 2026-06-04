from pathlib import Path


def test_start_script_self_locates_easyqc_root(easyqc_root: Path) -> None:
    source = (easyqc_root / "start.sh").read_text(encoding="utf-8")

    assert "BASH_SOURCE[0]" in source
    assert 'cd "$SCRIPT_DIR"' in source
    assert 'python "$SCRIPT_DIR/easyqc.py" "$@"' in source
    assert "/home/ubuntu/Softwares/easyqc" not in source


def test_setup_generates_self_locating_start_script(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"' in source
    assert 'SCRIPT_DIR="\\$(cd "\\$(dirname "\\${BASH_SOURCE[0]}")" && pwd)"' in source
    assert 'python "\\$SCRIPT_DIR/easyqc.py" "\\$@"' in source
    assert "/home/ubuntu/Softwares/easyqc" not in source


def test_setup_supports_check_only_mode(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert "CHECK_ONLY=false" in source
    assert "-check|--check)" in source
    assert "check_environment || exit 1" in source
    assert "verify_installation || exit 1" in source
    assert "check_start_script || exit 1" in source
    assert "检查完成：Linux 主线环境和启动脚本可用" in source


def test_setup_uses_tuple_python_version_check(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert "python_version_at_least_310()" in source
    assert "sys.version_info >= (3, 10)" in source
    assert "python_version_at_least_310 python3.10" in source
    assert "python_version_at_least_310 python3" in source
    assert '[[ "$PYTHON_VERSION" > "3.10" ]]' not in source


def test_setup_detects_stale_copied_virtualenv_paths(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert "EXPECTED_PREFIX=" in source
    assert "ACTIVE_PREFIX=" in source
    assert "环境路径不匹配，需要重新创建" in source
    assert 'readlink -f "$ACTIVE_PREFIX"' in source
    assert 'readlink -f "$EXPECTED_PREFIX"' in source
    assert "PIP_SHEBANG=" in source
    assert "pip 入口脚本路径不匹配，需要重新创建" in source


def test_setup_verification_fails_loudly_on_missing_dependencies(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert "错误：tkinter 不可用" in source
    assert "错误：部分依赖验证失败" in source
    assert 'python -c "import tkinter; print' in source
    assert "|| echo \"⚠ 部分依赖验证失败\"" not in source
    assert "MPLCONFIGDIR" in source


def test_setup_verifies_only_required_dependencies(easyqc_root: Path) -> None:
    source = (easyqc_root / "setup.sh").read_text(encoding="utf-8")

    assert "验证主要依赖..." in source
    assert "所有依赖验证完成" in source


def test_requirements_and_readme_document_structured_table_transform(easyqc_root: Path) -> None:
    requirements = (easyqc_root / "requirements.txt").read_text(encoding="utf-8")
    readme = (easyqc_root / "README.md").read_text(encoding="utf-8")

    assert "scikit-learn>=1.0.0" in requirements
    assert "内置 JSON 结构化操作" in readme
    assert "easyqc_back/" in readme
    assert "不作为日常启动目标" in readme


def test_easyqc_back_is_marked_as_reference_only(easyqc_root: Path) -> None:
    deprecated = (easyqc_root.parent / "easyqc_back" / "DEPRECATED.md").read_text(encoding="utf-8")

    assert "old EasyQC implementation" in deprecated
    assert "Do not use this directory as the daily application entry point" in deprecated
    assert "Do not add features or bug fixes here" in deprecated
