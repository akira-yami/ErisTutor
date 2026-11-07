# -*- coding: utf-8 -*-
"""Bộ lọc secret trước khi gửi câu hỏi/answer sang Tutor hoặc ghi log.

Không cố 'hiểu' secret bằng LLM. Chỉ dùng regex bảo thủ và deterministic.
Nếu lọt API key sang cửa sổ Gemini Web thì coi như ví điện toán tự xin làm vật tế,
nên lớp này được dùng ở mọi ngõ: topic, context, prompt, raw answer, log preview.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Pattern

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_LABELED_SECRET_RE = re.compile(
    r"\b(?:api[_-]?key|apikey|secret|token|bearer|authorization|password|passwd|cookie|session|sid|refresh[_-]?token|access[_-]?token)\b\s*[:=]\s*[^\s,;\])}]+",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{12,}\b", re.IGNORECASE)

# Provider-ish bare keys. Cố tình không bắt chuỗi ngắn để tránh redact nhầm tên file/id thường.
_SECRET_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{18,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{20,}\b"),
    re.compile(r"\bagt_codex_[A-Za-z0-9_\-]{12,}\b"),
    re.compile(r"\b(?:claude|anthropic|openrouter|gemini|google|qwen|deepseek)[_-]?(?:key|token)[_-]?[A-Za-z0-9_\-]{12,}\b", re.IGNORECASE),
    re.compile(r"\b[A-Za-z0-9_\-]{32,}\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b"),  # JWT-ish
)

_REDACTION = "[redacted-secret]"
_EMAIL_REDACTION = "[email]"


def scrub_secrets(text: Any, *, extra_patterns: Iterable[Pattern[str]] = ()) -> str:
    """Redact email và secret/token/API key thường gặp.

    Hàm này không ném exception. Đưa object gì vào cũng trả string sạch vừa đủ.
    """

    value = str(text or "")
    if not value:
        return ""
    value = _EMAIL_RE.sub(_EMAIL_REDACTION, value)
    value = _LABELED_SECRET_RE.sub(_REDACTION, value)
    value = _BEARER_RE.sub(_REDACTION, value)
    for pattern in tuple(_SECRET_PATTERNS) + tuple(extra_patterns or ()):  # tuple để tránh iterator bị tiêu thụ lạ đời
        try:
            value = pattern.sub(_REDACTION, value)
        except Exception:
            continue
    return value


def contains_secret_like(text: Any) -> bool:
    """True nếu văn bản có vẻ còn secret sau scrub, dùng cho test/guard."""

    value = str(text or "")
    if not value:
        return False
    if _LABELED_SECRET_RE.search(value) or _BEARER_RE.search(value):
        return True
    return any(pattern.search(value) for pattern in _SECRET_PATTERNS)


__all__ = ["scrub_secrets", "contains_secret_like"]
