from __future__ import annotations

"""Các contract nhỏ cho Screen Text Index.

Mục tiêu là biến UIA/OCR thành evidence có nguồn, độ tươi và confidence rõ ràng.
Không object nào trong file này được tự ghi semantic memory.
"""

from dataclasses import dataclass, field
from typing import Literal

ScreenRouteMode = Literal["none", "text_evidence", "text_evidence_soft", "vision", "ask_or_abstain"]
Bounds = tuple[int, int, int, int]


@dataclass(slots=True, frozen=True)
class ActiveWindowInfo:
    title: str = ""
    hwnd: int = 0
    bounds: Bounds | None = None
    process_name: str = ""
    pid: int = 0
    window_class: str = ""
    executable_path: str = ""


@dataclass(slots=True, frozen=True)
class ScreenTextBlock:
    block_id: str
    text: str
    source: str
    confidence: float
    window_title: str
    captured_at: float
    bounds: Bounds | None = None
    content_hash: str = ""

    def to_grounding_item(self, *, now: float, stale_after: float = 8.0) -> dict[str, object]:
        age = max(0.0, float(now) - float(self.captured_at))
        stale = age > max(0.1, float(stale_after))
        return {
            "id": self.block_id,
            "source": self.source,
            "claim": self.text,
            "confidence": max(0.0, min(1.0, float(self.confidence))),
            "grounded": True,
            "stale": stale,
            "captured_at": self.captured_at,
            "window_title": self.window_title,
            "bounds": self.bounds,
            "tags": ["screen", "screen_text", self.source],
        }


@dataclass(slots=True, frozen=True)
class ScreenTextSnapshot:
    window: ActiveWindowInfo
    blocks: tuple[ScreenTextBlock, ...] = ()
    captured_at: float = 0.0
    changed_ratio: float = 0.0
    revision: int = 0
    backend: str = ""
    privacy_blocked: bool = False
    errors: tuple[str, ...] = ()

    @property
    def best_confidence(self) -> float:
        return max((float(block.confidence) for block in self.blocks), default=0.0)


@dataclass(slots=True, frozen=True)
class ScreenRouteDecision:
    mode: ScreenRouteMode
    references_screen: bool = False
    needs_visual: bool = False
    requires_strong_evidence: bool = False
    reason: str = ""
    evidence: tuple[ScreenTextBlock, ...] = ()
    confidence: float = 0.0
    stale: bool = False
    debug: dict[str, object] = field(default_factory=dict)

    @property
    def should_call_vision(self) -> bool:
        return self.mode == "vision"


@dataclass(slots=True)
class PendingVisionEscalation:
    """Một yêu cầu vision trì hoãn sang lượt chat kế tiếp.

    OCR/UIA được thử trước ở lượt hiện tại. Nếu evidence quá mơ hồ, runtime
    không chụp vision ngay mà ghi một escalation ngắn hạn. Lượt chat kế tiếp
    chỉ consume escalation khi vẫn còn cùng cửa sổ hoặc cùng engagement.
    """

    pending: bool = False
    reason: str = ""
    requested_at: float = 0.0
    expires_at: float = 0.0
    window_key: str = ""
    screen_revision: int = 0
    engagement_id: str = ""
    query_hint: str = ""

    def active(self, *, now: float) -> bool:
        return bool(self.pending and self.expires_at > float(now))

    def clear(self) -> None:
        self.pending = False
        self.reason = ""
        self.requested_at = 0.0
        self.expires_at = 0.0
        self.window_key = ""
        self.screen_revision = 0
        self.engagement_id = ""
        self.query_hint = ""
