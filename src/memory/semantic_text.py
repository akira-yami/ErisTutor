from __future__ import annotations

"""
semantic_text.py
================

Các helper ngôn ngữ dùng chung cho semantic memory.

Mục tiêu của file này không phải biến vài keyword thành chân lý. Nó chỉ gom phần
"đọc bề mặt tiếng Việt" vào một chỗ để những file như claim_extractor.py,
query_intent.py, retention policy... không tự rải marker word khắp repo.

Nếu sau này bạn gắn embedding hoặc LLM structured parser, đây vẫn là lớp fallback
nhẹ để:
- normalize tiếng Việt có/không dấu;
- tách clause;
- nhận biết câu hỏi/greeting đơn giản;
- đọc vài dấu hiệu phủ định/thời gian mà không commit memory bừa.

Nói cách khác: lexical chỉ được làm cảm biến bề mặt, không được làm quan tòa.
Quan tòa là ontology + entailment + worthiness/retention policy.
"""

from dataclasses import dataclass, field
from typing import Iterable
import re
import unicodedata


FIRST_PERSON = (
    "toi", "minh", "tui", "to", "em", "i",
)

FIRST_PERSON_RE = r"(?:toi|minh|tui|to|em|i)"

CLAUSE_SPLIT_RE = re.compile(
    r"\s*(?:[,;.!?]+|\b(?:va|nhung|con|roi|thi|nen)\b)\s+",
    flags=re.IGNORECASE,
)

QUESTION_SHAPES = (
    " gi", " nao", " dau", " khong", " chua", " phai khong", " may",
    "what", "where", "when", "who", "which", "how",
)

# Greeting canonical chỉ dùng để chặn long-term semantic commit cho lời chào.
# Nó không dùng để đọc tính cách user. Nhân loại nói "alo" không có nghĩa là một fact bền vững.
GREETING_CANON = {
    "chao", "xin chao", "hello", "hi", "alo", "ua alo", "chao eris", "hello eris",
}

NEGATION_CUES = ("khong", "ko", "k", "chua", "chang", "no", "not", "dont", "don t")
PAST_CUES = ("hom qua", "truoc day", "hoi truoc", "tung", "da tung", "da o")
FUTURE_CUES = ("se", "sap", "dinh", "du dinh", "muon sau nay")
CURRENT_CUES = ("dang", "hien", "hien tai", "bay gio", "luc nay")


@dataclass(frozen=True, slots=True)
class TextSemanticFrame:
    """Bản đọc bề mặt của một câu user.

    Đây chỉ là context hỗ trợ. Đừng nhét nguyên frame này vào memory rồi gọi nó là
    semantic. Làm vậy thì lại quay về thời đồ đá có JSON indentation.
    """

    raw: str
    norm: str
    clauses: tuple[str, ...]
    is_question: bool = False
    is_greeting_only: bool = False
    assertion: str = "asserted"
    temporal_hint: str = "unknown"
    evidence: tuple[str, ...] = field(default_factory=tuple)


def normalize_vi(text: str) -> str:
    """Chuẩn hóa tiếng Việt về dạng không dấu, chữ thường, khoảng trắng gọn."""

    t = unicodedata.normalize("NFD", str(text or "").lower())
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Mn")
    t = t.replace("đ", "d")
    t = re.sub(r"[^a-z0-9_\s]+", " ", t)
    return " ".join(t.split())


def clean_surface_text(value: str) -> str:
    """Dọn object surface nhưng không tự canonicalize quá tay."""

    text = " ".join(str(value or "").strip().split())
    text = text.strip(" .,:;!?\"'“”‘’`")
    text = re.sub(r"\s+(nha|nhé|nhe|đấy|day|thôi|thoi|ạ|a)$", "", text, flags=re.IGNORECASE)
    return text.strip(" .,:;!?\"'“”‘’`")


def analyse_text(text: str) -> TextSemanticFrame:
    raw = " ".join(str(text or "").strip().split())
    norm = normalize_vi(raw)
    clauses = tuple(c for c in split_clauses(norm) if c)
    assertion = "denied" if has_any(norm, *NEGATION_CUES) else "asserted"
    temporal = "unknown"
    if has_any(norm, *PAST_CUES):
        temporal = "past"
    elif has_any(norm, *FUTURE_CUES):
        temporal = "future"
    elif has_any(norm, *CURRENT_CUES):
        temporal = "current"
    return TextSemanticFrame(
        raw=raw,
        norm=norm,
        clauses=clauses or ((norm,) if norm else ()),
        is_question=looks_like_question(raw, norm),
        is_greeting_only=looks_like_greeting_only(norm),
        assertion=assertion,
        temporal_hint=temporal,
        evidence=tuple(_surface_evidence(norm)),
    )


def split_clauses(norm_text: str) -> list[str]:
    if not norm_text:
        return []
    return [part.strip() for part in CLAUSE_SPLIT_RE.split(norm_text) if part.strip()]


def looks_like_question(raw: str, norm: str | None = None) -> bool:
    norm = normalize_vi(raw) if norm is None else norm
    if "?" in str(raw or ""):
        return True
    # Yêu cầu có first-person hoặc dạng hỏi rõ để không chặn mọi câu có chữ "không".
    if has_any(norm, "nho", "biet", "cho hoi", "hoi") and has_any(norm, *QUESTION_SHAPES):
        return True
    return any(shape in f" {norm} " for shape in QUESTION_SHAPES) and _has_interrogative_context(norm)


def looks_like_greeting_only(norm: str) -> bool:
    compact = norm.replace("cung", "").replace("nha", "").strip()
    return norm in GREETING_CANON or compact in GREETING_CANON


def has_any(norm: str, *phrases: str) -> bool:
    """Phrase containment có boundary nhẹ.

    Đây vẫn là lexical, nhưng nằm ở tầng cảm biến chung. File nghiệp vụ không nên
    tự bắn `if "abc" in text` khắp nơi nữa.
    """

    padded = f" {norm} "
    for phrase in phrases:
        p = normalize_vi(phrase)
        if not p:
            continue
        if f" {p} " in padded or p in norm:
            return True
    return False


def relation_regex(aliases: Iterable[str]) -> str:
    """Build regex cho relation aliases đã normalize.

    Alias được escape và sort dài trước để tránh `sinh` ăn mất `sinh thang`.
    """

    parts = sorted({normalize_vi(a) for a in aliases if normalize_vi(a)}, key=len, reverse=True)
    return r"(?:" + "|".join(re.escape(p).replace(r"\ ", r"\s+") for p in parts) + r")"


def _has_interrogative_context(norm: str) -> bool:
    return has_any(norm, "toi", "minh", "tui", "em", "eris", "ban", "ban", "my", "me")


def _surface_evidence(norm: str) -> list[str]:
    evidence: list[str] = []
    if has_any(norm, *NEGATION_CUES):
        evidence.append("surface:negation")
    if has_any(norm, *PAST_CUES):
        evidence.append("surface:past")
    if has_any(norm, *FUTURE_CUES):
        evidence.append("surface:future")
    if has_any(norm, *CURRENT_CUES):
        evidence.append("surface:current")
    return evidence


__all__ = [
    "FIRST_PERSON",
    "FIRST_PERSON_RE",
    "TextSemanticFrame",
    "analyse_text",
    "clean_surface_text",
    "has_any",
    "looks_like_greeting_only",
    "looks_like_question",
    "normalize_vi",
    "relation_regex",
    "split_clauses",
]
