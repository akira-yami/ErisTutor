# -*- coding: utf-8 -*-
"""Shared salience/attention routing for structured world events.

Sensors never decide to speak.  They only produce facts/events.  This router
turns a semantic edge into a small set of *non-public* actions such as update,
remember, seed, or wake cognition.  Public speech remains the responsibility of
the executive governor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True, slots=True)
class AttentionDecision:
    salience: float
    actions: tuple[str, ...]
    reason: str
    event_key: str = ""
    reversible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "salience": round(self.salience, 3),
            "actions": list(self.actions),
            "reason": self.reason,
            "event_key": self.event_key,
            "reversible": self.reversible,
        }


class AttentionRouter:
    """One routing policy shared by environment/chat/seed cognition."""

    def route_environment(self, awareness: Mapping[str, Any]) -> AttentionDecision:
        confidence = _clamp01(awareness.get("confidence", 0.0))
        delta = bool(awareness.get("semantic_delta", False))
        wake = bool(awareness.get("wake_recommended", False))
        level = max(0, min(4, int(awareness.get("max_attention_level", 0) or 0)))
        interruption = _clamp01(awareness.get("interruption_cost", 0.0))
        recent: Sequence[Mapping[str, Any]] = tuple(
            item for item in tuple(awareness.get("recent_events", ()) or ()) if isinstance(item, Mapping)
        )
        latest = recent[-1] if recent else {}
        importance = _clamp01(latest.get("importance", 0.0))
        novelty = _clamp01(latest.get("novelty", 0.0))
        event_conf = _clamp01(latest.get("confidence", confidence))
        event_key = str(latest.get("event_key", latest.get("kind", "")) or "")[:160]

        salience = _clamp01(
            0.18 * confidence
            + 0.18 * event_conf
            + 0.22 * importance
            + 0.18 * novelty
            + 0.08 * min(1.0, level / 3.0)
            + (0.16 if delta else 0.0)
        )
        actions: list[str] = ["update_world"]
        if delta and salience >= 0.38:
            actions.append("remember")
        if delta and salience >= 0.50:
            actions.append("generate_seed")
        if (wake or level >= 2) and salience >= 0.58:
            actions.append("wake_cognition")
        # Important: this is only a candidate. It does not authorize speech.
        if level >= 4 and salience >= 0.90 and interruption <= 0.35:
            actions.append("interrupt_candidate")

        reason = (
            f"environment_edge delta={int(delta)} conf={confidence:.2f} event_conf={event_conf:.2f} "
            f"importance={importance:.2f} novelty={novelty:.2f} level={level} interrupt={interruption:.2f}"
        )
        return AttentionDecision(salience=salience, actions=tuple(actions), reason=reason, event_key=event_key)


__all__ = ["AttentionDecision", "AttentionRouter"]
