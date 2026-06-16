"""Typed publish/subscribe event bus (ADR-002, AC-10).

Single-threaded, in-process, zero third-party dependency. Core layer — must NOT
import tkinter or any GUI module.

Lifecycle contract:
- Subscribers subscribe in their owner's ``__init__``.
- Subscribers MUST unsubscribe in their owner's ``destroy`` / teardown, otherwise
  the callback reference leaks (the bus holds strong references by design; weak
  references are deferred to a later hardening pass to keep this first version
  simple).

Error isolation:
- ``emit`` calls every subscriber registered for the event type, in subscription
  order. If a handler raises, the exception is logged via ``utils.logger`` and
  the remaining handlers still run. Exceptions are NEVER silently swallowed
  (AGENTS.md: fail loud) — they surface in the log with a full traceback.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable

from utils.logger import log_exception


class EventType(Enum):
    """Canonical event types emitted across the service layer."""

    PROJECT_CHANGED = auto()   # project loaded / created / removed
    MODULES_CHANGED = auto()   # module added / removed / updated
    SETTINGS_SAVED = auto()    # settings persisted
    RATING_SAVED = auto()      # a single rating was saved
    RATINGS_LOADED = auto()    # full rating aggregation completed
    SUBJECTS_CHANGED = auto()  # subject table updated


@dataclass(frozen=True)
class Event:
    """An immutable event payload.

    ``data`` is an optional dict; keep it small and serializable so handlers can
    inspect it without re-deriving state. Frozen so an Event is hashable and can
    be used as a dict key / set member.
    """

    type: EventType
    source: str
    data: dict[str, Any] | None = None


Subscriber = Callable[[Event], None]


class EventBus:
    """A typed event bus.

    Single-threaded use only (tkinter main loop). No locking; ``emit`` may
    recurse safely if a handler emits another event.
    """

    def __init__(self) -> None:
        self._subscribers: dict[EventType, list[Subscriber]] = {}

    def subscribe(self, event_type: EventType, callback: Subscriber) -> None:
        """Register ``callback`` for ``event_type``. Idempotent: subscribing the
        same callback twice means it fires twice."""
        self._subscribers.setdefault(event_type, []).append(callback)

    def unsubscribe(self, event_type: EventType, callback: Subscriber) -> None:
        """Remove ``callback`` for ``event_type``. No-op if not registered.
        Uses identity comparison (same as list.remove) and is safe to call from
        a handler or during teardown."""
        callbacks = self._subscribers.get(event_type)
        if not callbacks:
            return
        # remove all identity-equal entries (subscribe may have added dupes)
        self._subscribers[event_type] = [cb for cb in callbacks if cb is not callback]
        if not self._subscribers[event_type]:
            del self._subscribers[event_type]

    def emit(self, event: Event) -> None:
        """Dispatch ``event`` to every subscriber registered for its type, in
        subscription order. A raising handler is logged and isolated; the rest
        still run."""
        for callback in list(self._subscribers.get(event.type, [])):
            try:
                callback(event)
            except Exception:
                # log with full traceback (exc_info populated inside except)
                log_exception(
                    f"EventBus handler raised for {event.type.name} "
                    f"(source={event.source})",
                    module="EventBus",
                    show_popup=False,
                )


__all__ = ["EventBus", "Event", "EventType"]
