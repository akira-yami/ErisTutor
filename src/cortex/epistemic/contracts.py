from __future__ import annotations

import hashlib
import math
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if not math.isfinite(result):
        result = default
    return max(0.0, min(1.0, result))


def compact_text(value: Any, limit: int = 480) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "|".join(compact_text(part, 500).lower() for part in parts if str(part or "").strip())
    digest = hashlib.sha1((raw or str(time.time_ns())).encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{prefix}.{digest}"


class BeliefHorizon(str, Enum):
    TURN_STATE = "turn_state"
    SESSION_PATTERN = "session_pattern"
    PERSISTENT_BELIEF = "persistent_belief"


@dataclass(slots=True, frozen=True)
class EvidenceRef:
    id: str
    source: str
    independence_key: str
    source_kind: str = "runtime"
    directness: float = 0.0
    reliability: float = 0.5
    episode_id: str = ""
    timestamp: float = field(default_factory=time.time)
    storable: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", compact_text(self.id, 180))
        object.__setattr__(self, "source", compact_text(self.source or "unknown", 96))
        object.__setattr__(self, "independence_key", compact_text(self.independence_key or self.id, 180))
        object.__setattr__(self, "source_kind", compact_text(self.source_kind or "runtime", 48))
        object.__setattr__(self, "directness", clamp01(self.directness))
        object.__setattr__(self, "reliability", clamp01(self.reliability, 0.5))

    @property
    def counts_as_independent(self) -> bool:
        return self.storable and self.source_kind not in {"diary", "inner_thought", "private_mind", "internal_inference"}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "EvidenceRef":
        return cls(**{key: data.get(key) for key in cls.__dataclass_fields__ if key in data})


@dataclass(slots=True)
class Observation:
    id: str
    kind: str
    content: str
    source: str
    timestamp: float
    directness: float
    reliability: float
    independence_key: str
    source_kind: str = "runtime"
    evidence_refs: tuple[EvidenceRef, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.content = compact_text(self.content, 1200)
        self.directness = clamp01(self.directness)
        self.reliability = clamp01(self.reliability, 0.5)
        if not self.id:
            self.id = stable_id("obs", self.kind, self.source, self.independence_key, self.content)
        if not self.evidence_refs:
            self.evidence_refs = (
                EvidenceRef(
                    id=self.id,
                    source=self.source,
                    independence_key=self.independence_key or self.id,
                    source_kind=self.source_kind,
                    directness=self.directness,
                    reliability=self.reliability,
                    episode_id=str(self.metadata.get("episode_id") or ""),
                ),
            )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_refs"] = [item.to_dict() for item in self.evidence_refs]
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Observation":
        payload = dict(data)
        payload["evidence_refs"] = tuple(
            item if isinstance(item, EvidenceRef) else EvidenceRef.from_mapping(item)
            for item in data.get("evidence_refs", ())
            if isinstance(item, (EvidenceRef, Mapping))
        )
        return cls(**{key: payload.get(key) for key in cls.__dataclass_fields__ if key in payload})


@dataclass(slots=True)
class Hypothesis:
    id: str
    claim: str
    subject: str = "situation"
    facet: str = "general"
    confidence: float = 0.0
    uncertainty: float = 1.0
    overread_risk: float = 0.0
    evidence_refs: tuple[EvidenceRef, ...] = ()
    counterfactual_ids: tuple[str, ...] = ()
    origin: str = "rule"
    horizon_hint: BeliefHorizon = BeliefHorizon.TURN_STATE
    direct_assertion: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.claim = compact_text(self.claim, 600)
        self.confidence = clamp01(self.confidence)
        self.uncertainty = clamp01(self.uncertainty, 1.0 - self.confidence)
        self.overread_risk = clamp01(self.overread_risk)
        if not self.id:
            self.id = stable_id("hyp", self.subject, self.facet, self.claim)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["horizon_hint"] = self.horizon_hint.value
        data["evidence_refs"] = [item.to_dict() for item in self.evidence_refs]
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Hypothesis":
        payload = dict(data)
        payload["horizon_hint"] = BeliefHorizon(str(data.get("horizon_hint") or BeliefHorizon.TURN_STATE.value))
        payload["evidence_refs"] = tuple(
            item if isinstance(item, EvidenceRef) else EvidenceRef.from_mapping(item)
            for item in data.get("evidence_refs", ())
            if isinstance(item, (EvidenceRef, Mapping))
        )
        return cls(**{key: payload.get(key) for key in cls.__dataclass_fields__ if key in payload})


@dataclass(slots=True)
class HypothesisSet:
    hypotheses: tuple[Hypothesis, ...] = ()
    selected_id: str = ""
    uncertainty: float = 1.0

    @property
    def selected(self) -> Hypothesis | None:
        return next((item for item in self.hypotheses if item.id == self.selected_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "selected_id": self.selected_id,
            "uncertainty": round(clamp01(self.uncertainty), 4),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HypothesisSet":
        return cls(
            hypotheses=tuple(Hypothesis.from_mapping(item) for item in data.get("hypotheses", ()) if isinstance(item, Mapping)),
            selected_id=str(data.get("selected_id") or ""),
            uncertainty=clamp01(data.get("uncertainty", 1.0), 1.0),
        )


@dataclass(slots=True)
class Belief:
    id: str
    claim: str
    subject: str
    facet: str
    horizon: BeliefHorizon
    confidence: float
    uncertainty: float
    positive_evidence: tuple[EvidenceRef, ...] = ()
    contradictory_evidence: tuple[EvidenceRef, ...] = ()
    source_hypothesis_ids: tuple[str, ...] = ()
    direct_assertion: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.claim = compact_text(self.claim, 600)
        self.confidence = clamp01(self.confidence)
        self.uncertainty = clamp01(self.uncertainty)
        if not self.direct_assertion:
            self.confidence = min(0.9, self.confidence)
        if not self.id:
            self.id = stable_id("belief", self.subject, self.facet, self.claim)

    @property
    def independent_positive_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(ref.independence_key for ref in self.positive_evidence if ref.counts_as_independent))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["horizon"] = self.horizon.value
        data["positive_evidence"] = [item.to_dict() for item in self.positive_evidence]
        data["contradictory_evidence"] = [item.to_dict() for item in self.contradictory_evidence]
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Belief":
        payload = dict(data)
        payload["horizon"] = BeliefHorizon(str(data.get("horizon") or BeliefHorizon.TURN_STATE.value))
        for field_name in ("positive_evidence", "contradictory_evidence"):
            payload[field_name] = tuple(
                item if isinstance(item, EvidenceRef) else EvidenceRef.from_mapping(item)
                for item in data.get(field_name, ())
                if isinstance(item, (EvidenceRef, Mapping))
            )
        return cls(**{key: payload.get(key) for key in cls.__dataclass_fields__ if key in payload})


@dataclass(slots=True)
class ActionCandidate:
    id: str
    action_type: str
    content: str = ""
    task_fit: float = 0.0
    relational_fit: float = 0.0
    expected_helpfulness: float = 0.0
    epistemic_value: float = 0.0
    intrusion_risk: float = 0.0
    repetition: float = 0.0
    resource_cost: float = 0.0
    high_consequence: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def utility(self) -> float:
        positive = self.task_fit + self.relational_fit + self.expected_helpfulness + self.epistemic_value
        negative = self.intrusion_risk + self.repetition + self.resource_cost
        return positive - negative

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["utility"] = round(self.utility, 4)
        return data


@dataclass(slots=True)
class EpisodeFeedback:
    kind: str = "unknown"
    accepted: bool | None = None
    corrected: bool = False
    confused: bool = False
    felt_intruded: bool | None = None
    relation_improved: bool | None = None
    relation_ruptured: bool | None = None
    tone_feedback: bool = False
    text: str = ""
    evidence: tuple[str, ...] = ()
    confidence: float = 0.5


@dataclass(slots=True)
class LearningUpdate:
    id: str
    target_kind: str
    target_id: str
    operation: str
    before: float | None
    after: float | None
    reason: str
    evidence_ids: tuple[str, ...] = ()
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LearningUpdate":
        return cls(**{key: data.get(key) for key in cls.__dataclass_fields__ if key in data})


@dataclass(slots=True)
class CognitiveEpisode:
    id: str
    channel: str
    started_at: float
    observations: tuple[Observation, ...]
    hypothesis_set: HypothesisSet
    action: ActionCandidate | None = None
    expected_outcome: str = "unknown"
    feedback: EpisodeFeedback | None = None
    learning_updates: tuple[LearningUpdate, ...] = ()
    status: str = "open"
    closed_at: float = 0.0
    frame_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "channel": self.channel,
            "started_at": self.started_at,
            "observations": [item.to_dict() for item in self.observations],
            "hypothesis_set": self.hypothesis_set.to_dict(),
            "action": self.action.to_dict() if self.action else None,
            "expected_outcome": self.expected_outcome,
            "feedback": asdict(self.feedback) if self.feedback else None,
            "learning_updates": [item.to_dict() for item in self.learning_updates],
            "status": self.status,
            "closed_at": self.closed_at,
            "frame_id": self.frame_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "CognitiveEpisode":
        action_data = data.get("action")
        feedback_data = data.get("feedback")
        return cls(
            id=str(data.get("id") or ""),
            channel=str(data.get("channel") or "unknown"),
            started_at=float(data.get("started_at") or 0.0),
            observations=tuple(Observation.from_mapping(item) for item in data.get("observations", ()) if isinstance(item, Mapping)),
            hypothesis_set=HypothesisSet.from_mapping(data.get("hypothesis_set") or {}),
            action=ActionCandidate(**{key: action_data.get(key) for key in ActionCandidate.__dataclass_fields__ if key in action_data}) if isinstance(action_data, Mapping) else None,
            expected_outcome=str(data.get("expected_outcome") or "unknown"),
            feedback=EpisodeFeedback(**{key: feedback_data.get(key) for key in EpisodeFeedback.__dataclass_fields__ if key in feedback_data}) if isinstance(feedback_data, Mapping) else None,
            learning_updates=tuple(LearningUpdate.from_mapping(item) for item in data.get("learning_updates", ()) if isinstance(item, Mapping)),
            status=str(data.get("status") or "open"),
            closed_at=float(data.get("closed_at") or 0.0),
            frame_id=str(data.get("frame_id") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


def unique_evidence(refs: Iterable[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    by_id: dict[str, EvidenceRef] = {}
    for ref in refs:
        by_id.setdefault(ref.id, ref)
    return tuple(by_id.values())
