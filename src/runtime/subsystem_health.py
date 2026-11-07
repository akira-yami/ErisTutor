# -*- coding: utf-8 -*-
"""Observable health state for long-lived ERIS subsystems.

The registry is intentionally small and dependency-free.  Runtime boundaries
record success, degradation and failure here instead of swallowing exceptions
and forcing operators to infer system health from silence.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Callable, Iterator, Mapping


Clock = Callable[[], float]
ChangeSink = Callable[[Mapping[str, Any]], None]


class HealthStatus(str, Enum):
    UNKNOWN = "unknown"
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPED = "stopped"


class FailureKind(str, Enum):
    EXPECTED_DEGRADATION = "expected_degradation"
    PROVIDER_TRANSIENT = "provider_transient"
    INVALID_EXTERNAL_DATA = "invalid_external_data"
    INVARIANT_VIOLATION = "invariant_violation"
    PERSISTENCE_FAILURE = "persistence_failure"
    PROGRAMMER_ERROR = "programmer_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SubsystemHealth:
    name: str
    status: HealthStatus = HealthStatus.UNKNOWN
    last_started_at: float = 0.0
    last_success_at: float = 0.0
    last_failure_at: float = 0.0
    last_stopped_at: float = 0.0
    consecutive_failures: int = 0
    total_failures: int = 0
    restart_count: int = 0
    failure_kind: FailureKind = FailureKind.UNKNOWN
    degraded_reason: str = ""
    last_error_type: str = ""
    last_error_message: str = ""
    details: Mapping[str, Any] | None = None
    revision: int = 0

    @property
    def ready(self) -> bool:
        return self.status == HealthStatus.HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "ready": self.ready,
            "last_started_at": self.last_started_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "last_stopped_at": self.last_stopped_at,
            "consecutive_failures": self.consecutive_failures,
            "total_failures": self.total_failures,
            "restart_count": self.restart_count,
            "failure_kind": self.failure_kind.value,
            "degraded_reason": self.degraded_reason,
            "last_error_type": self.last_error_type,
            "last_error_message": self.last_error_message,
            "details": dict(self.details or {}),
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class HealthEvent:
    seq: int
    timestamp: float
    subsystem: str
    status: HealthStatus
    failure_kind: FailureKind = FailureKind.UNKNOWN
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "timestamp": self.timestamp,
            "subsystem": self.subsystem,
            "status": self.status.value,
            "failure_kind": self.failure_kind.value,
            "reason": self.reason,
        }


class SubsystemHealthRegistry:
    """Thread-safe health registry with deterministic clock injection."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        on_change: ChangeSink | None = None,
        max_events: int = 128,
    ) -> None:
        self._clock = clock or time.time
        self._on_change = on_change
        self._max_events = max(16, int(max_events))
        self._lock = threading.RLock()
        self._items: dict[str, SubsystemHealth] = {}
        self._events: list[HealthEvent] = []
        self._revision = 0
        self.last_sink_error: dict[str, str] = {}

    @property
    def revision(self) -> int:
        with self._lock:
            return self._revision

    def mark_starting(self, name: str, *, details: Mapping[str, Any] | None = None) -> SubsystemHealth:
        normalized = self._name(name)
        with self._lock:
            now = self._now()
            old = self._items.get(normalized) or SubsystemHealth(name=normalized)
            item = replace(
                old,
                status=HealthStatus.STARTING,
                last_started_at=now,
                degraded_reason="",
                details=dict(details or old.details or {}),
                revision=old.revision + 1,
            )
            return self._commit_locked(item, reason="starting")

    def record_success(self, name: str, *, details: Mapping[str, Any] | None = None) -> SubsystemHealth:
        normalized = self._name(name)
        with self._lock:
            now = self._now()
            old = self._items.get(normalized) or SubsystemHealth(name=normalized)
            item = replace(
                old,
                status=HealthStatus.HEALTHY,
                last_success_at=now,
                consecutive_failures=0,
                failure_kind=FailureKind.UNKNOWN,
                degraded_reason="",
                last_error_type="",
                last_error_message="",
                details=dict(details or old.details or {}),
                revision=old.revision + 1,
            )
            return self._commit_locked(item, reason="success")

    def record_failure(
        self,
        name: str,
        error: BaseException | str,
        *,
        kind: FailureKind = FailureKind.UNKNOWN,
        degraded: bool = True,
        reason: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> SubsystemHealth:
        normalized = self._name(name)
        with self._lock:
            now = self._now()
            old = self._items.get(normalized) or SubsystemHealth(name=normalized)
            error_type = type(error).__name__ if isinstance(error, BaseException) else "Error"
            message = " ".join(str(error or "").split()).strip()[:360]
            explanation = " ".join(str(reason or message or kind.value).split()).strip()[:360]
            item = replace(
                old,
                status=HealthStatus.DEGRADED if degraded else HealthStatus.FAILED,
                last_failure_at=now,
                consecutive_failures=old.consecutive_failures + 1,
                total_failures=old.total_failures + 1,
                failure_kind=kind,
                degraded_reason=explanation,
                last_error_type=error_type,
                last_error_message=message,
                details=dict(details or old.details or {}),
                revision=old.revision + 1,
            )
            return self._commit_locked(item, failure_kind=kind, reason=explanation)

    def record_restart(self, name: str, *, reason: str = "") -> SubsystemHealth:
        normalized = self._name(name)
        with self._lock:
            now = self._now()
            old = self._items.get(normalized) or SubsystemHealth(name=normalized)
            item = replace(
                old,
                status=HealthStatus.STARTING,
                last_started_at=now,
                restart_count=old.restart_count + 1,
                degraded_reason=" ".join(str(reason or "restart").split())[:360],
                revision=old.revision + 1,
            )
            return self._commit_locked(item, reason=item.degraded_reason)

    def mark_stopped(self, name: str, *, reason: str = "") -> SubsystemHealth:
        normalized = self._name(name)
        with self._lock:
            now = self._now()
            old = self._items.get(normalized) or SubsystemHealth(name=normalized)
            item = replace(
                old,
                status=HealthStatus.STOPPED,
                last_stopped_at=now,
                degraded_reason=" ".join(str(reason or "").split())[:360],
                revision=old.revision + 1,
            )
            return self._commit_locked(item, reason=reason or "stopped")

    def get(self, name: str) -> SubsystemHealth:
        normalized = self._name(name)
        with self._lock:
            return self._items.get(normalized) or SubsystemHealth(name=normalized)

    def snapshot(self, *, include_events: bool = True) -> dict[str, Any]:
        with self._lock:
            items = [item.to_dict() for item in sorted(self._items.values(), key=lambda value: value.name)]
            statuses = {item.status for item in self._items.values()}
            if HealthStatus.FAILED in statuses:
                overall = HealthStatus.FAILED
            elif HealthStatus.DEGRADED in statuses:
                overall = HealthStatus.DEGRADED
            elif self._items and statuses <= {HealthStatus.HEALTHY, HealthStatus.STOPPED}:
                overall = HealthStatus.HEALTHY
            elif HealthStatus.STARTING in statuses:
                overall = HealthStatus.STARTING
            else:
                overall = HealthStatus.UNKNOWN
            payload: dict[str, Any] = {
                "schema_version": 1,
                "revision": self._revision,
                "overall": overall.value,
                "subsystems": items,
                "last_sink_error": dict(self.last_sink_error),
            }
            if include_events:
                payload["events"] = [event.to_dict() for event in self._events[-self._max_events :]]
            return payload

    @contextmanager
    def boundary(
        self,
        name: str,
        *,
        kind: FailureKind = FailureKind.UNKNOWN,
        degraded: bool = True,
        success_details: Mapping[str, Any] | None = None,
    ) -> Iterator[None]:
        """Record a process/provider boundary while preserving exception flow."""
        self.mark_starting(name)
        try:
            yield
        except BaseException as exc:
            self.record_failure(name, exc, kind=kind, degraded=degraded)
            raise
        else:
            self.record_success(name, details=success_details)

    def _commit_locked(
        self,
        item: SubsystemHealth,
        *,
        failure_kind: FailureKind = FailureKind.UNKNOWN,
        reason: str = "",
    ) -> SubsystemHealth:
        self._items[item.name] = item
        self._revision += 1
        self._events.append(
            HealthEvent(
                seq=self._revision,
                timestamp=self._now(),
                subsystem=item.name,
                status=item.status,
                failure_kind=failure_kind,
                reason=" ".join(str(reason or "").split()).strip()[:360],
            )
        )
        self._events = self._events[-self._max_events :]
        sink = self._on_change
        if sink is not None:
            try:
                sink(self.snapshot(include_events=False))
            except Exception as exc:
                # The observer must never make the observed subsystem fail, but
                # the failure remains inspectable instead of vanishing.
                self.last_sink_error = {
                    "error": type(exc).__name__,
                    "message": str(exc)[:240],
                    "subsystem": item.name,
                }
        return item

    def _now(self) -> float:
        return max(0.0, float(self._clock()))

    @staticmethod
    def _name(value: str) -> str:
        normalized = ".".join(part for part in str(value or "").strip().lower().split(".") if part)
        if not normalized:
            raise ValueError("subsystem name is required")
        return normalized[:120]


__all__ = [
    "FailureKind",
    "HealthEvent",
    "HealthStatus",
    "SubsystemHealth",
    "SubsystemHealthRegistry",
]
