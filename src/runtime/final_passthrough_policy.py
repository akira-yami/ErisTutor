# -*- coding: utf-8 -*-
"""Generation sovereignty boundary.

Sau Decision hợp lệ đầu tiên, semantic public text thuộc về main model. Các lớp
hậu kỳ chỉ được quan sát, ghi ledger/hậu quả, điều khiển tool/motion/transport và
redact secret/protocol ở cửa xuất.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


def generation_sovereignty_enabled() -> bool:
    # Policy kiến trúc, không phải toggle vận hành. Người dùng đã yêu cầu raw
    # public sau lần parse đầu phải luôn đi qua; không để một setting cũ hay file
    # runtime vô tình bật lại dàn hậu kỳ viết lại câu. Test legacy vẫn có thể
    # monkeypatch hàm này để kiểm riêng code cũ, production thì luôn True.
    return True


# Compatibility name used by old modules/tests.
def final_passthrough_enabled() -> bool:
    return generation_sovereignty_enabled()


def _join_trace(trace: str, marker: str) -> str:
    trace = str(trace or "").strip()
    marker = str(marker or "").strip()
    return f"{trace} | {marker}" if trace and marker else (trace or marker)


def _verbose_audit_enabled() -> bool:
    raw = str(os.environ.get("ERIS_VERBOSE_POLICY_AUDIT", "0") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def audit_only(decision: Any, stage: str, *, attempted_text: str = "") -> Any:
    """Quan sát mà không làm bẩn Decision public.

    Trước đây mỗi lớp cũ đều gắn marker vào ``debug_trace`` và ``reasoning_tags``.
    Các marker đó lại bị đưa vào memory/prompt ở lượt sau, khiến model chính phải
    nói chuyện giữa một bãi biên bản nội bộ. Production giờ im lặng hoàn toàn;
    chỉ bật ``ERIS_VERBOSE_POLICY_AUDIT=1`` khi thật sự cần truy vết legacy.
    """
    if decision is None or not _verbose_audit_enabled():
        return decision
    marker = f"generation_sovereignty:observation_only:{str(stage or 'unknown').strip()}"
    if attempted_text and attempted_text != str(getattr(decision, "text", "") or ""):
        marker += ":mutation_discarded"
    try:
        decision.debug_trace = _join_trace(getattr(decision, "debug_trace", ""), marker)
    except (AttributeError, TypeError, ValueError):
        pass
    return decision


@dataclass(frozen=True, slots=True)
class PublicSpeechSnapshot:
    text: str
    should_speak: bool
    intent: str
    action: Any
    animation: str
    delivery_hint: str
    nonverbal_cue: str
    stage_motion: str
    speech_act: str
    preset_emotion: str

    @classmethod
    def capture(cls, decision: Any) -> "PublicSpeechSnapshot":
        return cls(
            text=str(getattr(decision, "text", "") or ""),
            should_speak=bool(getattr(decision, "should_speak", True)),
            intent=str(getattr(decision, "intent", "") or ""),
            action=getattr(decision, "action", ""),
            animation=str(getattr(decision, "animation", "") or ""),
            delivery_hint=str(getattr(decision, "delivery_hint", "") or ""),
            nonverbal_cue=str(getattr(decision, "nonverbal_cue", "") or ""),
            stage_motion=str(getattr(decision, "stage_motion", "") or ""),
            speech_act=str(getattr(decision, "speech_act", "") or ""),
            preset_emotion=str(getattr(decision, "preset_emotion", "") or ""),
        )

    def restore(self, decision: Any, *, stage: str) -> Any:
        if decision is None:
            return decision
        attempted = str(getattr(decision, "text", "") or "")
        for name in (
            "text", "should_speak", "intent", "action", "animation",
            "delivery_hint", "nonverbal_cue", "stage_motion", "speech_act",
            "preset_emotion",
        ):
            try:
                setattr(decision, name, getattr(self, name))
            except (AttributeError, TypeError, ValueError):
                pass
        return audit_only(decision, stage, attempted_text=attempted)


__all__ = [
    "PublicSpeechSnapshot",
    "audit_only",
    "generation_sovereignty_enabled",
    "final_passthrough_enabled",
]
