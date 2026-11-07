# -*- coding: utf-8 -*-
"""Contracts cho kiến trúc AI-orchestrated của ERIS.

Triết lý rất thẳng:
- Main model sở hữu lời public.
- Observer model diễn giải điều vừa xảy ra.
- Chat/startup appraise social_plan. Autonomous ưu tiên stateful expression
  commitment đã được owner của Eris duy trì; generic affordance vẫn chỉ là option.
  Subject/Neuro sở hữu action family, Executive là semantic critic, Surface chỉ
  hiện thực hóa wording.
- Reducer là nơi duy nhất được ghi state.
- Runtime chỉ thực thi sự thật cơ học.

Các contract trong file này không chứa câu public thay thế. Đây là ranh giới
quan trọng nhất của kiến trúc, vì một "observer" biết viết lại lời thì chỉ là
biên tập viên đội mũ mới.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Mapping, Sequence

from .semantic_contracts import ActivitySemantics, DiscourseFrame, TurnSemantics
from runtime.goal_lifecycle import GoalLifecycleRecord

from .identity import (
    Activation,
    ConflictState,
    IdentityProfile,
    IdentitySignal,
    PendingStrategyUse,
    PRIMARY_RELATIONSHIP_ID,
    ValueChoice,
)
from .personality import (
    ConsequenceAssessment,
    PersonalityProfile,
    SelfInterpretation,
    TraitSignal,
)


AI_RUNTIME_VERSION = "ai_orchestrated.v28.background_seed_reservoir"


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


def compact(value: Any, limit: int = 220) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


@dataclass(frozen=True, slots=True)
class RelationshipState:
    """Một nguồn state quan hệ duy nhất.

    Tách trust quan hệ khỏi độ tin cậy năng lực. User sửa code không còn bị diễn
    thành một vết thương tình cảm, một tiến bộ nhỏ nhưng đáng lẽ hiển nhiên.
    """

    relational_trust: float = 0.42
    competence_trust: float = 0.50
    factual_reliability: float = 0.50
    boundary_safety: float = 0.72
    familiarity: float = 0.18
    closeness: float = 0.20
    playful_safety: float = 0.52
    rupture: float = 0.0
    repair_progress: float = 0.0
    last_event: str = ""
    last_turn_seq: int = 0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "RelationshipState":
        data = payload or {}
        return cls(
            relational_trust=clamp01(data.get("relational_trust", 0.42), 0.42),
            competence_trust=clamp01(data.get("competence_trust", 0.50), 0.50),
            factual_reliability=clamp01(data.get("factual_reliability", 0.50), 0.50),
            boundary_safety=clamp01(data.get("boundary_safety", 0.72), 0.72),
            familiarity=clamp01(data.get("familiarity", 0.18), 0.18),
            closeness=clamp01(data.get("closeness", 0.20), 0.20),
            playful_safety=clamp01(data.get("playful_safety", 0.52), 0.52),
            rupture=clamp01(data.get("rupture", 0.0)),
            repair_progress=clamp01(data.get("repair_progress", 0.0)),
            last_event=compact(data.get("last_event", ""), 80),
            last_turn_seq=max(0, int(data.get("last_turn_seq", 0) or 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "version": AI_RUNTIME_VERSION}

    def prompt_summary(self) -> str:
        bits: list[str] = []
        if self.familiarity >= 0.72:
            bits.append("Hai người đã rất quen")
        elif self.familiarity >= 0.42:
            bits.append("Độ quen thuộc đang ở mức vừa")
        if self.rupture >= 0.28:
            bits.append("Còn một điểm quan hệ cần sửa trực tiếp")
        elif self.closeness >= 0.62:
            bits.append("Khoảng cách hiện tại khá gần")
        if self.boundary_safety <= 0.38:
            bits.append("Cần giữ ranh giới rõ hơn")
        return ". ".join(bits[:2])


@dataclass(frozen=True, slots=True)
class AppraisalState:
    pleasantness: float = 0.50
    curiosity: float = 0.20
    tenderness: float = 0.10
    embarrassment: float = 0.0
    irritation: float = 0.0
    pressure: float = 0.0
    novelty: float = 0.0
    last_turn_seq: int = 0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "AppraisalState":
        data = payload or {}
        return cls(
            pleasantness=clamp01(data.get("pleasantness", 0.50), 0.50),
            curiosity=clamp01(data.get("curiosity", 0.20), 0.20),
            tenderness=clamp01(data.get("tenderness", 0.10), 0.10),
            embarrassment=clamp01(data.get("embarrassment", 0.0)),
            irritation=clamp01(data.get("irritation", 0.0)),
            pressure=clamp01(data.get("pressure", 0.0)),
            novelty=clamp01(data.get("novelty", 0.0)),
            last_turn_seq=max(0, int(data.get("last_turn_seq", 0) or 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CognitiveObservation:
    """Nhận xét hậu kỳ của observer về một lượt đã thực sự xảy ra."""

    turn_id: str
    turn_seq: int
    session_id: str = "runtime"
    relationship_id: str = PRIMARY_RELATIONSHIP_ID
    event_kind: str = "chat"
    user_act: str = "unknown"
    relationship_meaning: str = "neutral"
    continuity: str = "new_or_neutral"
    repair_needed: bool = False
    assistant_fault_probability: float = 0.0
    memory_worthy: bool = False
    confidence: float = 0.0
    ambiguity: float = 1.0
    relational_delta: float = 0.0
    competence_delta: float = 0.0
    factual_delta: float = 0.0
    boundary_delta: float = 0.0
    familiarity_delta: float = 0.0
    closeness_delta: float = 0.0
    playful_safety_delta: float = 0.0
    rupture_delta: float = 0.0
    appraisal: AppraisalState = field(default_factory=AppraisalState)
    self_interpretation: SelfInterpretation = field(default_factory=SelfInterpretation)
    self_narrative_candidate: str = ""
    self_narrative_confidence: float = 0.0
    consequence: ConsequenceAssessment = field(default_factory=ConsequenceAssessment)
    trait_signals: tuple[TraitSignal, ...] = ()
    identity_signals: tuple[IdentitySignal, ...] = ()
    active_motives: tuple[Activation, ...] = ()
    activated_defenses: tuple[Activation, ...] = ()
    active_conflict: ConflictState = field(default_factory=ConflictState)
    value_choice: ValueChoice = field(default_factory=ValueChoice)
    discourse: DiscourseFrame = field(default_factory=DiscourseFrame)
    open_loop: str = ""
    # V93: owner của open-loop là provenance, không được đoán lại từ prose.
    open_loop_owner: str = "unknown"
    memory_candidate: str = ""
    evidence: tuple[str, ...] = ()
    notes: str = ""
    source: str = "none"
    latency_ms: float = 0.0
    # Runtime-owned: không nhận status từ model. Dùng để phân biệt model
    # chủ động nói unknown với request hỏng (request hỏng không tạo object).
    observation_status: str = "success"

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        turn_id: str,
        turn_seq: int,
        session_id: str = "runtime",
        relationship_id: str = PRIMARY_RELATIONSHIP_ID,
        source: str = "model",
        latency_ms: float = 0.0,
    ) -> "CognitiveObservation":
        data = payload or {}

        def signed(name: str) -> float:
            try:
                value = float(data.get(name, 0.0) or 0.0)
            except (TypeError, ValueError):
                value = 0.0
            if value != value:
                value = 0.0
            return max(-1.0, min(1.0, value))

        raw_evidence = data.get("evidence", ())
        evidence: tuple[str, ...]
        if isinstance(raw_evidence, str):
            evidence = (compact(raw_evidence, 140),) if raw_evidence.strip() else ()
        elif isinstance(raw_evidence, Sequence):
            evidence = tuple(
                item for item in (compact(value, 140) for value in raw_evidence[:4]) if item
            )
        else:
            evidence = ()

        raw_signals = data.get("trait_signals", ())
        if isinstance(raw_signals, Sequence) and not isinstance(raw_signals, str):
            trait_signals = tuple(
                signal
                for signal in (
                    TraitSignal.from_mapping(item)
                    for item in list(raw_signals)[:10]
                    if isinstance(item, Mapping)
                )
                if signal.category and signal.trait and signal.delta != 0.0
            )
        else:
            trait_signals = ()

        raw_identity_signals = data.get("identity_signals", ())
        if isinstance(raw_identity_signals, Sequence) and not isinstance(raw_identity_signals, str):
            identity_signals = tuple(
                signal
                for signal in (
                    IdentitySignal.from_mapping(item)
                    for item in list(raw_identity_signals)[:14]
                    if isinstance(item, Mapping)
                )
                if signal.dimension and signal.key and signal.delta != 0.0
            )
        else:
            identity_signals = ()

        def activations(name: str) -> tuple[Activation, ...]:
            raw = data.get(name, ())
            if not isinstance(raw, Sequence) or isinstance(raw, str):
                return ()
            return tuple(
                item
                for item in (Activation.from_mapping(value) for value in list(raw)[:4] if isinstance(value, Mapping))
                if item.key and item.strength > 0.0
            )

        # V93: autonomous observer có thể sinh open-loop về chính ERIS. Nếu không
        # gắn owner ở đây, state reducer sẽ vô tình nâng thought nội sinh thành
        # shared goal của user. Fail-closed qua helper typed, không đọc pronoun.
        try:
            from runtime.public_causal_receipt import infer_open_loop_owner
            open_loop_owner = infer_open_loop_owner(
                event_kind=data.get("event_kind", "chat"),
                explicit_owner=data.get("open_loop_owner", ""),
            )
        except Exception:
            open_loop_owner = "eris" if str(data.get("event_kind", "")).lower() == "autonomous" else "shared"

        return cls(
            turn_id=str(turn_id or f"turn-{turn_seq}"),
            turn_seq=max(0, int(turn_seq or 0)),
            session_id=compact(data.get("session_id", session_id), 100) or "runtime",
            relationship_id=compact(
                data.get("relationship_id", relationship_id),
                80,
            ) or PRIMARY_RELATIONSHIP_ID,
            event_kind=compact(data.get("event_kind", "chat"), 40).lower() or "chat",
            user_act=compact(data.get("user_act", "unknown"), 60).lower() or "unknown",
            relationship_meaning=compact(data.get("relationship_meaning", "neutral"), 80).lower() or "neutral",
            continuity=compact(data.get("continuity", "new_or_neutral"), 80).lower() or "new_or_neutral",
            repair_needed=bool(data.get("repair_needed", False)),
            assistant_fault_probability=clamp01(data.get("assistant_fault_probability", 0.0)),
            memory_worthy=bool(data.get("memory_worthy", False)),
            confidence=clamp01(data.get("confidence", 0.0)),
            ambiguity=clamp01(data.get("ambiguity", 1.0), 1.0),
            relational_delta=signed("relational_delta"),
            competence_delta=signed("competence_delta"),
            factual_delta=signed("factual_delta"),
            boundary_delta=signed("boundary_delta"),
            familiarity_delta=signed("familiarity_delta"),
            closeness_delta=signed("closeness_delta"),
            playful_safety_delta=signed("playful_safety_delta"),
            rupture_delta=signed("rupture_delta"),
            appraisal=AppraisalState.from_mapping(data.get("appraisal") if isinstance(data.get("appraisal"), Mapping) else {}),
            self_interpretation=SelfInterpretation.from_mapping(
                data.get("self_interpretation") if isinstance(data.get("self_interpretation"), Mapping) else {}
            ),
            self_narrative_candidate=compact(data.get("self_narrative_candidate", ""), 360),
            self_narrative_confidence=clamp01(data.get("self_narrative_confidence", 0.0)),
            consequence=ConsequenceAssessment.from_mapping(
                data.get("consequence") if isinstance(data.get("consequence"), Mapping) else {}
            ),
            trait_signals=trait_signals,
            identity_signals=identity_signals,
            active_motives=activations("active_motives"),
            activated_defenses=activations("activated_defenses"),
            active_conflict=ConflictState.from_mapping(
                data.get("active_conflict") if isinstance(data.get("active_conflict"), Mapping) else {}
            ),
            value_choice=ValueChoice.from_mapping(
                data.get("value_choice") if isinstance(data.get("value_choice"), Mapping) else {}
            ),
            discourse=DiscourseFrame.from_mapping(
                data.get("discourse") if isinstance(data.get("discourse"), Mapping) else {}
            ),
            open_loop=compact(data.get("open_loop", ""), 180),
            open_loop_owner=open_loop_owner,
            memory_candidate=compact(data.get("memory_candidate", ""), 260),
            evidence=evidence,
            notes=compact(data.get("notes", ""), 220),
            source=str(source or "model"),
            latency_ms=max(0.0, float(latency_ms or 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["self_interpretation"] = self.self_interpretation.to_dict()
        payload["consequence"] = self.consequence.to_dict()
        payload["trait_signals"] = [signal.to_dict() for signal in self.trait_signals]
        payload["identity_signals"] = [signal.to_dict() for signal in self.identity_signals]
        payload["active_motives"] = [item.to_dict() for item in self.active_motives]
        payload["activated_defenses"] = [item.to_dict() for item in self.activated_defenses]
        payload["active_conflict"] = self.active_conflict.to_dict()
        payload["value_choice"] = self.value_choice.to_dict()
        payload["discourse"] = self.discourse.to_dict()
        payload["version"] = AI_RUNTIME_VERSION
        return payload


@dataclass(frozen=True, slots=True)
class IntentionPlan:
    plan_id: str
    created_at: float
    channel: str
    # Public intent chỉ có hai owner hợp lệ. Chat do IntentPlanner tạo;
    # autonomous do SocialInitiative tạo. Các tầng khác chỉ đọc/cung cấp state.
    owner: str = ""
    speak: bool = False
    motive: str = "silence"
    strategy: str = "direct_response"
    value_focus: str = ""
    topic: str = ""
    importance: float = 0.0
    intrusion_risk: float = 0.0
    confidence: float = 0.0
    performance_hint: str = "neutral"
    tool_intent: str = ""
    tool_input: str = ""
    evidence_refs: tuple[str, ...] = ()
    # V93: provenance causal public-safe; không chứa chain-of-thought.
    semantic_owner: str = "unknown"
    trigger_owner: str = "unknown"
    source_kind: str = ""
    source_ref: str = ""
    why_now: str = ""
    user_triggered: bool = False
    semantics: TurnSemantics = field(default_factory=TurnSemantics)
    activity: ActivitySemantics = field(default_factory=ActivitySemantics)
    notes: str = ""
    continuation_lane: str = ""
    continuation_ticket_id: str = ""
    continuation_claim_token: str = ""
    continuation_idempotency_key: str = ""

    @classmethod
    def silence(cls, *, channel: str, reason: str = "no_valuable_intention") -> "IntentionPlan":
        return cls(
            plan_id=f"plan-{int(time.time() * 1000)}",
            created_at=time.time(),
            channel=channel,
            owner="intent_planner" if str(channel).lower() == "chat" else "social_initiative",
            speak=False,
            motive="silence",
            semantics=TurnSemantics(turn_kind="other", speech_act="silence", subject_key="none", evidence_scope="none"),
            notes=reason,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None, *, channel: str) -> "IntentionPlan":
        data = payload or {}
        raw_refs = data.get("evidence_refs", ())
        if isinstance(raw_refs, str):
            refs = (compact(raw_refs, 120),) if raw_refs.strip() else ()
        elif isinstance(raw_refs, Sequence):
            refs = tuple(compact(x, 120) for x in raw_refs[:5] if compact(x, 120))
        else:
            refs = ()
        try:
            from runtime.public_causal_receipt import normalize_semantic_owner
        except Exception:
            normalize_semantic_owner = lambda value, default="unknown": str(value or default).lower()  # type: ignore
        provenance = data.get("provenance") if isinstance(data.get("provenance"), Mapping) else {}
        is_chat = str(channel or "").lower() == "chat"
        semantic_owner = normalize_semantic_owner(
            provenance.get("semantic_owner", data.get("semantic_owner", "shared" if is_chat else "unknown"))
        )
        trigger_owner = normalize_semantic_owner(
            provenance.get("trigger_owner", data.get("trigger_owner", "user" if is_chat else "unknown"))
        )
        return cls(
            plan_id=compact(data.get("plan_id", ""), 80) or f"plan-{int(time.time() * 1000)}",
            created_at=time.time(),
            channel=str(channel or "autonomous"),
            owner=compact(
                data.get("owner", "intent_planner" if str(channel).lower() == "chat" else "social_initiative"),
                48,
            ).lower(),
            speak=bool(data.get("speak", False)),
            motive=compact(data.get("motive", "silence"), 80).lower() or "silence",
            strategy=compact(data.get("strategy", "direct_response"), 100).lower() or "direct_response",
            value_focus=compact(data.get("value_focus", ""), 80).lower(),
            topic=compact(data.get("topic", ""), 240),
            importance=clamp01(data.get("importance", 0.0)),
            intrusion_risk=clamp01(data.get("intrusion_risk", 0.0)),
            confidence=clamp01(data.get("confidence", 0.0)),
            performance_hint=compact(data.get("performance_hint", "neutral"), 60).lower() or "neutral",
            tool_intent=compact(data.get("tool_intent", ""), 80),
            tool_input=compact(data.get("tool_input", ""), 1200),
            evidence_refs=refs,
            semantic_owner=semantic_owner,
            trigger_owner=trigger_owner,
            source_kind=compact(provenance.get("source_kind", data.get("source_kind", "user_turn" if is_chat else "")), 100),
            source_ref=compact(provenance.get("source_ref", data.get("source_ref", "current_user_turn" if is_chat else "")), 180),
            why_now=compact(provenance.get("why_now", data.get("why_now", "current user turn requires a response" if is_chat else "")), 360),
            user_triggered=bool(provenance.get("user_triggered", data.get("user_triggered", is_chat))),
            semantics=TurnSemantics.from_mapping(
                data.get("semantics") if isinstance(data.get("semantics"), Mapping) else {
                    "turn_kind": data.get("turn_kind", "other"),
                    "speech_act": data.get("speech_act", data.get("strategy", "unspecified")),
                    "subject_key": data.get("subject_key", data.get("plan_id", "unknown")),
                    "evidence_scope": data.get("evidence_scope", "none"),
                    "continuation_key": data.get("continuation_key", "none"),
                }
            ),
            activity=ActivitySemantics.from_mapping(
                data.get("activity") if isinstance(data.get("activity"), Mapping) else {}
            ),
            notes=compact(data.get("notes", ""), 220),
            continuation_lane=compact(data.get("continuation_lane", ""), 48).lower(),
            continuation_ticket_id=compact(data.get("continuation_ticket_id", ""), 120),
            continuation_claim_token=compact(data.get("continuation_claim_token", ""), 180),
            continuation_idempotency_key=compact(data.get("continuation_idempotency_key", ""), 180),
        )

    @property
    def semantic_signature(self) -> str:
        return self.semantics.signature

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["semantics"] = self.semantics.to_dict()
        payload["activity"] = self.activity.to_dict()
        payload["provenance"] = {
            "semantic_owner": self.semantic_owner,
            "trigger_owner": self.trigger_owner,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "why_now": self.why_now,
            "user_triggered": self.user_triggered,
        }
        payload["semantic_signature"] = self.semantic_signature
        payload["version"] = AI_RUNTIME_VERSION
        return payload


@dataclass(frozen=True, slots=True)
class RuntimeMindState:
    version: int = 0
    last_applied_turn_seq: int = 0
    relationship: RelationshipState = field(default_factory=RelationshipState)
    appraisal: AppraisalState = field(default_factory=AppraisalState)
    personality: PersonalityProfile = field(default_factory=PersonalityProfile)
    identity: IdentityProfile = field(default_factory=IdentityProfile)
    identity_learning_enabled: bool = True
    latest_observation: Mapping[str, Any] = field(default_factory=dict)
    latest_intention: Mapping[str, Any] = field(default_factory=dict)
    open_loops: tuple[str, ...] = ()
    # V94: canonical lifecycle của goal user/shared. ``open_loops`` chỉ còn là
    # compatibility projection của các record ACTIVE, không còn là source of truth.
    goal_records: tuple[GoalLifecycleRecord, ...] = ()
    memory_candidates: tuple[Mapping[str, Any], ...] = ()
    updated_at: float = 0.0

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "RuntimeMindState":
        data = payload or {}
        raw_loops = data.get("open_loops", ())
        loops = tuple(compact(x, 180) for x in raw_loops[-8:] if compact(x, 180)) if isinstance(raw_loops, Sequence) and not isinstance(raw_loops, str) else ()
        raw_goal_records = data.get("goal_records", ())
        goal_records = tuple(
            GoalLifecycleRecord.from_mapping(item)
            for item in raw_goal_records[-8:]
            if isinstance(item, Mapping)
        ) if isinstance(raw_goal_records, Sequence) and not isinstance(raw_goal_records, str) else ()
        raw_candidates = data.get("memory_candidates", ())
        candidates = tuple(dict(x) for x in raw_candidates[-12:] if isinstance(x, Mapping)) if isinstance(raw_candidates, Sequence) and not isinstance(raw_candidates, str) else ()
        relationship = RelationshipState.from_mapping(
            data.get("relationship") if isinstance(data.get("relationship"), Mapping) else {}
        )
        personality = PersonalityProfile.from_mapping(
            data.get("personality") if isinstance(data.get("personality"), Mapping) else {}
        )
        raw_identity = data.get("identity") if isinstance(data.get("identity"), Mapping) else None
        identity = (
            IdentityProfile.from_mapping(raw_identity)
            if raw_identity is not None
            else IdentityProfile.from_legacy(
                relationship=relationship.to_dict(),
                strategy_reinforcement=personality.strategy_reinforcement,
            )
        )
        return cls(
            version=max(0, int(data.get("version", 0) or 0)),
            last_applied_turn_seq=max(0, int(data.get("last_applied_turn_seq", 0) or 0)),
            relationship=relationship,
            appraisal=AppraisalState.from_mapping(data.get("appraisal") if isinstance(data.get("appraisal"), Mapping) else {}),
            personality=personality,
            identity=identity,
            identity_learning_enabled=bool(data.get("identity_learning_enabled", True)),
            latest_observation=dict(data.get("latest_observation", {})) if isinstance(data.get("latest_observation"), Mapping) else {},
            latest_intention=dict(data.get("latest_intention", {})) if isinstance(data.get("latest_intention"), Mapping) else {},
            open_loops=loops,
            goal_records=goal_records,
            memory_candidates=candidates,
            updated_at=max(0.0, float(data.get("updated_at", 0.0) or 0.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": AI_RUNTIME_VERSION,
            "version": self.version,
            "last_applied_turn_seq": self.last_applied_turn_seq,
            "relationship": self.relationship.to_dict(),
            "appraisal": self.appraisal.to_dict(),
            "personality": self.personality.to_dict(),
            "identity": self.identity.to_dict(),
            "identity_learning_enabled": bool(self.identity_learning_enabled),
            "latest_observation": dict(self.latest_observation),
            "latest_intention": dict(self.latest_intention),
            "open_loops": list(self.open_loops),
            "goal_records": [item.to_dict() for item in self.goal_records],
            "memory_candidates": [dict(x) for x in self.memory_candidates],
            "updated_at": self.updated_at,
        }

    def prompt_context(self) -> str:
        """Context ngắn cho Main AI: bản thân hiện tại + continuity, không có type label."""
        parts = [
            self.identity.prompt_summary(
                personality_summary=self.personality.prompt_summary(limit=520),
                limit=880,
            ),
            self.relationship.prompt_summary(),
        ]
        latest = self.latest_observation
        if latest and float(latest.get("confidence", 0.0) or 0.0) >= 0.62:
            if bool(latest.get("repair_needed", False)):
                parts.append("Lượt trước có dấu hiệu cần nhận và sửa thẳng")
            meaning = compact(latest.get("relationship_meaning", ""), 80)
            if meaning in {"closeness_bid", "request_reciprocity", "vulnerability"}:
                parts.append("Người dùng đang chủ động mở khoảng cách gần hơn")
        return " ".join(part for part in parts if part)[:1100]

    def personality_prompt_context(self) -> str:
        return self.identity.prompt_summary(
            personality_summary=self.personality.prompt_summary(limit=520),
            limit=980,
        )

    def identity_prompt_context(self) -> str:
        return self.personality_prompt_context()


@dataclass(frozen=True, slots=True)
class StateEvent:
    kind: str
    turn_seq: int
    turn_id: str
    payload: Mapping[str, Any]
    base_version: int = -1
    created_at: float = field(default_factory=time.time)
    source: str = "runtime"

    @classmethod
    def from_observation(cls, observation: CognitiveObservation, *, base_version: int = -1) -> "StateEvent":
        return cls(
            kind="cognitive_observation",
            turn_seq=observation.turn_seq,
            turn_id=observation.turn_id,
            payload=observation.to_dict(),
            base_version=base_version,
            source=observation.source,
        )

    @classmethod
    def from_intention(cls, plan: IntentionPlan, *, turn_seq: int, base_version: int = -1) -> "StateEvent":
        return cls(
            kind="intention_plan",
            turn_seq=max(0, int(turn_seq or 0)),
            turn_id=plan.plan_id,
            payload=plan.to_dict(),
            base_version=base_version,
            source="ai_planner",
        )


    @classmethod
    def from_pending_strategy(
        cls,
        pending: PendingStrategyUse,
        *,
        base_version: int = -1,
    ) -> "StateEvent":
        return cls(
            kind="pending_strategy",
            turn_seq=pending.turn_seq,
            turn_id=pending.strategy_use_id,
            payload=pending.to_dict(),
            base_version=base_version,
            source="ai_planner",
        )

    @classmethod
    def from_mechanical_outcome(
        cls,
        payload: Mapping[str, Any],
        *,
        turn_seq: int,
        turn_id: str,
        base_version: int = -1,
    ) -> "StateEvent":
        data = dict(payload or {})
        data.setdefault("kind", data.get("action_name", "runtime_action"))
        return cls(
            kind="mechanical_outcome",
            turn_seq=max(0, int(turn_seq or 0)),
            turn_id=str(turn_id or f"mechanical-{turn_seq}"),
            payload=data,
            base_version=base_version,
            source="runtime",
        )


__all__ = [
    "AI_RUNTIME_VERSION",
    "AppraisalState",
    "CognitiveObservation",
    "IdentityProfile",
    "IdentitySignal",
    "IntentionPlan",
    "RelationshipState",
    "RuntimeMindState",
    "StateEvent",
    "ConsequenceAssessment",
    "PersonalityProfile",
    "SelfInterpretation",
    "TraitSignal",
    "PendingStrategyUse",
    "clamp01",
    "compact",
]
