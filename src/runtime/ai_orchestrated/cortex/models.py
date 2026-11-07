# -*- coding: utf-8 -*-
"""Các contract dữ liệu của Cortex heartbeat.

Cortex chỉ tạo state và signal tài nguyên. Nó không sở hữu câu public, không có
``should_speak`` và không được lén biến attention thành speech authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from cortex.daydream_consolidation import DaydreamResult
from cortex.endogenous_drive import RollingInternalState
from cortex.user_context_stream import RollingUserContext
from runtime.model_budget_policy import ResourceSnapshot


def clamp01(value: Any, default: float = 0.0) -> float:
    """Ép giá trị số về [0, 1], kể cả khi input là NaN hoặc dữ liệu hỏng."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number != number:
        number = default
    return max(0.0, min(1.0, number))


@dataclass(frozen=True, slots=True)
class CortexAttentionSignal:
    """Signal số học cho attention, tuyệt đối không phải quyết định public turn."""

    level: str = "quiet"
    external_pressure: float = 0.0
    internal_drive: float = 0.0
    causal_thread_pressure: float = 0.0
    dominant_thread_id: str = ""
    interrupt_cost: float = 0.0
    resource_caution: float = 0.0
    vision_refresh_recommended: bool = False
    consolidation_allowed: bool = False
    budget_reason: str = ""
    reason: str = ""
    confidence: float = 1.0
    debug: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["semantic_authority"] = "none"
        return payload


@dataclass(slots=True)
class CortexRuntimeTick:
    """Kết quả bất biến về nghĩa của một heartbeat Cortex."""

    user_context: RollingUserContext
    internal_state: RollingInternalState
    attention: CortexAttentionSignal
    daydream_result: DaydreamResult | None = None
    resource_snapshot: ResourceSnapshot | None = None
    next_heartbeat_seconds: float = 30.0
    debug: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_context": self.user_context.to_dict(),
            "internal_state": self.internal_state.to_dict(),
            "attention": self.attention.to_dict(),
            "daydream_result": self.daydream_result.to_dict() if self.daydream_result else None,
            "resource_snapshot": self.resource_snapshot.to_dict() if self.resource_snapshot else None,
            "next_heartbeat_seconds": self.next_heartbeat_seconds,
            "debug": dict(self.debug),
        }


__all__ = ["CortexAttentionSignal", "CortexRuntimeTick", "clamp01"]
