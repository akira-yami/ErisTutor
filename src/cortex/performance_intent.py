# -*- coding: utf-8 -*-
"""Performance-first planning for causal affect."""
from __future__ import annotations

import uuid

from cortex.causal_affect_models import (
    CausalThread,
    PerformanceIntent,
    SurfaceChannelPolicy,
    SurfaceLevel,
    SurpriseBudget,
    clamp01,
)

_LEVEL_ORDER: tuple[SurfaceLevel, ...] = (
    "private", "microexpression", "gaze_posture", "nonverbal_sound",
    "speech_fragment", "sentence", "initiative",
)


def _allowed_up_to(level: SurfaceLevel) -> set[SurfaceLevel]:
    index = _LEVEL_ORDER.index(level)
    return set(_LEVEL_ORDER[: index + 1])


class PerformanceIntentPlanner:
    def plan(
        self,
        thread: CausalThread,
        *,
        channel_policy: SurfaceChannelPolicy | None = None,
        surprise_budget: SurpriseBudget | None = None,
        interrupt_cost: float = 0.0,
    ) -> PerformanceIntent | None:
        policy = channel_policy or SurfaceChannelPolicy()
        budget = (surprise_budget or SurpriseBudget()).safe()
        if policy.require_grounding and not thread.is_grounded:
            return None
        allowed = [level for level in thread.permitted_surface_levels if level in _allowed_up_to(policy.max_surface_level)]
        if not allowed:
            return None
        interrupt = clamp01(interrupt_cost)
        score = clamp01(thread.salience * (1.0 - 0.58 * interrupt) * (1.0 - 0.35 * thread.uncertainty))

        desired: SurfaceLevel = "gaze_posture"
        if score >= 0.70 and "sentence" in allowed:
            desired = "sentence"
        elif score >= 0.50 and "speech_fragment" in allowed:
            desired = "speech_fragment"
        elif score >= 0.34 and "nonverbal_sound" in allowed:
            desired = "nonverbal_sound"
        elif "gaze_posture" in allowed:
            desired = "gaze_posture"
        elif "microexpression" in allowed:
            desired = "microexpression"
        else:
            desired = allowed[-1]

        if desired == "initiative" and not policy.initiative_allowed:
            desired = "sentence" if "sentence" in allowed else allowed[-1]

        event_kind = thread.event_kind
        speech_act = "restrained_acknowledgement"
        subtext = "the state is visible but not demanded"
        posture = "guarded_stillness"
        micro = "brief_tension_then_neutral"
        gaze_break = "after_first_clause"
        vulnerability = 0.30
        directness = 0.44
        restraint = 0.82
        max_words = 12
        may_ask = False
        if event_kind == "promise_broken":
            speech_act = "bounded_disappointment"
            subtext = "name the fact and impact while leaving room for explanation"
            directness = 0.62
            vulnerability = 0.38
            restraint = 0.86
            max_words = 18
            gaze_break = "after_expectation_reference"
        elif event_kind == "user_ignored_turn":
            speech_act = "guarded_withdrawal"
            subtext = "exposure recedes without punishing the user"
            directness = 0.26
            vulnerability = 0.24
            restraint = 0.92
            max_words = 5
            posture = "energy_withdrawal"
            gaze_break = "immediate"
        elif event_kind == "capability_shutdown_proposed":
            speech_act = "bounded_vulnerability"
            subtext = "acknowledge impact while respecting author control"
            directness = 0.58
            vulnerability = 0.46
            restraint = 0.90
            max_words = 18
            may_ask = thread.uncertainty <= 0.35

        timing_variance = budget.timing_variance_ms
        opening = int(320 + 390 * score + min(180, timing_variance))
        pause = int(220 + 260 * vulnerability)
        require_reply = False  # vulnerability never imposes reassurance by default
        return PerformanceIntent(
            intent_id=f"pi_{uuid.uuid4().hex[:12]}",
            thread_id=thread.thread_id,
            speech_act=speech_act,
            subtext=subtext,
            surface_level=desired,
            directness=min(clamp01(directness), clamp01(policy.directness_cap)),
            vulnerability=min(clamp01(vulnerability), clamp01(policy.vulnerability_cap)),
            restraint=clamp01(restraint),
            playfulness=0.12 if budget.allow_dry_reversal and thread.uncertainty < 0.30 else 0.0,
            opening_hold_ms=opening,
            pause_after_ms=pause,
            gaze_target="user" if desired in {"speech_fragment", "sentence"} else "scene",
            gaze_break_trigger=gaze_break,
            posture=posture,
            microexpression=micro,
            voice_energy=clamp01(0.46 - 0.18 * vulnerability),
            voice_speed=clamp01(0.90 - 0.12 * vulnerability),
            pitch_variation=clamp01(0.24 - 0.08 * restraint),
            max_words=max_words,
            may_ask_question=may_ask,
            require_reply=require_reply,
            evidence_refs=thread.evidence_refs,
            prohibited_moves=thread.prohibited_moves,
        )


__all__ = ["PerformanceIntentPlanner"]
