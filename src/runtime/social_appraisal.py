from __future__ import annotations
from core.di import resolve_dependency

"""Unified Social Appraisal Engine.

Mục tiêu
--------
Một user turn không nên bị nhiều module cùng tự đoán lại theo những bộ regex và
ngưỡng khác nhau. Module này tạo **một snapshot duy nhất** cho lượt hiện tại:

- trục epistemic: claim trước được ủng hộ, bị contest hay mâu thuẫn ở mức nào;
- trục relational: playful, affection, irritation, boundary, engagement...;
- provenance: mỗi score đến từ tín hiệu nào và đáng tin đến đâu;
- policy projection: downstream được phép làm gì với claim, nhưng policy không
  được quay ngược thành một "sự thật" về động cơ user.

Các module downstream chỉ đọc snapshot. Chúng có thể mở gate kỹ thuật bằng
threshold, nhưng không được tự phân loại lại user bằng keyword riêng.
"""

from dataclasses import dataclass, field
import hashlib
import os
import re
import unicodedata
from typing import Any, Iterable, Mapping

from runtime.social_stance_policy import STANCE_PUBLIC_RULES_VI


def clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(0.0, min(1.0, number))


def normalize_vi(text: str) -> str:
    raw = str(text or "").strip().lower()
    decomposed = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    raw = raw.replace("đ", "d")
    raw = re.sub(r"[^a-z0-9_./+#?\-\s]", " ", raw)
    return " ".join(raw.split())


_STOPWORDS = {
    "toi", "ban", "ban", "em", "anh", "chi", "minh", "ta", "no", "day",
    "la", "thi", "ma", "va", "voi", "cua", "mot", "cai", "do", "nay", "kia",
    "dang", "da", "se", "co", "khong", "ko", "k", "duoc", "bi", "cho", "vao",
    "ra", "len", "xuong", "ve", "gi", "sao", "the", "nhi", "nhe", "a", "ha",
    "roi", "nua", "hon", "rat", "qua", "tu", "nhu", "neu", "theo", "trong",
}


def content_tokens(text: str) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for token in normalize_vi(text).replace("?", " ").split():
        if len(token) < 3 or token in _STOPWORDS or token.isdigit() or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return tuple(result)


def _has_any(text: str, phrases: Iterable[str]) -> bool:
    return any(phrase in text for phrase in phrases)


def _extract_previous_claim(previous_assistant_text: str) -> tuple[str, tuple[str, ...]]:
    compact = re.sub(r"\s+", " ", str(previous_assistant_text or "")).strip()
    if not compact:
        return "", ()
    sentences = [
        part.strip(" -\t")
        for part in re.split(r"(?<=[.!?])\s+|\n+", compact)
        if part.strip()
    ]
    selected = sentences[0] if sentences else compact
    for sentence in sentences:
        norm = normalize_vi(sentence)
        if _has_any(norm, (" la ", " co the ", " nghia la ", " lien quan ", " dang ")):
            selected = sentence
            break
    return selected[:260], content_tokens(selected)


def _extract_user_contrast(user_text: str) -> str:
    parts = [part.strip(" .,!?:;\t") for part in re.split(r"[.!?]+", str(user_text or "")) if part.strip()]
    if not parts:
        return str(user_text or "").strip()[:140]
    preferred = [
        part for part in parts
        if _has_any(normalize_vi(part), ("toi ", "minh ", "thoi ma", "dau co", "chi "))
    ]
    return (preferred[-1] if preferred else parts[-1])[:140]


def _infer_previous_claim_kind(previous_claim: str) -> str:
    """Phân biệt fact với ấn tượng để disagreement không bị xử như correction.

    Đây chỉ là projection bảo thủ cho repair compatibility path. Nó không cố
    đọc ý định user từ keyword; nó nhìn cách ERIS đã đóng gói claim của chính
    mình. Claim có dấu hiệu ``tôi nghĩ/tôi thấy/có vẻ`` được quyền giữ như một
    ấn tượng, còn phát biểu sự kiện cụ thể vẫn phải hạ authority khi bị sửa.
    """

    norm = normalize_vi(previous_claim)
    if not norm:
        return "unknown"
    impression_markers = (
        "toi nghi", "toi thay", "theo toi", "co ve", "hinh nhu", "toi doan",
        "toi doc", "an tuong cua toi", "cam giac cua toi", "khien toi thay",
        "toi van nghi", "toi van thay",
    )
    if _has_any(norm, impression_markers):
        return "assistant_impression"
    user_state_markers = (
        "ban dang", "ban co ve", "ban muon", "ban so", "ban buon", "ban vui",
        "ban ghet", "ban thich", "ban cam thay", "ban co y", "y cua ban",
    )
    if _has_any(norm, user_state_markers):
        return "user_state_inference"
    if re.search(r"\b\d+(?:[.,]\d+)?\b", norm) or _has_any(
        norm,
        ("file ", "code ", "log ", "api ", "model ", "version ", "pid ", "port "),
    ):
        return "objective_assertion"
    return "assertion"


def _env_float(name: str, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    try:
        value = float(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True, slots=True)
class SocialPolicyThresholds:
    """Toàn bộ threshold xã hội nằm một chỗ để eval/tuning không phải săn số."""

    signal_visible: float = 0.34
    repair_active: float = 0.46
    claim_quarantine: float = 0.48
    claim_suppress: float = 0.66
    memory_write_min_authority: float = 0.52
    playful_repair: float = 0.42
    boundary_high: float = 0.55
    irritation_high: float = 0.52
    candidate_uncertainty: float = 0.34
    failure_gate: float = 0.58

    @classmethod
    def from_env(cls) -> "SocialPolicyThresholds":
        return cls(
            signal_visible=_env_float("ERIS_SOCIAL_SIGNAL_VISIBLE", 0.34),
            repair_active=_env_float("ERIS_SOCIAL_REPAIR_ACTIVE", 0.46),
            claim_quarantine=_env_float("ERIS_SOCIAL_CLAIM_QUARANTINE", 0.48),
            claim_suppress=_env_float("ERIS_SOCIAL_CLAIM_SUPPRESS", 0.66),
            memory_write_min_authority=_env_float("ERIS_SOCIAL_MEMORY_AUTHORITY_MIN", 0.52),
            playful_repair=_env_float("ERIS_SOCIAL_PLAYFUL_REPAIR", 0.42),
            boundary_high=_env_float("ERIS_SOCIAL_BOUNDARY_HIGH", 0.55),
            irritation_high=_env_float("ERIS_SOCIAL_IRRITATION_HIGH", 0.52),
            candidate_uncertainty=_env_float("ERIS_SOCIAL_CANDIDATE_UNCERTAINTY", 0.34),
            failure_gate=_env_float("ERIS_SOCIAL_FAILURE_GATE", 0.58),
        )


@dataclass(frozen=True, slots=True)
class EvidenceContribution:
    source: str
    signal: str
    weight: float
    confidence: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "signal": self.signal,
            "weight": round(float(self.weight), 4),
            "confidence": round(clamp01(self.confidence), 4),
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class SignalEstimate:
    value: float = 0.0
    confidence: float = 0.0
    evidence: tuple[EvidenceContribution, ...] = ()
    contradictions: tuple[str, ...] = ()
    ttl_turns: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": round(clamp01(self.value), 4),
            "confidence": round(clamp01(self.confidence), 4),
            "evidence": [item.to_dict() for item in self.evidence],
            "contradictions": list(self.contradictions),
            "ttl_turns": max(1, int(self.ttl_turns)),
        }


@dataclass(frozen=True, slots=True)
class ClaimEstimate:
    support: float = 0.0
    contest: float = 0.0
    contradiction: float = 0.0
    uncertainty: float = 1.0
    previous_claim_excerpt: str = ""
    user_contrast_excerpt: str = ""
    contested_terms: tuple[str, ...] = ()
    claim_kind: str = "unknown"
    provenance: tuple[EvidenceContribution, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "support": round(clamp01(self.support), 4),
            "contest": round(clamp01(self.contest), 4),
            "contradiction": round(clamp01(self.contradiction), 4),
            "uncertainty": round(clamp01(self.uncertainty), 4),
            "previous_claim_excerpt": self.previous_claim_excerpt,
            "user_contrast_excerpt": self.user_contrast_excerpt,
            "contested_terms": list(self.contested_terms),
            "claim_kind": self.claim_kind,
            "provenance": [item.to_dict() for item in self.provenance],
        }


@dataclass(frozen=True, slots=True)
class ClaimPolicy:
    """Projection kỹ thuật, không phải verdict về user.

    `authority_multiplier` hạ quyền của claim trước trong prompt/memory.
    `suppress_for_current_turn` chỉ nói downstream không được dựng tiếp trên claim
    đó ở lượt hiện tại; nó không có nghĩa user chắc chắn đang sửa hay claim chắc
    chắn sai trong mọi thế giới có thể.
    """

    authority_multiplier: float = 1.0
    memory_write_allowed: bool = True
    suppress_for_current_turn: bool = False
    stance_may_be_held: bool = False
    candidate_count_hint: int = 1
    response_posture: str = "preserve"
    expires_after_turns: int = 1
    reason: str = "insufficient_social_evidence"

    def to_dict(self) -> dict[str, Any]:
        return {
            "authority_multiplier": round(clamp01(self.authority_multiplier), 4),
            "memory_write_allowed": bool(self.memory_write_allowed),
            "suppress_for_current_turn": bool(self.suppress_for_current_turn),
            "stance_may_be_held": bool(self.stance_may_be_held),
            "candidate_count_hint": max(1, min(2, int(self.candidate_count_hint))),
            "response_posture": self.response_posture,
            "expires_after_turns": max(1, int(self.expires_after_turns)),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SocialAppraisalSnapshot:
    phase: str = "final"
    turn_fingerprint: str = ""
    correction: SignalEstimate = field(default_factory=SignalEstimate)
    confusion: SignalEstimate = field(default_factory=SignalEstimate)
    playful: SignalEstimate = field(default_factory=SignalEstimate)
    affection: SignalEstimate = field(default_factory=SignalEstimate)
    irritation: SignalEstimate = field(default_factory=SignalEstimate)
    boundary: SignalEstimate = field(default_factory=SignalEstimate)
    engagement: SignalEstimate = field(default_factory=SignalEstimate)
    intrusion: SignalEstimate = field(default_factory=SignalEstimate)
    rupture: SignalEstimate = field(default_factory=SignalEstimate)
    acceptance: SignalEstimate = field(default_factory=SignalEstimate)
    claim: ClaimEstimate = field(default_factory=ClaimEstimate)
    claim_policy: ClaimPolicy = field(default_factory=ClaimPolicy)
    thresholds: SocialPolicyThresholds = field(default_factory=SocialPolicyThresholds)

    @property
    def repair_pressure(self) -> float:
        return max(self.correction.value, self.claim.contest, self.claim.contradiction)

    @property
    def active(self) -> bool:
        return self.repair_pressure >= self.thresholds.repair_active

    @property
    def is_playful_repair(self) -> bool:
        return (
            self.active
            and self.playful.value >= self.thresholds.playful_repair
            and self.boundary.value < self.thresholds.boundary_high
            and self.irritation.value < 0.68
        )

    @property
    def failure_pressure(self) -> float:
        return max(self.repair_pressure, self.confusion.value, self.intrusion.value, self.rupture.value)

    def signal(self, name: str) -> SignalEstimate:
        return getattr(self, name, SignalEstimate())

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "turn_fingerprint": self.turn_fingerprint,
            "active": self.active,
            "repair_pressure": round(self.repair_pressure, 4),
            "failure_pressure": round(self.failure_pressure, 4),
            "is_playful_repair": self.is_playful_repair,
            "signals": {
                name: self.signal(name).to_dict()
                for name in (
                    "correction", "confusion", "playful", "affection", "irritation",
                    "boundary", "engagement", "intrusion", "rupture", "acceptance",
                )
            },
            "claim": self.claim.to_dict(),
            "claim_policy": self.claim_policy.to_dict(),
        }

    def to_prompt_context(self) -> str:
        if not self.active and max(self.playful.value, self.affection.value, self.boundary.value) < self.thresholds.signal_visible:
            return ""
        policy = self.claim_policy
        lines = [
            "UNIFIED_SOCIAL_APPRAISAL (hypotheses, not verdicts):",
            (
                f"- epistemic: correction={self.correction.value:.2f}; contest={self.claim.contest:.2f}; "
                f"contradiction={self.claim.contradiction:.2f}; uncertainty={self.claim.uncertainty:.2f}; "
                f"claim_authority={policy.authority_multiplier:.2f}."
            ),
            (
                f"- relational: playful={self.playful.value:.2f}; affection={self.affection.value:.2f}; "
                f"irritation={self.irritation.value:.2f}; boundary={self.boundary.value:.2f}; "
                f"engagement={self.engagement.value:.2f}."
            ),
            f"- response_posture={policy.response_posture}; candidate_hint={policy.candidate_count_hint}.",
        ]
        if policy.suppress_for_current_turn:
            lines.append("- Claim trước phải dừng ở lượt này; sửa nội dung gọn nhưng không tự động xin lỗi nếu chưa có lỗi quan hệ.")
        elif policy.stance_may_be_held:
            lines.append(
                "- Đây là bất đồng với một nhận định chủ quan của ERIS, không phải bằng chứng ERIS chắc chắn sai."
            )
            lines.append(f"- {STANCE_PUBLIC_RULES_VI}")
        elif policy.authority_multiplier < 0.52:
            lines.append("- Claim trước đang bị contest: hạ độ chắc, không dùng nó như fact và không ghi memory; chưa cần tự thú nhận sai.")
        if self.is_playful_repair:
            lines.append("- Có khả năng user đang vừa sửa vừa chọc: repair có duyên, không biến thành lời xin lỗi hành chính.")
        if self.boundary.value >= self.thresholds.boundary_high:
            lines.append("- Có boundary pressure đáng kể: giữ khoảng cách và không kéo gần thêm.")
        return "\n".join(lines)


class SocialAppraisalEngine:
    """Nguồn suy luận xã hội duy nhất cho hot path chat."""

    _EXPLICIT_CORRECTION = (
        "khong phai", "hieu sai", "nham roi", "sai roi", "toi dau co", "toi khong noi",
        "y toi la", "y minh la", "toi noi la", "toi bao la", "tao noi la", "tao bao la",
        "da bao la", "vua bao la", "o dau ra", "dau ra", "sao lai", "ban vua bia",
        "em vua bia", "tu dau ra", "khong dung y", "not what", "wrong", "incorrect",
    )
    _SURPRISE_OR_CHALLENGE = (
        "la sao", "sao ta", "sao vay", "sao the", "o dau ra", "ua", "toi nho", "minh nho", "dau co",
    )
    _CONTRAST = (
        "thoi ma", "chi ", "co moi", "dau co", "toi nho toi", "minh nho minh",
        "toi dang", "minh dang", "ma toi", "nhung toi",
    )
    _PLAYFUL = (
        "haha", "hehe", "hihi", "hi hi", "lol", "=))", ":))", "thoi ma", "choc", "khia", "dua", "gia vo", "ngay tho",
    )
    _IRRITATED = (
        "dcm", "dm", "vcl", "vl", "ngu", "xam", "vo ly", "bia dat", "noi linh tinh", "kho chiu", "sai hoai", "lai nua", "dung bia",
    )
    _BOUNDARY = (
        "dung nua", "thoi dung", "dung lam", "khong duoc", "de toi yen", "bo di", "khong dua", "dung treu", "dung choc", "dung bia",
    )
    _AFFECTION = (
        "de thuong", "dang yeu", "thuong", "quan tam", "quan trong", "gan gui", "thich em", "yeu em", "nho em", "muon hieu em", "tran trong em",
    )
    _INTRUSION = (
        "dung suy dien", "dung hoi", "noi khay", "moc mia", "xen vao", "overread", "mindread", "intrusive", "privacy", "dung phan tich",
    )
    _RUPTURE = (
        "kho chiu", "buc", "cay", "ghet", "tuc", "chan", "that vong",
    )
    _ACCEPTANCE = (
        # Không dùng token trần "dung": tiếng Việt bỏ dấu khiến "đúng" và
        # "đừng" cùng thành "dung", một trò đùa chính tả không nên được quyền
        # cập nhật relationship state.
        "dung roi", "dung y", "dung vay", "chuan", "hop ly", "ok", "oke",
        "duoc roi", "ngon", "on roi", "tiep tuc",
    )
    _MUNDANE_IRONY = ("tho", "ngoi yen", "dung yen", "ton tai", "uong nuoc", "nhin", "ngu")

    def __init__(self, thresholds: SocialPolicyThresholds | None = None) -> None:
        self.thresholds = resolve_dependency(thresholds, SocialPolicyThresholds.from_env)

    def appraise(
        self,
        *,
        user_text: str,
        previous_assistant_text: str = "",
        previous_user_text: str = "",
        affect_signal: Any = None,
        relational_feedback: Any = None,
        phase: str = "final",
    ) -> SocialAppraisalSnapshot:
        user_norm = normalize_vi(user_text)
        previous_claim, previous_tokens = _extract_previous_claim(previous_assistant_text)
        claim_kind = _infer_previous_claim_kind(previous_claim)
        fingerprint = hashlib.sha1(
            f"{phase}|{user_norm}|{normalize_vi(previous_assistant_text)}".encode("utf-8", errors="ignore")
        ).hexdigest()[:16]
        if not user_norm:
            return SocialAppraisalSnapshot(phase=phase, turn_fingerprint=fingerprint, thresholds=self.thresholds)

        user_tokens = set(content_tokens(user_text))
        previous_token_set = set(previous_tokens)
        contested = tuple(token for token in previous_tokens if token in user_tokens)[:8]
        prior_reference = clamp01(len(contested) / max(1.0, min(4.0, float(len(previous_token_set) or 1))))

        explicit = 1.0 if _has_any(user_norm, self._EXPLICIT_CORRECTION) else 0.0
        surprise = 1.0 if _has_any(user_norm, self._SURPRISE_OR_CHALLENGE) else 0.0
        contrast = 1.0 if _has_any(user_norm, self._CONTRAST) else 0.0
        playful_surface = 1.0 if _has_any(user_norm, self._PLAYFUL) else 0.0
        irritation_surface = 1.0 if _has_any(user_norm, self._IRRITATED) else 0.0
        boundary_surface = 1.0 if _has_any(user_norm, self._BOUNDARY) else 0.0
        affection_surface = 1.0 if _has_any(user_norm, self._AFFECTION) else 0.0
        intrusion_surface = 1.0 if _has_any(user_norm, self._INTRUSION) else 0.0
        rupture_surface = 1.0 if _has_any(user_norm, self._RUPTURE) else 0.0
        acceptance_surface = 1.0 if _has_any(user_norm, self._ACCEPTANCE) else 0.0
        has_replacement_shape = _has_any(user_norm, ("y toi la", "y minh la", "toi noi la", "toi bao la"))
        # Mẫu rất tự nhiên kiểu "tôi có buồn đâu" không chứa đúng chuỗi
        # ``đâu có``. Nếu bỏ sót, Eris sẽ coi lời phủ định self-report như không
        # có gì xảy ra rồi tiếp tục khẳng định, một kiểu lì không hề thông minh.
        self_report_denial = bool(
            re.search(r"\b(?:toi|minh)\s+(?:co\s+)?[a-z0-9_\s]{1,48}\s+dau\b", user_norm)
        )
        negative_surface = bool(
            _has_any(user_norm, ("khong phai", "sai", "nham", "o dau ra", "dau co", "khong dung"))
            or self_report_denial
        )
        # "Ừ đúng rồi, ý tôi là pixel" là làm rõ có tính ủng hộ, không phải
        # contradiction. Giữ cả hai hypothesis nhưng giảm quyền của correction.
        affirming_clarification = bool(acceptance_surface and has_replacement_shape and not negative_surface)
        explicit_for_contest = 0.16 if affirming_clarification else max(explicit, 0.82 if self_report_denial else 0.0)
        has_question = "?" in str(user_text or "") or _has_any(user_norm, ("la sao", "sao ta", "sao vay"))

        mundane_irony = bool(
            re.search(r"\b(?:toi|minh)\s+nho\s+(?:toi|minh)\b.*\bthoi\s+ma\b", user_norm)
            and _has_any(user_norm, self._MUNDANE_IRONY)
        )

        mismatch = clamp01(
            explicit_for_contest * 0.72
            + (prior_reference * 0.36 if surprise else prior_reference * 0.12)
            + contrast * 0.32
            + (0.24 if mundane_irony else 0.0)
            + (0.08 if has_question and contrast else 0.0)
            + intrusion_surface * 0.72
            + boundary_surface * 0.54
        )
        correction_value = clamp01(
            explicit_for_contest * 0.78
            + contrast * 0.28
            + prior_reference * 0.24
            + (0.22 if mundane_irony else 0.0)
            + intrusion_surface * 0.62
            + boundary_surface * 0.44
        )
        confusion_value = min(0.82, clamp01(
            surprise * 0.56 + (0.18 if has_question else 0.0) + max(0.0, mismatch - correction_value) * 0.28
        ))

        playful_from_affect = clamp01(getattr(affect_signal, "playfulness", 0.0))
        # Một marker không được đẩy score lên 1.0. Ta giữ hypothesis mạnh vừa
        # đủ, còn confidence nằm riêng trong provenance.
        playful_value = clamp01(max(
            playful_surface * 0.48,
            0.64 if mundane_irony else 0.0,
            0.46 if contrast and surprise and explicit_for_contest < 0.5 else 0.0,
            playful_from_affect * 0.75,
        ))
        conflict = clamp01(getattr(affect_signal, "conflict", 0.0))
        irritation_value = clamp01(irritation_surface * 0.70 + conflict * 0.38)
        boundary_value = clamp01(
            boundary_surface * 0.82 + clamp01(getattr(affect_signal, "romantic_boundary_probe", 0.0)) * 0.18
        )

        intimacy = clamp01(getattr(affect_signal, "intimacy", 0.0))
        attachment = clamp01(getattr(affect_signal, "attachment", 0.0))
        idiomatic_remember = bool(re.search(r"\b(?:toi|minh)\s+nho\s+(?:toi|minh)\b", user_norm))
        affection_value = clamp01(
            affection_surface * 0.58 + intimacy * 0.30 + attachment * 0.26
            - (0.20 if idiomatic_remember and not affection_surface else 0.0)
        )

        feedback_detected = bool(getattr(relational_feedback, "detected", False))
        feedback_intensity = clamp01(getattr(relational_feedback, "intensity", 0.0)) if feedback_detected else 0.0
        feedback_repair = clamp01(getattr(relational_feedback, "repair_needed", 0.0)) if feedback_detected else 0.0
        irritation_value = max(irritation_value, feedback_intensity * 0.45)
        intrusion_value = clamp01(intrusion_surface * 0.82 + clamp01(getattr(relational_feedback, "intrusion_risk", 0.0)) * 0.45)
        rupture_value = clamp01(rupture_surface * 0.68 + feedback_repair * 0.38 + irritation_value * boundary_value * 0.25)

        previous_user_overlap = 0.0
        if previous_user_text:
            old = set(content_tokens(previous_user_text))
            if old and user_tokens:
                previous_user_overlap = len(old & user_tokens) / max(1, len(old | user_tokens))
        engagement_value = min(0.78, clamp01(
            0.22 + min(0.24, len(user_norm.split()) / 55.0) + mismatch * 0.15 + playful_value * 0.16 + previous_user_overlap * 0.16
        ))
        acceptance_value = clamp01(
            acceptance_surface * 0.72
            + (0.18 if affirming_clarification else 0.0)
            - correction_value * (0.18 if affirming_clarification else 0.55)
            - confusion_value * 0.30
        )

        support = clamp01(
            acceptance_value * 0.78
            + (0.52 if affirming_clarification else 0.0)
            + (0.20 if prior_reference >= 0.25 and correction_value < 0.25 else 0.0)
        )
        contradiction = clamp01(
            explicit_for_contest * 0.82 + contrast * 0.30 + (0.20 if mundane_irony else 0.0)
        )
        contest = max(mismatch, correction_value, confusion_value * 0.62)

        relational_values = sorted((playful_value, affection_value, irritation_value, boundary_value), reverse=True)
        axis_gap = relational_values[0] - relational_values[1] if len(relational_values) > 1 else relational_values[0]
        uncertainty = clamp01(
            (0.42 if 0.42 <= contest <= 0.78 else 0.12)
            + (0.28 if axis_gap < 0.16 and relational_values[0] >= 0.32 else 0.0)
            + (0.18 if prior_reference < 0.18 and explicit < 0.5 else 0.0)
            + (0.14 if support >= 0.35 and contest >= 0.35 else 0.0)
        )

        evidence: list[EvidenceContribution] = []
        def add(source: str, signal: str, weight: float, confidence: float, note: str = "") -> None:
            evidence.append(EvidenceContribution(source, signal, weight, confidence, note))

        if explicit and not affirming_clarification:
            add("surface", "correction", 0.78, 0.88, "explicit_correction_surface")
        elif self_report_denial:
            add("surface", "correction", 0.64, 0.82, "self_report_denial_surface")
        if affirming_clarification:
            add("surface", "acceptance", 0.72, 0.76, "affirming_clarification")
        if surprise:
            add("surface", "confusion", 0.56, 0.68, "surprise_or_challenge")
        if contrast:
            add("surface", "claim_contest", 0.32, 0.74, "self_report_contrast")
        if prior_reference > 0.0:
            add("turn_overlap", "claim_reference", prior_reference, 0.72, f"prior_claim_overlap:{prior_reference:.2f}")
        if mundane_irony:
            add("discourse_shape", "playful", 0.48, 0.72, "mundane_ironic_understatement")
        if playful_surface or playful_from_affect > 0.20:
            add("surface+affect", "playful", playful_value, 0.64, "playful_relational_axis")
        if affection_surface or affection_value > 0.35:
            add("surface+affect", "affection", affection_value, 0.58, "affectionate_relational_axis")
        if irritation_value > 0.35:
            add("surface+affect", "irritation", irritation_value, 0.66, "irritation_axis")
        if boundary_value > 0.35:
            add("surface+affect", "boundary", boundary_value, 0.78, "boundary_axis")
        add("turn_continuity", "engagement", engagement_value, 0.52, "continued_engagement")

        def signal(name: str, value: float, *, ttl: int = 1, contradiction_notes: tuple[str, ...] = ()) -> SignalEstimate:
            allowed = {name}
            if name in {"correction", "confusion"}:
                allowed.add("claim_reference")
            selected = tuple(item for item in evidence if item.signal in allowed)
            confidence = 0.0 if value <= 0.0 and not selected else clamp01(0.22 + len(selected) * 0.12 + value * 0.34)
            return SignalEstimate(value=clamp01(value), confidence=confidence, evidence=selected, contradictions=contradiction_notes, ttl_turns=ttl)

        authority = clamp01(1.0 - max(contest * 0.72, contradiction * 0.92) + support * 0.20)

        # User phản bác một ấn tượng không đồng nghĩa claim đã bị chứng minh sai.
        # Chỉ suppress khi có boundary/intrusion rõ hoặc user cung cấp replacement
        # cho một assertion mang tính sự kiện. Các claim chủ quan được giữ như
        # impression, nhưng không được ghi memory như fact về user.
        subjective_claim = claim_kind in {"assistant_impression", "user_state_inference"}
        active_contest = contest >= self.thresholds.repair_active
        hard_boundary = (
            boundary_value >= self.thresholds.boundary_high
            or intrusion_value >= self.thresholds.boundary_high
        )
        factual_replacement = bool(
            has_replacement_shape
            and explicit >= 0.5
            and claim_kind in {"objective_assertion", "assertion"}
            and contradiction >= self.thresholds.claim_suppress
        )
        suppress = bool(active_contest and (hard_boundary or factual_replacement))
        stance_may_be_held = bool(active_contest and subjective_claim and not suppress)
        if stance_may_be_held:
            authority = max(authority, 0.56)
        memory_allowed = authority >= self.thresholds.memory_write_min_authority and not suppress
        if active_contest:
            # Bất đồng đang mở không nên đóng băng thành fact trong memory, kể cả
            # khi ERIS vẫn được quyền giữ ấn tượng cá nhân.
            memory_allowed = False
        if suppress and boundary_value >= self.thresholds.boundary_high:
            posture = "boundary_repair"
        elif suppress and playful_value >= max(self.thresholds.playful_repair, irritation_value - 0.08):
            posture = "playful_self_correction"
        elif suppress and irritation_value >= self.thresholds.irritation_high:
            posture = "direct_correction"
        elif suppress:
            posture = "grounded_self_correction"
        elif stance_may_be_held and playful_value >= self.thresholds.playful_repair:
            posture = "playful_stance_hold"
        elif stance_may_be_held:
            posture = "soft_stance_hold"
        elif authority < self.thresholds.memory_write_min_authority:
            posture = "verify_without_apology"
        elif playful_value >= 0.48:
            posture = "acknowledge_tease"
        else:
            posture = "preserve"
        candidate_hint = 2 if contest >= self.thresholds.repair_active and uncertainty >= self.thresholds.candidate_uncertainty else 1
        reason = (
            "claim_suppressed_for_current_turn" if suppress
            else "subjective_stance_contested_but_preserved" if stance_may_be_held
            else "claim_authority_lowered" if authority < self.thresholds.memory_write_min_authority
            else "claim_preserved_with_soft_social_context"
        )

        claim = ClaimEstimate(
            support=support,
            contest=contest,
            contradiction=contradiction,
            uncertainty=uncertainty,
            previous_claim_excerpt=previous_claim,
            user_contrast_excerpt=_extract_user_contrast(user_text),
            contested_terms=contested,
            claim_kind=claim_kind,
            provenance=tuple(evidence),
        )
        policy = ClaimPolicy(
            authority_multiplier=authority,
            memory_write_allowed=memory_allowed,
            suppress_for_current_turn=suppress,
            stance_may_be_held=stance_may_be_held,
            candidate_count_hint=candidate_hint,
            response_posture=posture,
            expires_after_turns=1,
            reason=reason,
        )

        return SocialAppraisalSnapshot(
            phase=phase,
            turn_fingerprint=fingerprint,
            correction=signal("correction", correction_value),
            confusion=signal("confusion", confusion_value),
            playful=signal("playful", playful_value, ttl=2),
            affection=signal("affection", affection_value, ttl=2),
            irritation=signal("irritation", irritation_value, ttl=2),
            boundary=signal("boundary", boundary_value, ttl=3),
            engagement=signal("engagement", engagement_value, ttl=3),
            intrusion=signal("intrusion", intrusion_value, ttl=2),
            rupture=signal("rupture", rupture_value, ttl=3),
            acceptance=signal("acceptance", acceptance_value),
            claim=claim,
            claim_policy=policy,
            thresholds=self.thresholds,
        )


def ensure_social_appraisal_engine(host: Any) -> SocialAppraisalEngine:
    engine = getattr(host, "social_appraisal_engine", None)
    if isinstance(engine, SocialAppraisalEngine):
        return engine
    engine = SocialAppraisalEngine()
    try:
        host.social_appraisal_engine = engine
    except Exception:
        pass
    return engine


def social_snapshot_from_any(value: Any) -> SocialAppraisalSnapshot | None:
    if isinstance(value, SocialAppraisalSnapshot):
        return value
    if value is None:
        return None
    snapshot = getattr(value, "social_snapshot", None) or getattr(value, "social_appraisal", None)
    return snapshot if isinstance(snapshot, SocialAppraisalSnapshot) else None


__all__ = [
    "ClaimEstimate",
    "ClaimPolicy",
    "EvidenceContribution",
    "SignalEstimate",
    "SocialAppraisalEngine",
    "SocialAppraisalSnapshot",
    "SocialPolicyThresholds",
    "clamp01",
    "content_tokens",
    "ensure_social_appraisal_engine",
    "normalize_vi",
    "social_snapshot_from_any",
]
