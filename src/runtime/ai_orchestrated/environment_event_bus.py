from __future__ import annotations

"""Typed, bounded event bus between native sensors and semantic cognition."""

import hashlib
import threading
import time
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from runtime.privacy.environment_policy import EnvironmentPolicy, PrivacyScope


def _clamp(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


@dataclass(frozen=True, slots=True)
class NormalizedEnvironmentEvent:
    timestamp: float
    source: str
    event_type: str
    action: str = ""
    process_name: str = ""
    pid: int = 0
    hwnd: int = 0
    window_class: str = ""
    region_id: str = ""
    role: str = ""
    title: str = ""
    value: str = ""
    semantic: bool = True
    source_confidence: float = 0.8
    novelty: float = 0.5
    importance: float = 0.2
    user_relevance: float = 0.5
    confidence: float = 0.8
    attention_level: int = 1
    privacy_scope: str = "SEMANTIC"
    ttl_ms: int = 90_000
    evidence_refs: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = ""

    def __post_init__(self) -> None:
        if self.event_id:
            return
        raw = "|".join((
            str(round(float(self.timestamp), 3)), self.source, self.event_type, self.action,
            str(self.pid), str(self.hwnd), self.region_id, self.role, self.title,
        ))
        object.__setattr__(self, "event_id", "evt_" + hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:18])

    @property
    def expired(self) -> bool:
        return self.ttl_ms > 0 and (time.time() - self.timestamp) * 1000.0 > self.ttl_ms

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any], *, now: float | None = None) -> "NormalizedEnvironmentEvent":
        now = time.time() if now is None else float(now)
        refs = tuple(str(item) for item in value.get("evidence_refs", ()) if str(item).strip())
        return cls(
            timestamp=float(value.get("timestamp", value.get("wall_time", now)) or now),
            source=_compact(value.get("source", "unknown"), 80),
            event_type=_compact(value.get("event_type", value.get("kind", "unknown")), 100),
            action=_compact(value.get("action", ""), 100),
            process_name=_compact(value.get("process_name", value.get("process", "")), 120),
            pid=int(value.get("pid", 0) or 0),
            hwnd=int(value.get("hwnd", 0) or 0),
            window_class=_compact(value.get("window_class", ""), 100),
            region_id=_compact(value.get("region_id", ""), 220),
            role=_compact(value.get("role", ""), 80),
            title=_compact(value.get("title", value.get("detail", "")), 260),
            value=_compact(value.get("value", ""), 260),
            semantic=bool(value.get("semantic", True)),
            source_confidence=_clamp(value.get("source_confidence", value.get("confidence", 0.8)), 0.8),
            novelty=_clamp(value.get("novelty", 0.5), 0.5),
            importance=_clamp(value.get("importance", 0.2), 0.2),
            user_relevance=_clamp(value.get("user_relevance", 0.5), 0.5),
            confidence=_clamp(value.get("confidence", 0.8), 0.8),
            attention_level=max(0, min(4, int(value.get("attention_level", 1) or 0))),
            privacy_scope=str(value.get("privacy_scope", "SEMANTIC") or "SEMANTIC").upper(),
            ttl_ms=max(0, int(value.get("ttl_ms", 90_000) or 0)),
            evidence_refs=refs[:12],
            metadata=dict(value.get("metadata", {}) or {}),
            event_id=str(value.get("event_id", "") or ""),
        )


class EnvironmentEventBus:
    """Thread-safe event buffer with coalescing, privacy filtering and TTL."""

    def __init__(
        self,
        *,
        max_events: int = 512,
        coalesce_ms: int = 300,
        policy: EnvironmentPolicy | None = None,
    ) -> None:
        self._events: deque[NormalizedEnvironmentEvent] = deque(maxlen=max(64, int(max_events)))
        self._lock = threading.RLock()
        self._coalesce_ms = max(0, int(coalesce_ms))
        self._last_signature: OrderedDict[str, float] = OrderedDict()
        self.policy = policy or EnvironmentPolicy()

    @staticmethod
    def signature(event: NormalizedEnvironmentEvent) -> str:
        if event.attention_level == 0 or not event.semantic or event.event_type in {"counter_update", "clock_tick", "scroll_churn", "progress_update"}:
            raw = "|".join((event.source, event.event_type, event.action, str(event.pid), str(event.hwnd)))
            return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]
        stable_title = event.title if event.event_type not in {"value_changed", "counter_update", "progress_update"} else ""
        raw = "|".join((event.source, event.event_type, event.action, str(event.pid), str(event.hwnd), event.region_id, event.role, stable_title))
        return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:20]

    def publish(self, event: NormalizedEnvironmentEvent | Mapping[str, Any]) -> NormalizedEnvironmentEvent | None:
        if not isinstance(event, NormalizedEnvironmentEvent):
            event = NormalizedEnvironmentEvent.from_mapping(event)
        decision = self.policy.scope_for(
            process_name=event.process_name,
            window_title=event.title,
            url=str(event.metadata.get("url", "") or ""),
            workspace=str(event.metadata.get("workspace", "") or ""),
            is_password=bool(event.metadata.get("is_password", False)),
            elevated_credential_ui=bool(event.metadata.get("credential_ui", False)),
        )
        if decision.scope == PrivacyScope.OFF:
            return None

        sanitized_meta = self.policy.sanitize_payload(event.metadata, decision)
        title = event.title
        value = event.value
        if decision.scope <= PrivacyScope.METADATA:
            title = ""
            value = ""
        elif decision.scope == PrivacyScope.SEMANTIC:
            value = ""
        event = NormalizedEnvironmentEvent(
            timestamp=event.timestamp,
            source=event.source,
            event_type=event.event_type,
            action=event.action,
            process_name=event.process_name,
            pid=event.pid,
            hwnd=event.hwnd,
            window_class=event.window_class,
            region_id=event.region_id,
            role=event.role,
            title=title,
            value=value,
            semantic=event.semantic,
            source_confidence=event.source_confidence,
            novelty=event.novelty,
            importance=event.importance,
            user_relevance=event.user_relevance,
            confidence=event.confidence,
            attention_level=event.attention_level,
            privacy_scope=decision.scope.name,
            ttl_ms=decision.ttl_ms or event.ttl_ms,
            evidence_refs=event.evidence_refs,
            metadata=sanitized_meta,
            event_id=event.event_id,
        )

        if event.expired:
            return None
        signature = self.signature(event)
        now_ms = event.timestamp * 1000.0
        with self._lock:
            last_ms = self._last_signature.get(signature, -1e18)
            if self._coalesce_ms and now_ms - last_ms < self._coalesce_ms:
                return None
            if self._coalesce_ms:
                self._last_signature[signature] = now_ms
                self._last_signature.move_to_end(signature)
                if len(self._last_signature) > self._events.maxlen:
                    self._last_signature.popitem(last=False)
            self._events.append(event)
        return event

    def extend(self, events: Iterable[NormalizedEnvironmentEvent | Mapping[str, Any]]) -> tuple[NormalizedEnvironmentEvent, ...]:
        accepted: list[NormalizedEnvironmentEvent] = []
        for event in events:
            item = self.publish(event)
            if item is not None:
                accepted.append(item)
        return tuple(accepted)

    def drain(self, *, since: float = 0.0, limit: int = 128) -> tuple[NormalizedEnvironmentEvent, ...]:
        with self._lock:
            now = time.time()
            self._events = deque(
                (event for event in self._events
                 if event.ttl_ms <= 0 or (now - event.timestamp) * 1000.0 <= event.ttl_ms),
                maxlen=self._events.maxlen,
            )
            items = [item for item in self._events if item.timestamp >= float(since)]
        return tuple(items[-max(1, int(limit)):])

    def latest(self, limit: int = 32) -> tuple[NormalizedEnvironmentEvent, ...]:
        return self.drain(since=0.0, limit=limit)

__all__ = ["EnvironmentEventBus", "NormalizedEnvironmentEvent"]
