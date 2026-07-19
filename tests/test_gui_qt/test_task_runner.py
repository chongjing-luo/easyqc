from __future__ import annotations

from threading import Event

from gui_qt.task_runner import RevisionedTaskController


def test_revisioned_task_controller_accepts_only_latest_result(qtbot):
    controller = RevisionedTaskController()
    gate = Event()
    results = []
    controller.resultReady.connect(lambda revision, result: results.append((revision, result)))

    controller.submit(1, lambda: (gate.wait(1), "stale")[1])
    controller.submit(2, lambda: "current")
    gate.set()

    qtbot.waitUntil(lambda: results == [(2, "current")], timeout=2000)
    qtbot.wait(50)
    assert results == [(2, "current")]


def test_revisioned_task_controller_surfaces_current_error(qtbot):
    controller = RevisionedTaskController()
    errors = []
    controller.errorRaised.connect(lambda revision, error: errors.append((revision, error)))

    def fail():
        raise RuntimeError("query failed")

    controller.submit(7, fail)

    qtbot.waitUntil(lambda: bool(errors), timeout=2000)
    assert errors[0][0] == 7
    assert isinstance(errors[0][1], RuntimeError)
    assert "query failed" in str(errors[0][1])


def test_cancel_discards_running_result_and_clears_busy(qtbot):
    controller = RevisionedTaskController()
    gate = Event()
    results = []
    controller.resultReady.connect(lambda revision, result: results.append((revision, result)))

    controller.submit(1, lambda: (gate.wait(1), "late")[1])
    assert controller.busy
    controller.cancel()
    gate.set()
    qtbot.wait(50)

    assert not controller.busy
    assert results == []
