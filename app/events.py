"""Lightweight event system: a bounded in-memory log plus a transition
tracker that turns "component X is in state Y this cycle" into one event
per state *change*, never one event per poll cycle.
"""

import threading
import time
from collections import deque

MAX_EVENTS_IN_MEMORY = 500


def make_event(event_type, severity, message, metadata=None):
    return {
        "timestamp": time.time(),
        "type": event_type,
        "severity": severity,
        "message": message,
        "metadata": metadata or {},
    }


class EventLog:
    """Bounded, thread-safe. Written by the poll loop, read by HTTP
    handler threads - a simple lock is enough given how infrequently
    events are added (only on state transitions, not every poll).
    """

    def __init__(self, history=None):
        self._lock = threading.Lock()
        self._events = deque(maxlen=MAX_EVENTS_IN_MEMORY)
        self._history = history

    def add(self, event):
        with self._lock:
            self._events.append(event)
        if self._history is not None:
            self._history.insert_event(event)

    def add_many(self, events):
        for e in events:
            self.add(e)

    def recent(self, limit=100, min_severity=None):
        order = {"info": 0, "warning": 1, "critical": 2}
        with self._lock:
            items = list(self._events)
        if min_severity:
            floor = order.get(min_severity, 0)
            items = [e for e in items if order.get(e["severity"], 0) >= floor]
        return list(reversed(items))[:limit]


class TransitionTracker:
    """Tracks named boolean/enum conditions and emits an event only when a
    condition's value actually changes between polls. `check(name, value,
    ...)` returns an event dict on a real transition, else None.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last = {}

    def check(self, name, value, on_enter=None, on_leave=None):
        """`value` is any hashable state (e.g. True/False, or a status
        string like "healthy"/"warning"/"critical"). `on_enter`/`on_leave`
        are (event_type, severity, message_fn) tuples used when entering or
        leaving `value`'s "true-ish" state - callers that just want plain
        transition detection can ignore the return convenience and build
        their own event from the (old, new) pair instead.
        """
        with self._lock:
            old = self._last.get(name)
            changed = name not in self._last or old != value
            self._last[name] = value
        if not changed:
            return None
        return {"name": name, "old": old, "new": value}

    def reset(self, name=None):
        with self._lock:
            if name is None:
                self._last.clear()
            else:
                self._last.pop(name, None)
