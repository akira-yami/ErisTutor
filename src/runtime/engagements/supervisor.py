# -*- coding: utf-8 -*-
"""Fair, deterministic supervisor for managed engagement consumers."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Iterable

from runtime.subsystem_health import FailureKind, SubsystemHealthRegistry

from .consumer import ConsumerPollResult, EngagementEventConsumer


@dataclass(frozen=True, slots=True)
class ConsumerTickReport:
    polled: int
    processed: int
    idle: int
    degraded: int
    results: tuple[ConsumerPollResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "polled": self.polled,
            "processed": self.processed,
            "idle": self.idle,
            "degraded": self.degraded,
            "results": [item.to_dict() for item in self.results],
        }


class EngagementConsumerSupervisor:
    """Poll managed consumers fairly without allowing one failure to abort a tick."""

    def __init__(
        self,
        *,
        health_registry: SubsystemHealthRegistry | None = None,
        clock=None,
        poll_budget: int = 8,
    ) -> None:
        self.health_registry = health_registry
        self._clock = clock or time.time
        self.poll_budget = max(1, int(poll_budget))
        self._consumers: dict[str, EngagementEventConsumer] = {}
        self._order: list[str] = []
        self._offset = 0
        self.tick_count = 0
        self.last_tick_at = 0.0

    def register(self, consumer: EngagementEventConsumer, *, replace: bool = False) -> None:
        key = consumer.consumer_id
        if key in self._consumers and not replace:
            raise ValueError(f"consumer already registered: {key}")
        if key not in self._consumers:
            self._order.append(key)
        self._consumers[key] = consumer

    def unregister(self, consumer_id: str) -> EngagementEventConsumer | None:
        key = str(consumer_id or "").strip()[:96]
        consumer = self._consumers.pop(key, None)
        self._order = [item for item in self._order if item != key]
        if self._order:
            self._offset %= len(self._order)
        else:
            self._offset = 0
        return consumer

    def get(self, consumer_id: str) -> EngagementEventConsumer | None:
        return self._consumers.get(str(consumer_id or "").strip()[:96])

    def ids(self) -> tuple[str, ...]:
        return tuple(self._order)

    def _next_ids(self, limit: int) -> Iterable[str]:
        if not self._order:
            return ()
        count = min(max(0, int(limit)), len(self._order))
        start = self._offset % len(self._order)
        ids = tuple(self._order[(start + index) % len(self._order)] for index in range(count))
        self._offset = (start + count) % len(self._order)
        return ids

    def tick(self, *, now: float | None = None, limit: int | None = None) -> ConsumerTickReport:
        now = self._clock() if now is None else max(0.0, float(now))
        self.tick_count += 1
        self.last_tick_at = now
        results: list[ConsumerPollResult] = []
        for consumer_id in self._next_ids(self.poll_budget if limit is None else limit):
            consumer = self._consumers.get(consumer_id)
            if consumer is None:
                continue
            try:
                result = consumer.poll_once(now=now)
            except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                result = ConsumerPollResult(
                    consumer_id=consumer_id,
                    status="supervisor_error",
                    error=f"{type(exc).__name__}: {exc}"[:240],
                )
                if self.health_registry is not None:
                    self.health_registry.record_failure(
                        f"engagement.consumer.{consumer_id}",
                        exc,
                        kind=FailureKind.PROGRAMMER_ERROR,
                        degraded=True,
                        reason="managed engagement consumer escaped its poll boundary",
                    )
            results.append(result)
        processed_statuses = {"processed", "duplicate_suppressed", "replayed"}
        idle_statuses = {"idle", "backoff"}
        processed = sum(item.status in processed_statuses for item in results)
        idle = sum(item.status in idle_statuses for item in results)
        degraded = len(results) - processed - idle
        return ConsumerTickReport(
            polled=len(results), processed=processed, idle=idle,
            degraded=degraded, results=tuple(results),
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "registered": len(self._consumers),
            "consumer_ids": list(self._order),
            "poll_budget": self.poll_budget,
            "tick_count": self.tick_count,
            "last_tick_at": self.last_tick_at,
            "consumers": {
                key: consumer.snapshot()
                for key, consumer in sorted(self._consumers.items())
            },
        }


__all__ = ["ConsumerTickReport", "EngagementConsumerSupervisor"]
