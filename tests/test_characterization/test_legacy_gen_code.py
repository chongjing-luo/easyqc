from types import SimpleNamespace

import pandas as pd

from gui.gui_qcpage import gui_qcpage


def _page_with_code(code: str) -> gui_qcpage:
    page = gui_qcpage()
    page.module_index = "1"
    page.dt = SimpleNamespace()
    page.dt.settings = {
        "constants": {"site": "BNU"},
        "qcmodule": {
            "1": {
                "name": "example",
                "code": code,
            }
        },
    }
    page.dt.tab = {
        "example": pd.DataFrame(
            [
                {
                    "ezqcid": "SUB001",
                    "age": 29,
                    "image_path": "/tmp/sub001.nii.gz",
                }
            ]
        )
    }
    return page


def test_gen_code_replaces_three_variable_syntaxes() -> None:
    page = _page_with_code("echo $ezqcid ${age} {site} {image_path}")

    code, code_exe = page.gen_code("SUB001")

    assert code == "echo SUB001 29 BNU /tmp/sub001.nii.gz"
    assert code_exe == {0: "echo SUB001 29 BNU /tmp/sub001.nii.gz"}


def test_gen_code_multicmd_at_start_splits_commands() -> None:
    page = _page_with_code("MULTICMD echo {ezqcid};| echo ${age}")

    code, code_exe = page.gen_code("SUB001")

    assert code == "echo SUB001;| echo 29"
    assert code_exe == {0: "echo SUB001", 1: "echo 29"}


def test_gen_code_multicmd_after_prefix_prepends_prefix_to_each_command() -> None:
    page = _page_with_code("cd /tmp MULTICMD echo {ezqcid};| echo done")

    code, code_exe = page.gen_code("SUB001")

    assert code == "cd /tmp MULTICMD echo SUB001;| echo done"
    assert code_exe == {0: "cd /tmp;echo SUB001", 1: "cd /tmp;echo done"}
