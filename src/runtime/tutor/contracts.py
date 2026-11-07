# -*- coding: utf-8 -*-
"""Các contract bất biến cho Tutor Executive của Eris.

Mục tiêu của module này là giữ *ý nghĩa sư phạm* ở dạng dữ liệu có cấu trúc,
không để reasoning của model tràn trực tiếp vào runtime.

Nguyên tắc:
- Tutor có thể hình thành *giả thuyết* về người học, nhưng giả thuyết không phải sự thật.
- Mastery/evidence bền vững vẫn thuộc LearningService.
- Writer chỉ nhận TeachingMove đã được policy giới hạn; không nhận expected_answer.
- Không lưu chain-of-thought. Chỉ lưu summary ngắn đủ để audit/debug.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from runtime.learning_core.contracts import LearnerProfile, MasteryRecord
from runtime.learning_core.policy import TutorStep

_MAX_ID = 120
_MAX_SHORT = 240
_MAX_TEXT = 900
_MAX_USER_TEXT = 1800
_MAX_TURNS = 6
_MAX_HYPOTHESES = 8


def _text(value: Any, limit: int = _MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _id(value: Any) -> str:
    return _text(value, _MAX_ID)


def _ratio(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(1.0, number))


def _timestamp(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(number):
        return 0.0
    return max(0.0, min(10**12, number))


def _strings(value: Any, *, limit: int = 12, item_limit: int = _MAX_SHORT) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    return tuple(_text(item, item_limit) for item in value[:limit] if _text(item, item_limit))


@dataclass(frozen=True, slots=True)
class ContextTurn:
    """Một turn public đã được rút gọn trước khi đưa cho tutor reasoner."""

    role: str
    content: str

    def __post_init__(self) -> None:
        role = _id(self.role).lower()
        object.__setattr__(self, "role", role if role in {"user", "assistant"} else "user")
        object.__setattr__(self, "content", _text(self.content, 700))

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


HYPOTHESIS_KINDS = frozenset({
    "misconception",
    "missing_prerequisite",
    "fragile_knowledge",
    "procedural_gap",
    "transfer_gap",
    "confidence_mismatch",
    "unknown",
})

HYPOTHESIS_STATUSES = frozenset({"active", "resolved", "rejected"})


@dataclass(frozen=True, slots=True)
class LearnerHypothesis:
    """Giả thuyết mềm về nguyên nhân user đang vướng.

    Đây KHÔNG phải nhãn lâu dài về con người. Confidence thấp thì tutor phải hỏi
    để kiểm tra, không được biến phỏng đoán thành kết luận.
    """

    hypothesis_id: str
    kind: str
    kp_id: str
    summary: str
    confidence: float = 0.5
    evidence_refs: tuple[str, ...] = ()
    status: str = "active"
    first_seen_at: float = 0.0
    last_seen_at: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "hypothesis_id", _id(self.hypothesis_id))
        kind = _id(self.kind).lower()
        object.__setattr__(self, "kind", kind if kind in HYPOTHESIS_KINDS else "unknown")
        object.__setattr__(self, "kp_id", _id(self.kp_id))
        object.__setattr__(self, "summary", _text(self.summary, 500))
        object.__setattr__(self, "confidence", _ratio(self.confidence, 0.5))
        object.__setattr__(self, "evidence_refs", _strings(self.evidence_refs, limit=8, item_limit=_MAX_ID))
        status = _id(self.status).lower()
        object.__setattr__(self, "status", status if status in HYPOTHESIS_STATUSES else "active")
        object.__setattr__(self, "first_seen_at", _timestamp(self.first_seen_at))
        object.__setattr__(self, "last_seen_at", _timestamp(self.last_seen_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "kind": self.kind,
            "kp_id": self.kp_id,
            "summary": self.summary,
            "confidence": round(self.confidence, 4),
            "evidence_refs": list(self.evidence_refs),
            "status": self.status,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LearnerHypothesis":
        return cls(
            hypothesis_id=data.get("hypothesis_id", ""),
            kind=data.get("kind", "unknown"),
            kp_id=data.get("kp_id", ""),
            summary=data.get("summary", ""),
            confidence=data.get("confidence", 0.5),
            evidence_refs=tuple(data.get("evidence_refs", ())),
            status=data.get("status", "active"),
            first_seen_at=data.get("first_seen_at", 0.0),
            last_seen_at=data.get("last_seen_at", 0.0),
        )


PRIMARY_NEEDS = frozenset({
    "diagnose",
    "explain",
    "misconception",
    "prerequisite",
    "practice",
    "assess",
    "review",
    "advance",
    "complete",
    "wait",
})


@dataclass(frozen=True, slots=True)
class TutorDiagnosis:
    """Kết quả chẩn đoán sư phạm ngắn gọn, không chứa chain-of-thought."""

    primary_need: str
    summary: str
    hypotheses: tuple[LearnerHypothesis, ...] = ()
    understood_points: tuple[str, ...] = ()
    uncertain_points: tuple[str, ...] = ()
    recommended_strategy: str = ""
    confidence: float = 0.5

    def __post_init__(self) -> None:
        need = _id(self.primary_need).lower()
        object.__setattr__(self, "primary_need", need if need in PRIMARY_NEEDS else "diagnose")
        object.__setattr__(self, "summary", _text(self.summary, 650))
        object.__setattr__(self, "hypotheses", tuple(self.hypotheses[:_MAX_HYPOTHESES]))
        object.__setattr__(self, "understood_points", _strings(self.understood_points, limit=6))
        object.__setattr__(self, "uncertain_points", _strings(self.uncertain_points, limit=6))
        object.__setattr__(self, "recommended_strategy", _text(self.recommended_strategy, _MAX_SHORT))
        object.__setattr__(self, "confidence", _ratio(self.confidence, 0.5))

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_need": self.primary_need,
            "summary": self.summary,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "understood_points": list(self.understood_points),
            "uncertain_points": list(self.uncertain_points),
            "recommended_strategy": self.recommended_strategy,
            "confidence": round(self.confidence, 4),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TutorDiagnosis":
        hypotheses = tuple(
            LearnerHypothesis.from_mapping(item)
            for item in data.get("hypotheses", ())
            if isinstance(item, Mapping)
        )
        return cls(
            primary_need=data.get("primary_need", "diagnose"),
            summary=data.get("summary", ""),
            hypotheses=hypotheses,
            understood_points=tuple(data.get("understood_points", ())),
            uncertain_points=tuple(data.get("uncertain_points", ())),
            recommended_strategy=data.get("recommended_strategy", ""),
            confidence=data.get("confidence", 0.5),
        )


MOVE_KINDS = frozenset({
    "probe_question",
    "socratic_question",
    "explain",
    "counterexample",
    "analogy",
    "worked_example",
    "guided_practice",
    "assessment",
    "review",
    "repair",
    "transition",
    "complete",
    "wait",
})


@dataclass(frozen=True, slots=True)
class MoveEnvelope:
    """Khung quyền hạn deterministic mà model phải tuân theo trong turn này."""

    allowed_kinds: tuple[str, ...]
    requires_user_response: bool
    max_sentences: int
    interaction_type: str = "short_answer"
    reason: str = ""

    def __post_init__(self) -> None:
        clean = tuple(kind for kind in self.allowed_kinds if kind in MOVE_KINDS)
        object.__setattr__(self, "allowed_kinds", clean or ("wait",))
        object.__setattr__(self, "requires_user_response", bool(self.requires_user_response))
        try:
            count = int(self.max_sentences)
        except (TypeError, ValueError, OverflowError):
            count = 3
        object.__setattr__(self, "max_sentences", max(1, min(6, count)))
        object.__setattr__(self, "interaction_type", _id(self.interaction_type) or "short_answer")
        object.__setattr__(self, "reason", _text(self.reason, _MAX_SHORT))

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed_kinds": list(self.allowed_kinds),
            "requires_user_response": self.requires_user_response,
            "max_sentences": self.max_sentences,
            "interaction_type": self.interaction_type,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class TeachingMove:
    """Một hành động sư phạm duy nhất mà writer sẽ hiện thực hóa thành câu chữ."""

    move_id: str
    kind: str
    kp_id: str
    objective: str
    instruction_vi: str
    question_goal: str = ""
    expected_response_shape: str = ""
    interaction_type: str = "short_answer"
    requires_user_response: bool = False
    max_sentences: int = 3
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "move_id", _id(self.move_id))
        kind = _id(self.kind).lower()
        object.__setattr__(self, "kind", kind if kind in MOVE_KINDS else "wait")
        object.__setattr__(self, "kp_id", _id(self.kp_id))
        object.__setattr__(self, "objective", _text(self.objective, 500))
        object.__setattr__(self, "instruction_vi", _text(self.instruction_vi, 700))
        object.__setattr__(self, "question_goal", _text(self.question_goal, 500))
        object.__setattr__(self, "expected_response_shape", _text(self.expected_response_shape, 400))
        object.__setattr__(self, "interaction_type", _id(self.interaction_type) or "short_answer")
        object.__setattr__(self, "requires_user_response", bool(self.requires_user_response))
        try:
            count = int(self.max_sentences)
        except (TypeError, ValueError, OverflowError):
            count = 3
        object.__setattr__(self, "max_sentences", max(1, min(6, count)))
        object.__setattr__(self, "reason", _text(self.reason, _MAX_SHORT))

    def to_writer_dict(self) -> dict[str, Any]:
        """Projection an toàn cho writer; cố ý không có expected_answer."""
        return {
            "move_id": self.move_id,
            "kind": self.kind,
            "kp_id": self.kp_id,
            "objective": self.objective,
            "instruction_vi": self.instruction_vi,
            "question_goal": self.question_goal,
            "expected_response_shape": self.expected_response_shape,
            "interaction_type": self.interaction_type,
            "requires_user_response": self.requires_user_response,
            "max_sentences": self.max_sentences,
            "reason": self.reason,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TeachingMove":
        return cls(
            move_id=data.get("move_id", ""),
            kind=data.get("kind", "wait"),
            kp_id=data.get("kp_id", ""),
            objective=data.get("objective", ""),
            instruction_vi=data.get("instruction_vi", ""),
            question_goal=data.get("question_goal", ""),
            expected_response_shape=data.get("expected_response_shape", ""),
            interaction_type=data.get("interaction_type", "short_answer"),
            requires_user_response=data.get("requires_user_response", False),
            max_sentences=data.get("max_sentences", 3),
            reason=data.get("reason", ""),
        )


ASSESSMENT_VERDICTS = frozenset({"pass", "partial", "fail", "uncertain"})


@dataclass(frozen=True, slots=True)
class SemanticAssessment:
    """Đánh giá semantic cho câu trả lời tự do của user.

    Contract chỉ mô tả kết quả. TutorExecutive mới quyết định kết quả này có đủ
    tin cậy để biến thành LearningEvidence hay không.
    """

    kp_id: str
    verdict: str
    confidence: float
    evidence_type: str
    strength: float
    summary: str = ""
    error_code: str = ""
    misconception: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "kp_id", _id(self.kp_id))
        verdict = _id(self.verdict).lower()
        object.__setattr__(self, "verdict", verdict if verdict in ASSESSMENT_VERDICTS else "uncertain")
        object.__setattr__(self, "confidence", _ratio(self.confidence, 0.0))
        object.__setattr__(self, "evidence_type", _id(self.evidence_type))
        object.__setattr__(self, "strength", _ratio(self.strength, 0.5))
        object.__setattr__(self, "summary", _text(self.summary, 600))
        object.__setattr__(self, "error_code", _id(self.error_code))
        object.__setattr__(self, "misconception", _text(self.misconception, 500))

    @property
    def correct(self) -> bool:
        return self.verdict in {"pass", "partial"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "kp_id": self.kp_id,
            "verdict": self.verdict,
            "confidence": round(self.confidence, 4),
            "evidence_type": self.evidence_type,
            "strength": round(self.strength, 4),
            "summary": self.summary,
            "error_code": self.error_code,
            "misconception": self.misconception,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SemanticAssessment":
        return cls(
            kp_id=data.get("kp_id", ""),
            verdict=data.get("verdict", "uncertain"),
            confidence=data.get("confidence", 0.0),
            evidence_type=data.get("evidence_type", ""),
            strength=data.get("strength", 0.5),
            summary=data.get("summary", ""),
            error_code=data.get("error_code", ""),
            misconception=data.get("misconception", ""),
        )


@dataclass(frozen=True, slots=True)
class TutorState:
    """State session-scoped của tutor; không phải learner truth lâu dài."""

    session_id: str = ""
    path_id: str = ""
    kp_id: str = ""
    session_goal: str = ""
    hypotheses: tuple[LearnerHypothesis, ...] = ()
    last_move: TeachingMove | None = None
    # Exact public wording đã thực sự được delivery ở move gần nhất. Assessor dùng
    # field này để chấm đúng câu user đã nghe, không chỉ chấm theo intent trước writer.
    last_delivery_text: str = ""
    turn_count: int = 0
    consecutive_stalls: int = 0
    started_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _id(self.session_id))
        object.__setattr__(self, "path_id", _id(self.path_id))
        object.__setattr__(self, "kp_id", _id(self.kp_id))
        object.__setattr__(self, "session_goal", _text(self.session_goal, 600))
        object.__setattr__(self, "hypotheses", tuple(self.hypotheses[:_MAX_HYPOTHESES]))
        object.__setattr__(self, "last_delivery_text", _text(self.last_delivery_text, 1600))
        try:
            turns = int(self.turn_count)
        except (TypeError, ValueError, OverflowError):
            turns = 0
        try:
            stalls = int(self.consecutive_stalls)
        except (TypeError, ValueError, OverflowError):
            stalls = 0
        object.__setattr__(self, "turn_count", max(0, min(100000, turns)))
        object.__setattr__(self, "consecutive_stalls", max(0, min(1000, stalls)))
        object.__setattr__(self, "started_at", _timestamp(self.started_at))
        object.__setattr__(self, "updated_at", _timestamp(self.updated_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "path_id": self.path_id,
            "kp_id": self.kp_id,
            "session_goal": self.session_goal,
            "hypotheses": [item.to_dict() for item in self.hypotheses],
            "last_move": self.last_move.to_writer_dict() if self.last_move else None,
            "last_delivery_text": self.last_delivery_text,
            "turn_count": self.turn_count,
            "consecutive_stalls": self.consecutive_stalls,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "TutorState":
        """Khôi phục *session cognition* sau restart mà không lưu chain-of-thought.

        Chỉ những kết luận audit được mới được hydrate: hypothesis, teaching move
        gần nhất và exact public wording đã phát. Mastery/evidence vẫn thuộc
        LearningService/SQLite và không bao giờ được suy ra từ snapshot này.
        """
        raw = data if isinstance(data, Mapping) else {}
        hypotheses = tuple(
            LearnerHypothesis.from_mapping(item)
            for item in raw.get("hypotheses", ())
            if isinstance(item, Mapping)
        )
        move_raw = raw.get("last_move")
        last_move = TeachingMove.from_mapping(move_raw) if isinstance(move_raw, Mapping) else None
        return cls(
            session_id=raw.get("session_id", ""),
            path_id=raw.get("path_id", ""),
            kp_id=raw.get("kp_id", ""),
            session_goal=raw.get("session_goal", ""),
            hypotheses=hypotheses,
            last_move=last_move,
            last_delivery_text=raw.get("last_delivery_text", ""),
            turn_count=raw.get("turn_count", 0),
            consecutive_stalls=raw.get("consecutive_stalls", 0),
            started_at=raw.get("started_at", 0.0),
            updated_at=raw.get("updated_at", 0.0),
        )


@dataclass(frozen=True, slots=True)
class TutorObservation:
    """Ảnh chụp bounded mà reasoner được phép nhìn trong một tutor cycle."""

    turn_id: str
    user_text: str
    path_id: str
    path_goal: str
    kp_id: str
    kp_title: str
    kp_description: str
    kp_type: str
    policy_step: TutorStep
    mastery: MasteryRecord
    learner: LearnerProfile
    recent_history: tuple[ContextTurn, ...] = ()
    signals: tuple[str, ...] = ()
    now: float = 0.0
    source_context: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "turn_id", _id(self.turn_id))
        object.__setattr__(self, "user_text", _text(self.user_text, _MAX_USER_TEXT))
        object.__setattr__(self, "path_id", _id(self.path_id))
        object.__setattr__(self, "path_goal", _text(self.path_goal, 700))
        object.__setattr__(self, "kp_id", _id(self.kp_id))
        object.__setattr__(self, "kp_title", _text(self.kp_title, _MAX_SHORT))
        object.__setattr__(self, "kp_description", _text(self.kp_description, 700))
        object.__setattr__(self, "kp_type", _id(self.kp_type))
        object.__setattr__(self, "recent_history", tuple(self.recent_history[-_MAX_TURNS:]))
        object.__setattr__(self, "signals", _strings(self.signals, limit=16, item_limit=_MAX_ID))
        object.__setattr__(self, "now", _timestamp(self.now))

    def to_reasoner_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "user_text": self.user_text,
            "path": {"path_id": self.path_id, "goal": self.path_goal},
            "knowledge_point": {
                "kp_id": self.kp_id,
                "title": self.kp_title,
                "description": self.kp_description,
                "kp_type": self.kp_type,
            },
            "policy_step": self.policy_step.to_dict(),
            "sources": [dict(item) for item in self.source_context[:3]],
            "mastery": self.mastery.to_dict(),
            "learner": self.learner.to_dict(),
            "recent_history": [turn.to_dict() for turn in self.recent_history],
            "signals": list(self.signals),
            "now": self.now,
        }


@dataclass(frozen=True, slots=True)
class TutorDecision:
    """Kết quả cuối của TutorExecutive cho một turn."""

    policy_step: TutorStep
    diagnosis: TutorDiagnosis
    envelope: MoveEnvelope
    move: TeachingMove
    state: TutorState

    def to_writer_context(self) -> dict[str, Any]:
        """Chỉ projection cần cho writer, không đẩy learner hypothesis thô ra public."""
        return {
            "schema": "eris.tutor.directive.v1",
            "policy_step": self.policy_step.to_dict(),
            "move": self.move.to_writer_dict(),
            "delivery_constraints": {
                "max_sentences": self.move.max_sentences,
                "requires_user_response": self.move.requires_user_response,
            },
        }

    def to_debug_dict(self) -> dict[str, Any]:
        return {
            "policy_step": self.policy_step.to_dict(),
            "diagnosis": self.diagnosis.to_dict(),
            "envelope": self.envelope.to_dict(),
            "move": self.move.to_writer_dict(),
            "state": self.state.to_dict(),
        }


__all__ = [
    "ASSESSMENT_VERDICTS",
    "ContextTurn",
    "HYPOTHESIS_KINDS",
    "LearnerHypothesis",
    "MOVE_KINDS",
    "MoveEnvelope",
    "SemanticAssessment",
    "TeachingMove",
    "TutorDecision",
    "TutorDiagnosis",
    "TutorObservation",
    "TutorState",
]
