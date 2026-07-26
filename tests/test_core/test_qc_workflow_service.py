from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from core.code_executor import CodeExecutor
from core.qc_workflow_service import (
    QcIdentityError,
    QcReadOnlyError,
    QcSessionError,
    QcWorkflowService,
)
from core.rating_service import RatingService


def _module(*, rater: str | None = "rater1", watch_mode: bool = False) -> dict:
    return {
        "name": "AnatQC",
        "label": "Anatomical image quality",
        "rater": rater,
        "ezqcid": None,
        "watch_mode": watch_mode,
        "tags": {"1": {"label": "Motion artifact", "value": False}},
        "scores": {
            "1": {
                "label": "Overall quality",
                "num": "Poor,Fair,Good",
                "num_": "Poor,Fair,Good",
                "value": None,
            }
        },
        "code": "MULTICMD freeview {image};|itksnap {image}",
        "interper": "shell",
        "control": True,
        "select_filter": None,
        "showing": True,
        "code_exe": None,
        "time": None,
        "notes": None,
        "button": {"help": "Open SOP"},
    }


def _subjects() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ezqcid": ["SUB001", "SUB002", "SUB003"],
            "image": ["/data/1.nii.gz", "/data/2.nii.gz", "/data/3.nii.gz"],
        }
    )


class _FakeExecutor:
    def __init__(self) -> None:
        self.planner = CodeExecutor()
        self.started = []
        self.close_calls = 0

    def render_command_plan(self, template, variables):
        return self.planner.render_command_plan(template, variables)

    def start_commands(self, commands, control=False, cwd=None):
        self.started.append((dict(commands), control, cwd))
        return [object() for _ in commands]

    def close_current_processes(self):
        self.close_calls += 1


def _workflow(
    tmp_path: Path,
    *,
    module=None,
    executor=None,
    queue_summaries=None,
    initial_read_only=False,
) -> QcWorkflowService:
    return QcWorkflowService(
        module or _module(),
        _subjects(),
        rating_dir=tmp_path / "RatingFiles" / "AnatQC" / "rater1",
        constants={"project": "synthetic"},
        code_executor=executor or _FakeExecutor(),
        queue_summaries=queue_summaries,
        initial_read_only=initial_read_only,
    )


def test_queue_summary_combines_seeded_rows_with_live_draft_and_committed_save(
    tmp_path,
) -> None:
    workflow = _workflow(
        tmp_path,
        queue_summaries={
            "SUB001": ("Fair", ""),
            "SUB002": ("Good", "Motion artifact"),
        },
    )

    assert workflow.queue_summary("SUB001") == ("", "")
    assert workflow.queue_summary("SUB002") == ("Good", "Motion artifact")
    workflow.set_score("1", "Good")
    workflow.set_tag("1", True)
    assert workflow.queue_summary("SUB001") == ("Good", "Motion artifact")

    workflow.save()
    workflow.navigate_to("SUB002")

    assert workflow.queue_summary("SUB001") == ("Good", "Motion artifact")
    # Once selected, the live rating-file load is authoritative over the
    # earlier detached aggregate snapshot.
    assert workflow.queue_summary("SUB002") == ("", "")
    with pytest.raises(QcIdentityError, match="Unknown QC ezqcid"):
        workflow.queue_summary("FOREIGN")


def test_viewer_plan_launch_navigation_and_close_use_code_executor(tmp_path) -> None:
    executor = _FakeExecutor()
    workflow = _workflow(tmp_path, executor=executor)

    plan = workflow.viewer_plan()
    assert plan.commands == {
        0: "freeview /data/1.nii.gz",
        1: "itksnap /data/1.nii.gz",
    }
    assert plan.control is True

    workflow.launch_viewer()
    assert executor.started == [(plan.commands, True, None)]
    assert workflow.navigate_to("SUB002")
    assert executor.close_calls == 1
    assert workflow.current_ezqcid == "SUB002"

    workflow.close()
    assert executor.close_calls == 2


def test_partial_viewer_launch_failure_cleans_started_processes(tmp_path) -> None:
    executor = _FakeExecutor()

    def fail_start(*_args, **_kwargs):
        raise OSError("second viewer failed")

    executor.start_commands = fail_start
    workflow = _workflow(tmp_path, executor=executor)

    with pytest.raises(OSError, match="second viewer failed"):
        workflow.launch_viewer()
    assert executor.close_calls == 1


def test_watch_mode_without_rater_rejects_edits_and_writes_nothing(tmp_path) -> None:
    target = tmp_path / "should-not-exist"
    workflow = QcWorkflowService(
        _module(rater="  "),
        _subjects(),
        rating_dir=target,
        code_executor=_FakeExecutor(),
    )

    assert workflow.watch_mode
    assert "rater" in workflow.read_only_reason.lower()
    with pytest.raises(QcReadOnlyError):
        workflow.set_score("1", "Good")
    with pytest.raises(QcReadOnlyError):
        workflow.save()
    assert not target.exists()
    assert workflow.viewer_plan().commands


def test_initial_read_only_is_a_presentation_hint_not_a_core_write_block(
    tmp_path,
) -> None:
    workflow = _workflow(tmp_path, initial_read_only=True)

    assert workflow.initial_read_only
    assert not workflow.watch_mode
    assert workflow.read_only_reason == ""

    workflow.set_score("1", "Good")
    workflow.set_tag("1", True)
    workflow.set_notes("corrected after review")
    saved = workflow.save()

    assert saved.exists()
    assert workflow.current_module.scores["1"].value == "Good"
    assert workflow.current_module.tags["1"].value is True
    assert workflow.current_module.notes == "corrected after review"


def test_save_preserves_full_module_payload_and_schema_version(tmp_path) -> None:
    workflow = _workflow(tmp_path)
    workflow.set_score("1", "Good")
    workflow.set_tag("1", True)
    workflow.set_notes("Reviewed carefully")

    path = workflow.save()
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["name"] == "AnatQC"
    assert payload["label"] == "Anatomical image quality"
    assert payload["button"] == {"help": "Open SOP"}
    assert payload["code"] == _module()["code"]
    assert payload["scores"]["1"]["value"] == "Good"
    assert payload["tags"]["1"]["value"] is True
    assert payload["notes"] == "Reviewed carefully"


def test_failed_save_does_not_advance_or_delete_previous_rating(monkeypatch, tmp_path) -> None:
    workflow = _workflow(tmp_path)
    workflow.set_score("1", "Good")
    target = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target.mkdir(parents=True)
    old = target / "AnatQC._.SUB001._.rater1._.Old._.False.json"
    old.write_text("{}", encoding="utf-8")

    def fail_save(*_args, **_kwargs):
        raise OSError("atomic write failed")

    monkeypatch.setattr(RatingService, "save_rating_to_rater_dir", fail_save)

    with pytest.raises(OSError, match="atomic write failed"):
        workflow.save_and_move(1)
    assert workflow.current_index == 0
    assert workflow.current_ezqcid == "SUB001"
    assert old.exists()


def test_duplicate_rating_files_force_visible_read_only_state(tmp_path) -> None:
    target = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target.mkdir(parents=True)
    first = _module()
    first.update({"ezqcid": "SUB001", "rater": "rater1"})
    second = json.loads(json.dumps(first))
    second["scores"]["1"]["value"] = "Good"
    (target / "AnatQC._.SUB001._.rater1._.A._.False.json").write_text(
        json.dumps(first), encoding="utf-8"
    )
    (target / "AnatQC._.SUB001._.rater1._.B._.False.json").write_text(
        json.dumps(second), encoding="utf-8"
    )

    workflow = _workflow(tmp_path)

    assert workflow.watch_mode
    assert "multiple" in workflow.read_only_reason.lower()
    with pytest.raises(QcReadOnlyError):
        workflow.set_notes("must remain read-only")
    with pytest.raises(QcReadOnlyError):
        workflow.save()


def test_schema_drift_forces_read_only_but_keeps_saved_rating_visible(tmp_path) -> None:
    target = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target.mkdir(parents=True)
    saved = _module()
    saved.update({"ezqcid": "SUB001", "rater": "rater1"})
    saved["scores"]["1"]["num_"] = "Reject,Accept"
    saved["scores"]["1"]["value"] = "Accept"
    (target / "AnatQC._.SUB001._.rater1._.Accept._.False.json").write_text(
        json.dumps(saved), encoding="utf-8"
    )

    workflow = _workflow(tmp_path)

    assert workflow.watch_mode
    assert "schema" in workflow.read_only_reason.lower()
    assert workflow.current_module.scores["1"].value == "Accept"
    with pytest.raises(QcReadOnlyError):
        workflow.set_score("1", "Good")
    with pytest.raises(QcReadOnlyError):
        workflow.save()


def test_schema_drift_reason_clears_after_successful_case_navigation(tmp_path) -> None:
    target = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target.mkdir(parents=True)
    saved = _module()
    saved.update({"ezqcid": "SUB001", "rater": "rater1"})
    saved["scores"]["1"]["num_"] = "Reject,Accept"
    saved["scores"]["1"]["value"] = "Accept"
    (target / "AnatQC._.SUB001._.rater1._.Accept._.False.json").write_text(
        json.dumps(saved), encoding="utf-8"
    )

    workflow = _workflow(tmp_path)
    assert workflow.watch_mode
    assert "schema" in workflow.read_only_reason.lower()

    assert workflow.navigate_to("SUB002")

    assert not workflow.watch_mode
    assert workflow.read_only_reason == ""
    assert workflow.current_module.scores["1"].value is None
    workflow.set_score("1", "Good")
    assert workflow.dirty


def test_duplicate_rating_reason_clears_after_successful_case_navigation(tmp_path) -> None:
    target = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target.mkdir(parents=True)
    saved = _module()
    saved.update({"ezqcid": "SUB001", "rater": "rater1"})
    for suffix in ("A", "B"):
        (target / f"AnatQC._.SUB001._.rater1._.{suffix}._.False.json").write_text(
            json.dumps(saved), encoding="utf-8"
        )

    workflow = _workflow(tmp_path)
    assert workflow.watch_mode
    assert "multiple" in workflow.read_only_reason.lower()

    assert workflow.navigate_to("SUB002")

    assert not workflow.watch_mode
    assert workflow.read_only_reason == ""


def test_session_watch_reason_survives_case_reason_clear(tmp_path) -> None:
    target = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target.mkdir(parents=True)
    saved = _module(watch_mode=True)
    saved.update({"ezqcid": "SUB001", "rater": "rater1"})
    saved["scores"]["1"]["num_"] = "Reject,Accept"
    saved["scores"]["1"]["value"] = "Accept"
    (target / "AnatQC._.SUB001._.rater1._.Accept._.False.json").write_text(
        json.dumps(saved), encoding="utf-8"
    )

    workflow = _workflow(tmp_path, module=_module(watch_mode=True))
    assert "watch mode" in workflow.read_only_reason.lower()
    assert "schema" in workflow.read_only_reason.lower()

    assert workflow.navigate_to("SUB002")

    assert workflow.watch_mode
    assert "watch mode" in workflow.read_only_reason.lower()
    assert "schema" not in workflow.read_only_reason.lower()
    with pytest.raises(QcReadOnlyError):
        workflow.set_score("1", "Good")
    with pytest.raises(QcReadOnlyError):
        workflow.save()


def test_failed_target_load_restores_prior_case_draft_and_reasons(
    monkeypatch,
    tmp_path,
) -> None:
    workflow = _workflow(tmp_path)
    workflow.set_score("1", "Good")
    workflow.set_notes("draft remains")
    before = workflow.current_module
    before_reason = workflow.read_only_reason

    target = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target.mkdir(parents=True)
    saved = _module()
    saved.update({"ezqcid": "SUB002", "rater": "rater1"})
    for suffix in ("A", "B"):
        (target / f"AnatQC._.SUB002._.rater1._.{suffix}._.False.json").write_text(
            json.dumps(saved), encoding="utf-8"
        )
    rating_files_before = {
        path.name: path.read_bytes() for path in sorted(target.glob("*.json"))
    }

    def fail_target_load(_path):
        raise OSError("target rating unavailable")

    monkeypatch.setattr(
        RatingService,
        "load_legacy_rating_file",
        staticmethod(fail_target_load),
    )

    with pytest.raises(OSError, match="target rating unavailable"):
        workflow.navigate_to("SUB002", discard_changes=True)

    assert workflow.current_index == 0
    assert workflow.current_ezqcid == "SUB001"
    assert workflow.current_module == before
    assert workflow.current_module.scores["1"].value == "Good"
    assert workflow.current_module.notes == "draft remains"
    assert workflow.dirty
    assert workflow.read_only_reason == before_reason
    assert not workflow.watch_mode
    assert {
        path.name: path.read_bytes() for path in sorted(target.glob("*.json"))
    } == rating_files_before


@pytest.mark.parametrize(
    "identities",
    [(["SUB001", "SUB001"]), (["SUB001", "  "])],
)
def test_session_rejects_duplicate_or_blank_qc_identity(tmp_path, identities) -> None:
    source = pd.DataFrame({"ezqcid": identities, "image": ["a", "b"]})

    with pytest.raises(QcIdentityError):
        QcWorkflowService(
            _module(),
            source,
            rating_dir=tmp_path,
            code_executor=_FakeExecutor(),
        )


def test_unsaved_draft_blocks_plain_navigation(tmp_path) -> None:
    workflow = _workflow(tmp_path)
    workflow.set_notes("draft")

    with pytest.raises(QcSessionError, match="unsaved"):
        workflow.navigate_to("SUB002")
    assert workflow.current_ezqcid == "SUB001"


def test_closed_workflow_rejects_late_mutation_and_save(tmp_path) -> None:
    workflow = _workflow(tmp_path)
    workflow.close()

    with pytest.raises(QcSessionError, match="closed"):
        workflow.set_score("1", "Good")
    with pytest.raises(QcSessionError, match="closed"):
        workflow.save()
