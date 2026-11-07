# -*- coding: utf-8 -*-
"""Cầu nối tự-tra-cứu khi Eris tự nhận ra mình cần search.

Lớp này xử lý đúng cảm giác "con người": nếu pre-detector không bắt được một
câu hỏi lắt léo, nhưng main brain tự surface kiểu "đợi em tra web đã", runtime
không được để câu đó thành lời hứa rỗng. Nó sẽ chuyển lời hứa ấy thành hành động:
spawn Tutor/Gemini Web ở nền, rồi nhắn lại khi xong.

Không dùng Gemini API, Google AI grounding hay Deep Research. Chỉ dùng Tutor hiện
có và các guard nhẹ quanh nó. Vâng, capability thật sự, không phải cosplay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from runtime.tutor_research_policy import (
    is_explicit_research_request,
    needs_external_verification,
    normalize_marker_text,
)

TutorSelfSearchMode = Literal["none", "quick_search", "research"]


_SELF_SEARCH_PROMISE_MARKERS = (
    # "Tôi/em sẽ tra" family.
    "de em tra", "de toi tra", "de minh tra", "de t tra", "de anh tra",
    "em se tra", "toi se tra", "minh se tra", "t se tra", "anh se tra",
    "em di tra", "toi di tra", "minh di tra", "t di tra",
    "em tra nhanh", "toi tra nhanh", "minh tra nhanh", "tra nhanh da",
    "doi em tra", "doi toi tra", "cho em tra", "cho toi tra", "cho minh tra",
    "doi em kiem tra", "doi toi kiem tra", "cho em kiem tra", "cho toi kiem tra",
    "de em kiem tra", "de toi kiem tra", "em kiem tra lai", "toi kiem tra lai",
    "em se kiem tra", "toi se kiem tra", "can kiem tra lai",
    # Search/web/tutor family.
    "de em search", "de toi search", "em se search", "toi se search",
    "search nhanh da", "check web da", "de em check web", "de toi check web",
    "de em len web", "de toi len web", "em len web", "toi len web",
    "hoi gemini da", "de em hoi gemini", "de toi hoi gemini",
    "hoi tutor da", "de em hoi tutor", "de toi hoi tutor",
    # More human phrasing.
    "em chua dam chot", "toi chua dam chot", "chua dam noi bua",
    "em chua chac", "toi chua chac", "khong chac nen phai tra",
    "phai doi chieu", "can doi chieu", "can nguon ngoai",
)

_SELF_SEARCH_NEGATION_MARKERS = (
    "khong can tra", "khong can search", "dung search", "dung tra", "khoi tra",
    "khong search", "khong dung web", "khong hoi gemini", "khong hoi tutor",
)

_UNCERTAIN_BUT_CAPABLE_MARKERS = (
    "chua chac", "khong chac", "chua chot", "chua dam chot", "chua dam noi",
    "can kiem tra", "phai kiem tra", "can doi chieu", "phai doi chieu",
)

_RESEARCH_MODE_MARKERS = (
    "nghien cuu", "dao sau", "phan tich ky", "kiem chung", "doi chieu", "nhieu nguon",
    "nguon chinh", "fix elegant", "bao cao", "toan dien",
)


@dataclass(frozen=True, slots=True)
class TutorSelfSearchDecision:
    should_start: bool
    mode: TutorSelfSearchMode
    reason: str
    score: float


def _has_marker(normalized: str, marker: str) -> bool:
    if not normalized or not marker:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(marker.strip()) + r"(?![a-z0-9])"
    return re.search(pattern, normalized) is not None


def _has_any(normalized: str, markers) -> bool:
    return any(_has_marker(normalized, marker) for marker in markers)


def _choose_mode(user_norm: str, assistant_norm: str) -> TutorSelfSearchMode:
    joined = f"{user_norm} {assistant_norm}".strip()
    if is_explicit_research_request(joined) or _has_any(joined, _RESEARCH_MODE_MARKERS):
        return "research"
    return "quick_search"


def detect_self_search_request(
    user_text: object,
    assistant_text: object,
    *,
    already_delegated: bool = False,
) -> TutorSelfSearchDecision:
    """Nhận ra lời hứa tự-search trong câu Eris vừa định nói.

    `already_delegated=True` nghĩa là pre-detector đã chạy Tutor rồi, khi đó
    không spawn thêm lần hai. Một lời hứa đã có hành động phía sau thì không cần
    nhân bản thành hai worker, loài người gọi đó là "race condition", rất thơ.
    """

    if already_delegated:
        return TutorSelfSearchDecision(False, "none", "already_delegated", 0.0)
    user_norm = normalize_marker_text(user_text)
    assistant_norm = normalize_marker_text(assistant_text)
    if not assistant_norm:
        return TutorSelfSearchDecision(False, "none", "empty_assistant", 0.0)
    if _has_any(assistant_norm, _SELF_SEARCH_NEGATION_MARKERS):
        return TutorSelfSearchDecision(False, "none", "explicit_no_search", 0.0)

    promised = _has_any(assistant_norm, _SELF_SEARCH_PROMISE_MARKERS)
    uncertain = _has_any(assistant_norm, _UNCERTAIN_BUT_CAPABLE_MARKERS)
    external_risk = needs_external_verification(user_norm)

    if promised:
        mode = _choose_mode(user_norm, assistant_norm)
        return TutorSelfSearchDecision(True, mode, "assistant_self_search_promise", 0.91)
    if uncertain and external_risk:
        mode = _choose_mode(user_norm, assistant_norm)
        return TutorSelfSearchDecision(True, mode, "assistant_uncertain_external_risk", 0.84)
    return TutorSelfSearchDecision(False, "none", "no_self_search_signal", 0.0)


def build_self_search_wait_text(*, mode: TutorSelfSearchMode, topic: object = "") -> str:
    """Câu nhịp 1: nói đợi, không đoán đáp án."""

    topic_text = str(topic or "").strip()
    if len(topic_text) > 90:
        topic_text = topic_text[:87].rstrip() + "..."
    if mode == "research":
        if topic_text:
            return f"Tôi chưa chốt vội vụ “{topic_text}”. Đợi tôi hỏi Tutor và đối chiếu nguồn một chút, xong tôi nhắn tiếp."
        return "Tôi chưa chốt vội vụ này. Đợi tôi hỏi Tutor và đối chiếu nguồn một chút, xong tôi nhắn tiếp."
    if topic_text:
        return f"Tôi chưa muốn đoán mò vụ “{topic_text}”. Đợi tôi search nhanh một chút, xong tôi nhắn lại."
    return "Tôi chưa muốn đoán mò vụ này. Đợi tôi search nhanh một chút, xong tôi nhắn lại."


__all__ = [
    "TutorSelfSearchDecision",
    "detect_self_search_request",
    "build_self_search_wait_text",
]
