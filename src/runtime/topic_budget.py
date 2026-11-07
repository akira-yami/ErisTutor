# -*- coding: utf-8 -*-
"""Small thread-safe rolling budgets for repeated background topics.

PDF annotation, page 20
-----------------------
P2 asks for per-topic Tutor budgets rather than a cooldown alone. A cooldown
prevents immediate duplicates, but a stubborn topic can still consume quota all
day. ``TopicBudget`` therefore combines a rolling call cap with explicit
``record`` semantics: failed/admission-rejected work is not charged, while a
successful remote result is.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class TopicBudget:
    max_calls: int
    window_seconds: float = 3600.0
    _events: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def _prune_locked(self, topic_key: str, now: float) -> deque[float]:
        events = self._events[str(topic_key or "default")]
        threshold = now - max(1.0, float(self.window_seconds))
        while events and events[0] < threshold:
            events.popleft()
        return events

    def allow(self, topic_key: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else float(now)
        with self._lock:
            events = self._prune_locked(topic_key, current)
            return len(events) < max(1, int(self.max_calls))

    def record(self, topic_key: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else float(now)
        with self._lock:
            events = self._prune_locked(topic_key, current)
            events.append(current)

    def remaining(self, topic_key: str, *, now: float | None = None) -> int:
        current = time.time() if now is None else float(now)
        with self._lock:
            events = self._prune_locked(topic_key, current)
            return max(0, max(1, int(self.max_calls)) - len(events))


__all__ = ["TopicBudget"]
