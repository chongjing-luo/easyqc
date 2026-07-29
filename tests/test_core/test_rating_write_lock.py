from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from core.rating_write_lock import (
    RatingWriteBusyError,
    RatingWriteLockError,
    rating_write_lock,
)


LOCK_FILENAME = ".easyqc-rating-write.lock"


def test_lock_lives_under_nearest_ratingfiles_ancestor_and_persists(
    tmp_path: Path,
) -> None:
    outer = tmp_path / "RatingFiles"
    inner = outer / "archive" / "RatingFiles"
    target = inner / "Anat" / "rater_1" / "Anat-rater_1-S01.json"

    with rating_write_lock(target) as lock_path:
        assert lock_path == inner / LOCK_FILENAME
        assert lock_path.is_file()

    assert lock_path.is_file()


def test_lock_falls_back_to_target_directory_without_ratingfiles(
    tmp_path: Path,
) -> None:
    target = tmp_path / "synthetic" / "rating.json"

    with rating_write_lock(target) as lock_path:
        assert lock_path == target.parent / LOCK_FILENAME

    assert lock_path.is_file()


def test_existing_root_mode_does_not_create_missing_ratingfiles(
    tmp_path: Path,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    target = rating_root / "Anat" / "r1" / "rating.json"

    with pytest.raises(RatingWriteLockError, match="must already exist"):
        with rating_write_lock(target, create_lock_root=False):
            pytest.fail("lock unexpectedly acquired for a missing root")

    assert not rating_root.exists()


def test_default_mode_still_creates_ratingfiles_for_normal_save(
    tmp_path: Path,
) -> None:
    rating_root = tmp_path / "RatingFiles"
    target = rating_root / "Anat" / "r1" / "rating.json"

    with rating_write_lock(target) as lock_path:
        assert lock_path == rating_root / LOCK_FILENAME

    assert rating_root.is_dir()
    assert lock_path.is_file()


def test_same_process_threads_serialize(tmp_path: Path) -> None:
    target = tmp_path / "RatingFiles" / "Anat" / "r1" / "rating.json"
    first_entered = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    second_entered = threading.Event()
    failures: list[BaseException] = []

    def first_writer() -> None:
        try:
            with rating_write_lock(target):
                first_entered.set()
                assert release_first.wait(timeout=5)
        except BaseException as exc:
            failures.append(exc)

    def second_writer() -> None:
        try:
            assert first_entered.wait(timeout=5)
            second_started.set()
            with rating_write_lock(target):
                second_entered.set()
        except BaseException as exc:
            failures.append(exc)

    first = threading.Thread(target=first_writer)
    second = threading.Thread(target=second_writer)
    first.start()
    second.start()

    assert second_started.wait(timeout=5)
    time.sleep(0.05)
    assert not second_entered.is_set()
    release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert failures == []
    assert second_entered.is_set()


def test_same_thread_can_reenter_project_lock(tmp_path: Path) -> None:
    target = tmp_path / "RatingFiles" / "Anat" / "r1" / "rating.json"

    with rating_write_lock(target) as outer:
        with rating_write_lock(target) as inner:
            assert inner == outer


def test_real_subprocess_contention_raises_typed_busy_error(
    easyqc_root: Path,
    tmp_path: Path,
) -> None:
    target = tmp_path / "RatingFiles" / "Anat" / "r1" / "rating.json"
    ready_path = tmp_path / "child-ready"
    code = """
import sys
from pathlib import Path
from core.rating_write_lock import rating_write_lock

with rating_write_lock(sys.argv[1]):
    Path(sys.argv[2]).write_text("READY", encoding="utf-8")
    sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code, str(target), str(ready_path)],
        cwd=easyqc_root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready_path.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready_path.exists(), f"child did not acquire lock; returncode={child.poll()}"
        with pytest.raises(RatingWriteBusyError, match="already being written"):
            with rating_write_lock(target):
                pytest.fail("contending process unexpectedly acquired rating lock")
    finally:
        if child.poll() is None and child.stdin is not None:
            try:
                child.stdin.write("release\n")
                child.stdin.flush()
            except BrokenPipeError:
                pass
        try:
            stdout, stderr = child.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate(timeout=5)
            pytest.fail(f"lock-holder subprocess did not exit; stdout={stdout!r} stderr={stderr!r}")
        assert child.returncode == 0, f"stdout={stdout!r} stderr={stderr!r}"

    with rating_write_lock(target):
        pass
