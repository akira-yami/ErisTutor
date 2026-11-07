# -*- coding: utf-8 -*-
"""Sliding readiness SLO and incident ledger for the ERIS runtime.

A readiness verdict answers whether the runtime is usable *now*. This module
answers the operator's more useful follow-up: whether it has been reliable over
an interval, how long the current degradation has lasted, and which incident
keeps reopening like a badly written support ticket.
"""
from __future__ import annotations
from core.di import resolve_dependency

import hashlib
import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

from runtime.policy_settings import RuntimeObservabilitySettings
from runtime.readiness import ReadinessStatus, RuntimeReadiness


_SEVERITY = {
    ReadinessStatus.READY.value: 0,
    ReadinessStatus.STARTING.value: 1,
    ReadinessStatus.DEGRADED.value: 2,
    ReadinessStatus.FAILED.value: 3,
}


def _status(value: Any) -> str:
    raw = value.value if isinstance(value, ReadinessStatus) else str(value or "").strip().lower()
    return raw if raw in _SEVERITY else ReadinessStatus.DEGRADED.value


def _reasons(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(dict.fromkeys(str(item).strip()[:160] for item in value if str(item).strip()))[:16]
    return ()


@dataclass(frozen=True, slots=True)
class ReadinessSample:
    timestamp: float
    status: str
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"timestamp": self.timestamp, "status": self.status, "reasons": list(self.reasons)}


@dataclass(frozen=True, slots=True)
class RuntimeIncident:
    incident_id: str
    started_at: float
    status: str
    reasons: tuple[str, ...] = ()
    highest_severity: str = ReadinessStatus.DEGRADED.value
    updated_at: float = 0.0
    ended_at: float = 0.0
    transitions: int = 0

    @property
    def open(self) -> bool:
        return self.ended_at <= 0.0

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        current = time.time() if now is None else float(now)
        payload = asdict(self)
        payload["open"] = self.open
        payload["duration_seconds"] = round(
            max(0.0, (current if self.open else self.ended_at) - self.started_at), 3
        )
        payload["reasons"] = list(self.reasons)
        return payload


class RuntimeSLOTracker:
    """Thread-safe, bounded readiness history with incident transitions."""

    def __init__(
        self,
        *,
        settings: RuntimeObservabilitySettings | None = None,
        clock=None,
    ) -> None:
        self.settings = resolve_dependency(settings, lambda: RuntimeObservabilitySettings())
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._samples: list[ReadinessSample] = []
        self._incidents: list[RuntimeIncident] = []
        self._open_incident_id = ""
        self._transition_count = 0

    def record(
        self,
        readiness: RuntimeReadiness | Mapping[str, Any],
        *,
        now: float | None = None,
    ) -> ReadinessSample:
        now = self._clock() if now is None else max(0.0, float(now))
        if isinstance(readiness, RuntimeReadiness):
            status = readiness.status.value
            reasons = tuple(readiness.reasons) + tuple(readiness.critical_failures) + tuple(readiness.degraded_components)
        else:
            status = _status(readiness.get("status"))
            reasons = (
                _reasons(readiness.get("reasons"))
                + _reasons(readiness.get("critical_failures"))
                + _reasons(readiness.get("degraded_components"))
            )
        sample = ReadinessSample(timestamp=now, status=status, reasons=tuple(dict.fromkeys(reasons))[:16])
        with self._lock:
            previous = self._samples[-1] if self._samples else None
            if previous is not None and previous.status != sample.status:
                self._transition_count += 1
            self._samples.append(sample)
            self._prune_locked(now)
            self._update_incident_locked(sample)
            return sample

    def snapshot(self, *, now: float | None = None) -> dict[str, Any]:
        now = self._clock() if now is None else max(0.0, float(now))
        with self._lock:
            self._prune_locked(now)
            durations = {key: 0.0 for key in _SEVERITY}
            window_start = max(0.0, now - self.settings.slo_window_seconds)
            samples = [item for item in self._samples if item.timestamp >= window_start]
            if samples:
                for index, sample in enumerate(samples):
                    start = max(window_start, sample.timestamp)
                    end = now if index + 1 == len(samples) else min(now, samples[index + 1].timestamp)
                    durations[sample.status] += max(0.0, end - start)
                observed = max(0.0, now - max(window_start, samples[0].timestamp))
                if observed <= 0.0:
                    observed = 1.0
                    durations[samples[-1].status] = 1.0
            else:
                observed = 0.0
            ready_ratio = durations[ReadinessStatus.READY.value] / observed if observed > 0.0 else 0.0
            current = samples[-1] if samples else None
            open_incident = next(
                (item for item in reversed(self._incidents) if item.incident_id == self._open_incident_id and item.open),
                None,
            )
            current_degraded_seconds = (
                max(0.0, now - open_incident.started_at) if open_incident is not None else 0.0
            )
            breaches: list[str] = []
            if observed > 0.0 and ready_ratio < self.settings.minimum_ready_ratio:
                breaches.append("ready_ratio_below_target")
            if current_degraded_seconds > self.settings.maximum_degraded_seconds:
                breaches.append("current_degradation_too_long")
            return {
                "schema_version": 1,
                "window_seconds": self.settings.slo_window_seconds,
                "observed_seconds": round(observed, 3),
                "sample_count": len(samples),
                "transition_count": self._transition_count,
                "current_status": current.status if current is not None else "unknown",
                "ready_ratio": round(ready_ratio, 6),
                "target_ready_ratio": self.settings.minimum_ready_ratio,
                "status_seconds": {key: round(value, 3) for key, value in durations.items()},
                "current_degraded_seconds": round(current_degraded_seconds, 3),
                "maximum_degraded_seconds": self.settings.maximum_degraded_seconds,
                "breached": bool(breaches),
                "breaches": breaches,
                "open_incident": open_incident.to_dict(now=now) if open_incident is not None else {},
                "recent_incidents": [
                    item.to_dict(now=now) for item in self._incidents[-8:]
                ],
            }

    def incidents(self) -> tuple[RuntimeIncident, ...]:
        with self._lock:
            return tuple(self._incidents)

    def _update_incident_locked(self, sample: ReadinessSample) -> None:
        current = next(
            (item for item in reversed(self._incidents) if item.incident_id == self._open_incident_id and item.open),
            None,
        )
        if sample.status == ReadinessStatus.READY.value:
            if current is not None:
                index = self._incidents.index(current)
                self._incidents[index] = replace(
                    current,
                    updated_at=sample.timestamp,
                    ended_at=sample.timestamp,
                    transitions=current.transitions + 1,
                )
            self._open_incident_id = ""
            return

        if current is None:
            raw = f"{sample.timestamp:.6f}|{sample.status}|{'|'.join(sample.reasons)}"
            incident_id = "inc_" + hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:12]
            incident = RuntimeIncident(
                incident_id=incident_id,
                started_at=sample.timestamp,
                status=sample.status,
                reasons=sample.reasons,
                highest_severity=sample.status,
                updated_at=sample.timestamp,
            )
            self._incidents.append(incident)
            self._open_incident_id = incident_id
        else:
            highest = (
                sample.status
                if _SEVERITY[sample.status] > _SEVERITY[current.highest_severity]
                else current.highest_severity
            )
            merged_reasons = tuple(dict.fromkeys(current.reasons + sample.reasons))[:16]
            index = self._incidents.index(current)
            self._incidents[index] = replace(
                current,
                status=sample.status,
                reasons=merged_reasons,
                highest_severity=highest,
                updated_at=sample.timestamp,
                transitions=current.transitions + (1 if current.status != sample.status else 0),
            )
        self._incidents = self._incidents[-self.settings.max_incidents:]

    def _prune_locked(self, now: float) -> None:
        cutoff = max(0.0, now - self.settings.slo_window_seconds)
        self._samples = [item for item in self._samples if item.timestamp >= cutoff]
        self._samples = self._samples[-self.settings.slo_max_samples:]
        self._incidents = self._incidents[-self.settings.max_incidents:]


__all__ = [
    "ReadinessSample",
    "RuntimeIncident",
    "RuntimeSLOTracker",
]
