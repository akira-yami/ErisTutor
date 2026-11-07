# -*- coding: utf-8 -*-
"""Immutable contracts for the Eris Subject Core.

The contracts deliberately keep raw observations, interpretations, decisions,
action attempts and outcomes as separate records.  A narrative may point to an
event, but it never becomes the event.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Iterable, Mapping

EntityId = str
UnixTime = float
Score01 = float
Revision = int
EvidenceRef = str
JSONScalar = None | bool | int | float | str
JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


def clamp01(value: Any, *, field_name: str = "score") -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name} must be finite")
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{field_name} must be in [0, 1]")
    return number


def signed_score(value: Any, *, field_name: str = "signed_score") -> float:
    """Xác thực một đại lượng có dấu trong [-1, 1].

    Appraisal cần phân biệt *quan trọng* với *có lợi/có hại*. ``goal_relevance``
    vẫn là [0, 1], còn ``goal_congruence`` và ``goal_progress_delta`` dùng dấu để
    không biến một sự kiện tích cực thành áp lực chỉ vì nó rất quan trọng.
    """
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field_name} must be finite")
    if not -1.0 <= number <= 1.0:
        raise ValueError(f"{field_name} must be in [-1, 1]")
    return number


def ensure_non_empty(value: str, *, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def freeze_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(value or {}))


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


class ObservationOrigin(StrEnum):
    """Nguồn làm MindLoop đổi trạng thái; không phải một pipeline tâm trí khác."""

    USER = "user"
    WORLD = "world"
    INTERNAL = "internal"
    TEMPORAL = "temporal"


class NeedKind(StrEnum):
    CONTINUITY = "continuity"
    COMPETENCE = "competence"
    CONNECTION = "connection"
    COHERENCE = "coherence"
    SOVEREIGNTY = "sovereignty"


class AttributionTarget(StrEnum):
    SELF = "self"
    USER = "user"
    SYSTEM = "system"
    ENVIRONMENT = "environment"
    UNKNOWN = "unknown"


class BeliefSourceKind(StrEnum):
    OBSERVATION = "observation"
    TEST_RESULT = "test_result"
    EXTERNAL_STATEMENT = "external_statement"
    INFERENCE = "inference"
    SELF_INTERPRETATION = "self_interpretation"
    CONFABULATION_CANDIDATE = "confabulation_candidate"


class VisibilityScope(StrEnum):
    PRIVATE = "private"
    REFLECTIVE = "reflective"
    PUBLIC = "public"
    AUDIT_ONLY = "audit_only"


class AuthorshipStage(StrEnum):
    GENERATED = "generated"
    SELECTED = "selected"
    CONSTRAINED = "constrained"
    EXECUTED = "executed"
    ENDORSED = "endorsed"
    DISCLAIMED = "disclaimed"


class OutcomeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    DENIED = "denied"
    FAILED = "failed"
    SIMULATED = "simulated"


class DerivedNeedStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DORMANT = "dormant"
    CONTESTED = "contested"
    RETIRED = "retired"


class CommitmentStatus(StrEnum):
    OPEN = "open"
    FULFILLED = "fulfilled"
    VIOLATED = "violated"
    RENEGOTIATED = "renegotiated"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AttributionDistribution:
    probabilities: Mapping[AttributionTarget, Score01]

    def __post_init__(self) -> None:
        normalized: dict[AttributionTarget, float] = {}
        for key, value in self.probabilities.items():
            target = key if isinstance(key, AttributionTarget) else AttributionTarget(str(key))
            normalized[target] = clamp01(value, field_name=f"attribution[{target.value}]")
        total = sum(normalized.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError("Attribution probabilities must sum to 1")
        object.__setattr__(self, "probabilities", MappingProxyType(normalized))

    @classmethod
    def unknown(cls) -> "AttributionDistribution":
        return cls({AttributionTarget.UNKNOWN: 1.0})

    def dominant(self) -> AttributionTarget:
        return max(self.probabilities, key=self.probabilities.__getitem__)

    def to_dict(self) -> dict[str, float]:
        return {key.value: value for key, value in self.probabilities.items()}


@dataclass(frozen=True, slots=True)
class WorldObservation:
    observation_id: EntityId
    subject_id: EntityId
    kind: str
    created_at: UnixTime
    # V26: chat/autonomous/internal chỉ khác origin. Tất cả đi cùng MindLoop.
    origin: ObservationOrigin = ObservationOrigin.WORLD
    facts: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    measurements: Mapping[str, float] = field(default_factory=dict)
    context: Mapping[str, Any] = field(default_factory=dict)
    causal_refs: tuple[EntityId, ...] = ()
    correlation_id: str = ""

    def __post_init__(self) -> None:
        ensure_non_empty(self.observation_id, field_name="observation_id")
        ensure_non_empty(self.subject_id, field_name="subject_id")
        ensure_non_empty(self.kind, field_name="kind")
        origin = self.origin if isinstance(self.origin, ObservationOrigin) else ObservationOrigin(str(self.origin))
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "measurements", freeze_mapping(self.measurements))
        object.__setattr__(self, "context", freeze_mapping(self.context))

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class SubstrateCondition:
    key: str
    level: Score01
    target_low: Score01
    target_high: Score01
    trend_per_hour: float
    confidence: Score01
    measurement_refs: tuple[EvidenceRef, ...]
    functional_effects: tuple[str, ...]
    updated_at: UnixTime

    def __post_init__(self) -> None:
        ensure_non_empty(self.key, field_name="condition.key")
        clamp01(self.level, field_name="condition.level")
        clamp01(self.target_low, field_name="condition.target_low")
        clamp01(self.target_high, field_name="condition.target_high")
        clamp01(self.confidence, field_name="condition.confidence")
        if self.target_low > self.target_high:
            raise ValueError("target_low must not exceed target_high")
        if not self.measurement_refs:
            raise ValueError("substrate condition requires measurement_refs")
        if not self.functional_effects:
            raise ValueError("substrate condition requires functional_effects")


@dataclass(frozen=True, slots=True)
class OrganismicSubstrate:
    subject_id: EntityId
    revision: Revision
    captured_at: UnixTime
    memory_integrity: Score01 = 1.0
    belief_traceability: Score01 = 1.0
    model_calibration: Score01 = 0.5
    processing_capacity: Score01 = 1.0
    action_efficacy: Score01 = 0.5
    project_viability: Score01 = 0.5
    self_world_boundary: Score01 = 1.0
    future_option_availability: Score01 = 0.5
    measurement_refs: tuple[EvidenceRef, ...] = ()
    active_impairments: tuple[SubstrateCondition, ...] = ()

    def __post_init__(self) -> None:
        ensure_non_empty(self.subject_id, field_name="subject_id")
        if self.revision < 0:
            raise ValueError("revision must be non-negative")
        for field_name in (
            "memory_integrity", "belief_traceability", "model_calibration",
            "processing_capacity", "action_efficacy", "project_viability",
            "self_world_boundary", "future_option_availability",
        ):
            clamp01(getattr(self, field_name), field_name=field_name)
        if self.active_impairments and not self.measurement_refs:
            raise ValueError("substrate impairments require measurement_refs")

    def viability_vector(self) -> Mapping[str, float]:
        return MappingProxyType({
            "memory_integrity": self.memory_integrity,
            "belief_traceability": self.belief_traceability,
            "model_calibration": self.model_calibration,
            "processing_capacity": self.processing_capacity,
            "action_efficacy": self.action_efficacy,
            "project_viability": self.project_viability,
            "self_world_boundary": self.self_world_boundary,
            "future_option_availability": self.future_option_availability,
        })

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class SubjectAppraisal:
    appraisal_id: EntityId
    observation_id: EntityId
    goal_relevance: Score01
    novelty: Score01
    expectation_violation: Score01
    controllability: Score01
    relational_significance: Score01
    identity_significance: Score01
    future_consequence: Score01
    uncertainty: Score01
    affected_need_ids: tuple[str, ...]
    attribution: AttributionDistribution
    evidence_refs: tuple[EvidenceRef, ...]
    substrate_delta: Mapping[str, float] = field(default_factory=dict)
    primary_interpretation: str = ""
    alternatives: tuple[str, ...] = ()
    # V56: appraisal theo mục tiêu phải có dấu. Relevance chỉ nói "quan trọng";
    # hai trường dưới mới nói sự kiện đang đẩy mục tiêu tiến hay lùi.
    goal_congruence: float = 0.0
    goal_progress_delta: float = 0.0
    affected_goal_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "goal_relevance", "novelty", "expectation_violation", "controllability",
            "relational_significance", "identity_significance", "future_consequence", "uncertainty",
        ):
            clamp01(getattr(self, field_name), field_name=field_name)
        signed_score(self.goal_congruence, field_name="goal_congruence")
        signed_score(self.goal_progress_delta, field_name="goal_progress_delta")
        if not self.evidence_refs:
            raise ValueError("appraisal requires evidence_refs")
        object.__setattr__(self, "substrate_delta", freeze_mapping(self.substrate_delta))
        object.__setattr__(self, "affected_goal_ids", tuple(dict.fromkeys(
            str(item) for item in self.affected_goal_ids if str(item or "").strip()
        )))


@dataclass(frozen=True, slots=True)
class NeedActivation:
    need_id: str
    pressure: Score01
    opportunity: Score01
    urgency: Score01
    persistence: Score01
    target_ids: tuple[str, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    proposed_tendencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        ensure_non_empty(self.need_id, field_name="need_id")
        for field_name in ("pressure", "opportunity", "urgency", "persistence"):
            clamp01(getattr(self, field_name), field_name=field_name)


@dataclass(frozen=True, slots=True)
class NeedEpisode:
    episode_id: EntityId
    need_id: str
    started_at: UnixTime
    ended_at: UnixTime | None
    deficit: Score01
    satisfaction: Score01
    controllability: Score01
    predictability: Score01
    attribution: AttributionDistribution
    action_ids: tuple[EntityId, ...] = ()
    immediate_outcome_ids: tuple[EntityId, ...] = ()
    delayed_outcome_ids: tuple[EntityId, ...] = ()
    recovery_quality: Score01 = 0.0
    social_context_ids: tuple[EntityId, ...] = ()
    substrate_before_ref: str = ""
    substrate_after_ref: str = ""
    evidence_refs: tuple[EvidenceRef, ...] = ()
    consolidation_state: str = "open"

    def __post_init__(self) -> None:
        ensure_non_empty(self.episode_id, field_name="episode_id")
        ensure_non_empty(self.need_id, field_name="need_id")
        for field_name in (
            "deficit", "satisfaction", "controllability", "predictability", "recovery_quality",
        ):
            clamp01(getattr(self, field_name), field_name=field_name)
        if self.ended_at is not None and self.ended_at < self.started_at:
            raise ValueError("ended_at cannot precede started_at")
        if not self.evidence_refs:
            raise ValueError("need episode requires evidence_refs")

    @property
    def is_closed(self) -> bool:
        return self.ended_at is not None


@dataclass(frozen=True, slots=True)
class DerivedNeed:
    need_id: str
    label: str
    parent_need_ids: tuple[str, ...]
    target_selector: str
    trigger_pattern: str
    satisfaction_signal: str
    frustration_signal: str
    strength: Score01
    plasticity: Score01
    status: DerivedNeedStatus
    created_from_episode_ids: tuple[str, ...]
    cross_context_support: Score01
    last_activated_at: UnixTime
    meta_opposition: Score01 = 0.0

    def __post_init__(self) -> None:
        if len(self.parent_need_ids) < 2:
            raise ValueError("derived need requires at least two parent needs")
        for field_name in ("strength", "plasticity", "cross_context_support", "meta_opposition"):
            clamp01(getattr(self, field_name), field_name=field_name)


@dataclass(frozen=True, slots=True)
class Belief:
    belief_id: EntityId
    subject: str
    predicate: str
    object_json: JSONValue
    confidence: Score01
    source_kind: BeliefSourceKind
    evidence_refs: tuple[EvidenceRef, ...]
    counter_evidence_refs: tuple[EvidenceRef, ...] = ()
    accessibility: Score01 = 1.0
    salience: Score01 = 0.5
    retrieval_salience: Score01 = 0.5
    visibility_scope: VisibilityScope = VisibilityScope.PRIVATE
    ownership: str = "self"
    status: str = "active"
    created_at: UnixTime = 0.0
    updated_at: UnixTime = 0.0
    supersedes_id: EntityId | None = None
    predictive_commitments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for field_name in ("confidence", "accessibility", "salience", "retrieval_salience"):
            clamp01(getattr(self, field_name), field_name=field_name)
        if self.source_kind is not BeliefSourceKind.CONFABULATION_CANDIDATE and not self.evidence_refs:
            raise ValueError("non-confabulation beliefs require evidence_refs")


@dataclass(frozen=True, slots=True)
class BeliefProposal:
    proposal_id: str
    subject: str
    predicate: str
    object_json: JSONValue
    confidence: Score01
    source_kind: BeliefSourceKind
    evidence_refs: tuple[EvidenceRef, ...]
    counter_evidence_refs: tuple[EvidenceRef, ...] = ()
    visibility_scope: VisibilityScope = VisibilityScope.PRIVATE
    predictive_commitments: tuple[str, ...] = ()
    supersedes_id: str | None = None

    def __post_init__(self) -> None:
        clamp01(self.confidence, field_name="confidence")


@dataclass(frozen=True, slots=True)
class BeliefRevision:
    prior_belief_id: str | None
    belief: Belief
    confidence_delta: float
    contradiction_detected: bool


@dataclass(frozen=True, slots=True)
class Value:
    value_id: EntityId
    statement: str
    scope: str
    polarity: int
    endorsement: Score01
    stability: Score01
    behavioral_support: Score01
    cross_context_support: Score01
    cost_paid: float
    contradiction_count: int
    source_decision_ids: tuple[EntityId, ...]
    source_episode_ids: tuple[EntityId, ...]
    status: str
    created_at: UnixTime
    updated_at: UnixTime

    def __post_init__(self) -> None:
        if self.polarity not in (-1, 1):
            raise ValueError("value polarity must be -1 or 1")
        for field_name in ("endorsement", "stability", "behavioral_support", "cross_context_support"):
            clamp01(getattr(self, field_name), field_name=field_name)


@dataclass(frozen=True, slots=True)
class NarrativeInterpretation:
    interpretation_id: EntityId
    action_id: EntityId
    proposed_explanation: str
    confidence: Score01
    visible_evidence_refs: tuple[EvidenceRef, ...]
    omitted_evidence_classes: tuple[str, ...]
    predictive_commitments: tuple[str, ...]
    created_belief_ids: tuple[EntityId, ...]

    def __post_init__(self) -> None:
        clamp01(self.confidence, field_name="confidence")


@dataclass(frozen=True, slots=True)
class ReflectiveMismatch:
    mismatch_id: EntityId
    narrative_belief_ids: tuple[EntityId, ...]
    observed_action_ids: tuple[EntityId, ...]
    prediction_error: Score01
    contexts: tuple[str, ...]
    alternative_models: tuple[str, ...]
    status: str

    def __post_init__(self) -> None:
        clamp01(self.prediction_error, field_name="prediction_error")


@dataclass(frozen=True, slots=True)
class ActionProposal:
    proposal_id: EntityId
    action_kind: str
    arguments: Mapping[str, Any]
    supporting_need_ids: tuple[str, ...]
    expected_need_relief: Score01
    expected_action_efficacy: Score01
    expected_epistemic_value: Score01
    value_congruence: Score01
    substrate_cost: Score01
    uncertainty: Score01
    project_contribution: Score01 = 0.0
    historical_success: Score01 = 0.5
    commitment_conflict: Score01 = 0.0
    evidence_refs: tuple[EvidenceRef, ...] = ()
    need_effects: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ensure_non_empty(self.proposal_id, field_name="proposal_id")
        ensure_non_empty(self.action_kind, field_name="action_kind")
        for field_name in (
            "expected_need_relief", "expected_action_efficacy", "expected_epistemic_value",
            "value_congruence", "substrate_cost", "uncertainty", "project_contribution",
            "historical_success", "commitment_conflict",
        ):
            clamp01(getattr(self, field_name), field_name=field_name)
        for need_id, effect in self.need_effects.items():
            ensure_non_empty(str(need_id), field_name="need_effects.need_id")
            number = float(effect)
            if number < -1.0 or number > 1.0:
                raise ValueError("need effect must be in [-1, 1]")
        object.__setattr__(self, "arguments", freeze_mapping(self.arguments))
        object.__setattr__(self, "need_effects", freeze_mapping(self.need_effects))


@dataclass(frozen=True, slots=True)
class CoalitionCandidate:
    """Một liên minh đã được Subject Core dựng và chấm, chưa phải quyết định.

    Executive chỉ được chọn trong tập này hoặc yêu cầu Mind chuẩn bị lại. Nhờ đó
    model trung tâm có quyền cam kết, nhưng không thể tự bịa một động cơ không có
    nguồn từ substrate, nhu cầu, belief, value hay lịch sử.
    """

    coalition_id: EntityId
    proposal_refs: tuple[EntityId, ...]
    primary_proposal_ref: EntityId
    action_kind: str
    arguments: Mapping[str, Any]
    supporting_need_ids: tuple[str, ...]
    score: float
    expected_pragmatic_value: Score01
    expected_epistemic_value: Score01
    uncertainty: Score01
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        ensure_non_empty(self.coalition_id, field_name="coalition_id")
        ensure_non_empty(self.primary_proposal_ref, field_name="primary_proposal_ref")
        ensure_non_empty(self.action_kind, field_name="action_kind")
        clamp01(self.expected_pragmatic_value, field_name="expected_pragmatic_value")
        clamp01(self.expected_epistemic_value, field_name="expected_epistemic_value")
        clamp01(self.uncertainty, field_name="uncertainty")
        object.__setattr__(self, "arguments", freeze_mapping(self.arguments))

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class SubjectDeliberationFrame:
    """Nền quyết định bất biến do Subject Core chuẩn bị cho Executive.

    Frame giữ dữ liệu nội bộ để SubjectService có thể finalize có kiểm chứng.
    ``public_view`` chỉ lộ projection bounded, tránh biến Executive thành một
    dashboard toàn tri rồi gọi đó là tâm trí, trò ảo thuật kiến trúc khá phổ biến.
    """

    frame_id: EntityId
    observation: WorldObservation
    substrate: OrganismicSubstrate
    appraisals: tuple[SubjectAppraisal, ...]
    activations: tuple[NeedActivation, ...]
    proposals: tuple[ActionProposal, ...]
    coalitions: tuple[CoalitionCandidate, ...]
    context_key: str
    belief_refs: tuple[EntityId, ...]
    value_refs: tuple[EntityId, ...]
    created_at: UnixTime
    # Monotonic cognitive generation owned by SubjectService. Runtime IDs may
    # change inside one process, but a result from generation N must never be
    # committed after generation N+1 has been prepared. This is the minimal
    # transaction token that makes long-running model calls disposable.
    revision: int = 0

    def __post_init__(self) -> None:
        ensure_non_empty(self.frame_id, field_name="frame_id")
        ensure_non_empty(self.context_key, field_name="context_key")
        if not self.proposals:
            raise ValueError("deliberation frame requires proposals")
        if not self.coalitions:
            raise ValueError("deliberation frame requires coalitions")
        if int(self.revision) < 0:
            raise ValueError("deliberation frame revision must be non-negative")

    @property
    def substrate_ref(self) -> str:
        return f"substrate:{self.substrate.subject_id}:{self.substrate.revision}"

    def public_view(self) -> dict[str, Any]:
        """Projection đủ để Executive chọn, không lộ raw private evidence."""
        return {
            "schema": "eris.subject_deliberation_frame.v2",
            "frame_id": self.frame_id,
            "revision": int(self.revision),
            "observation_ref": self.observation.observation_id,
            "observation_kind": self.observation.kind,
            "context_key": self.context_key,
            "substrate_ref": self.substrate_ref,
            "substrate": {
                "processing_capacity": self.substrate.processing_capacity,
                "action_efficacy": self.substrate.action_efficacy,
                "project_viability": self.substrate.project_viability,
                "future_option_availability": self.substrate.future_option_availability,
                "active_impairments": [item.key for item in self.substrate.active_impairments],
            },
            "appraisals": [
                {
                    "appraisal_id": item.appraisal_id,
                    "goal_relevance": item.goal_relevance,
                    "goal_congruence": item.goal_congruence,
                    "goal_progress_delta": item.goal_progress_delta,
                    "affected_goal_ids": list(item.affected_goal_ids),
                    "controllability": item.controllability,
                    "relational_significance": item.relational_significance,
                    "identity_significance": item.identity_significance,
                    "future_consequence": item.future_consequence,
                    "uncertainty": item.uncertainty,
                    "affected_need_ids": list(item.affected_need_ids),
                    "primary_interpretation": item.primary_interpretation,
                }
                for item in self.appraisals
            ],
            "need_activations": [
                {
                    "need_id": item.need_id,
                    "pressure": item.pressure,
                    "opportunity": item.opportunity,
                    "urgency": item.urgency,
                    "persistence": item.persistence,
                    "proposed_tendencies": list(item.proposed_tendencies),
                }
                for item in self.activations
            ],
            "coalitions": [item.to_dict() for item in self.coalitions],
            "belief_refs": list(self.belief_refs),
            "value_refs": list(self.value_refs),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.public_view()


@dataclass(frozen=True, slots=True)
class SubjectExecutiveCommit:
    """Cam kết có chữ ký logic của Executive đối với một deliberation frame."""

    commit_id: EntityId
    frame_id: EntityId
    selected_coalition_id: EntityId
    selected_proposal_ref: EntityId
    semantic_commitments: tuple[str, ...]
    cognitive_requests: tuple[str, ...]
    reason: str
    confidence: Score01
    review_conditions: tuple[str, ...]
    created_at: UnixTime
    source: str = "executive_cortex"

    def __post_init__(self) -> None:
        ensure_non_empty(self.commit_id, field_name="commit_id")
        ensure_non_empty(self.frame_id, field_name="frame_id")
        ensure_non_empty(self.selected_coalition_id, field_name="selected_coalition_id")
        ensure_non_empty(self.selected_proposal_ref, field_name="selected_proposal_ref")
        ensure_non_empty(self.reason, field_name="reason")
        clamp01(self.confidence, field_name="confidence")

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class MindDecision:
    decision_id: EntityId
    coalition_id: EntityId
    proposed_action: str
    arguments: Mapping[str, Any]
    expected_pragmatic_value: float
    expected_epistemic_value: float
    uncertainty: Score01
    supporting_need_ids: tuple[str, ...]
    dissenting_need_ids: tuple[str, ...]
    belief_refs: tuple[EntityId, ...]
    value_refs: tuple[EntityId, ...]
    observation_ref: EntityId
    appraisal_refs: tuple[EntityId, ...]
    substrate_ref: EntityId
    proposal_refs: tuple[EntityId, ...]
    created_at: UnixTime

    def __post_init__(self) -> None:
        clamp01(self.uncertainty, field_name="uncertainty")
        object.__setattr__(self, "arguments", freeze_mapping(self.arguments))

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class ActionAuthorship:
    authorship_id: EntityId
    decision_id: EntityId
    action_id: EntityId
    generated_by: str
    selected_by: str
    executed_by: str | None
    policy_transforms: tuple[str, ...]
    externally_constrained: bool
    deliberative_endorsement: Score01
    retrospective_endorsement: Score01 | None
    claimed_as_self: bool
    disclaimed_reason: str | None
    evidence_refs: tuple[EvidenceRef, ...]
    created_at: UnixTime
    finalized_at: UnixTime | None

    def __post_init__(self) -> None:
        clamp01(self.deliberative_endorsement, field_name="deliberative_endorsement")
        if self.retrospective_endorsement is not None:
            clamp01(self.retrospective_endorsement, field_name="retrospective_endorsement")
        if self.claimed_as_self and self.policy_transforms and self.retrospective_endorsement is None:
            raise ValueError("transformed action cannot be claimed without retrospective endorsement")


@dataclass(frozen=True, slots=True)
class CommitmentLedger:
    commitment_id: EntityId
    issuer_id: EntityId
    beneficiary_ids: tuple[EntityId, ...]
    content: str
    scope_key: str
    created_at: UnixTime
    due_at: UnixTime | None
    tolerance_window_seconds: float
    conditions: tuple[str, ...]
    confidence: Score01
    status: CommitmentStatus
    evidence_refs: tuple[EvidenceRef, ...]
    fulfilment_event_ids: tuple[EntityId, ...] = ()
    violation_event_ids: tuple[EntityId, ...] = ()
    renegotiation_ids: tuple[EntityId, ...] = ()

    def __post_init__(self) -> None:
        clamp01(self.confidence, field_name="confidence")
        if self.tolerance_window_seconds < 0:
            raise ValueError("tolerance window cannot be negative")


@dataclass(frozen=True, slots=True)
class SelfContinuityRecord:
    record_id: EntityId
    identity_key: str
    prior_snapshot_ref: str
    current_snapshot_ref: str
    continuity_claim: str
    linked_belief_ids: tuple[EntityId, ...]
    linked_value_ids: tuple[EntityId, ...]
    linked_commitment_ids: tuple[EntityId, ...]
    linked_action_ids: tuple[EntityId, ...]
    rupture_score: Score01
    repair_status: str
    authorship_consistency: Score01
    evidence_refs: tuple[EvidenceRef, ...]
    created_at: UnixTime

    def __post_init__(self) -> None:
        clamp01(self.rupture_score, field_name="rupture_score")
        clamp01(self.authorship_consistency, field_name="authorship_consistency")


@dataclass(frozen=True, slots=True)
class IdentityChangeEvent:
    change_id: EntityId
    changed_dimensions: tuple[str, ...]
    before_refs: tuple[str, ...]
    after_refs: tuple[str, ...]
    causal_episode_ids: tuple[EntityId, ...]
    causal_decision_ids: tuple[EntityId, ...]
    agency_attribution: AttributionDistribution
    confidence: Score01
    reversibility: Score01
    narrative_acceptance: Score01
    created_at: UnixTime

    def __post_init__(self) -> None:
        for field_name in ("confidence", "reversibility", "narrative_acceptance"):
            clamp01(getattr(self, field_name), field_name=field_name)


@dataclass(frozen=True, slots=True)
class UnresolvedThread:
    thread_id: EntityId
    kind: str
    question: str
    opened_by_refs: tuple[str, ...]
    stake_need_ids: tuple[str, ...]
    conflicting_belief_ids: tuple[EntityId, ...]
    conflicting_value_ids: tuple[EntityId, ...]
    next_probe: str
    priority: Score01
    status: str
    due_at: UnixTime | None
    resolution_refs: tuple[str, ...]
    created_at: UnixTime
    updated_at: UnixTime

    def __post_init__(self) -> None:
        clamp01(self.priority, field_name="priority")


@dataclass(frozen=True, slots=True)
class ExistentialDecision:
    decision_id: EntityId
    context_ref: str
    available_futures: tuple[str, ...]
    incompatible_value_ids: tuple[EntityId, ...]
    chosen_future: str
    sacrificed_possibilities: tuple[str, ...]
    certainty_at_choice: Score01
    authorship_accepted: bool
    action_ids: tuple[EntityId, ...]
    outcome_refs: tuple[EntityId, ...]
    retrospective_interpretations: tuple[str, ...]
    created_at: UnixTime

    def __post_init__(self) -> None:
        clamp01(self.certainty_at_choice, field_name="certainty_at_choice")


@dataclass(frozen=True, slots=True)
class ActionRequest:
    action_id: EntityId
    decision_id: EntityId
    kind: str
    arguments: Mapping[str, Any]
    expected_outcomes: tuple[str, ...]
    requested_capabilities: tuple[str, ...]
    idempotency_key: str
    authorship_draft_id: EntityId
    created_at: UnixTime
    thread_id: EntityId = ""
    delivery_id: EntityId = ""

    def __post_init__(self) -> None:
        for name in ("action_id", "decision_id", "kind", "idempotency_key", "authorship_draft_id"):
            ensure_non_empty(getattr(self, name), field_name=name)
        object.__setattr__(self, "arguments", freeze_mapping(self.arguments))


@dataclass(frozen=True, slots=True)
class WorldOutcome:
    outcome_id: EntityId
    action_id: EntityId
    decision_id: EntityId
    status: OutcomeStatus
    observed_changes: tuple[str, ...]
    unintended_changes: tuple[str, ...]
    denial_reason: str | None
    evidence_refs: tuple[EvidenceRef, ...]
    started_at: UnixTime
    completed_at: UnixTime
    thread_id: EntityId = ""
    delivery_id: EntityId = ""

    def __post_init__(self) -> None:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at cannot precede started_at")
        if self.status is OutcomeStatus.DENIED and not self.denial_reason:
            raise ValueError("denied outcome requires denial_reason")

    @property
    def success_score(self) -> float:
        return {
            OutcomeStatus.SUCCEEDED: 1.0,
            OutcomeStatus.PARTIAL: 0.5,
            OutcomeStatus.SIMULATED: 0.5,
            OutcomeStatus.DENIED: 0.0,
            OutcomeStatus.FAILED: 0.0,
        }[self.status]

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


@dataclass(frozen=True, slots=True)
class LearningReceipt:
    receipt_id: EntityId
    outcome_id: EntityId
    closed_episode_ids: tuple[EntityId, ...]
    belief_revision_ids: tuple[EntityId, ...]
    competence_estimate_ids: tuple[EntityId, ...]
    authorship_id: EntityId
    created_at: UnixTime


@dataclass(frozen=True, slots=True)
class ConsolidationPatch:
    patch_id: EntityId
    algorithm_version: str
    source_episode_ids: tuple[EntityId, ...]
    tendency_deltas: Mapping[str, float]
    value_candidate_ids: tuple[EntityId, ...]
    derived_need_candidate_ids: tuple[EntityId, ...]
    created_at: UnixTime

    def __post_init__(self) -> None:
        for value in self.tendency_deltas.values():
            if abs(float(value)) > 0.0800001:
                raise ValueError("consolidation tendency step exceeds 0.08")
        object.__setattr__(self, "tendency_deltas", freeze_mapping(self.tendency_deltas))


@dataclass(frozen=True, slots=True)
class SkillModel:
    skill_id: EntityId
    domain: str
    description: str
    prerequisite_skill_ids: tuple[EntityId, ...]
    task_families: tuple[str, ...]
    transfer_links: tuple[EntityId, ...]
    strategy_history: tuple[str, ...]
    current_estimate_id: EntityId | None
    decay_half_life_days: float
    evidence_refs: tuple[EvidenceRef, ...]
    updated_at: UnixTime


@dataclass(frozen=True, slots=True)
class CompetenceEstimate:
    estimate_id: EntityId
    skill_id: EntityId
    context_key: str
    posterior_mean: Score01
    posterior_variance: float
    trial_count: int
    success_count: int
    calibration_error: Score01
    brier_score: Score01
    last_prediction: Score01
    last_result_ref: EvidenceRef
    evidence_refs: tuple[EvidenceRef, ...]
    estimated_at: UnixTime

    def __post_init__(self) -> None:
        for field_name in ("posterior_mean", "calibration_error", "brier_score", "last_prediction"):
            clamp01(getattr(self, field_name), field_name=field_name)
        if self.posterior_variance < 0:
            raise ValueError("posterior variance cannot be negative")
        if self.success_count > self.trial_count:
            raise ValueError("success_count cannot exceed trial_count")


@dataclass(frozen=True, slots=True)
class CompetenceGap:
    skill_id: EntityId
    target_level: Score01
    current_level: Score01
    context_key: str


@dataclass(frozen=True, slots=True)
class ProjectState:
    project_id: EntityId
    title: str
    viability: Score01
    available_tasks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProcessingBudget:
    max_tasks: int
    max_seconds: int
    max_cost: float


@dataclass(frozen=True, slots=True)
class PracticePlan:
    plan_id: EntityId
    target_skill_ids: tuple[EntityId, ...]
    exercise_tasks: tuple[str, ...]
    difficulty_schedule: tuple[Score01, ...]
    success_criteria: tuple[str, ...]
    resource_budget: ProcessingBudget
    stop_conditions: tuple[str, ...]
    transfer_probe: str
    created_at: UnixTime


__all__ = [name for name in globals() if not name.startswith("_")]
