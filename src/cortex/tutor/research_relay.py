# -*- coding: utf-8 -*-
"""Relay nghiên cứu nhẹ: local/open-model đặt câu hỏi cho Tutor Gemini rồi chấm lại.

Không dùng Gemini Deep Research API, không Google AI grounding, không background
agent nhiều bước. Đây chỉ là một lớp contract rẻ:

1. topic -> prompt ngắn để Tutor Gemini web tự search nếu cần;
2. raw answer -> phân tích cục bộ/heuristic để tách phần dùng được, phần yếu,
   nguồn nhìn thấy;
3. trả một văn bản đã guard để Eris surface lại, không tin raw blob như kinh
   thánh rơi từ trời xuống. Nhân loại đã thử việc tin blob text rồi, kết quả là
   comment section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

try:  # pragma: no cover
    from cortex.tutor.config import scrub_identity
except Exception:  # pragma: no cover
    from .config import scrub_identity  # type: ignore

try:  # pragma: no cover
    from cortex.tutor.research_evidence import EvidenceBundle, build_evidence_bundle
except Exception:  # pragma: no cover
    from .research_evidence import EvidenceBundle, build_evidence_bundle  # type: ignore

try:  # pragma: no cover
    from cortex.tutor.local_research_critic import LocalResearchCritique, run_local_research_critic
except Exception:  # pragma: no cover
    try:
        from .local_research_critic import LocalResearchCritique, run_local_research_critic  # type: ignore
    except Exception:  # pragma: no cover
        LocalResearchCritique = None  # type: ignore
        run_local_research_critic = None  # type: ignore

try:  # pragma: no cover
    from runtime.tutor_research_policy import (
        build_lightweight_research_prompt,
        build_lightweight_search_prompt,
        compact_research_answer,
        normalize_marker_text,
    )
except Exception:  # pragma: no cover
    from src.runtime.tutor_research_policy import (  # type: ignore
        build_lightweight_research_prompt,
        build_lightweight_search_prompt,
        compact_research_answer,
        normalize_marker_text,
    )


CriticFn = Callable[[str], str]
_URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.IGNORECASE)
_SOURCE_LINE_RE = re.compile(r"^\s*(?:[-*•]\s*)?(?:ngu[oồ]n|source|tham kh[aả]o|link)\b\s*[:：-]?\s*(.+)$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s*(?:\d+\)?[.)]?\s*)?(k[eế]t lu[aậ]n|đi[eể]m|chưa chắc|ngu[oồ]n|sources?|tham kh[aả]o)\b", re.IGNORECASE)
_UNCERTAIN_RE = re.compile(r"\b(chưa chắc|kh[oô]ng r[oõ]|c[oó] th[eể]|d[uữ] li[eệ]u thi[eế]u|chưa x[aá]c minh|kh[oô]ng t[iì]m th[aấ]y)\b", re.IGNORECASE)


@dataclass(slots=True)
class TutorResearchRelayResult:
    """Kết quả đã qua lớp chấm cục bộ sau khi Tutor Gemini trả lời."""

    topic: str
    tutor_prompt: str
    raw_answer: str
    usable_summary: str
    supported_points: list[str] = field(default_factory=list)
    weak_points: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    sources_seen: list[str] = field(default_factory=list)
    confidence: str = "low"  # low | medium | high
    evidence_bundle: Optional[EvidenceBundle] = None
    answer_max_words: int = 320

    @property
    def answer_text(self) -> str:
        return format_relay_answer(self)


def build_gemini_research_prompt(topic: object, *, context: object = "", max_words: int = 250, max_chars: int = 395) -> str:
    """Prompt mà local/open-model đưa cho Tutor Gemini.

    Giữ dưới TutorQuestion 400 ký tự. Dòng chữ "Gemini" là cố ý: nếu backend là
    Gemini web thì prompt giống một lời giao việc trực tiếp; nếu backend khác, nó
    vẫn là yêu cầu search/tóm tắt bình thường.
    """

    topic_text = " ".join(str(topic or "").split())
    context_text = " ".join(str(context or "").split())
    prompt = build_lightweight_research_prompt(
        topic_text,
        context=context_text,
        max_words=max_words,
        max_chars=max_chars,
    )
    # Nắn câu để đúng ý chủ dự án: "local/open-model hỏi Gemini search web" thay vì
    # một prompt nghiên cứu chung chung. Vẫn hard-cap để khỏi dài như giấy phạt.
    prefix = "Gemini, "
    if not prompt.lower().startswith("gemini"):
        prompt = prefix + prompt[: max(0, max_chars - len(prefix))]
    return prompt[:max_chars].rstrip()




def build_gemini_search_prompt(topic: object, *, context: object = "", max_words: int = 160, max_chars: int = 395) -> str:
    """Prompt search bình thường: nhanh, có nguồn, không kéo nghiên cứu sâu."""

    prompt = build_lightweight_search_prompt(
        topic,
        context=context,
        max_words=max_words,
        max_chars=max_chars,
    )
    prefix = "Gemini, "
    if not prompt.lower().startswith("gemini"):
        prompt = prefix + prompt[: max(0, max_chars - len(prefix))]
    return prompt[:max_chars].rstrip()


def analyze_tutor_research_answer(
    topic: object,
    raw_answer: object,
    *,
    tutor_prompt: object = "",
    critic_fn: Optional[CriticFn] = None,
    max_words: int = 280,
    mode: str = "research",
) -> TutorResearchRelayResult:
    """Chấm câu trả lời của Tutor bằng critic cục bộ nếu có, rồi heuristic fallback.

    ``critic_fn`` thường là local/open-model lane. Vì hàm generate_fn hiện tại của
    Tutor wiring chỉ bảo đảm trả text ngắn, relay KHÔNG phụ thuộc JSON từ model.
    Model cục bộ chỉ được dùng như bộ rewrite rẻ; verification vẫn do heuristic
    nguồn/URL bảo vệ.
    """

    topic_text = scrub_identity(str(topic or "").strip())
    raw = scrub_identity(str(raw_answer or "").strip())
    prompt = scrub_identity(str(tutor_prompt or "").strip())
    raw = compact_research_answer(raw, max_words=max_words)

    sources = _extract_sources(raw)
    weak_points = _extract_weak_points(raw)
    supported_points = _extract_supported_points(raw, sources=sources)
    confidence = _estimate_confidence(raw, sources=sources, weak_points=weak_points)

    critique = None
    if run_local_research_critic is not None:
        try:
            critique = run_local_research_critic(topic_text, raw, critic_fn=critic_fn)
        except Exception:
            critique = None

    if critique is not None:
        usable = str(getattr(critique, "usable_summary", "") or "").strip()
        # Nếu local model không trả JSON hợp lệ, run_local_research_critic rơi về
        # heuristic. Lúc đó vẫn thử legacy one-paragraph critic để giữ tương thích
        # với generate_fn cũ vốn chỉ biết trả một đoạn text. Không thì test cũ sẽ
        # khóc, còn repo thì thêm một truyền thống đau khổ nữa.
        if critic_fn is not None and not bool(getattr(critique, "used_model", False)):
            legacy_summary = _try_critic_summary(topic_text, raw, critic_fn=critic_fn)
            if legacy_summary:
                usable = legacy_summary
        usable = usable or _heuristic_summary(raw)
        supported_points = list(supported_points) + list(getattr(critique, "supported_points", []) or [])
        weak_points = list(weak_points) + list(getattr(critique, "weak_points", []) or [])
        contradictions = list(getattr(critique, "contradictions", []) or [])
        sources = list(sources) + list(getattr(critique, "sources_seen", []) or [])
        confidence = _lower_confidence(confidence, str(getattr(critique, "confidence", "low") or "low"))
        caution = str(getattr(critique, "caution", "") or "").strip()
        if caution:
            weak_points.append(caution)
        if contradictions and confidence == "high":
            confidence = "medium"
    else:
        usable = _try_critic_summary(topic_text, raw, critic_fn=critic_fn) or _heuristic_summary(raw)
        contradictions = []

    usable = compact_research_answer(usable, max_words=max(90, min(180, max_words)))

    if not usable:
        usable = "Tutor không trả ra phần đủ sạch để dùng. Máy móc lại chọn cách im lặng bằng văn bản, rất thơ."
        confidence = "low"
    if not sources and "nguồn" not in normalize_marker_text(raw):
        weak_points.insert(0, "Tutor không đưa nguồn rõ ràng, nên chỉ được coi là gợi ý chứ chưa phải kết luận chắc.")
        confidence = "low"
    if contradictions:
        for item in contradictions[:2]:
            weak_points.append("Có dấu hiệu mâu thuẫn/cần đối chiếu: " + _strip_terminal_dot(item))

    supported_clean = _dedupe_keep_order(supported_points)[:5]
    contradiction_clean = _dedupe_keep_order(contradictions)[:3]
    weak_clean = _dedupe_keep_order(weak_points)[:4]
    sources_clean = _dedupe_keep_order(sources)[:5]
    bundle = build_evidence_bundle(
        query=topic_text,
        raw_answer=raw,
        supported_points=supported_clean,
        weak_points=weak_clean,
        mode=str(mode or "research"),
    )
    # Verification thắng confidence heuristic. Nếu bundle fail, không cho surface như
    # kết luận chắc, dù Tutor nói bằng giọng thánh hiền markdown.
    try:
        overall = str(bundle.verification.overall_status)
        if overall == "failed":
            confidence = "low"
        elif overall == "passed_with_warnings" and confidence == "high":
            confidence = "medium"
    except Exception:
        pass

    return TutorResearchRelayResult(
        topic=topic_text,
        tutor_prompt=prompt,
        raw_answer=raw,
        usable_summary=usable,
        supported_points=supported_clean,
        weak_points=weak_clean,
        contradictions=contradiction_clean,
        sources_seen=sources_clean,
        confidence=confidence,
        evidence_bundle=bundle,
        answer_max_words=max(80, int(max_words or 320)),
    )


def format_relay_answer(result: TutorResearchRelayResult) -> str:
    """Surface factual gọn, đã guard, để coordinator chỉ thêm lead của Eris."""

    chunks: list[str] = []
    summary = str(result.usable_summary or "").strip()
    if summary:
        chunks.append(summary)

    if result.weak_points:
        weak = "; ".join(_strip_terminal_dot(item) for item in result.weak_points[:2] if item.strip())
        if weak:
            chunks.append(f"Điểm cần dè chừng: {weak}.")

    if getattr(result, "contradictions", None):
        conflict = "; ".join(_strip_terminal_dot(item) for item in result.contradictions[:2] if item.strip())
        if conflict:
            chunks.append(f"Mâu thuẫn cần đối chiếu: {conflict}.")

    bundle = getattr(result, "evidence_bundle", None)
    if bundle is not None:
        try:
            verification = bundle.verification
            if verification.overall_status != "passed":
                warn = "; ".join(str(item) for item in verification.warnings[:2])
                if warn:
                    chunks.append(f"Kiểm chứng nhẹ: {warn}")
        except Exception:
            pass

    if result.sources_seen:
        chunks.append("Nguồn Tutor đưa: " + "; ".join(result.sources_seen[:4]))
    elif result.confidence == "low":
        chunks.append("Nguồn: Tutor chưa đưa nguồn đủ rõ, nên em không chốt cứng phần này.")

    text = "\n".join(chunk.strip() for chunk in chunks if chunk.strip())
    return compact_research_answer(text, max_words=max(80, int(getattr(result, "answer_max_words", 320) or 320)))


def _try_critic_summary(topic: str, raw_answer: str, *, critic_fn: Optional[CriticFn]) -> str:
    if critic_fn is None or not raw_answer:
        return ""
    try:
        prompt = (
            "Bạn là critic cục bộ cho Eris. Đọc câu trả lời Tutor/Gemini và viết lại "
            "thành MỘT đoạn tiếng Việt ngắn, chỉ giữ claim có vẻ được nguồn trong đáp án hỗ trợ; "
            "không thêm kiến thức mới; nếu thiếu nguồn thì nói dè dặt. Tối đa 90 từ.\n"
            f"Chủ đề: {topic[:240]}\n"
            f"Tutor trả lời: {raw_answer[:1400]}\n"
            "Đoạn dùng được:"
        )
        text = scrub_identity(str(critic_fn(prompt) or "").strip())
        return _clean_generated_summary(text)
    except Exception:
        return ""


def _clean_generated_summary(text: str) -> str:
    value = " ".join(str(text or "").replace("```", " ").split()).strip().strip('"').strip("'")
    if not value:
        return ""
    # generate_fn đôi khi trả JSON text lồng kiểu {"text":"..."}; đừng để Eris đọc ngoặc nhọn.
    match = re.search(r'"text"\s*:\s*"([^"]+)"', value)
    if match:
        value = match.group(1).strip()
    return value[:900].strip()


def _extract_sources(text: str) -> list[str]:
    sources: list[str] = []
    for url in _URL_RE.findall(text or ""):
        sources.append(url.rstrip(".,;"))
    for line in str(text or "").splitlines():
        match = _SOURCE_LINE_RE.match(line)
        if match:
            body = match.group(1).strip()
            if body and not _URL_RE.search(body):
                sources.append(_strip_terminal_dot(body)[:180])
    return _dedupe_keep_order(sources)


def _extract_weak_points(text: str) -> list[str]:
    weak: list[str] = []
    for sent in _split_sentences(text):
        if _UNCERTAIN_RE.search(sent):
            weak.append(_strip_terminal_dot(sent)[:240])
    return weak


def _extract_supported_points(text: str, *, sources: Iterable[str]) -> list[str]:
    points: list[str] = []
    source_set = tuple(sources or ())
    for sent in _split_sentences(text):
        clean = sent.strip()
        if not clean or _SOURCE_LINE_RE.match(clean):
            continue
        if _URL_RE.search(clean) or any(src and src in clean for src in source_set):
            points.append(_strip_terminal_dot(_URL_RE.sub("", clean)).strip()[:240])
    if not points and source_set:
        # Có nguồn ở cuối nhưng câu claim không gắn URL inline. Giữ vài câu đầu, vẫn thấp hơn
        # citation alignment thật, nhưng tốt hơn vứt tất cả xuống biển rồi gọi là kiến trúc.
        for sent in _split_sentences(text)[:4]:
            clean = _strip_terminal_dot(sent)
            if clean and not _SOURCE_LINE_RE.match(clean):
                points.append(clean[:240])
    return points


def _heuristic_summary(text: str) -> str:
    lines = []
    for line in str(text or "").splitlines():
        clean = line.strip(" \t-*•0123456789.)")
        if not clean:
            continue
        if _SOURCE_LINE_RE.match(clean) or normalize_marker_text(clean).startswith(("nguon", "sources", "tham khao")):
            break
        lines.append(clean)
        if len(" ".join(lines).split()) >= 120:
            break
    if lines:
        return " ".join(lines).strip()
    return " ".join(_split_sentences(text)[:3]).strip()


def _estimate_confidence(text: str, *, sources: list[str], weak_points: list[str]) -> str:
    if not str(text or "").strip():
        return "low"
    if len(sources) >= 2 and not weak_points:
        return "high"
    if sources:
        return "medium"
    return "low"


def _split_sentences(text: str) -> list[str]:
    value = re.sub(r"\s+", " ", str(text or "").strip())
    if not value:
        return []
    pieces = re.split(r"(?<=[.!?。！？])\s+|\s+[•]\s+", value)
    return [piece.strip() for piece in pieces if piece.strip()]


def _strip_terminal_dot(text: str) -> str:
    return str(text or "").strip().rstrip(" .;，,。")


def _dedupe_keep_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items or ():
        value = str(item or "").strip()
        if not value:
            continue
        key = normalize_marker_text(value) or value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _lower_confidence(a: str, b: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    inv = {0: "low", 1: "medium", 2: "high"}
    return inv[min(order.get(str(a or "low").lower(), 0), order.get(str(b or "low").lower(), 0))]


__all__ = [
    "TutorResearchRelayResult",
    "analyze_tutor_research_answer",
    "build_gemini_research_prompt",
    "build_gemini_search_prompt",
    "format_relay_answer",
]
