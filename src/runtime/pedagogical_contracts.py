"""Immutable, bounded contracts for the pedagogical conversation controller."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping


EPISODE_STATUSES = frozenset(
    {
        "planning", "ready", "speaking", "dwelling", "waiting_user", "observing",
        "repairing", "completed", "abandoned", "expired", "failed",
    }
)
MAX_ID = 120
MAX_TEXT = 420
MAX_SHORT_TEXT = 240
MAX_DIRECTIVE_IDEAS = 3
MAX_DIRECTIVE_SENTENCES = 5
MAX_COUNTER = 1_000_000
MAX_TIMESTAMP = 10**12


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value or "")[:limit]


def _ratio(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(0.0, min(1.0, number))


def _finite_float(value: Any, default: float = 0.0, *, maximum: float = MAX_TIMESTAMP) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(maximum, number))


def _bounded_int(value: Any, default: int = 0, *, minimum: int = 0, maximum: int = MAX_COUNTER) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(minimum, min(maximum, number))


def _boolean(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no", ""}:
            return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    return default


def _tuple_strings(value: Any, limit: int = 16) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(_text(item, MAX_SHORT_TEXT) for item in value[:limit])


@dataclass(frozen=True, slots=True)
class TeachingConcept:
    concept_id: str
    description: str
    objective: str = ""
    prerequisites: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "concept_id", _text(self.concept_id, MAX_ID))
        object.__setattr__(self, "description", _text(self.description))
        object.__setattr__(self, "objective", _text(self.objective))
        object.__setattr__(self, "prerequisites", _tuple_strings(self.prerequisites))

    def to_dict(self) -> dict[str, Any]:
        return {"concept_id": self.concept_id, "description": self.description,
                "objective": self.objective, "prerequisites": list(self.prerequisites)}

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TeachingConcept":
        return cls(payload.get("concept_id", payload.get("id", "")), payload.get("description", ""),
                   payload.get("objective", ""), tuple(payload.get("prerequisites", ())))


@dataclass(frozen=True, slots=True)
class ComprehensionState:
    estimated_mastery: float = 0.0
    confidence: float = 0.0
    current_concept_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "estimated_mastery", _ratio(self.estimated_mastery))
        object.__setattr__(self, "confidence", _ratio(self.confidence))
        object.__setattr__(self, "current_concept_id", _text(self.current_concept_id, MAX_ID))

    def to_dict(self) -> dict[str, Any]:
        return {"estimated_mastery": self.estimated_mastery, "confidence": self.confidence,
                "current_concept_id": self.current_concept_id}

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "ComprehensionState":
        payload = payload or {}
        return cls(payload.get("estimated_mastery", 0.0), payload.get("confidence", 0.0),
                   payload.get("current_concept_id", ""))


@dataclass(frozen=True, slots=True)
class ComprehensionEvidence:
    code: str
    concept_id: str = ""
    confidence: float = 0.0
    summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _text(self.code, MAX_SHORT_TEXT))
        object.__setattr__(self, "concept_id", _text(self.concept_id, MAX_ID))
        object.__setattr__(self, "confidence", _ratio(self.confidence))
        object.__setattr__(self, "summary", _text(self.summary))

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "concept_id": self.concept_id,
                "confidence": self.confidence, "summary": self.summary}


@dataclass(frozen=True, slots=True)
class MisconceptionHypothesis:
    hypothesis_id: str
    description: str
    concept_id: str = ""
    confidence: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "hypothesis_id", _text(self.hypothesis_id, MAX_ID))
        object.__setattr__(self, "description", _text(self.description))
        object.__setattr__(self, "concept_id", _text(self.concept_id, MAX_ID))
        object.__setattr__(self, "confidence", _ratio(self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {"hypothesis_id": self.hypothesis_id, "description": self.description,
                "concept_id": self.concept_id, "confidence": self.confidence}


@dataclass(frozen=True, slots=True)
class TeachingChunk:
    chunk_id: str
    concept_id: str
    objective: str
    claim: str = ""
    contrast: str = ""
    example_policy: str = "allowed"
    max_ideas: int = 2
    max_sentences: int = 4

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunk_id", _text(self.chunk_id, MAX_ID))
        object.__setattr__(self, "concept_id", _text(self.concept_id, MAX_ID))
        object.__setattr__(self, "objective", _text(self.objective))
        object.__setattr__(self, "claim", _text(self.claim))
        object.__setattr__(self, "contrast", _text(self.contrast))
        object.__setattr__(self, "example_policy", _text(self.example_policy, MAX_SHORT_TEXT))
        object.__setattr__(self, "max_ideas", _bounded_int(self.max_ideas, maximum=MAX_COUNTER))
        object.__setattr__(self, "max_sentences", _bounded_int(self.max_sentences, maximum=MAX_COUNTER))

    def to_dict(self) -> dict[str, Any]:
        return {"chunk_id": self.chunk_id, "concept_id": self.concept_id, "objective": self.objective,
                "claim": self.claim, "contrast": self.contrast, "example_policy": self.example_policy,
                "max_ideas": self.max_ideas, "max_sentences": self.max_sentences}

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TeachingChunk":
        return cls(payload.get("chunk_id", ""), payload.get("concept_id", ""), payload.get("objective", ""),
                   payload.get("claim", ""), payload.get("contrast", ""), payload.get("example_policy", "allowed"),
                   payload.get("max_ideas", 2), payload.get("max_sentences", 4))


@dataclass(frozen=True, slots=True)
class TeachingPlan:
    global_goal: str
    concepts: tuple[TeachingConcept, ...]
    prerequisite_edges: tuple[tuple[str, str], ...] = ()
    success_evidence: tuple[str, ...] = ()
    misconception_hypotheses: tuple[MisconceptionHypothesis, ...] = ()
    example_policy: str = "allowed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "global_goal", _text(self.global_goal))
        concepts = tuple(self.concepts)
        if len(concepts) < 3:
            raise ValueError("concepts_required")
        if len(concepts) > 8:
            raise ValueError("concepts_limit")
        object.__setattr__(self, "concepts", concepts)
        edges = tuple(((_text(edge[0], MAX_ID), _text(edge[1], MAX_ID)) for edge in self.prerequisite_edges
                       if isinstance(edge, (tuple, list)) and len(edge) >= 2))
        object.__setattr__(self, "prerequisite_edges", edges[:32])
        object.__setattr__(self, "success_evidence", _tuple_strings(self.success_evidence))
        object.__setattr__(self, "misconception_hypotheses", tuple(self.misconception_hypotheses[:16]))
        object.__setattr__(self, "example_policy", _text(self.example_policy, MAX_SHORT_TEXT))

    def to_dict(self) -> dict[str, Any]:
        return {"global_goal": self.global_goal, "concepts": [c.to_dict() for c in self.concepts],
                "prerequisite_edges": [list(edge) for edge in self.prerequisite_edges],
                "success_evidence": list(self.success_evidence),
                "misconception_hypotheses": [h.to_dict() for h in self.misconception_hypotheses],
                "example_policy": self.example_policy}


@dataclass(frozen=True, slots=True)
class TeachingDirective:
    episode_id: str
    plan_revision: int
    chunk_id: str
    global_goal: str
    current_objective: str
    claim: str
    contrast: str
    example_policy: str
    max_ideas: int
    max_sentences: int
    allow_question: bool
    stop_after_chunk: bool
    allow_autonomous_resume: bool
    claim_token: str = ""

    def __post_init__(self) -> None:
        for name in ("episode_id", "chunk_id", "claim_token"):
            object.__setattr__(self, name, _text(getattr(self, name), MAX_ID))
        for name in ("global_goal", "current_objective", "claim", "contrast", "example_policy"):
            object.__setattr__(self, name, _text(getattr(self, name)))
        object.__setattr__(self, "plan_revision", _bounded_int(self.plan_revision, 1, minimum=1))
        object.__setattr__(self, "max_ideas", _bounded_int(self.max_ideas, maximum=MAX_COUNTER))
        object.__setattr__(self, "max_sentences", _bounded_int(self.max_sentences, maximum=MAX_COUNTER))
        object.__setattr__(self, "allow_question", _boolean(self.allow_question))
        object.__setattr__(self, "stop_after_chunk", _boolean(self.stop_after_chunk))
        object.__setattr__(self, "allow_autonomous_resume", _boolean(self.allow_autonomous_resume))

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class TeachingReceipt:
    episode_id: str
    plan_revision: int
    chunk_id: str
    turn_id: str = ""
    text_signature: str = ""
    successful: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "episode_id", _text(self.episode_id, MAX_ID))
        object.__setattr__(self, "chunk_id", _text(self.chunk_id, MAX_ID))
        object.__setattr__(self, "turn_id", _text(self.turn_id, MAX_ID))
        object.__setattr__(self, "text_signature", _text(self.text_signature, MAX_SHORT_TEXT))
        object.__setattr__(self, "plan_revision", _bounded_int(self.plan_revision, 1, minimum=1))
        object.__setattr__(self, "successful", _boolean(self.successful, default=True))

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class TeachingEpisode:
    episode_id: str
    topic_key: str
    global_goal: str
    concepts: tuple[TeachingConcept, ...]
    started_at: float
    expires_at: float
    status: str = "planning"
    plan_revision: int = 1
    comprehension: ComprehensionState = ComprehensionState()
    pending_chunk: TeachingChunk | None = None
    autonomous_chunks_used: int = 0
    repair_attempts: int = 0
    consecutive_stalls: int = 0
    last_activity_at: float = 0.0
    repair_concept_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "episode_id", _text(self.episode_id, MAX_ID))
        object.__setattr__(self, "topic_key", _text(self.topic_key, MAX_ID))
        object.__setattr__(self, "global_goal", _text(self.global_goal))
        object.__setattr__(self, "concepts", tuple(self.concepts[:8]))
        object.__setattr__(self, "status", self.status if self.status in EPISODE_STATUSES else "failed")
        object.__setattr__(self, "started_at", _finite_float(self.started_at))
        object.__setattr__(self, "expires_at", _finite_float(self.expires_at))
        object.__setattr__(self, "plan_revision", _bounded_int(self.plan_revision, 1, minimum=1))
        object.__setattr__(self, "autonomous_chunks_used", _bounded_int(self.autonomous_chunks_used))
        object.__setattr__(self, "repair_attempts", _bounded_int(self.repair_attempts))
        object.__setattr__(self, "consecutive_stalls", _bounded_int(self.consecutive_stalls))
        object.__setattr__(self, "last_activity_at", _finite_float(self.last_activity_at))
        object.__setattr__(self, "repair_concept_id", _text(self.repair_concept_id, MAX_ID))
        if not isinstance(self.comprehension, ComprehensionState):
            object.__setattr__(self, "comprehension", ComprehensionState.from_mapping(self.comprehension))

    @classmethod
    def new(cls, *, episode_id: str, topic_key: str, global_goal: str,
            concepts: tuple[TeachingConcept, ...], now: float, expires_at: float) -> "TeachingEpisode":
        return cls(episode_id, topic_key, global_goal, concepts, now, expires_at, last_activity_at=now)

    def to_dict(self) -> dict[str, Any]:
        return {"episode_id": self.episode_id, "topic_key": self.topic_key, "global_goal": self.global_goal,
                "concepts": [c.to_dict() for c in self.concepts], "started_at": self.started_at,
                "expires_at": self.expires_at, "status": self.status, "plan_revision": self.plan_revision,
                "comprehension": self.comprehension.to_dict(),
                "pending_chunk": self.pending_chunk.to_dict() if self.pending_chunk else None,
                "autonomous_chunks_used": self.autonomous_chunks_used, "repair_attempts": self.repair_attempts,
                "consecutive_stalls": self.consecutive_stalls, "last_activity_at": self.last_activity_at,
                "repair_concept_id": self.repair_concept_id}

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TeachingEpisode":
        concepts = tuple(TeachingConcept.from_mapping(item) for item in payload.get("concepts", ()) if isinstance(item, Mapping))
        pending = payload.get("pending_chunk")
        return cls(payload.get("episode_id", ""), payload.get("topic_key", ""), payload.get("global_goal", ""),
                   concepts, payload.get("started_at", 0.0), payload.get("expires_at", 0.0),
                   payload.get("status", "planning"), payload.get("plan_revision", 1),
                   ComprehensionState.from_mapping(payload.get("comprehension")),
                   TeachingChunk.from_mapping(pending) if isinstance(pending, Mapping) else None,
                   payload.get("autonomous_chunks_used", 0), payload.get("repair_attempts", 0),
                   payload.get("consecutive_stalls", 0), payload.get("last_activity_at", 0.0),
                   payload.get("repair_concept_id", ""))


def normalize_episode(payload: Mapping[str, Any] | None) -> TeachingEpisode | None:
    if not isinstance(payload, Mapping) or payload.get("status") not in EPISODE_STATUSES:
        return None
    try:
        return TeachingEpisode.from_mapping(payload)
    except (TypeError, ValueError, KeyError):
        return None


def validate_directive(directive: TeachingDirective) -> tuple[bool, str]:
    if directive.max_ideas <= 0 or directive.max_sentences <= 0:
        return False, "directive_budget_exceeded"
    if directive.max_ideas > MAX_DIRECTIVE_IDEAS or directive.max_sentences > MAX_DIRECTIVE_SENTENCES:
        return False, "directive_budget_exceeded"
    if not directive.episode_id or not directive.chunk_id or directive.plan_revision < 1:
        return False, "directive_identity_invalid"
    return True, "ok"


__all__ = [
    "EPISODE_STATUSES", "TeachingPlan", "TeachingEpisode", "TeachingConcept", "ComprehensionState",
    "ComprehensionEvidence", "MisconceptionHypothesis", "TeachingChunk", "TeachingDirective",
    "TeachingReceipt", "normalize_episode", "validate_directive",
]
