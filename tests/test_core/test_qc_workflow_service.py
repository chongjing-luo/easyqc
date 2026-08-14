from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from core.command_output import (
    CommandOutputBatch,
    CommandOutputEvent,
    CommandOutputJournal,
    CommandOutputStatus,
    ViewerExecutionContext,
)
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
        "easyqcid": None,
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
            "easyqcid": ["SUB001", "SUB002", "SUB003"],
            "image": ["/data/1.nii.gz", "/data/2.nii.gz", "/data/3.nii.gz"],
        }
    )


class _FakeExecutor:
    def __init__(self) -> None:
        self.planner = CodeExecutor()
        self.started = []
        self.close_calls = 0
        self.output_queries = []
        self.output_batch = CommandOutputBatch(
            events=(),
            next_sequence=0,
            truncated=False,
            status=CommandOutputStatus(
                session_id="fake-session",
                file_logging_enabled=False,
                session_log_path=None,
            ),
        )

    def render_command_plan(self, template, variables):
        return self.planner.render_command_plan(template, variables)

    def start_commands(
        self,
        commands,
        control=False,
        cwd=None,
        output_contexts=None,
    ):
        self.started.append(
            (
                dict(commands),
                control,
                cwd,
                None if output_contexts is None else dict(output_contexts),
            )
        )
        return [object() for _ in commands]

    def command_output_since(self, after_sequence):
        self.output_queries.append(after_sequence)
        return self.output_batch

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
    with pytest.raises(QcIdentityError, match="Unknown QC easyqcid"):
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
    first_contexts = executor.started[0][3]
    assert executor.started[0][:3] == (plan.commands, True, None)
    assert tuple(first_contexts) == tuple(plan.commands)
    assert first_contexts == {
        0: ViewerExecutionContext("AnatQC", "rater1", "SUB001", 0),
        1: ViewerExecutionContext("AnatQC", "rater1", "SUB001", 1),
    }
    assert workflow.navigate_to("SUB002")
    assert executor.close_calls == 1
    assert workflow.current_easyqcid == "SUB002"

    workflow.launch_viewer()
    second_contexts = executor.started[1][3]
    assert tuple(second_contexts) == tuple(plan.commands)
    assert second_contexts == {
        0: ViewerExecutionContext("AnatQC", "rater1", "SUB002", 0),
        1: ViewerExecutionContext("AnatQC", "rater1", "SUB002", 1),
    }

    workflow.close()
    assert executor.close_calls == 2


def test_viewer_context_keeps_an_explicit_empty_rater(tmp_path) -> None:
    executor = _FakeExecutor()
    workflow = _workflow(tmp_path, module=_module(rater=None), executor=executor)

    workflow.launch_viewer()

    contexts = executor.started[0][3]
    assert contexts
    assert {context.rater for context in contexts.values()} == {""}


def test_viewer_context_uses_sparse_integer_plan_keys_as_command_indices(
    tmp_path,
) -> None:
    executor = _FakeExecutor()
    executor.render_command_plan = lambda *_args: (
        "viewer three; viewer seven",
        {3: "viewer three", 7: "viewer seven"},
    )
    workflow = _workflow(tmp_path, executor=executor)

    workflow.launch_viewer()

    commands, _control, _cwd, contexts = executor.started[0]
    assert tuple(commands) == (3, 7)
    assert tuple(contexts) == (3, 7)
    assert contexts == {
        3: ViewerExecutionContext("AnatQC", "rater1", "SUB001", 3),
        7: ViewerExecutionContext("AnatQC", "rater1", "SUB001", 7),
    }


def test_viewer_plan_rejects_non_integer_command_keys_before_launch(tmp_path) -> None:
    executor = _FakeExecutor()
    executor.render_command_plan = lambda *_args: (
        "viewer invalid",
        {"first": "viewer invalid"},
    )
    workflow = _workflow(tmp_path, executor=executor)

    with pytest.raises(QcSessionError, match="command ind"):
        workflow.launch_viewer()

    assert executor.started == []
    assert workflow.current_module.code_exe is None


def test_command_output_query_is_exact_non_destructive_and_requires_active_workflow(
    tmp_path,
) -> None:
    executor = _FakeExecutor()
    context = ViewerExecutionContext("AnatQC", "rater1", "SUB001", 0)
    event = CommandOutputEvent(
        sequence=4,
        timestamp=datetime.now().astimezone(),
        execution_id="C001",
        kind="stdout",
        text="same retained event",
        context=context,
    )
    batch = CommandOutputBatch(
        events=(event,),
        next_sequence=4,
        truncated=False,
        status=CommandOutputStatus(
            session_id="fake-session",
            file_logging_enabled=False,
            session_log_path=None,
        ),
    )
    executor.output_batch = batch
    workflow = _workflow(tmp_path, executor=executor)

    first = workflow.command_output_since(3)
    second = workflow.command_output_since(3)

    assert first is batch
    assert second is batch
    assert first.events[0] is second.events[0]
    assert executor.output_queries == [3, 3]

    workflow.close()
    with pytest.raises(QcSessionError, match="closed"):
        workflow.command_output_since(3)
    assert executor.output_queries == [3, 3]


def test_real_subprocess_output_crosses_workflow_with_qc_context_and_log(
    tmp_path,
) -> None:
    script = (
        "import sys;"
        "print('workflow-out',flush=True);"
        "print('workflow-err',file=sys.stderr,flush=True)"
    )
    module = _module()
    module["code"] = subprocess.list2cmdline([sys.executable, "-c", script])
    module["control"] = False
    journal = CommandOutputJournal(log_root=tmp_path / "viewer-logs")
    executor = CodeExecutor(command_output_journal=journal)
    workflow = _workflow(tmp_path, module=module, executor=executor)

    try:
        processes = workflow.launch_viewer()
        first = workflow.command_output_since(0)
        second = workflow.command_output_since(0)

        assert len(processes) == 1
        assert {event.kind for event in first.events} >= {
            "start",
            "command",
            "stdout",
            "stderr",
            "exit",
        }
        assert any(
            event.kind == "stdout" and event.text == "workflow-out"
            for event in first.events
        )
        assert any(
            event.kind == "stderr" and event.text == "workflow-err"
            for event in first.events
        )
        assert any(
            event.kind == "exit" and event.returncode == 0
            for event in first.events
        )
        assert {
            event.context for event in first.events
        } == {ViewerExecutionContext("AnatQC", "rater1", "SUB001", 0)}
        assert first.events == second.events
        assert all(
            left is right for left, right in zip(first.events, second.events)
        )

        log_path = first.status.session_log_path
        assert log_path is not None
        log_text = log_path.read_text(encoding="utf-8")
        assert "module=AnatQC rater=rater1 easyqcid=SUB001 index=0" in log_text
        assert "workflow-out" in log_text
        assert "workflow-err" in log_text
        assert "EXIT    code=0" in log_text
    finally:
        workflow.close()
        journal.close()


def test_viewer_plan_rejects_project_constant_row_column_collision(tmp_path) -> None:
    subjects = _subjects().assign(project=["row-a", "row-b", "row-c"])
    workflow = QcWorkflowService(
        _module(),
        subjects,
        rating_dir=tmp_path / "ratings",
        constants={"project": "constant-value"},
        code_executor=_FakeExecutor(),
    )

    with pytest.raises(QcSessionError, match="project"):
        workflow.viewer_plan()

    assert workflow.current_module.code_exe is None


def test_partial_viewer_launch_failure_cleans_started_processes(tmp_path) -> None:
    executor = _FakeExecutor()
    module_source = _module()
    subjects_source = _subjects()
    original_module_source = deepcopy(module_source)
    original_subjects_source = subjects_source.copy(deep=True)

    def fail_start(*_args, **_kwargs):
        raise OSError("second viewer failed")

    executor.start_commands = fail_start
    workflow = QcWorkflowService(
        module_source,
        subjects_source,
        rating_dir=tmp_path / "RatingFiles" / "AnatQC" / "rater1",
        constants={"project": "synthetic"},
        code_executor=executor,
    )
    workflow.set_notes("unsaved draft must survive")
    before_module = workflow.current_module
    before_index = workflow.current_index
    before_dirty = workflow.dirty

    with pytest.raises(OSError, match="second viewer failed"):
        workflow.launch_viewer()
    assert executor.close_calls == 1
    assert workflow.current_module == before_module
    assert workflow.current_index == before_index
    assert workflow.dirty is before_dirty
    assert not (tmp_path / "RatingFiles").exists()
    assert module_source == original_module_source
    pd.testing.assert_frame_equal(subjects_source, original_subjects_source)


def test_watch_mode_without_rater_rejects_edits_and_writes_nothing(tmp_path) -> None:
    target = tmp_path / "should-not-exist"
    workflow = QcWorkflowService(
        _module(rater=""),
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

    assert payload["schema_version"] == 3
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
    old = target / "unrelated-sentinel.json"
    old.write_text("{}", encoding="utf-8")

    def fail_save(*_args, **_kwargs):
        raise OSError("atomic write failed")

    monkeypatch.setattr(RatingService, "save_rating_to_rater_dir", fail_save)

    with pytest.raises(OSError, match="atomic write failed"):
        workflow.save_and_move(1)
    assert workflow.current_index == 0
    assert workflow.current_easyqcid == "SUB001"
    assert old.exists()


def test_schema_drift_forces_read_only_but_keeps_saved_rating_visible(tmp_path) -> None:
    target = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target.mkdir(parents=True)
    saved = _module()
    saved.update({"easyqcid": "SUB001", "rater": "rater1"})
    saved["schema_version"] = 3
    saved["scores"]["1"]["num_"] = "Reject,Accept"
    saved["scores"]["1"]["value"] = "Accept"
    (target / "AnatQC-rater1-SUB001.json").write_text(
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
    saved.update({"easyqcid": "SUB001", "rater": "rater1"})
    saved["schema_version"] = 3
    saved["scores"]["1"]["num_"] = "Reject,Accept"
    saved["scores"]["1"]["value"] = "Accept"
    (target / "AnatQC-rater1-SUB001.json").write_text(
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


def test_session_watch_reason_survives_case_reason_clear(tmp_path) -> None:
    target = tmp_path / "RatingFiles" / "AnatQC" / "rater1"
    target.mkdir(parents=True)
    saved = _module(watch_mode=True)
    saved.update({"easyqcid": "SUB001", "rater": "rater1"})
    saved["schema_version"] = 3
    saved["scores"]["1"]["num_"] = "Reject,Accept"
    saved["scores"]["1"]["value"] = "Accept"
    (target / "AnatQC-rater1-SUB001.json").write_text(
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
    saved.update({"easyqcid": "SUB002", "rater": "rater1"})
    saved["schema_version"] = 3
    (target / "AnatQC-rater1-SUB002.json").write_text(
        json.dumps(saved),
        encoding="utf-8",
    )
    rating_files_before = {
        path.name: path.read_bytes() for path in sorted(target.glob("*.json"))
    }

    def fail_target_load(_path):
        raise OSError("target rating unavailable")

    monkeypatch.setattr(
        RatingService,
        "load_rating_payload",
        staticmethod(fail_target_load),
    )

    with pytest.raises(OSError, match="target rating unavailable"):
        workflow.navigate_to("SUB002", discard_changes=True)

    assert workflow.current_index == 0
    assert workflow.current_easyqcid == "SUB001"
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
    source = pd.DataFrame({"easyqcid": identities, "image": ["a", "b"]})

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
    assert workflow.current_easyqcid == "SUB001"


def test_closed_workflow_rejects_late_mutation_and_save(tmp_path) -> None:
    workflow = _workflow(tmp_path)
    workflow.close()

    with pytest.raises(QcSessionError, match="closed"):
        workflow.set_score("1", "Good")
    with pytest.raises(QcSessionError, match="closed"):
        workflow.save()
