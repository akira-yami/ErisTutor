from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

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

'''
File này tạo ra một tầng nằm giữa:

user đang nói gì
và ERIS nên phản ứng với “lịch sử đối thoại” đó như thế nào

Thay vì mỗi turn độc lập kiểu:

user nói → model trả lời → quên sạch
nó giữ:

tension của cuộc nói chuyện
pressure tích lũy
loại fail gần nhất
mode hiện tại của cuộc hội thoại
các “adaptation flag” để steer prompt
'''

VALID_CONVERSATION_MODES = {
    "explore", #đào sâu
    "clarify", #hỏi ngắn
    "repair", #acknowledge nhanh + sửa
    "relational_repair",
    "execute", #action-first
    "compress", #ngắn và dense
    "defer", #tạm dừng hoặc tránh nhảy vào
    "settled", #bình thường
}

VALID_FAILURE_TYPES = {
    "misread",
    "overexplained",
    "underacted",
    "tone_mismatch",
    "tone_distance_mismatch",
    "wrong_abstraction",
    "format_error",
    "none",
}

POSITIVE_RELIEF_MARKERS = (
    "ok",
    "oke",
    "on",
    "on roi",
    "duoc",
    "duoc roi",
    "dung roi",
    "chuan",
    "tot",
    "hop ly",
    "co ly",
    "hieu roi",
    "tiep tuc",
    "gio tiep",
    "gio dung",
    "khong sao",
    "van on",
    "tam duoc",
)

POSITIVE_RELIEF_WEIGHTS = {
    "ok": 0.12,
    "oke": 0.12,
    "on": 0.16,
    "duoc": 0.16,
    "tot": 0.18,
    "chuan": 0.22,
    "hop ly": 0.24,
    "co ly": 0.24,
    "tam duoc": 0.26,
    "van on": 0.28,
    "khong sao": 0.30,
    "on roi": 0.32,
    "duoc roi": 0.34,
    "hieu roi": 0.34,
    "gio tiep": 0.34,
    "tiep tuc": 0.34,
    "gio dung": 0.36,
    "dung roi": 0.38,
}

ADVERSATIVE_MARKERS = (
    "nhung",
    "nhung ma",
    "ma",
    "tuy nhien",
    "co dieu",
)

RELIEF_REVERSAL_MARKERS = (
    "sai",
    "khong dung",
    "chua dung",
    "khong phai",
    "lech",
    "nham",
    "thieu",
    "fail",
    "lan man",
    "dai dong",
    "qua te",
)

FRUSTRATION_MARKER_WEIGHTS = {
    "buc": 0.18,
    "kho chiu": 0.20,
    "uc che": 0.24,
    "met": 0.12,
    "chan": 0.12,
    "lai sai": 0.24,
    "sai nua": 0.26,
    "bao lan": 0.30,
    "da noi": 0.24,
    "cu lap lai": 0.28,
    "fail": 0.18,
    "qua te": 0.24,
}

FRUSTRATION_MARKERS = tuple(FRUSTRATION_MARKER_WEIGHTS)

MINOR_CORRECTION_MARKERS = (
    "typo",
    "nho thoi",
    "nhe thoi",
    "mot chut",
    "chut thoi",
    "doi chu",
    "sua chu",
    "doi ten",
)


def _clamp(value: object, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    if numeric != numeric:
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def _clean_text(value: object, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def normalize_vietnamese_ascii(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text or "")
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = normalized.replace("\u0111", "d").replace("\u0110", "D")
    normalized = re.sub(r"[^\w\s]", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized.lower()).strip()


def _recent_unique(current: tuple[str, ...], incoming: tuple[str, ...], *, limit: int = 8) -> tuple[str, ...]:
    values = [item for item in current if str(item or "").strip()]
    for item in incoming:
        clean = _clean_text(item, limit=80)
        if clean and clean not in values:
            values.append(clean)
    return tuple(values[-limit:])


def _token_set(text: str) -> set[str]:
    return {token for token in normalize_vietnamese_ascii(text).split() if len(token) > 1}


def _jaccard_distance(left: str, right: str) -> float:
    left_tokens = _token_set(left)
    right_tokens = _token_set(right)
    if not left_tokens or not right_tokens:
        return 0.5 if left_tokens or right_tokens else 0.0
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return _clamp(1.0 - (overlap / max(1, union)))


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(re.search(rf"\b{re.escape(phrase)}\b", text))


def _previous_assistant_asked_clarification(text: str) -> bool:
    normalized = normalize_vietnamese_ascii(text)
    if not normalized:
        return False
    markers = (
        "co dieu gi toi vua noi",
        "ban co the noi ro hon",
        "toi chua hieu y ban",
        "co dieu gi khien ban",
        "noi ro hon duoc khong",
    )
    return "?" in (text or "") and any(marker in normalized for marker in markers)


def _user_now_explains_reason(text: str) -> bool:
    normalized = normalize_vietnamese_ascii(text)
    if not normalized:
        return False
    reason_markers = (
        "vi",
        "boi vi",
        "tai vi",
        "do",
        "ly do",
        "nguyen nhan",
        "em bao",
        "chi tran trong",
        "khong yeu",
        "phu phang",
        "tinh cam cua toi",
    )
    return any(_contains_phrase(normalized, marker) for marker in reason_markers)


def _marker_has_acceptance_after(text: str, marker: str) -> bool:
    index = text.find(marker)
    if index < 0:
        return False
    tail = text[index + len(marker) :]
    return bool(re.search(r"\b(?:nhung|ma|nhung ma|tuy nhien)\b.{0,32}\b(?:on|duoc|ok|oke|tot|hop ly|van on)\b", tail))


def _has_negative_length_polarity(text: str) -> bool:
    if not text:
        return False
    if "dai" in text and _marker_has_acceptance_after(text, "dai"):
        return False
    negative_patterns = (
        r"\bdai dong\b",
        r"\blan man\b",
        r"\boverexplain",
        r"\bqua\s+dai\b",
        r"\bdai\s+qua\b",
        r"\bhoi\s+dai\b",
        r"\bkhong\s+can.{0,24}\bdai\b",
        r"\bbot.{0,16}\bdai\b",
        r"\brut\s+gon\b",
    )
    return any(re.search(pattern, text) for pattern in negative_patterns)


def _has_relief_reversal(text: str, marker: str) -> bool:
    marker_pattern = re.escape(marker)
    adversatives = "|".join(re.escape(item) for item in ADVERSATIVE_MARKERS)
    negatives = "|".join(re.escape(item) for item in RELIEF_REVERSAL_MARKERS)
    pattern = rf"\b{marker_pattern}\b(?:\s+\w+){{0,4}}\s+(?:{adversatives})(?:\s+\w+){{0,8}}\s+(?:{negatives})\b"
    return bool(re.search(pattern, text))


def _positive_relief_score(text: str) -> float:
    score = 0.0
    for marker, weight in POSITIVE_RELIEF_WEIGHTS.items():
        if not _contains_phrase(text, marker):
            continue
        if _has_relief_reversal(text, marker):
            continue
        score = max(score, weight)
    return _clamp(score)


@dataclass(slots=True)
class ConversationFailure:
    type: str = "none"
    trigger: str = ""
    correction_needed: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.type if self.type in VALID_FAILURE_TYPES else "none",
            "trigger": self.trigger,
            "correction_needed": self.correction_needed,
        }

    @classmethod
    def from_dict(cls, data: object) -> "ConversationFailure":
        if not isinstance(data, dict):
            return cls()
        failure_type = str(data.get("type", "none") or "none").strip()
        if failure_type not in VALID_FAILURE_TYPES:
            failure_type = "none"
        return cls(
            type=failure_type,
            trigger=_clean_text(data.get("trigger", ""), limit=160),
            correction_needed=_clean_text(data.get("correction_needed", ""), limit=220),
        )


@dataclass(slots=True)
class CorrectionAssessment:
    detected: bool = False
    severity: float = 0.0
    semantic_divergence: float = 0.0
    frustration: float = 0.0
    minor: bool = False


@dataclass(slots=True)
class ContinuityCurve:
    semantic_convergence: float = 0.5 #đánh giá mức độ hội thoại đang đi vào quỹ đạo hội thoại ổn định, dựa trên các tín hiệu như sự giảm tension, sự hài lòng của người dùng, và chất lượng acknowledgment. Giá trị cao cho thấy cuộc hội thoại đang dần đi vào quỹ đạo ổn định và có khả năng đạt được sự hiểu biết chung giữa ERIS và người dùng.
    trust_recovery: float = 0.5 #đánh giá mức độ ERIS đã lấy lại được trust sau khi có sự cố, dựa trên các tín hiệu như sự giảm tension, sự hài lòng của người dùng, và chất lượng acknowledgment. Giá trị cao cho thấy ERIS đang dần lấy lại được sự tin tưởng của người dùng sau một lỗi hoặc sự cố.
    abstraction_stability: float = 0.5 #đánh giá mức độ ổn định của các khung trừu tượng mà ERIS sử dụng để hiểu ý định của người dùng, dựa trên các tín hiệu như sự giảm tension, sự hài lòng của người dùng, và chất lượng acknowledgment. Giá trị cao cho thấy ERIS đang duy trì được sự ổn định trong cách nó trừu tượng hóa và hiểu ý định của người dùng, ngay cả khi có sự cố xảy ra.
    alignment_trend: str = "neutral" #đánh giá xu hướng chung của cuộc hội thoại đang đi theo hướng hội tụ (converging), phân kỳ (diverging), điều chỉnh (adjusting), hay trung tính (neutral), dựa trên các tín hiệu như sự giảm tension, sự hài lòng của người dùng, chất lượng acknowledgment, và các dấu hiệu khác trong văn bản. Xu hướng hội tụ cho thấy cuộc hội thoại đang dần đi vào quỹ đạo ổn định, trong khi xu hướng phân kỳ cho thấy cuộc hội thoại đang đi lệch ra khỏi quỹ đạo ổn định.
    rolling_arc: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "semantic_convergence": round(float(self.semantic_convergence), 3),
            "trust_recovery": round(float(self.trust_recovery), 3),
            "abstraction_stability": round(float(self.abstraction_stability), 3),
            "alignment_trend": self.alignment_trend,
            "rolling_arc": list(self.rolling_arc),
        }

    @classmethod
    def from_dict(cls, data: object) -> "ContinuityCurve":
        if not isinstance(data, dict):
            return cls()
        return cls(
            semantic_convergence=_clamp(data.get("semantic_convergence", 0.5)),
            trust_recovery=_clamp(data.get("trust_recovery", 0.5)),
            abstraction_stability=_clamp(data.get("abstraction_stability", 0.5)),
            alignment_trend=_clean_text(data.get("alignment_trend", "neutral"), limit=40) or "neutral",
            rolling_arc=tuple(str(item) for item in (data.get("rolling_arc") or ()) if str(item or "").strip())[-8:],
        )


@dataclass(slots=True)
class ConversationDynamicsState:
    mode: str = "settled"
    semantic_tension: float = 0.0
    conversation_pressure: float = 0.0
    correction_severity: float = 0.0
    semantic_divergence: float = 0.0
    frustration_estimate: float = 0.0
    conversational_relief: float = 0.0
    continuity_curve: ContinuityCurve = field(default_factory=ContinuityCurve)
    last_failure: ConversationFailure = field(default_factory=ConversationFailure)
    adaptation_flags: tuple[str, ...] = ()
    correction_streak: int = 0
    turn_count: int = 0
    recent_modes: tuple[str, ...] = ()
    recent_failure_types: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode if self.mode in VALID_CONVERSATION_MODES else "settled",
            "semantic_tension": round(float(self.semantic_tension), 3),
            "conversation_pressure": round(float(self.conversation_pressure), 3),
            "correction_severity": round(float(self.correction_severity), 3),
            "semantic_divergence": round(float(self.semantic_divergence), 3),
            "frustration_estimate": round(float(self.frustration_estimate), 3),
            "conversational_relief": round(float(self.conversational_relief), 3),
            "continuity_curve": self.continuity_curve.to_dict(),
            "last_failure": self.last_failure.to_dict(),
            "adaptation_flags": list(self.adaptation_flags),
            "correction_streak": int(self.correction_streak),
            "turn_count": int(self.turn_count),
            "recent_modes": list(self.recent_modes),
            "recent_failure_types": list(self.recent_failure_types),
        }

    @classmethod
    def from_dict(cls, data: object) -> "ConversationDynamicsState":
        if not isinstance(data, dict):
            return cls()
        mode = str(data.get("mode", "settled") or "settled").strip()
        if mode not in VALID_CONVERSATION_MODES:
            mode = "settled"
        return cls(
            mode=mode,
            semantic_tension=_clamp(data.get("semantic_tension", 0.0)),
            conversation_pressure=_clamp(data.get("conversation_pressure", 0.0)),
            correction_severity=_clamp(data.get("correction_severity", 0.0)),
            semantic_divergence=_clamp(data.get("semantic_divergence", 0.0)),
            frustration_estimate=_clamp(data.get("frustration_estimate", 0.0)),
            conversational_relief=_clamp(data.get("conversational_relief", 0.0)),
            continuity_curve=ContinuityCurve.from_dict(data.get("continuity_curve") or {}),
            last_failure=ConversationFailure.from_dict(data.get("last_failure") or {}),
            adaptation_flags=tuple(str(item) for item in (data.get("adaptation_flags") or ()) if str(item or "").strip())[-12:],
            correction_streak=max(0, int(data.get("correction_streak", 0) or 0)),
            turn_count=max(0, int(data.get("turn_count", 0) or 0)),
            recent_modes=tuple(str(item) for item in (data.get("recent_modes") or ()) if str(item or "").strip())[-8:],
            recent_failure_types=tuple(str(item) for item in (data.get("recent_failure_types") or ()) if str(item or "").strip())[-8:],
        )

    def to_prompt_context(self) -> str:
        flags = ", ".join(self.adaptation_flags[-8:]) or "none"
        recent_modes = ", ".join(self.recent_modes[-4:]) or "none"
        failure = self.last_failure
        curve = self.continuity_curve
        lines = [
            "conversation_dynamics: guidance only; preserve ERIS voice and do not announce these labels.",
            (
                "conversation_state: "
                f"mode={self.mode}; semantic_tension={self.semantic_tension:.2f}; "
                f"conversation_pressure={self.conversation_pressure:.2f}; correction_streak={self.correction_streak}; "
                f"correction_severity={self.correction_severity:.2f}; semantic_divergence={self.semantic_divergence:.2f}; "
                f"frustration_estimate={self.frustration_estimate:.2f}; conversational_relief={self.conversational_relief:.2f}."
            ),
            (
                "continuity_curve: "
                f"semantic_convergence={curve.semantic_convergence:.2f}; trust_recovery={curve.trust_recovery:.2f}; "
                f"abstraction_stability={curve.abstraction_stability:.2f}; alignment_trend={curve.alignment_trend}; "
                f"rolling_arc={', '.join(curve.rolling_arc[-4:]) or 'neutral'}."
            ),
            (
                "previous_failure: "
                f"type={failure.type}; trigger={failure.trigger or 'none'}; "
                f"correction_needed={failure.correction_needed or 'none'}."
            ),
            f"adaptation_flags: {flags}.",
            f"recent_conversation_modes: {recent_modes}.",
            "conversation_rule: if mode=repair, briefly absorb the correction, avoid the failed frame, answer the current intent directly, lower overconfident inference, and keep the same living voice.",
            "relational_repair_rule: if mode=relational_repair, acknowledge the specific tone/distance feedback, concede the prior wording if needed, do not defend the previous line, do not ask what caused it when the user named it, keep the non-human boundary soft, and do not end with a question.",
        ]
        imperatives = self._flag_imperatives()
        if imperatives:
            lines.append(
                "active_constraints (hard; obey even if it costs politeness): " + " ".join(imperatives)
            )
        return "\n".join(lines)

    # Cờ không còn là gợi ý phẳng: chuyển thành mệnh lệnh tường minh để model
    # khó lờ đi. Đây là phần C của bản vá chống-disclaimer.
    _FLAG_IMPERATIVES = {
        "do_not_lead_with_identity_disclaimer": (
            "Do NOT open with an identity/capability disclaimer "
            "('tôi là ERIS, một hiện diện nhỏ...', 'tôi chỉ là...'); answer the point first."
        ),
        "avoid_over_human_love_claim": (
            "Do not claim human emotion, romance, or devotion — but do not flatly deny any inner "
            "reaction either; use the soft form ('câu đó làm tôi dừng lại một chút')."
        ),
        "warmth_floor_required": (
            "Keep a minimum of real warmth; no cold helpdesk 'tôi chỉ cố gắng hỗ trợ và giúp đỡ' framing."
        ),
        "forbid_defensive_clarification": (
            "Do not defend or re-explain the previous wording; absorb the feedback and move."
        ),
        "acknowledge_tone_feedback": (
            "Name the specific tone/distance feedback the user just gave, in one short concession."
        ),
        "lower_distance_temporarily": (
            "Lower the guarded distance by one notch this turn; do not over-correct into sweetness."
        ),
        "no_more_clarifying_question": (
            "Do not ask another clarifying question; give a direct answer."
        ),
        "answer_without_clarifying_question": (
            "Answer the current intent without asking a clarifying question."
        ),
        "do_not_lead_with_identity_disclaimer_soft": (
            "Avoid clinical self-description; stay situated and concrete."
        ),
    }

    def _flag_imperatives(self) -> tuple[str, ...]:
        seen: list[str] = []
        for flag in self.adaptation_flags:
            line = self._FLAG_IMPERATIVES.get(str(flag))
            if line and line not in seen:
                seen.append(line)
        return tuple(seen)


@dataclass(slots=True)
class ConversationDynamicsReport:
    state: ConversationDynamicsState = field(default_factory=ConversationDynamicsState)
    prompt_context: str = ""
    correction_detected: bool = False
    failure_detected: bool = False
    evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.to_dict(),
            "prompt_context": self.prompt_context,
            "correction_detected": bool(self.correction_detected),
            "failure_detected": bool(self.failure_detected),
            "evidence": list(self.evidence),
        }


class ConversationDynamicsEngine:
    def evaluate_chat(
        self,
        state: ConversationDynamicsState | None,
        *,
        user_text: str,
        last_assistant_text: str = "",
        last_user_intent: str = "",
        active_topic: str = "",
        repeat_count: int = 1,
        correction_detected: bool = False,
        affect_signal: object | None = None,
        relational_feedback: RelationalFeedbackSignal | None = None,
        social_appraisal: object | None = None,
    ) -> ConversationDynamicsReport:
        previous = state or ConversationDynamicsState() #nếu không có state trước đó, bắt đầu với state mặc định
        normalized = normalize_vietnamese_ascii(user_text) #bình thường hóa văn bản người dùng để dễ dàng phân tích
        if social_appraisal is not None:
            correction_signal = getattr(social_appraisal, "correction", None)
            claim = getattr(social_appraisal, "claim", None)
            irritation_signal = getattr(social_appraisal, "irritation", None)
            thresholds = getattr(social_appraisal, "thresholds", None)
            pressure = max(
                _clamp(getattr(correction_signal, "value", 0.0)),
                _clamp(getattr(claim, "contest", 0.0)),
                _clamp(getattr(claim, "contradiction", 0.0)),
            )
            repair_gate = float(getattr(thresholds, "repair_active", 0.46) or 0.46)
            suppress_gate = float(getattr(thresholds, "claim_suppress", 0.66) or 0.66)
            correction = CorrectionAssessment(
                detected=pressure >= repair_gate,
                severity=pressure,
                semantic_divergence=_clamp(getattr(claim, "contradiction", 0.0)),
                frustration=_clamp(getattr(irritation_signal, "value", 0.0)),
                minor=(pressure >= repair_gate and pressure < suppress_gate),
            )
        else:
            # Fallback cho caller legacy. Hot path chat phải truyền snapshot chung.
            correction = self.assess_correction(
                user_text,
                last_assistant_text=last_assistant_text,
                external_detected=correction_detected,
            )
        has_correction = correction.detected #phát hiện xem người dùng có đang sửa lỗi không, dựa trên cờ correction_detected hoặc các dấu hiệu trong văn bản
        references_previous_failure = self.references_previous_failure(user_text) #kiểm tra xem người dùng có đang nhắc đến lỗi trước đó không, dựa trên các dấu hiệu trong văn bản
        direct_execute = self.looks_like_execute_request(user_text) #phát hiện xem người dùng có đang yêu cầu thực thi trực tiếp không, dựa trên các dấu hiệu trong văn bản
        compression = self.looks_like_compression_request(user_text) #phát hiện xem người dùng có đang yêu cầu nén thông tin không, dựa trên các dấu hiệu trong văn bản
        clarification = self.looks_like_clarification_request(user_text) #phát hiện xem người dùng có đang yêu cầu làm rõ ý định không, dựa trên các dấu hiệu trong văn bản
        perceived_rejection = _clamp(getattr(affect_signal, "perceived_rejection", 0.0))
        attachment_signal = _clamp(getattr(affect_signal, "attachment", 0.0))
        relational_feedback = relational_feedback or RelationalFeedbackSignal.empty()
        feedback_repair = _clamp(getattr(relational_feedback, "repair_needed", 0.0))
        feedback_intensity = _clamp(getattr(relational_feedback, "intensity", 0.0))
        feedback_warmth_request = _clamp(getattr(relational_feedback, "warmth_request", 0.0))
        feedback_defensive_risk = _clamp(getattr(relational_feedback, "defensive_risk", 0.0))
        relational_repair_needed = (
            perceived_rejection >= 0.65 and attachment_signal >= 0.45
        ) or (bool(getattr(relational_feedback, "detected", False)) and feedback_repair >= 0.35)
        explanation_after_clarification = (
            _previous_assistant_asked_clarification(last_assistant_text)
            and _user_now_explains_reason(user_text)
        )
        relief = self.estimate_conversational_relief(
            user_text,
            previous=previous,
            last_assistant_text=last_assistant_text,
            direct_execute=direct_execute,
            compression=compression,
        )

        failure = previous.last_failure
        evidence: list[str] = []
        flags: list[str] = []

        tension = previous.semantic_tension #cuộc trò chuyện đang lệch semantic bao nhiêu
        pressure = previous.conversation_pressure #user đang mất kiên nhẫn bao nhiêu
        correction_streak = previous.correction_streak #liên tiếp bao nhiêu lần user sửa lỗi (có thể tăng mức độ acknowledge và sửa nhanh hơn)

        if has_correction:
            failure = self.infer_failure(user_text, last_assistant_text=last_assistant_text)
            if correction.severity >= 0.35:
                correction_streak += 1
            else:
                correction_streak = max(0, correction_streak - 1)
            tension = _clamp(
                tension * (0.48 + correction.severity * 0.18)
                + 0.12
                + correction.severity * 0.42
                + correction.semantic_divergence * 0.18
            )
            pressure = _clamp(
                pressure * (0.56 + correction.frustration * 0.22)
                + correction.severity * 0.24
                + correction.frustration * 0.26
                + min(0.16, correction_streak * 0.04)
            )
            evidence.append(f"user_correction_severity={correction.severity:.2f}")
            if correction.minor:
                flags.extend(("apply_small_correction", "avoid_overrepair", "preserve_conversation_mode"))
            else:
                flags.extend(("repair_first", "avoid_failed_frame", "lower_inference_confidence"))
        else:
            correction_streak = max(0, correction_streak - 1)
            acknowledgment_quality = self.estimate_acknowledgment_quality(last_assistant_text)
            tension_retention = 0.88 - relief * 0.34 - acknowledgment_quality * 0.08
            pressure_retention = 0.90 - relief * 0.38 - acknowledgment_quality * 0.10
            tension = _clamp(tension * tension_retention - relief * 0.03)
            pressure = _clamp(pressure * pressure_retention - relief * 0.02)
            if relief >= 0.35:
                evidence.append(f"conversational_relief={relief:.2f}")
                flags.append("continue_recovered_thread")

        # Failure chỉ sống khi lượt hiện tại còn đang sửa/nhắc lại lỗi. Một lượt
        # sạch phải cho state quay về bình thường; nếu không mọi câu sau đều bị ép
        # vào repair_reframe chỉ vì một record cũ còn nằm đó.
        if (
            not has_correction
            and not references_previous_failure
            and not relational_repair_needed
            and str(getattr(failure, "type", "none") or "none") != "none"
        ):
            failure = ConversationFailure()
            evidence.append("last_failure_cleared_after_clean_turn")

        if references_previous_failure:
            tension = _clamp(tension + 0.12)
            pressure = _clamp(pressure + 0.10)
            evidence.append("references_previous_failure")
            flags.append("adapt_to_prior_failure")

        if repeat_count >= 2:
            pressure = _clamp(pressure + min(0.16, repeat_count * 0.04))
            evidence.append(f"repeat_count={repeat_count}")
            flags.append("respect_repetition_pressure")

        if direct_execute:
            evidence.append("execute_request")
            flags.append("answer_with_action_shape")
        if compression:
            evidence.append("compression_request")
            flags.append("compress_response")
        if clarification:
            evidence.append("clarification_request")
            flags.append("clarify_intent_before_expanding")

        if relational_repair_needed:
            failure = ConversationFailure(
                type="tone_distance_mismatch",
                trigger=_clean_text(last_assistant_text or user_text, limit=160),
                correction_needed="acknowledge cold/distant tone feedback, concede wording fault, lower distance one notch",
            )
            repair_pressure = max(perceived_rejection, feedback_repair, feedback_intensity)
            tension = _clamp(max(tension, 0.46) + repair_pressure * 0.10)
            pressure = _clamp(max(pressure, 0.42) + repair_pressure * 0.12)
            evidence.append(f"perceived_rejection={perceived_rejection:.2f}")
            if bool(getattr(relational_feedback, "detected", False)):
                evidence.append(f"relational_feedback={getattr(relational_feedback, 'kind', 'unknown')}:{feedback_intensity:.2f}")
            flags.extend((
                "relational_repair",
                "acknowledge_tone_feedback",
                "forbid_defensive_clarification",
                "lower_distance_temporarily",
                "warmth_floor_required",
                "avoid_over_human_love_claim",
                "do_not_lead_with_identity_disclaimer",
            ))
            if feedback_defensive_risk >= 0.45:
                flags.append("do_not_justify_previous_wording")
            if feedback_warmth_request >= 0.30:
                flags.append("small_concession_not_sweetness")

        if explanation_after_clarification:
            evidence.append("user_explained_after_clarification")
            flags.extend(("no_more_clarifying_question", "answer_the_explanation"))

        mode = self.choose_mode(
            tension=tension,
            pressure=pressure,
            has_correction=has_correction,
            correction_severity=correction.severity,
            direct_execute=direct_execute,
            compression=compression,
            clarification=clarification,
            normalized=normalized,
            relational_repair=relational_repair_needed,
        )
        continuity_curve = self.update_continuity_curve(
            previous.continuity_curve,
            mode=mode,
            correction=correction,
            relief=relief,
            failure=failure,
            direct_execute=direct_execute,
            compression=compression,
        )
        flags.extend(self.mode_flags(mode))
        if active_topic:
            evidence.append(f"topic={_clean_text(active_topic, limit=80)}")
        if last_user_intent:
            evidence.append(f"last_intent={_clean_text(last_user_intent, limit=80)}")

        clean_flags = _recent_unique(previous.adaptation_flags, tuple(flags), limit=12)
        if not flags and clean_flags:
            clean_flags = clean_flags[-4:]

        next_state = ConversationDynamicsState(
            mode=mode,
            semantic_tension=tension,
            conversation_pressure=pressure,
            correction_severity=correction.severity if has_correction else 0.0,
            semantic_divergence=correction.semantic_divergence if has_correction else 0.0,
            frustration_estimate=correction.frustration,
            conversational_relief=relief,
            continuity_curve=continuity_curve,
            last_failure=failure,
            adaptation_flags=clean_flags,
            correction_streak=correction_streak,
            turn_count=previous.turn_count + 1,
            recent_modes=(*previous.recent_modes, mode)[-8:],
            recent_failure_types=(
                *previous.recent_failure_types,
                failure.type if (has_correction or relational_repair_needed) else "none",
            )[-8:],
        )
        return ConversationDynamicsReport(
            state=next_state,
            prompt_context=next_state.to_prompt_context(),
            correction_detected=has_correction,
            failure_detected=has_correction or references_previous_failure or relational_repair_needed,
            evidence=tuple(dict.fromkeys(evidence))[-10:],
        )

    # Dựa trên các dấu hiệu trong văn bản, xác định xem người dùng có đang sửa lỗi hay không
    def assess_correction(
        self,
        text: str,
        *,
        last_assistant_text: str = "",
        external_detected: bool = False,
    ) -> CorrectionAssessment:
        normalized = normalize_vietnamese_ascii(text)
        if not normalized:
            return CorrectionAssessment()

        replacement_markers = (
            "y toi la",
            "y minh la",
            "toi noi la",
            "toi bao la",
            "sua lai la",
            "phai la",
            "dung ra la",
        )
        meta_challenge_markers = (
            "hieu sai la sao",
            "toi da noi gi dau",
            "toi noi gi dau",
            "co sua gi dau",
            "toi sua gi dau",
        )
        has_replacement = _contains_any(normalized, replacement_markers)
        last_assistant_norm = normalize_vietnamese_ascii(last_assistant_text)
        last_assistant_claimed_repair = _contains_any(
            last_assistant_norm,
            ("hieu sai", "vua sua", "vua chinh", "bo frame"),
        )
        # Meta-challenge không phải correction thật. Ví dụ:
        # "hiểu sai là sao, tôi đã nói gì đâu" đang chất vấn câu repair của ERIS,
        # chưa cung cấp nội dung thay thế để ERIS sửa theo.
        if (
            _contains_any(normalized, meta_challenge_markers)
            and not has_replacement
            and (last_assistant_claimed_repair or normalized.startswith("hieu sai la sao"))
        ):
            return CorrectionAssessment()

        strong_markers = (
            "khong phai y toi",
            "khong phai y minh",
            "sai roi",
            "ban hieu sai",
            "hieu sai roi",
            "khong dung",
            "chua dung",
            "toi khong noi vay",
            "ban doan sai",
            "ban hieu lech",
        )
        moderate_markers = (
            "khong phai",
            "thieu la",
            "thieu nua",
            "con thieu",
            "chua lam",
            "chua them",
            "nhung sai",
            "ma sai",
            "van sai",
            "sai framework",
            "sai tang",
            "wrong abstraction",
            "response truoc",
        )
        minor = _contains_any(normalized, MINOR_CORRECTION_MARKERS)
        strong = _contains_any(normalized, strong_markers)
        moderate = _contains_any(normalized, moderate_markers)
        detected = bool(external_detected or strong or moderate or minor)
        if not detected:
            return CorrectionAssessment()

        frustration = self.estimate_frustration(normalized, correction_detected=detected)
        if strong:
            severity = 0.72
        elif moderate:
            severity = 0.50
        elif external_detected:
            severity = 0.44
        else:
            severity = 0.24

        if "sai roi" in normalized or "hieu sai" in normalized:
            severity += 0.08
        if references := self.references_previous_failure(text):
            severity += 0.08
        if frustration:
            severity += min(0.12, frustration * 0.30)
        if minor:
            severity *= 0.48

        semantic_divergence = self.estimate_semantic_divergence(
            normalized,
            last_assistant_text=last_assistant_text,
            base_severity=severity,
        )
        if references:
            semantic_divergence = _clamp(semantic_divergence + 0.10)

        return CorrectionAssessment(
            detected=True,
            severity=_clamp(severity),
            semantic_divergence=semantic_divergence,
            frustration=frustration,
            minor=bool(minor or severity < 0.35),
        )

    def looks_like_correction(self, text: str) -> bool:
        return self.assess_correction(text).detected

    def estimate_semantic_divergence(
        self,
        normalized_text: str,
        *,
        last_assistant_text: str = "",
        base_severity: float = 0.0,
    ) -> float:
        divergence = base_severity * 0.55
        if any(marker in normalized_text for marker in ("khong phai y", "hieu sai", "toi khong noi", "doan sai")):
            divergence = max(divergence, 0.74)
        elif any(marker in normalized_text for marker in ("sai framework", "sai tang", "wrong abstraction", "abstract")):
            divergence = max(divergence, 0.68)
        elif any(marker in normalized_text for marker in ("thieu", "chua lam", "chua them")):
            divergence = max(divergence, 0.42)
        elif _contains_any(normalized_text, MINOR_CORRECTION_MARKERS):
            divergence = min(divergence, 0.22)

        if last_assistant_text:
            lexical_distance = _jaccard_distance(normalized_text, last_assistant_text)
            divergence = divergence * 0.72 + lexical_distance * 0.28
        return _clamp(divergence)

    def estimate_frustration(self, normalized_text: str, *, correction_detected: bool = False) -> float:
        if not normalized_text:
            return 0.0
        score = 0.0
        for marker, weight in FRUSTRATION_MARKER_WEIGHTS.items():
            if _contains_phrase(normalized_text, marker):
                score += weight

        if correction_detected and any(marker in normalized_text for marker in ("ban", "co", "eris")):
            score += 0.06
        if any(marker in normalized_text for marker in ("that su", "qua", "rat", "cuc ky")):
            score += 0.06
        if any(marker in normalized_text for marker in ("nhe thoi", "nho thoi", "mot chut", "chut thoi", "khong sao")):
            score *= 0.55
        if _positive_relief_score(normalized_text) >= 0.30 and not any(marker in normalized_text for marker in RELIEF_REVERSAL_MARKERS):
            score *= 0.70
        return _clamp(score)

    def estimate_acknowledgment_quality(self, last_assistant_text: str) -> float:
        normalized = normalize_vietnamese_ascii(last_assistant_text)
        if not normalized:
            return 0.0
        markers = (
            "toi hieu",
            "dung",
            "dung roi",
            "xin loi",
            "toi da lech",
            "de toi sua",
            "sua lai",
            "bo qua khung",
            "khong doan",
        )
        hits = sum(1 for marker in markers if marker in normalized)
        return _clamp(hits * 0.18)

    #giúp tension/pressure giảm theo chất lượng hội thoại, không decay cơ học ngu ngốc nữa
    def estimate_conversational_relief(
        self,
        text: str,
        *,
        previous: ConversationDynamicsState,
        last_assistant_text: str = "",
        direct_execute: bool = False,
        compression: bool = False,
    ) -> float:
        normalized = normalize_vietnamese_ascii(text)
        if not normalized:
            return 0.0
        relief = 0.0
        positive_relief = _positive_relief_score(normalized)
        if positive_relief > 0.0:
            relief += positive_relief
        if direct_execute or compression: #yêu cầu execute hoặc compression thường đi kèm với sự hài lòng tức thì, vì nó cho thấy người dùng cảm thấy câu trả lời đã đáp ứng được ý định của họ một cách rõ ràng.
            relief += 0.12
        if previous.mode == "repair" and positive_relief >= 0.30:
            relief += 0.18
        if previous.conversation_pressure >= 0.25 and not self.assess_correction(text).detected:
            relief += 0.08
        relief += self.estimate_acknowledgment_quality(last_assistant_text) * 0.35
        return _clamp(relief)

    def update_continuity_curve(
        self,
        previous: ContinuityCurve,
        *,
        mode: str,
        correction: CorrectionAssessment,
        relief: float,
        failure: ConversationFailure,
        direct_execute: bool = False,
        compression: bool = False,
    ) -> ContinuityCurve:
        convergence_signal = 0.50 + relief * 0.42
        convergence_signal -= correction.severity * 0.34 + correction.semantic_divergence * 0.18 + correction.frustration * 0.12
        if direct_execute or compression:
            convergence_signal += 0.06
        semantic_convergence = _clamp(previous.semantic_convergence * 0.66 + convergence_signal * 0.34)

        recovery_signal = 0.50 + relief * 0.45 - correction.severity * 0.38 - correction.frustration * 0.18
        trust_recovery = _clamp(previous.trust_recovery * 0.70 + recovery_signal * 0.30)

        abstraction_signal = 0.56 + relief * 0.18 - correction.semantic_divergence * 0.36
        if failure.type == "wrong_abstraction":
            abstraction_signal -= 0.24
        abstraction_stability = _clamp(previous.abstraction_stability * 0.68 + abstraction_signal * 0.32)

        if correction.severity >= 0.55:
            arc = "rupture"
        elif correction.detected:
            arc = "minor_adjustment"
        elif relief >= 0.45:
            arc = "recovering"
        elif semantic_convergence >= 0.58 and trust_recovery >= 0.55:
            arc = "aligned"
        elif mode in {"repair", "relational_repair"}:
            arc = "repairing"
        else:
            arc = "neutral"

        if arc in {"recovering", "aligned"}:
            trend = "converging"
        elif arc == "rupture" or (correction.semantic_divergence >= 0.62 and relief < 0.20):
            trend = "diverging"
        elif arc == "minor_adjustment":
            trend = "adjusting"
        else:
            trend = "neutral"

        return ContinuityCurve(
            semantic_convergence=semantic_convergence,
            trust_recovery=trust_recovery,
            abstraction_stability=abstraction_stability,
            alignment_trend=trend,
            rolling_arc=(*previous.rolling_arc, arc)[-8:],
        )

    def references_previous_failure(self, text: str) -> bool:
        normalized = normalize_vietnamese_ascii(text)
        markers = (
            "that bai truoc",
            "loi truoc",
            "lan truoc",
            "phan truoc",
            "response truoc",
            "cau truoc",
            "pattern cu",
            "fail vi",
        )
        return any(marker in normalized for marker in markers)

    def looks_like_execute_request(self, text: str) -> bool:
        normalized = normalize_vietnamese_ascii(text)
        starters = (
            "hay them",
            "them vao",
            "implement",
            "please implement",
            "lam di",
            "sua di",
            "fix",
            "tao",
            "viet",
            "chay",
        )
        return any(normalized.startswith(item) for item in starters) or "implement this plan" in normalized

    def looks_like_compression_request(self, text: str) -> bool:
        normalized = normalize_vietnamese_ascii(text)
        return any(marker in normalized for marker in ("ngan gon", "tom tat", "compress", "rut gon"))

    def looks_like_clarification_request(self, text: str) -> bool:
        normalized = normalize_vietnamese_ascii(text)
        return normalized in {"gi", "sao", "y la sao", "the nao"} or normalized.endswith(" la sao")

    def infer_failure(self, text: str, *, last_assistant_text: str = "") -> ConversationFailure:
        normalized = normalize_vietnamese_ascii(text)
        failure_type = "misread"
        correction_needed = _clean_text(text, limit=220)

        if any(marker in normalized for marker in ("thieu", "chua lam", "chua them", "underact")):
            failure_type = "underacted"
        elif _has_negative_length_polarity(normalized):
            failure_type = "overexplained"
        elif any(marker in normalized for marker in ("giong", "tone", "qua lanh", "lanh lung", "xa cach", "vo hon", "robot", "qua may", "corporate")):
            failure_type = "tone_distance_mismatch"
        elif any(marker in normalized for marker in ("abstract", "abstraction", "tru tuong", "sai tang", "sai framework")):
            failure_type = "wrong_abstraction"

        missing_match = re.search(r"(?:thi[eế]u(?:\s+l[aà])?|con\s+thi[eế]u)\s*[:\-]?\s*(.+)$", text or "", flags=re.IGNORECASE)
        if missing_match:
            correction_needed = _clean_text(missing_match.group(1), limit=220)

        return ConversationFailure(
            type=failure_type,
            trigger=_clean_text(last_assistant_text or text, limit=160),
            correction_needed=correction_needed,
        )

    def choose_mode(
        self,
        *,
        tension: float,
        pressure: float,
        has_correction: bool,
        correction_severity: float,
        direct_execute: bool,
        compression: bool,
        clarification: bool,
        normalized: str,
        relational_repair: bool = False,
    ) -> str:
        if relational_repair:
            return "relational_repair"
        if has_correction and correction_severity >= 0.48:
            return "repair"
        if tension >= 0.62 or pressure >= 0.72:
            return "repair"
        if direct_execute:
            return "execute"
        if compression:
            return "compress"
        if clarification:
            return "clarify"
        if has_correction:
            return "settled"
        if any(marker in normalized for marker in ("tam dung", "de sau", "bo qua", "dung lai")):
            return "defer"
        if normalized.endswith("?"):
            return "explore"
        return "settled"

    def mode_flags(self, mode: str) -> tuple[str, ...]:
        mapping = {
            "repair": ("brief_acknowledge_correction", "answer_current_intent_directly"),
            "relational_repair": (
                "acknowledge_specific_relational_bruise",
                "answer_without_clarifying_question",
                "preserve_identity_boundary_softly",
            ),
            "execute": ("prefer_action_over_exposition",),
            "compress": ("shorten_without_losing_semantics",),
            "clarify": ("ask_narrow_clarifying_question",),
            "explore": ("answer_then_extend_carefully",),
            "defer": ("respect_pause_or_boundary",),
            "settled": ("maintain_rhythm",),
        }
        return mapping.get(mode, ("maintain_rhythm",))
