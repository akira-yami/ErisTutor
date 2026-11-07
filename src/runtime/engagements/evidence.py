# -*- coding: utf-8 -*-
"""Evidence binding and routing for durable engagements.

Mechanical activity is not global truth.  A window change belongs to the
engagement bound to that surface or workspace, not every active task currently
occupying ERIS.  This module keeps that boundary explicit and deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


def _clean(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _tags(value: Any) -> tuple[str, ...]:
    items = tuple(value or ()) if not isinstance(value, str) else (value,)
    return tuple(dict.fromkeys(_clean(item, 80).lower() for item in items if _clean(item, 80)))[:16]


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    """Stable identifiers tying evidence to one ongoing engagement.

    Empty fields are wildcards only in the sense that they provide no evidence;
    they never magically match another empty field.  Strong identifiers such as
    resource/workspace/session win over a merely similar application name.
    """

    surface_key: str = ""
    app: str = ""
    window_title: str = ""
    activity_session_id: str = ""
    workspace_id: str = ""
    resource_id: str = ""
    conversation_id: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def meaningful(self) -> bool:
        return any((
            self.surface_key,
            self.app,
            self.activity_session_id,
            self.workspace_id,
            self.resource_id,
            self.conversation_id,
            self.tags,
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "surface_key": self.surface_key,
            "app": self.app,
            "window_title": self.window_title,
            "activity_session_id": self.activity_session_id,
            "workspace_id": self.workspace_id,
            "resource_id": self.resource_id,
            "conversation_id": self.conversation_id,
            "tags": list(self.tags),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | "EvidenceBinding" | None) -> "EvidenceBinding":
        if isinstance(data, cls):
            return data
        data = data if isinstance(data, Mapping) else {}
        return cls(
            surface_key=_clean(data.get("surface_key"), 160),
            app=_clean(data.get("app"), 100).lower(),
            window_title=_clean(data.get("window_title"), 180).lower(),
            activity_session_id=_clean(data.get("activity_session_id") or data.get("session_id"), 120),
            workspace_id=_clean(data.get("workspace_id"), 160),
            resource_id=_clean(data.get("resource_id"), 200),
            conversation_id=_clean(data.get("conversation_id"), 160),
            tags=_tags(data.get("tags")),
        )

    def merged(self, other: Mapping[str, Any] | "EvidenceBinding" | None) -> "EvidenceBinding":
        incoming = EvidenceBinding.from_mapping(other)
        return EvidenceBinding(
            surface_key=incoming.surface_key or self.surface_key,
            app=incoming.app or self.app,
            window_title=incoming.window_title or self.window_title,
            activity_session_id=incoming.activity_session_id or self.activity_session_id,
            workspace_id=incoming.workspace_id or self.workspace_id,
            resource_id=incoming.resource_id or self.resource_id,
            conversation_id=incoming.conversation_id or self.conversation_id,
            tags=tuple(dict.fromkeys((*self.tags, *incoming.tags)))[:16],
        )

    def match_score(self, other: Mapping[str, Any] | "EvidenceBinding" | None) -> int:
        candidate = EvidenceBinding.from_mapping(other)
        if not self.meaningful or not candidate.meaningful:
            return 0

        # A conflict on a strong identifier means the evidence belongs elsewhere.
        for left, right in (
            (self.resource_id, candidate.resource_id),
            (self.workspace_id, candidate.workspace_id),
            (self.activity_session_id, candidate.activity_session_id),
            (self.surface_key, candidate.surface_key),
            (self.conversation_id, candidate.conversation_id),
        ):
            if left and right and left != right:
                return -1

        score = 0
        if self.resource_id and self.resource_id == candidate.resource_id:
            score += 100
        if self.workspace_id and self.workspace_id == candidate.workspace_id:
            score += 75
        if self.activity_session_id and self.activity_session_id == candidate.activity_session_id:
            score += 65
        if self.surface_key and self.surface_key == candidate.surface_key:
            score += 60
        if self.conversation_id and self.conversation_id == candidate.conversation_id:
            score += 45
        if self.app and self.app == candidate.app:
            score += 22
        if self.window_title and self.window_title == candidate.window_title:
            score += 18
        score += min(20, 5 * len(set(self.tags).intersection(candidate.tags)))
        return score


@dataclass(frozen=True, slots=True)
class EvidenceEnvelope:
    kind: str
    source: str
    binding: EvidenceBinding = field(default_factory=EvidenceBinding)
    payload: Mapping[str, Any] = field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()


class EvidenceRouter:
    """Select engagement targets without broadcasting mechanical evidence."""

    def route(
        self,
        engagements: Sequence[Any],
        envelope: EvidenceEnvelope,
        *,
        adapters: Any,
        foreground_id: str = "",
    ) -> tuple[str, ...]:
        scored: list[tuple[int, int, float, str]] = []
        fallback: list[tuple[int, float, str]] = []
        for engagement in engagements:
            if not bool(getattr(engagement, "active", False)):
                continue
            if str(getattr(getattr(engagement, "focus", None), "value", "")) == "suspended":
                continue
            adapter = adapters.get(getattr(engagement, "adapter_id", "generic"))
            accepts = getattr(adapter, "accepts_evidence", None)
            if callable(accepts) and not accepts(envelope.kind, source=envelope.source, payload=envelope.payload):
                continue
            binding = EvidenceBinding.from_mapping(getattr(engagement, "evidence_binding", None))
            score = binding.match_score(envelope.binding)
            if score < 0:
                continue
            if score > 0:
                scored.append((score, int(getattr(engagement, "priority", 0)), float(getattr(engagement, "updated_at", 0.0)), engagement.engagement_id))
                continue
            # Unbound evidence can seed only the foreground engagement.  Background
            # monitors must be explicitly bound or targeted, otherwise every mouse
            # twitch becomes a deployment update. Humanity has enough false alerts.
            if engagement.engagement_id == foreground_id and (
                not envelope.binding.meaningful or not binding.meaningful
            ):
                fallback.append((int(getattr(engagement, "priority", 0)), float(getattr(engagement, "updated_at", 0.0)), engagement.engagement_id))

        if scored:
            best = max(scored)
            return (best[-1],)
        if fallback:
            return (max(fallback)[-1],)
        return ()


__all__ = ["EvidenceBinding", "EvidenceEnvelope", "EvidenceRouter"]
