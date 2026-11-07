# -*- coding: utf-8 -*-
from __future__ import annotations

"""TaskGraph deterministic/temporal cho Deep Environment.

Đây vẫn là Phase A/B theo PDF: typed event -> action/activity/task graph trước,
không gọi LLM trên raw event firehose. Graph giữ episode, object và edge có
nghĩa (FOLLOWS/TESTS/RESEARCHES/MODIFIES/RETURNS_TO/RESOLVES), đồng thời tách
confidence thành các thành phần evidence thay vì một con số "cảm tính".
"""

import time
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from typing import Any, Sequence

from .environment_event_bus import NormalizedEnvironmentEvent


@dataclass(slots=True)
class TaskEpisode:
    kind: str
    timestamp: float
    object_ref: str = ""
    outcome: str = ""
    event_id: str = ""
    source: str = ""


@dataclass(slots=True)
class TaskEdge:
    relation: str
    source_event_id: str
    target_event_id: str
    object_ref: str = ""
    confidence: float = 0.0


@dataclass(slots=True)
class TaskState:
    task_id: str
    label: str = "unknown"
    activity: str = "unknown"
    phase: str = "unknown"
    confidence: float = 0.0
    started_at: float = 0.0
    updated_at: float = 0.0
    objects: set[str] = field(default_factory=set)
    episodes: list[TaskEpisode] = field(default_factory=list)
    edges: list[TaskEdge] = field(default_factory=list)
    confidence_components: dict[str, float] = field(default_factory=dict)


class TaskGraph:
    def __init__(self, *, switch_grace_seconds: float = 20.0, max_episodes: int = 96, max_edges: int = 160) -> None:
        self.switch_grace_seconds = max(2.0, float(switch_grace_seconds))
        self.max_episodes = max(16, int(max_episodes))
        self.max_edges = max(24, int(max_edges))
        self.current = TaskState(task_id="task_0")
        self._seq = 0
        self._previous_surface = ""
        self._previous_event: TaskEpisode | None = None
        self._last_source = ""

    @staticmethod
    def _surface(event: NormalizedEnvironmentEvent) -> str:
        return event.title or event.region_id or event.process_name

    @staticmethod
    def _similar(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def ingest(self, events: Sequence[NormalizedEnvironmentEvent], *, now: float | None = None) -> TaskState:
        now = time.time() if now is None else float(now)
        if not events:
            return self.current
        for event in events:
            self._apply(event, now=now)
        return self.current

    def _ensure_task(self, event: NormalizedEnvironmentEvent) -> None:
        if self.current.started_at:
            return
        self._seq += 1
        self.current = TaskState(task_id=f"task_{self._seq}", started_at=event.timestamp, updated_at=event.timestamp)

    @staticmethod
    def _relation(previous: TaskEpisode | None, current: TaskEpisode) -> str:
        if previous is None:
            return "USES"
        prev = previous.kind
        kind = current.kind
        if kind in {"file_saved", "source_modified"}:
            return "MODIFIES"
        if prev in {"file_saved", "source_modified", "editor_focused"} and kind in {"command_started", "test_started", "build_started"}:
            return "TESTS"
        if prev in {"command_failed", "test_failed", "build_failed", "error_candidate", "possible_command_failure"} and kind.startswith("browser_"):
            return "RESEARCHES"
        if prev.startswith("browser_") and kind in {"editor_focused", "file_saved", "source_modified"}:
            return "RETURNS_TO"
        if kind in {"command_succeeded", "test_passed", "build_succeeded", "task_completed"}:
            return "RESOLVES"
        return "FOLLOWS"

    def _confidence_components(self, event: NormalizedEnvironmentEvent, surface: str, *, now: float, outcome_link: bool) -> dict[str, float]:
        state = self.current
        source_quality = max(0.0, min(1.0, max(event.source_confidence, event.confidence)))

        gap = max(0.0, event.timestamp - state.updated_at) if state.updated_at else 0.0
        if not state.episodes:
            temporal = 0.55
        elif gap <= 8.0:
            temporal = 0.95
        elif gap <= self.switch_grace_seconds:
            temporal = 0.82
        elif gap <= self.switch_grace_seconds * 3:
            temporal = 0.58
        else:
            temporal = 0.30

        similarities = [self._similar(surface, obj) for obj in list(state.objects)[-16:]] if surface else []
        semantic_similarity = max(similarities, default=0.45 if not state.objects else 0.20)

        recent_sources = {ep.source for ep in state.episodes[-8:] if ep.source}
        cross_source = 0.88 if event.source and recent_sources and event.source not in recent_sources else 0.58
        if len(recent_sources) >= 2:
            cross_source = max(cross_source, 0.82)

        outcome_strength = 0.95 if outcome_link else (0.55 if event.event_type in {"file_saved", "source_modified", "browser_tab_activated"} else 0.35)

        contradiction = 0.0
        if event.event_type in {"media_changed", "media_state_changed"} and state.phase in {"implementation", "testing", "diagnosis"} and gap < 8.0:
            contradiction = 0.25
        if surface and self._previous_surface and self._similar(surface, self._previous_surface) < 0.08 and gap > self.switch_grace_seconds:
            contradiction = max(contradiction, 0.18)

        return {
            "structured_source_quality": source_quality,
            "temporal_continuity": temporal,
            "semantic_similarity": max(0.0, min(1.0, semantic_similarity)),
            "cross_source_agreement": cross_source,
            "outcome_link_strength": outcome_strength,
            "contradiction": contradiction,
        }

    @staticmethod
    def _score_confidence(parts: dict[str, float]) -> float:
        score = (
            0.25 * parts.get("structured_source_quality", 0.0)
            + 0.20 * parts.get("temporal_continuity", 0.0)
            + 0.20 * parts.get("semantic_similarity", 0.0)
            + 0.20 * parts.get("cross_source_agreement", 0.0)
            + 0.15 * parts.get("outcome_link_strength", 0.0)
            - 0.30 * parts.get("contradiction", 0.0)
        )
        return max(0.0, min(0.98, score))

    def _apply(self, event: NormalizedEnvironmentEvent, *, now: float) -> None:
        self._ensure_task(event)
        current = self.current
        kind = event.event_type
        surface = self._surface(event)

        activity = current.activity
        phase = current.phase
        label = current.label
        outcome = ""
        episode_kind = kind

        if kind in {"file_saved", "source_modified", "editor_focused"}:
            activity = "software_development"
            phase = "implementation"
            label = label if label != "unknown" else (surface or "software development")
        elif kind in {"command_started", "test_started", "build_started"}:
            activity = "software_development"
            phase = "testing"
            label = label if label != "unknown" else "testing current change"
        elif kind in {"command_failed", "test_failed", "build_failed", "error_candidate", "possible_command_failure"}:
            activity = "software_development"
            phase = "diagnosis"
            outcome = "failed"
            label = label if label != "unknown" else "debugging failure"
        elif kind in {"command_succeeded", "test_passed", "build_succeeded", "task_completed", "completion_candidate"}:
            activity = "software_development" if current.activity == "software_development" else current.activity
            phase = "verification_success"
            outcome = "passed"
        elif kind in {"browser_tab_activated", "browser_tab_title_changed", "navigation_changed"}:
            # Đi docs ngắn sau failure vẫn là cùng task, không đổi task vì một Alt-Tab.
            activity = "documentation_research"
            if current.phase in {"diagnosis", "testing", "implementation"}:
                phase = "diagnosis" if current.phase == "diagnosis" else current.phase
            if current.label == "unknown":
                label = surface or "documentation research"
        elif kind in {"media_state_changed", "media_changed"}:
            if current.updated_at and event.timestamp - current.updated_at > self.switch_grace_seconds:
                activity, phase, label = "media_consumption", "active", "unknown"

        if surface:
            current.objects.add(surface)

        outcome_link = bool(
            kind in {"command_succeeded", "command_failed", "test_passed", "test_failed", "build_succeeded", "build_failed"}
            and any(ep.kind in {"command_started", "test_started", "build_started"} for ep in current.episodes[-8:])
        )
        parts = self._confidence_components(event, surface, now=now, outcome_link=outcome_link)

        episode = TaskEpisode(episode_kind, event.timestamp, surface, outcome, event.event_id, event.source)
        relation = self._relation(self._previous_event, episode)
        if self._previous_event is not None:
            current.edges.append(TaskEdge(
                relation=relation,
                source_event_id=self._previous_event.event_id or f"episode:{len(current.episodes)}",
                target_event_id=episode.event_id or f"episode:{len(current.episodes) + 1}",
                object_ref=surface,
                confidence=max(0.45, min(0.98, self._score_confidence(parts))),
            ))
        elif surface:
            current.edges.append(TaskEdge("USES", "task_root", episode.event_id or "episode:1", surface, 0.72))

        current.activity = activity
        current.phase = phase
        current.label = label
        current.updated_at = max(current.updated_at, event.timestamp)
        current.confidence_components = parts
        new_confidence = self._score_confidence(parts)
        # EMA nhẹ giữ continuity nhưng không để confidence cũ áp đảo contradiction mới.
        current.confidence = max(0.0, min(0.98, 0.35 * current.confidence + 0.65 * new_confidence if current.confidence else new_confidence))
        current.episodes.append(episode)

        if len(current.episodes) > self.max_episodes:
            del current.episodes[: len(current.episodes) - self.max_episodes]
        if len(current.edges) > self.max_edges:
            del current.edges[: len(current.edges) - self.max_edges]

        self._previous_surface = surface or self._previous_surface
        self._previous_event = episode
        self._last_source = event.source or self._last_source

    def interruption_cost(self, *, idle_seconds: float = 0.0, busy_score: float = 0.0, now: float | None = None) -> float:
        """Ước lượng interruption cost từ nhịp task, không cần raw keylog."""
        now = time.time() if now is None else float(now)
        phase = self.current.phase
        episodes = self.current.episodes
        base = 0.14 + max(0.0, min(1.0, float(busy_score))) * 0.44

        # Event density gần đây phản ánh user đang edit/test/research liên tục.
        recent_12 = [ep for ep in episodes if now - ep.timestamp <= 12.0]
        density = min(1.0, len(recent_12) / 7.0)
        base += 0.20 * density

        recent_kinds = [ep.kind for ep in recent_12]
        if phase in {"implementation", "testing", "diagnosis"}:
            base += 0.16
        elif phase == "verification_success":
            base -= 0.24

        if any(kind in {"file_saved", "source_modified"} for kind in recent_kinds):
            base += 0.08
        if any(kind in {"command_started", "test_started", "build_started"} for kind in recent_kinds):
            base += 0.10
        if any(kind in {"command_succeeded", "test_passed", "build_succeeded", "task_completed"} for kind in recent_kinds):
            base -= 0.12

        # Research <-> editor chuyển nhanh là dấu hiệu diagnosis đang có flow.
        roles = [
            "browser" if kind.startswith("browser_") else "editor" if kind in {"editor_focused", "file_saved", "source_modified"} else "other"
            for kind in recent_kinds
        ]
        transitions = sum(1 for a, b in zip(roles, roles[1:]) if {a, b} == {"browser", "editor"})
        base += min(0.14, transitions * 0.05)

        if idle_seconds >= 20.0:
            base -= min(0.34, idle_seconds / 150.0)
        if idle_seconds >= 180.0:
            base = min(base, 0.12)
        return max(0.0, min(1.0, base))

    def to_dict(self) -> dict[str, Any]:
        state = self.current
        return {
            "task_id": state.task_id,
            "task": state.label,
            "activity": state.activity,
            "phase": state.phase,
            "confidence": round(state.confidence, 3),
            "confidence_components": {key: round(value, 3) for key, value in state.confidence_components.items()},
            "objects": sorted(state.objects)[-12:],
            "episodes": [asdict(episode) for episode in state.episodes[-12:]],
            "edges": [asdict(edge) for edge in state.edges[-16:]],
        }


__all__ = ["TaskEdge", "TaskEpisode", "TaskGraph", "TaskState"]
