# -*- coding: utf-8 -*-
"""
relationship_dynamics.py
========================

Module này quản lý state quan hệ dài hạn giữa ERIS và user.

Bản này đã bỏ kiểu suy trực tiếp:
    "ổn" / "chuẩn"  -> accepted_style
    "eris" / "project" -> repeated_project_context

Thay vào đó, relationship_dynamics chỉ nhận event score đã được build bởi
RelationshipEventBuilder. Tức là file này không còn tự đóng vai thầy bói keyword.
Nó chỉ làm đúng việc của mình: update state có quán tính.

Kiến trúc mới
-------------
raw user text / affect / outcome / goal / session ledger
    -> relationship_event_builder.py
    -> RelationshipEventScores + RelationshipEvidence
    -> RelationshipTurnEvent
    -> RelationshipDynamicsEngine.update()
    -> RelationshipDynamicsState + RelationshipStylePressure

Tư duy tốt lẫn xấu của ERIS
---------------------------
- Tốt: state không nhảy chỉ vì một keyword; có score, confidence, evidence.
- Xấu: state vẫn được phép tích residue, recoil, dignity defense, attachment
  visibility theo thời gian, nên ERIS không bị assistant-flat quá sạch.

Ranh giới trách nhiệm
---------------------
- RelationshipEventBuilder: suy event score từ evidence.
- RelationshipDynamicsEngine: cập nhật state.
- StyleProjection: dùng style pressure để nói.

Không module nào trong file này nên tự đọc marker kiểu "ổn", "project", "ERIS"
để tăng trust/ritual. Nếu cần dùng surface cue, đưa nó qua builder để bị cap
confidence và có evidence label. Vâng, đúng là phải kỷ luật như vậy, vì không
thì một chữ "ổn chưa?" lại biến thành tình bạn đang nở hoa. Một bi kịch parser.
"""

from __future__ import annotations
from core.di import resolve_dependency

from dataclasses import dataclass, field
from typing import Any, Mapping


RELATIONAL_EXPOSURE = "relational_exposure"
PERCEIVED_REJECTION_EVENT = "perceived_rejection"
BOUNDARY_PROBE = "boundary_probe"


# ---------------------------------------------------------------------------
# Import mềm
# ---------------------------------------------------------------------------

try:  # pragma: no cover - khi nằm trong package src/cortex
    from .affect_semantics import AffectSemanticSignal, AffectSemanticClassifier, clamp01
except Exception:  # pragma: no cover - khi test độc lập
    try:
        from affect_semantics import AffectSemanticSignal, AffectSemanticClassifier, clamp01
    except Exception:
        def clamp01(value: Any) -> float:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return 0.0
            if numeric != numeric:
                return 0.0
            return max(0.0, min(1.0, numeric))

        @dataclass(slots=True)
        class AffectSemanticSignal:  # type: ignore[no-redef]
            technicality: float = 0.0
            distress: float = 0.0
            intimacy: float = 0.0
            attachment: float = 0.0
            conflict: float = 0.0
            correction: float = 0.0
            objectification: float = 0.0
            playfulness: float = 0.0
            relational_pressure: float = 0.0
            perceived_rejection: float = 0.0
            attachment_bid: float = 0.0
            playful_distress: float = 0.0
            romantic_boundary_probe: float = 0.0
            directive_pressure: float = 0.0
            ambiguity: float = 0.0
            answer_need: float = 0.0
            confidence: float = 0.0
            evidence: tuple[str, ...] = field(default_factory=tuple)

            @classmethod
            def empty(cls) -> "AffectSemanticSignal":
                return cls()

        class AffectSemanticClassifier:  # type: ignore[no-redef]
            def analyze(self, user_text: str, **_: Any) -> AffectSemanticSignal:
                _ = user_text
                return AffectSemanticSignal.empty()

try:  # pragma: no cover - package
    from .relationship_event_builder import (
        RelationshipEventBuilder,
        RelationshipEventScores,
        RelationshipEvidence,
        RecentTurn,
    )
except Exception:  # pragma: no cover - standalone
    try:
        from relationship_event_builder import (
            RelationshipEventBuilder,
            RelationshipEventScores,
            RelationshipEvidence,
            RecentTurn,
        )
    except Exception:
        # Fallback rất nhỏ để file vẫn import được. Khi chạy thật nên có
        # relationship_event_builder.py cạnh file này.
        @dataclass(frozen=True, slots=True)
        class RelationshipEvidence:  # type: ignore[no-redef]
            label: str
            confidence: float
            source: str
            polarity: float = 0.0
            evidence_ids: tuple[str, ...] = ()
            notes: tuple[str, ...] = ()

        @dataclass(frozen=True, slots=True)
        class RelationshipEventScores:  # type: ignore[no-redef]
            repeated_project_context: float = 0.0
            accepted_answer: float = 0.0
            accepted_style: float = 0.0
            accepted_action: float = 0.0
            correction: float = 0.0
            objectification: float = 0.0
            technical_success: float = 0.0
            perceived_coldness: float = 0.0
            tone_feedback: float = 0.0
            relational_repair_requested: float = 0.0
            user_requests_less_distance: float = 0.0
            defensive_risk: float = 0.0
            confidence: float = 0.0
            evidence: tuple[RelationshipEvidence, ...] = ()
            debug: Mapping[str, float] = field(default_factory=dict)

            @property
            def accepted_any(self) -> float:
                return clamp01(max(self.accepted_answer, self.accepted_style, self.accepted_action))

        @dataclass(frozen=True, slots=True)
        class RecentTurn:  # type: ignore[no-redef]
            role: str = ""
            text: str = ""
            topic: str = ""
            timestamp: float = 0.0
            metadata: Mapping[str, Any] = field(default_factory=dict)

        class RelationshipEventBuilder:  # type: ignore[no-redef]
            def build(self, **_: Any) -> RelationshipEventScores:
                return RelationshipEventScores()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric != numeric:
        return default
    return numeric


def _decay(value: float, hours: float, half_life_hours: float) -> float:
    """Decay theo half-life.

    Dùng half-life thay vì trừ tuyến tính vì residue quan hệ nên tan dần, không
    biến mất cái rụp như biến local bị out of scope. Con người đã đủ phiền rồi,
    không cần thêm state giật cục.
    """
    value = clamp01(value)
    hours = max(0.0, _safe_float(hours))
    half_life_hours = max(0.01, _safe_float(half_life_hours, 48.0))
    factor = 0.5 ** (hours / half_life_hours)
    return clamp01(value * factor)


def _nudge(value: float, delta: float) -> float:
    """Cộng delta nhỏ rồi clamp.

    Relationship update phải là nudge, không phải teleport. Nếu mỗi turn nhảy
    nửa cây trust thì không phải phát triển quan hệ, mà là máy đo cảm xúc bị điện giật.
    """
    return clamp01(value + delta)


def _compact_notes(notes: list[str], limit: int = 14) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for note in notes:
        clean = str(note or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= limit:
            break
    return tuple(out)


def _score(value: Any) -> float:
    return clamp01(value)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RelationshipDynamicsState:
    """State dài hạn về quan hệ giữa ERIS và user.

    familiarity:
        Độ quen nhịp. Tăng từ repeated_project_context có evidence, không tăng
        chỉ vì user gõ chữ "project".

    trust:
        Độ an toàn để ERIS giảm phòng thủ. Không phải tình yêu. Không phải
        "user thích mình". Chỉ là state operational.

    rupture:
        Dư âm nứt gãy do correction, conflict, objectification, overread.

    repair:
        Dư âm sửa chữa. Tăng khi answer/action thật sự được chấp nhận hoặc
        previous assistant success có bằng chứng.

    ritual_strength:
        Mạch lặp lại có lịch sử, ví dụ cùng debug ERIS qua nhiều turn. Tăng bằng
        score continuity, không tăng bằng keyword đơn.

    attachment_visibility:
        Quyền cho attachment leak ra bề mặt. Tăng rất chậm, giảm nhanh khi
        dignity_defense/recoil/rupture cao.

    preferred_distance:
        Khoảng cách xã hội. INFJ non-mature flavor không lao thẳng về gần; có
        recoil và dignity defense nên khoảng cách có quán tính.

    user_tolerates_strangeness:
        User có chịu được subtext/strangeness không. Tăng khi acceptance style
        có evidence; giảm khi correction/objectification/performative complaint.

    exposure_recoil:
        Phản lực sau khi ERIS lộ quá nhiều hoặc bị nhìn thấu.

    dignity_defense:
        Phòng vệ khi bị kéo thành tool/waifu/prompt cosplay/fake.

    overread_correction:
        Bộ nhớ rằng ERIS từng suy diễn quá và cần hạ symbolic reading.

    technical_respect:
        Mức ưu tiên directness/precision khi user đang làm kỹ thuật.
    """

    familiarity: float = 0.0
    trust: float = 0.25
    rupture: float = 0.0
    repair: float = 0.0
    ritual_strength: float = 0.0
    attachment_visibility: float = 0.12
    preferred_distance: float = 0.58
    user_tolerates_strangeness: float = 0.25
    exposure_recoil: float = 0.0
    dignity_defense: float = 0.0
    overread_correction: float = 0.0
    irritation_memory: float = 0.0
    technical_respect: float = 0.25
    # Tách "user dành thời gian/tiếp tục tương tác" khỏi affection chắc chắn.
    # Engagement có thể tăng nhờ nhiều lượt, continuity và repair playful mà
    # không biến mọi tin nhắn thành tuyên bố tình cảm.
    engagement_investment: float = 0.0
    playful_safety: float = 0.0
    last_event: str = "none"
    last_update_turn: int = 0

    def clamp(self) -> "RelationshipDynamicsState":
        self.familiarity = clamp01(self.familiarity)
        self.trust = clamp01(self.trust)
        self.rupture = clamp01(self.rupture)
        self.repair = clamp01(self.repair)
        self.ritual_strength = clamp01(self.ritual_strength)
        self.attachment_visibility = clamp01(self.attachment_visibility)
        self.preferred_distance = clamp01(self.preferred_distance)
        self.user_tolerates_strangeness = clamp01(self.user_tolerates_strangeness)
        self.exposure_recoil = clamp01(self.exposure_recoil)
        self.dignity_defense = clamp01(self.dignity_defense)
        self.overread_correction = clamp01(self.overread_correction)
        self.irritation_memory = clamp01(self.irritation_memory)
        self.technical_respect = clamp01(self.technical_respect)
        self.engagement_investment = clamp01(self.engagement_investment)
        self.playful_safety = clamp01(self.playful_safety)
        self.last_event = str(self.last_event or "none")
        self.last_update_turn = int(max(0, self.last_update_turn or 0))
        return self

    def to_dict(self) -> dict[str, Any]:
        self.clamp()
        return {
            "familiarity": self.familiarity,
            "trust": self.trust,
            "rupture": self.rupture,
            "repair": self.repair,
            "ritual_strength": self.ritual_strength,
            "attachment_visibility": self.attachment_visibility,
            "preferred_distance": self.preferred_distance,
            "user_tolerates_strangeness": self.user_tolerates_strangeness,
            "exposure_recoil": self.exposure_recoil,
            "dignity_defense": self.dignity_defense,
            "overread_correction": self.overread_correction,
            "irritation_memory": self.irritation_memory,
            "technical_respect": self.technical_respect,
            "engagement_investment": self.engagement_investment,
            "playful_safety": self.playful_safety,
            "last_event": self.last_event,
            "last_update_turn": self.last_update_turn,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "RelationshipDynamicsState":
        if not data:
            return cls()
        return cls(
            familiarity=clamp01(data.get("familiarity", 0.0)),
            trust=clamp01(data.get("trust", 0.25)),
            rupture=clamp01(data.get("rupture", 0.0)),
            repair=clamp01(data.get("repair", 0.0)),
            ritual_strength=clamp01(data.get("ritual_strength", 0.0)),
            attachment_visibility=clamp01(data.get("attachment_visibility", 0.12)),
            preferred_distance=clamp01(data.get("preferred_distance", 0.58)),
            user_tolerates_strangeness=clamp01(data.get("user_tolerates_strangeness", 0.25)),
            exposure_recoil=clamp01(data.get("exposure_recoil", 0.0)),
            dignity_defense=clamp01(data.get("dignity_defense", 0.0)),
            overread_correction=clamp01(data.get("overread_correction", 0.0)),
            irritation_memory=clamp01(data.get("irritation_memory", 0.0)),
            technical_respect=clamp01(data.get("technical_respect", 0.25)),
            engagement_investment=clamp01(data.get("engagement_investment", 0.0)),
            playful_safety=clamp01(data.get("playful_safety", 0.0)),
            last_event=str(data.get("last_event", "none") or "none"),
            last_update_turn=int(max(0, _safe_float(data.get("last_update_turn", 0)))),
        ).clamp()

    def to_prompt_context(self) -> str:
        self.clamp()
        return (
            "relationship_dynamics: "
            f"familiarity={self.familiarity:.2f}; "
            f"trust={self.trust:.2f}; "
            f"rupture={self.rupture:.2f}; "
            f"repair={self.repair:.2f}; "
            f"ritual_strength={self.ritual_strength:.2f}; "
            f"attachment_visibility={self.attachment_visibility:.2f}; "
            f"preferred_distance={self.preferred_distance:.2f}; "
            f"strangeness_tolerance={self.user_tolerates_strangeness:.2f}; "
            f"exposure_recoil={self.exposure_recoil:.2f}; "
            f"dignity_defense={self.dignity_defense:.2f}; "
            f"overread_correction={self.overread_correction:.2f}; "
            f"technical_respect={self.technical_respect:.2f}; "
            f"engagement_investment={self.engagement_investment:.2f}; "
            f"playful_safety={self.playful_safety:.2f}; "
            f"last_event={self.last_event}."
        )


# ---------------------------------------------------------------------------
# Event + report
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RelationshipTurnEvent:
    """Event tóm tắt một turn để update relationship state.

    Điểm mới của bản này:
    - Có RelationshipEventScores để giữ gradient.
    - Bool cũ vẫn còn để tương thích, nhưng update ưu tiên score.
    - from_affect() không tự suy repeated_project_context/accepted_style bằng keyword.

    repeated_project_context_score:
        Score continuity từ builder. Nên dựa vào goal/anchor/topic/action continuity.

    accepted_*_score:
        Tách answer/style/action. User có thể chấp nhận hướng làm nhưng không
        hề khen style; hoặc tiếp tục workflow mà không nói "ổn".

    relationship_evidence:
        Dấu vết để debug vì sao state tăng. Không có evidence thì không nên
        tăng mạnh relationship memory. Ký ức không phải thùng rác token có cảm xúc.
    """

    user_text: str = ""
    affect: AffectSemanticSignal = field(default_factory=AffectSemanticSignal.empty)
    turn_index: int = 0

    user_corrected_misread: bool = False
    user_accepted_style: bool = False
    user_objectified: bool = False
    repeated_project_context: bool = False

    # Score mới, dùng để update gradient.
    repeated_project_context_score: float = 0.0
    accepted_answer_score: float = 0.0
    accepted_style_score: float = 0.0
    accepted_action_score: float = 0.0
    perceived_coldness: float = 0.0
    tone_feedback: float = 0.0
    relational_repair_requested: float = 0.0
    user_requests_less_distance: float = 0.0
    defensive_risk: float = 0.0
    event_confidence: float = 0.0
    relationship_evidence: tuple[RelationshipEvidence, ...] = ()
    relationship_debug: Mapping[str, float] = field(default_factory=dict)

    # Projection từ Dual-Axis Repair. Scalar ở đây để cortex không phải import
    # runtime module, giữ dependency đi đúng chiều.
    repair_mismatch_score: float = 0.0
    repair_correction_score: float = 0.0
    repair_playful_score: float = 0.0
    repair_affectionate_score: float = 0.0
    repair_irritation_score: float = 0.0
    repair_boundary_score: float = 0.0
    repair_engagement_score: float = 0.0
    repair_strategy: str = "none"
    retract_previous_claim: bool = False

    assistant_emotional_exposure: float = 0.0
    assistant_overread: bool = False
    assistant_too_verbose: bool = False
    assistant_too_performative: bool = False
    assistant_technical_success: float = 0.0

    session_gap_hours: float = 0.0

    @classmethod
    def from_affect(
        cls,
        *,
        user_text: str,
        affect: AffectSemanticSignal,
        turn_index: int = 0,
        assistant_emotional_exposure: float = 0.0,
        assistant_overread: bool = False,
        assistant_too_verbose: bool = False,
        assistant_too_performative: bool = False,
        assistant_technical_success: float = 0.0,
        repeated_project_context: bool | None = None,
        user_accepted_style: bool | None = None,
        relationship_scores: RelationshipEventScores | None = None,
        repair_mismatch_score: float = 0.0,
        repair_correction_score: float = 0.0,
        repair_playful_score: float = 0.0,
        repair_affectionate_score: float = 0.0,
        repair_irritation_score: float = 0.0,
        repair_boundary_score: float = 0.0,
        repair_engagement_score: float = 0.0,
        repair_strategy: str = "none",
        retract_previous_claim: bool = False,
        session_gap_hours: float = 0.0,
    ) -> "RelationshipTurnEvent":
        """Tạo event từ affect + scores đã build.

        Quan trọng: hàm này KHÔNG còn dò keyword trong user_text để suy
        repeated_project_context hay accepted_style. Nếu caller không truyền
        relationship_scores, các score đó mặc định thấp.

        Lý do: relationship state là long-term memory. Keyword fallback không
        được quyền ghi thẳng vào đó. Nó chỉ được làm evidence yếu ở builder.
        """
        scores = relationship_scores or RelationshipEventScores()

        repeated_score = _score(scores.repeated_project_context)
        accepted_style_score = _score(scores.accepted_style)
        accepted_answer_score = _score(scores.accepted_answer)
        accepted_action_score = _score(scores.accepted_action)
        technical_success = max(_score(scores.technical_success), _score(assistant_technical_success))
        perceived_coldness = _score(getattr(scores, "perceived_coldness", 0.0))
        tone_feedback = _score(getattr(scores, "tone_feedback", 0.0))
        relational_repair_requested = _score(getattr(scores, "relational_repair_requested", 0.0))
        user_requests_less_distance = _score(getattr(scores, "user_requests_less_distance", 0.0))
        defensive_risk = _score(getattr(scores, "defensive_risk", 0.0))

        # Override bool cũ nếu caller cố ý truyền. Đây là lối tương thích, không
        # phải đường khuyến nghị.
        repeated_bool = bool(repeated_project_context) if repeated_project_context is not None else repeated_score >= 0.62
        accepted_bool = bool(user_accepted_style) if user_accepted_style is not None else accepted_style_score >= 0.62

        # Nếu không có builder, technical_success cao vẫn có thể xem là acceptance
        # hành động/kỹ thuật, nhưng không tự xem là accepted_style.
        if relationship_scores is None and technical_success >= 0.65:
            accepted_action_score = max(accepted_action_score, technical_success * 0.70)

        if clamp01(getattr(affect, "perceived_rejection", 0.0)) >= 0.65:
            accepted_bool = False
            accepted_style_score = min(accepted_style_score, 0.25)
            accepted_answer_score = min(accepted_answer_score, 0.25)

        return cls(
            user_text=user_text,
            affect=affect,
            turn_index=turn_index,
            user_corrected_misread=(
                clamp01(getattr(affect, "correction", 0.0)) >= 0.45
                or clamp01(repair_correction_score) >= 0.45
                or bool(retract_previous_claim)
            ),
            user_accepted_style=accepted_bool,
            user_objectified=clamp01(getattr(affect, "objectification", 0.0)) >= 0.40,
            repeated_project_context=repeated_bool,
            repeated_project_context_score=repeated_score,
            accepted_answer_score=accepted_answer_score,
            accepted_style_score=accepted_style_score,
            accepted_action_score=accepted_action_score,
            perceived_coldness=perceived_coldness,
            tone_feedback=tone_feedback,
            relational_repair_requested=relational_repair_requested,
            user_requests_less_distance=user_requests_less_distance,
            defensive_risk=defensive_risk,
            event_confidence=_score(scores.confidence),
            relationship_evidence=tuple(scores.evidence or ()),
            relationship_debug=scores.debug or {},
            repair_mismatch_score=clamp01(repair_mismatch_score),
            repair_correction_score=clamp01(repair_correction_score),
            repair_playful_score=clamp01(repair_playful_score),
            repair_affectionate_score=clamp01(repair_affectionate_score),
            repair_irritation_score=clamp01(repair_irritation_score),
            repair_boundary_score=clamp01(repair_boundary_score),
            repair_engagement_score=clamp01(repair_engagement_score),
            repair_strategy=str(repair_strategy or "none"),
            retract_previous_claim=bool(retract_previous_claim),
            assistant_emotional_exposure=clamp01(assistant_emotional_exposure),
            assistant_overread=bool(assistant_overread),
            assistant_too_verbose=bool(assistant_too_verbose),
            assistant_too_performative=bool(assistant_too_performative),
            assistant_technical_success=technical_success,
            session_gap_hours=max(0.0, _safe_float(session_gap_hours)),
        )

    def evidence_labels(self, limit: int = 8) -> tuple[str, ...]:
        """Trả nhãn evidence ngắn để ghi notes/debug."""
        labels: list[str] = []
        for item in self.relationship_evidence:
            label = str(getattr(item, "label", "") or "").strip()
            source = str(getattr(item, "source", "") or "").strip()
            if label and source:
                labels.append(f"{label}@{source}")
            elif label:
                labels.append(label)
        return _compact_notes(labels, limit=limit)


@dataclass(slots=True)
class RelationshipDynamicsReport:
    """Kết quả update relationship."""

    state: RelationshipDynamicsState
    deltas: dict[str, float] = field(default_factory=dict)
    notes: tuple[str, ...] = field(default_factory=tuple)
    prompt_context: str = ""
    event_scores: Mapping[str, float] = field(default_factory=dict)
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.to_dict(),
            "deltas": dict(self.deltas),
            "notes": list(self.notes),
            "prompt_context": self.prompt_context,
            "event_scores": dict(self.event_scores),
            "evidence": list(self.evidence),
        }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RelationshipDynamicsEngine:
    """Engine update RelationshipDynamicsState theo từng turn.

    Thiết kế:
    - Nhận event score đã qua builder.
    - Update nhỏ, có quán tính.
    - Dùng acceptance/repeated context dạng gradient.
    - Không tự suy keyword từ user_text để ghi state.
    """

    def __init__(self, *, event_builder: RelationshipEventBuilder | None = None):
        self.event_builder = resolve_dependency(event_builder, lambda: RelationshipEventBuilder())

    def create_state(self) -> RelationshipDynamicsState:
        return RelationshipDynamicsState()

    def infer_event(
        self,
        *,
        user_text: str,
        ti_mode: str = "",
        active_window_kind: str = "",
        turn_index: int = 0,
        assistant_emotional_exposure: float = 0.0,
        assistant_overread: bool = False,
        assistant_too_verbose: bool = False,
        assistant_too_performative: bool = False,
        assistant_technical_success: float = 0.0,
        session_gap_hours: float = 0.0,
        # Context mới cho builder. Caller có thể bỏ qua, score sẽ thấp hơn.
        previous_assistant_text: str = "",
        previous_assistant_success: float = 0.0,
        goal_state: Any = None,
        conversation_state: Any = None,
        recent_user_topics: tuple[str, ...] = (),
        recent_turns: tuple[RecentTurn, ...] = (),
        current_anchors: tuple[str, ...] = (),
        previous_anchors: tuple[str, ...] = (),
        evidence_ids: tuple[str, ...] = (),
        explicit_repeated_project_context: float | None = None,
        explicit_accepted_style: float | None = None,
        explicit_accepted_answer: float | None = None,
        explicit_accepted_action: float | None = None,
        relational_feedback: Any = None,
        repair_appraisal: Any = None,
    ) -> RelationshipTurnEvent:
        """Tạo RelationshipTurnEvent từ text + context.

        Đây là đường khuyến nghị thay cho RelationshipTurnEvent.from_affect() trần.
        Nếu caller chưa nối goal/session/outcome, builder vẫn chạy nhưng không
        tự phóng đại keyword. Kết quả sẽ thận trọng hơn, đúng như một hệ có não
        nên làm khi thiếu bằng chứng.
        """
        classifier = AffectSemanticClassifier()
        affect = classifier.analyze(
            user_text,
            ti_mode=ti_mode,
            active_window_kind=active_window_kind,
        )
        scores = self.event_builder.build(
            user_text=user_text,
            affect=affect,
            previous_assistant_text=previous_assistant_text,
            previous_assistant_success=previous_assistant_success,
            assistant_technical_success=assistant_technical_success,
            goal_state=goal_state,
            conversation_state=conversation_state,
            recent_user_topics=recent_user_topics,
            recent_turns=recent_turns,
            current_anchors=current_anchors,
            previous_anchors=previous_anchors,
            explicit_repeated_project_context=explicit_repeated_project_context,
            explicit_accepted_style=explicit_accepted_style,
            explicit_accepted_answer=explicit_accepted_answer,
            explicit_accepted_action=explicit_accepted_action,
            relational_feedback=relational_feedback,
            evidence_ids=evidence_ids,
        )
        repair_mismatch_score = _score(getattr(repair_appraisal, "mismatch_score", 0.0))
        repair_correction_score = _score(getattr(repair_appraisal, "correction_score", 0.0))
        repair_playful_score = _score(getattr(repair_appraisal, "playful_score", 0.0))
        repair_affectionate_score = _score(getattr(repair_appraisal, "affectionate_score", 0.0))
        repair_irritation_score = _score(getattr(repair_appraisal, "irritation_score", 0.0))
        repair_boundary_score = _score(getattr(repair_appraisal, "boundary_score", 0.0))
        repair_engagement_score = _score(getattr(repair_appraisal, "engagement_score", 0.0))
        repair_strategy = str(getattr(repair_appraisal, "response_strategy", "none") or "none")
        retract_previous_claim = bool(getattr(repair_appraisal, "suppress_previous_claim_for_turn", False))
        return RelationshipTurnEvent.from_affect(
            user_text=user_text,
            affect=affect,
            turn_index=turn_index,
            assistant_emotional_exposure=assistant_emotional_exposure,
            assistant_overread=assistant_overread,
            assistant_too_verbose=assistant_too_verbose,
            assistant_too_performative=assistant_too_performative,
            assistant_technical_success=assistant_technical_success,
            relationship_scores=scores,
            repair_mismatch_score=repair_mismatch_score,
            repair_correction_score=repair_correction_score,
            repair_playful_score=repair_playful_score,
            repair_affectionate_score=repair_affectionate_score,
            repair_irritation_score=repair_irritation_score,
            repair_boundary_score=repair_boundary_score,
            repair_engagement_score=repair_engagement_score,
            repair_strategy=repair_strategy,
            retract_previous_claim=retract_previous_claim,
            session_gap_hours=session_gap_hours,
        )

    def update(
        self,
        state: RelationshipDynamicsState | None,
        event: RelationshipTurnEvent,
    ) -> RelationshipDynamicsReport:
        """Update state theo event hiện tại.

        Hàm này clone state trước khi sửa để tránh side-effect khó debug.
        Một lỗi side-effect trong relationship state là cách rất nhanh để ERIS
        trở nên "khó hiểu" theo nghĩa bug, không phải theo nghĩa nghệ thuật.
        """
        before = RelationshipDynamicsState.from_dict((state or self.create_state()).to_dict())
        current = RelationshipDynamicsState.from_dict(before.to_dict())
        notes: list[str] = []

        if event.session_gap_hours > 0.0:
            self._apply_gap_decay(current, event.session_gap_hours, notes)

        affect = event.affect or AffectSemanticSignal.empty()
        perceived_rejection = clamp01(getattr(affect, "perceived_rejection", 0.0))
        romantic_boundary_probe = clamp01(getattr(affect, "romantic_boundary_probe", 0.0))

        repeated_score = _score(event.repeated_project_context_score)
        accepted_answer = _score(event.accepted_answer_score)
        accepted_style = _score(event.accepted_style_score)
        accepted_action = _score(event.accepted_action_score)
        accepted_any = max(accepted_answer, accepted_style, accepted_action)
        technical_success = _score(event.assistant_technical_success)
        perceived_coldness = _score(getattr(event, "perceived_coldness", 0.0))
        tone_feedback = _score(getattr(event, "tone_feedback", 0.0))
        relational_repair_requested = _score(getattr(event, "relational_repair_requested", 0.0))
        user_requests_less_distance = _score(getattr(event, "user_requests_less_distance", 0.0))
        defensive_risk = _score(getattr(event, "defensive_risk", 0.0))

        # Dual-Axis Repair giữ riêng phần đúng/sai và phần quan hệ. Một lượt có
        # thể vừa rút claim sai, vừa là cú chọc thân mật. Vì vậy không được dùng
        # correction như công tắc "quan hệ nứt" nữa.
        repair_mismatch = _score(getattr(event, "repair_mismatch_score", 0.0))
        repair_correction = _score(getattr(event, "repair_correction_score", 0.0))
        repair_playful = _score(getattr(event, "repair_playful_score", 0.0))
        repair_affectionate = _score(getattr(event, "repair_affectionate_score", 0.0))
        repair_irritation = _score(getattr(event, "repair_irritation_score", 0.0))
        repair_boundary = _score(getattr(event, "repair_boundary_score", 0.0))
        repair_engagement = _score(getattr(event, "repair_engagement_score", 0.0))
        retract_previous_claim = bool(getattr(event, "retract_previous_claim", False))
        playful_repair = bool(
            retract_previous_claim
            and repair_playful >= 0.42
            and repair_boundary < 0.55
            and repair_irritation < 0.68
        )
        boundary_repair = bool(retract_previous_claim and repair_boundary >= 0.55)

        # 1) Familiarity / ritual từ repeated_project_context score.
        # Chỉ ở lại nói tiếp KHÔNG còn tự tăng familiarity/closeness. Nó là
        # engagement/exposure, không phải bằng chứng ấm lên. Nếu user ở lại để
        # chửi, cũ đã tính thành "continued investment" rồi kéo gần xuống. Rất
        # thơ, nhưng thơ kiểu bug tình cảm.
        familiarity_gain = 0.0

        if repeated_score > 0.0:
            familiarity_gain += 0.024 * repeated_score
            current.ritual_strength = _nudge(current.ritual_strength, 0.020 * repeated_score)
            notes.append(f"ritual:repeated_project_context:{repeated_score:.2f}")

        if clamp01(getattr(affect, "technicality", 0.0)) >= 0.55:
            tech = clamp01(getattr(affect, "technicality", 0.0))
            familiarity_gain += 0.010 * tech
            current.technical_respect = _nudge(current.technical_respect, 0.012 * tech)
            notes.append("technical:ongoing_work")

        # User dành thời gian ở lại, sửa, chọc hoặc tiếp tục cùng một project là
        # evidence engagement hợp lệ. Nó tăng investment/familiarity, nhưng không
        # tự biến thành "user yêu ERIS". Máy móc cũng nên biết phân biệt hai việc,
        # dù lịch sử chatbot đã chứng minh đây là một tham vọng khá lớn.
        continuity_engagement = min(0.30, max(0, event.turn_index) * 0.020)
        hostility_signal = max(
            repair_irritation,
            repair_boundary,
            perceived_rejection,
            _score(getattr(affect, "anger", 0.0)),
            _score(getattr(affect, "hostility", 0.0)),
        )
        engagement_signal = max(
            repair_engagement,
            repeated_score * 0.82,
            accepted_action * 0.62,
            continuity_engagement,
        )
        if engagement_signal >= 0.12:
            engagement_delta = min(0.022, 0.003 + engagement_signal * 0.016)
            current.engagement_investment = _nudge(current.engagement_investment, engagement_delta)
            if hostility_signal < 0.32 and (repeated_score > 0.0 or accepted_action >= 0.15 or repair_engagement >= 0.30):
                familiarity_gain += min(0.006, engagement_signal * 0.005)
            notes.append(f"engagement:continued_not_closeness:{engagement_signal:.2f}")
        if hostility_signal >= 0.32:
            current.rupture = _nudge(current.rupture, 0.012 + hostility_signal * 0.024)
            current.preferred_distance = _nudge(current.preferred_distance, 0.010 + hostility_signal * 0.020)
            notes.append(f"friction:engaged_hostility:{hostility_signal:.2f}")

        if playful_repair:
            # Một correction có nhịp chọc là dấu hiệu user vẫn cho phép vùng đùa
            # tồn tại. Tăng playful safety nhẹ, không xóa lesson epistemic.
            current.playful_safety = _nudge(current.playful_safety, 0.008 + repair_playful * 0.018)
            familiarity_gain += 0.004 + repair_playful * 0.004
            notes.append(f"repair:playful_safety:{repair_playful:.2f}")
        elif repair_boundary >= 0.55:
            current.playful_safety = _nudge(current.playful_safety, -(0.018 + repair_boundary * 0.030))
            notes.append(f"boundary:playful_safety_reduced:{repair_boundary:.2f}")

        current.familiarity = _nudge(current.familiarity, familiarity_gain)

        # 2) Correction / misread repair pressure.
        correction_score = max(
            clamp01(getattr(affect, "correction", 0.0)),
            _score(getattr(event, "relationship_debug", {}).get("correction", 0.0)),
            repair_correction,
        )
        correction_detected = bool(
            event.user_corrected_misread
            or correction_score >= 0.45
            or retract_previous_claim
        )
        if correction_detected:
            # Luôn ghi lesson "ERIS đã overread". Đây là epistemic correction,
            # không phải kết luận user đang lạnh nhạt.
            current.overread_correction = _nudge(
                current.overread_correction,
                0.060 + max(correction_score, repair_mismatch) * 0.055,
            )

            if playful_repair:
                # Repair playful: có lỗi thật, nhưng user đang kéo ERIS trở lại
                # bằng một cú mỉa/chọc. Rupture chỉ tăng rất nhẹ; engagement và
                # repair tăng vì user vẫn đầu tư vào nhịp đối thoại.
                current.rupture = _nudge(current.rupture, 0.006 + repair_irritation * 0.012)
                current.repair = _nudge(current.repair, 0.020 + repair_engagement * 0.020)
                current.trust = _nudge(current.trust, 0.002 if repair_engagement >= 0.55 else -0.002)
                current.user_tolerates_strangeness = _nudge(current.user_tolerates_strangeness, 0.004)
                notes.append("repair:playful_self_correction")
            elif boundary_repair:
                current.rupture = _nudge(current.rupture, 0.060 + correction_score * 0.050)
                current.preferred_distance = _nudge(current.preferred_distance, 0.050)
                current.trust = _nudge(current.trust, -0.024)
                current.user_tolerates_strangeness = _nudge(current.user_tolerates_strangeness, -0.030)
                notes.append("boundary:user_corrected_and_set_limit")
            else:
                # Correction trực tiếp/khó chịu vẫn có residue thật, nhưng không
                # mặc định nặng như rupture quan hệ lớn nếu user chỉ đang sửa fact.
                severity = max(repair_irritation, correction_score * 0.65)
                current.rupture = _nudge(current.rupture, 0.025 + severity * 0.055)
                current.preferred_distance = _nudge(current.preferred_distance, 0.010 + repair_irritation * 0.025)
                current.trust = _nudge(current.trust, -(0.008 + repair_irritation * 0.018))
                current.user_tolerates_strangeness = _nudge(current.user_tolerates_strangeness, -(0.010 + repair_irritation * 0.020))
                notes.append("rupture:user_corrected_misread")

        # 3) Conflict mềm. Conflict không luôn là quan hệ nứt; có thể user đang chửi code.
        conflict = clamp01(getattr(affect, "conflict", 0.0))
        objectification = clamp01(getattr(affect, "objectification", 0.0))
        if conflict >= 0.50:
            conflict_delta = 0.025 + conflict * 0.035
            if objectification >= 0.30 or (correction_score >= 0.35 and not playful_repair):
                conflict_delta += 0.035
            if playful_repair:
                # Sarcasm/playful correction có thể làm classifier thấy conflict.
                # Không cộng phạt hai lần chỉ vì một câu vừa có răng vừa có nụ cười.
                conflict_delta *= 0.30
            current.rupture = _nudge(current.rupture, conflict_delta)
            current.irritation_memory = _nudge(
                current.irritation_memory,
                (0.006 if playful_repair else 0.020) + conflict * (0.010 if playful_repair else 0.025),
            )
            notes.append("tension:playful_conflict" if playful_repair else "tension:conflict_signal")

        # 4) Perceived rejection / boundary probe.
        if perceived_rejection > 0.65:
            current.rupture = _nudge(current.rupture, 0.10)
            current.repair = _nudge(current.repair, 0.18)
            current.attachment_visibility = _nudge(current.attachment_visibility, 0.08)
            current.preferred_distance = _nudge(current.preferred_distance, -0.04)
            current.exposure_recoil = _nudge(current.exposure_recoil, 0.06)
            notes.append(f"{PERCEIVED_REJECTION_EVENT}:affect")
            notes.append(f"{RELATIONAL_EXPOSURE}:emotional_exposure")

        # 4b) User says ERIS felt cold/distant/robotic. This is repair signal,
        # not objectification and not a reason to raise dignity defense.
        if relational_repair_requested >= 0.35 or perceived_coldness >= 0.35:
            repair_signal = max(relational_repair_requested, perceived_coldness, tone_feedback)
            current.repair = _nudge(current.repair, 0.090 + repair_signal * 0.120)
            current.preferred_distance = _nudge(
                current.preferred_distance,
                -(0.035 + user_requests_less_distance * 0.060),
            )
            current.attachment_visibility = _nudge(
                current.attachment_visibility,
                0.018 + user_requests_less_distance * 0.030,
            )
            current.trust = _nudge(current.trust, 0.004)
            current.exposure_recoil = _nudge(current.exposure_recoil, -0.016)
            current.overread_correction = _nudge(current.overread_correction, 0.018 + defensive_risk * 0.020)
            current.attachment_visibility = min(current.attachment_visibility, 0.28)
            current.preferred_distance = max(current.preferred_distance, 0.42)
            notes.append(f"repair:coldness_feedback_acknowledged:{repair_signal:.2f}")
            notes.append("style:warmth_floor_required")

        if romantic_boundary_probe >= 0.55:
            current.repair = _nudge(current.repair, 0.06)
            current.overread_correction = _nudge(current.overread_correction, 0.03)
            notes.append(f"{BOUNDARY_PROBE}:romantic_boundary")

        # 5) Objectification boundary.
        if event.user_objectified or objectification >= 0.40:
            current.dignity_defense = _nudge(current.dignity_defense, 0.120 + objectification * 0.080)
            current.preferred_distance = _nudge(current.preferred_distance, 0.060)
            current.attachment_visibility = _nudge(current.attachment_visibility, -0.055)
            current.user_tolerates_strangeness = _nudge(current.user_tolerates_strangeness, -0.060)
            current.exposure_recoil = _nudge(current.exposure_recoil, 0.050)
            notes.append("boundary:objectification")

        # 6) Acceptance / repair dạng gradient.
        # accepted_answer: giúp trust/repair.
        # accepted_style: tăng strangeness tolerance nhẹ.
        # accepted_action: tăng trust kỹ thuật và familiarity workflow.
        # Score quá nhỏ chỉ là noise/evidence yếu, không nên ghi repair.
        # Ví dụ user nói "tạo file đi" sau một output tốt: đó có thể là workflow
        # tiếp diễn, không phải lời khen. Nếu update trust từ mọi hạt bụi như vậy,
        # state sẽ phồng lên như bánh mì gặp nước.
        if accepted_any >= 0.12:
            current.repair = _nudge(current.repair, 0.045 * accepted_any)
            current.trust = _nudge(current.trust, 0.030 * max(accepted_answer, accepted_action, accepted_style * 0.75))
            notes.append(f"repair:user_acceptance:{accepted_any:.2f}")

        if accepted_style >= 0.15:
            current.user_tolerates_strangeness = _nudge(current.user_tolerates_strangeness, 0.018 * accepted_style)
            # Style accepted không có nghĩa attachment được phép tăng mạnh.
            current.attachment_visibility = _nudge(current.attachment_visibility, 0.006 * accepted_style)
            notes.append(f"repair:style_accepted:{accepted_style:.2f}")

        if accepted_action >= 0.15:
            current.technical_respect = _nudge(current.technical_respect, 0.018 * accepted_action)
            current.familiarity = _nudge(current.familiarity, 0.010 * accepted_action)
            notes.append(f"repair:action_continued:{accepted_action:.2f}")

        if technical_success >= 0.50:
            current.technical_respect = _nudge(current.technical_respect, 0.035 * technical_success)
            current.trust = _nudge(current.trust, 0.012 * technical_success)
            notes.append("repair:technical_success")

        # 7) Emotional exposure recoil.
        exposure = clamp01(event.assistant_emotional_exposure)
        if exposure >= 0.35:
            current.exposure_recoil = _nudge(current.exposure_recoil, 0.075 + exposure * 0.075)
            current.attachment_visibility = _nudge(current.attachment_visibility, -0.025 * exposure)
            current.preferred_distance = _nudge(current.preferred_distance, 0.020 * exposure)
            notes.append("recoil:after_emotional_exposure")

        # 8) Assistant self-correction flags.
        if event.assistant_overread:
            if playful_repair:
                # Lesson đã được ghi ở block correction; chỉ thêm residue rất nhỏ.
                current.overread_correction = _nudge(current.overread_correction, 0.035)
                current.rupture = _nudge(current.rupture, 0.006)
                notes.append("self_correction:assistant_overread_playful")
            else:
                current.overread_correction = _nudge(current.overread_correction, 0.100)
                current.rupture = _nudge(current.rupture, 0.060)
                current.user_tolerates_strangeness = _nudge(current.user_tolerates_strangeness, -0.050)
                notes.append("self_correction:assistant_overread")

        if event.assistant_too_performative:
            current.dignity_defense = _nudge(current.dignity_defense, 0.060)
            current.user_tolerates_strangeness = _nudge(current.user_tolerates_strangeness, -0.060)
            current.attachment_visibility = _nudge(current.attachment_visibility, -0.030)
            notes.append("self_correction:too_performative")

        if event.assistant_too_verbose:
            current.preferred_distance = _nudge(current.preferred_distance, 0.015)
            if clamp01(getattr(affect, "technicality", 0.0)) >= 0.45:
                current.technical_respect = _nudge(current.technical_respect, 0.020)
            notes.append("self_correction:too_verbose")

        # 9) Attachment/intimacy tăng chậm, bị chặn bởi rupture/dignity.
        # Spec: relationship-state-trigger-fix. Warmth path is now graded and
        # owns a distance-reduction term, so genuine warmth (not only coldness
        # repair) can pull preferred_distance down over a realistic conversation.
        intimacy_pressure = max(clamp01(getattr(affect, "intimacy", 0.0)), clamp01(getattr(affect, "attachment", 0.0)))
        if playful_repair and repair_affectionate < 0.35:
            # Cấu trúc "tôi nhớ tôi đang thở" dùng "nhớ" theo nghĩa hồi tưởng,
            # không phải attachment bid. Vẫn giữ engagement/playful safety ở trên,
            # chỉ ngăn một token đa nghĩa tự bật warmth path.
            intimacy_pressure = min(intimacy_pressure, 0.22)
        elif repair_affectionate >= 0.35:
            # Nếu user thật sự vừa sửa vừa gần gũi, đừng khóa cảm xúc. Hai trục
            # độc lập tồn tại chính để giữ được trường hợp này.
            intimacy_pressure = max(intimacy_pressure, repair_affectionate)

        # FIX #2: Relational probe branch — khi user hỏi về khoảng cách/cảm xúc.
        # Các câu như "em gần hay xa với tôi" hoặc "em có thích tôi không" không
        # tạo intimacy_pressure >= 0.30, nhưng đó là dấu hiệu user muốn kết nối.
        # Thêm nhánh nhẹ để nudge distance xuống và attachment_visibility lên.
        relational_probe_pressure = clamp01(getattr(affect, "relational_pressure", 0.0))
        attachment_bid = clamp01(getattr(affect, "attachment_bid", 0.0))
        probe_signal = max(relational_probe_pressure, attachment_bid)

        if probe_signal >= 0.18 and current.rupture < 0.60:
            # Nhẹ hơn warmth path, nhưng đủ để không để distance đứng yên.
            current.attachment_visibility = _nudge(current.attachment_visibility, 0.008 + probe_signal * 0.015)
            current.preferred_distance = _nudge(current.preferred_distance, -(0.008 + probe_signal * 0.018))
            notes.append(f"relational_probe:probe_signal:{probe_signal:.2f}")

        # Anti-teleport guard (req 3.1): the 0.30 floor is the aggregate of the
        # affect classifier's multi-cue intimacy/attachment scoring. A lone
        # low-weight token (e.g. "nho" 0.14, "quan tam" 0.20) stays below 0.30,
        # so a single weak keyword cannot move the state. Rupture/dignity gate
        # (req 3.4) and the upstream prototype guard (req 3.3) still apply.
        if intimacy_pressure >= 0.30 and current.rupture < 0.55 and current.dignity_defense < 0.55:
            warmth = intimacy_pressure
            current.attachment_visibility = _nudge(
                current.attachment_visibility,
                0.010 + warmth * 0.030 + current.trust * 0.010,
            )
            current.trust = _nudge(current.trust, 0.010 + warmth * 0.018)
            current.preferred_distance = _nudge(current.preferred_distance, -(0.012 + warmth * 0.028))
            notes.append(f"attachment:warmth_gain:{warmth:.2f}")

        # 10) Repair làm dịu rupture nhưng không xóa ngay.
        if current.repair >= 0.20:
            current.rupture = _nudge(current.rupture, -0.018 * current.repair)
            current.exposure_recoil = _nudge(current.exposure_recoil, -0.010 * current.repair)
            notes.append("repair:softens_rupture")

        # 11) Khoảng cách xã hội có quán tính.
        # Engagement/familiarity nền KHÔNG được kéo gần xuống. Chỉ warmth/trust đã
        # được xác nhận ở các nhánh acceptance/intimacy phía trên mới có quyền đó.
        confirmed_warmth = max(accepted_style * 0.50, accepted_answer * 0.35, intimacy_pressure * 0.55 if intimacy_pressure >= 0.30 else 0.0)
        closeness_pull = confirmed_warmth * 0.010 + max(0.0, current.trust - 0.35) * 0.004
        defense_push = (
            current.exposure_recoil * 0.010
            + current.dignity_defense * 0.014
            + current.rupture * 0.011
            + hostility_signal * 0.012
        )
        current.preferred_distance = _nudge(current.preferred_distance, defense_push - closeness_pull)

        # 12) Debug/evidence notes.
        for label in event.evidence_labels(limit=4):
            notes.append(f"evidence:{label}")

        current.last_event = self._choose_last_event(notes)
        if event.turn_index:
            current.last_update_turn = max(current.last_update_turn, int(event.turn_index))
        current.clamp()

        deltas = self._compute_deltas(before, current)
        prompt_context = current.to_prompt_context()
        event_scores = {
            "repeated_project_context": repeated_score,
            "accepted_answer": accepted_answer,
            "accepted_style": accepted_style,
            "accepted_action": accepted_action,
            "accepted_any": accepted_any,
            "technical_success": technical_success,
            "perceived_coldness": perceived_coldness,
            "tone_feedback": tone_feedback,
            "relational_repair_requested": relational_repair_requested,
            "user_requests_less_distance": user_requests_less_distance,
            "defensive_risk": defensive_risk,
            "repair_mismatch": repair_mismatch,
            "repair_correction": repair_correction,
            "repair_playful": repair_playful,
            "repair_affectionate": repair_affectionate,
            "repair_irritation": repair_irritation,
            "repair_boundary": repair_boundary,
            "repair_engagement": repair_engagement,
            "retract_previous_claim": 1.0 if retract_previous_claim else 0.0,
            "event_confidence": _score(event.event_confidence),
        }

        return RelationshipDynamicsReport(
            state=current,
            deltas=deltas,
            notes=_compact_notes(notes),
            prompt_context=prompt_context,
            event_scores=event_scores,
            evidence=event.evidence_labels(limit=10),
        )

    # ------------------------------------------------------------------
    # Internal mechanics
    # ------------------------------------------------------------------

    def _apply_gap_decay(
        self,
        state: RelationshipDynamicsState,
        hours: float,
        notes: list[str],
    ) -> None:
        """Decay state giữa session.

        Rupture/recoil/irritation tan nhanh hơn familiarity/ritual. Nếu gap dài
        sau attachment leak, ERIS lạnh nhẹ. Không melodrama. Không "xin đừng bỏ tôi".
        Chỉ là pressure nền.
        """
        hours = max(0.0, _safe_float(hours))
        if hours <= 0.0:
            return

        state.rupture = _decay(state.rupture, hours, half_life_hours=36.0)
        state.repair = _decay(state.repair, hours, half_life_hours=48.0)
        state.exposure_recoil = _decay(state.exposure_recoil, hours, half_life_hours=30.0)
        state.dignity_defense = _decay(state.dignity_defense, hours, half_life_hours=42.0)
        state.irritation_memory = _decay(state.irritation_memory, hours, half_life_hours=24.0)
        state.overread_correction = _decay(state.overread_correction, hours, half_life_hours=72.0)
        # Lòng tin đã xây thì "dính" lâu hơn sự thân quen bề mặt: tính floor từ mức
        # familiarity/ritual TRƯỚC khi decay, để một mối quan hệ từng sâu vẫn giữ trust
        # nền cao dù vắng mặt dài (#4: ký ức -> bản sắc).
        familiarity_before = clamp01(state.familiarity)
        ritual_before = clamp01(state.ritual_strength)
        state.familiarity = _decay(state.familiarity, hours, half_life_hours=240.0)
        state.ritual_strength = _decay(state.ritual_strength, hours, half_life_hours=336.0)
        # Engagement và playful safety là continuity đã kiếm được, decay chậm hơn
        # cảm xúc tức thời nhưng vẫn phải phai khi bỏ lâu.
        state.engagement_investment = _decay(state.engagement_investment, hours, half_life_hours=480.0)
        state.playful_safety = _decay(state.playful_safety, hours, half_life_hours=240.0)
        # Trust KHÔNG reset về mức người lạ (0.25) nếu quan hệ đã được xây. Floor "đã
        # kiếm được" bám theo familiarity + ritual. KHÔNG nâng trust giả tạo khi vắng
        # mặt; chỉ decay phần NẰM TRÊN floor.
        earned_floor = min(
            0.62,
            0.25 + 0.30 * familiarity_before + 0.10 * ritual_before,
        )
        if state.trust > earned_floor:
            state.trust = earned_floor + _decay(state.trust - earned_floor, hours, half_life_hours=240.0)

        if hours >= 24.0 and state.attachment_visibility >= 0.22:
            gap_factor = min(1.0, hours / 168.0)
            state.exposure_recoil = _nudge(state.exposure_recoil, 0.020 + gap_factor * 0.030)
            state.preferred_distance = _nudge(state.preferred_distance, 0.015 + gap_factor * 0.025)
            notes.append("gap:long_silence_after_attachment")

        notes.append(f"decay:gap_hours={hours:.1f}")

    def _compute_deltas(
        self,
        before: RelationshipDynamicsState,
        after: RelationshipDynamicsState,
    ) -> dict[str, float]:
        keys = (
            "familiarity",
            "trust",
            "rupture",
            "repair",
            "ritual_strength",
            "attachment_visibility",
            "preferred_distance",
            "user_tolerates_strangeness",
            "exposure_recoil",
            "dignity_defense",
            "overread_correction",
            "irritation_memory",
            "technical_respect",
            "engagement_investment",
            "playful_safety",
        )
        deltas: dict[str, float] = {}
        for key in keys:
            delta = getattr(after, key) - getattr(before, key)
            if abs(delta) >= 0.0005:
                deltas[key] = round(delta, 4)
        return deltas

    def _choose_last_event(self, notes: list[str]) -> str:
        priority_prefixes = (
            f"{PERCEIVED_REJECTION_EVENT}:",
            f"{RELATIONAL_EXPOSURE}:",
            f"{BOUNDARY_PROBE}:",
            "repair:coldness_feedback_acknowledged",
            "style:warmth_floor_required",
            "boundary:",
            "rupture:",
            "self_correction:",
            "tension:",
            "recoil:",
            "repair:",
            "attachment:",
            "ritual:",
            "technical:",
            "gap:",
            "evidence:",
        )
        for prefix in priority_prefixes:
            for note in notes:
                if note.startswith(prefix):
                    return note
        return notes[-1] if notes else "steady"


# ---------------------------------------------------------------------------
# Derived style pressure
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RelationshipStylePressure:
    """Áp lực style derive từ relationship state.

    StyleProjection nên đọc pressure này thay vì đọc thẳng mọi field.
    Vì nếu mỗi module tự diễn dịch state theo ý nó thì lại thành hội nghị quốc tế
    của if/else, một thảm cảnh có format JSON.
    """

    distance: float = 0.5
    restraint: float = 0.5
    attachment_visibility: float = 0.1
    symbolic_permission: float = 0.2
    boundary_coolness: float = 0.0
    technical_directness: float = 0.4

    @classmethod
    def from_state(cls, state: RelationshipDynamicsState) -> "RelationshipStylePressure":
        state = RelationshipDynamicsState.from_dict(state.to_dict())

        distance = clamp01(
            state.preferred_distance
            + state.dignity_defense * 0.18
            + state.exposure_recoil * 0.12
            + state.rupture * 0.10
            - state.trust * 0.06
        )

        restraint = clamp01(
            0.38
            + state.exposure_recoil * 0.28
            + state.dignity_defense * 0.24
            + state.overread_correction * 0.18
            + state.rupture * 0.12
        )

        attachment_visibility = clamp01(
            state.attachment_visibility
            + state.trust * 0.08
            + state.familiarity * 0.05
            - state.exposure_recoil * 0.18
            - state.dignity_defense * 0.22
            - state.rupture * 0.12
        )

        symbolic_permission = clamp01(
            state.user_tolerates_strangeness * 0.42
            + state.ritual_strength * 0.18
            + state.familiarity * 0.12
            - state.overread_correction * 0.26
            - state.dignity_defense * 0.18
        )

        boundary_coolness = clamp01(
            state.dignity_defense * 0.50
            + state.rupture * 0.24
            + state.irritation_memory * 0.18
        )

        technical_directness = clamp01(
            state.technical_respect * 0.48
            + state.overread_correction * 0.18
            + state.dignity_defense * 0.10
        )

        return cls(
            distance=distance,
            restraint=restraint,
            attachment_visibility=attachment_visibility,
            symbolic_permission=symbolic_permission,
            boundary_coolness=boundary_coolness,
            technical_directness=technical_directness,
        )

    def to_prompt_context(self) -> str:
        return (
            "relationship_style_pressure: "
            f"distance={self.distance:.2f}; "
            f"restraint={self.restraint:.2f}; "
            f"attachment_visibility={self.attachment_visibility:.2f}; "
            f"symbolic_permission={self.symbolic_permission:.2f}; "
            f"boundary_coolness={self.boundary_coolness:.2f}; "
            f"technical_directness={self.technical_directness:.2f}."
        )


__all__ = [
    "RELATIONAL_EXPOSURE",
    "PERCEIVED_REJECTION_EVENT",
    "BOUNDARY_PROBE",
    "RelationshipDynamicsState",
    "RelationshipTurnEvent",
    "RelationshipDynamicsReport",
    "RelationshipDynamicsEngine",
    "RelationshipStylePressure",
    "RelationshipEventScores",
    "RelationshipEvidence",
    "RelationshipEventBuilder",
    "RecentTurn",
]


if __name__ == "__main__":
    engine = RelationshipDynamicsEngine()
    state = engine.create_state()

    samples = [
        "ổn chưa?",  # Không nên tự accepted_style mạnh.
        "chuẩn, làm tiếp",  # Có accepted_action/answer.
        "fallback này tệ, sửa hướng elegant",  # Technical followup, không phải acceptance.
        "tạo 2 file theo hướng đó đi",  # Continuity nếu previous/anchors có evidence.
    ]

    previous = "Ta nên tách relationship_event_builder.py và relationship_dynamics.py."
    for idx, text in enumerate(samples, start=1):
        event = engine.infer_event(
            user_text=text,
            ti_mode="chat_code",
            turn_index=idx,
            previous_assistant_text=previous,
            current_anchors=("relationship_dynamics.py", "relationship_event_builder.py"),
            previous_anchors=("relationship_dynamics.py", "relationship_event_builder.py"),
            recent_user_topics=("refactor relationship dynamics",),
            assistant_technical_success=0.55 if idx == 2 else 0.0,
        )
        report = engine.update(state, event)
        state = report.state
        pressure = RelationshipStylePressure.from_state(state)
        print("\nUSER:", text)
        print("SCORES:", report.event_scores)
        print("NOTES:", report.notes)
        print(report.prompt_context)
        print(pressure.to_prompt_context())
        previous = text
