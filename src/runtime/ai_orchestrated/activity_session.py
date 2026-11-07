# -*- coding: utf-8 -*-
"""Phiên hoạt động public-safe cho self-initiated turn.

Tracker này chỉ nhận biết thay đổi cơ học của app/window/screen. Nghĩa như
``gaming``, ``post_match`` hay ``build_failed`` chỉ được ghi khi main planner trả
``ActivitySemantics`` có evidence. Không còn bảng keyword victory/error/loading.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from .semantic_contracts import ActivitySemantics, compact, semantic_key


def _surface_key(app: str, title: str) -> str:
    raw = f"{app.strip().lower()}|{title.strip().lower()}"
    return hashlib.sha1(raw.encode("utf-8", "ignore")).hexdigest()[:16] if raw.strip("|") else ""


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    timestamp: float
    kind: str
    event_key: str
    detail: str = ""
    confidence: float = 1.0
    evidence_refs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActivitySession:
    session_id: str = ""
    app: str = ""
    window_title: str = ""
    surface_key: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    revision: int = 0
    change_count: int = 0
    activity_kind: str = "unknown"
    phase: str = "unknown"
    semantic_confidence: float = 0.0
    recent_events: tuple[ActivityEvent, ...] = ()
    new_event_keys: tuple[str, ...] = ()

    @property
    def age_seconds(self) -> float:
        return max(0.0, self.updated_at - self.started_at) if self.started_at else 0.0

    @property
    def has_new_event(self) -> bool:
        return bool(self.new_event_keys)

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "app": self.app,
            "window_title": self.window_title,
            "surface_key": self.surface_key,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "age_seconds": round(self.age_seconds, 2),
            "revision": self.revision,
            "change_count": self.change_count,
            "activity_kind": self.activity_kind,
            "phase": self.phase,
            "semantic_confidence": round(self.semantic_confidence, 3),
            "recent_events": [item.to_dict() for item in self.recent_events[-8:]],
            "new_event_keys": list(self.new_event_keys),
            "epistemic_note": (
                "App/window/screen_delta events are mechanical. activity_kind/phase/event are copied from "
                "main-planner ActivitySemantics and are not inferred from keywords by runtime."
            ),
        }


class ActivitySessionTracker:
    """Giữ một phiên app/window trong RAM; không persist OCR hay lịch sử riêng tư."""

    def __init__(self, *, session_gap_seconds: float = 420.0) -> None:
        self.session_gap_seconds = max(60.0, float(session_gap_seconds))
        self._current = ActivitySession()
        self._seen_event_keys: set[str] = set()

    def observe(
        self,
        screen_context: Any,
        *,
        telemetry: Mapping[str, Any] | None = None,
        now: float | None = None,
    ) -> ActivitySession:
        now = time.time() if now is None else float(now)
        telemetry = telemetry if isinstance(telemetry, Mapping) else {}
        app = compact(getattr(screen_context, "process_name", "") or telemetry.get("active_app", ""), 100)
        title = compact(getattr(screen_context, "window_title", "") or telemetry.get("active_window_title", ""), 180)
        surface = _surface_key(app, title)
        previous = self._current
        new_session = bool(
            not previous.session_id
            or (previous.updated_at and now - previous.updated_at > self.session_gap_seconds)
            or (surface and previous.surface_key and surface != previous.surface_key)
        )
        if new_session:
            session_id = f"activity-{int(now * 1000)}"
            started_at = now
            events: list[ActivityEvent] = []
            self._seen_event_keys.clear()
            change_count = 0
            activity_kind = "unknown"
            phase = "unknown"
            semantic_confidence = 0.0
        else:
            session_id = previous.session_id
            started_at = previous.started_at or now
            events = list(previous.recent_events)
            change_count = previous.change_count
            activity_kind = previous.activity_kind
            phase = previous.phase
            semantic_confidence = previous.semantic_confidence

        new_keys: list[str] = []
        evidence_refs = tuple(getattr(screen_context, "evidence_refs", ()) or ())
        if new_session:
            key = f"surface_entered:{surface or session_id}"
            events.append(ActivityEvent(now, "surface_entered", key, title or app, 1.0, evidence_refs))
            self._seen_event_keys.add(key)
            new_keys.append(key)
            change_count += 1
        elif bool(getattr(screen_context, "window_changed", False)):
            key = f"window_changed:{surface}:{int(getattr(screen_context, 'revision', 0) or 0)}"
            if key not in self._seen_event_keys:
                events.append(ActivityEvent(now, "window_changed", key, title or app, 1.0, evidence_refs))
                self._seen_event_keys.add(key)
                new_keys.append(key)
                change_count += 1
        elif bool(getattr(screen_context, "meaningful_delta", False)):
            revision = int(getattr(screen_context, "revision", 0) or 0)
            key = f"screen_delta:{surface}:{revision}"
            if key not in self._seen_event_keys:
                events.append(ActivityEvent(now, "screen_delta", key, "screen content changed", 1.0, evidence_refs))
                self._seen_event_keys.add(key)
                new_keys.append(key)
                change_count += 1

        self._current = ActivitySession(
            session_id=session_id,
            app=app,
            window_title=title,
            surface_key=surface,
            started_at=started_at,
            updated_at=now,
            revision=max(int(getattr(screen_context, "revision", 0) or 0), previous.revision if not new_session else 0),
            change_count=change_count,
            activity_kind=activity_kind,
            phase=phase,
            semantic_confidence=semantic_confidence,
            recent_events=tuple(events[-16:]),
            new_event_keys=tuple(new_keys),
        )
        return self._current

    def annotate(self, semantics: ActivitySemantics, *, now: float | None = None) -> ActivitySession:
        """Ghi annotation từ main planner cho tick sau, không tự suy diễn lại."""
        now = time.time() if now is None else float(now)
        current = self._current
        if not current.session_id:
            return current
        events = list(current.recent_events)
        new_keys: list[str] = list(current.new_event_keys)
        if semantics.has_event:
            key = f"semantic:{semantic_key(semantics.event_key)}"
            if key not in self._seen_event_keys:
                events.append(ActivityEvent(
                    timestamp=now,
                    kind="semantic_event",
                    event_key=key,
                    detail=semantics.event,
                    confidence=semantics.confidence,
                    evidence_refs=semantics.evidence_refs,
                ))
                self._seen_event_keys.add(key)
                new_keys.append(key)
        self._current = ActivitySession(
            session_id=current.session_id,
            app=current.app,
            window_title=current.window_title,
            surface_key=current.surface_key,
            started_at=current.started_at,
            updated_at=now,
            revision=current.revision,
            change_count=current.change_count + (1 if semantics.has_event else 0),
            activity_kind=semantics.activity_kind,
            phase=semantics.phase,
            semantic_confidence=semantics.confidence,
            recent_events=tuple(events[-16:]),
            new_event_keys=tuple(dict.fromkeys(new_keys))[-8:],
        )
        return self._current


__all__ = ["ActivityEvent", "ActivitySession", "ActivitySessionTracker"]
