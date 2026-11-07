# -*- coding: utf-8 -*-
"""Deterministic throttle for durable runtime flushes."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class PersistenceGateSnapshot:
    interval_seconds: float
    last_flush_at: float | None
    flush_count: int
    dirty: bool


class PersistenceGate:
    """Track dirty state and decide when an expensive saver should run.

    In-memory state may be updated on every event.  The gate only throttles the
    durable flush.  ``force=True`` is intended for lifecycle boundaries such as
    pause, completion and shutdown.
    """

    def __init__(self, interval_seconds: float, *, clock: Clock | None = None) -> None:
        self.interval_seconds = max(0.0, float(interval_seconds or 0.0))
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._last_flush_at: float | None = None
        self._flush_count = 0
        self._dirty = False

    def mark_dirty(self) -> None:
        with self._lock:
            self._dirty = True

    def should_flush(self, *, force: bool = False) -> bool:
        with self._lock:
            if not self._dirty and not force:
                return False
            if force or self.interval_seconds <= 0.0 or self._last_flush_at is None:
                return True
            return self._now() - self._last_flush_at >= self.interval_seconds

    def mark_flushed(self) -> None:
        with self._lock:
            self._last_flush_at = self._now()
            self._flush_count += 1
            self._dirty = False

    def snapshot(self) -> PersistenceGateSnapshot:
        with self._lock:
            return PersistenceGateSnapshot(
                interval_seconds=self.interval_seconds,
                last_flush_at=self._last_flush_at,
                flush_count=self._flush_count,
                dirty=self._dirty,
            )

    def _now(self) -> float:
        try:
            return float(self._clock())
        except (TypeError, ValueError, OverflowError):
            return time.time()


__all__ = ["PersistenceGate", "PersistenceGateSnapshot"]
