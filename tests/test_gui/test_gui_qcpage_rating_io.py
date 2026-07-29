import json
from types import SimpleNamespace

import pytest
import pandas as pd

import core.rating_service as rating_service
from gui import gui_qcpage as gui_qcpage_module
from gui.qc_page import QCPageController
from gui.gui_qcpage import gui_qcpage


class _WatchMode:
    def __init__(self, value: bool = False) -> None:
        self.value = value

    def get(self) -> bool:
        return self.value

    def set(self, value: bool) -> None:
        self.value = value


class _ValueVar:
    def __init__(self) -> None:
        self.value = None

    def set(self, value) -> None:
        self.value = value


class _NotesText:
    def __init__(self) -> None:
        self.content = None

    def delete(self, *args) -> None:
        self.content = ""

    def insert(self, _index, content) -> None:
        self.content = content


def _module(score="Good", tag=True) -> dict:
    return {
        "name": "example",
        "label": "Current Label",
        "rater": "rater1",
        "ezqcid": "SUB001",
        "tags": {"1": {"label": "Visible artifact", "value": tag}},
        "scores": {
            "1": {
                "label": "Overall quality",
                "num": "Poor,Fair,Good",
                "num_": ["Poor", "Fair", "Good"],
                "value": score,
            }
        },
        "code": "current-code",
        "interper": "shell",
        "control": True,
        "select_filter": "current-filter",
        "showing": True,
        "code_exe": {"0": "current"},
        "time": None,
        "notes": None,
    }


def _page(tmp_path, module=None) -> gui_qcpage:
    module = module or _module()
    page = gui_qcpage()
    page.module_index = "1"
    page.module_name = "example"
    page.watch_mode = _WatchMode(False)
    page.watch_mode_ = False
    page.dt = SimpleNamespace()
    module_dir = tmp_path / "RatingFiles" / module["name"]
    rater = module.get("rater")
    if isinstance(rater, str) and rater.strip():
        module_dir /= rater.strip()
    page.dt.dir_module_rater = str(module_dir)
    page.dt.settings = {"qcmodule": {"1": module}}
    return page


def _rating_dir(tmp_path):
    return tmp_path / "RatingFiles" / "example" / "rater1"


def test_save_rating_writes_canonical_current_snapshot(tmp_path) -> None:
    page = _page(tmp_path)

    page.save_rating()

    new_file = _rating_dir(tmp_path) / "example-rater1-SUB001.json"
    assert new_file.exists()
    payload = json.loads(new_file.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["name"] == "example"
    assert payload["code"] == "current-code"
    assert payload["scores"]["1"]["value"] == "Good"


def test_save_rating_surfaces_legacy_conflict_without_deleting_it(
    monkeypatch,
    tmp_path,
) -> None:
    page = _page(tmp_path)
    rating_dir = _rating_dir(tmp_path)
    rating_dir.mkdir(parents=True)
    old_file = rating_dir / "example._.SUB001._.rater1._.Old._.False.json"
    old_file.write_text("{}", encoding="utf-8")
    errors = []
    monkeypatch.setattr(
        gui_qcpage_module.messagebox,
        "showerror",
        lambda *args: errors.append(args),
    )

    page.save_rating()

    assert old_file.exists()
    assert not (rating_dir / "example-rater1-SUB001.json").exists()
    assert errors and "Legacy rating requires explicit migration" in errors[0][-1]


def test_save_rating_keeps_old_file_if_atomic_write_fails(monkeypatch, tmp_path) -> None:
    page = _page(tmp_path)
    rating_dir = _rating_dir(tmp_path)
    rating_dir.mkdir(parents=True)
    old_file = rating_dir / "example-rater1-SUB001.json"
    old_file.write_text(json.dumps(_module(score="Poor", tag=False)), encoding="utf-8")
    before = old_file.read_bytes()

    def fail_save(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(rating_service.FileUtils, "safe_json_save", fail_save)
    monkeypatch.setattr(gui_qcpage_module.messagebox, "showerror", lambda *args, **kwargs: None)

    page.save_rating()

    assert old_file.exists()
    assert old_file.read_bytes() == before
    assert list(rating_dir.glob("*.json")) == [old_file]


def test_tk_controller_reuses_one_directory_index_for_loads_and_save(
    monkeypatch,
    tmp_path,
) -> None:
    rating_dir = _rating_dir(tmp_path)
    rating_dir.mkdir(parents=True)
    for ezqcid in ("SUB001", "SUB002"):
        payload = _module(score="Good", tag=True)
        payload["ezqcid"] = ezqcid
        path = (
            rating_dir
            / f"example._.{ezqcid}._.rater1._.Good._.True.json"
        )
        path.write_text(json.dumps(payload), encoding="utf-8")

    real_scan = rating_service.RatingRaterDirectoryIndex._scan_legacy_paths
    scans = 0

    def counted_scan(index):
        nonlocal scans
        scans += 1
        return real_scan(index)

    monkeypatch.setattr(
        rating_service.RatingRaterDirectoryIndex,
        "_scan_legacy_paths",
        counted_scan,
    )

    def unexpected_fallback(*_args):
        pytest.fail("save rebuilt the legacy lookup instead of reusing the index")

    monkeypatch.setattr(
        rating_service.RatingService,
        "_legacy_files_for_identity",
        staticmethod(unexpected_fallback),
    )
    controller = QCPageController()
    module = _module(score=None, tag=False)

    for ezqcid in ("SUB001", "SUB002"):
        files, payload = controller.load_first_legacy_module_rating(
            module,
            rating_dir,
            ezqcid,
            "rater1",
        )
        assert len(files) == 1
        assert payload is not None
        assert payload["ezqcid"] == ezqcid

    new_rating = _module(score="Good", tag=False)
    new_rating["ezqcid"] = "SUB003"
    saved = controller.save_legacy_module_rating(new_rating, rating_dir)

    assert saved.name == "example-rater1-SUB003.json"
    assert scans == 1


def test_set_module_rater_dir_without_rater_enters_watch_mode(tmp_path) -> None:
    module = _module()
    module["rater"] = None
    page = _page(tmp_path, module=module)

    path = page._set_module_rater_dir("example", None)

    assert page.watch_mode_ is True
    assert page.watch_mode.get() is True
    expected = (
        tmp_path
        / "RatingFiles"
        / "example"
        / "__observation_no_rater__"
    )
    assert path == str(expected)
    assert not expected.exists()


def test_check_module_without_rater_enters_watch_mode_without_popup(monkeypatch, tmp_path) -> None:
    module = _module()
    module["rater"] = " "
    page = _page(tmp_path, module=module)
    monkeypatch.setattr(gui_qcpage_module.messagebox, "showerror", lambda *args, **kwargs: pytest.fail("unexpected popup"))

    result = page.check_module()

    assert not result
    assert page.watch_mode_ is True
    assert page.watch_mode.get() is True


def test_save_rating_without_rater_enters_watch_mode_and_writes_nothing(tmp_path) -> None:
    module = _module()
    module["rater"] = ""
    page = _page(tmp_path, module=module)

    page.save_rating()

    assert page.watch_mode_ is True
    assert page.watch_mode.get() is True
    assert list(tmp_path.iterdir()) == []


def test_load_rating_only_applies_rating_state_not_module_configuration(tmp_path) -> None:
    page = _page(tmp_path, module=_module(score=None, tag=False))
    rating_payload = _module(score="Good", tag=True)
    rating_payload["label"] = "Stale Label From Rating"
    rating_payload["code"] = "stale-code"
    rating_payload["select_filter"] = "stale-filter"
    rating_payload["notes"] = "saved notes"
    rating_payload["time"] = "2026-06-02 00:00:00"
    rating_payload["code_exe"] = {"0": "saved"}
    rating_dir = _rating_dir(tmp_path)
    rating_dir.mkdir(parents=True)
    rating_file = rating_dir / "example._.SUB001._.rater1._.Good._.True.json"
    rating_file.write_text(json.dumps(rating_payload), encoding="utf-8")

    page.load_rating(ezqcid="SUB001")

    module = page.dt.settings["qcmodule"]["1"]
    assert module["label"] == "Current Label"
    assert module["code"] == "current-code"
    assert module["select_filter"] == "current-filter"
    assert module["scores"]["1"]["value"] == "Good"
    assert module["tags"]["1"]["value"] is True
    assert module["notes"] == "saved notes"
    assert module["time"] == "2026-06-02 00:00:00"
    assert module["code_exe"] == {"0": "saved"}


def test_load_rating_with_duplicate_files_loads_first_without_popup(monkeypatch, tmp_path) -> None:
    page = _page(tmp_path, module=_module(score=None, tag=False))
    first_payload = _module(score="A", tag=False)
    second_payload = _module(score="B", tag=True)
    rating_dir = _rating_dir(tmp_path)
    rating_dir.mkdir(parents=True)
    first_file = rating_dir / "example._.SUB001._.rater1._.A._.False.json"
    second_file = rating_dir / "example._.SUB001._.rater1._.B._.True.json"
    first_file.write_text(json.dumps(first_payload), encoding="utf-8")
    second_file.write_text(json.dumps(second_payload), encoding="utf-8")
    monkeypatch.setattr(gui_qcpage_module.messagebox, "showinfo", lambda *args, **kwargs: pytest.fail("unexpected popup"))
    monkeypatch.setattr(gui_qcpage_module.messagebox, "showerror", lambda *args, **kwargs: pytest.fail("unexpected popup"))

    page.load_rating(ezqcid="SUB001")

    module = page.dt.settings["qcmodule"]["1"]
    assert module["scores"]["1"]["value"] == "A"
    assert module["tags"]["1"]["value"] is False


def test_load_rating_without_rater_enters_watch_mode_without_popup(monkeypatch, tmp_path) -> None:
    module = _module(score="Good", tag=True)
    module["rater"] = None
    page = _page(tmp_path, module=module)
    monkeypatch.setattr(gui_qcpage_module.messagebox, "showerror", lambda *args, **kwargs: pytest.fail("unexpected popup"))

    page.load_rating(ezqcid="SUB002")

    current_module = page.dt.settings["qcmodule"]["1"]
    assert page.watch_mode_ is True
    assert page.watch_mode.get() is True
    assert current_module["ezqcid"] == "SUB002"
    assert current_module["scores"]["1"]["value"] is None
    assert current_module["tags"]["1"]["value"] is False


def test_load_rating_incompatible_file_enters_watch_mode_without_popup(monkeypatch, tmp_path) -> None:
    page = _page(tmp_path, module=_module(score=None, tag=False))
    rating_payload = _module(score="Good", tag=True)
    rating_payload["scores"]["1"]["num_"] = ["Bad", "Ugly"]
    rating_dir = _rating_dir(tmp_path)
    rating_dir.mkdir(parents=True)
    rating_file = rating_dir / "example._.SUB001._.rater1._.Good._.True.json"
    rating_file.write_text(json.dumps(rating_payload), encoding="utf-8")
    monkeypatch.setattr(gui_qcpage_module.messagebox, "showerror", lambda *args, **kwargs: pytest.fail("unexpected popup"))

    page.load_rating(ezqcid="SUB001")

    module = page.dt.settings["qcmodule"]["1"]
    assert page.watch_mode_ is True
    assert page.watch_mode.get() is True
    assert module["scores"]["1"]["value"] == "Good"
    assert module["tags"]["1"]["value"] is True


def test_load_present_to_gui_without_module_uses_current_module(tmp_path) -> None:
    module = _module(score="Good", tag=True)
    module["notes"] = "saved note"
    page = _page(tmp_path, module=module)
    page.score_vars = {"1": _ValueVar()}
    page.tag_vars = {"1": _ValueVar()}
    page.notes_text = _NotesText()
    page.ezqcid_index = None

    page.load_present_to_gui()

    assert page.score_vars["1"].value == "Good"
    assert page.tag_vars["1"].value is True
    assert page.notes_text.content == "saved note"


def test_previous_note_delete_span_deletes_back_to_previous_space() -> None:
    content = "alpha beta gamma"
    start, end = gui_qcpage._previous_note_delete_span(content, len(content))

    assert content[:start] + content[end:] == "alpha beta "


def test_previous_note_delete_span_includes_trailing_space() -> None:
    content = "alpha beta "
    start, end = gui_qcpage._previous_note_delete_span(content, len(content))

    assert content[:start] + content[end:] == "alpha "


def test_gen_code_respects_passed_settings_for_current_module(tmp_path) -> None:
    page = _page(tmp_path)
    module = _module()
    module["code"] = "echo {image_path}"
    settings = {
        "constants": {"image_path": "/custom/path.nii.gz"},
        "qcmodule": {"1": module},
    }
    table = pd.DataFrame({"ezqcid": ["SUB001"]})

    code, code_exe = page.gen_code("SUB001", settings=settings, table=table)

    assert code == "echo /custom/path.nii.gz"
    assert code_exe == {0: "echo /custom/path.nii.gz"}
