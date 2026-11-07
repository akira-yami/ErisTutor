# -*- coding: utf-8 -*-
"""Unified diagnostics snapshot for the composed ERIS runtime."""
from __future__ import annotations
from core.di import resolve_dependency

import time
from typing import Any

from runtime.readiness import RuntimeReadinessEvaluator
from runtime.runtime_slo import RuntimeSLOTracker


class RuntimeDiagnostics:
    """Read-only projection of runtime boundaries and long-lived commitments."""

    def __init__(self, owner: Any, *, clock=None, readiness_evaluator=None) -> None:
        self.owner = owner
        self._clock = clock or time.time
        self._readiness = resolve_dependency(readiness_evaluator, lambda: RuntimeReadinessEvaluator())
        policy = getattr(getattr(owner, "runtime_policy_settings", None), "observability", None)
        self._slo = RuntimeSLOTracker(settings=policy, clock=self._clock)

    @staticmethod
    def _snapshot(target: Any, method: str = "snapshot", **kwargs) -> dict[str, Any]:
        callback = getattr(target, method, None)
        if not callable(callback):
            return {}
        try:
            value = callback(**kwargs)
        except Exception as exc:
            return {"error": type(exc).__name__, "message": str(exc)[:240]}
        return dict(value) if isinstance(value, dict) else {"value": value}

    def snapshot(self, *, include_events: bool = False) -> dict[str, Any]:
        owner = self.owner
        engagement = getattr(owner, "ai_engagement_runtime", None)
        engagement_registry = getattr(engagement, "registry", None)
        payload = {
            "schema_version": 7,
            "generated_at": max(0.0, float(self._clock())),
            "bootstrap": {
                "completed_phases": list(getattr(owner, "bootstrap_completed_phases", ()) or ()),
                "ai_runtime_enabled": bool(getattr(owner, "ai_runtime_enabled", False)),
            },
            "health": self._snapshot(
                getattr(owner, "subsystem_health_registry", None),
                include_events=include_events,
            ),
            "supervisor": self._snapshot(getattr(owner, "subsystem_supervisor", None)),
            "boundaries": {
                "transport": self._snapshot(getattr(owner, "transport_boundary", None)),
                "protocol": self._snapshot(getattr(owner, "incoming_dispatcher", None)),
                "loop": self._snapshot(getattr(owner, "loop_guard", None)),
                "chess_bridge": self._snapshot(
                    getattr(owner, "chess_bridge_service", None), method="diagnostics"
                ),
            },
            "engagement": (
                engagement.diagnostics(now=self._clock())
                if callable(getattr(engagement, "diagnostics", None))
                else {
                    "kernel": self._snapshot(engagement_registry),
                    "planner": (
                        engagement.planner_context(now=self._clock())
                        if callable(getattr(engagement, "planner_context", None))
                        else {}
                    ),
                    "profiles": list(getattr(getattr(engagement, "profiles", None), "ids", lambda: ())()),
                    "adapters": list(getattr(getattr(engagement, "adapters", None), "ids", lambda: ())()),
                }
            ),
        }
        journal = getattr(owner, "_ai_engagement_snapshot_journal", None)
        if isinstance(payload.get("engagement"), dict):
            payload["engagement"]["snapshot_journal"] = self._snapshot(journal, method="diagnostics")
        readiness = self._readiness.evaluate(payload)
        payload["readiness"] = readiness.to_dict()
        self._slo.record(readiness, now=payload["generated_at"])
        payload["slo"] = self._slo.snapshot(now=payload["generated_at"])
        return payload


__all__ = ["RuntimeDiagnostics"]
