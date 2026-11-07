# -*- coding: utf-8 -*-
"""Small typed intent normalizer for environment/screen awareness.

This is a deterministic fast-path, not a full language model.  The important
change is that callers consume a typed intent frame instead of each owning a
slightly different list of Vietnamese keywords.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from runtime.cognition.normalization import normalize_vi


@dataclass(frozen=True, slots=True)
class IntentFrame:
    domain: str = "conversation"
    operation: str = "respond"
    granularity: str = "general"
    confidence: float = 0.55
    requires_pixels: bool = False
    requires_current_environment: bool = False
    requires_screen_text: bool = False
    normalized_text: str = ""

    @property
    def environment_sufficient(self) -> bool:
        return self.requires_current_environment and not self.requires_pixels and not self.requires_screen_text

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "operation": self.operation,
            "granularity": self.granularity,
            "confidence": round(self.confidence, 3),
            "requires_pixels": self.requires_pixels,
            "requires_current_environment": self.requires_current_environment,
            "requires_screen_text": self.requires_screen_text,
        }


_ENV_TARGETS = {
    "activity": ("lam gi", "dang lam", "dang thao tac", "dang lam viec", "working on", "what am i doing"),
    "app": ("app nao", "ung dung nao", "dung app", "mo app", "which app", "what app"),
    "window": ("cua so nao", "window nao", "what window", "dang mo gi"),
    "process": ("process nao", "tien trinh nao"),
    "task": ("code gi", "dang code", "task gi", "viec gi", "working on"),
}

_PIXEL_MARKERS = (
    "mau gi", "mau nao", "color", "pixel", "bo cuc", "layout", "vi tri nut", "nut ben", "icon", "hinh anh",
    "nhin thay gi", "thay gi tren man hinh", "trong anh", "screenshot",
)
_TEXT_MARKERS = (
    "doc dong", "doc chu", "noi dung code", "code chinh xac", "dong code", "text tren man hinh", "chu gi", "doan dang mo",
)
_SCREEN_MARKERS = (
    "man hinh", "screen", "xem man hinh", "nhin man hinh", "tu xem", "tu nhin", "xem giup", "nhin giup",
)
_AWARENESS_VERBS = (
    "biet", "xem", "thay", "nhin", "dang", "hien tai", "current", "what", "which",
)


_ENTITY_STATUS_MARKERS = (
    "dang bi sao", "bi sao", "sao the", "sao vay", "bi gi",
    "co loi gi", "loi gi", "co van de gi", "van de gi", "on khong",
    "what is wrong", "what's wrong", "having trouble", "is it okay",
)
_ENTITY_STATUS_STOPWORDS = frozenset({
    "dang", "bi", "sao", "the", "vay", "gi", "co", "loi", "van", "de",
    "on", "khong", "hien", "tai", "nay", "kia", "do", "no", "app",
    "ung", "dung", "cua", "toi", "tui", "ban", "em", "anh", "chi",
    "what", "is", "wrong", "with", "having", "trouble", "it", "okay",
})


def looks_like_entity_status_question(user_text: str) -> bool:
    """Nhận diện *shape* hỏi trạng thái, chưa tự kết luận đó là môi trường hiện tại.

    Chú thích VI: bước này cố ý không chứa tên GitPilot/VS Code. Entity chỉ được
    bind ở bước kế tiếp với nhãn thật từ WorldState, tránh biến mọi câu “X bị sao”
    thành yêu cầu nhìn máy tính.
    """

    normalized = normalize_vi(user_text)
    return bool(normalized and _contains_any(normalized, _ENTITY_STATUS_MARKERS))


def matches_current_environment_entity_status(
    user_text: str,
    environment_labels: tuple[str, ...] | list[str],
) -> bool:
    """Bind câu hỏi trạng thái vào entity thật đang hiện diện trong WorldState.

    Chỉ trả True khi (1) câu có dạng status/health và (2) ít nhất một token có
    nghĩa của user xuất hiện trong app/process/window/task hiện tại. Vì vậy
    “docker bị sao thế” không tự route vào môi trường nếu máy đang ở GitPilot.
    """

    normalized = normalize_vi(user_text)
    if not looks_like_entity_status_question(normalized):
        return False
    query_tokens = {
        token for token in re.findall(r"[a-z0-9_#+.\-]+", normalized)
        if len(token) >= 3 and token not in _ENTITY_STATUS_STOPWORDS
    }
    if not query_tokens:
        return False
    for raw_label in tuple(environment_labels or ()):
        label = normalize_vi(raw_label)
        if not label:
            continue
        label_tokens = set(re.findall(r"[a-z0-9_#+.\-]+", label))
        compact_label = re.sub(r"[^a-z0-9]+", "", label)
        for token in query_tokens:
            compact_token = re.sub(r"[^a-z0-9]+", "", token)
            if token in label_tokens or (len(compact_token) >= 4 and compact_token in compact_label):
                return True
    return False


def _contains_any(text: str, markers: tuple[str, ...] | list[str]) -> bool:
    return any(marker and marker in text for marker in markers)


def normalize_intent(user_text: str) -> IntentFrame:
    normalized = normalize_vi(user_text)
    if not normalized:
        return IntentFrame(normalized_text="")

    requires_pixels = _contains_any(normalized, _PIXEL_MARKERS)
    requires_screen_text = _contains_any(normalized, _TEXT_MARKERS)
    screen_reference = _contains_any(normalized, _SCREEN_MARKERS)

    granularity = "general"
    env_target = False
    for key, markers in _ENV_TARGETS.items():
        if _contains_any(normalized, markers):
            granularity = key
            env_target = True
            break

    # Semantic shape: an awareness verb + an environment target is enough even
    # when the exact word order has never appeared in a hand-written marker.
    awareness_shape = _contains_any(normalized, _AWARENESS_VERBS) and env_target
    current_environment = env_target or awareness_shape

    if requires_pixels:
        return IntentFrame(
            domain="screen",
            operation="inspect_visual_state",
            granularity=granularity if granularity != "general" else "visual",
            confidence=0.91,
            requires_pixels=True,
            requires_current_environment=True,
            requires_screen_text=False,
            normalized_text=normalized,
        )
    if requires_screen_text:
        return IntentFrame(
            domain="screen",
            operation="inspect_visible_text",
            granularity="text",
            confidence=0.90,
            requires_pixels=False,
            requires_current_environment=True,
            requires_screen_text=True,
            normalized_text=normalized,
        )
    if current_environment:
        return IntentFrame(
            domain="environment",
            operation="inspect_current_state",
            granularity=granularity,
            confidence=0.88 if awareness_shape else 0.78,
            requires_pixels=False,
            requires_current_environment=True,
            requires_screen_text=False,
            normalized_text=normalized,
        )
    # Capability/limitation questions are about sensor policy/health, not a
    # request to capture a fresh frame.  This must run before generic screen
    # reference routing (e.g. "em không xem được màn hình tôi hả?").
    if re.search(r"\b(khong|co)\b.*\b(xem|nhin|thay)\b.*\b(man hinh|screen)\b", normalized):
        return IntentFrame(
            domain="environment",
            operation="inspect_perception_capability",
            granularity="capability",
            confidence=0.88,
            requires_current_environment=True,
            normalized_text=normalized,
        )

    if screen_reference:
        return IntentFrame(
            domain="screen",
            operation="inspect_screen",
            granularity="general",
            confidence=0.82,
            requires_pixels=False,
            requires_current_environment=True,
            requires_screen_text=False,
            normalized_text=normalized,
        )

    return IntentFrame(normalized_text=normalized)


__all__ = [
    "IntentFrame",
    "looks_like_entity_status_question",
    "matches_current_environment_entity_status",
    "normalize_intent",
]
