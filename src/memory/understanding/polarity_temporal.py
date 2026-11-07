from __future__ import annotations

"""
memory.understanding.polarity_temporal
======================================

Đọc tín hiệu phủ định/thời gian ở mức feature.

Đây KHÔNG phải nơi quyết định predicate. Nó chỉ nói: câu có dấu hiệu phủ định,
quá khứ, hiện tại, tương lai hay không. Nếu để từng extractor tự dò các dấu này,
repo sẽ mọc lại cỏ dại marker-word chỉ sau một đêm mất ngủ.
"""

from dataclasses import dataclass
import re

from .entity_linker import normalize


NEGATION = ("khong", "ko", "k", "chua", "chang", "no", "not", "dont", "don t")
UNCERTAIN = ("co le", "hinh nhu", "chac", "khong chac", "maybe", "probably")
PAST = ("hom qua", "truoc day", "hoi truoc", "tung", "da tung", "tuan truoc", "nam truoc")
FUTURE = ("se", "sap", "dinh", "du dinh", "muon sau nay", "from now on", "tu gio")
CURRENT = ("dang", "hien", "hien tai", "bay gio", "luc nay", "now", "currently")
QUESTION = (" gi", " nao", " dau", " khong", " chua", " phai khong", "what", "where", "when", "who", "how")


@dataclass(frozen=True, slots=True)
class SurfaceFeatures:
    raw: str
    normalized: str
    polarity: str = "asserted"
    time_scope: str = "unknown"
    is_question: bool = False
    is_greeting_like: bool = False
    has_explicit_memory_request: bool = False
    evidence: tuple[str, ...] = ()


def analyse_surface(text: str) -> SurfaceFeatures:
    raw = str(text or "").strip()
    norm = normalize(raw)
    evidence: list[str] = []

    polarity = "asserted"
    if _has_any(norm, NEGATION):
        polarity = "denied"
        evidence.append("surface:negation")
    elif _has_any(norm, UNCERTAIN):
        polarity = "uncertain"
        evidence.append("surface:uncertain")

    time_scope = "unknown"
    if _has_any(norm, CURRENT):
        time_scope = "current"
        evidence.append("surface:current")
    elif _has_any(norm, PAST):
        time_scope = "past"
        evidence.append("surface:past")
    elif _has_any(norm, FUTURE):
        time_scope = "future"
        evidence.append("surface:future")

    # So khớp theo cụm token, không dùng substring. Bản cũ coi "giới tính"
    # là câu hỏi vì chuỗi " gi" nằm ở đầu "gioi", một lỗi nhỏ đủ làm mất claim.
    is_question = "?" in raw or _has_any(norm, QUESTION)
    if is_question:
        evidence.append("surface:question")

    greeting = norm in {"chao", "xin chao", "hello", "hi", "alo", "ua alo", "chao eris", "hello eris"}
    if greeting:
        evidence.append("surface:greeting")

    memory_request = _has_any(norm, ("nho", "ghi lai", "luu", "dung quen", "remember", "do not forget"))
    if memory_request:
        evidence.append("surface:memory_request")

    return SurfaceFeatures(
        raw=raw,
        normalized=norm,
        polarity=polarity,
        time_scope=time_scope,
        is_question=is_question,
        is_greeting_like=greeting,
        has_explicit_memory_request=memory_request,
        evidence=tuple(evidence),
    )


def _has_any(norm: str, phrases: tuple[str, ...]) -> bool:
    hay = f" {norm} "
    return any(f" {normalize(p)} " in hay for p in phrases if normalize(p))


def split_clauses(text: str) -> list[str]:
    """Tách clause thô để parser xử lý từng mệnh đề nhỏ.

    Dùng cho fallback thôi. Model parser nên tự trả frame có evidence_span.
    """

    raw = str(text or "").strip()
    if not raw:
        return []
    parts = re.split(r"\s*(?:[,;.!?]+|\b(?:và|va|nhưng|nhung|còn|con|rồi|roi|thì|thi)\b)\s+", raw, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


__all__ = ["SurfaceFeatures", "analyse_surface", "split_clauses"]
