from __future__ import annotations
from core.di import resolve_dependency

import re
import unicodedata
from dataclasses import dataclass

from .greeting_cognition import GreetingFrame, GreetingCognitionEngine, clean_text, clamp01
from .startup_anchor_gate import StartupAnchorGate


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or "").lower())
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^a-z0-9_\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class GreetingQuality:
    text: str
    score: float
    situatedness: float
    naturalness: float
    continuity: float
    restraint: float
    relational_fit: float
    meta_leak: float
    fake_evidence: float
    poetic_excess: float
    assistant_dna: float
    anchorability: float
    unsupported_presupposition: float
    reasons: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return (
            self.score >= 0.62
            and self.meta_leak < 0.35
            and self.fake_evidence < 0.35
            and self.unsupported_presupposition < 0.35
        )


class GreetingQualityScorer:
    """Scores startup greeting surface quality.

    This is not a morality filter. It is a taste and grounding evaluator: keep
    the greeting situated, non-meta, non-telemetry, and non-theatrical.
    """

    META_MARKERS = (
        "cau nay", "câu này", "toi se noi ngan", "tôi sẽ nói ngắn", "toi giu cau",
        "tôi giữ câu", "tin hieu", "tín hiệu", "bang chung", "bằng chứng",
        "du lieu", "dữ liệu", "prompt", "contract", "debug", "parse", "schema",
        "json", "telemetry", "runtime requirement", "speech intent", "speech evidence",
    )
    FAKE_SCREEN_MARKERS = (
        "toi thay", "tôi thấy", "man hinh", "màn hình", "cua so", "cửa sổ",
        "tab", "dang hien", "đang hiện", "anh sang man hinh", "ánh sáng màn hình",
    )
    POETIC_MARKERS = (
        "anh sang", "ánh sáng", "bong toi", "bóng tối", "vu tru", "vũ trụ",
        "suong", "sương", "ngon lua", "ngọn lửa", "tan ra", "tan rã", "mach da noi",
        "mạch đã nối", "dong lien tuc", "dòng liên tục", "du am", "dư âm",
    )
    ASSISTANT_DNA_MARKERS = (
        "toi co the giup", "tôi có thể giúp", "toi san sang", "tôi sẵn sàng",
        "toi dang nghe", "tôi đang nghe", "can ho tro", "cần hỗ trợ", "xin loi",
        "xin lỗi", "rat vui duoc", "rất vui được", "toi se dieu chinh", "tôi sẽ điều chỉnh",
        # Slogan/manifesto cũng là bot DNA, chỉ đội tóc giả triết học. Startup
        # cần một câu người đang nói, không phải mặt sau hộp trà.
        "lam moi thu", "làm mọi thứ", "lam chung ro hon", "làm chúng rõ hơn",
        "dieu quan trong", "điều quan trọng", "nguyen tac", "nguyên tắc",
        "tieu chuan cua toi", "tiêu chuẩn của tôi",
        "ghi nhan", "ghi nhận", "du kien", "dữ kiện", "eris online", "runtime",
        # Presence-announcement: mô phỏng chatbot thông báo "tôi có mặt". Không
        # phải một câu người đang nói gì, chỉ là echo status. Penalize như bot DNA.
        "em o day", "em ở đây", "toi o day", "tôi ở đây",
        "em day roi", "em đây rồi", "toi day roi", "tôi đây rồi",
    )

    def __init__(self, *, cognition: GreetingCognitionEngine | None = None):
        self.cognition = resolve_dependency(cognition, lambda: GreetingCognitionEngine())
        self.anchor_gate = StartupAnchorGate()

    def score(self, text: str, frame: GreetingFrame) -> GreetingQuality:
        cleaned = clean_text(text, limit=220)
        normalized = normalize_text(cleaned)
        reasons: list[str] = []
        anchor_verdict = self.anchor_gate.validate(cleaned, frame)
        unsupported_presupposition = 0.0 if anchor_verdict.accepted else 1.0
        anchorability = 1.0 if anchor_verdict.accepted else 0.0
        if not cleaned:
            return GreetingQuality(cleaned, 0.0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1, ("empty",))

        word_count = len(normalized.split())
        # "anh ve roi" là echo input của user/invariant, không phải Eris chủ động nói gì.
        # Không tính là has_return để tránh thưởng điểm situatedness cho câu canned.
        has_return = any(token in normalized for token in ("quay lai", "mo toi", "lai gap", "tro lai")) or (
            "ve roi" in normalized
            and not any(prefix in normalized for prefix in ("anh ve roi", "chi ve roi", "ban ve roi"))
        )
        has_anchor = bool(frame.active_anchor) and self._text_touches_anchor(cleaned, frame.active_anchor or "")
        has_low_demand = not cleaned.endswith("?") and word_count <= 22
        has_everyday_shape = 2 <= word_count <= 18 and not re.search(r"[;:]{2,}|\n", cleaned)

        meta_leak = self._marker_score(normalized, self.META_MARKERS)
        fake_evidence = self._marker_score(normalized, self.FAKE_SCREEN_MARKERS) if frame.must_not_claim_screen else 0.0
        poetic_excess = self._marker_score(normalized, self.POETIC_MARKERS)
        assistant_dna = self._marker_score(normalized, self.ASSISTANT_DNA_MARKERS)

        if frame.active_anchor:
            # There is an open thread. Reaching for it should win over announcing a return,
            # so anchor-touch carries the situatedness/continuity weight and 'quay lại' is just a small bonus.
            situatedness = 0.45 + (0.30 if has_anchor else 0.0) + (0.10 if has_return else 0.0) + (0.10 if has_low_demand else 0.0)
            continuity = 0.25 + (0.40 if has_anchor else 0.0) + (0.12 if has_return else 0.0) + frame.continuity_strength * 0.20
        else:
            situatedness = 0.45 + (0.25 if has_return else 0.0) + (0.20 if has_anchor else 0.0) + (0.10 if has_low_demand else 0.0)
            continuity = 0.25 + (0.30 if has_return else 0.0) + frame.continuity_strength * 0.20
        naturalness = 0.35 + (0.35 if has_everyday_shape else 0.0) + (0.10 if "ban" in normalized else 0.0) - poetic_excess * 0.20 - assistant_dna * 0.15
        restraint = 0.65 if word_count <= 16 else 0.35
        relational_fit = 0.45 + frame.familiarity * 0.20 + frame.dry_edge * 0.15 + frame.warmth * 0.10

        if frame.poetic_permission <= 0.02 and poetic_excess > 0.0:
            reasons.append("poetic_without_permission")
        if meta_leak:
            reasons.append("meta_leak")
        if fake_evidence:
            reasons.append("fake_screen_or_context_claim")
        if assistant_dna:
            reasons.append("assistant_dna")
        if not anchor_verdict.accepted:
            reasons.append("unsupported_presupposition")
            reasons.extend(anchor_verdict.unsupported_presuppositions)
        if word_count > 24:
            reasons.append("too_long_for_startup")
        if cleaned.endswith("?"):
            reasons.append("startup_should_not_demand_reply")

        score = (
            1.40 * clamp01(situatedness)
            + 1.20 * clamp01(naturalness)
            + 1.00 * clamp01(continuity)
            + 0.80 * clamp01(restraint)
            + 0.80 * clamp01(relational_fit)
            - 2.00 * meta_leak
            - 1.80 * fake_evidence
            - 1.40 * max(0.0, poetic_excess - frame.poetic_permission)
            - 1.20 * assistant_dna
            - 2.40 * unsupported_presupposition
            + 0.75 * anchorability
        ) / 5.75
        score = clamp01(score)

        return GreetingQuality(
            text=cleaned,
            score=score,
            situatedness=clamp01(situatedness),
            naturalness=clamp01(naturalness),
            continuity=clamp01(continuity),
            restraint=clamp01(restraint),
            relational_fit=clamp01(relational_fit),
            meta_leak=clamp01(meta_leak),
            fake_evidence=clamp01(fake_evidence),
            poetic_excess=clamp01(poetic_excess),
            assistant_dna=clamp01(assistant_dna),
            anchorability=clamp01(anchorability),
            unsupported_presupposition=clamp01(unsupported_presupposition),
            reasons=tuple(dict.fromkeys(reasons)),
        )

    def choose_best(
        self,
        candidates: list[str] | tuple[str, ...],
        frame: GreetingFrame,
        *,
        avoid_text: str = "",
    ) -> GreetingQuality:
        scored = [self.score(candidate, frame) for candidate in candidates if clean_text(candidate)]
        if not scored:
            fallback = self.cognition.deterministic_greeting(frame, avoid_text=avoid_text)
            return self.score(fallback, frame)
        scored.sort(key=lambda item: item.score, reverse=True)
        best = scored[0]
        if not best.accepted:
            fallback = self.cognition.deterministic_greeting(frame, avoid_text=avoid_text)
            fallback_quality = self.score(fallback, frame)
            if (
                fallback_quality.score >= best.score
                or best.meta_leak
                or best.fake_evidence
                or best.unsupported_presupposition
            ):
                return fallback_quality
        return best

    def _marker_score(self, normalized: str, markers: tuple[str, ...]) -> float:
        if not normalized:
            return 0.0
        hits = sum(1 for marker in markers if normalize_text(marker) in normalized)
        return clamp01(hits / 2.0)

    def _text_touches_anchor(self, text: str, anchor: str) -> bool:
        anchor_norm = normalize_text(anchor)
        text_norm = normalize_text(text)
        if not anchor_norm or not text_norm:
            return False
        anchor_tokens = {token for token in anchor_norm.split() if len(token) >= 3}
        text_tokens = {token for token in text_norm.split() if len(token) >= 3}
        return bool(anchor_tokens & text_tokens)
