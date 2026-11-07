# -*- coding: utf-8 -*-
"""speech_delivery_planner.py — cầu nối cảm xúc nền -> cách biểu diễn.

ERIS đã có affect nền: valence/arousal/stance, curiosity, restraint, overload...
Nhưng TTS/avatar không nên đọc thẳng các số đó. Module này dịch chúng thành một
``SpeechDeliveryPlan`` nhỏ: giọng đọc, emotion sân khấu, motion nhẹ, action không
lời như ``laugh_soft`` hoặc ``small_smile``.

Quan trọng: đây là event sân khấu, không phải text.  Runtime không bắt model viết
"(cười khẽ)" vào câu; nếu có cười thì planner đưa nó vào ``pre_speech_actions`` để
UI/avatar/TTS dùng, còn memory và TTS transcript vẫn sạch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class AffectSignals:
    valence: float = 0.0
    arousal: float = 0.3
    stance: str = ""
    curiosity: float = 0.0
    restraint: float = 0.0
    attachment: float = 0.0
    overload: float = 0.0
    irritation: float = 0.0
    fatigue: float = 0.0


@dataclass(frozen=True, slots=True)
class SpeechDeliveryPlan:
    voice_style: str = "neutral"
    emotion: str = "neutral"
    motion: str = "none"
    pre_speech_actions: tuple[str, ...] = ()
    post_speech_actions: tuple[str, ...] = ()
    prosody_notes: str = ""
    stage_notes: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "voice_style": self.voice_style,
            "emotion": self.emotion,
            "motion": self.motion,
            "pre_speech_actions": list(self.pre_speech_actions),
            "post_speech_actions": list(self.post_speech_actions),
            "prosody_notes": self.prosody_notes,
            "stage_notes": self.stage_notes,
        }


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value: object, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _safe_float(value, default)))


def _unique(items: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return tuple(out)


def affect_signals_from(payload: dict[str, Any], frame: Any = None) -> AffectSignals:
    """Rút affect hiện có mà không bắt stage phụ thuộc sâu vào cognition.

    Các repo nhỏ hay hỏng ở đây vì stage import thẳng object nội tâm phức tạp. Ta
    chỉ đọc bằng ``getattr``/dict, thiếu gì thì dùng default. Nhờ vậy test offline
    và runtime cũ vẫn chạy được.
    """

    emotion_debug = payload.get("emotion_debug") if isinstance(payload, dict) else {}
    mood = {}
    if isinstance(emotion_debug, dict) and isinstance(emotion_debug.get("summary"), dict):
        mood = emotion_debug.get("summary") or {}

    appraisal = getattr(frame, "appraisal", None) if frame is not None else None
    internal_state = getattr(frame, "internal_state", None) if frame is not None else None
    if internal_state is None:
        internal_state = getattr(frame, "state", None) if frame is not None else None

    stance = str(getattr(appraisal, "stance", "") or "").strip().lower()
    valence = _safe_float(mood.get("valence"), 0.0) if isinstance(mood, dict) else 0.0
    arousal = _safe_float(getattr(appraisal, "arousal", None), 0.3)
    if arousal == 0.3 and isinstance(mood, dict):
        arousal = _safe_float(mood.get("arousal"), 0.3)

    return AffectSignals(
        valence=max(-1.0, min(1.0, valence)),
        arousal=_clamp01(arousal, 0.3),
        stance=stance,
        curiosity=_clamp01(getattr(internal_state, "curiosity", 0.0), 0.0),
        restraint=_clamp01(getattr(internal_state, "restraint", 0.0), 0.0),
        attachment=_clamp01(getattr(internal_state, "attachment", 0.0), 0.0),
        overload=_clamp01(getattr(internal_state, "overload", 0.0), 0.0),
        irritation=_clamp01(getattr(internal_state, "tension", 0.0), 0.0),
        fatigue=_clamp01(getattr(internal_state, "fatigue", 0.0), 0.0),
    )


class SpeechDeliveryPlanner:
    """Chọn cách nói/ngắt/mặt từ affect nền.

    Planner này cố ý rule-based. Model nhỏ chỉ cần viết câu; tầng sân khấu chịu
    trách nhiệm biến "playful + restraint" thành giọng dry tease hay small smile.
    """

    def plan(
        self,
        text: str,
        *,
        intent: str = "",
        delivery_hint: str = "",
        explicit_emotion: str = "",
        explicit_motion: str = "",
        extracted_actions: Iterable[str] = (),
        affect: AffectSignals | None = None,
    ) -> SpeechDeliveryPlan:
        signals = affect or AffectSignals()
        hay = " ".join([str(text or ""), str(intent or ""), str(explicit_emotion or ""), signals.stance]).lower()
        actions = list(extracted_actions or ())

        voice_style = "neutral"
        emotion = explicit_emotion or "neutral"
        motion = explicit_motion or "none"
        notes: list[str] = []

        if signals.overload >= 0.68 or signals.fatigue >= 0.72:
            voice_style = "low_quiet"
            emotion = "distant" if signals.overload >= 0.68 else "tired_soft"
            motion = motion if motion != "none" else "look_down"
            notes.append("Low energy. Short breath groups. Do not dramatize the exhaustion.")
        elif self._looks_teasing(hay, signals):
            voice_style = "dry_tease" if signals.restraint >= 0.48 or signals.valence < 0.18 else "soft_tease"
            emotion = "dry_tease" if voice_style == "dry_tease" else "mischief_soft"
            motion = motion if motion != "none" else ("look_away" if voice_style == "dry_tease" else "small_smile")
            if "laugh_soft" not in actions and signals.valence >= 0.08 and signals.restraint <= 0.78:
                actions.append("laugh_soft")
            notes.append("Put the smile in the voice at the intensity supported by current valence/arousal; do not flatten it just to preserve a restrained persona.")
        elif self._looks_boundary(hay, signals):
            voice_style = "deadpan"
            emotion = "flat_calm"
            motion = motion if motion != "none" else "blink_slow"
            notes.append("Deadpan restraint. Crisp ending, no apology tone.")
        elif signals.curiosity >= 0.58 or "curious" in hay or "tò mò" in hay:
            voice_style = "curious_soft"
            emotion = "curious"
            motion = motion if motion != "none" else "tilt_head"
            actions.append("tilt_head")
            notes.append("Light curiosity. Lift the question gently, not like an interview.")
        elif signals.arousal >= 0.62:
            voice_style = "focused"
            emotion = "focused"
            motion = motion if motion != "none" else "focus"
            notes.append("Clear and steady. Technical precision first.")
        elif signals.valence > 0.18 or signals.attachment >= 0.55:
            voice_style = "warm_soft"
            emotion = "warm_soft"
            motion = motion if motion != "none" else "lean_in_small"
            notes.append("Positive/attached state is active: let it register naturally. Brightness follows live arousal and social energy; no fixed quiet/not-bubbly ceiling.")
        elif signals.valence < -0.18:
            voice_style = "low_quiet"
            emotion = "quiet_low"
            motion = motion if motion != "none" else "look_aside"
            notes.append("Quiet, controlled, a little withdrawn.")

        if signals.restraint >= 0.72 and voice_style in {"soft_tease", "warm_soft", "curious_soft"}:
            notes.append("Restraint is high: reduce brightness and keep pitch narrow.")

        hinted_style = str(delivery_hint or "").strip().lower()
        if hinted_style in {
            "neutral",
            "focused",
            "warm_soft",
            "curious_soft",
            "soft_tease",
            "dry_tease",
            "self_aware_dry",
            "annoyed_soft",
            "sharp_deadpan",
            "cold_boundary",
            "deadpan",
            "low_quiet",
        }:
            # LLM được quyền chọn delivery ở field riêng, nhưng planner vẫn giữ
            # action/motion và notes từ affect nền để câu không thành diễn quá tay.
            voice_style = hinted_style
            if hinted_style in {"dry_tease", "soft_tease"}:
                emotion = "dry_tease" if hinted_style == "dry_tease" else "mischief_soft"
            elif hinted_style == "self_aware_dry":
                emotion = "flat_calm"
            elif hinted_style in {"annoyed_soft", "sharp_deadpan"}:
                emotion = "annoyed_soft"
            elif hinted_style == "cold_boundary":
                emotion = "flat_cold"
            elif hinted_style == "curious_soft":
                emotion = "curious"
            elif hinted_style == "deadpan":
                emotion = "flat_calm"
            elif hinted_style == "low_quiet":
                emotion = "quiet_low"

        return SpeechDeliveryPlan(
            voice_style=voice_style,
            emotion=emotion,
            motion=motion or "none",
            pre_speech_actions=_unique(actions),
            post_speech_actions=(),
            prosody_notes=" ".join(notes).strip(),
            stage_notes=self._stage_notes(signals),
        )

    @staticmethod
    def _looks_teasing(hay: str, signals: AffectSignals) -> bool:
        if any(token in hay for token in ("tease", "playful", "amused", "châm chọc", "trêu", "cà khịa")):
            return True
        if any(token in hay for token in ("lại", "đáng nghi", "hay nhỉ", "giỏi", "vá bằng", "toang")):
            return signals.valence >= -0.12
        return signals.valence > 0.24 and signals.restraint >= 0.35

    @staticmethod
    def _looks_boundary(hay: str, signals: AffectSignals) -> bool:
        if signals.irritation >= 0.58 and signals.restraint >= 0.45:
            return True
        return any(token in hay for token in ("đừng", "không phải", "bớt", "thôi", "guarded", "detached", "irritated"))

    @staticmethod
    def _stage_notes(signals: AffectSignals) -> str:
        return (
            f"affect valence={signals.valence:.2f} arousal={signals.arousal:.2f} "
            f"restraint={signals.restraint:.2f} curiosity={signals.curiosity:.2f}"
        )


__all__ = [
    "AffectSignals",
    "SpeechDeliveryPlan",
    "SpeechDeliveryPlanner",
    "affect_signals_from",
]
