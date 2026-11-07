# -*- coding: utf-8 -*-
"""Candidate biểu đạt nội bộ có kiểu dữ liệu rõ ràng.

Đây chỉ là dữ liệu trung gian cho các subsystem nhận thức/động lực còn sống.
Nó không có quyền phát public speech, không schedule continuation và không chứa
social agenda runtime. Mọi public expression cuối cùng vẫn phải đi qua
ConversationKernel -> RootAgent -> NextConversationMove.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence


CANDIDATE_SCHEMA = "eris.social_speech_candidate.v2"

_ALLOWED_KINDS = {
    "continuation", "followup_reminder", "task_followup", "discovery_share",
    "screen_comment", "semantic_scene", "memory_callback", "inner_thought",
    "social_check_in", "relational_initiative", "casual_curiosity",
    "playful_prompt", "random_chat", "capability_statement",
    "grounded_tool_finding",
}

_SOCIAL_KINDS = {"social_check_in", "casual_curiosity", "playful_prompt", "random_chat"}


def _compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


@dataclass(frozen=True, slots=True)
class SocialSpeechCandidate:
    """One semantic continuation candidate without public wording rights."""

    decision: str = "none"
    kind: str = "random_chat"
    topic: str = ""
    speech_act: str = "social_check_in"
    anchor_type: str = "idle"
    anchor_id: str = ""
    evidence_refs: tuple[str, ...] = ()
    requires_grounding: bool = False
    confidence: float = 0.0
    relevance: float = 0.0
    urgency: float = 0.0
    tone: str = "soft_warm"
    reason: str = ""
    source: str = "internal_expression"
    continuation_lane: str = ""
    claim_token: str = ""
    idempotency_key: str = ""
    seed_id: str = ""
    seed_mode: str = ""
    # P0: MentalMaterialRuntime.project() đã phát ra ba thuộc tính này nhưng
    # contract cũ không nhận, gây TypeError và làm mất proposal của RootAgent.
    # Đây là metadata nội bộ, tuyệt đối không cấp quyền tự phát ngôn cho Seed.
    seed_event_kind: str = ""
    seed_epistemic_mode: str = ""
    seed_open_loop: bool = False
    source_kind: str = ""
    topic_tags: tuple[str, ...] = ()
    desired_response: tuple[str, ...] = ()
    eris_motive_kind: str = ""
    eris_motive_why_now: str = ""
    personal_stake: float = 0.0
    authenticity: float = 0.0
    daring: float = 0.0
    uncertainty: float = 0.0
    seed_intrusion_risk: float = 0.0
    face_risk: float = 0.0
    recovery_available: bool = False
    seed_lease_token: str = ""
    seed_generated_at: float = 0.0
    seed_expires_at: float = 0.0
    seed_ttl_seconds: float = 600.0
    seed_quality_score: float = 0.0
    seed_specificity: float = 0.0
    seed_motive_strength: float = 0.0
    seed_semantic_completeness: float = 0.0
    seed_temporal_context: Mapping[str, Any] = field(default_factory=dict)
    semantic_development_mode: str = ""
    semantic_development_score: float = 0.0
    semantic_development_required: bool = False
    rumination_return: bool = False
    semantic_parent_concern_id: str = ""
    expression_urge_id: str = ""
    expression_pressure: float = 0.0
    expression_freshness: float = 0.0
    expression_satisfaction: float = 0.0
    expression_motive: str = ""
    # Chỉ là semantic payload nội bộ; public writer không đọc candidate trực tiếp.
    expression_commitment: Mapping[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.decision == "candidate" and self.kind in _ALLOWED_KINDS and bool(self.topic)

    @property
    def social(self) -> bool:
        return self.kind in _SOCIAL_KINDS

    @property
    def random_chat_seed(self) -> dict[str, Any]:
        if not self.seed_id:
            return {}
        return {
            "schema": "eris.random_chat_seed.v1",
            "decision": "seed",
            "seed_id": self.seed_id,
            "mode": self.seed_mode or "shared",
            "premise": self.topic,
            "source_kind": self.source_kind or "recent_shared_context",
            "source_refs": list(self.evidence_refs),
            "topic_tags": list(self.topic_tags),
            "opening_move": self.speech_act,
            "desired_response": list(self.desired_response),
            "eris_motive": {
                "kind": self.eris_motive_kind or "want_to_share",
                "why_now": self.eris_motive_why_now,
                "personal_stake": self.personal_stake,
                "authenticity": self.authenticity,
            },
            "risk": {
                "daring": self.daring,
                "uncertainty": self.uncertainty,
                "intrusion_risk": self.seed_intrusion_risk,
                "face_risk": self.face_risk,
                "recovery_available": self.recovery_available,
            },
            "estimated_fit": self.relevance,
            "confidence": self.confidence,
            "tone": self.tone,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = CANDIDATE_SCHEMA
        payload["evidence_refs"] = list(self.evidence_refs)
        payload["topic_tags"] = list(self.topic_tags)
        payload["desired_response"] = list(self.desired_response)
        payload["expression_commitment"] = dict(self.expression_commitment or {})
        payload["random_chat_seed"] = self.random_chat_seed
        return payload

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        source: str = "internal_expression",
    ) -> "SocialSpeechCandidate":
        data = payload or {}
        decision = _compact(data.get("decision", "none"), 24).lower() or "none"
        if decision not in {"candidate", "none"}:
            decision = "none"
        kind = _compact(data.get("kind", "random_chat"), 48).lower() or "random_chat"
        if kind not in _ALLOWED_KINDS:
            decision = "none"
            kind = "random_chat"
        refs = tuple(
            item for item in (_compact(value, 140) for value in _sequence(data.get("evidence_refs"))[:6]) if item
        )
        seed = data.get("random_chat_seed") if isinstance(data.get("random_chat_seed"), Mapping) else data
        motive = seed.get("eris_motive") if isinstance(seed.get("eris_motive"), Mapping) else {}
        risk = seed.get("risk") if isinstance(seed.get("risk"), Mapping) else {}
        topic_tags = tuple(
            item for item in (_compact(value, 48).lower().replace(" ", "_") for value in _sequence(seed.get("topic_tags"))[:8]) if item
        )
        desired_response = tuple(
            item for item in (_compact(value, 64).lower() for value in _sequence(seed.get("desired_response"))[:6]) if item
        )
        return cls(
            decision=decision,
            kind=kind,
            topic=_compact(data.get("topic", ""), 1200),
            speech_act=_compact(data.get("speech_act", kind), 64).lower() or kind,
            anchor_type=_compact(data.get("anchor_type", "idle"), 40).lower() or "idle",
            anchor_id=_compact(data.get("anchor_id", ""), 120),
            evidence_refs=refs,
            requires_grounding=bool(data.get("requires_grounding", False)),
            confidence=_clamp01(data.get("confidence", 0.0)),
            relevance=_clamp01(data.get("relevance", 0.0)),
            urgency=_clamp01(data.get("urgency", 0.0)),
            tone=_compact(data.get("tone", "soft_warm"), 48).lower() or "soft_warm",
            reason=_compact(data.get("reason", ""), 240),
            source=source,
            continuation_lane=_compact(data.get("continuation_lane", ""), 48).lower(),
            claim_token=_compact(data.get("claim_token", ""), 180),
            idempotency_key=_compact(data.get("idempotency_key", ""), 180),
            seed_id=_compact(seed.get("seed_id", data.get("seed_id", "")), 80),
            seed_mode=_compact(seed.get("mode", data.get("seed_mode", "")), 24).lower(),
            seed_event_kind=_compact(data.get("seed_event_kind", seed.get("seed_event_kind", "")), 64).lower(),
            seed_epistemic_mode=_compact(data.get("seed_epistemic_mode", seed.get("seed_epistemic_mode", "")), 48).lower(),
            seed_open_loop=bool(data.get("seed_open_loop", seed.get("seed_open_loop", False))),
            source_kind=_compact(seed.get("source_kind", data.get("source_kind", "")), 40).lower(),
            topic_tags=topic_tags,
            desired_response=desired_response,
            eris_motive_kind=_compact(motive.get("kind", data.get("eris_motive_kind", "")), 40).lower(),
            eris_motive_why_now=_compact(motive.get("why_now", data.get("eris_motive_why_now", "")), 220),
            personal_stake=_clamp01(motive.get("personal_stake", data.get("personal_stake", 0.0))),
            authenticity=_clamp01(motive.get("authenticity", data.get("authenticity", 0.0))),
            daring=_clamp01(risk.get("daring", data.get("daring", 0.0))),
            uncertainty=_clamp01(risk.get("uncertainty", data.get("uncertainty", 0.0))),
            seed_intrusion_risk=_clamp01(risk.get("intrusion_risk", data.get("seed_intrusion_risk", 0.0))),
            face_risk=_clamp01(risk.get("face_risk", data.get("face_risk", 0.0))),
            recovery_available=bool(risk.get("recovery_available", data.get("recovery_available", False))),
            seed_lease_token=_compact(data.get("seed_lease_token", ""), 120),
            seed_generated_at=float(data.get("seed_generated_at", 0.0) or 0.0),
            seed_expires_at=float(data.get("seed_expires_at", 0.0) or 0.0),
            seed_ttl_seconds=max(60.0, min(1800.0, float(data.get("seed_ttl_seconds", 600.0) or 600.0))),
            seed_quality_score=_clamp01(data.get("seed_quality_score", 0.0)),
            seed_specificity=_clamp01(data.get("seed_specificity", 0.0)),
            seed_motive_strength=_clamp01(data.get("seed_motive_strength", 0.0)),
            seed_semantic_completeness=_clamp01(data.get("seed_semantic_completeness", 0.0)),
            seed_temporal_context=dict(data.get("seed_temporal_context", {})) if isinstance(data.get("seed_temporal_context"), Mapping) else {},
            semantic_development_mode=_compact(data.get("semantic_development_mode", ""), 24).lower(),
            semantic_development_score=_clamp01(data.get("semantic_development_score", 0.0)),
            semantic_development_required=bool(data.get("semantic_development_required", False)),
            rumination_return=bool(data.get("rumination_return", False)),
            semantic_parent_concern_id=_compact(data.get("semantic_parent_concern_id", ""), 120),
            expression_urge_id=_compact(data.get("expression_urge_id", ""), 96),
            expression_pressure=_clamp01(data.get("expression_pressure", 0.0)),
            expression_freshness=_clamp01(data.get("expression_freshness", 0.0)),
            expression_satisfaction=_clamp01(data.get("expression_satisfaction", 0.0)),
            expression_motive=_compact(data.get("expression_motive", ""), 32).lower(),
            expression_commitment=(
                dict(data.get("expression_commitment", {}))
                if isinstance(data.get("expression_commitment"), Mapping) else {}
            ),
        )


__all__ = ["CANDIDATE_SCHEMA", "SocialSpeechCandidate"]
