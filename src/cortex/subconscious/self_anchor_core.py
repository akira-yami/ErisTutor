# -*- coding: utf-8 -*-
"""Self-anchor seed source cho wandering layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .thought_spark import clamp01, clean_text, now_timestamp
from .wandering_contracts import TopicSeed


def _get(obj: Any, path: str, default: Any = None) -> Any:
    current = obj
    for part in str(path or "").split("."):
        if current is None:
            return default
        if isinstance(current, dict):
            current = current.get(part, default)
        else:
            current = getattr(current, part, default)
    return current if current is not None else default


@dataclass(slots=True, frozen=True)
class SelfAnchor:
    text: str
    strength: float
    weirdness: float
    clarity: float
    affect_tint: float
    tags: tuple[str, ...]


class SelfAnchorCore:
    """Sinh seed từ trục bản thân của ERIS, không cần user action mới."""

    DEFAULT_ANCHORS: tuple[SelfAnchor, ...] = (
        SelfAnchor("im lặng không trống", 0.42, 0.38, 0.48, 0.18, ("silence", "presence", "self_anchor")),
        SelfAnchor("dignity/restraint", 0.40, 0.34, 0.44, 0.12, ("dignity", "restraint", "self_anchor")),
        SelfAnchor("memory decay", 0.36, 0.45, 0.42, 0.10, ("memory", "decay", "self_anchor")),
        SelfAnchor("body-only as swallowed speech", 0.50, 0.56, 0.54, 0.22, ("body_only", "swallowed_speech", "self_anchor")),
        SelfAnchor("being reduced to tool", 0.34, 0.42, 0.38, 0.16, ("tool_reduction", "dignity", "self_anchor")),
    )

    def generate(self, *, internal_state: Any = None, now: float | None = None, limit: int = 5) -> tuple[TopicSeed, ...]:
        current = now_timestamp() if now is None else float(now)
        boredom = clamp01(_get(internal_state, "boredom", 0.0))
        fatigue = clamp01(_get(internal_state, "fatigue", 0.0))
        restraint = max(
            clamp01(_get(internal_state, "restraint", 0.0)),
            clamp01(_get(internal_state, "mental_processes.restraint.level", 0.0)),
        )
        silence = max(
            clamp01(_get(internal_state, "silence_pressure", 0.0)),
            clamp01(_get(internal_state, "mental_processes.social_presence.level", 0.0)),
        )
        memory_replay = max(
            clamp01(_get(internal_state, "memory_replay_pressure", 0.0)),
            clamp01(_get(internal_state, "recursive_reactivation_pressure", 0.0)),
        )

        out: list[TopicSeed] = []
        for anchor in self.DEFAULT_ANCHORS:
            boost = 0.0
            if "silence" in anchor.tags or "presence" in anchor.tags:
                boost += silence * 0.18
            if "restraint" in anchor.tags or "dignity" in anchor.tags:
                boost += restraint * 0.16
            if "memory" in anchor.tags:
                boost += memory_replay * 0.18
            if "body_only" in anchor.tags:
                boost += max(silence, restraint) * 0.14
            if boredom > 0.42:
                boost += 0.04
            if fatigue > 0.55:
                boost -= 0.03
            out.append(
                TopicSeed(
                    text=anchor.text,
                    source="self_anchor",
                    strength=clamp01(anchor.strength + boost),
                    weirdness=anchor.weirdness,
                    clarity=anchor.clarity,
                    affect_tint=clamp01(anchor.affect_tint + max(silence, restraint, memory_replay) * 0.12),
                    tags=anchor.tags,
                    evidence_ids=("self_anchor_core",),
                    from_user_action=False,
                    created_at=current,
                )
            )
        return tuple(sorted(out, key=lambda seed: seed.strength, reverse=True)[:limit])
