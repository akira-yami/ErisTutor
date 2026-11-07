"""Syntax-only exclusions for session-scoped social permission text.

PDF annotation, pages 12-13 and 20: this module protects the persistence
boundary.  It does not classify open conversational meaning and never affects
public wording.
"""

from __future__ import annotations

import re
import unicodedata


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFD", str(text or ""))
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d").replace("Đ", "D").lower()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


_SESSION_PERMISSION_PATTERNS = (
    r"\b(?:toi|ta|minh) cho phep (?:em|eris|ban)? ?(?:treu|ca khia|choc|lam phien|chu dong)\b",
    r"\b(?:em|eris|ban) duoc phep (?:treu|ca khia|choc|lam phien|chu dong)\b",
    r"\bcu (?:treu|ca khia|choc|lam phien|chu dong)(?: toi| tui| minh)?\b",
    r"\b(?:dung|ngung|thoi) (?:treu|ca khia|choc|lam phien|chu dong)(?: toi| tui| minh)?\b",
    r"\bkhong duoc (?:treu|ca khia|choc|lam phien|chu dong)\b",
)


def should_exclude_from_persistent_memory(text: str) -> bool:
    """Return True only for explicit, session-scoped permission commands."""

    normalized = _normalize(text)
    return bool(normalized and any(re.search(pattern, normalized) for pattern in _SESSION_PERMISSION_PATTERNS))


__all__ = ["should_exclude_from_persistent_memory"]
