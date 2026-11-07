# -*- coding: utf-8 -*-
"""local/open-model critic cho kết quả Tutor research.

Đây là lớp P1 rẻ theo PDF: Local model không đi web, không tự bịa citation, chỉ đọc
blob Tutor/Gemini Web đã trả rồi chấm lại. Mục tiêu là biến "Tutor nói vậy" thành
một critique có cấu trúc: claim nào dùng được, điểm nào yếu, có mâu thuẫn không,
và nên surface dè dặt tới đâu.

Không bắt buộc phải có một local model cụ thể. Nếu runtime truyền `critic_fn` thì module này
ép nó trả JSON ngắn; nếu model trả bừa, parser rơi về heuristic. Thế là con người
vẫn có một hàng rào, dù model lại biểu diễn nghệ thuật ngoặc nhọn.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

try:  # pragma: no cover
    from cortex.tutor.secret_scrubber import scrub_secrets
except Exception:  # pragma: no cover
    from .secret_scrubber import scrub_secrets  # type: ignore

CriticFn = Callable[[str], str]

_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)
_CONTRADICTION_RE = re.compile(
    r"\b("
    r"mâu\s*thuẫn|trái\s+ngược|không\s+thống\s+nhất|xung\s+đột|"
    r"contested|conflict(?:ing)?|contradict(?:s|ion|ory)?|disagree(?:s|ment)?|"
    r"however|but|tuy\s+nhiên|nhưng"
    r")\b",
    re.IGNORECASE,
)
_UNCERTAIN_RE = re.compile(
    r"\b(chưa\s+chắc|không\s+rõ|có\s+thể|chưa\s+xác\s+minh|thiếu\s+nguồn|không\s+có\s+nguồn|might|maybe|unclear)\b",
    re.IGNORECASE,
)
_SOURCE_HINT_RE = re.compile(r"\b(nguồn|source|tham\s+khảo|link|citation)\b", re.IGNORECASE)


@dataclass(slots=True)
class LocalResearchCritique:
    """Critique có cấu trúc từ local/open-model lane hoặc heuristic fallback."""

    usable_summary: str = ""
    supported_points: list[str] = field(default_factory=list)
    weak_points: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    sources_seen: list[str] = field(default_factory=list)
    confidence: str = "low"  # low | medium | high
    caution: str = ""
    used_model: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "usable_summary": self.usable_summary,
            "supported_points": list(self.supported_points),
            "weak_points": list(self.weak_points),
            "contradictions": list(self.contradictions),
            "sources_seen": list(self.sources_seen),
            "confidence": self.confidence,
            "caution": self.caution,
            "used_model": bool(self.used_model),
        }


def build_local_critic_prompt(topic: object, raw_answer: object, *, max_chars: int = 1800) -> str:
    """Prompt ngắn cho local/open-model critic.

    Không bảo local model search. Nó chỉ kiểm văn bản Tutor trả về. Output JSON để relay
    parse được và không biến câu trả lời thành một bài tùy bút có dấu ngoặc vuông.
    """

    topic_text = _clip_words(scrub_secrets(topic), 80)
    answer_text = scrub_secrets(raw_answer)
    budget = max(600, int(max_chars or 1800))
    header = (
        "Bạn là local-model critic cục bộ cho Eris. KHÔNG search web, KHÔNG thêm fact mới. "
        "Chỉ đọc câu trả lời Tutor/Gemini bên dưới, tách phần dùng được và phần yếu. "
        "Nếu thiếu nguồn hoặc có mâu thuẫn, nói rõ. Trả JSON hợp lệ, không thêm lời dẫn.\n"
        "Schema: {\"usable_summary\": str, \"supported_points\": [str], \"weak_points\": [str], "
        "\"contradictions\": [str], \"sources_seen\": [str], \"confidence\": \"low|medium|high\", \"caution\": str}\n"
        f"Chủ đề: {topic_text}\nTutor/Gemini trả lời:\n"
    )
    tail = "\nJSON:"
    remain = max(200, budget - len(header) - len(tail))
    return (header + answer_text[:remain] + tail)[:budget]


def run_local_research_critic(
    topic: object,
    raw_answer: object,
    *,
    critic_fn: Optional[CriticFn] = None,
    max_chars: int = 1800,
) -> LocalResearchCritique:
    """Chạy local critic nếu có, rồi merge với heuristic guard.

    Hàm luôn trả critique. Nếu model không có hoặc trả rác, heuristic vẫn tách được
    nguồn/điểm yếu/mâu thuẫn cơ bản. Một lớp critic mà fail kín là lớp trang điểm,
    không phải kiến trúc.
    """

    raw = scrub_secrets(raw_answer)
    heuristic = heuristic_research_critique(topic, raw)
    if critic_fn is None or not raw.strip():
        return heuristic

    try:
        prompt = build_local_critic_prompt(topic, raw, max_chars=max_chars)
        generated = scrub_secrets(critic_fn(prompt))
        parsed = parse_local_critic_response(generated)
        if parsed is None:
            return heuristic
        parsed.used_model = True
        return merge_critiques(parsed, heuristic)
    except Exception:
        return heuristic


def parse_local_critic_response(text: object) -> LocalResearchCritique | None:
    """Parse JSON từ local model, kể cả khi nó bọc trong code fence."""

    raw = scrub_secrets(text).strip()
    if not raw:
        return None
    candidates = []
    fence = _CODE_FENCE_RE.search(raw)
    if fence:
        candidates.append(fence.group(1).strip())
    obj = _JSON_OBJECT_RE.search(raw)
    if obj:
        candidates.append(obj.group(0).strip())
    candidates.append(raw)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        return LocalResearchCritique(
            usable_summary=_clean_text(data.get("usable_summary", ""), limit=700),
            supported_points=_clean_list(data.get("supported_points", []), limit_each=260, max_items=6),
            weak_points=_clean_list(data.get("weak_points", []), limit_each=260, max_items=5),
            contradictions=_clean_list(data.get("contradictions", []), limit_each=260, max_items=4),
            sources_seen=_clean_list(data.get("sources_seen", []), limit_each=220, max_items=6),
            confidence=_normalize_confidence(data.get("confidence", "low")),
            caution=_clean_text(data.get("caution", ""), limit=260),
            used_model=True,
        )
    return None


def heuristic_research_critique(topic: object, raw_answer: object) -> LocalResearchCritique:
    """Critique fallback không cần model."""

    raw = scrub_secrets(raw_answer)
    sources = _extract_sources(raw)
    weak: list[str] = []
    contradictions: list[str] = []
    supported: list[str] = []

    for sent in _split_sentences(raw):
        clean = _clean_text(sent, limit=260)
        if not clean:
            continue
        if _CONTRADICTION_RE.search(clean):
            contradictions.append(clean)
        if _UNCERTAIN_RE.search(clean):
            weak.append(clean)
        if _URL_RE.search(sent) or (sources and len(supported) < 4 and not _UNCERTAIN_RE.search(clean)):
            supported.append(_clean_text(_URL_RE.sub("", clean), limit=260))

    summary = _first_meaningful_summary(raw)
    confidence = "low"
    if len(sources) >= 2 and not weak and not contradictions:
        confidence = "high"
    elif sources:
        confidence = "medium"
    caution = ""
    if not sources:
        caution = "Tutor không đưa nguồn rõ; chỉ xem như gợi ý."
    elif contradictions:
        caution = "Có dấu hiệu mâu thuẫn, cần surface dè dặt."
    elif weak:
        caution = "Có điểm chưa chắc, không nên chốt mạnh."

    return LocalResearchCritique(
        usable_summary=summary,
        supported_points=_dedupe(supported)[:5],
        weak_points=_dedupe(weak)[:4],
        contradictions=_dedupe(contradictions)[:3],
        sources_seen=_dedupe(sources)[:6],
        confidence=confidence,
        caution=caution,
        used_model=False,
    )


def merge_critiques(primary: LocalResearchCritique, fallback: LocalResearchCritique) -> LocalResearchCritique:
    """Merge model critique với heuristic, heuristic được quyền kéo confidence xuống."""

    summary = primary.usable_summary or fallback.usable_summary
    supported = _dedupe(list(primary.supported_points) + list(fallback.supported_points))[:6]
    weak = _dedupe(list(primary.weak_points) + list(fallback.weak_points))[:5]
    contradictions = _dedupe(list(primary.contradictions) + list(fallback.contradictions))[:4]
    sources = _dedupe(list(primary.sources_seen) + list(fallback.sources_seen))[:6]
    confidence = _lower_confidence(primary.confidence, fallback.confidence)
    if contradictions and confidence == "high":
        confidence = "medium"
    if not sources:
        confidence = "low"
    caution = primary.caution or fallback.caution
    if contradictions and "mâu thuẫn" not in caution.lower():
        caution = (caution + "; " if caution else "") + "Có dấu hiệu mâu thuẫn/cần đối chiếu."
    if not sources and "nguồn" not in caution.lower():
        caution = (caution + "; " if caution else "") + "Thiếu nguồn rõ."
    return LocalResearchCritique(
        usable_summary=summary,
        supported_points=supported,
        weak_points=weak,
        contradictions=contradictions,
        sources_seen=sources,
        confidence=confidence,
        caution=caution[:260],
        used_model=bool(primary.used_model),
    )


def _extract_sources(text: str) -> list[str]:
    out: list[str] = []
    for url in _URL_RE.findall(text or ""):
        out.append(url.rstrip(".,;"))
    for line in str(text or "").splitlines():
        clean = line.strip(" -*•\t")
        if not clean or _URL_RE.search(clean):
            continue
        # Chỉ coi là nguồn text nếu đó là dòng nguồn thật, không phải câu kiểu
        # "chưa có nguồn rõ". Regex mà nhặt câu phủ định làm citation thì đúng là
        # tự ký giấy chứng nhận hề.
        if re.match(r"^(nguồn|source|sources|tham\s+khảo|link)\b", clean, re.IGNORECASE) and not _UNCERTAIN_RE.search(clean):
            out.append(_clean_text(clean, limit=220))
    return _dedupe(out)


def _first_meaningful_summary(text: str) -> str:
    lines: list[str] = []
    for line in str(text or "").splitlines():
        clean = line.strip(" \t-*•0123456789.)")
        if not clean:
            continue
        if _SOURCE_HINT_RE.search(clean) and len(clean) < 80:
            break
        lines.append(clean)
        if len(" ".join(lines).split()) >= 95:
            break
    if not lines:
        lines = _split_sentences(text)[:3]
    return _clip_words(" ".join(lines), 120)


def _split_sentences(text: str) -> list[str]:
    raw = re.sub(r"\s+", " ", str(text or "").strip())
    if not raw:
        return []
    return [p.strip() for p in re.split(r"(?<=[.!?。！？])\s+|\s+[•]\s+", raw) if p.strip()]


def _clean_list(value: Any, *, limit_each: int = 260, max_items: int = 6) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, Iterable):
        items = list(value)
    else:
        items = []
    return _dedupe(_clean_text(item, limit=limit_each) for item in items)[:max_items]


def _clean_text(value: Any, *, limit: int = 300) -> str:
    text = scrub_secrets(value)
    text = text.replace("```", " ")
    text = re.sub(r"\s+", " ", text).strip(" \t\n\r\"'`.,;")
    return text[: max(0, int(limit or 300))].strip()


def _clip_words(value: Any, max_words: int) -> str:
    words = _clean_text(value, limit=4000).split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip() + "…"


def _normalize_confidence(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"high", "medium", "low"}:
        return text
    if text in {"cao", "chắc", "sure"}:
        return "high"
    if text in {"vừa", "trung bình", "moderate"}:
        return "medium"
    return "low"


def _lower_confidence(a: str, b: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    inv = {0: "low", 1: "medium", 2: "high"}
    return inv[min(order.get(_normalize_confidence(a), 0), order.get(_normalize_confidence(b), 0))]


def _dedupe(items: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items or ():
        text = _clean_text(item, limit=300)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


__all__ = [
    "LocalResearchCritique",
    "build_local_critic_prompt",
    "heuristic_research_critique",
    "merge_critiques",
    "parse_local_critic_response",
    "run_local_research_critic",
]
