# -*- coding: utf-8 -*-
"""Các policy thuần của Cortex: attention và nhịp heartbeat."""
from __future__ import annotations

from typing import Any

from runtime.model_budget_policy import ModelBudgetPolicy, ResourceSnapshot

from .models import CortexAttentionSignal, clamp01


class CortexAttentionPolicy:
    """Biến context/state/resource thành signal số học, không sinh public intent."""

    def evaluate(
        self,
        *,
        user_context: Any,
        internal_state: Any,
        observer_frame: Any,
        resource: ResourceSnapshot,
        budget_policy: ModelBudgetPolicy,
    ) -> CortexAttentionSignal:
        external = max(
            clamp01(getattr(user_context, "novelty", 0.0)),
            clamp01(getattr(observer_frame, "novelty", 0.0)),
            clamp01(getattr(observer_frame, "possible_error", 0.0)),
        )
        internal = clamp01(getattr(internal_state, "internal_drive", 0.0))
        thread_pressure = clamp01(getattr(internal_state, "causal_thread_pressure", 0.0))
        dominant_thread_id = str(getattr(internal_state, "dominant_thread_id", "") or "")
        interrupt = clamp01(getattr(user_context, "interrupt_cost", 0.0))
        caution = clamp01(getattr(internal_state, "resource_caution", 0.0))
        budget = budget_policy.can_run(
            "subconscious_4b",
            resource_snapshot=resource,
            reason="cortex_consolidation",
        )
        vision_hint = bool(
            getattr(observer_frame, "recommended_action", "") == "call_vision_compressor"
            or getattr(observer_frame, "should_request_vision", False)
        )
        # A grounded causal thread gets its own salience lane. Scalar dread is
        # already capped upstream when it lacks evidence.
        pressure = max(external, internal, thread_pressure)
        level = "active" if pressure >= 0.72 else "observe" if pressure >= 0.38 else "quiet"
        return CortexAttentionSignal(
            level=level,
            external_pressure=external,
            internal_drive=internal,
            causal_thread_pressure=thread_pressure,
            dominant_thread_id=dominant_thread_id,
            interrupt_cost=interrupt,
            resource_caution=caution,
            vision_refresh_recommended=vision_hint,
            consolidation_allowed=bool(getattr(budget, "allowed", False) and interrupt < 0.72),
            budget_reason=str(getattr(budget, "reason", "") or ""),
            reason="attention_snapshot",
            debug={
                "observer_available": observer_frame is not None,
                "dominant_thread_id": dominant_thread_id,
                "causal_thread_pressure": thread_pressure,
            },
        )


class CortexHeartbeatPolicy:
    """Chọn nhịp tick kế tiếp từ attention hiện tại."""

    def next_seconds(self, attention: CortexAttentionSignal) -> float:
        base = 5.0 if attention.level == "active" else 15.0 if attention.level == "observe" else 45.0
        return max(2.0, min(90.0, base * (1.0 + max(0.0, attention.interrupt_cost - 0.35))))


__all__ = ["CortexAttentionPolicy", "CortexHeartbeatPolicy"]
