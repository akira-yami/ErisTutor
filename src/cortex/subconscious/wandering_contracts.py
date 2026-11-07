# -*- coding: utf-8 -*-
"""Contracts cho tầng wandering thought.

File này chỉ gom shape dữ liệu. Logic nằm ở DriftPool/FreeAssociation/WanderingEngine.
Không cho contract tự thông minh, vì dataclass có cái tôi là khởi đầu của mọi thảm họa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .thought_spark import ThoughtSpark, SparkSource, clamp01, clean_text, now_timestamp
from .thought_fragment import ThoughtFragment


WanderingTopicStatus = Literal["simmering", "dormant", "surfaced", "decayed"]


@dataclass(slots=True, frozen=True)
class TopicSeed:
    text: str
    source: SparkSource
    strength: float = 0.35
    weirdness: float = 0.30
    clarity: float = 0.25
    affect_tint: float = 0.0
    tags: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    from_user_action: bool = False
    created_at: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", clean_text(self.text, limit=180))
        object.__setattr__(self, "strength", clamp01(self.strength))
        object.__setattr__(self, "weirdness", clamp01(self.weirdness))
        object.__setattr__(self, "clarity", clamp01(self.clarity))
        object.__setattr__(self, "affect_tint", clamp01(self.affect_tint))
        object.__setattr__(self, "tags", tuple(dict.fromkeys(clean_text(t, limit=60).lower().replace(" ", "_") for t in self.tags if clean_text(t, limit=60))))
        object.__setattr__(self, "evidence_ids", tuple(dict.fromkeys(clean_text(e, limit=120) for e in self.evidence_ids if clean_text(e, limit=120))))
        object.__setattr__(self, "created_at", float(self.created_at or now_timestamp()))

    def to_spark(self, *, now: float | None = None, ttl_seconds: float = 540.0) -> ThoughtSpark:
        tags = tuple(dict.fromkeys((self.source, *self.tags)))
        return ThoughtSpark.create(
            seed=self.text,
            source=self.source,
            raw_text=self.text,
            strength=self.strength,
            weirdness=self.weirdness,
            clarity=self.clarity,
            affect_tint=self.affect_tint,
            ttl_seconds=ttl_seconds,
            tags=tags,
            now=now or self.created_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "text": self.text,
            "source": self.source,
            "strength": round(float(self.strength), 3),
            "weirdness": round(float(self.weirdness), 3),
            "clarity": round(float(self.clarity), 3),
            "affect_tint": round(float(self.affect_tint), 3),
            "tags": list(self.tags),
            "evidence_ids": list(self.evidence_ids),
            "from_user_action": bool(self.from_user_action),
            "created_at": round(float(self.created_at), 3),
        }


@dataclass(slots=True, frozen=True)
class AssociationPath:
    spark_ids: tuple[str, ...]
    seeds: tuple[str, ...]
    statement: str
    weirdness: float
    groundedness: float
    source_evidence: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    score: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "spark_ids", tuple(dict.fromkeys(clean_text(x, limit=120) for x in self.spark_ids if clean_text(x, limit=120))))
        object.__setattr__(self, "seeds", tuple(dict.fromkeys(clean_text(x, limit=120) for x in self.seeds if clean_text(x, limit=120))))
        object.__setattr__(self, "statement", clean_text(self.statement, limit=420))
        object.__setattr__(self, "weirdness", clamp01(self.weirdness))
        object.__setattr__(self, "groundedness", clamp01(self.groundedness))
        object.__setattr__(self, "source_evidence", tuple(dict.fromkeys(clean_text(x, limit=140) for x in self.source_evidence if clean_text(x, limit=140))))
        object.__setattr__(self, "tags", tuple(dict.fromkeys(clean_text(x, limit=60).lower().replace(" ", "_") for x in self.tags if clean_text(x, limit=60))))
        object.__setattr__(self, "score", clamp01(self.score if self.score else self.groundedness * 0.62 + self.weirdness * 0.38))

    def to_fragment(
        self,
        *,
        surface_allowed: bool,
        now: float | None = None,
        kind: str = "wandering_fragment",
        privacy_level: str = "normal",
    ) -> ThoughtFragment:
        evidence = tuple(dict.fromkeys((*self.source_evidence, *(f"spark:{item}" for item in self.spark_ids))))[:24]
        confidence = clamp01(self.groundedness * 0.68 + (1.0 - abs(self.weirdness - 0.55)) * 0.18)
        urgency = clamp01(self.score * (0.72 if surface_allowed else 0.42))
        return ThoughtFragment.create(
            source_agent="wandering_thought",
            kind=kind,
            content=self.statement,
            evidence_ids=evidence,
            confidence=confidence,
            urgency=urgency,
            affect_charge=clamp01(self.weirdness * 0.28 + self.groundedness * 0.20),
            privacy_level=privacy_level,
            surface_allowed=surface_allowed,
            ttl_seconds=480,
            tags=("wandering", *self.tags),
            now=now,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "spark_ids": list(self.spark_ids),
            "seeds": list(self.seeds),
            "statement": self.statement,
            "weirdness": round(float(self.weirdness), 3),
            "groundedness": round(float(self.groundedness), 3),
            "source_evidence": list(self.source_evidence),
            "tags": list(self.tags),
            "score": round(float(self.score), 3),
        }


@dataclass(slots=True, frozen=True)
class WanderingTopic:
    id: str
    theme: str
    recurrence: float
    last_surface_at: float
    last_private_tick_at: float
    related_sparks: list[str] = field(default_factory=list)
    related_memories: list[str] = field(default_factory=list)
    status: WanderingTopicStatus = "simmering"

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", clean_text(self.id, limit=140) or clean_text(self.theme, limit=80).lower().replace(" ", "_"))
        object.__setattr__(self, "theme", clean_text(self.theme, limit=160))
        object.__setattr__(self, "recurrence", clamp01(self.recurrence))
        object.__setattr__(self, "last_surface_at", float(self.last_surface_at or 0.0))
        object.__setattr__(self, "last_private_tick_at", float(self.last_private_tick_at or 0.0))
        object.__setattr__(self, "related_sparks", list(dict.fromkeys(clean_text(x, limit=120) for x in self.related_sparks if clean_text(x, limit=120))))
        object.__setattr__(self, "related_memories", list(dict.fromkeys(clean_text(x, limit=140) for x in self.related_memories if clean_text(x, limit=140))))
        if self.status not in {"simmering", "dormant", "surfaced", "decayed"}:
            object.__setattr__(self, "status", "simmering")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "theme": self.theme,
            "recurrence": round(float(self.recurrence), 3),
            "last_surface_at": round(float(self.last_surface_at), 3),
            "last_private_tick_at": round(float(self.last_private_tick_at), 3),
            "related_sparks": list(self.related_sparks),
            "related_memories": list(self.related_memories),
            "status": self.status,
        }


@dataclass(slots=True, frozen=True)
class WanderingThoughtResult:
    sparks: list[ThoughtSpark]
    fragments: list[ThoughtFragment]
    topic_candidates: list[str]
    mood_delta: float
    debug_reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "sparks": [item.to_dict() for item in self.sparks],
            "fragments": [item.to_dict() for item in self.fragments],
            "topic_candidates": list(self.topic_candidates),
            "mood_delta": round(float(self.mood_delta), 3),
            "debug_reason": self.debug_reason,
        }
