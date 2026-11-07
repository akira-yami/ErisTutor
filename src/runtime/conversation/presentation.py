# -*- coding: utf-8 -*-
"""Map mô tả delivery plain-language sang presentation hint gần nhất.

Không có chiều ngược presentation -> semantic. Nếu không map được, trả neutral.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PresentationHint:
    expression: str = "neutral"
    motion: str = "idle"
    prosody: str = "natural"


def map_delivery(delivery: str) -> PresentationHint:
    text = str(delivery or "").lower()
    if any(k in text for k in ("cười", "vui", "playful", "trêu")):
        return PresentationHint("smile", "soft_bounce", "light")
    if any(k in text for k in ("ngạc nhiên", "bất ngờ", "surpris")):
        return PresentationHint("surprised", "glance", "bright")
    if any(k in text for k in ("nghĩ", "trầm", "suy")):
        return PresentationHint("thinking", "glance", "measured")
    if any(k in text for k in ("nhẹ", "dịu", "soft")):
        return PresentationHint("soft", "idle", "soft")
    return PresentationHint()


def emit_delivery_hint(owner, delivery: str, *, turn_id: str = "", session_id: str = "") -> bool:
    """Mirror presentation hint ra stage; không mang lifecycle/floor semantic."""
    emit = getattr(owner, "_emit_stage_protocol_state", None)
    if not callable(emit):
        return False
    hint = map_delivery(delivery)
    try:
        emit(
            "conversation_delivery",
            reason="root_agent_delivery_v12",
            data={
                "turn_id": str(turn_id or "")[:180],
                "session_id": str(session_id or "")[:180],
                "expression": hint.expression,
                "motion": hint.motion,
                "prosody": hint.prosody,
                "owner": "conversation_kernel",
            },
        )
        return True
    except Exception:
        return False


__all__ = ["PresentationHint", "emit_delivery_hint", "map_delivery"]
