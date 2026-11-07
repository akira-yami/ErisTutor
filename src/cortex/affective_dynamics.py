# -*- coding: utf-8 -*-
"""Event-driven affect dynamics cho ERIS V43.

V43 không tạo một ``mood database`` mới. ``PersistentInternalState`` vẫn là
nguồn current mood. Module này chỉ cung cấp phép biến đổi thuần:

    CognitiveEvent + social appraisal + relationship context
                              ↓
                     affect delta / impulse
                              ↓
                    PersistentInternalState

Có hai tốc độ:
- ``mood``: nằm ở các field sẵn có của PersistentInternalState và một số trục
  phụ trong ``affective_state['mood']``; phai theo phút/giờ.
- ``impulse``: phản ứng ngắn sau một event, phai theo giây/phút và chỉ làm
  biểu đạt/selection bias nhúc nhích tạm thời.

Personality không được dùng làm current mood ở đây. Nó là disposition chậm và
được projector đọc riêng ở tầng biểu đạt.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from cortex.cognitive_event import CognitiveEvent


AFFECTIVE_DYNAMICS_VERSION = "eris.affect.v43.event_driven"

# Baseline current-state hiện hữu của ERIS. Đây là điểm hồi phục trung tính,
# không phải mệnh lệnh giọng nói. Personality vẫn có baseline/disposition riêng.
CORE_BASELINE: dict[str, float] = {
    "valence": 0.02,
    "arousal": 0.16,
    "curiosity": 0.38,
    "social_energy": 0.38,
    "attachment": 0.30,
    "irritation": 0.10,
    "fatigue": 0.12,
    "tenderness": 0.16,
    "playful_pressure": 0.0,
}

AUX_BASELINE: dict[str, float] = {
    "delight": 0.19,
    "sadness": 0.22,
    "anxiety": 0.27,
    "playfulness": 0.10,
    "yearning": 0.36,
    "embarrassment": 0.18,
}

# Mood phai đủ chậm để một khoảnh khắc vui/bực không biến mất ở tick kế tiếp.
MOOD_HALF_LIFE: dict[str, float] = {
    "valence": 42 * 60.0,
    "arousal": 14 * 60.0,
    "curiosity": 38 * 60.0,
    "social_energy": 34 * 60.0,
    "attachment": 3.5 * 3600.0,
    "irritation": 26 * 60.0,
    "fatigue": 70 * 60.0,
    "tenderness": 58 * 60.0,
    "playful_pressure": 22 * 60.0,
    "delight": 24 * 60.0,
    "sadness": 52 * 60.0,
    "anxiety": 31 * 60.0,
    "playfulness": 24 * 60.0,
    "yearning": 2.5 * 3600.0,
    "embarrassment": 13 * 60.0,
}

# Impulse chỉ là dư chấn ngắn. Mỗi trục có thể phai khác nhau, thay vì một
# ``tone=happy`` giữ nguyên cho tới khi prompt khác đè lên.
IMPULSE_HALF_LIFE: dict[str, float] = {
    "valence": 95.0,
    "arousal": 70.0,
    "curiosity": 120.0,
    "social_energy": 110.0,
    "attachment": 180.0,
    "irritation": 130.0,
    "tenderness": 150.0,
    "delight": 105.0,
    "sadness": 180.0,
    "anxiety": 120.0,
    "playfulness": 105.0,
    "yearning": 240.0,
    "embarrassment": 90.0,
}

SIGNED_AXES = {"valence"}
CORE_AXES = set(CORE_BASELINE)
AUX_AXES = set(AUX_BASELINE)
ALL_AXES = CORE_AXES | AUX_AXES


def disposition_baselines(profile: Mapping[str, Any] | None = None) -> tuple[dict[str, float], dict[str, float]]:
    """Chiếu personality mood thành baseline CHẬM, không phải current affect.

    PersonalityProfile.mood có thể học/đổi rất chậm qua reducer. V43 chỉ dùng
    nó như nơi current mood hồi về. Event vẫn là thứ duy nhất được quyền tạo
    cú lệch nhanh trong PersistentInternalState.
    """

    raw_profile = dict(profile or {}) if isinstance(profile, Mapping) else {}
    raw_mood = raw_profile.get("mood") if isinstance(raw_profile.get("mood"), Mapping) else raw_profile
    mood = dict(raw_mood or {}) if isinstance(raw_mood, Mapping) else {}
    core = dict(CORE_BASELINE)
    aux = dict(AUX_BASELINE)
    if mood:
        core["arousal"] = clamp01(mood.get("energy", core["arousal"]), core["arousal"])
        core["curiosity"] = clamp01(mood.get("curiosity", core["curiosity"]), core["curiosity"])
        core["irritation"] = clamp01(mood.get("irritation", core["irritation"]), core["irritation"])
        core["tenderness"] = clamp01(mood.get("tenderness", core["tenderness"]), core["tenderness"])
        aux["delight"] = clamp01(mood.get("delight", aux["delight"]), aux["delight"])
        aux["sadness"] = clamp01(mood.get("sadness", aux["sadness"]), aux["sadness"])
        aux["anxiety"] = clamp01(mood.get("anxiety_tension", aux["anxiety"]), aux["anxiety"])
        aux["playfulness"] = clamp01(mood.get("mischief", aux["playfulness"]), aux["playfulness"])
        aux["yearning"] = clamp01(mood.get("yearning", aux["yearning"]), aux["yearning"])
        aux["embarrassment"] = clamp01(mood.get("embarrassment", aux["embarrassment"]), aux["embarrassment"])
    return core, aux


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number:
        number = float(default)
    return max(0.0, min(1.0, number))


def clamp_signed(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number:
        number = float(default)
    return max(-1.0, min(1.0, number))


def _signal(snapshot: Any, name: str) -> float:
    signal = getattr(snapshot, name, None) if snapshot is not None else None
    return clamp01(getattr(signal, "value", 0.0), 0.0)


def _state_map(state: Any) -> dict[str, Any]:
    raw = getattr(state, "affective_state", {}) if state is not None else {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _aux_mood(state: Any) -> dict[str, float]:
    payload = _state_map(state)
    raw = payload.get("mood") if isinstance(payload.get("mood"), Mapping) else {}
    return {
        key: clamp01(raw.get(key, baseline), baseline)
        for key, baseline in AUX_BASELINE.items()
    }


def _impulses(state: Any) -> dict[str, float]:
    payload = _state_map(state)
    raw = payload.get("impulse") if isinstance(payload.get("impulse"), Mapping) else {}
    result: dict[str, float] = {}
    for key in ALL_AXES:
        value = raw.get(key, 0.0)
        result[key] = clamp_signed(value, 0.0)
    return result


def _decay_to(value: float, baseline: float, elapsed: float, half_life: float, *, signed: bool = False) -> float:
    if elapsed <= 0.0:
        return clamp_signed(value) if signed else clamp01(value)
    factor = math.pow(0.5, elapsed / max(1.0, half_life))
    next_value = float(baseline) + (float(value) - float(baseline)) * factor
    return clamp_signed(next_value) if signed else clamp01(next_value)


def _apply_delta(value: float, delta: float, *, signed: bool = False) -> float:
    """Add delta với soft headroom để event mạnh không đóng đinh state ở 0/1."""

    d = max(-1.0, min(1.0, float(delta)))
    if signed:
        # Valence có hai phía nên dùng headroom theo phía delta.
        if d >= 0.0:
            return clamp_signed(value + d * (1.0 - value) * 0.72)
        return clamp_signed(value + d * (1.0 + value) * 0.72)
    x = clamp01(value)
    if d >= 0.0:
        return clamp01(x + d * (1.0 - x))
    return clamp01(x + d * x)


def effective_affect_values(state: Any) -> dict[str, float]:
    """Đọc current mood + impulse thành snapshot hiệu dụng cho voice/body/TTS.

    Đây là phép chiếu, không mutate state và không dùng personality. Nhờ một hàm
    chung, text và sân khấu không mỗi nơi tự chế một nhiệt độ cảm xúc khác nhau.
    """

    aux = _aux_mood(state)
    impulse = _impulses(state)

    def unit(key: str, base: float, gain: float = 0.60) -> float:
        return clamp01(float(base) + float(impulse.get(key, 0.0)) * gain)

    return {
        "valence": clamp_signed(float(getattr(state, "valence", CORE_BASELINE["valence"])) + impulse.get("valence", 0.0) * 0.58),
        "arousal": unit("arousal", getattr(state, "arousal", CORE_BASELINE["arousal"]), 0.62),
        "curiosity": unit("curiosity", getattr(state, "curiosity", CORE_BASELINE["curiosity"]), 0.50),
        "social_energy": unit("social_energy", getattr(state, "social_energy", CORE_BASELINE["social_energy"]), 0.56),
        "attachment": unit("attachment", getattr(state, "attachment", CORE_BASELINE["attachment"]), 0.36),
        "irritation": unit("irritation", getattr(state, "irritation", CORE_BASELINE["irritation"]), 0.58),
        "fatigue": clamp01(getattr(state, "fatigue", CORE_BASELINE["fatigue"])),
        "tenderness": unit("tenderness", getattr(state, "tenderness", CORE_BASELINE["tenderness"]), 0.54),
        "delight": unit("delight", aux["delight"], 0.66),
        "sadness": unit("sadness", aux["sadness"], 0.58),
        "anxiety": unit("anxiety", aux["anxiety"], 0.60),
        "playfulness": unit("playfulness", clamp01(aux["playfulness"] * 0.78 + getattr(state, "playful_pressure", 0.0) * 0.22), 0.68),
        "yearning": unit("yearning", aux["yearning"], 0.45),
        "embarrassment": unit("embarrassment", aux["embarrassment"], 0.62),
        "impulse_strength": max((abs(float(value)) for value in impulse.values()), default=0.0),
    }


def affective_selection_bias(state: Any) -> dict[str, float]:
    """Bias nhỏ cho candidate selection trước Neuro, không phải action policy.

    Mood chỉ thay đổi cái gì dễ được chú ý hơn. Nó không được giảm grounding,
    không tự cấp quyền speak và không sửa epistemic truth. Baseline bình thường
    cho bias gần 0 để V43 không âm thầm đổi toàn bộ tuning cũ.
    """

    affect = effective_affect_values(state)
    curiosity = clamp01(affect.get("curiosity", CORE_BASELINE["curiosity"]))
    playfulness = clamp01(affect.get("playfulness", AUX_BASELINE["playfulness"]))
    social = clamp01(affect.get("social_energy", CORE_BASELINE["social_energy"]))
    irritation = clamp01(affect.get("irritation", CORE_BASELINE["irritation"]))
    fatigue = clamp01(affect.get("fatigue", CORE_BASELINE["fatigue"]))
    return {
        "exploration_pull": clamp01((curiosity - CORE_BASELINE["curiosity"]) / max(0.01, 1.0 - CORE_BASELINE["curiosity"])),
        "playful_pull": clamp01((playfulness - AUX_BASELINE["playfulness"]) / max(0.01, 1.0 - AUX_BASELINE["playfulness"])),
        "social_withdrawal": clamp01(max(0.0, CORE_BASELINE["social_energy"] - social) / max(0.01, CORE_BASELINE["social_energy"]) * 0.72 + fatigue * 0.28),
        "friction_salience": clamp01((irritation - CORE_BASELINE["irritation"]) / max(0.01, 1.0 - CORE_BASELINE["irritation"])),
    }


@dataclass(frozen=True, slots=True)
class AffectDelta:
    """Delta nhỏ từ một event. Không field nào là fact về user."""

    mood: Mapping[str, float] = field(default_factory=dict)
    impulse: Mapping[str, float] = field(default_factory=dict)
    labels: tuple[str, ...] = ()
    strength: float = 0.0

    def compact(self) -> str:
        merged = {
            key: float(value)
            for key, value in {**dict(self.mood), **dict(self.impulse)}.items()
            if abs(float(value)) >= 0.01
        }
        parts = sorted(merged.items(), key=lambda item: abs(item[1]), reverse=True)[:7]
        return ",".join(f"{key}={value:+.2f}" for key, value in parts) or "none"


@dataclass(frozen=True, slots=True)
class AffectEventReceipt:
    event_id: str
    event_kind: str
    applied: bool
    delta: AffectDelta = field(default_factory=AffectDelta)
    reason: str = ""


class AffectiveDynamicsEngine:
    """Pure state transition engine cho mood/impulse V43."""

    def decay(
        self,
        state: Any,
        *,
        now: float | None = None,
        disposition: Mapping[str, Any] | None = None,
    ) -> Any:
        current = float(now if now is not None else time.time())
        payload = _state_map(state)
        previous = float(payload.get("last_decay_at", 0.0) or 0.0)
        if previous <= 0.0:
            previous = current
        elapsed = max(0.0, min(24.0 * 3600.0, current - previous))

        core_baseline, aux_baseline = disposition_baselines(disposition)
        core_updates: dict[str, float] = {}
        for key, baseline in core_baseline.items():
            value = getattr(state, key, baseline)
            core_updates[key] = _decay_to(
                float(value), baseline, elapsed, MOOD_HALF_LIFE[key], signed=key in SIGNED_AXES
            )

        # Lần đầu V43 nhận state, khởi tạo aux mood từ disposition chậm hiện
        # tại để không có cú nhảy giả từ PersonalityProfile sang hardcoded default.
        raw_affect = _state_map(state)
        raw_aux = raw_affect.get("mood") if isinstance(raw_affect.get("mood"), Mapping) else {}
        aux = _aux_mood(state) if raw_aux else dict(aux_baseline)
        for key, baseline in aux_baseline.items():
            aux[key] = _decay_to(aux[key], baseline, elapsed, MOOD_HALF_LIFE[key])

        impulses = _impulses(state)
        for key, value in list(impulses.items()):
            factor = math.pow(0.5, elapsed / max(1.0, IMPULSE_HALF_LIFE.get(key, 120.0))) if elapsed > 0.0 else 1.0
            impulses[key] = clamp_signed(value * factor)

        payload.update(
            {
                "version": AFFECTIVE_DYNAMICS_VERSION,
                "last_decay_at": current,
                "mood": aux,
                "impulse": impulses,
            }
        )
        return replace(state, **core_updates, affective_state=payload)

    def appraise_user_event(
        self,
        event: CognitiveEvent,
        *,
        social_appraisal: Any,
        state: Any,
    ) -> AffectDelta:
        """Map social appraisal thành phản ứng của ERIS, không suy ra cảm xúc user."""

        playful = _signal(social_appraisal, "playful")
        affection = _signal(social_appraisal, "affection")
        irritation = _signal(social_appraisal, "irritation")
        boundary = _signal(social_appraisal, "boundary")
        rupture = _signal(social_appraisal, "rupture")
        acceptance = _signal(social_appraisal, "acceptance")
        correction = _signal(social_appraisal, "correction")
        confusion = _signal(social_appraisal, "confusion")
        engagement = _signal(social_appraisal, "engagement")
        intrusion = _signal(social_appraisal, "intrusion")

        relation = getattr(state, "relationship_dynamics", None)
        trust = clamp01(getattr(relation, "trust", 0.25), 0.25)
        playful_safety = clamp01(getattr(relation, "playful_safety", 0.0), 0.0)
        rupture_memory = clamp01(getattr(relation, "rupture", 0.0), 0.0)
        safe = 0.50 + 0.50 * max(trust, playful_safety)
        guarded = 0.35 + 0.65 * rupture_memory

        # Cùng một tiếng cười không tạo cùng một phản ứng ở mọi quan hệ/state.
        # Relation chỉ modulate biên độ, không tự tạo cảm xúc khi event không có.
        playful_effect = playful * safe * (1.0 - rupture_memory * 0.45)
        affection_effect = affection * (0.55 + trust * 0.45) * (1.0 - rupture_memory * 0.35)
        conflict_effect = max(irritation, boundary * 0.72, intrusion * 0.78, rupture)

        mood = {
            "valence": +0.10 * playful_effect + 0.12 * affection_effect + 0.08 * acceptance - 0.13 * conflict_effect - 0.045 * correction,
            "arousal": +0.10 * playful_effect + 0.08 * irritation + 0.06 * confusion + 0.04 * engagement,
            "curiosity": +0.035 * confusion + 0.018 * engagement,
            "social_energy": +0.075 * engagement + 0.075 * playful_effect + 0.045 * affection_effect - 0.12 * boundary - 0.10 * rupture,
            "attachment": +0.035 * affection_effect + 0.018 * engagement - 0.025 * rupture,
            "irritation": +0.16 * irritation + 0.08 * intrusion + 0.06 * boundary + 0.035 * correction - 0.07 * playful_effect,
            "tenderness": +0.13 * affection_effect + 0.05 * acceptance - 0.07 * conflict_effect,
            "playful_pressure": +0.10 * playful_effect - 0.08 * boundary - 0.06 * rupture,
            "delight": +0.15 * playful_effect + 0.10 * acceptance + 0.07 * affection_effect - 0.08 * conflict_effect,
            "sadness": +0.07 * rupture + 0.035 * boundary - 0.045 * playful_effect,
            "anxiety": +0.09 * rupture + 0.06 * intrusion + 0.035 * confusion - 0.035 * acceptance,
            "playfulness": +0.16 * playful_effect - 0.10 * boundary - 0.08 * rupture,
            "yearning": +0.035 * affection_effect - 0.02 * boundary,
            "embarrassment": +0.14 * correction * (1.0 - playful_effect * 0.45) + 0.04 * confusion,
        }
        impulse = {
            "valence": +0.16 * playful_effect + 0.14 * affection_effect + 0.08 * acceptance - 0.16 * conflict_effect,
            "arousal": +0.22 * playful_effect + 0.15 * irritation + 0.10 * confusion,
            "curiosity": +0.07 * confusion + 0.03 * engagement,
            "social_energy": +0.16 * playful_effect + 0.09 * affection_effect - 0.18 * boundary,
            "attachment": +0.07 * affection_effect - 0.04 * rupture,
            "irritation": +0.20 * irritation + 0.11 * intrusion + 0.08 * boundary - 0.10 * playful_effect,
            "tenderness": +0.18 * affection_effect + 0.06 * acceptance - 0.08 * conflict_effect,
            "delight": +0.28 * playful_effect + 0.14 * acceptance + 0.10 * affection_effect,
            "sadness": +0.10 * rupture + 0.05 * boundary,
            "anxiety": +0.13 * rupture + 0.08 * intrusion + 0.05 * confusion,
            "playfulness": +0.30 * playful_effect - 0.14 * boundary - 0.10 * rupture,
            "yearning": +0.06 * affection_effect,
            "embarrassment": +0.20 * correction * (1.0 - playful_effect * 0.50),
        }
        labels: list[str] = []
        for label, value in (
            ("playful_contact", playful_effect),
            ("affiliative_contact", affection_effect),
            ("accepted", acceptance),
            ("corrected", correction),
            ("boundary", boundary),
            ("rupture", rupture),
        ):
            if value >= 0.30:
                labels.append(label)
        strength = max((abs(float(v)) for v in (*mood.values(), *impulse.values())), default=0.0)
        return AffectDelta(mood=mood, impulse=impulse, labels=tuple(labels), strength=clamp01(strength))

    def appraise_goal_appraisal(self, appraisal: Any) -> AffectDelta:
        """Map appraisal theo goal thành modulation affect nhỏ và có provenance.

        Đây là cầu V56 giữa Subject Appraisal và current affect. Nó chỉ dùng state
        của Eris (goal congruence/uncertainty/controllability), không suy diễn
        người dùng đang vui, buồn hay có ý đồ gì.
        """
        relevance = clamp01(getattr(appraisal, "goal_relevance", 0.0), 0.0)
        congruence = clamp_signed(getattr(appraisal, "goal_congruence", 0.0), 0.0)
        progress = clamp_signed(getattr(appraisal, "goal_progress_delta", 0.0), 0.0)
        uncertainty = clamp01(getattr(appraisal, "uncertainty", 0.5), 0.5)
        controllability = clamp01(getattr(appraisal, "controllability", 0.5), 0.5)
        relational = clamp01(getattr(appraisal, "relational_significance", 0.0), 0.0)
        identity = clamp01(getattr(appraisal, "identity_significance", 0.0), 0.0)

        support = max(0.0, congruence)
        threat = max(0.0, -congruence)
        forward = max(0.0, progress)
        setback = max(0.0, -progress)
        evidence_gain = relevance * (0.55 + 0.45 * (1.0 - uncertainty))

        mood = {
            "valence": 0.10 * support * evidence_gain - 0.12 * threat * evidence_gain,
            "arousal": 0.04 * abs(congruence) * relevance + 0.025 * setback,
            "curiosity": 0.035 * uncertainty * relevance,
            "social_energy": 0.035 * support * relational - 0.045 * threat * relational,
            "attachment": 0.018 * support * relational - 0.018 * threat * relational,
            "irritation": 0.055 * threat * controllability + 0.025 * setback,
            "tenderness": 0.025 * support * relational,
            "delight": 0.075 * support * evidence_gain + 0.035 * forward,
            "sadness": 0.055 * threat * (0.55 + 0.45 * identity),
            "anxiety": 0.085 * threat * (1.0 - 0.65 * controllability) + 0.025 * uncertainty * relevance,
            "embarrassment": 0.035 * setback * controllability * identity,
        }
        impulse = {
            "valence": 0.15 * support * evidence_gain - 0.17 * threat * evidence_gain,
            "arousal": 0.07 * abs(congruence) * relevance,
            "curiosity": 0.055 * uncertainty * relevance,
            "social_energy": 0.055 * support * relational - 0.065 * threat * relational,
            "irritation": 0.08 * threat * controllability,
            "delight": 0.12 * support * evidence_gain + 0.05 * forward,
            "sadness": 0.08 * threat * (0.55 + 0.45 * identity),
            "anxiety": 0.12 * threat * (1.0 - 0.65 * controllability),
            "embarrassment": 0.05 * setback * controllability * identity,
        }
        labels: list[str] = []
        if support >= 0.20:
            labels.append("goal_supported")
        if threat >= 0.20:
            labels.append("goal_threatened")
        if forward >= 0.20:
            labels.append("goal_progress")
        if setback >= 0.20:
            labels.append("goal_setback")
        strength = max((abs(float(v)) for v in (*mood.values(), *impulse.values())), default=0.0)
        return AffectDelta(mood=mood, impulse=impulse, labels=tuple(labels), strength=clamp01(strength))

    def appraise_cognitive_event(self, event: CognitiveEvent) -> AffectDelta:
        """Internal/seed event chỉ nudge cognition, không tự bịa vui/buồn."""

        kind = str(event.kind or "").strip().lower()
        meta = dict(event.metadata or {})
        quality = clamp01(meta.get("seed_quality", 0.0), 0.0)
        motive = clamp01(meta.get("seed_motive_strength", 0.0), 0.0)
        gain = max(0.35, quality) * (0.55 + motive * 0.45)
        mood: dict[str, float] = {}
        impulse: dict[str, float] = {}
        labels: list[str] = []
        if kind in {"curiosity_question", "question", "inquiry"}:
            mood = {"curiosity": 0.055 * gain, "arousal": 0.018 * gain}
            impulse = {"curiosity": 0.12 * gain, "arousal": 0.04 * gain}
            labels.append("curiosity_spark")
        elif kind in {"thought", "association", "memory_resonance"}:
            mood = {"curiosity": 0.028 * gain}
            impulse = {"curiosity": 0.055 * gain}
            labels.append("internal_interest")
        elif kind in {"resolution", "success", "closure"}:
            mood = {"valence": 0.06 * gain, "delight": 0.08 * gain, "arousal": -0.02 * gain}
            impulse = {"valence": 0.09 * gain, "delight": 0.14 * gain}
            labels.append("closure_relief")
        strength = max((abs(float(v)) for v in (*mood.values(), *impulse.values())), default=0.0)
        return AffectDelta(mood=mood, impulse=impulse, labels=tuple(labels), strength=clamp01(strength))

    def apply_event(
        self,
        state: Any,
        event: CognitiveEvent,
        *,
        delta: AffectDelta,
        now: float | None = None,
        disposition: Mapping[str, Any] | None = None,
    ) -> Any:
        current = float(now if now is not None else event.occurred_at or time.time())
        state = self.decay(state, now=current, disposition=disposition)
        payload = _state_map(state)
        aux = _aux_mood(state)
        impulses = _impulses(state)
        core_updates: dict[str, float] = {}

        for key, raw_delta in dict(delta.mood).items():
            if key not in ALL_AXES:
                continue
            d = float(raw_delta)
            if key in CORE_AXES:
                current_value = float(getattr(state, key, CORE_BASELINE[key]))
                core_updates[key] = _apply_delta(current_value, d, signed=key in SIGNED_AXES)
            else:
                aux[key] = _apply_delta(aux[key], d, signed=False)

        for key, raw_delta in dict(delta.impulse).items():
            if key not in ALL_AXES:
                continue
            impulses[key] = clamp_signed(impulses.get(key, 0.0) + float(raw_delta))

        payload.update(
            {
                "version": AFFECTIVE_DYNAMICS_VERSION,
                "last_decay_at": current,
                "last_event_at": current,
                "last_event_id": str(event.event_id or ""),
                "last_event_kind": str(event.kind or ""),
                "last_event_origin": str(event.origin or ""),
                "event_count": int(payload.get("event_count", 0) or 0) + 1,
                "last_labels": list(delta.labels),
                "last_delta_strength": round(float(delta.strength), 4),
                "mood": aux,
                "impulse": impulses,
            }
        )
        return replace(state, **core_updates, affective_state=payload)


__all__ = [
    "AFFECTIVE_DYNAMICS_VERSION",
    "AUX_BASELINE",
    "CORE_BASELINE",
    "AffectDelta",
    "AffectEventReceipt",
    "AffectiveDynamicsEngine",
    "effective_affect_values",
    "affective_selection_bias",
    "disposition_baselines",
]
