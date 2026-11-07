# -*- coding: utf-8 -*-
"""Canonical shared WorldState for Eris.

WorldState is the one read-model all cognition lanes consume.  Sensors publish
facts; semantic workers publish interpretations; conversation/cognition/system
state are folded into the same immutable snapshot.  Lane-specific projections
keep prompts compact without letting each lane rebuild its own version of the
world.

VLM remains deliberately outside this module.  A VLM result may later be added
as another evidence source, but WorldState never invokes it.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Sequence

from runtime.attention_router import AttentionDecision, AttentionRouter
from runtime.behavior_proposals import ensure_proposal_bus
from runtime.intent_model import IntentFrame, normalize_intent
from runtime.world_event_journal import AppendOnlyWorldJournal


def _text(value: Any, limit: int = 320) -> str:
    return " ".join(str(value or "").split())[:limit]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _clamp01(value: Any) -> float:
    return max(0.0, min(1.0, _float(value)))


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            raw = to_dict()
            return dict(raw) if isinstance(raw, Mapping) else {}
        except Exception:
            return {}
    return {}


@dataclass(frozen=True, slots=True)
class WorldFact:
    key: str
    value: Any
    source: str
    confidence: float
    observed_at: float
    expires_at: float
    evidence_refs: tuple[str, ...] = ()

    def is_stale(self, now: float | None = None) -> bool:
        current = float(now if now is not None else time.time())
        return bool(self.expires_at > 0.0 and current > self.expires_at)

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "stale": self.is_stale(now),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class WorldInterpretation:
    key: str
    value: Any
    source: str
    confidence: float
    observed_at: float
    expires_at: float
    based_on: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()

    def is_stale(self, now: float | None = None) -> bool:
        current = float(now if now is not None else time.time())
        return bool(self.expires_at > 0.0 and current > self.expires_at)

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "stale": self.is_stale(now),
            "based_on": list(self.based_on),
            "evidence_refs": list(self.evidence_refs),
        }


@dataclass(frozen=True, slots=True)
class EnvironmentWorldState:
    revision: int = 0
    captured_at: float = 0.0
    facts: tuple[WorldFact, ...] = ()
    interpretations: tuple[WorldInterpretation, ...] = ()
    recent_events: tuple[dict[str, Any], ...] = ()
    confidence: float = 0.0
    semantic_delta: bool = False
    signature: str = ""

    def fact(self, key: str, *, now: float | None = None) -> WorldFact | None:
        for item in self.facts:
            if item.key == key and not item.is_stale(now):
                return item
        return None

    def interpretation(self, key: str, *, now: float | None = None) -> WorldInterpretation | None:
        for item in self.interpretations:
            if item.key == key and not item.is_stale(now):
                return item
        return None

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "captured_at": self.captured_at,
            "confidence": round(self.confidence, 3),
            "semantic_delta": self.semantic_delta,
            "facts": [item.to_dict(now=now) for item in self.facts],
            "interpretations": [item.to_dict(now=now) for item in self.interpretations],
            "recent_events": [dict(item) for item in self.recent_events[-12:]],
            "signature": self.signature,
        }


@dataclass(frozen=True, slots=True)
class ConversationWorldState:
    last_user_text: str = ""
    last_assistant_text: str = ""
    topic: str = ""
    user_intent: Mapping[str, Any] = field(default_factory=dict)
    unresolved: str = ""
    social_state: str = "unknown"
    turn_revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_user_text": self.last_user_text,
            "last_assistant_text": self.last_assistant_text,
            "topic": self.topic,
            "user_intent": dict(self.user_intent),
            "unresolved": self.unresolved,
            "social_state": self.social_state,
            "turn_revision": self.turn_revision,
        }


@dataclass(frozen=True, slots=True)
class CognitionWorldState:
    active_concerns: tuple[dict[str, Any], ...] = ()
    mental_material: tuple[dict[str, Any], ...] = ()
    current_goal: str = ""
    attention: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_concerns": [dict(item) for item in self.active_concerns],
            "mental_material": [dict(item) for item in self.mental_material],
            "current_goal": self.current_goal,
            "attention": self.attention,
        }


@dataclass(frozen=True, slots=True)
class SystemWorldState:
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    sensor_health: Mapping[str, str] = field(default_factory=dict)
    resource_budget: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "capabilities": dict(self.capabilities),
            "sensor_health": dict(self.sensor_health),
            "resource_budget": dict(self.resource_budget),
        }


@dataclass(frozen=True, slots=True)
class WorldStateSnapshot:
    revision: int = 0
    captured_at: float = 0.0
    environment: EnvironmentWorldState = field(default_factory=EnvironmentWorldState)
    conversation: ConversationWorldState = field(default_factory=ConversationWorldState)
    cognition: CognitionWorldState = field(default_factory=CognitionWorldState)
    system: SystemWorldState = field(default_factory=SystemWorldState)
    attention: Mapping[str, Any] = field(default_factory=dict)
    journal_tail: tuple[dict[str, Any], ...] = ()

    def to_dict(self, *, now: float | None = None) -> dict[str, Any]:
        return {
            "schema": "eris.world_state.v1",
            "revision": self.revision,
            "captured_at": self.captured_at,
            "environment": self.environment.to_dict(now=now),
            "conversation": self.conversation.to_dict(),
            "cognition": self.cognition.to_dict(),
            "system": self.system.to_dict(),
            "attention": dict(self.attention),
            "journal_tail": [dict(item) for item in self.journal_tail],
            "contract": (
                "Facts are sensor/runtime observations. Interpretations are derived semantic hypotheses. "
                "Do not promote an interpretation to a sensor fact. Respect provenance, freshness and confidence."
            ),
        }

    def _env_value(self, key: str, *, now: float | None = None, interpretation: bool = False) -> Any:
        item = self.environment.interpretation(key, now=now) if interpretation else self.environment.fact(key, now=now)
        return getattr(item, "value", "") if item is not None else ""

    def environment_compatibility_view(self, *, now: float | None = None) -> dict[str, Any]:
        """Compatibility shape for pre-WorldState callers and seed logic."""
        current = float(now if now is not None else self.captured_at or time.time())
        event_tail = [dict(item) for item in self.environment.recent_events[-8:]]
        focused_app = _text(self._env_value("environment.focused_app", now=current), 120)
        focused_process = _text(self._env_value("environment.focused_process", now=current), 120)
        focused_object = _text(self._env_value("environment.focused_object", now=current), 240)
        activity = _text(self._env_value("environment.activity", now=current, interpretation=True), 180) or "unknown"
        task_hint = _text(self._env_value("environment.task_hint", now=current, interpretation=True), 240) or "unknown"
        phase = _text(self._env_value("environment.phase", now=current, interpretation=True), 120) or "unknown"
        last_action = _text(self._env_value("environment.last_action", now=current, interpretation=True), 180) or "none"
        last_outcome = _text(self._env_value("environment.last_outcome", now=current, interpretation=True), 180) or "unknown"
        attention_state = _text(self._env_value("environment.attention_state", now=current, interpretation=True), 120) or "unknown"
        evidence_refs: list[str] = []
        for item in (*self.environment.facts, *self.environment.interpretations):
            evidence_refs.extend(item.evidence_refs)
        latest_stamp = max(
            [item.observed_at for item in (*self.environment.facts, *self.environment.interpretations)] or [0.0]
        )
        age = max(0.0, current - latest_stamp) if latest_stamp else 9999.0
        return {
            "schema": "environment_awareness.v1",
            "revision": self.environment.revision,
            "captured_at": self.environment.captured_at,
            "situation_timestamp": latest_stamp,
            "age_seconds": round(age, 2),
            "fresh": age <= 120.0,
            "source": "world_state",
            "focused_app": focused_app,
            "focused_process": focused_process,
            "focused_object": focused_object,
            "activity": activity,
            "task_hint": task_hint,
            "phase": phase,
            "last_action": last_action,
            "last_outcome": last_outcome,
            "attention_state": attention_state,
            "interruption_cost": _float(self._env_value("environment.interruption_cost", now=current, interpretation=True), 0.0),
            "busy_score": _float(self._env_value("environment.busy_score", now=current, interpretation=True), 0.0),
            "confidence": self.environment.confidence,
            "semantic_delta": self.environment.semantic_delta,
            "wake_recommended": "wake_cognition" in tuple(self.attention.get("actions", ()) or ()),
            "max_attention_level": int(_float(self._env_value("environment.max_attention_level", now=current, interpretation=True), 0.0)),
            "evidence_refs": list(dict.fromkeys(evidence_refs))[:16],
            "recent_events": event_tail,
            "signature": self.environment.signature,
            "epistemic_contract": (
                "Structured WorldState environment projection only; app/process/window facts are distinct from task/activity interpretations. "
                "This is not screenshot/VLM evidence."
            ),
        }

    def seed_context(self, *, now: float | None = None) -> dict[str, Any]:
        current = float(now if now is not None else self.captured_at or time.time())
        env = self.environment_compatibility_view(now=current)
        latest = dict(env.get("recent_events", [])[-1]) if env.get("recent_events") else {}
        concern = dict(self.cognition.active_concerns[0]) if self.cognition.active_concerns else {}
        return {
            "schema": "eris.seed_context.v1",
            "world_revision": self.revision,
            "environment_revision": env.get("revision", 0),
            "current_activity": env.get("activity", "unknown"),
            "current_task": env.get("task_hint", "unknown"),
            "phase": env.get("phase", "unknown"),
            "focused_app": env.get("focused_app", ""),
            "focused_object": env.get("focused_object", ""),
            "meaningful_change": {
                "event_key": latest.get("event_key", latest.get("kind", "")),
                "kind": latest.get("kind", ""),
                "detail": latest.get("detail", ""),
                "importance": latest.get("importance", 0.0),
                "novelty": latest.get("novelty", 0.0),
                "confidence": latest.get("confidence", env.get("confidence", 0.0)),
                "semantic_delta": bool(env.get("semantic_delta", False)),
            },
            "active_concern": concern,
            "conversation_residue": {
                "topic": self.conversation.topic,
                "last_user_text": self.conversation.last_user_text,
                "unresolved": self.conversation.unresolved,
            },
            "novelty": latest.get("novelty", 0.0),
            "attention": dict(self.attention),
            "evidence_refs": env.get("evidence_refs", []),
            "contract": (
                "SeedContext is a compact projection. Generate associations from current activity/change/concern only; "
                "never infer pixel details and never speak directly from this context."
            ),
        }

    def projection(self, lane: str, *, now: float | None = None) -> dict[str, Any]:
        lane_key = _text(lane, 64).casefold() or "generic"
        base = {
            "schema": "eris.world_state_projection.v1",
            "lane": lane_key,
            "revision": self.revision,
            "environment_awareness": self.environment_compatibility_view(now=now),
            "conversation": self.conversation.to_dict(),
            "system": self.system.to_dict(),
            "attention": dict(self.attention),
        }
        if lane_key in {"chat", "subject", "self_turn", "observer", "executive", "generic"}:
            base["cognition"] = self.cognition.to_dict()
        if lane_key == "seed":
            return {
                "schema": "eris.world_state_projection.v1",
                "lane": "seed",
                "revision": self.revision,
                "seed_context": self.seed_context(now=now),
                "environment_awareness": base["environment_awareness"],
                "conversation": base["conversation"],
            }
        return base

    def prompt_context(self, lane: str, *, now: float | None = None) -> str:
        projection = self.projection(lane, now=now)
        env = projection.get("environment_awareness", {})
        conv = projection.get("conversation", {})
        system = projection.get("system", {})
        lines = [
            "WORLD_STATE_V1:",
            f"- revision={self.revision}; lane={lane}",
            f"- environment fact: app={env.get('focused_app') or '<unknown>'}; process={env.get('focused_process') or '<unknown>'}; object={env.get('focused_object') or '<unknown>'}",
            f"- environment interpretation: activity={env.get('activity') or 'unknown'}; task={env.get('task_hint') or 'unknown'}; phase={env.get('phase') or 'unknown'}; confidence={float(env.get('confidence', 0.0) or 0.0):.2f}",
            f"- conversation: topic={conv.get('topic') or '<none>'}; intent={json.dumps(conv.get('user_intent', {}), ensure_ascii=False, separators=(',', ':'))}",
        ]
        cognition = projection.get("cognition", {}) if isinstance(projection, Mapping) else {}
        concerns = cognition.get("active_concerns", ()) if isinstance(cognition, Mapping) else ()
        materials = cognition.get("mental_material", ()) if isinstance(cognition, Mapping) else ()
        if concerns:
            lines.append("- active_concerns=" + json.dumps(list(concerns)[:12], ensure_ascii=False, separators=(",", ":"), default=str)[:10000])
        if materials:
            # Mental material là semantic artifact đã được worker tạo, không phải raw
            # chain-of-thought. Root có thể dùng làm private evidence, nhưng không
            # được copy nguyên văn thành lời public hay coi nó là fact bên ngoài.
            lines.append("- mental_material=" + json.dumps(list(materials)[:12], ensure_ascii=False, separators=(",", ":"), default=str)[:12000])
            lines.append("- mental_material_rule=private semantic material only; never expose as hidden reasoning and never promote to external fact.")
        sensors = system.get("sensor_health", {}) if isinstance(system, Mapping) else {}
        if sensors:
            lines.append("- sensor_health=" + ", ".join(f"{key}:{value}" for key, value in sorted(sensors.items())))
        lines.extend((
            "- FACT != INTERPRETATION. Do not turn task/activity inference into direct sensor fact.",
            "- Respect freshness/confidence/provenance. Stale or missing evidence must lower certainty.",
            "- Capability wording must match sensor_health: do not say 'I cannot see your computer/screen' when Deep Environment/UIA/OCR evidence is present. State the exact layer you have instead.",
            "- VLM unavailable/degraded does NOT erase structured Deep Environment or UIA/OCR awareness.",
            "- WorldState does not invoke or imply VLM/pixel vision.",
        ))
        return "\n".join(lines)


class WorldStateService:
    """Single shared read-model and projection service."""

    def __init__(self, host: Any, *, logger: Any | None = None, clock: Callable[[], float] = time.time) -> None:
        self.host = host
        self.logger = logger if logger is not None else getattr(host, "logger", None)
        self.clock = clock
        self._lock = threading.RLock()
        self.journal = AppendOnlyWorldJournal(clock=clock)
        self.attention_router = AttentionRouter()
        self._revision = 0
        self._environment = EnvironmentWorldState()
        self._conversation = ConversationWorldState()
        self._cognition = CognitionWorldState()
        self._system = SystemWorldState()
        self._attention: dict[str, Any] = {}
        self._last_environment_edge_token = ""

    @staticmethod
    def _signature(payload: Mapping[str, Any]) -> str:
        stable = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.blake2s(stable.encode("utf-8", errors="ignore"), digest_size=10).hexdigest()

    def ingest_environment(self, awareness: Any, *, now: float | None = None, source: str = "environment_awareness") -> None:
        current = float(now if now is not None else self.clock())
        raw = _mapping(awareness)
        if not raw:
            return
        observed_at = _float(raw.get("situation_timestamp", raw.get("captured_at", current)), current) or current
        focus_is_current = bool(raw.get("focus_from_current_telemetry", False))
        focus_observed_at = current if focus_is_current else observed_at
        focus_ttl = focus_observed_at + 45.0
        semantic_ttl = observed_at + 120.0
        refs = tuple(dict.fromkeys(_text(item, 180) for item in tuple(raw.get("evidence_refs", ()) or ()) if _text(item, 180)))[:16]
        focus_conf = _clamp01(raw.get("confidence", 0.0))
        if focus_is_current:
            focus_conf = max(focus_conf, 0.94)
        facts: list[WorldFact] = []
        for key, field_name in (
            ("environment.focused_app", "focused_app"),
            ("environment.focused_process", "focused_process"),
            ("environment.focused_object", "focused_object"),
        ):
            value = _text(raw.get(field_name, ""), 240)
            if value:
                facts.append(WorldFact(
                    key=key, value=value, source=_text(raw.get("source", source), 96) or source,
                    confidence=max(0.55, focus_conf), observed_at=focus_observed_at, expires_at=focus_ttl,
                    evidence_refs=refs,
                ))
        interpretations: list[WorldInterpretation] = []
        for key, field_name, fallback in (
            ("environment.activity", "activity", "unknown"),
            ("environment.task_hint", "task_hint", "unknown"),
            ("environment.phase", "phase", "unknown"),
            ("environment.last_action", "last_action", "none"),
            ("environment.last_outcome", "last_outcome", "unknown"),
            ("environment.attention_state", "attention_state", "unknown"),
            ("environment.interruption_cost", "interruption_cost", 0.0),
            ("environment.busy_score", "busy_score", 0.0),
            ("environment.max_attention_level", "max_attention_level", 0),
        ):
            value = raw.get(field_name, fallback)
            if isinstance(value, str):
                value = _text(value, 240) or fallback
            interpretations.append(WorldInterpretation(
                key=key,
                value=value,
                source="desktop_semantics",
                confidence=_clamp01(raw.get("confidence", 0.0)),
                observed_at=observed_at,
                expires_at=semantic_ttl,
                based_on=tuple(item.key for item in facts),
                evidence_refs=refs,
            ))
        recent = tuple(dict(item) for item in tuple(raw.get("recent_events", ()) or ()) if isinstance(item, Mapping))[-12:]
        env_signature = self._signature({
            "facts": [(item.key, item.value) for item in facts],
            "interpretations": [(item.key, item.value) for item in interpretations],
            "events": [(item.get("event_key", item.get("kind", "")), item.get("detail", "")) for item in recent[-4:]],
        })
        latest_event_key = ""
        if recent:
            latest = recent[-1]
            latest_event_key = _text(latest.get("event_key", latest.get("kind", "")), 180)
        edge_token = self._signature({
            "signature": env_signature,
            "upstream_revision": int(raw.get("revision", 0) or 0),
            "semantic_delta": bool(raw.get("semantic_delta", False)),
            "latest_event_key": latest_event_key,
        })
        with self._lock:
            changed = env_signature != self._environment.signature
            edge_is_new = edge_token != self._last_environment_edge_token
            if changed:
                self._revision += 1
            env_revision = int(raw.get("revision", 0) or 0) or self._environment.revision + (1 if changed else 0)
            self._environment = EnvironmentWorldState(
                revision=env_revision,
                captured_at=current,
                facts=tuple(facts),
                interpretations=tuple(interpretations),
                recent_events=recent,
                confidence=_clamp01(raw.get("confidence", 0.0)),
                semantic_delta=bool(raw.get("semantic_delta", False)),
                signature=env_signature,
            )
            if edge_is_new:
                self._last_environment_edge_token = edge_token

        attention = self.attention_router.route_environment(raw)
        self._attention = attention.to_dict()
        if edge_is_new and (changed or bool(raw.get("semantic_delta", False))):
            self.journal.append(
                "environment_changed",
                source=source,
                payload={
                    "environment_revision": env_revision,
                    "focused_app": raw.get("focused_app", ""),
                    "focused_object": raw.get("focused_object", ""),
                    "activity": raw.get("activity", "unknown"),
                    "task_hint": raw.get("task_hint", "unknown"),
                    "phase": raw.get("phase", "unknown"),
                    "attention": attention.to_dict(),
                },
                evidence_refs=refs,
                timestamp=current,
                world_revision=self._revision,
            )
        # Sensors may request cognition work, never public speech.
        bus = ensure_proposal_bus(self.host)
        for action in attention.actions if edge_is_new else ():
            if action not in {"generate_seed", "wake_cognition", "interrupt_candidate"}:
                continue
            try:
                bus.submit(
                    source="attention_router",
                    kind=action,
                    subject=_text(raw.get("task_hint", raw.get("activity", "")), 240),
                    payload={"world_revision": self._revision, "attention": attention.to_dict()},
                    confidence=attention.salience,
                    public=False,
                )
            except Exception:
                pass
        try:
            setattr(self.host, "last_world_state", self.snapshot(now=current))
        except Exception:
            pass

    def observe_user_turn(self, user_text: str, *, intent: IntentFrame | None = None, now: float | None = None) -> IntentFrame:
        current = float(now if now is not None else self.clock())
        frame = intent or normalize_intent(user_text)
        topic = _text(getattr(self.host, "active_conversational_topic", ""), 240)
        unresolved = ""
        handoff = getattr(self.host, "conversation_handoff_runtime", None)
        if handoff is not None:
            state = getattr(handoff, "state", None)
            unresolved = _text(getattr(state, "unresolved", ""), 240)
        social_state = _text(getattr(getattr(self.host, "current_conversation_lane", None), "name", ""), 80) or "unknown"
        with self._lock:
            self._revision += 1
            self._conversation = replace(
                self._conversation,
                last_user_text=_text(user_text, 1200),
                topic=topic,
                user_intent=frame.to_dict(),
                unresolved=unresolved,
                social_state=social_state,
                turn_revision=self._conversation.turn_revision + 1,
            )
        self.journal.append(
            "user_turn",
            source="chat",
            payload={"text": _text(user_text, 600), "intent": frame.to_dict(), "topic": topic},
            timestamp=current,
            world_revision=self._revision,
        )
        try:
            setattr(self.host, "current_world_intent", frame)
        except Exception:
            pass
        return frame

    def observe_assistant_commit(self, text: str, *, now: float | None = None) -> None:
        current = float(now if now is not None else self.clock())
        with self._lock:
            self._revision += 1
            self._conversation = replace(
                self._conversation,
                last_assistant_text=_text(text, 1200),
                turn_revision=self._conversation.turn_revision + 1,
            )
        self.journal.append(
            "assistant_commit",
            source="executive_governor",
            payload={"text": _text(text, 600)},
            timestamp=current,
            world_revision=self._revision,
        )

    def _refresh_cognition_from_host(self, *, now: float) -> None:
        concerns: tuple[dict[str, Any], ...] = ()
        concern_runtime = getattr(self.host, "active_concern_runtime", None)
        snapshot_fn = getattr(concern_runtime, "snapshot", None)
        if callable(snapshot_fn):
            try:
                concerns = tuple(dict(item) for item in tuple(snapshot_fn(now=now, limit=8) or ()) if isinstance(item, Mapping))
            except Exception:
                concerns = ()
        materials: tuple[dict[str, Any], ...] = ()
        material_runtime = getattr(self.host, "mental_material_runtime", None)
        # MentalMaterialRuntime exposes snapshot(), không có panel_view().
        # V12 hard-cutover từng gọi nhầm API nên toàn bộ material bị rơi thành ().
        snapshot_material = getattr(material_runtime, "snapshot", None)
        if callable(snapshot_material):
            try:
                raw = snapshot_material(now=now, limit=8)
                materials = tuple(dict(item) for item in tuple(raw or ())[:8] if isinstance(item, Mapping))
            except Exception:
                materials = ()
        attention_state = _text(getattr(self.host, "cortex_attention_level", ""), 80) or _text(self._attention.get("reason", ""), 120) or "unknown"
        goal = _text(getattr(self.host, "active_conversational_topic", ""), 240)
        with self._lock:
            self._cognition = CognitionWorldState(
                active_concerns=concerns,
                mental_material=materials,
                current_goal=goal,
                attention=attention_state,
            )

    def _refresh_system_from_host(self) -> None:
        capabilities: dict[str, Any] = {}
        sensors: dict[str, str] = {}
        try:
            from actions.capability_runtime import runtime_capability_snapshot, runtime_sensor_health_snapshot
            capabilities = runtime_capability_snapshot(refresh=False)
            sensors = runtime_sensor_health_snapshot(owner=self.host)
        except Exception:
            capabilities = {}
            sensors = {
                "deep_environment": "unknown", "uia": "unknown", "ocr": "unknown",
                "browser_bridge": "unknown", "vlm": "unknown", "workspace": "unknown",
            }

        resource = {
            "chat_mode": bool(getattr(self.host, "chat_mode", False)),
            "vision_disabled_until": _float(getattr(self.host, "vision_disabled_until", 0.0), 0.0),
            "autonomous_collision_until": _float(getattr(self.host, "autonomous_turn_collision_until", 0.0), 0.0),
        }
        with self._lock:
            self._system = SystemWorldState(capabilities=capabilities, sensor_health=sensors, resource_budget=resource)

    def refresh_from_host(self, *, now: float | None = None) -> WorldStateSnapshot:
        current = float(now if now is not None else self.clock())
        awareness = getattr(self.host, "last_environment_awareness", None)
        if awareness is not None:
            raw = _mapping(awareness)
            if raw:
                self.ingest_environment(raw, now=current, source="world_refresh")
        self._refresh_cognition_from_host(now=current)
        self._refresh_system_from_host()
        return self.snapshot(now=current)

    def snapshot(self, *, now: float | None = None) -> WorldStateSnapshot:
        current = float(now if now is not None else self.clock())
        with self._lock:
            revision = self._revision
            env = self._environment
            conv = self._conversation
            cog = self._cognition
            system = self._system
            attention = dict(self._attention)
        journal_tail = tuple(item.to_dict() for item in self.journal.tail(12))
        return WorldStateSnapshot(
            revision=revision,
            captured_at=current,
            environment=env,
            conversation=conv,
            cognition=cog,
            system=system,
            attention=attention,
            journal_tail=journal_tail,
        )

    def projection(self, lane: str, *, now: float | None = None, refresh: bool = True) -> dict[str, Any]:
        snapshot = self.refresh_from_host(now=now) if refresh else self.snapshot(now=now)
        return snapshot.projection(lane, now=now)

    def prompt_context(self, lane: str, *, now: float | None = None, refresh: bool = True) -> str:
        snapshot = self.refresh_from_host(now=now) if refresh else self.snapshot(now=now)
        return snapshot.prompt_context(lane, now=now)

    def explain(self, key: str, *, now: float | None = None) -> dict[str, Any]:
        snap = self.snapshot(now=now)
        facts = [item.to_dict(now=now) for item in snap.environment.facts if item.key == key]
        interpretations = [item.to_dict(now=now) for item in snap.environment.interpretations if item.key == key]
        journal = [item.to_dict() for item in self.journal.find(contains=key, limit=12)]
        return {"key": key, "facts": facts, "interpretations": interpretations, "journal": journal}


def ensure_world_state_service(host: Any) -> WorldStateService:
    existing = getattr(host, "world_state_service", None)
    if isinstance(existing, WorldStateService):
        return existing
    service = WorldStateService(host, logger=getattr(host, "logger", None))
    try:
        setattr(host, "world_state_service", service)
    except Exception:
        pass
    return service


def attach_world_state_projection(
    host: Any,
    state_summary: Mapping[str, Any] | None,
    *,
    lane: str = "observer",
    now: float | None = None,
) -> dict[str, Any]:
    """Attach the canonical lane projection to a worker state packet.

    This tiny adapter is intentionally shared by post-observer/autonomous paths
    so those workers do not reconstruct environment context from unrelated host
    fields.  It is a projection only; it grants no public authority.
    """
    output = dict(state_summary or {})
    try:
        service = ensure_world_state_service(host)
        output["world_state"] = service.projection(lane, now=now, refresh=True)
        output["world_state_context"] = service.prompt_context(lane, now=now, refresh=False)
    except Exception:
        pass
    return output


__all__ = [
    "WorldFact", "WorldInterpretation", "EnvironmentWorldState", "ConversationWorldState",
    "CognitionWorldState", "SystemWorldState", "WorldStateSnapshot", "WorldStateService",
    "ensure_world_state_service", "attach_world_state_projection",
]
