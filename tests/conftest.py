from __future__ import annotations

import builtins
import io
import os
import shutil
from pathlib import Path

# Qt tests must be safe to run individually on headless CI/workstations. This
# test-only default is applied before pytest-qt creates QApplication; product
# launch never imports tests/conftest.py and therefore keeps the real platform.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


_EASYQC_ROOT = Path(__file__).resolve().parents[1]
_PROTECTED_RUNTIME_ROOTS = (
    _EASYQC_ROOT / "projects.json",
    _EASYQC_ROOT / "projects",
    _EASYQC_ROOT.parent / "easyqc_CCNPPEKI",
)


def _is_protected_runtime_path(candidate: object) -> bool:
    if isinstance(candidate, int):
        return False
    try:
        raw_path = os.fsdecode(os.fspath(candidate))
    except TypeError:
        return False

    absolute = Path(os.path.abspath(raw_path))
    canonical = Path(os.path.realpath(raw_path))
    return any(
        path == root or root in path.parents
        for path in (absolute, canonical)
        for root in _PROTECTED_RUNTIME_ROOTS
    )


def _reject_protected_runtime_path(operation: str, *candidates: object) -> None:
    for candidate in candidates:
        if _is_protected_runtime_path(candidate):
            raise RuntimeError(
                f"pytest blocked {operation} on protected EasyQC runtime data: "
                f"{os.fspath(candidate)}"
            )


@pytest.fixture(scope="session", autouse=True)
def protect_real_runtime_data():
    """Fail before tests can read or mutate repository/user project state."""

    patcher = pytest.MonkeyPatch()

    original_builtin_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_listdir = os.listdir
    original_scandir = os.scandir
    path_accessor_type = type(Path._accessor)
    original_path_open = Path._accessor.open
    original_path_listdir = Path._accessor.listdir
    original_path_scandir = Path._accessor.scandir

    def guarded_builtin_open(file, *args, **kwargs):
        _reject_protected_runtime_path("open", file)
        return original_builtin_open(file, *args, **kwargs)

    def guarded_io_open(file, *args, **kwargs):
        _reject_protected_runtime_path("open", file)
        return original_io_open(file, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        _reject_protected_runtime_path("open", path)
        return original_os_open(path, flags, *args, **kwargs)

    def guarded_listdir(path="."):
        _reject_protected_runtime_path("directory traversal", path)
        return original_listdir(path)

    def guarded_scandir(path="."):
        _reject_protected_runtime_path("directory traversal", path)
        return original_scandir(path)

    def guarded_path_open(path, *args, **kwargs):
        _reject_protected_runtime_path("open", path)
        return original_path_open(path, *args, **kwargs)

    def guarded_path_listdir(path):
        _reject_protected_runtime_path("directory traversal", path)
        return original_path_listdir(path)

    def guarded_path_scandir(path):
        _reject_protected_runtime_path("directory traversal", path)
        return original_path_scandir(path)

    def guard_one_path(name, original):
        def guarded(path, *args, **kwargs):
            _reject_protected_runtime_path(name, path)
            return original(path, *args, **kwargs)

        return guarded

    def guard_two_paths(name, original):
        def guarded(source, destination, *args, **kwargs):
            _reject_protected_runtime_path(name, source, destination)
            return original(source, destination, *args, **kwargs)

        return guarded

    patcher.setattr(builtins, "open", guarded_builtin_open)
    patcher.setattr(io, "open", guarded_io_open)
    patcher.setattr(os, "open", guarded_os_open)
    patcher.setattr(os, "listdir", guarded_listdir)
    patcher.setattr(os, "scandir", guarded_scandir)
    patcher.setattr(
        path_accessor_type,
        "open",
        staticmethod(guarded_path_open),
    )
    patcher.setattr(
        path_accessor_type,
        "listdir",
        staticmethod(guarded_path_listdir),
    )
    patcher.setattr(
        path_accessor_type,
        "scandir",
        staticmethod(guarded_path_scandir),
    )

    for name in (
        "chmod",
        "chown",
        "lchown",
        "makedirs",
        "mkdir",
        "remove",
        "rmdir",
        "truncate",
        "unlink",
        "utime",
    ):
        original = getattr(os, name, None)
        if original is not None:
            patcher.setattr(os, name, guard_one_path(name, original))

    for name in ("link", "rename", "replace", "symlink"):
        original = getattr(os, name, None)
        if original is not None:
            patcher.setattr(os, name, guard_two_paths(name, original))

    for name in ("chmod", "mkdir", "rmdir", "unlink"):
        original = getattr(Path._accessor, name, None)
        if original is not None:
            patcher.setattr(
                path_accessor_type,
                name,
                staticmethod(guard_one_path(name, original)),
            )

    for name in ("link", "rename", "replace", "symlink"):
        original = getattr(Path._accessor, name, None)
        if original is not None:
            patcher.setattr(
                path_accessor_type,
                name,
                staticmethod(guard_two_paths(name, original)),
            )

    yield
    patcher.undo()


@pytest.fixture
def easyqc_root() -> Path:
    return _EASYQC_ROOT


@pytest.fixture
def legacy_easyqc_root(easyqc_root: Path) -> Path:
    return easyqc_root.parent / "easyqc_back"


@pytest.fixture
def fixtures_dir(easyqc_root: Path) -> Path:
    return easyqc_root / "tests" / "fixtures"


@pytest.fixture
def sample_project_dir(tmp_path: Path, fixtures_dir: Path) -> Path:
    project_dir = tmp_path / "easyqc_SAMPLE"
    table_dir = project_dir / "Table"
    rating_dir = project_dir / "RatingFiles" / "example" / "rater1"

    table_dir.mkdir(parents=True)
    rating_dir.mkdir(parents=True)

    shutil.copy2(fixtures_dir / "sample_settings.json", project_dir / "settings_SAMPLE.json")
    shutil.copy2(fixtures_dir / "sample_ezqc_all.csv", table_dir / "ezqc_all.csv")

    source_rating = (
        fixtures_dir
        / "sample_ratings"
        / "example"
        / "rater1"
        / "example._.SUB001._.rater1._.Good._.True.json"
    )
    shutil.copy2(source_rating, rating_dir / source_rating.name)

    return project_dir


@pytest.fixture
def ccnppeki_compat_project_dir(tmp_path: Path, fixtures_dir: Path) -> Path:
    source_dir = fixtures_dir / "ccnppeki_compat" / "easyqc_CCNPPEKI_COMPAT"
    project_dir = tmp_path / "easyqc_CCNPPEKI_COMPAT"
    shutil.copytree(source_dir, project_dir)
    return project_dir
