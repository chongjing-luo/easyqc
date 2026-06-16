"""Tests for core.event_bus — typed publish/subscribe layer (P1-B, AC-10, ADR-002)."""

from core.event_bus import EventBus, Event, EventType


def test_event_types_are_distinct_enums() -> None:
    # The architecture contract names these six event types.
    names = {member.name for member in EventType}
    assert names == {
        "PROJECT_CHANGED", "MODULES_CHANGED", "SETTINGS_SAVED",
        "RATING_SAVED", "RATINGS_LOADED", "SUBJECTS_CHANGED",
    }


def test_event_is_frozen_and_hashable() -> None:
    event = Event(type=EventType.PROJECT_CHANGED, source="ProjectService")
    assert event.data is None
    # frozen dataclass is hashable -> usable as dict key / set member
    assert len({event, event}) == 1
    try:
        event.source = "other"  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("Event must be frozen (immutable)")


def test_subscribe_and_emit_invokes_callback_with_event() -> None:
    bus = EventBus()
    received: list[Event] = []
    bus.subscribe(EventType.MODULES_CHANGED, received.append)

    bus.emit(Event(type=EventType.MODULES_CHANGED, source="ProjectService",
                   data={"module": "Anat"}))

    assert len(received) == 1
    assert received[0].type is EventType.MODULES_CHANGED
    assert received[0].source == "ProjectService"
    assert received[0].data == {"module": "Anat"}


def test_unsubscribe_stops_callback() -> None:
    bus = EventBus()
    received: list[Event] = []

    def on_event(event: Event) -> None:
        received.append(event)

    bus.subscribe(EventType.PROJECT_CHANGED, on_event)
    bus.unsubscribe(EventType.PROJECT_CHANGED, on_event)

    bus.emit(Event(type=EventType.PROJECT_CHANGED, source="X"))

    assert received == []


def test_one_handler_exception_does_not_break_others() -> None:
    """AC-4: a failing handler must be isolated; other handlers still run.
    The failure is logged, never silently swallowed."""
    bus = EventBus()
    calls: list[str] = []

    def good_before(_e: Event) -> None:
        calls.append("before")

    def bad(_e: Event) -> None:
        calls.append("bad")
        raise RuntimeError("boom")

    def good_after(_e: Event) -> None:
        calls.append("after")

    bus.subscribe(EventType.RATING_SAVED, good_before)
    bus.subscribe(EventType.RATING_SAVED, bad)
    bus.subscribe(EventType.RATING_SAVED, good_after)

    bus.emit(Event(type=EventType.RATING_SAVED, source="RatingService"))

    # all three invoked in subscription order, despite bad raising
    assert calls == ["before", "bad", "after"]


def test_emit_unsubscribed_type_is_noop() -> None:
    """AC-5: emitting a type nobody subscribed to must not crash."""
    bus = EventBus()
    bus.emit(Event(type=EventType.SUBJECTS_CHANGED, source="TableService"))


def test_event_types_dispatch_isolates_subscribers() -> None:
    """A subscriber for one type must not be called when another type fires."""
    bus = EventBus()
    project_calls: list[Event] = []
    module_calls: list[Event] = []
    bus.subscribe(EventType.PROJECT_CHANGED, project_calls.append)
    bus.subscribe(EventType.MODULES_CHANGED, module_calls.append)

    bus.emit(Event(type=EventType.PROJECT_CHANGED, source="X"))

    assert len(project_calls) == 1
    assert module_calls == []
