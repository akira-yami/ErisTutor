# -*- coding: utf-8 -*-
"""Canonical data contracts của Cortex V2.

Cortex chỉ được mô tả điều nó thấy và mức chắc chắn. Không contract nào chứa
câu public thay thế, callback ghi state, model client, storage hay TTS. Ranh giới
này giữ cognition hữu ích mà không dựng lại hội đồng biên tập từng chữ của Eris.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


def _compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _clean_tuple(values: Sequence[Any] | str | None, *, limit: int, item_limit: int = 140) -> tuple[str, ...]:
    if isinstance(values, str):
        text = _compact(values, item_limit)
        return (text,) if text else ()
    if not isinstance(values, Sequence):
        return ()
    result: list[str] = []
    for value in values[:limit]:
        text = _compact(value, item_limit)
        if text and text not in result:
            result.append(text)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CognitiveEvidence:
    id: str
    source: str
    text: str
    role: str = ""
    turn_index: int = -1
    reliability: float = 1.0
    freshness: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _compact(self.id, 120))
        object.__setattr__(self, "source", _compact(self.source, 60).lower())
        object.__setattr__(self, "text", _compact(self.text, 1200))
        object.__setattr__(self, "role", _compact(self.role, 30).lower())
        object.__setattr__(self, "reliability", _clamp01(self.reliability, 1.0))
        object.__setattr__(self, "freshness", _clamp01(self.freshness, 1.0))

    @property
    def quality(self) -> float:
        return self.reliability * self.freshness

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CognitiveFrame:
    trace_id: str
    turn_id: str
    turn_seq: int
    user_text: str
    evidence: tuple[CognitiveEvidence, ...] = ()
    grounding_context: str = ""
    relationship_summary: str = ""
    personality_summary: str = ""
    memory_available: bool = False
    interrupt_context: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def last_assistant_text(self) -> str:
        for item in reversed(self.evidence):
            if item.source == "public_history" and item.role == "assistant" and item.text:
                return item.text
        return ""

    @property
    def last_user_text(self) -> str:
        skipped_current = False
        for item in reversed(self.evidence):
            if item.role != "user" or not item.text:
                continue
            if not skipped_current and item.source == "user_text":
                skipped_current = True
                continue
            return item.text
        return ""

    @property
    def last_assistant_evidence_id(self) -> str:
        for item in reversed(self.evidence):
            if item.source == "public_history" and item.role == "assistant" and item.text:
                return item.id
        return ""

    def evidence_by_id(self) -> dict[str, CognitiveEvidence]:
        return {item.id: item for item in self.evidence}


@dataclass(frozen=True, slots=True)
class Hypothesis:
    key: str
    value: str
    lane: str
    confidence: float
    ambiguity: float = 0.0
    relevance: float = 1.0
    urgency: float = 0.5
    evidence_ids: tuple[str, ...] = ()
    source_module: str = ""
    ttl_turns: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _compact(self.key, 80).lower())
        object.__setattr__(self, "value", _compact(self.value, 180))
        object.__setattr__(self, "lane", _compact(self.lane, 40).lower())
        object.__setattr__(self, "confidence", _clamp01(self.confidence))
        object.__setattr__(self, "ambiguity", _clamp01(self.ambiguity))
        object.__setattr__(self, "relevance", _clamp01(self.relevance, 1.0))
        object.__setattr__(self, "urgency", _clamp01(self.urgency, 0.5))
        object.__setattr__(self, "evidence_ids", _clean_tuple(self.evidence_ids, limit=8, item_limit=120))
        object.__setattr__(self, "source_module", _compact(self.source_module, 80))
        object.__setattr__(self, "ttl_turns", max(1, min(8, int(self.ttl_turns or 1))))


@dataclass(frozen=True, slots=True)
class ProjectionFailure:
    module: str
    error_type: str
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "module", _compact(self.module, 80).lower())
        object.__setattr__(self, "error_type", _compact(self.error_type, 80))
        object.__setattr__(self, "detail", _compact(self.detail, 180))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DiscourseProjection:
    relation: str = "new_topic"
    ambiguity_owner: str = "unknown"
    expected_next_move: str = "answer"
    open_loop: str = ""
    referenced_phrase: str = ""
    assistant_fault_probability: float = 0.0
    confidence: float = 0.5
    ambiguity: float = 0.5
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SemanticProjection:
    user_act: str = "statement"
    task_goal: str = "respond_to_current_turn"
    literal_question: bool = False
    action_requested: bool = False
    confidence: float = 0.5
    ambiguity: float = 0.5
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EpistemicProjection:
    grounding_scope: tuple[str, ...] = ("user_text", "public_history")
    missing_evidence: tuple[str, ...] = ()
    uncertainty: float = 0.2
    overread_risk: float = 0.2
    tool_eligible: bool = False
    confidence: float = 0.7
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SocialProjection:
    user_posture: str = "neutral"
    seriousness: float = 0.5
    playfulness: float = 0.1
    repair_pressure: float = 0.0
    relational_stakes: float = 0.2
    confidence: float = 0.5
    ambiguity: float = 0.5
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StyleProjection:
    delivery: str = "calm_direct"
    distance: str = "guarded_neutral"
    warmth_visibility: str = "subtle"
    edge: str = "dry_soft"
    pacing: str = "compact"
    avoid_moves: tuple[str, ...] = ()
    confidence: float = 0.6
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PreResponseCognitiveBrief:
    trace_id: str
    primary_goal: str = "respond_to_current_turn"
    discourse_relation: str = "new_topic"
    ambiguity_owner: str = "unknown"
    response_move: str = "answer"
    user_act: str = "statement"
    user_posture: str = "neutral"
    assistant_fault_probability: float = 0.0
    grounding_scope: tuple[str, ...] = ("user_text", "public_history")
    missing_evidence: tuple[str, ...] = ()
    delivery: str = "calm_direct"
    avoid_moves: tuple[str, ...] = ()
    confidence: float = 0.5
    ambiguity: float = 0.5
    decision_margin: float = 0.0
    evidence_coverage: float = 0.0
    needs_deep_inference: bool = False
    evidence_ids: tuple[str, ...] = ()
    degraded_modules: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @classmethod
    def neutral(cls, trace_id: str = "", *, degraded_modules: Sequence[str] = ()) -> "PreResponseCognitiveBrief":
        return cls(
            trace_id=trace_id or "cognition:neutral",
            degraded_modules=_clean_tuple(degraded_modules, limit=8, item_limit=80),
            needs_deep_inference=bool(degraded_modules),
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "PreResponseCognitiveBrief":
        data = payload or {}
        return cls(
            trace_id=_compact(data.get("trace_id", ""), 120) or "cognition:unknown",
            primary_goal=_compact(data.get("primary_goal", "respond_to_current_turn"), 180),
            discourse_relation=_compact(data.get("discourse_relation", "new_topic"), 80).lower(),
            ambiguity_owner=_compact(data.get("ambiguity_owner", "unknown"), 40).lower(),
            response_move=_compact(data.get("response_move", "answer"), 80).lower(),
            user_act=_compact(data.get("user_act", "statement"), 80).lower(),
            user_posture=_compact(data.get("user_posture", "neutral"), 100).lower(),
            assistant_fault_probability=_clamp01(data.get("assistant_fault_probability", 0.0)),
            grounding_scope=_clean_tuple(data.get("grounding_scope", ()), limit=8, item_limit=80),
            missing_evidence=_clean_tuple(data.get("missing_evidence", ()), limit=6, item_limit=120),
            delivery=_compact(data.get("delivery", "calm_direct"), 80).lower(),
            avoid_moves=_clean_tuple(data.get("avoid_moves", ()), limit=10, item_limit=100),
            confidence=_clamp01(data.get("confidence", 0.5), 0.5),
            ambiguity=_clamp01(data.get("ambiguity", 0.5), 0.5),
            decision_margin=_clamp01(data.get("decision_margin", 0.0)),
            evidence_coverage=_clamp01(data.get("evidence_coverage", 0.0)),
            needs_deep_inference=bool(data.get("needs_deep_inference", False)),
            evidence_ids=_clean_tuple(data.get("evidence_ids", ()), limit=12, item_limit=120),
            degraded_modules=_clean_tuple(data.get("degraded_modules", ()), limit=8, item_limit=80),
            conflicts=_clean_tuple(data.get("conflicts", ()), limit=8, item_limit=140),
            notes=_clean_tuple(data.get("notes", ()), limit=8, item_limit=180),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def prompt_context(self) -> str:
        """Projection nhỏ cho planner/writer, không nhét nội tạng vào prompt."""
        lines = [
            "pre_response_cognitive_brief:",
            f"- primary_goal: {self.primary_goal}",
            f"- discourse_relation: {self.discourse_relation}",
            f"- ambiguity_owner: {self.ambiguity_owner}",
            f"- response_move: {self.response_move}",
            f"- user_posture: {self.user_posture}",
            f"- assistant_fault_probability: {self.assistant_fault_probability:.2f}",
            f"- delivery: {self.delivery}",
            f"- confidence: {self.confidence:.2f}",
            f"- ambiguity: {self.ambiguity:.2f}",
        ]
        if self.needs_deep_inference:
            lines.append("- inference_status: tentative; preserve ambiguity and avoid hard social claims")
        if self.grounding_scope:
            lines.append("- grounding_scope: " + ", ".join(self.grounding_scope))
        if self.missing_evidence:
            lines.append("- missing_evidence: " + ", ".join(self.missing_evidence))
        if self.avoid_moves:
            lines.append("- avoid_moves: " + ", ".join(self.avoid_moves))
        if self.notes:
            lines.append("- notes: " + " | ".join(self.notes))
        return "\n".join(lines)
