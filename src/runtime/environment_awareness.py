# -*- coding: utf-8 -*-
"""Canonical, shared environment awareness for Eris.

This module is intentionally *not* a vision system.  It fuses the structured
machine/environment state Eris already owns (WinEvent/UIA/IA2/telemetry +
DesktopSemanticRuntime) into one immutable projection that every cognition lane
can read:

    Deep Environment -> EnvironmentAwarenessHub -> chat / self-turn / seed / ...

VLM/screenshot interpretation stays on its existing path.  The hub never claims
pixel details and never stores raw keystrokes or clipboard payloads.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence


def _text(value: Any, limit: int = 240) -> str:
    value = " ".join(str(value or "").split())
    return value[:limit]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _clamp01(value: Any) -> float:
    return max(0.0, min(1.0, _float(value)))


def _machine_context(snapshot: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if snapshot is None:
        return payload
    to_context = getattr(snapshot, "to_machine_context", None)
    if callable(to_context):
        try:
            raw = to_context() or {}
            if isinstance(raw, Mapping):
                payload.update(dict(raw))
        except Exception:
            pass
    if isinstance(snapshot, Mapping):
        payload.update(dict(snapshot))
    return payload


def _event_to_dict(event: Any) -> dict[str, Any]:
    if isinstance(event, Mapping):
        raw = dict(event)
    else:
        to_dict = getattr(event, "to_dict", None)
        if callable(to_dict):
            try:
                raw = dict(to_dict() or {})
            except Exception:
                raw = {}
        else:
            raw = {
                key: getattr(event, key, None)
                for key in (
                    "timestamp", "kind", "category", "semantic", "attention_level",
                    "confidence", "novelty", "importance", "source", "app",
                    "object_ref", "detail", "evidence_refs", "event_key",
                )
            }
    return {
        "timestamp": _float(raw.get("timestamp"), 0.0),
        "kind": _text(raw.get("kind", raw.get("event_type", "")), 72),
        "category": _text(raw.get("category", ""), 72),
        "semantic": bool(raw.get("semantic", True)),
        "attention_level": max(0, min(4, int(_float(raw.get("attention_level"), 0.0)))),
        "confidence": round(_clamp01(raw.get("confidence")), 3),
        "novelty": round(_clamp01(raw.get("novelty")), 3),
        "importance": round(_clamp01(raw.get("importance")), 3),
        "source": _text(raw.get("source", ""), 72),
        "app": _text(raw.get("app", raw.get("process_name", "")), 120),
        "object_ref": _text(raw.get("object_ref", ""), 180),
        "detail": _text(raw.get("detail", raw.get("title", "")), 320),
        "evidence_refs": [
            _text(item, 140) for item in tuple(raw.get("evidence_refs", ()) or ()) if _text(item, 140)
        ][:10],
        "event_key": _text(raw.get("event_key", raw.get("event_id", "")), 140),
    }


@dataclass(frozen=True, slots=True)
class EnvironmentAwarenessSnapshot:
    """Public-safe structured understanding of the user's current computer state."""

    revision: int = 0
    captured_at: float = 0.0
    situation_timestamp: float = 0.0
    source: str = "deep_environment"
    focused_app: str = ""
    focused_process: str = ""
    focused_object: str = ""
    activity: str = "unknown"
    task_hint: str = "unknown"
    phase: str = "unknown"
    last_action: str = "none"
    last_outcome: str = "unknown"
    attention_state: str = "unknown"
    interruption_cost: float = 0.0
    busy_score: float = 0.0
    confidence: float = 0.0
    semantic_delta: bool = False
    wake_recommended: bool = False
    max_attention_level: int = 0
    evidence_refs: tuple[str, ...] = ()
    recent_events: tuple[dict[str, Any], ...] = ()
    focus_from_current_telemetry: bool = False
    signature: str = ""

    @property
    def has_focus_identity(self) -> bool:
        return bool(self.focused_app or self.focused_process or self.focused_object)

    @property
    def has_semantic_task(self) -> bool:
        return any(
            value and value.casefold() not in {"unknown", "none"}
            for value in (self.activity, self.task_hint, self.phase, self.last_action, self.last_outcome)
        )

    def age_seconds(self, *, now: float | None = None) -> float:
        current = float(now if now is not None else time.time())
        stamp = float(self.situation_timestamp or self.captured_at or 0.0)
        return max(0.0, current - stamp) if stamp > 0.0 else 9999.0

    def is_fresh(self, *, now: float | None = None, max_age_seconds: float = 120.0) -> bool:
        return self.age_seconds(now=now) <= max(1.0, float(max_age_seconds))

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        age = self.age_seconds(now=now)
        return {
            "schema": "environment_awareness.v1",
            "revision": int(self.revision),
            "captured_at": float(self.captured_at),
            "situation_timestamp": float(self.situation_timestamp),
            "age_seconds": round(age, 2),
            "fresh": bool(age <= 120.0),
            "source": self.source,
            "focused_app": self.focused_app,
            "focused_process": self.focused_process,
            "focused_object": self.focused_object,
            "activity": self.activity,
            "task_hint": self.task_hint,
            "phase": self.phase,
            "last_action": self.last_action,
            "last_outcome": self.last_outcome,
            "attention_state": self.attention_state,
            "interruption_cost": round(self.interruption_cost, 3),
            "busy_score": round(self.busy_score, 3),
            "confidence": round(self.confidence, 3),
            "semantic_delta": bool(self.semantic_delta),
            "wake_recommended": bool(self.wake_recommended),
            "max_attention_level": int(self.max_attention_level),
            "evidence_refs": list(self.evidence_refs),
            "recent_events": [dict(item) for item in self.recent_events[-8:]],
            "focus_from_current_telemetry": bool(self.focus_from_current_telemetry),
            "signature": self.signature,
            "epistemic_contract": (
                "Structured local environment only: app/process/window/task/activity hints may be used according to "
                "freshness/confidence. This is not VLM/pixel vision and does not prove exact visible pixel content or user intent."
            ),
        }

    def prompt_context(self, *, now: float | None = None) -> str:
        age = self.age_seconds(now=now)
        lines = [
            "environment_awareness:",
            f"- revision={self.revision}; age={age:.1f}s; confidence={self.confidence:.2f}; source={self.source}",
            f"- app={self.focused_app or '<unknown>'}; process={self.focused_process or '<unknown>'}",
            f"- focused_object={self.focused_object or '<unknown>'}",
            f"- activity={self.activity}; task_hint={self.task_hint}; phase={self.phase}",
            f"- last_action={self.last_action}; last_outcome={self.last_outcome}",
            f"- attention={self.attention_state}; busy={self.busy_score:.2f}; interruption_cost={self.interruption_cost:.2f}",
            f"- semantic_delta={str(self.semantic_delta).lower()}; wake_recommended={str(self.wake_recommended).lower()}",
            "environment_contract:",
            "- This is shared structured Deep Environment state for chat, cognition and seed generation.",
            "- It may ground app/window/task/activity claims when fresh and confident.",
            "- It is NOT screenshot/VLM evidence. Never infer exact pixels, colors, code text or visual layout from it.",
            "- Task/activity are conservative semantic hints; do not turn them into certainty about the user's private intent.",
        ]
        if self.recent_events:
            lines.append("- recent_environment_events:")
            for event in self.recent_events[-5:]:
                lines.append(
                    "  - kind={kind}; level={level}; conf={conf:.2f}; app={app}; detail={detail}".format(
                        kind=event.get("kind") or "unknown",
                        level=int(event.get("attention_level", 0) or 0),
                        conf=float(event.get("confidence", 0.0) or 0.0),
                        app=event.get("app") or "<unknown>",
                        detail=event.get("detail") or "<none>",
                    )
                )
        return "\n".join(lines)

    def grounding_facts(self, *, now: float | None = None) -> tuple[dict[str, object], ...]:
        current = float(now if now is not None else time.time())
        age = self.age_seconds(now=current)
        facts: list[dict[str, object]] = []
        if self.has_focus_identity:
            parts: list[str] = []
            if self.focused_app:
                parts.append(f"app={self.focused_app[:120]}")
            if self.focused_process:
                parts.append(f"process={self.focused_process[:120]}")
            if self.focused_object:
                parts.append(f"window_or_object={self.focused_object[:220]}")
            focus_conf = 0.97 if self.focus_from_current_telemetry else max(0.72, self.confidence)
            facts.append({
                "id": f"deep_environment:focus:r{self.revision}",
                "source": "runtime_state",
                "claim": "Deep Environment current focus: " + "; ".join(parts) + ".",
                "confidence": min(0.98, focus_conf),
                "grounded": True,
                "stale": bool(age > 30.0 and not self.focus_from_current_telemetry),
                "kind": "deep_environment_focus",
                "captured_at": self.captured_at or current,
                "tags": ["runtime_state", "deep_environment", "environment_awareness", "current_focus"],
            })
        if self.has_semantic_task and age <= 120.0:
            parts = []
            for label, value in (
                ("activity", self.activity), ("task_hint", self.task_hint), ("phase", self.phase),
                ("last_action", self.last_action), ("last_outcome", self.last_outcome),
                ("attention", self.attention_state),
            ):
                if value and value.casefold() not in {"unknown", "none"}:
                    parts.append(f"{label}={value[:180]}")
            confidence = max(0.58, self.confidence)
            if age > 30.0:
                confidence *= 0.74
            facts.append({
                "id": f"deep_environment:task:r{self.revision}",
                "source": "runtime_state",
                "claim": (
                    "Deep Environment current task state: " if age <= 30.0
                    else "Deep Environment recent task state (may have changed): "
                ) + "; ".join(parts) + ".",
                "confidence": min(0.96, confidence),
                "grounded": True,
                "stale": bool(age > 30.0),
                "kind": "deep_environment_task",
                "captured_at": self.situation_timestamp or self.captured_at or current,
                "tags": ["runtime_state", "deep_environment", "environment_awareness", "task_state"],
            })
        return tuple(facts)


class EnvironmentAwarenessHub:
    """Single-writer-ish projection shared by all higher cognition lanes."""

    def __init__(self, host: Any, *, logger: Any | None = None, clock=time.time) -> None:
        self.host = host
        self.logger = logger if logger is not None else getattr(host, "logger", None)
        self.clock = clock
        self._lock = threading.RLock()
        self._snapshot = EnvironmentAwarenessSnapshot()
        self._revision = 0

    def _resolve_situation(self, situation: Any | None) -> Any | None:
        if situation is not None:
            return situation
        worker = getattr(self.host, "semantic_environment_worker", None)
        latest = getattr(worker, "latest_situation", None) if worker is not None else None
        return latest if latest is not None else getattr(self.host, "last_user_situation", None)

    @staticmethod
    def _stable_signature(payload: Mapping[str, Any]) -> str:
        stable = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.blake2s(stable.encode("utf-8", errors="ignore"), digest_size=10).hexdigest()

    def refresh(
        self,
        *,
        machine_snapshot: Any | None = None,
        situation: Any | None = None,
        environment_events: Sequence[Any] | None = None,
        now: float | None = None,
        source: str = "deep_environment",
    ) -> EnvironmentAwarenessSnapshot:
        current = float(now if now is not None else self.clock())
        resolved = self._resolve_situation(situation)
        machine = _machine_context(machine_snapshot)

        focused_app = _text(machine.get("active_app"), 120)
        focused_process = _text(machine.get("active_process"), 120)
        focused_object = _text(machine.get("active_window_title"), 240)
        current_focus = bool(focused_app or focused_process or focused_object)

        if resolved is not None:
            focused_app = focused_app or _text(getattr(resolved, "focused_app", ""), 120)
            focused_process = focused_process or _text(getattr(resolved, "focused_process", ""), 120)
            focused_object = focused_object or _text(getattr(resolved, "focused_object", ""), 240)
            activity = _text(getattr(resolved, "activity", "unknown"), 180) or "unknown"
            task_hint = _text(getattr(resolved, "task_hint", "unknown"), 240) or "unknown"
            phase = _text(getattr(resolved, "phase", "unknown"), 120) or "unknown"
            last_action = _text(getattr(resolved, "last_action", "none"), 180) or "none"
            last_outcome = _text(getattr(resolved, "last_outcome", "unknown"), 180) or "unknown"
            attention_state = _text(getattr(resolved, "attention_state", "unknown"), 120) or "unknown"
            interruption_cost = _clamp01(getattr(resolved, "interruption_cost", 0.0))
            busy_score = _clamp01(getattr(resolved, "busy_score", 0.0))
            confidence = _clamp01(getattr(resolved, "confidence", 0.0))
            semantic_delta = bool(getattr(resolved, "semantic_delta", False))
            wake_recommended = bool(getattr(resolved, "wake_recommended", False))
            max_attention_level = max(0, min(4, int(_float(getattr(resolved, "max_attention_level", 0), 0.0))))
            situation_timestamp = _float(getattr(resolved, "timestamp", 0.0), 0.0)
            evidence_refs = tuple(
                dict.fromkeys(
                    _text(item, 140)
                    for item in tuple(getattr(resolved, "evidence_refs", ()) or ())
                    if _text(item, 140)
                )
            )[:16]
            raw_events: Iterable[Any] = (
                environment_events if environment_events is not None
                else tuple(getattr(resolved, "recent_events", ()) or ())
            )
        else:
            activity, task_hint, phase = "unknown", "unknown", "unknown"
            last_action, last_outcome, attention_state = "none", "unknown", "unknown"
            interruption_cost = busy_score = confidence = 0.0
            semantic_delta = wake_recommended = False
            max_attention_level = 0
            situation_timestamp = current if current_focus else 0.0
            evidence_refs = ()
            raw_events = environment_events or ()

        # Current telemetry is authoritative for focus identity even before the
        # semantic worker's next event batch finishes.
        if current_focus:
            confidence = max(confidence, 0.94)
            # Fresh foreground telemetry refreshes focus identity only. Do not
            # make an old semantic task look current merely because the user is
            # still in the same app/window.
            if resolved is None and situation_timestamp <= 0.0:
                situation_timestamp = current

        events = tuple(_event_to_dict(item) for item in tuple(raw_events or ())[-8:])
        events = tuple(item for item in events if item.get("kind") or item.get("detail") or item.get("event_key"))
        stable_payload = {
            "focused_app": focused_app.casefold(),
            "focused_process": focused_process.casefold(),
            "focused_object": focused_object.casefold(),
            "activity": activity.casefold(),
            "task_hint": task_hint.casefold(),
            "phase": phase.casefold(),
            "last_action": last_action.casefold(),
            "last_outcome": last_outcome.casefold(),
            "attention_state": attention_state.casefold(),
            "semantic_delta": semantic_delta,
            "wake_recommended": wake_recommended,
            "max_attention_level": max_attention_level,
            "event_tail": [
                (item.get("event_key") or item.get("kind") or "", item.get("detail") or "")
                for item in events[-3:]
            ],
        }
        signature = self._stable_signature(stable_payload)

        with self._lock:
            if signature != self._snapshot.signature:
                self._revision += 1
            snap = EnvironmentAwarenessSnapshot(
                revision=self._revision,
                captured_at=current,
                situation_timestamp=situation_timestamp,
                source=_text(source, 72) or "deep_environment",
                focused_app=focused_app,
                focused_process=focused_process,
                focused_object=focused_object,
                activity=activity,
                task_hint=task_hint,
                phase=phase,
                last_action=last_action,
                last_outcome=last_outcome,
                attention_state=attention_state,
                interruption_cost=interruption_cost,
                busy_score=busy_score,
                confidence=confidence,
                semantic_delta=semantic_delta,
                wake_recommended=wake_recommended,
                max_attention_level=max_attention_level,
                evidence_refs=evidence_refs,
                recent_events=events,
                focus_from_current_telemetry=current_focus,
                signature=signature,
            )
            self._snapshot = snap
        try:
            setattr(self.host, "last_environment_awareness", snap)
        except Exception:
            pass

        # WorldState v1 is the canonical consumer of environment state.  Keep
        # EnvironmentAwarenessSnapshot as the compatibility/sensor projection,
        # then publish it once into the shared world model.  No VLM call occurs
        # here.
        try:
            from runtime.world_state import ensure_world_state_service
            ensure_world_state_service(self.host).ingest_environment(
                snap.to_dict(now=current),
                now=current,
                source=snap.source or source,
            )
        except Exception as exc:
            if self.logger is not None:
                try:
                    self.logger.debug("[world state] environment ingest failed: %s", exc)
                except Exception:
                    pass
        return snap

    def snapshot(self, *, now: float | None = None, max_age_seconds: float = 180.0) -> EnvironmentAwarenessSnapshot:
        current = float(now if now is not None else self.clock())
        with self._lock:
            snap = self._snapshot
        # Keep identity available for continuity, but explicitly degrade stale
        # semantic certainty so no downstream lane can mistake old task state for now.
        if not snap.is_fresh(now=current, max_age_seconds=max_age_seconds):
            return replace(
                snap,
                confidence=min(snap.confidence, 0.42),
                semantic_delta=False,
                wake_recommended=False,
            )
        return snap


def ensure_environment_awareness_hub(host: Any) -> EnvironmentAwarenessHub:
    existing = getattr(host, "environment_awareness_hub", None)
    if isinstance(existing, EnvironmentAwarenessHub):
        return existing
    hub = EnvironmentAwarenessHub(host, logger=getattr(host, "logger", None))
    try:
        setattr(host, "environment_awareness_hub", hub)
    except Exception:
        pass
    return hub


__all__ = [
    "EnvironmentAwarenessHub",
    "EnvironmentAwarenessSnapshot",
    "ensure_environment_awareness_hub",
]
