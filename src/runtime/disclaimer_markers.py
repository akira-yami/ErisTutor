"""Shared anti-disclaimer marker lists + predicates.

Đây từng là HOME của các marker register cấm. Sau khi gom về một nguồn canonical
(Nước 1 — VoicePolicy consolidation), module này chỉ còn là **shim re-export** từ
``runtime/voice_policy.py`` để mọi importer cũ tiếp tục chạy nguyên vẹn:

    from runtime.disclaimer_markers import (
        CHAT_DISCLAIMER_HARD_MARKERS, CHAT_DISCLAIMER_HELPER_MARKERS,
        CHAT_DISCLAIMER_EMOTION_MARKERS, is_disclaimer_text, normalize_guard_ascii,
    )

Định nghĩa thật (marker + predicate) sống DUY NHẤT ở ``voice_policy``. Đừng thêm
literal mới ở đây — sửa giọng/register là sửa ở ``voice_policy``.
"""

from __future__ import annotations

from runtime.voice_policy import (
    EMOTION_MARKERS as CHAT_DISCLAIMER_EMOTION_MARKERS,
    HARD_MARKERS as CHAT_DISCLAIMER_HARD_MARKERS,
    HELPER_MARKERS as CHAT_DISCLAIMER_HELPER_MARKERS,
    is_disclaimer_text,
    normalize_guard_ascii,
)

__all__ = [
    "CHAT_DISCLAIMER_EMOTION_MARKERS",
    "CHAT_DISCLAIMER_HARD_MARKERS",
    "CHAT_DISCLAIMER_HELPER_MARKERS",
    "is_disclaimer_text",
    "normalize_guard_ascii",
]
