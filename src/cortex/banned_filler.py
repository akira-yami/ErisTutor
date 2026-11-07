# -*- coding: utf-8 -*-
"""Single source of truth for stale filler lines the character must never speak.

Both the LLM prompt construction (``llm/service.py``) and the autonomous output
filter (``runtime/presence_controller.py``) import from here, so adding or
removing a line is reflected in the instruction and the enforcement at once.

The character may stay silent or use body-only presence, but it must never emit
one of these hollow lines as an autonomous line.
"""

from __future__ import annotations

import re
import unicodedata


# Hollow "I am present / I am listening" status pings.
HOLLOW_PRESENCE_LINES: tuple[str, ...] = (
    "Tôi ở đây.",
    "Tôi đang nghe.",
    "Tôi vẫn quan sát.",
    "Tôi vẫn ở đây.",
)

# Stale micro templates the loop kept repeating.
STALE_MICRO_TEMPLATES: tuple[str, ...] = (
    "Mạch này vẫn giữ chỗ.",
    "Nhịp này im hơi lâu rồi.",
    "Vẫn đang đợi chút nhé.",
    "Khoảng yên này vẫn giữ chỗ.",
    "Nhịp này vẫn yên thêm chút.",
)

# Canonical banned set. Order is preserved for stable prompt rendering.
BANNED_FILLER_LINES: tuple[str, ...] = HOLLOW_PRESENCE_LINES + STALE_MICRO_TEMPLATES

_TRAILING_PUNCT_RE = re.compile(r"[\s.。…!?]+$")
_SPACE_RE = re.compile(r"\s+")


def normalize_banned(text: str) -> str:
    """Normalize for membership comparison.

    Lowercases, NFC-normalizes, collapses internal whitespace, and trims leading
    and trailing whitespace plus trailing sentence punctuation so that
    "Tôi ở đây" and "Tôi ở đây." compare equal.
    """

    raw = unicodedata.normalize("NFC", str(text or ""))
    collapsed = _SPACE_RE.sub(" ", raw).strip()
    trimmed = _TRAILING_PUNCT_RE.sub("", collapsed)
    return trimmed.casefold()


# Precomputed normalized set for O(1) membership.
_NORMALIZED_BANNED: frozenset[str] = frozenset(
    normalize_banned(line) for line in BANNED_FILLER_LINES
)


def is_banned_filler(text: str) -> bool:
    """True if ``text`` matches a banned line after normalization."""

    normalized = normalize_banned(text)
    if not normalized:
        return False
    return normalized in _NORMALIZED_BANNED


def quoted_fragment(lines: tuple[str, ...]) -> str:
    """Render lines as a comma-separated quoted list for prompt text.

    Example: ``"'Tôi ở đây', 'Tôi đang nghe'"``. Trailing periods are dropped to
    match the existing prompt style.
    """

    return ", ".join(f"'{line.rstrip('. ')}'" for line in lines)
