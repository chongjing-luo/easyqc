"""Reusable read-only Qt presentation for viewer command output."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFontDatabase,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.command_output import CommandOutputBatch, CommandOutputEvent


_KIND_PREFIXES = {
    "start": "START",
    "command": "CMD",
    "stdout": "OUT",
    "stderr": "ERR",
    "exit": "EXIT",
    "warning": "WARN",
}
_KIND_FOREGROUNDS = {
    "start": "#35684A",
    "command": "#596779",
    "stdout": "#2B607A",
    "stderr": "#9A5B16",
    "exit": "#884A4A",
    "warning": "#806A20",
}
_DEFAULT_FOREGROUND = "#354052"


class QtCommandOutputPanel(QWidget):
    """Render incremental Core output without owning processes or files.

    Input is one provider with ``command_output_since(int)``. Output is a
    bounded read-only timeline and explicit Qt actions. Side effects are
    limited to a GUI timer, clipboard writes, and system open-log requests.
    Process control, persistence, QSettings, and historical search are split
    responsibilities outside this widget.
    """

    expandedChanged = Signal(bool)

    def __init__(
        self,
        query_provider: Any,
        *,
        poll_interval_ms: int = 200,
        maximum_blocks: int = 5000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        query = getattr(query_provider, "command_output_since", None)
        if not callable(query):
            raise TypeError(
                "command output provider must expose command_output_since(int)"
            )
        if (
            not isinstance(poll_interval_ms, int)
            or isinstance(poll_interval_ms, bool)
            or poll_interval_ms < 1
        ):
            raise ValueError("poll_interval_ms must be a positive integer")
        if (
            not isinstance(maximum_blocks, int)
            or isinstance(maximum_blocks, bool)
            or maximum_blocks < 1
        ):
            raise ValueError("maximum_blocks must be a positive integer")

        self._query_provider = query_provider
        self._last_sequence = 0
        self._clear_baseline = 0
        self._expanded = True
        self._log_path = None
        self._running_execution_ids: set[str] = set()

        self.setObjectName("commandOutputPanel")
        self.setAccessibleName("命令输出面板")
        self._build_ui(maximum_blocks)

        self.timer = QTimer(self)
        self.timer.setObjectName("commandOutputPollTimer")
        self.timer.setInterval(poll_interval_ms)
        self.timer.timeout.connect(self.refresh_output)
        self.timer.start()

    def _build_ui(self, maximum_blocks: int) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.header_widget = QWidget(self)
        self.header_widget.setObjectName("commandOutputHeader")
        header = QHBoxLayout(self.header_widget)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        self.toggle_button = QToolButton(self.header_widget)
        self.toggle_button.setObjectName("commandOutputToggle")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setChecked(True)
        self.toggle_button.setText("收起")
        self.toggle_button.setAccessibleName("收起命令输出")

        self.title_label = QLabel("命令输出", self.header_widget)
        self.title_label.setObjectName("commandOutputTitle")
        self.title_label.setProperty("role", "sectionTitle")
        self.title_label.setAccessibleName("命令输出标题")

        self.running_label = QLabel("运行中 0", self.header_widget)
        self.running_label.setObjectName("commandOutputRunningCount")
        self.running_label.setProperty("role", "secondary")
        self.running_label.setAccessibleName("运行中的查看器命令数量")

        self.copy_button = QPushButton("复制全部", self.header_widget)
        self.copy_button.setObjectName("commandOutputCopyAll")
        self.copy_button.setAccessibleName("复制全部命令输出")

        self.clear_button = QPushButton("清空显示", self.header_widget)
        self.clear_button.setObjectName("commandOutputClear")
        self.clear_button.setAccessibleName("清空当前命令输出显示")

        self.open_log_button = QPushButton("打开日志", self.header_widget)
        self.open_log_button.setObjectName("commandOutputOpenLog")
        self.open_log_button.setAccessibleName("使用系统应用打开完整命令日志")
        self.open_log_button.setEnabled(False)

        for control in (
            self.toggle_button,
            self.copy_button,
            self.clear_button,
            self.open_log_button,
        ):
            control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        header.addWidget(self.toggle_button)
        header.addWidget(self.title_label)
        header.addWidget(self.running_label)
        header.addStretch(1)
        header.addWidget(self.copy_button)
        header.addWidget(self.clear_button)
        header.addWidget(self.open_log_button)
        layout.addWidget(self.header_widget)

        self.body_widget = QWidget(self)
        self.body_widget.setObjectName("commandOutputBody")
        body = QVBoxLayout(self.body_widget)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)

        self.status_label = QLabel("等待命令输出", self.body_widget)
        self.status_label.setObjectName("commandOutputStatus")
        self.status_label.setProperty("role", "secondary")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("命令输出和文件日志状态")

        self.output_edit = QPlainTextEdit(self.body_widget)
        self.output_edit.setObjectName("commandOutputTimeline")
        self.output_edit.setAccessibleName("查看器命令输出时间线")
        self.output_edit.setReadOnly(True)
        self.output_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.output_edit.setFont(
            QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        )
        self.output_edit.document().setMaximumBlockCount(maximum_blocks)
        self.output_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.output_edit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByKeyboard
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )

        body.addWidget(self.status_label)
        body.addWidget(self.output_edit, 1)
        layout.addWidget(self.body_widget, 1)

        self.setFocusProxy(self.output_edit)
        self.toggle_button.toggled.connect(self.set_expanded)
        self.copy_button.clicked.connect(self.copy_all)
        self.clear_button.clicked.connect(self.clear_display)
        self.open_log_button.clicked.connect(self.open_log)

    @property
    def last_sequence(self) -> int:
        return self._last_sequence

    @property
    def clear_baseline(self) -> int:
        return self._clear_baseline

    @property
    def expanded(self) -> bool:
        return self._expanded

    @Slot(bool)
    def set_expanded(self, expanded: bool) -> None:
        normalized = bool(expanded)
        changed = normalized != self._expanded
        self._expanded = normalized
        if self.toggle_button.isChecked() != normalized:
            blocker = QSignalBlocker(self.toggle_button)
            self.toggle_button.setChecked(normalized)
            del blocker
        self.body_widget.setVisible(normalized)
        if normalized:
            self.toggle_button.setText("收起")
            self.toggle_button.setAccessibleName("收起命令输出")
        else:
            self.toggle_button.setText("展开")
            self.toggle_button.setAccessibleName("展开命令输出")
        if changed:
            self.expandedChanged.emit(normalized)

    @Slot()
    def refresh_output(self) -> None:
        self._query_and_render()

    def _query_and_render(self) -> bool:
        try:
            batch = self._query_provider.command_output_since(
                self._last_sequence
            )
            if not isinstance(batch, CommandOutputBatch):
                raise TypeError(
                    "command_output_since must return CommandOutputBatch"
                )
            if batch.next_sequence < self._last_sequence:
                raise ValueError("command output cursor moved backwards")
            self._append_events(batch.events)
            self._last_sequence = batch.next_sequence
            self._apply_status(batch)
        except Exception as exc:
            self.status_label.setText(f"命令输出刷新失败：{exc}")
            self.status_label.setProperty("role", "error")
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            return False
        return True

    def _append_events(self, events: tuple[CommandOutputEvent, ...]) -> None:
        if not events:
            return
        scrollbar = self.output_edit.verticalScrollBar()
        was_at_bottom = scrollbar.value() >= scrollbar.maximum()
        previous_position = scrollbar.value()
        rendered_events = []
        for event in events:
            if not isinstance(event, CommandOutputEvent):
                raise TypeError("command output events must be CommandOutputEvent")
            rendered_events.append((event.kind, self._format_event(event)))
            if event.kind == "start":
                self._running_execution_ids.add(event.execution_id)
            elif event.kind == "exit":
                self._running_execution_ids.discard(event.execution_id)

        self._insert_formatted_lines(rendered_events)
        if was_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(min(previous_position, scrollbar.maximum()))
        self.running_label.setText(
            f"运行中 {len(self._running_execution_ids)}"
        )

    def _insert_formatted_lines(
        self,
        rendered_events: list[tuple[str, str]],
    ) -> None:
        document = self.output_edit.document()
        cursor = QTextCursor(document)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        needs_block = not document.isEmpty()
        formats: dict[str, QTextCharFormat] = {}
        cursor.beginEditBlock()
        try:
            for kind, line in rendered_events:
                if needs_block:
                    cursor.insertBlock()
                text_format = formats.get(kind)
                if text_format is None:
                    text_format = QTextCharFormat()
                    text_format.setForeground(
                        QColor(
                            _KIND_FOREGROUNDS.get(
                                kind,
                                _DEFAULT_FOREGROUND,
                            )
                        )
                    )
                    formats[kind] = text_format
                cursor.insertText(line, text_format)
                needs_block = True
        finally:
            cursor.endEditBlock()

    @staticmethod
    def _format_event(event: CommandOutputEvent) -> str:
        timestamp = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
        prefix = _KIND_PREFIXES.get(event.kind, event.kind.upper())
        return (
            f"[{timestamp}] [{event.execution_id}] "
            f"{prefix:<5} {event.text}"
        )

    def _apply_status(self, batch: CommandOutputBatch) -> None:
        status = batch.status
        self._log_path = (
            status.session_log_path
            if status.file_logging_enabled
            else None
        )
        log_available = self._log_path is not None
        self.open_log_button.setEnabled(log_available)

        messages = []
        if not log_available:
            messages.append(
                status.warning_message or "文件日志不可用；内存显示仍在继续"
            )
        elif status.warning_message:
            messages.append(status.warning_message)
        else:
            messages.append("文件日志可用")
        if batch.truncated:
            messages.append("较早的内存输出已不可用")
        self.status_label.setText(" · ".join(messages))
        self.status_label.setProperty(
            "role",
            (
                "error"
                if (not log_available or status.warning_message)
                else "secondary"
            ),
        )
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    @Slot()
    def copy_all(self) -> None:
        QApplication.clipboard().setText(self.output_edit.toPlainText())

    @Slot()
    def clear_display(self) -> None:
        if not self._query_and_render():
            return
        self._clear_baseline = self._last_sequence
        self.output_edit.clear()

    @Slot()
    def open_log(self) -> None:
        if self._log_path is None:
            self.status_label.setText("文件日志不可用")
            self.open_log_button.setEnabled(False)
            return
        try:
            opened = QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._log_path))
            )
        except Exception as exc:
            self.status_label.setText(f"无法打开命令日志：{exc}")
            self.status_label.setProperty("role", "error")
            return
        if not opened:
            self.status_label.setText("无法打开命令日志")
            self.status_label.setProperty("role", "error")


__all__ = ["QtCommandOutputPanel"]
