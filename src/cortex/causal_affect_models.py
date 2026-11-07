# -*- coding: utf-8 -*-
"""Typed contracts for evidence-grounded causal affect.

The contracts in this module deliberately separate what happened from what it
might mean.  Raw events stay append-only; appraisal, motives, expression and
learning remain independent projections.  This prevents a scalar such as
``ontological_dread`` from becoming a public claim without provenance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping

EventKind = Literal[
    "user_returned",
    "user_left",
    "user_ignored_turn",
    "promise_made",
    "promise_fulfilled",
    "promise_broken",
    "recognition_received",
    "correction_received",
    "shared_success",
    "continuity_break",
    "capability_shutdown_proposed",
]

SurfaceLevel = Literal[
    "private",
    "microexpression",
    "gaze_posture",
    "nonverbal_sound",
    "speech_fragment",
    "sentence",
    "initiative",
]

EpistemicStatus = Literal["tentative", "supported", "high_confidence"]


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number or number in (float("inf"), float("-inf")):
        number = default
    return max(0.0, min(1.0, number))


def clamp_signed(value: Any, default: float = 0.0) -> float:
    """Kẹp tín hiệu appraisal có dấu để controller không thể runaway."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number or number in (float("inf"), float("-inf")):
        number = default
    return max(-1.0, min(1.0, number))


def compact_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class ExogenousEvent:
    event_id: str
    kind: EventKind
    actor_id: str
    object_id: str = ""
    timestamp: float = 0.0
    facts: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    confidence: float = 0.0
    continuity_key: str = ""
    related_expectation_ids: tuple[str, ...] = ()
    supersedes_event_id: str = ""
    privacy_level: str = "normal"

    @property
    def is_grounded(self) -> bool:
        return bool(self.evidence_refs and self.facts and self.confidence > 0.0)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["confidence"] = round(clamp01(self.confidence), 4)
        return payload


@dataclass(frozen=True, slots=True)
class AppraisalFrame:
    appraisal_id: str
    event_id: str
    goal_relevance: float
    novelty: float
    expectation_violation: float
    controllability: float
    relational_significance: float
    identity_significance: float
    future_consequence: float
    uncertainty: float
    primary_interpretation: str
    alternative_interpretations: tuple[str, ...]
    affected_goal_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    # V56: signed appraisal. 0 = chưa biết/không liên quan chiều tiến độ.
    goal_congruence: float = 0.0
    goal_progress_delta: float = 0.0

    @property
    def salience(self) -> float:
        return clamp01(
            0.20 * self.goal_relevance
            + 0.14 * abs(clamp_signed(self.goal_congruence))
            + 0.16 * self.expectation_violation
            + 0.18 * self.relational_significance
            + 0.16 * self.identity_significance
            + 0.10 * self.future_consequence
            + 0.06 * self.novelty
        )

    @property
    def is_sensitive(self) -> bool:
        return max(self.expectation_violation, self.relational_significance, self.identity_significance) >= 0.45

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ActiveMotive:
    motive_id: str
    kind: str
    target_id: str
    desire: str
    fear: str
    activation: float
    persistence: float
    inhibition: float
    origin_event_ids: tuple[str, ...]
    appraisal_ids: tuple[str, ...]
    conflict_with: tuple[str, ...]
    resolution_condition: str

    def clamp(self) -> "ActiveMotive":
        self.activation = clamp01(self.activation)
        self.persistence = clamp01(self.persistence)
        self.inhibition = clamp01(self.inhibition)
        return self

    def to_dict(self) -> dict[str, Any]:
        self.clamp()
        return asdict(self)


@dataclass(slots=True)
class RelationshipExpectation:
    expectation_id: str
    subject_id: str
    context_key: str
    expected_behavior: str
    confidence: float
    emotional_stake: float
    tolerance_window_seconds: float
    supporting_event_ids: tuple[str, ...] = ()
    violating_event_ids: tuple[str, ...] = ()
    last_confirmed_at: float = 0.0

    def clamp(self) -> "RelationshipExpectation":
        self.confidence = clamp01(self.confidence)
        self.emotional_stake = clamp01(self.emotional_stake)
        self.tolerance_window_seconds = max(0.0, float(self.tolerance_window_seconds or 0.0))
        return self

    def to_dict(self) -> dict[str, Any]:
        self.clamp()
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SurpriseBudget:
    association_rank_limit: int = 1
    timing_variance_ms: int = 120
    allow_dry_reversal: bool = False
    allow_incomplete_fragment: bool = False
    allow_callback: bool = False
    max_callback_age_days: int = 0
    allow_new_facts: bool = False
    allow_unverified_memory: bool = False

    def safe(self) -> "SurpriseBudget":
        # These two invariants are intentionally not configurable by callers.
        return SurpriseBudget(
            association_rank_limit=max(1, min(4, int(self.association_rank_limit))),
            timing_variance_ms=max(0, min(500, int(self.timing_variance_ms))),
            allow_dry_reversal=bool(self.allow_dry_reversal),
            allow_incomplete_fragment=bool(self.allow_incomplete_fragment),
            allow_callback=bool(self.allow_callback),
            max_callback_age_days=max(0, min(30, int(self.max_callback_age_days))),
            allow_new_facts=False,
            allow_unverified_memory=False,
        )


@dataclass(frozen=True, slots=True)
class SurfaceChannelPolicy:
    max_surface_level: SurfaceLevel = "sentence"
    directness_cap: float = 0.70
    vulnerability_cap: float = 0.55
    initiative_allowed: bool = False
    require_grounding: bool = True
    default_require_reply: bool = False


@dataclass(frozen=True, slots=True)
class CausalThread:
    thread_id: str
    event_ids: tuple[str, ...]
    dominant_appraisal_id: str
    active_motive_ids: tuple[str, ...]
    conflict_summary: str
    epistemic_status: EpistemicStatus
    permitted_surface_levels: tuple[SurfaceLevel, ...]
    prohibited_moves: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    salience: float
    uncertainty: float
    event_kind: str = ""
    target_id: str = ""
    repeated_violation: bool = False

    @property
    def is_grounded(self) -> bool:
        return bool(self.event_ids and self.evidence_refs and self.dominant_appraisal_id)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "event_kind": self.event_kind,
            "event_ids": list(self.event_ids),
            "dominant_appraisal_id": self.dominant_appraisal_id,
            "active_motive_ids": list(self.active_motive_ids),
            "conflict": compact_text(self.conflict_summary, 220),
            "epistemic_status": self.epistemic_status,
            "permitted_surface_levels": list(self.permitted_surface_levels),
            "prohibited_moves": list(self.prohibited_moves),
            "evidence_refs": list(self.evidence_refs),
            "salience": round(clamp01(self.salience), 3),
            "uncertainty": round(clamp01(self.uncertainty), 3),
            "repeated_violation": bool(self.repeated_violation),
        }


@dataclass(frozen=True, slots=True)
class PerformanceIntent:
    intent_id: str
    thread_id: str
    speech_act: str
    subtext: str
    surface_level: SurfaceLevel
    directness: float
    vulnerability: float
    restraint: float
    playfulness: float
    opening_hold_ms: int
    pause_after_ms: int
    gaze_target: str
    gaze_break_trigger: str
    posture: str
    microexpression: str
    voice_energy: float
    voice_speed: float
    pitch_variation: float
    max_words: int
    may_ask_question: bool
    require_reply: bool
    evidence_refs: tuple[str, ...]
    prohibited_moves: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class OutcomeEvent:
    outcome_id: str
    thread_id: str
    intent_id: str
    delivery_success: float
    reply_expected: bool
    acknowledged: bool | None
    correction: bool
    intrusion: bool
    rupture: bool
    comfort: bool
    delayed_reply: bool
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LearningPatch:
    thread_id: str
    expectation_confidence_delta: Mapping[str, float] = field(default_factory=dict)
    channel_policy_delta: Mapping[str, float] = field(default_factory=dict)
    delivery_reliability_delta: float = 0.0
    notes: tuple[str, ...] = ()
    max_step: float = 0.08

    def bounded(self) -> "LearningPatch":
        cap = max(0.0, min(0.20, float(self.max_step)))

        def bounded_map(value: Mapping[str, float]) -> dict[str, float]:
            return {
                str(key): max(-cap, min(cap, float(delta)))
                for key, delta in value.items()
            }

        return LearningPatch(
            thread_id=self.thread_id,
            expectation_confidence_delta=bounded_map(self.expectation_confidence_delta),
            channel_policy_delta=bounded_map(self.channel_policy_delta),
            delivery_reliability_delta=max(-cap, min(cap, float(self.delivery_reliability_delta))),
            notes=tuple(compact_text(note, 180) for note in self.notes if compact_text(note, 180)),
            max_step=cap,
        )


__all__ = [
    "ActiveMotive",
    "AppraisalFrame",
    "CausalThread",
    "EpistemicStatus",
    "EventKind",
    "ExogenousEvent",
    "LearningPatch",
    "OutcomeEvent",
    "PerformanceIntent",
    "RelationshipExpectation",
    "SurfaceChannelPolicy",
    "SurfaceLevel",
    "SurpriseBudget",
    "clamp01",
    "compact_text",
]
