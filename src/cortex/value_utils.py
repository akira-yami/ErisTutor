"""Tiện ích số và token hóa dùng chung cho các specialist còn hoạt động."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

_WORD_RE = re.compile(r"[\w\-]+", re.UNICODE)


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number:  # NaN
        number = float(default)
    return max(0.0, min(1.0, number))


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return float(default) if number != number else number


def tokenize(text: Any) -> set[str]:
    normalized = unicodedata.normalize("NFKC", str(text or "")).lower()
    return {match.group(0) for match in _WORD_RE.finditer(normalized) if len(match.group(0)) >= 2}
