"""Immutable domain contracts for the learning kernel.

All types are frozen dataclasses with bounded post-init validation.
No Pydantic, no I/O, no imports from the rest of Eris runtime.

Ported from HKUDS/DeepTutor (Apache 2.0) with significant adaptation
to Eris frozen-dataclass conventions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping


# ---------------------------------------------------------------------------
# Primitive sanitisers (no external dependencies)
# ---------------------------------------------------------------------------

_MAX_ID = 120
_MAX_TEXT = 500
_MAX_SHORT = 240
_MAX_TS = 10 ** 12


def _text(v: Any, limit: int = _MAX_TEXT) -> str:
    return str(v or "")[:limit]


def _sid(v: Any) -> str:
    return _text(v, _MAX_ID).strip()


def _ratio(v: Any, default: float = 0.0) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = default
    if not math.isfinite(n):
        n = default
    return max(0.0, min(1.0, n))


def _pos_float(v: Any, default: float, *, hi: float = _MAX_TS) -> float:
    try:
        n = float(v)
    except (TypeError, ValueError):
        n = default
    if not math.isfinite(n):
        n = default
    return max(0.0, min(hi, n))


def _int(v: Any, default: int = 0, *, lo: int = 0, hi: int = 1_000_000) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError, OverflowError):
        n = default
    return max(lo, min(hi, n))


def _bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "1", "yes"}
    if isinstance(v, (int, float)):
        return v == 1
    return default


def _strs(v: Any, limit: int = 32) -> tuple[str, ...]:
    if not isinstance(v, (list, tuple)):
        return ()
    return tuple(_sid(x) for x in v[:limit])


# ---------------------------------------------------------------------------
# KnowledgePoint  (curriculum node)
# ---------------------------------------------------------------------------

# Supported KP types – controls which mastery gate applies.
KP_TYPES = frozenset({"fact", "procedure", "concept", "design"})

# Mastery thresholds by type (mirrors DeepTutor mastery policy)
MASTERY_THRESHOLDS: dict[str, float] = {
    "fact":      0.90,
    "procedure": 0.90,
    "concept":   0.75,
    "design":    0.70,
}


@dataclass(frozen=True, slots=True)
class KnowledgePoint:
    """A single curriculum node (mirrors DeepTutor KnowledgePoint)."""

    kp_id: str
    title: str
    kp_type: str = "concept"        # fact | procedure | concept | design
    description: str = ""
    prerequisites: tuple[str, ...] = ()
    mastery_threshold: float = 0.75

    def __post_init__(self) -> None:
        object.__setattr__(self, "kp_id", _sid(self.kp_id))
        object.__setattr__(self, "title", _text(self.title, _MAX_SHORT))
        t = _text(self.kp_type, _MAX_ID).lower()
        object.__setattr__(self, "kp_type", t if t in KP_TYPES else "concept")
        object.__setattr__(self, "description", _text(self.description))
        object.__setattr__(self, "prerequisites", _strs(self.prerequisites))
        threshold = MASTERY_THRESHOLDS.get(self.kp_type, 0.75)
        object.__setattr__(self, "mastery_threshold", threshold)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kp_id": self.kp_id,
            "title": self.title,
            "kp_type": self.kp_type,
            "description": self.description,
            "prerequisites": list(self.prerequisites),
            "mastery_threshold": self.mastery_threshold,
        }

    @classmethod
    def from_mapping(cls, d: Mapping[str, Any]) -> "KnowledgePoint":
        return cls(
            kp_id=d.get("kp_id", d.get("id", "")),
            title=d.get("title", ""),
            kp_type=d.get("kp_type", "concept"),
            description=d.get("description", ""),
            prerequisites=tuple(d.get("prerequisites", ())),
        )


# ---------------------------------------------------------------------------
# LearningPath  (ordered sequence of KPs)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LearningPath:
    """Ordered curriculum for one learning goal."""

    path_id: str
    title: str
    goal: str
    knowledge_points: tuple[KnowledgePoint, ...]
    source: str = "user"            # user | companion | eris_auto
    domain_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "path_id", _sid(self.path_id))
        object.__setattr__(self, "title", _text(self.title, _MAX_SHORT))
        object.__setattr__(self, "goal", _text(self.goal))
        kps = tuple(self.knowledge_points)
        if not kps:
            raise ValueError("learning_path_requires_at_least_one_kp")
        object.__setattr__(self, "knowledge_points", kps)
        object.__setattr__(self, "source", _sid(self.source) or "user")
        object.__setattr__(self, "domain_id", _sid(self.domain_id))

    @property
    def kp_ids(self) -> tuple[str, ...]:
        return tuple(kp.kp_id for kp in self.knowledge_points)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "title": self.title,
            "goal": self.goal,
            "knowledge_points": [kp.to_dict() for kp in self.knowledge_points],
            "source": self.source,
            "domain_id": self.domain_id,
        }


# ---------------------------------------------------------------------------
# MasteryRecord  (per-user, per-KP durable state)
# ---------------------------------------------------------------------------

MASTERY_STAGES = frozenset({"unknown", "learning", "practicing", "mastered", "retained"})


@dataclass(frozen=True, slots=True)
class MasteryRecord:
    """Durable mastery snapshot for one (user, kp_id) pair."""

    kp_id: str
    mastery: float = 0.0            # recency-weighted [0, 1]
    confidence: float = 0.0         # confidence cap [0, 1]
    stage: str = "unknown"
    attempt_count: int = 0
    correct_count: int = 0
    last_evidence_at: float = 0.0
    next_review_at: float = 0.0
    retrievability: float = 1.0     # forgetting curve [0, 1]
    error_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "kp_id", _sid(self.kp_id))
        object.__setattr__(self, "mastery", _ratio(self.mastery))
        object.__setattr__(self, "confidence", _ratio(self.confidence))
        s = _sid(self.stage)
        object.__setattr__(self, "stage", s if s in MASTERY_STAGES else "unknown")
        object.__setattr__(self, "attempt_count", _int(self.attempt_count))
        object.__setattr__(self, "correct_count", _int(self.correct_count))
        object.__setattr__(self, "last_evidence_at", _pos_float(self.last_evidence_at, 0.0))
        object.__setattr__(self, "next_review_at", _pos_float(self.next_review_at, 0.0))
        object.__setattr__(self, "retrievability", _ratio(self.retrievability, 1.0))
        object.__setattr__(self, "error_codes", _strs(self.error_codes, 16))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kp_id": self.kp_id,
            "mastery": round(self.mastery, 4),
            "confidence": round(self.confidence, 4),
            "stage": self.stage,
            "attempt_count": self.attempt_count,
            "correct_count": self.correct_count,
            "last_evidence_at": self.last_evidence_at,
            "next_review_at": self.next_review_at,
            "retrievability": round(self.retrievability, 4),
            "error_codes": list(self.error_codes),
        }

    @classmethod
    def blank(cls, kp_id: str) -> "MasteryRecord":
        return cls(kp_id=kp_id)

    @classmethod
    def from_mapping(cls, d: Mapping[str, Any]) -> "MasteryRecord":
        return cls(
            kp_id=d.get("kp_id", ""),
            mastery=d.get("mastery", 0.0),
            confidence=d.get("confidence", 0.0),
            stage=d.get("stage", "unknown"),
            attempt_count=d.get("attempt_count", 0),
            correct_count=d.get("correct_count", 0),
            last_evidence_at=d.get("last_evidence_at", 0.0),
            next_review_at=d.get("next_review_at", 0.0),
            retrievability=d.get("retrievability", 1.0),
            error_codes=d.get("error_codes", ()),
        )


# ---------------------------------------------------------------------------
# LearningEvidence  (one graded interaction result)
# ---------------------------------------------------------------------------

EVIDENCE_TYPES = frozenset({
    "quiz_correct",
    "quiz_incorrect",
    "feynman_pass",
    "feynman_fail",
    "application",
    "paraphrase",
    "self_report",
    "misconception",
    "exposure",
})


@dataclass(frozen=True, slots=True)
class LearningEvidence:
    """One atomic piece of evidence about a learner's knowledge state."""

    evidence_id: str
    kp_id: str
    evidence_type: str              # see EVIDENCE_TYPES
    strength: float = 0.5           # [0, 1]; 1 = full demonstration
    correct: bool = True
    interaction_id: str = ""
    turn_id: str = ""
    recorded_at: float = 0.0
    error_code: str = ""
    metadata: str = ""              # compact JSON string, max 4096 chars

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_id", _sid(self.evidence_id))
        object.__setattr__(self, "kp_id", _sid(self.kp_id))
        et = _sid(self.evidence_type)
        object.__setattr__(self, "evidence_type", et if et in EVIDENCE_TYPES else "exposure")
        object.__setattr__(self, "strength", _ratio(self.strength, 0.5))
        object.__setattr__(self, "correct", _bool(self.correct, True))
        object.__setattr__(self, "interaction_id", _sid(self.interaction_id))
        object.__setattr__(self, "turn_id", _sid(self.turn_id))
        object.__setattr__(self, "recorded_at", _pos_float(self.recorded_at, 0.0))
        object.__setattr__(self, "error_code", _sid(self.error_code))
        object.__setattr__(self, "metadata", _text(self.metadata, 4096))

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "kp_id": self.kp_id,
            "evidence_type": self.evidence_type,
            "strength": round(self.strength, 4),
            "correct": self.correct,
            "interaction_id": self.interaction_id,
            "turn_id": self.turn_id,
            "recorded_at": self.recorded_at,
            "error_code": self.error_code,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# PendingInteraction  (server-side question lifecycle)
# ---------------------------------------------------------------------------

INTERACTION_TYPES = frozenset({
    "multiple_choice",
    "true_false",
    "feynman_explain",
    "application",
    "short_answer",
})

PENDING_STATUSES = frozenset({"pending", "answered", "expired", "cancelled"})


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    """Server-owned record of a question posed to the learner.

    The expected_answer is only stored server-side and never in the public
    writer output. Grading happens deterministically against this record.
    """

    interaction_id: str
    path_id: str
    kp_id: str
    interaction_type: str
    question_text: str
    expected_answer: str            # canonical answer / key for grading
    options: tuple[str, ...] = ()   # for multiple_choice; empty otherwise
    correct_index: int = -1         # index into options; -1 if N/A
    status: str = "pending"
    created_at: float = 0.0
    expires_at: float = 0.0
    answered_at: float = 0.0
    turn_id: str = ""
    review_recall: bool = False  # only set by trusted question-authoring caller

    def __post_init__(self) -> None:
        object.__setattr__(self, "review_recall", _bool(self.review_recall))
        object.__setattr__(self, "interaction_id", _sid(self.interaction_id))
        object.__setattr__(self, "path_id", _sid(self.path_id))
        object.__setattr__(self, "kp_id", _sid(self.kp_id))
        it = _sid(self.interaction_type)
        object.__setattr__(self, "interaction_type", it if it in INTERACTION_TYPES else "short_answer")
        object.__setattr__(self, "question_text", _text(self.question_text))
        object.__setattr__(self, "expected_answer", _text(self.expected_answer))
        object.__setattr__(self, "options", _strs(self.options, 6))
        object.__setattr__(self, "correct_index", _int(self.correct_index, -1, lo=-1, hi=5))
        st = _sid(self.status)
        object.__setattr__(self, "status", st if st in PENDING_STATUSES else "pending")
        object.__setattr__(self, "created_at", _pos_float(self.created_at, 0.0))
        object.__setattr__(self, "expires_at", _pos_float(self.expires_at, 0.0))
        object.__setattr__(self, "answered_at", _pos_float(self.answered_at, 0.0))
        object.__setattr__(self, "turn_id", _sid(self.turn_id))

    @property
    def is_choice(self) -> bool:
        return self.interaction_type in {"multiple_choice", "true_false"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_id": self.interaction_id,
            "path_id": self.path_id,
            "kp_id": self.kp_id,
            "review_recall": self.review_recall,
            "interaction_type": self.interaction_type,
            "question_text": self.question_text,
            "options": list(self.options),
            "correct_index": self.correct_index,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "answered_at": self.answered_at,
            "turn_id": self.turn_id,
            # expected_answer intentionally omitted from public dict
        }

    @classmethod
    def from_mapping(cls, d: Mapping[str, Any]) -> "PendingInteraction":
        return cls(
            interaction_id=d.get("interaction_id", ""),
            path_id=d.get("path_id", ""),
            kp_id=d.get("kp_id", ""),
            interaction_type=d.get("interaction_type", "short_answer"),
            question_text=d.get("question_text", ""),
            expected_answer=d.get("expected_answer", ""),
            options=d.get("options", ()),
            correct_index=d.get("correct_index", -1),
            status=d.get("status", "pending"),
            created_at=d.get("created_at", 0.0),
            expires_at=d.get("expires_at", 0.0),
            answered_at=d.get("answered_at", 0.0),
            turn_id=d.get("turn_id", ""),
            review_recall=d.get("review_recall", False),
        )


# ---------------------------------------------------------------------------
# ReviewItem  (spaced-repetition queue entry)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ReviewItem:
    """One entry in the spaced-repetition review queue."""

    kp_id: str
    due_at: float
    forgetting_risk: float = 0.0    # [0, 1]; higher = review sooner
    interval_days: float = 1.0
    ease_factor: float = 2.5        # SM-2 style

    def __post_init__(self) -> None:
        object.__setattr__(self, "kp_id", _sid(self.kp_id))
        object.__setattr__(self, "due_at", _pos_float(self.due_at, 0.0))
        object.__setattr__(self, "forgetting_risk", _ratio(self.forgetting_risk))
        object.__setattr__(self, "interval_days", _pos_float(self.interval_days, 1.0, hi=365.0))
        ef = _pos_float(self.ease_factor, 2.5, hi=5.0)
        object.__setattr__(self, "ease_factor", max(1.3, ef))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kp_id": self.kp_id,
            "due_at": self.due_at,
            "forgetting_risk": round(self.forgetting_risk, 4),
            "interval_days": round(self.interval_days, 2),
            "ease_factor": round(self.ease_factor, 3),
        }


# ---------------------------------------------------------------------------
# LearnerProfile  (long-term preferences; owned by living_user projection)
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class LearnerProfile:
    """Read-only projection of learner preferences for the learning kernel.

    The authoritative copy lives in living_user. This is a bounded snapshot
    for passing into pure policy functions without pulling in living_user deps.
    """

    preferred_pace: str = "normal"      # slow | normal | fast
    preferred_style: str = "conceptual" # conceptual | procedural | mixed
    native_language: str = "vi"
    goal_description: str = ""
    active_domain: str = ""

    def __post_init__(self) -> None:
        pace = _sid(self.preferred_pace)
        object.__setattr__(self, "preferred_pace", pace if pace in {"slow", "normal", "fast"} else "normal")
        style = _sid(self.preferred_style)
        object.__setattr__(self, "preferred_style", style if style in {"conceptual", "procedural", "mixed"} else "conceptual")
        object.__setattr__(self, "native_language", _sid(self.native_language) or "vi")
        object.__setattr__(self, "goal_description", _text(self.goal_description, _MAX_SHORT))
        object.__setattr__(self, "active_domain", _sid(self.active_domain))

    def to_dict(self) -> dict[str, Any]:
        return {
            "preferred_pace": self.preferred_pace,
            "preferred_style": self.preferred_style,
            "native_language": self.native_language,
            "goal_description": self.goal_description,
            "active_domain": self.active_domain,
        }

    @classmethod
    def from_mapping(cls, d: Mapping[str, Any] | None) -> "LearnerProfile":
        d = d or {}
        return cls(
            preferred_pace=d.get("preferred_pace", "normal"),
            preferred_style=d.get("preferred_style", "conceptual"),
            native_language=d.get("native_language", "vi"),
            goal_description=d.get("goal_description", ""),
            active_domain=d.get("active_domain", ""),
        )


__all__ = [
    "KnowledgePoint",
    "LearnerProfile",
    "LearningEvidence",
    "LearningPath",
    "MasteryRecord",
    "PendingInteraction",
    "ReviewItem",
    "KP_TYPES",
    "MASTERY_THRESHOLDS",
    "MASTERY_STAGES",
    "EVIDENCE_TYPES",
    "INTERACTION_TYPES",
    "PENDING_STATUSES",
]
