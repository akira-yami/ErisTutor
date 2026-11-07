# -*- coding: utf-8 -*-
"""
relationship_event_builder.py
=============================

Tầng này chuyển một lượt hội thoại thành các score quan hệ có bằng chứng.

Lý do tồn tại
-------------
Trước đây RelationshipDynamics có khuynh hướng suy trực tiếp:
    "ổn" / "chuẩn"  -> user_accepted_style
    "eris" / "project" -> repeated_project_context

Cách đó nhanh, nhưng bẩn. Nó biến relationship memory thành nơi keyword có thể
đi thẳng vào trust/ritual/familiarity. Một chữ "ổn chưa?" mà tăng trust thì
khác gì ký hợp đồng hôn nhân vì thấy nút Like. Rất nhân loại, rất nên tránh.

File này làm việc khác:
- gom nhiều nguồn yếu thành score có confidence;
- yêu cầu nhiều nhóm evidence trước khi bật repeated_project_context;
- phân biệt accepted_answer, accepted_style, accepted_action;
- chỉ dùng marker bề mặt như tín hiệu yếu, không cho nó tự quyết định state.

Ranh giới trách nhiệm
---------------------
- Builder được phép: suy event score + evidence.
- Builder không được phép: mutate relationship state.
- RelationshipDynamicsEngine mới là nơi update state dài hạn.

Cách nối tối thiểu
------------------
    builder = RelationshipEventBuilder()
    scores = builder.build(
        user_text=user_text,
        affect=affect,
        previous_assistant_text=last_assistant_text,
        goal_state=goal_state,
        conversation_state=conversation_state,
        recent_user_topics=ledger.gist_recent_user_topics(),
        current_anchors=("relationship_dynamics.py", "micro_policy.py"),
        assistant_technical_success=0.7,
    )

    event = RelationshipTurnEvent.from_affect(..., relationship_scores=scores)

Thiết kế này cho ERIS "tư duy tốt lẫn xấu":
- tốt: có bằng chứng, biết mơ hồ, không phán bừa;
- xấu: vẫn cho phép residue/ritual/attachment phát triển lệch nhẹ theo lịch sử,
  nhưng lệch bằng gradient và evidence, không phải keyword trigger rẻ tiền.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

try:  # pragma: no cover - package import
    from .relational_feedback import RelationalFeedbackSignal
except Exception:  # pragma: no cover - standalone import
    try:
        from relational_feedback import RelationalFeedbackSignal
    except Exception:
        @dataclass(frozen=True, slots=True)
        class RelationalFeedbackSignal:  # type: ignore[no-redef]
            detected: bool = False
            kind: str = "unknown"
            intensity: float = 0.0
            repair_needed: float = 0.0
            warmth_request: float = 0.0
            defensive_risk: float = 0.0
            evidence: tuple[str, ...] = ()

            @classmethod
            def empty(cls) -> "RelationalFeedbackSignal":
                return cls()


# ---------------------------------------------------------------------------
# Helper chung
# ---------------------------------------------------------------------------


def clamp01(value: Any, default: float = 0.0) -> float:
    """Ép một giá trị về [0, 1].

    File này có thể được import độc lập, nên không phụ thuộc cứng vào
    affect_semantics.clamp01. Một helper nhỏ ở đây tránh lỗi import dây chuyền.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric != numeric:  # NaN guard.
        return default
    return max(0.0, min(1.0, numeric))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    if numeric != numeric:
        return default
    return numeric


def normalize_vi(text: str) -> str:
    """Normalize tiếng Việt để so khớp nhẹ.

    Dùng cho scoring mềm, không dùng như chân lý. Normalize bỏ dấu rất tiện,
    và cũng rất nguy hiểm nếu đem làm não chính. Vì vậy mọi kết quả từ text marker
    trong file này đều bị xem là surface evidence, không phải evidence cấp cao.
    """
    lowered = str(text or "").strip().lower()
    try:
        fixed = lowered.encode("latin1").decode("utf-8")
        if fixed:
            lowered = fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    lowered = unicodedata.normalize("NFD", lowered)
    lowered = "".join(ch for ch in lowered if unicodedata.category(ch) != "Mn")
    lowered = lowered.replace("đ", "d").replace("Đ", "d")
    lowered = re.sub(r"[^a-z0-9_./#:+\-\s]", " ", lowered)
    return " ".join(lowered.split())


def token_set(text: str) -> set[str]:
    """Token vừa đủ cho overlap.

    Bỏ filler để "ổn chưa" không tự biến thành semantic continuity. Một ngày nào
    đó loài người sẽ ngừng bắt máy đoán ý bằng hai chữ, nhưng chưa phải hôm nay.
    """
    normalized = normalize_vi(text)
    filler = {
        "toi", "ban", "ban", "em", "anh", "chi", "la", "co", "cua", "va",
        "thi", "ma", "nhung", "roi", "chua", "nay", "kia", "do", "di", "cho",
        "mot", "cai", "nua", "len", "xuong", "vao", "ra", "ve", "voi", "hon",
        "nhe", "di", "a", "ha", "khong", "ko", "k", "qua", "dang", "van",
    }
    return {tok for tok in normalized.split() if len(tok) >= 3 and tok not in filler}


def compact(items: Iterable[str], limit: int = 12) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = str(item or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= limit:
            break
    return tuple(out)


def overlap_score(a: Iterable[str], b: Iterable[str]) -> float:
    """Jaccard mềm cho anchor/topic overlap."""
    left = {str(x).strip().lower() for x in a if str(x).strip()}
    right = {str(x).strip().lower() for x in b if str(x).strip()}
    if not left or not right:
        return 0.0
    inter = len(left & right)
    if inter <= 0:
        return 0.0
    union = max(1, len(left | right))
    # Cộng nhẹ theo số intersection để anchor cụ thể có tác dụng hơn token chung.
    return clamp01((inter / union) * 0.75 + min(0.25, inter * 0.08))


def get_attr_or_key(obj: Any, name: str, default: Any = None) -> Any:
    """Đọc field từ dataclass/object/dict an toàn."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ---------------------------------------------------------------------------
# Contracts
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RelationshipEvidence:
    """Một mảnh bằng chứng dùng để build relationship event.

    label:
        Nhãn ngắn, ví dụ "goal_continuity", "same_anchor", "positive_ack".
    confidence:
        Độ tin riêng của evidence này.
    source:
        Nguồn: goal_state, session_ledger, conversation_dynamics, surface_text,
        outcome_observer, explicit_override...
    polarity:
        +1 hỗ trợ, -1 chống lại, 0 trung tính.
    evidence_ids:
        Nếu upstream có id evidence/frame/turn thì nhét vào đây để debug.
    notes:
        Ghi chú ngắn, không nhét prose dài vào state.
    """

    label: str
    confidence: float
    source: str
    polarity: float = 0.0
    evidence_ids: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def normalized(self) -> "RelationshipEvidence":
        return RelationshipEvidence(
            label=str(self.label or "").strip(),
            confidence=clamp01(self.confidence),
            source=str(self.source or "unknown").strip() or "unknown",
            polarity=max(-1.0, min(1.0, safe_float(self.polarity))),
            evidence_ids=compact(self.evidence_ids, limit=8),
            notes=compact(self.notes, limit=8),
        )


@dataclass(frozen=True, slots=True)
class RelationshipEventScores:
    """Score quan hệ cho một lượt.

    Các score này là gradient. RelationshipDynamicsEngine nên nhân delta theo
    score, không dùng bool như công tắc đèn. Mô phỏng quan hệ bằng bool là cách
    biến ERIS thành cái máy bán nước tự động có nhật ký.
    """

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
        """Acceptance tổng hợp nhưng vẫn giữ ba nhánh riêng để update tinh hơn."""
        return clamp01(max(self.accepted_answer, self.accepted_style, self.accepted_action))

    def normalized(self) -> "RelationshipEventScores":
        return RelationshipEventScores(
            repeated_project_context=clamp01(self.repeated_project_context),
            accepted_answer=clamp01(self.accepted_answer),
            accepted_style=clamp01(self.accepted_style),
            accepted_action=clamp01(self.accepted_action),
            correction=clamp01(self.correction),
            objectification=clamp01(self.objectification),
            technical_success=clamp01(self.technical_success),
            perceived_coldness=clamp01(self.perceived_coldness),
            tone_feedback=clamp01(self.tone_feedback),
            relational_repair_requested=clamp01(self.relational_repair_requested),
            user_requests_less_distance=clamp01(self.user_requests_less_distance),
            defensive_risk=clamp01(self.defensive_risk),
            confidence=clamp01(self.confidence),
            evidence=tuple(item.normalized() for item in self.evidence if item.label),
            debug={str(k): round(clamp01(v), 4) for k, v in dict(self.debug or {}).items()},
        )


@dataclass(frozen=True, slots=True)
class RecentTurn:
    """Turn gọn để builder đọc session/public ledger mà không phụ thuộc class thật."""

    role: str = ""
    text: str = ""
    topic: str = ""
    timestamp: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RelationshipEventBuildContext:
    """Context đầu vào giàu hơn cho builder.

    Dùng dataclass này nếu caller muốn gọi rõ ràng. Builder.build() vẫn nhận
    keyword args để dễ nối vào coordinator hiện tại.
    """

    user_text: str = ""
    affect: Any = None
    previous_assistant_text: str = ""
    previous_assistant_success: float = 0.0
    assistant_technical_success: float = 0.0
    goal_state: Any = None
    conversation_state: Any = None
    recent_user_topics: tuple[str, ...] = ()
    recent_turns: tuple[RecentTurn, ...] = ()
    current_anchors: tuple[str, ...] = ()
    previous_anchors: tuple[str, ...] = ()
    explicit_repeated_project_context: float | None = None
    explicit_accepted_style: float | None = None
    explicit_accepted_answer: float | None = None
    explicit_accepted_action: float | None = None
    relational_feedback: RelationalFeedbackSignal = field(default_factory=RelationalFeedbackSignal.empty)
    evidence_ids: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class RelationshipEventBuilder:
    """Build RelationshipEventScores từ nhiều nguồn có confidence.

    Điểm quan trọng:
    - Một marker bề mặt không được tự bật relationship event mạnh.
    - repeated_project_context cần ít nhất hai nhóm evidence, trừ khi caller
      truyền explicit score từ module đáng tin.
    - acceptance bị trừ mạnh nếu có correction/contrast/question-about-quality.
    """

    # Marker ở đây chỉ là surface cue yếu. Không có marker nào tự update state.
    _POSITIVE_ACK = (
        "dung roi", "chuan", "hop ly", "on do", "ngon", "hay do", "duoc do",
        "ok do", "tot", "chinh xac", "co ly", "nghe on", "hieu roi",
    )
    _STYLE_TARGET = (
        "giong", "style", "cach noi", "cach tra loi", "tone", "vibe",
        "bot hon", "do chatbot", "tu nhien hon", "song dong", "khong fake",
    )
    _ACTION_CONTINUE = (
        "lam tiep", "tiep tuc", "gui file", "tao file", "sua tiep", "noi vao",
        "viet tiep", "fix tiep", "them test", "gui toi", "tao het", "lam het",
        "trien khai", "patch", "refactor", "tach file",
    )
    _CONTRAST_OR_CORRECTION = (
        "nhung", "ma ", "tuy nhien", "con", "thieu", "sai", "khong phai",
        "ko phai", "chua", "fallback te", "te", "qua te", "chua dung",
        "chua on", "khong on", "loi", "bug", "fail", "khong dung", "dcmmmm",
    )
    _QUALITY_QUESTION = (
        "on chua", "chuan khong", "dung khong", "duoc chua", "tot chua",
        "hop ly khong", "co on khong", "the nao",
    )
    _TECHNICAL_FOLLOWUP = (
        "sua", "fix", "tao", "file", ".py", "test", "runtime", "engine",
        "policy", "router", "class", "dataclass", "module", "contract", "patch",
        "refactor", "fallback", "keyword", "semantic", "confidence", "evidence",
    )

    def build(self, **kwargs: Any) -> RelationshipEventScores:
        """Build scores bằng keyword args để caller dễ nối dần."""
        ctx = self._context_from_kwargs(kwargs)
        evidence: list[RelationshipEvidence] = []

        repeated, repeated_debug, repeated_evidence = self._score_repeated_project_context(ctx)
        evidence.extend(repeated_evidence)

        acceptance, acceptance_debug, acceptance_evidence = self._score_acceptance(ctx)
        evidence.extend(acceptance_evidence)

        correction = clamp01(get_attr_or_key(ctx.affect, "correction", 0.0))
        objectification = clamp01(get_attr_or_key(ctx.affect, "objectification", 0.0))
        technical_success = clamp01(max(ctx.assistant_technical_success, ctx.previous_assistant_success))
        relational_feedback = ctx.relational_feedback or RelationalFeedbackSignal.empty()
        perceived_coldness = 0.0
        tone_feedback = 0.0
        relational_repair_requested = 0.0
        user_requests_less_distance = 0.0
        defensive_risk = 0.0
        if bool(getattr(relational_feedback, "detected", False)):
            perceived_coldness = clamp01(getattr(relational_feedback, "intensity", 0.0))
            tone_feedback = perceived_coldness
            relational_repair_requested = clamp01(getattr(relational_feedback, "repair_needed", 0.0))
            user_requests_less_distance = clamp01(getattr(relational_feedback, "warmth_request", 0.0))
            defensive_risk = clamp01(getattr(relational_feedback, "defensive_risk", 0.0))
            evidence.append(RelationshipEvidence(
                label=f"relational_feedback:{getattr(relational_feedback, 'kind', 'unknown')}",
                confidence=max(perceived_coldness, relational_repair_requested),
                source="relational_feedback",
                polarity=-0.35,
                evidence_ids=ctx.evidence_ids,
                notes=tuple(str(item) for item in (getattr(relational_feedback, "evidence", ()) or ()) if str(item or "").strip())[:6],
            ))

        if correction > 0.0:
            evidence.append(RelationshipEvidence(
                label="affect_correction",
                confidence=correction,
                source="affect_semantics",
                polarity=-1.0,
                evidence_ids=ctx.evidence_ids,
            ))
        if objectification > 0.0:
            evidence.append(RelationshipEvidence(
                label="affect_objectification",
                confidence=objectification,
                source="affect_semantics",
                polarity=-1.0,
                evidence_ids=ctx.evidence_ids,
            ))
        if technical_success > 0.0:
            evidence.append(RelationshipEvidence(
                label="assistant_technical_success",
                confidence=technical_success,
                source="outcome_observer",
                polarity=1.0,
                evidence_ids=ctx.evidence_ids,
            ))

        # Confidence tổng: trung bình có trọng số của các score có evidence.
        score_values = [
            repeated,
            acceptance.accepted_answer,
            acceptance.accepted_style,
            acceptance.accepted_action,
            correction,
            objectification,
            technical_success,
            perceived_coldness,
            relational_repair_requested,
        ]
        nonzero = [value for value in score_values if value > 0.001]
        confidence = clamp01(sum(nonzero) / len(nonzero)) if nonzero else 0.0

        debug = {
            **{f"repeated_{k}": v for k, v in repeated_debug.items()},
            **{f"accept_{k}": v for k, v in acceptance_debug.items()},
        }

        return RelationshipEventScores(
            repeated_project_context=repeated,
            accepted_answer=acceptance.accepted_answer,
            accepted_style=acceptance.accepted_style,
            accepted_action=acceptance.accepted_action,
            correction=correction,
            objectification=objectification,
            technical_success=technical_success,
            perceived_coldness=perceived_coldness,
            tone_feedback=tone_feedback,
            relational_repair_requested=relational_repair_requested,
            user_requests_less_distance=user_requests_less_distance,
            defensive_risk=defensive_risk,
            confidence=confidence,
            evidence=tuple(item.normalized() for item in evidence),
            debug=debug,
        ).normalized()

    def build_from_context(self, ctx: RelationshipEventBuildContext) -> RelationshipEventScores:
        """Biến thể typed cho caller thích rõ contract."""
        return self.build(**ctx.__dict__)

    # ------------------------------------------------------------------
    # Context normalization
    # ------------------------------------------------------------------

    def _context_from_kwargs(self, data: Mapping[str, Any]) -> RelationshipEventBuildContext:
        recent_turns_raw = data.get("recent_turns", ()) or ()
        recent_turns: list[RecentTurn] = []
        for item in recent_turns_raw:
            if isinstance(item, RecentTurn):
                recent_turns.append(item)
            elif isinstance(item, Mapping):
                recent_turns.append(RecentTurn(
                    role=str(item.get("role", "") or ""),
                    text=str(item.get("text", "") or ""),
                    topic=str(item.get("topic", "") or ""),
                    timestamp=safe_float(item.get("timestamp", 0.0)),
                    metadata=item.get("metadata", {}) if isinstance(item.get("metadata", {}), Mapping) else {},
                ))
            else:
                recent_turns.append(RecentTurn(
                    role=str(getattr(item, "role", "") or ""),
                    text=str(getattr(item, "text", "") or ""),
                    topic=str(getattr(item, "topic", "") or ""),
                    timestamp=safe_float(getattr(item, "timestamp", 0.0)),
                    metadata=getattr(item, "metadata", {}) if isinstance(getattr(item, "metadata", {}), Mapping) else {},
                ))

        def tuple_str(name: str) -> tuple[str, ...]:
            value = data.get(name, ()) or ()
            if isinstance(value, str):
                return (value,) if value.strip() else ()
            return compact(str(x) for x in value)

        return RelationshipEventBuildContext(
            user_text=str(data.get("user_text", "") or ""),
            affect=data.get("affect"),
            previous_assistant_text=str(data.get("previous_assistant_text", "") or ""),
            previous_assistant_success=clamp01(data.get("previous_assistant_success", 0.0)),
            assistant_technical_success=clamp01(data.get("assistant_technical_success", 0.0)),
            goal_state=data.get("goal_state"),
            conversation_state=data.get("conversation_state"),
            recent_user_topics=tuple_str("recent_user_topics"),
            recent_turns=tuple(recent_turns),
            current_anchors=tuple_str("current_anchors"),
            previous_anchors=tuple_str("previous_anchors"),
            explicit_repeated_project_context=self._optional_score(data.get("explicit_repeated_project_context")),
            explicit_accepted_style=self._optional_score(data.get("explicit_accepted_style")),
            explicit_accepted_answer=self._optional_score(data.get("explicit_accepted_answer")),
            explicit_accepted_action=self._optional_score(data.get("explicit_accepted_action")),
            relational_feedback=self._coerce_relational_feedback(data.get("relational_feedback")),
            evidence_ids=tuple_str("evidence_ids"),
        )

    def _coerce_relational_feedback(self, value: Any) -> RelationalFeedbackSignal:
        if value is None:
            return RelationalFeedbackSignal.empty()
        if isinstance(value, RelationalFeedbackSignal):
            return value
        if isinstance(value, Mapping):
            from_dict = getattr(RelationalFeedbackSignal, "from_dict", None)
            if callable(from_dict):
                try:
                    return from_dict(value)
                except Exception:
                    pass
            return RelationalFeedbackSignal(
                detected=bool(value.get("detected", False)),
                kind=str(value.get("kind", "unknown") or "unknown"),  # type: ignore[arg-type]
                intensity=clamp01(value.get("intensity", 0.0)),
                repair_needed=clamp01(value.get("repair_needed", 0.0)),
                warmth_request=clamp01(value.get("warmth_request", 0.0)),
                defensive_risk=clamp01(value.get("defensive_risk", 0.0)),
                evidence=tuple(str(item) for item in (value.get("evidence", ()) or ()) if str(item or "").strip()),
            )
        return RelationalFeedbackSignal.empty()

    def _optional_score(self, value: Any) -> float | None:
        if value is None:
            return None
        return clamp01(value)

    # ------------------------------------------------------------------
    # repeated_project_context
    # ------------------------------------------------------------------

    def _score_repeated_project_context(
        self,
        ctx: RelationshipEventBuildContext,
    ) -> tuple[float, dict[str, float], tuple[RelationshipEvidence, ...]]:
        if ctx.explicit_repeated_project_context is not None:
            score = clamp01(ctx.explicit_repeated_project_context)
            return score, {"explicit": score}, (RelationshipEvidence(
                label="explicit_repeated_project_context",
                confidence=score,
                source="explicit_override",
                polarity=1.0,
                evidence_ids=ctx.evidence_ids,
            ),)

        evidence: list[RelationshipEvidence] = []
        user_tokens = token_set(ctx.user_text)
        current_anchors = self._extract_anchors(ctx.user_text) | {normalize_vi(x) for x in ctx.current_anchors}
        previous_anchors = {normalize_vi(x) for x in ctx.previous_anchors}
        for turn in ctx.recent_turns:
            previous_anchors |= self._extract_anchors(turn.text)

        goal_score = self._goal_continuity_score(ctx, user_tokens, current_anchors)
        if goal_score >= 0.08:
            evidence.append(RelationshipEvidence(
                label="goal_continuity",
                confidence=goal_score,
                source="goal_state",
                polarity=1.0,
                evidence_ids=ctx.evidence_ids,
            ))

        anchor_score = overlap_score(current_anchors, previous_anchors)
        if anchor_score >= 0.08:
            evidence.append(RelationshipEvidence(
                label="anchor_overlap",
                confidence=anchor_score,
                source="session_anchors",
                polarity=1.0,
                evidence_ids=ctx.evidence_ids,
                notes=compact(current_anchors & previous_anchors, limit=5),
            ))

        topic_score = self._recent_topic_score(ctx, user_tokens)
        if topic_score >= 0.08:
            evidence.append(RelationshipEvidence(
                label="recent_topic_continuity",
                confidence=topic_score,
                source="session_ledger",
                polarity=1.0,
                evidence_ids=ctx.evidence_ids,
            ))

        technical_followup = self._technical_followup_score(ctx)
        if technical_followup >= 0.08:
            evidence.append(RelationshipEvidence(
                label="technical_followup",
                confidence=technical_followup,
                source="affect_and_surface_shape",
                polarity=1.0,
                evidence_ids=ctx.evidence_ids,
            ))

        raw = clamp01(
            goal_score * 0.35
            + anchor_score * 0.25
            + topic_score * 0.20
            + technical_followup * 0.15
            + clamp01(get_attr_or_key(ctx.affect, "technicality", 0.0)) * 0.05
        )

        # Elegant bit: không cho một họ evidence duy nhất bật context lặp.
        # Nếu chỉ có technicality/surface marker thì cap thấp. Nếu có goal + anchor/topic
        # thì score mới được lên cao.
        family_count = sum(1 for value in (goal_score, anchor_score, topic_score, technical_followup) if value >= 0.20)
        if family_count < 2:
            raw = min(raw, 0.55)
        if goal_score >= 0.65 and (anchor_score >= 0.25 or topic_score >= 0.25):
            raw = max(raw, 0.68)

        debug = {
            "goal": goal_score,
            "anchor": anchor_score,
            "topic": topic_score,
            "technical_followup": technical_followup,
            "family_count": min(1.0, family_count / 4.0),
            "raw": raw,
        }
        return raw, debug, tuple(evidence)

    def _goal_continuity_score(
        self,
        ctx: RelationshipEventBuildContext,
        user_tokens: set[str],
        current_anchors: set[str],
    ) -> float:
        goal = ctx.goal_state
        if goal is None:
            return 0.0

        status = str(get_attr_or_key(goal, "status", "") or "").lower()
        if status and status not in {"active", "open", "ongoing", "current"}:
            return 0.0

        summary = str(get_attr_or_key(goal, "summary", "") or "")
        goal_tokens = token_set(summary)
        anchors_raw = get_attr_or_key(goal, "anchors", ()) or ()
        if isinstance(anchors_raw, str):
            goal_anchors = {normalize_vi(anchors_raw)}
        else:
            goal_anchors = {normalize_vi(x) for x in anchors_raw if str(x).strip()}

        token_overlap = overlap_score(user_tokens, goal_tokens)
        anchor_overlap = overlap_score(current_anchors, goal_anchors)
        strength = clamp01(get_attr_or_key(goal, "strength", 0.0))
        confidence = clamp01(get_attr_or_key(goal, "confidence", 0.0))
        coherence = clamp01(get_attr_or_key(goal, "coherence", get_attr_or_key(goal, "orientation_coherence", 0.0)))
        can_intervene = bool(get_attr_or_key(goal, "can_intervene", False))
        permission = clamp01(get_attr_or_key(goal, "intervention_permission", 0.0))

        base = token_overlap * 0.32 + anchor_overlap * 0.34 + strength * 0.14 + confidence * 0.12 + coherence * 0.08
        if can_intervene or permission >= 0.55:
            base += 0.08
        return clamp01(base)

    def _recent_topic_score(self, ctx: RelationshipEventBuildContext, user_tokens: set[str]) -> float:
        topic_tokens: set[str] = set()
        for topic in ctx.recent_user_topics:
            topic_tokens |= token_set(topic)
        for turn in ctx.recent_turns[-6:]:
            if turn.role.lower() == "user":
                topic_tokens |= token_set(turn.topic or turn.text)
        return overlap_score(user_tokens, topic_tokens)

    def _technical_followup_score(self, ctx: RelationshipEventBuildContext) -> float:
        normalized = normalize_vi(ctx.user_text)
        affect_technicality = clamp01(get_attr_or_key(ctx.affect, "technicality", 0.0))
        marker_hits = sum(1 for marker in self._TECHNICAL_FOLLOWUP if marker in normalized)
        marker_score = min(0.55, marker_hits * 0.12)

        # Nếu câu ngắn kiểu "lên hướng tối ưu elegant" vẫn là follow-up kỹ thuật
        # khi recent turns/previous assistant có nội dung kỹ thuật.
        previous_tokens = token_set(ctx.previous_assistant_text)
        current_tokens = token_set(ctx.user_text)
        previous_technical = bool(previous_tokens & {"file", "class", "engine", "policy", "runtime", "semantic", "test", "router", "context"})
        followup_shape = any(word in normalized for word in ("huong", "toi uu", "elegant", "sua", "tao", "tach", "lam"))
        shape_score = 0.18 if previous_technical and followup_shape else 0.0
        return clamp01(affect_technicality * 0.45 + marker_score + shape_score)

    def _extract_anchors(self, text: str) -> set[str]:
        """Rút anchor cụ thể: file/module/class/function.

        Không dùng "eris/project" làm anchor chính vì quá rộng. Anchor tốt là thứ
        có định danh kỹ thuật cụ thể: micro_policy.py, RelationshipDynamicsEngine,
        MeaningLayer, source_grounding...
        """
        raw = str(text or "")
        anchors: set[str] = set()
        patterns = (
            r"\b[a-zA-Z_][\w_\-]*\.(?:py|cs|gd|json|txt|md|xml|yml|yaml)\b",
            r"\b[A-Z][A-Za-z0-9_]{2,}(?:Engine|Policy|Guard|Layer|Core|Builder|State|Report|Frame|Lens)\b",
            r"\b[a-z][a-z0-9_]+(?:_[a-z0-9]+){1,}\b",
        )
        for pattern in patterns:
            for match in re.findall(pattern, raw):
                anchors.add(normalize_vi(match))
        return anchors

    # ------------------------------------------------------------------
    # acceptance
    # ------------------------------------------------------------------

    @dataclass(frozen=True, slots=True)
    class _AcceptanceScores:
        accepted_answer: float = 0.0
        accepted_style: float = 0.0
        accepted_action: float = 0.0

    def _score_acceptance(
        self,
        ctx: RelationshipEventBuildContext,
    ) -> tuple[_AcceptanceScores, dict[str, float], tuple[RelationshipEvidence, ...]]:
        normalized = normalize_vi(ctx.user_text)
        evidence: list[RelationshipEvidence] = []

        correction = clamp01(get_attr_or_key(ctx.affect, "correction", 0.0))
        perceived_rejection = clamp01(get_attr_or_key(ctx.affect, "perceived_rejection", 0.0))
        has_contrast = self._contains_any(normalized, self._CONTRAST_OR_CORRECTION)
        quality_question = self._contains_any(normalized, self._QUALITY_QUESTION) or self._looks_like_quality_question(normalized)

        explicit_answer = ctx.explicit_accepted_answer
        explicit_style = ctx.explicit_accepted_style
        explicit_action = ctx.explicit_accepted_action

        positive_ack = self._positive_ack_score(normalized)
        style_target = self._style_target_score(normalized)
        action_continue = self._action_continue_score(normalized)
        continued_plan = self._continued_same_plan_score(ctx)
        previous_success = clamp01(max(ctx.previous_assistant_success, ctx.assistant_technical_success))

        negative_gate = clamp01(
            correction * 0.55
            + perceived_rejection * 0.50
            + (0.45 if has_contrast else 0.0)
            + (0.30 if quality_question else 0.0)
        )

        answer = clamp01(positive_ack * 0.58 + previous_success * 0.22 + continued_plan * 0.20 - negative_gate)
        style = clamp01(style_target * 0.48 + positive_ack * 0.16 + previous_success * 0.18 + continued_plan * 0.18 - negative_gate)
        action = clamp01(action_continue * 0.50 + continued_plan * 0.32 + previous_success * 0.18 - correction * 0.30 - perceived_rejection * 0.35)

        if explicit_answer is not None:
            answer = clamp01(explicit_answer)
            evidence.append(RelationshipEvidence("explicit_accepted_answer", answer, "explicit_override", 1.0, ctx.evidence_ids))
        if explicit_style is not None:
            style = clamp01(explicit_style)
            evidence.append(RelationshipEvidence("explicit_accepted_style", style, "explicit_override", 1.0, ctx.evidence_ids))
        if explicit_action is not None:
            action = clamp01(explicit_action)
            evidence.append(RelationshipEvidence("explicit_accepted_action", action, "explicit_override", 1.0, ctx.evidence_ids))

        if positive_ack > 0.0:
            evidence.append(RelationshipEvidence("positive_ack", positive_ack, "surface_text", 1.0, ctx.evidence_ids))
        if style_target > 0.0:
            evidence.append(RelationshipEvidence("style_target_positive", style_target, "surface_text", 1.0, ctx.evidence_ids))
        if action_continue > 0.0:
            evidence.append(RelationshipEvidence("continued_action_request", action_continue, "surface_text", 1.0, ctx.evidence_ids))
        if continued_plan > 0.0:
            evidence.append(RelationshipEvidence("continued_same_plan", continued_plan, "conversation_context", 1.0, ctx.evidence_ids))
        if previous_success > 0.0:
            evidence.append(RelationshipEvidence("previous_assistant_success", previous_success, "outcome_observer", 1.0, ctx.evidence_ids))
        if negative_gate > 0.0:
            evidence.append(RelationshipEvidence("acceptance_negative_gate", negative_gate, "surface_or_affect", -1.0, ctx.evidence_ids))

        debug = {
            "positive_ack": positive_ack,
            "style_target": style_target,
            "action_continue": action_continue,
            "continued_plan": continued_plan,
            "previous_success": previous_success,
            "negative_gate": negative_gate,
            "answer": answer,
            "style": style,
            "action": action,
        }
        return self._AcceptanceScores(answer, style, action), debug, tuple(evidence)

    def _positive_ack_score(self, normalized: str) -> float:
        if not normalized:
            return 0.0
        score = 0.0
        for marker in self._POSITIVE_ACK:
            if marker in normalized:
                score = max(score, 0.62 if len(normalized.split()) <= 6 else 0.48)
        # "ok" một mình quá yếu, nhưng "ok làm tiếp" sẽ ăn ở accepted_action.
        if normalized in {"ok", "oke", "okay", "duoc", "on"}:
            score = max(score, 0.35)
        return clamp01(score)

    def _style_target_score(self, normalized: str) -> float:
        if not normalized:
            return 0.0
        target = any(marker in normalized for marker in self._STYLE_TARGET)
        positive = any(marker in normalized for marker in self._POSITIVE_ACK) or any(
            marker in normalized for marker in ("hop", "do", "tot", "tu nhien", "khong bi", "bot")
        )
        if target and positive:
            return 0.72
        if target:
            return 0.38
        return 0.0

    def _action_continue_score(self, normalized: str) -> float:
        if not normalized:
            return 0.0
        hits = sum(1 for marker in self._ACTION_CONTINUE if marker in normalized)
        if hits <= 0:
            return 0.0
        return clamp01(0.38 + min(0.34, hits * 0.12))

    def _continued_same_plan_score(self, ctx: RelationshipEventBuildContext) -> float:
        # ConversationDynamics nếu có mode execute/repair/continuing thì tin hơn.
        mode = str(get_attr_or_key(ctx.conversation_state, "mode", "") or "").lower()
        pressure = clamp01(get_attr_or_key(ctx.conversation_state, "conversation_pressure", 0.0))
        alignment = str(get_attr_or_key(ctx.conversation_state, "alignment_trend", "") or "").lower()

        score = 0.0
        if mode in {"execute", "compress", "repair", "technical", "continue"}:
            score = max(score, 0.34)
        if alignment in {"aligned", "recovering", "adjusting"}:
            score = max(score, 0.30)
        if pressure > 0.0 and mode in {"execute", "repair"}:
            score = max(score, 0.25 + pressure * 0.15)

        # Fallback mềm từ shape: user đưa lệnh tiếp sau assistant có nội dung kỹ thuật.
        normalized = normalize_vi(ctx.user_text)
        if any(marker in normalized for marker in self._ACTION_CONTINUE) and ctx.previous_assistant_text:
            score = max(score, 0.42)
        return clamp01(score)

    def _looks_like_quality_question(self, normalized: str) -> bool:
        if "?" in normalized:
            return True
        return normalized.endswith(" chua") or normalized.endswith(" khong") or any(
            phrase in normalized for phrase in ("co on", "co dung", "co chuan", "nen khong")
        )

    def _contains_any(self, normalized: str, markers: Sequence[str]) -> bool:
        return any(marker in normalized for marker in markers)


__all__ = [
    "RelationshipEvidence",
    "RelationshipEventScores",
    "RelationshipEventBuildContext",
    "RelationshipEventBuilder",
    "RecentTurn",
    "clamp01",
    "normalize_vi",
]
