from __future__ import annotations

"""Guard tập trung cho fact ``user.name`` / ``user.nickname``.

Tên người là dữ liệu ổn định và được surface rất tự tin, nên false positive kiểu
"Tên bạn là bạn" tệ hơn việc bỏ sót một câu tự giới thiệu mơ hồ. Module này dùng
cả lúc ghi lẫn lúc đọc để dữ liệu rác cũ không tiếp tục được phát ngôn.
"""

import re
import unicodedata
from typing import Any

_GENERIC_VALUES = {
    "ban", "ban oi", "nguoi ban", "nguoi dung", "user", "the user",
    "ban", "chu nhan", "sep", "boss", "khach", "nguoi hoi",
    "toi", "minh", "tui", "tao", "ta", "em", "anh", "chi", "co", "chu",
    "you", "your", "me", "myself", "someone", "somebody",
    "ai", "gi", "ten gi", "la gi", "who", "what", "unknown", "khong biet",
    "null", "none", "n a", "na",
}
_QUESTION_TOKENS = {"ai", "gi", "what", "who", "which", "unknown"}


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFD", str(text or ""))
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def normalize_name_text(value: Any) -> str:
    text = _strip_accents(str(value or "").lower()).replace("đ", "d")
    text = re.sub(r"[^0-9a-zA-Z_\s-]+", " ", text)
    return " ".join(text.split())


def clean_name_surface(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    return text.strip(" .,:;!?\"'“”‘’`")


def is_plausible_user_name(value: Any) -> bool:
    """Reject đại từ, từ hỏi, câu dài và placeholder bị model gắn nhầm làm tên."""

    clean = clean_name_surface(value)
    norm = normalize_name_text(clean)
    if not clean or not norm or len(clean) > 60:
        return False
    words = norm.split()
    if not 1 <= len(words) <= 6:
        return False
    if norm in _GENERIC_VALUES:
        return False
    if any(token in _QUESTION_TOKENS for token in words):
        return False
    if re.search(r"\b(?:ten|name)\s+(?:cua\s+)?(?:ban|you|nguoi dung)\b", norm):
        return False
    if re.search(r"\b(?:la|is)\s+(?:ai|gi|who|what)\b", norm):
        return False
    # Tên không nên là cả một mệnh đề hội thoại. Dấu nối/underscore/số vẫn được
    # giữ để nickname kiểu Gtvkun_01 không bị đạo đức giả dữ liệu loại bỏ.
    if not re.search(r"[A-Za-zÀ-ỹ]", clean):
        return False
    return True


def has_explicit_user_name_declaration(text: str) -> bool:
    """Chỉ nhận cue tự khai tên của user, không nhận câu hỏi "tên bạn là gì"."""

    norm = normalize_name_text(text)
    if not norm:
        return False
    if re.search(r"\b(?:ten gi|la gi|la ai|what is|who is)\b", norm):
        return False
    patterns = (
        r"\b(?:toi|minh|tui|tao|em|i)\s+ten\s+(?:day\s+du\s+)?(?:la\s+)?\S+",
        r"\bten\s+(?:that\s+)?day\s+du\s+(?:cua\s+)?(?:toi|minh|tui|tao|em)\s+(?:la\s+)?\S+",
        r"\bten\s+(?:that\s+)?(?:cua\s+)?(?:toi|minh|tui|tao|em)\s+(?:la\s+)?\S+",
        r"\b(?:goi|cu goi)\s+(?:toi|minh|tui|tao|em)\s+(?:la\s+)?\S+",
        r"\b(?:toi|minh|tui|tao|em)\s+moi\s+doi\s+ten\s+(?:sang|thanh)\s+\S+",
        r"\bdoi\s+ten\s+(?:sang|thanh)\s+\S+",
        r"\btu\s+gio\s+(?:cu\s+)?goi\s+(?:toi|minh|tui|tao|em)\s+(?:la\s+)?\S+",
        r"\b(?:my name is|call me)\s+\S+",
        r"\b(?:nho|hay nho|remember)\s+(?:rang\s+)?(?:toi|minh|tui|em)\s+(?:ten\s+)?(?:la\s+)?\S+",
    )
    return any(re.search(pattern, norm) for pattern in patterns)


def has_explicit_user_nickname_declaration(text: str) -> bool:
    norm = normalize_name_text(text)
    if not norm or re.search(r"\b(?:biet danh|nickname)\s+(?:gi|la gi|what)\b", norm):
        return False
    return bool(
        re.search(
            r"\b(?:biet danh|biet hieu|nickname|ten goi)(?:\s+cua)?\s+(?:toi|minh|tui|em)?\s*(?:la|is)\s+\S+",
            norm,
        )
        or re.search(r"\b(?:toi|minh|tui|em)\s+co\s+(?:biet danh|nickname)\s+(?:la\s+)?\S+", norm)
    )


def is_valid_name_claim(value: Any, evidence_text: str, *, nickname: bool = False) -> bool:
    if not is_plausible_user_name(value):
        return False
    if nickname:
        return has_explicit_user_nickname_declaration(evidence_text)
    return has_explicit_user_name_declaration(evidence_text)


__all__ = [
    "clean_name_surface",
    "has_explicit_user_name_declaration",
    "has_explicit_user_nickname_declaration",
    "is_plausible_user_name",
    "is_valid_name_claim",
    "normalize_name_text",
]
