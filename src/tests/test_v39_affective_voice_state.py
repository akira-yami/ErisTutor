# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from runtime.affective_voice_state import (
    AFFECTIVE_VOICE_VERSION,
    AffectiveVoiceTracker,
    project_affective_voice_state,
)
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.eris_voice_contract import CHAT_VOICE_CORE_V39, surface_voice_rules
from runtime.public_reply_context import compile_public_reply_context


def _personality(*, delight=0.20, sadness=0.20, mischief=0.12):
    return SimpleNamespace(
        mood={
            "energy": 0.45,
            "tenderness": 0.35,
            "curiosity": 0.55,
            "irritation": 0.08,
            "anxiety_tension": 0.18,
            "sadness": sadness,
            "delight": delight,
            "yearning": 0.22,
            "mischief": mischief,
        },
        temperament={
            "self_disclosure_selectivity": 0.83,
            "social_guardedness": 0.76,
            "directness": 0.43,
            "dry_humor": 0.48,
            "playfulness": 0.44,
        },
    )


def _relationship():
    return SimpleNamespace(
        familiarity=0.72,
        closeness=0.61,
        playful_safety=0.66,
        boundary_safety=0.81,
        rupture=0.03,
    )


def _internal(**overrides):
    data = dict(
        valence=0.10,
        arousal=0.38,
        curiosity=0.56,
        social_energy=0.48,
        attachment=0.42,
        tenderness=0.28,
        irritation=0.08,
        fatigue=0.10,
        restraint_pressure=0.14,
        playful_pressure=0.10,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def test_v39_personality_is_shape_not_static_temperature() -> None:
    prompt = AIRuntimeModelRouter._public_reply_prompt("vi", "ja")
    assert "AFFECTIVE_VOICE_STATE" in prompt
    assert "preset ấm/lạnh/điềm/restrained" in prompt
    assert "ấm kín" not in prompt
    assert "default surface is quietly warm" not in prompt
    assert "mood vui" in prompt


def test_v39_live_joy_can_raise_expression_despite_guarded_personality() -> None:
    tracker = AffectiveVoiceTracker()
    state = tracker.project(
        internal_state=_internal(
            valence=0.86,
            arousal=0.78,
            social_energy=0.80,
            curiosity=0.74,
            playful_pressure=0.62,
            restraint_pressure=0.05,
        ),
        personality=_personality(delight=0.88, sadness=0.04, mischief=0.64),
        relationship=_relationship(),
        channel="chat",
        now=1000.0,
    )
    # Guardedness vẫn cao nhưng không còn quyền triệt tiêu một mood vui thật.
    assert state.social_guardedness >= 0.70
    assert state.delight >= 0.80
    assert state.playfulness >= 0.60
    assert state.expressive_drive >= 0.60
    context = state.prompt_context()
    assert "Affect hiện tại là authority" in context
    assert "không được kéo affect về một preset" in context


def test_v39_sad_or_irritated_state_is_not_forced_warm() -> None:
    tracker = AffectiveVoiceTracker()
    state = tracker.project(
        internal_state=_internal(
            valence=-0.58,
            arousal=0.55,
            irritation=0.72,
            social_energy=0.22,
            fatigue=0.48,
            restraint_pressure=0.40,
        ),
        personality=_personality(delight=0.04, sadness=0.72, mischief=0.03),
        relationship=_relationship(),
        channel="chat",
        now=1000.0,
    )
    assert state.valence < -0.50
    assert state.irritation >= 0.70
    assert state.sadness >= 0.70
    assert "không diễn quá tay" in state.prompt_context()
    assert "được có cạnh" in state.prompt_context()


def test_v39_tracker_exposes_real_trajectory_not_retry_noise() -> None:
    tracker = AffectiveVoiceTracker()
    internal = _internal(valence=0.05, arousal=0.30)
    tracker.project(
        internal_state=internal,
        personality=_personality(delight=0.12),
        relationship=_relationship(),
        channel="chat",
        now=1000.0,
    )
    internal.valence = 0.72
    internal.arousal = 0.70
    second = tracker.project(
        internal_state=internal,
        personality=_personality(delight=0.70),
        relationship=_relationship(),
        channel="autonomous",
        now=1001.0,
    )
    assert second.trajectory["valence"] > 0.60
    assert second.trajectory["arousal"] > 0.35
    assert second.trajectory_strength > 0.60

    retry = tracker.project(
        internal_state=internal,
        personality=_personality(delight=0.70),
        relationship=_relationship(),
        channel="autonomous",
        now=1001.10,
    )
    # State không đổi nên retry không được tự bịa thêm một mood swing.
    assert abs(retry.trajectory["valence"]) < 1e-6


def test_v39_host_projector_is_shared_between_chat_and_surface() -> None:
    host = SimpleNamespace()
    host.internal = _internal(valence=0.62, arousal=0.66, social_energy=0.70)
    host._get_internal_state = lambda: host.internal
    mind = SimpleNamespace(personality=_personality(delight=0.68), relationship=_relationship())

    chat = project_affective_voice_state(host, mind_state=mind, channel="chat", now=2000.0)
    surface = project_affective_voice_state(host, mind_state=mind, channel="autonomous", now=2001.0)
    assert chat.version == AFFECTIVE_VOICE_VERSION == surface.version
    assert chat.valence == surface.valence
    assert chat.delight == surface.delight
    assert chat.social_guardedness == surface.social_guardedness
    assert getattr(host, "_affective_voice_tracker") is not None


def test_v39_technical_prompt_budget_keeps_live_affect_before_static_personality() -> None:
    live = "AFFECTIVE_VOICE_STATE eris.affective_voice.v39.state_authority valence=+0.82 arousal=0.75 delight=0.88. "
    compiled = compile_public_reply_context(
        route={"reasons": ["technical_depth"]},
        personality=live + ("STATIC_PERSONALITY " * 80),
        grounding="ground",
    )
    assert compiled.personality.startswith("AFFECTIVE_VOICE_STATE")
    assert "valence=+0.82" in compiled.personality


def test_v39_voice_core_has_no_static_emotional_temperature() -> None:
    lowered = CHAT_VOICE_CORE_V39.lower()
    assert "ấm kín" not in lowered
    assert "warm-first" not in lowered
    assert "default warm" not in lowered
    assert "affective_voice_state" in lowered


def test_v39_tts_and_chat_contract_do_not_reintroduce_static_temperature() -> None:
    from runtime.tts_engine import _build_tts_prompt
    from llm.contracts import CHAT_RULES_PROMPT

    tts_prompt = _build_tts_prompt(
        "Cái này làm tôi vui hơn tôi tưởng.",
        "",
        language_hint="vi",
        prosody_notes="bright, quick, delighted",
        quality_mode="high",
    )
    lowered = tts_prompt.lower()
    assert "warm-first" not in lowered
    assert "emotionally nuanced, restrained" not in lowered
    assert "state-responsive" in lowered

    chat_rules = CHAT_RULES_PROMPT.lower()
    assert "apply restrained personality" not in chat_rules
    assert "live affect" in chat_rules
