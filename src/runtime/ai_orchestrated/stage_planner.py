# -*- coding: utf-8 -*-
"""Ánh xạ ý định biểu hiện sang metadata sân khấu cơ học.

Main model vẫn sở hữu câu public. IdentityExpressionBridge chỉ chọn preset dựa
trên state đã được reducer chấp nhận; module này không sửa văn và không chèn tag
hành động vào text.
"""

from __future__ import annotations
from core.di import resolve_dependency

from dataclasses import dataclass
from typing import Any

from .contracts import RuntimeMindState
from .expression import IdentityExpressionBridge


@dataclass(frozen=True, slots=True)
class StagePlan:
    animation: str = "stand"
    next_animation: str = ""
    delivery_hint: str = ""
    nonverbal_cue: str = ""
    stage_motion: str = ""
    direction: int = 0
    target_x: float = -1.0
    bounce_action: str = ""


_STAGE_PRESETS: dict[str, StagePlan] = {
    "neutral": StagePlan(),
    "soft_warm": StagePlan(delivery_hint="warm_soft", nonverbal_cue="small_smile", stage_motion="lean_in_small"),
    "guarded_warm": StagePlan(delivery_hint="restrained_warmth", nonverbal_cue="soft_breath", stage_motion="blink_slow"),
    "warm_embarrassed": StagePlan(delivery_hint="gentle_low", nonverbal_cue="soft_breath", stage_motion="look_away"),
    "blush_affection": StagePlan(delivery_hint="warm_soft", nonverbal_cue="soft_breath", stage_motion="look_away"),
    "dry_tease": StagePlan(delivery_hint="dry_tease", nonverbal_cue="scoff_soft", stage_motion="blink_slow"),
    "playful": StagePlan(delivery_hint="soft_tease", nonverbal_cue="laugh_soft", stage_motion="tilt_head"),
    "curious": StagePlan(delivery_hint="curious_soft", nonverbal_cue="small_pause", stage_motion="tilt_head"),
    "focused": StagePlan(animation="read", delivery_hint="focused", stage_motion="still"),
    "cold_precision": StagePlan(delivery_hint="cold_precision", stage_motion="still"),
    "annoyed_soft": StagePlan(delivery_hint="annoyed_soft", nonverbal_cue="scoff_soft", stage_motion="look_away"),
    "sad_distant": StagePlan(delivery_hint="low_quiet", nonverbal_cue="soft_breath", stage_motion="look_down"),
    "repair": StagePlan(delivery_hint="low_direct", nonverbal_cue="nod_small", stage_motion="blink_slow"),
    "surprised": StagePlan(delivery_hint="surprised_soft", nonverbal_cue="small_gasp", stage_motion="lean_back_small"),
    "yan_warning": StagePlan(delivery_hint="cold_low", nonverbal_cue="still", stage_motion="still"),
}


class StagePlanner:
    def __init__(self, *, expression_bridge: IdentityExpressionBridge | None = None) -> None:
        self.expression_bridge = resolve_dependency(expression_bridge, lambda: IdentityExpressionBridge())

    def plan(
        self,
        hint: str,
        *,
        fallback: str = "neutral",
        state: RuntimeMindState | None = None,
        channel: str = "chat",
    ) -> tuple[StagePlan, dict[str, Any]]:
        expression = self.expression_bridge.derive(
            state,
            requested_hint=hint or fallback,
            channel=channel,
        )
        key = str(expression.preset or fallback or "neutral").strip().lower().replace("-", "_").replace(" ", "_")
        plan = _STAGE_PRESETS.get(key, _STAGE_PRESETS.get(fallback, StagePlan()))
        return plan, expression.to_dict()

    def apply(
        self,
        decision: Any,
        hint: str = "",
        *,
        state: RuntimeMindState | None = None,
        channel: str = "chat",
    ) -> Any:
        plan, expression = self.plan(
            hint or getattr(decision, "performance_hint", ""),
            state=state,
            channel=channel,
        )
        if not str(getattr(decision, "animation", "") or "").strip() or getattr(decision, "animation", "") == "stand":
            decision.animation = plan.animation
        decision.next_animation = str(getattr(decision, "next_animation", "") or "") or plan.next_animation
        decision.delivery_hint = str(getattr(decision, "delivery_hint", "") or "") or plan.delivery_hint
        decision.nonverbal_cue = str(getattr(decision, "nonverbal_cue", "") or "") or plan.nonverbal_cue
        decision.stage_motion = str(getattr(decision, "stage_motion", "") or "") or plan.stage_motion
        decision.performance_hint = str(expression.get("preset", "") or hint or "neutral")
        if int(getattr(decision, "direction", 0) or 0) == 0:
            decision.direction = plan.direction
        if float(getattr(decision, "target_x", -1.0) or -1.0) < 0.0:
            decision.target_x = plan.target_x
        decision.bounce_action = str(getattr(decision, "bounce_action", "") or "") or plan.bounce_action

        meta = dict(getattr(decision, "behavior_observation_meta", {}) or {})
        meta["identity_expression"] = expression
        decision.behavior_observation_meta = meta
        return decision


__all__ = ["StagePlan", "StagePlanner"]
