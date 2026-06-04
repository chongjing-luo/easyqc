import json
from types import SimpleNamespace

import pytest
import pandas as pd

import models.rating as rating_model
from gui import gui_qcpage as gui_qcpage_module
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
    page = gui_qcpage()
    page.module_index = "1"
    page.module_name = "example"
    page.watch_mode = _WatchMode(False)
    page.watch_mode_ = False
    page.dt = SimpleNamespace()
    page.dt.dir_module_rater = str(tmp_path)
    page.dt.settings = {"qcmodule": {"1": module or _module()}}
    return page


def test_save_rating_writes_new_file_before_cleaning_old_files(tmp_path) -> None:
    page = _page(tmp_path)
    old_file = tmp_path / "example._.SUB001._.rater1._.Old._.False.json"
    old_file.write_text("{}", encoding="utf-8")

    page.save_rating()

    new_file = tmp_path / "example._.SUB001._.rater1._.Good._.True.json"
    assert new_file.exists()
    assert not old_file.exists()
    payload = json.loads(new_file.read_text(encoding="utf-8"))
    assert payload["name"] == "example"
    assert payload["code"] == "current-code"
    assert payload["scores"]["1"]["value"] == "Good"


def test_save_rating_keeps_old_file_if_atomic_write_fails(monkeypatch, tmp_path) -> None:
    page = _page(tmp_path)
    old_file = tmp_path / "example._.SUB001._.rater1._.Old._.False.json"
    old_file.write_text("{}", encoding="utf-8")

    def fail_save(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(rating_model.FileUtils, "safe_json_save", fail_save)
    monkeypatch.setattr(gui_qcpage_module.messagebox, "showerror", lambda *args, **kwargs: None)

    page.save_rating()

    assert old_file.exists()
    assert not (tmp_path / "example._.SUB001._.rater1._.Good._.True.json").exists()


def test_load_rating_only_applies_rating_state_not_module_configuration(tmp_path) -> None:
    page = _page(tmp_path, module=_module(score=None, tag=False))
    rating_payload = _module(score="Good", tag=True)
    rating_payload["label"] = "Stale Label From Rating"
    rating_payload["code"] = "stale-code"
    rating_payload["select_filter"] = "stale-filter"
    rating_payload["notes"] = "saved notes"
    rating_payload["time"] = "2026-06-02 00:00:00"
    rating_payload["code_exe"] = {"0": "saved"}
    rating_file = tmp_path / "example._.SUB001._.rater1._.Good._.True.json"
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
    first_file = tmp_path / "example._.SUB001._.rater1._.A._.False.json"
    second_file = tmp_path / "example._.SUB001._.rater1._.B._.True.json"
    first_file.write_text(json.dumps(first_payload), encoding="utf-8")
    second_file.write_text(json.dumps(second_payload), encoding="utf-8")
    monkeypatch.setattr(gui_qcpage_module.messagebox, "showinfo", lambda *args, **kwargs: pytest.fail("unexpected popup"))
    monkeypatch.setattr(gui_qcpage_module.messagebox, "showerror", lambda *args, **kwargs: pytest.fail("unexpected popup"))

    page.load_rating(ezqcid="SUB001")

    module = page.dt.settings["qcmodule"]["1"]
    assert module["scores"]["1"]["value"] == "A"
    assert module["tags"]["1"]["value"] is False


def test_load_rating_incompatible_file_enters_watch_mode_without_popup(monkeypatch, tmp_path) -> None:
    page = _page(tmp_path, module=_module(score=None, tag=False))
    rating_payload = _module(score="Good", tag=True)
    rating_payload["scores"]["1"]["num_"] = ["Bad", "Ugly"]
    rating_file = tmp_path / "example._.SUB001._.rater1._.Good._.True.json"
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
