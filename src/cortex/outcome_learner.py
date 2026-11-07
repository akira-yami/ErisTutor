# -*- coding: utf-8 -*-
"""Bounded learning patches for causal-affect outcomes."""
from __future__ import annotations

from typing import Any

from cortex.causal_affect_models import CausalThread, LearningPatch, PerformanceIntent


class OutcomeLearner:
    def observe(
        self,
        *,
        thread: CausalThread,
        performance: PerformanceIntent,
        outcome: Any,
    ) -> LearningPatch:
        expectation_delta: dict[str, float] = {}
        channel_delta: dict[str, float] = {}
        notes: list[str] = []
        delivery_delta = 0.0

        corrected = bool(getattr(outcome, "user_corrected", False))
        intruded = bool(getattr(outcome, "user_felt_intruded", False))
        accepted = getattr(outcome, "user_accepted", None)
        ignored = bool(getattr(outcome, "user_ignored", False))
        body_match = float(getattr(outcome, "body_match_success", 1.0) or 0.0)
        action_success = float(getattr(outcome, "action_success", 1.0) or 0.0)
        ignored_reason = str(getattr(outcome, "ignored_reason", "") or "")

        if corrected:
            expectation_delta["interpretation_confidence"] = -0.08
            channel_delta["directness"] = -0.05
            notes.append("correction outranks acceptance; reduce overread")
        if intruded:
            channel_delta["directness"] = min(channel_delta.get("directness", 0.0), -0.08)
            channel_delta["initiative_frequency"] = -0.08
            notes.append("intrusion lowers directness and initiative")
        elif accepted is True:
            channel_delta["selected_surface_confidence"] = 0.04
            notes.append("acceptance supports this bounded surface level")
        if ignored:
            if not performance.require_reply:
                notes.append("silence after a non-required reply does not update rupture")
            elif "delivery" in ignored_reason or action_success < 0.5:
                delivery_delta = -0.08
                notes.append("delivery failure updates delivery reliability, not relationship")
            else:
                channel_delta["vulnerability_frequency"] = -0.04
                notes.append("unacknowledged required bid reduces future vulnerability frequency")
        if body_match < 0.70:
            channel_delta["body_speech_coherence"] = -0.06
            notes.append("body and speech mismatch requires calibration")
        return LearningPatch(
            thread_id=thread.thread_id,
            expectation_confidence_delta=expectation_delta,
            channel_policy_delta=channel_delta,
            delivery_reliability_delta=delivery_delta,
            notes=tuple(notes),
            max_step=0.08,
        ).bounded()


__all__ = ["OutcomeLearner"]
