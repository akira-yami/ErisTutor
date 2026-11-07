from __future__ import annotations

"""Chọn câu public từ nhiều câu nháp.

Model nhỏ thường không thiếu "ý" bằng thiếu tai biên tập: một lần sinh có thể
ra câu sáo, câu thứ ba lại có nhịp rất ổn. Tầng này không viết persona thay
model; nó chỉ nghe nhiều candidate, chấm các lỗi độ-người/rhythm/grounding,
rồi chọn câu ít tệ nhất. Nếu không có câu đủ sạch, trả rỗng để tầng trên được
im hoặc body-only.
"""

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

try:
    from cortex.banned_filler import HOLLOW_PRESENCE_LINES, STALE_MICRO_TEMPLATES
except Exception:  # pragma: no cover - partial import trong test tối giản
    HOLLOW_PRESENCE_LINES = ()
    STALE_MICRO_TEMPLATES = ()

try:
    from runtime.utterance_evaluator import UtteranceEvaluator
except Exception:  # pragma: no cover
    UtteranceEvaluator = None


_INTERNAL_MARKERS = (
    "json",
    "debug",
    "telemetry",
    "runtime",
    "evidence",
    "signal",
    "prompt",
    "schema",
    "contract",
    "metadata",
    "system",
)

_CORPORATE_HELPER = (
    "tôi có thể giúp",
    "cần tôi hỗ trợ",
    "tôi sẽ hỗ trợ",
    "sẵn sàng hỗ trợ",
    "bạn cần gì",
    "bạn cần gì",
)

_BROAD_QUESTIONS = (
    "bạn đang làm gì",
    "bạn muốn nói gì",
    "bạn cần gì",
    "có gì không",
    "nói gì đi",
    "kể tôi nghe",
)

_SCREEN_CLAIM_MARKERS = (
    "tôi thấy",
    "màn hình",
    "trên màn",
    "cửa sổ",
    "tab ",
    "đang mở",
    "đang code",
    "đang làm",
)

_STATE_WORDS = (
    "chợt",
    "hơi",
    "lạ",
    "kỳ",
    "ngứa",
    "cấn",
    "nhớ",
    "nghĩ",
    "ghen",
    "dỗi",
    "lạnh",
    "mềm",
    "im",
    "yên",
    "ngại",
)

_VI_DIACRITIC_RE = re.compile(r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)


@dataclass(frozen=True, slots=True)
class CandidateScore:
    text: str
    score: float
    reasons: tuple[str, ...] = ()
    rejected: bool = False


@dataclass(frozen=True, slots=True)
class UtteranceSelection:
    text: str = ""
    score: float = 0.0
    reasons: tuple[str, ...] = ()
    rejected: tuple[CandidateScore, ...] = field(default_factory=tuple)


def _normalize_ascii(text: Any) -> str:
    raw = str(text or "").strip().lower()
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = raw.replace("đ", "d").replace("Đ", "d")
    raw = re.sub(r"[^a-z0-9_./:+\-\s?]", " ", raw)
    return " ".join(raw.split())


def _words(text: str) -> list[str]:
    return re.findall(r"[0-9A-Za-zÀ-ỹ]+", text or "", flags=re.UNICODE)


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(marker and marker in lowered for marker in markers)


class UtteranceSelector:
    """Deterministic scorer cho câu nháp public.

    Các điểm cộng/trừ cố tình nhỏ và cộng dồn. Mục tiêu không phải biến scorer
    thành nhà văn, mà là loại các failure mode làm Eris nghe như bot support:
    status ping, câu hỏi rộng, bịa màn hình, lộ contract, và lặp câu cũ.
    """

    def __init__(self, evaluator: Any | None = None) -> None:
        if evaluator is not None:
            self.evaluator = evaluator
        elif UtteranceEvaluator is not None:
            try:
                self.evaluator = UtteranceEvaluator()
            except Exception:
                self.evaluator = None
        else:
            self.evaluator = None

    def choose_best(
        self,
        candidates: tuple[str, ...] | list[str],
        *,
        channel: str = "micro_presence",
        user_text: str = "",
        avoid_text: str = "",
        context: dict[str, Any] | None = None,
        must_surface: bool = False,
        min_score: float = 0.54,
        snapshot: Any = None,
    ) -> UtteranceSelection:
        unique: list[str] = []
        seen: set[str] = set()
        for item in candidates or ():
            text = re.sub(r"\s+", " ", str(item or "")).strip().strip("\"'")
            if not text:
                continue
            key = _normalize_ascii(text)
            if key and key not in seen:
                unique.append(text)
                seen.add(key)

        scored = tuple(
            self.score_candidate(
                text,
                channel=channel,
                user_text=user_text,
                avoid_text=avoid_text,
                context=context,
                snapshot=snapshot,
            )
            for text in unique
        )
        viable = [item for item in scored if not item.rejected]
        if not viable:
            return UtteranceSelection(
                text="",
                score=0.0,
                reasons=("no_viable_candidate",),
                rejected=scored,
            )

        best = max(viable, key=lambda item: item.score)
        threshold = min_score if must_surface else max(min_score, 0.58)
        if best.score < threshold:
            return UtteranceSelection(
                text="",
                score=best.score,
                reasons=("best_below_threshold", *best.reasons),
                rejected=scored,
            )
        return UtteranceSelection(text=best.text, score=best.score, reasons=best.reasons, rejected=scored)

    def score_candidate(
        self,
        text: str,
        *,
        channel: str = "micro_presence",
        user_text: str = "",
        avoid_text: str = "",
        context: dict[str, Any] | None = None,
        snapshot: Any = None,
    ) -> CandidateScore:
        raw = re.sub(r"\s+", " ", str(text or "")).strip()
        if not raw:
            return CandidateScore(text="", score=0.0, reasons=("empty",), rejected=True)

        ctx = context or {}
        norm = _normalize_ascii(raw)
        reasons: list[str] = []
        score = 0.50
        rejected = False

        word_count = len(_words(raw))
        if 3 <= word_count <= 14:
            score += 0.14
            reasons.append("compact")
        elif word_count <= 2:
            score -= 0.22
            reasons.append("too_thin")
        elif word_count > 22:
            score -= 0.18
            reasons.append("too_long")

        if _VI_DIACRITIC_RE.search(raw):
            score += 0.05
            reasons.append("vi_diacritics")
        else:
            score -= 0.03
            reasons.append("no_diacritics")

        if any(marker in norm for marker in _INTERNAL_MARKERS):
            score -= 0.35
            rejected = True
            reasons.append("internal_surface")

        if _has_any(raw, _CORPORATE_HELPER):
            score -= 0.32
            reasons.append("helper_register")

        hollow = tuple(str(item).lower() for item in (*HOLLOW_PRESENCE_LINES, *STALE_MICRO_TEMPLATES))
        if any(item and item in raw.lower() for item in hollow):
            score -= 0.26
            reasons.append("stale_presence_template")

        if any(marker in norm for marker in _BROAD_QUESTIONS):
            score -= 0.22
            reasons.append("broad_question")

        if "bạn" in raw.lower():
            score += 0.04
            reasons.append("relational_address")

        if any(word in raw.lower() for word in _STATE_WORDS):
            score += 0.10
            reasons.append("state_colored")

        if self._near_duplicate(raw, avoid_text):
            score -= 0.30
            reasons.append("near_duplicate")

        surface_contract = str(ctx.get("surface_contract", "") or "")
        must_not_claim_screen = (
            "must_not_claim_screen" in surface_contract
            or str(ctx.get("must_not_claim_screen", "") or "").lower() == "true"
            or str(ctx.get("thin_presence_no_screen", "") or "").lower() == "true"
        )
        if must_not_claim_screen and any(marker in norm for marker in _SCREEN_CLAIM_MARKERS):
            score -= 0.34
            reasons.append("screen_claim_without_permission")

        topic_seed = str(ctx.get("topic_seed", "") or "")
        reaction_seed = str(ctx.get("reaction_seed", "") or "")
        if topic_seed or reaction_seed:
            overlap = self._token_overlap(raw, f"{topic_seed} {reaction_seed}")
            if overlap > 0.0:
                score += min(0.12, overlap)
                reasons.append("seed_overlap")

        if self.evaluator is not None:
            try:
                evaluation = self.evaluator.evaluate(
                    raw,
                    channel=channel,
                    user_text=user_text,
                    snapshot=snapshot,
                )
                if evaluation.verdict == "drop":
                    rejected = True
                    score = min(score, evaluation.quality)
                    reasons.append(f"evaluator_drop:{evaluation.reason}")
                elif evaluation.verdict == "rewrite":
                    score = min(score, evaluation.quality)
                    reasons.append(f"evaluator_rewrite:{evaluation.reason}")
                else:
                    score += 0.04
                    reasons.append("evaluator_allow")
            except Exception:
                pass

        score = max(0.0, min(1.0, score))
        if score < 0.22:
            rejected = True
        return CandidateScore(text=raw, score=score, reasons=tuple(dict.fromkeys(reasons)), rejected=rejected)

    def _near_duplicate(self, text: str, avoid_text: str) -> bool:
        avoid = re.sub(r"\s+", " ", str(avoid_text or "")).strip()
        if not avoid:
            return False
        left = _normalize_ascii(text)
        right = _normalize_ascii(avoid)
        if not left or not right:
            return False
        if left in right or right in left:
            return True
        return difflib.SequenceMatcher(a=left, b=right).ratio() >= 0.78

    def _token_overlap(self, text: str, anchor: str) -> float:
        left = {word for word in _normalize_ascii(text).split() if len(word) >= 3}
        right = {word for word in _normalize_ascii(anchor).split() if len(word) >= 3}
        if not left or not right:
            return 0.0
        return len(left & right) / max(1, len(left))


__all__ = ["CandidateScore", "UtteranceSelection", "UtteranceSelector"]
