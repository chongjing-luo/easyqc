from copy import deepcopy

import pytest

from models.qcmodule import QCModule


def test_command_group_round_trip_and_default_mode():
    from models.qcmodule import CommandGroup

    group = CommandGroup.from_legacy_dict("7", {"code": "viewer {image}"})
    assert group.key == "7"
    assert group.to_legacy_dict() == {"code": "viewer {image}", "interper": "direct"}


def test_module_preserves_ordered_groups_without_mutating_source():
    payload = {
        "name": "AnatQC", "label": "Anatomical QC", "code": None,
        "commands": {
            "later": {"code": "viewer {image}", "interper": "direct"},
            "earlier": {"code": "echo ready | cat", "interper": "shell"},
        },
    }
    before = deepcopy(payload)
    module = QCModule.from_legacy_dict(payload)
    assert module.commands is not None
    assert tuple(module.commands) == ("later", "earlier")
    assert module.to_legacy_dict()["commands"] == payload["commands"]
    module.commands["later"].code = "viewer other"
    assert payload == before


@pytest.mark.parametrize("commands", [None, {}])
def test_absent_groups_do_not_extend_legacy_payload(commands):
    module = QCModule.from_legacy_dict({"name": "AnatQC", "commands": commands})
    assert "commands" not in module.to_legacy_dict()


@pytest.mark.parametrize("commands", [[], "viewer", {"1": None}, {"1": {}},
    {"1": {"code": None}}, {"1": {"code": 7}},
    {"1": {"code": "viewer", "interper": "unexpected"}},
])
def test_invalid_group_payloads_fail_loudly(commands):
    with pytest.raises(ValueError, match="[Cc]ommand|commands"):
        QCModule.from_legacy_dict({"name": "AnatQC", "commands": commands})
