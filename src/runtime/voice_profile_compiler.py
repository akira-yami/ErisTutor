# -*- coding: utf-8 -*-
"""Deterministic compiler from semantic performance preset to voice metadata.

The semantic authority chooses one compact preset and intensity.  This module
compiles that immutable choice into the delivery contract already consumed by
StageOutputPipeline/ElevenLabs.  It performs no network I/O and never rewrites
public or spoken text.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    style: str
    speed: float
    energy: float
    warmth: float
    pitch: str = "neutral"
    vocalization: str = "none"
    vocalization_position: str = "prefix"


_PROFILES: dict[str, VoiceProfile] = {
    "neutral": VoiceProfile("neutral", 1.00, 0.40, 0.58),
    "focused": VoiceProfile("focused", 1.01, 0.48, 0.52, "slightly_low"),
    "soft_warm": VoiceProfile("warm_soft", 0.97, 0.34, 0.74),
    "warm_embarrassed": VoiceProfile("warm_soft", 0.96, 0.32, 0.72, "slightly_high", "soft_hum"),
    "dry_tease": VoiceProfile("dry_tease", 0.99, 0.42, 0.55, "slightly_low", "small_laugh", "suffix"),
    "playful": VoiceProfile("soft_tease", 1.02, 0.50, 0.70, "slightly_high", "small_laugh", "suffix"),
    "curious": VoiceProfile("curious_soft", 1.01, 0.42, 0.66, "slightly_high", "curious_hm"),
    "cold_precision": VoiceProfile("cold_boundary", 0.98, 0.38, 0.38, "slightly_low"),
    "annoyed_soft": VoiceProfile("annoyed_soft", 1.00, 0.48, 0.44, "slightly_low", "soft_sigh"),
    "sad_distant": VoiceProfile("low_quiet", 0.95, 0.28, 0.42, "slightly_low", "soft_sigh"),
    "repair": VoiceProfile("warm_soft", 0.96, 0.30, 0.76, "slightly_low", "gentle_ack"),
    "surprised": VoiceProfile("curious_soft", 1.03, 0.54, 0.64, "slightly_high", "soft_surprise"),
}

_NONVERBAL_TO_VOCALIZATION = {
    "none": "none",
    "small_breath": "soft_hum",
    "soft_breath": "soft_hum",
    "soft_hum": "soft_hum",
    "gentle_ack": "gentle_ack",
    "small_laugh": "small_laugh",
    "laugh_soft": "small_laugh",
    "soft_sigh": "soft_sigh",
    "sigh_soft": "soft_sigh",
    "scoff_soft": "soft_sigh",
    "curious_hm": "curious_hm",
    "soft_surprise": "soft_surprise",
}


def _clamp(value: object, low: float, high: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def compile_voice_plan(
    *,
    preset: object,
    intensity: object = 0.5,
    nonverbal: object = "none",
    fallback_style: object = "neutral",
) -> dict[str, Any]:
    """Compile one stable performance choice into the legacy voice-plan shape."""
    preset_name = str(preset or "neutral").strip().lower() or "neutral"
    profile = _PROFILES.get(preset_name)
    if profile is None:
        fallback_name = str(fallback_style or "neutral").strip().lower() or "neutral"
        # Existing prosody styles can pass through without inventing semantics.
        profile = VoiceProfile(fallback_name, 1.00, 0.40, 0.58)

    level = _clamp(intensity, 0.0, 1.0, 0.5)
    delta = level - 0.5
    speed = _clamp(profile.speed + delta * 0.04, 0.94, 1.06, profile.speed)
    energy = _clamp(profile.energy + delta * 0.20, 0.26, 0.62, profile.energy)
    warmth = _clamp(profile.warmth + delta * 0.10, 0.35, 0.85, profile.warmth)

    cue_name = str(nonverbal or "none").strip().lower() or "none"
    vocalization = _NONVERBAL_TO_VOCALIZATION.get(cue_name, profile.vocalization)
    position = "suffix" if vocalization == "small_laugh" else profile.vocalization_position

    return {
        "version": 4,
        "source": "deterministic_voice_profile_compiler_v1",
        "global_style": profile.style,
        "delivery": {
            "style": profile.style,
            "emphasis": [],
            "speed": round(speed, 3),
            "energy": round(energy, 3),
            "warmth": round(warmth, 3),
            "pitch": profile.pitch,
            "pause_after_ms": 0,
            "vocalization": vocalization,
            "vocalization_position": position,
        },
        "reason": f"preset={preset_name};intensity={level:.2f}",
    }


def compile_voice_plan_from_payload(
    payload: Mapping[str, Any] | None,
    *,
    fallback_style: object = "neutral",
) -> dict[str, Any]:
    data = dict(payload or {})
    performance = data.get("performance") if isinstance(data.get("performance"), Mapping) else {}
    utterance = data.get("utterance") if isinstance(data.get("utterance"), Mapping) else {}
    if utterance and isinstance(utterance.get("performance"), Mapping):
        performance = dict(utterance.get("performance") or {})
    return compile_voice_plan(
        preset=performance.get("preset", data.get("performance_hint", fallback_style)),
        intensity=performance.get("intensity", data.get("performance_intensity", 0.5)),
        nonverbal=performance.get("nonverbal", data.get("nonverbal_cue", "none")),
        fallback_style=fallback_style,
    )


__all__ = ["VoiceProfile", "compile_voice_plan", "compile_voice_plan_from_payload"]
