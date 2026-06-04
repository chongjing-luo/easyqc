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
