from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from core.command_output import (
    CommandOutputBatch,
    CommandOutputEvent,
    CommandOutputStatus,
    ViewerExecutionContext,
)
from gui_qt.command_output_panel import QtCommandOutputPanel


class _FakeOutputProvider:
    def __init__(self, status: CommandOutputStatus) -> None:
        self.status = status
        self.events: list[CommandOutputEvent] = []
        self.calls: list[int] = []
        self.failures: list[Exception] = []
        self.truncated = False

    def add(self, *events: CommandOutputEvent) -> None:
        self.events.extend(events)

    def command_output_since(self, after_sequence: int) -> CommandOutputBatch:
        self.calls.append(after_sequence)
        if self.failures:
            raise self.failures.pop(0)
        latest_sequence = max(
            (event.sequence for event in self.events),
            default=after_sequence,
        )
        return CommandOutputBatch(
            events=tuple(
                event
                for event in self.events
                if event.sequence > after_sequence
            ),
            next_sequence=latest_sequence,
            truncated=self.truncated,
            status=self.status,
        )


def _status(
    log_path: Path | None = None,
    *,
    enabled: bool = True,
    warning: str | None = None,
) -> CommandOutputStatus:
    return CommandOutputStatus(
        session_id="20260814_120000_42",
        file_logging_enabled=enabled,
        session_log_path=log_path,
        warning_message=warning,
    )


def _event(
    sequence: int,
    kind: str,
    text: str,
    *,
    execution_id: str = "C001",
    returncode: int | None = None,
) -> CommandOutputEvent:
    return CommandOutputEvent(
        sequence=sequence,
        timestamp=datetime(2026, 8, 14, 12, 3, 4, 123000, tzinfo=timezone.utc),
        execution_id=execution_id,
        kind=kind,
        text=text,
        context=ViewerExecutionContext("AnatQC", "rater1", "SUB001", 0),
        returncode=returncode,
    )


def _stopped_panel(qtbot, provider, **kwargs) -> QtCommandOutputPanel:
    panel = QtCommandOutputPanel(provider, **kwargs)
    qtbot.addWidget(panel)
    panel.timer.stop()
    return panel


def test_panel_starts_200ms_timer_and_collects_while_collapsed(qtbot) -> None:
    provider = _FakeOutputProvider(_status())
    provider.add(_event(1, "stdout", "collected while collapsed"))
    panel = QtCommandOutputPanel(provider)
    qtbot.addWidget(panel)
    changes: list[bool] = []
    panel.expandedChanged.connect(changes.append)

    assert panel.timer.isActive()
    assert panel.timer.interval() == 200
    panel.timer.setInterval(60_000)
    panel.set_expanded(False)
    assert panel.body_widget.isHidden()
    assert panel.timer.isActive()

    panel.timer.timeout.emit()

    assert provider.calls == [0]
    assert "collected while collapsed" in panel.output_edit.toPlainText()
    assert panel.last_sequence == 1
    assert panel.timer.isActive()

    panel.set_expanded(False)
    panel.toggle_button.setFocus()
    qtbot.keyClick(panel.toggle_button, Qt.Key_Space)

    assert panel.expanded
    assert changes == [False, True]
    assert panel.timer.isActive()


def test_panel_formats_one_plain_chronological_timeline_and_running_count(
    qtbot,
) -> None:
    provider = _FakeOutputProvider(_status())
    provider.add(
        _event(1, "start", "module=AnatQC rater=rater1 easyqcid=SUB001 index=0"),
        _event(2, "command", "viewer '<scan>&$HOME'", execution_id="C001"),
        _event(3, "stdout", "外部 OUT 文本\t不翻译", execution_id="C001"),
        _event(
            4,
            "start",
            "module=AnatQC rater=rater2 easyqcid=SUB002 index=1",
            execution_id="C002",
        ),
        _event(5, "stderr", "warning <b>literal</b>", execution_id="C002"),
        _event(6, "warning", "persistence warning", execution_id="C002"),
        _event(7, "exit", "code=7", execution_id="C001", returncode=7),
    )
    panel = _stopped_panel(qtbot, provider)

    panel.refresh_output()

    lines = panel.output_edit.toPlainText().splitlines()
    assert len(lines) == 7
    assert [
        prefix in line
        for prefix, line in zip(
            ("START", "CMD", "OUT", "START", "ERR", "WARN", "EXIT"),
            lines,
        )
    ] == [True] * 7
    assert all("[12:03:04.123]" in line for line in lines)
    assert "[C001]" in lines[0]
    assert "[C002]" in lines[3]
    assert "viewer '<scan>&$HOME'" in lines[1]
    assert "外部 OUT 文本\t不翻译" in lines[2]
    assert "warning <b>literal</b>" in lines[4]
    assert panel.output_edit.isReadOnly()
    assert panel.running_label.text().endswith("1")


def test_semantic_blocks_have_distinct_foregrounds_and_plain_text_prefixes(
    qtbot,
) -> None:
    provider = _FakeOutputProvider(_status())
    provider.add(
        _event(1, "command", "viewer scan.nii.gz"),
        _event(2, "stdout", "loaded volume"),
        _event(3, "stderr", "optional surface missing"),
        _event(4, "exit", "code=2", returncode=2),
    )
    panel = _stopped_panel(qtbot, provider)

    panel.refresh_output()

    document = panel.output_edit.document()
    foregrounds = []
    for block_number in range(4):
        block = document.findBlockByNumber(block_number)
        fragment = block.begin().fragment()
        assert fragment.isValid()
        foregrounds.append(fragment.charFormat().foreground().color().name())

    assert len(set(foregrounds)) == 4
    assert [
        prefix in line
        for prefix, line in zip(
            ("CMD", "OUT", "ERR", "EXIT"),
            panel.output_edit.toPlainText().splitlines(),
        )
    ] == [True] * 4


def test_panel_keeps_exactly_newest_5000_blocks_without_touching_log(
    qtbot,
    tmp_path,
) -> None:
    log_path = tmp_path / "complete-session.log"
    log_path.write_text("complete disk history\n", encoding="utf-8")
    provider = _FakeOutputProvider(_status(log_path))
    provider.add(
        *(
            _event(index, "stdout", f"record-{index:04d}")
            for index in range(1, 5008)
        )
    )
    panel = _stopped_panel(qtbot, provider)

    panel.refresh_output()

    displayed = panel.output_edit.toPlainText().splitlines()
    assert panel.output_edit.document().maximumBlockCount() == 5000
    assert panel.output_edit.document().blockCount() == 5000
    assert len(displayed) == 5000
    assert "record-0008" in displayed[0]
    assert "record-5007" in displayed[-1]
    assert log_path.read_text(encoding="utf-8") == "complete disk history\n"


def test_refresh_follows_bottom_but_preserves_manual_scroll_position(
    qtbot,
    qapp,
) -> None:
    provider = _FakeOutputProvider(_status())
    provider.add(
        *(
            _event(index, "stdout", f"scroll-{index:03d}")
            for index in range(1, 121)
        )
    )
    panel = _stopped_panel(qtbot, provider)
    panel.resize(680, 220)
    panel.show()
    panel.refresh_output()
    qapp.processEvents()
    scrollbar = panel.output_edit.verticalScrollBar()

    assert scrollbar.maximum() > 0
    assert scrollbar.value() == scrollbar.maximum()

    manual_position = max(1, scrollbar.maximum() // 3)
    scrollbar.setValue(manual_position)
    provider.add(
        *(
            _event(index, "stdout", f"scroll-{index:03d}")
            for index in range(121, 126)
        )
    )
    panel.refresh_output()
    qapp.processEvents()

    assert scrollbar.value() == manual_position

    scrollbar.setValue(scrollbar.maximum())
    provider.add(_event(126, "stdout", "follow-bottom"))
    panel.refresh_output()
    qapp.processEvents()

    assert scrollbar.value() == scrollbar.maximum()


def test_copy_all_and_clear_display_use_only_the_display_cursor(qtbot) -> None:
    provider = _FakeOutputProvider(_status())
    provider.add(_event(1, "stdout", "first displayed"))
    panel = _stopped_panel(qtbot, provider)
    panel.refresh_output()
    displayed = panel.output_edit.toPlainText()

    qtbot.mouseClick(panel.copy_button, Qt.LeftButton)

    assert QApplication.clipboard().text() == displayed

    provider.add(_event(2, "stderr", "arrived before clear"))
    qtbot.mouseClick(panel.clear_button, Qt.LeftButton)

    assert provider.calls == [0, 1]
    assert panel.last_sequence == 2
    assert panel.clear_baseline == 2
    assert panel.output_edit.toPlainText() == ""

    provider.add(_event(3, "stdout", "arrived after clear"))
    panel.refresh_output()

    assert provider.calls[-1] == 2
    assert "arrived after clear" in panel.output_edit.toPlainText()
    assert "arrived before clear" not in panel.output_edit.toPlainText()


def test_open_log_uses_system_service_and_reports_recoverable_failure(
    qtbot,
    tmp_path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "viewer.log"
    provider = _FakeOutputProvider(_status(log_path))
    panel = QtCommandOutputPanel(provider)
    qtbot.addWidget(panel)
    panel.timer.setInterval(60_000)
    opened: list[Path] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        lambda url: opened.append(Path(url.toLocalFile())) or True,
    )
    panel.refresh_output()

    assert panel.open_log_button.isEnabled()
    qtbot.mouseClick(panel.open_log_button, Qt.LeftButton)
    assert opened == [log_path]

    monkeypatch.setattr(QDesktopServices, "openUrl", lambda _url: False)
    qtbot.mouseClick(panel.open_log_button, Qt.LeftButton)

    assert "无法打开" in panel.status_label.text()
    assert panel.timer.isActive()


def test_degraded_and_query_failures_stay_visible_and_timer_recovers(qtbot) -> None:
    provider = _FakeOutputProvider(
        _status(
            enabled=False,
            warning="persistent storage unavailable: permission denied",
        )
    )
    provider.truncated = True
    provider.failures.append(RuntimeError("temporary query failure"))
    panel = QtCommandOutputPanel(provider)
    qtbot.addWidget(panel)
    panel.timer.setInterval(60_000)

    panel.refresh_output()

    assert "temporary query failure" in panel.status_label.text()
    assert panel.timer.isActive()
    assert panel.last_sequence == 0
    assert provider.calls == [0]

    provider.add(_event(1, "warning", "memory capture continues"))
    panel.refresh_output()

    assert "persistent storage unavailable: permission denied" in (
        panel.status_label.text()
    )
    assert "较早" in panel.status_label.text()
    assert "memory capture continues" in panel.output_edit.toPlainText()
    assert not panel.open_log_button.isEnabled()
    assert panel.timer.isActive()
    assert provider.calls == [0, 0]


def test_public_controls_and_output_are_accessible_and_keyboard_focusable(
    qtbot,
) -> None:
    panel = _stopped_panel(qtbot, _FakeOutputProvider(_status()))

    assert panel.accessibleName()
    for control in (
        panel.toggle_button,
        panel.copy_button,
        panel.clear_button,
        panel.open_log_button,
        panel.output_edit,
        panel.status_label,
        panel.running_label,
    ):
        assert control.accessibleName(), type(control).__name__
    for control in (
        panel.toggle_button,
        panel.copy_button,
        panel.clear_button,
        panel.open_log_button,
        panel.output_edit,
    ):
        assert control.focusPolicy() != Qt.NoFocus, type(control).__name__
