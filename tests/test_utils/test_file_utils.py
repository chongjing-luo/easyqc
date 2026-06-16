import json

from utils.file_utils import FileUtils


def test_read_file_returns_content_and_none_for_missing_file(tmp_path) -> None:
    path = tmp_path / "hello.txt"
    path.write_text("hello", encoding="utf-8")

    file_utils = FileUtils()

    assert file_utils.read_file(str(path)) == "hello"
    assert file_utils.read_file(str(tmp_path / "missing.txt")) is None


def test_copy_file_copies_named_files_and_subdirs(tmp_path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "sub").mkdir(parents=True)
    (source / "sub" / "a.txt").write_text("A", encoding="utf-8")

    copied = FileUtils().copy_file(str(source), str(target), "a.txt", subdir_list=["sub"])

    assert copied == [str(target / "sub" / "a.txt")]
    assert (target / "sub" / "a.txt").read_text(encoding="utf-8") == "A"


def test_safe_json_save_uses_atomic_replace(tmp_path) -> None:
    path = tmp_path / "rating.json"
    path.write_text('{"old": true}', encoding="utf-8")

    FileUtils.safe_json_save(path, {"new": True})

    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}
    assert not list(tmp_path.glob("*.tmp.*"))


def test_atomic_write_keeps_original_when_replace_fails(monkeypatch, tmp_path) -> None:
    path = tmp_path / "rating.json"
    path.write_text('{"old": true}', encoding="utf-8")

    def fail_replace(*args, **kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr("utils.file_utils.os.replace", fail_replace)

    try:
        FileUtils.safe_json_save(path, {"new": True})
    except OSError:
        pass

    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}
    assert not list(tmp_path.glob(".*.tmp.*"))


def test_safe_json_load_reads_json(tmp_path) -> None:
    path = tmp_path / "rating.json"
    path.write_text('{"ok": true}', encoding="utf-8")

    assert FileUtils.safe_json_load(path) == {"ok": True}


# AC-6 regression: atomic persistence must never leave a truncated/half-written
# file. BUG-3 was already fixed before this test landed (atomic_write shipped in
# phase 0), but the contract is load-bearing for settings/registry/rating/CSV
# writes, so it gets an explicit gate.


def test_atomic_write_produces_complete_new_content_and_no_partial_file(tmp_path) -> None:
    """On success the target is byte-for-byte the new content and no temp
    fragment remains in the directory."""
    path = tmp_path / "settings.json"
    path.write_text('{"old": true}', encoding="utf-8")
    new_content = '{"schema_version": 1, "qcmodule": {}}'

    FileUtils.atomic_write(path, new_content)

    assert path.read_text(encoding="utf-8") == new_content
    # no temp fragments
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_atomic_write_never_leaves_truncated_file_on_failure(tmp_path) -> None:
    """AC-6: if os.replace fails (simulating crash/power-loss at the rename
    step), the target file must be either the pre-save content or absent —
    never a truncated half-write. The temp file must also be cleaned up."""
    path = tmp_path / "projects.json"
    original = '{"projects": {"X": "/x"}, "last_project": "X"}'
    path.write_text(original, encoding="utf-8")

    import utils.file_utils as fu

    def boom(*args, **kwargs):
        raise OSError("disk full during replace")

    # simulate failure at the final rename step
    monkeypatch_target = fu.os
    original_replace = monkeypatch_target.replace
    monkeypatch_target.replace = boom  # type: ignore[method-assign]

    raised = False
    try:
        FileUtils.atomic_write(path, '{"projects": {}, "last_project": null}')
    except OSError:
        raised = True
    finally:
        monkeypatch_target.replace = original_replace  # type: ignore[method-assign]

    assert raised, "atomic_write should propagate the OSError"
    # target is intact (old content), NOT truncated
    assert path.read_text(encoding="utf-8") == original
    # no leftover temp fragment
    assert [p.name for p in tmp_path.iterdir()] == ["projects.json"]


def test_atomic_write_to_missing_parent_creates_dirs(tmp_path) -> None:
    """atomic_write must mkdir parents (ratings live in nested
    RatingFiles/<module>/<rater>/ that may not exist yet)."""
    path = tmp_path / "RatingFiles" / "Anat" / "r1" / "rating.json"
    FileUtils.atomic_write(path, '{"ok": true}')

    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}
