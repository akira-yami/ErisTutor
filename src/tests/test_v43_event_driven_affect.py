# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cortex.affective_dynamics import (
    AFFECTIVE_DYNAMICS_VERSION,
    AffectiveDynamicsEngine,
    effective_affect_values,
)
from cortex.cognitive_event import CognitiveEvent
from cortex.internal_state import PersistentInternalState
from runtime.affective_dynamics_runtime import AffectiveDynamicsRuntime
from runtime.affective_voice_state import AFFECTIVE_VOICE_VERSION, AffectiveVoiceTracker
from runtime.live2d_affect_bridge import _extract_affect
from runtime.public_output_runtime import PublicOutputRuntimeMixin
from runtime.social_appraisal import SocialAppraisalEngine


class _Logger:
    def debug(self, *_args, **_kwargs):
        pass

    info = debug
    warning = debug


class _Host:
    def __init__(self, state: PersistentInternalState | None = None) -> None:
        self.state = state or PersistentInternalState()
        self.logger = _Logger()
        self.latest_personality_profile = {
            "mood": {
                "energy": 0.34,
                "tenderness": 0.42,
                "curiosity": 0.56,
                "irritation": 0.08,
                "anxiety_tension": 0.27,
                "sadness": 0.22,
                "delight": 0.19,
                "yearning": 0.36,
                "mischief": 0.16,
                "embarrassment": 0.18,
            }
        }
        self.stage_event_bus = None

    def _get_internal_state(self):
        return self.state

    def _set_internal_state(self, state):
        self.state = state


def _personality(*, delight: float = 0.19, mischief: float = 0.16):
    return SimpleNamespace(
        mood={
            "energy": 0.34,
            "tenderness": 0.42,
            "curiosity": 0.56,
            "irritation": 0.08,
            "anxiety_tension": 0.27,
            "sadness": 0.22,
            "delight": delight,
            "yearning": 0.36,
            "mischief": mischief,
            "embarrassment": 0.18,
        },
        temperament={
            "self_disclosure_selectivity": 0.87,
            "social_guardedness": 0.78,
            "directness": 0.40,
            "dry_humor": 0.47,
            "playfulness": 0.41,
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


def test_v43_user_event_identity_is_stable_per_turn() -> None:
    a = CognitiveEvent.from_user_turn("hí hí", turn_id="turn:43", now=100.0)
    b = CognitiveEvent.from_user_turn("hí hí", turn_id="turn:43", now=120.0)
    c = CognitiveEvent.from_user_turn("hí hí", turn_id="turn:44", now=120.0)
    assert a is not None and b is not None and c is not None
    assert a.event_id == b.event_id == "cogevt:user:turn:43"
    assert c.event_id != a.event_id


def test_v43_real_vietnamese_laughter_is_seen_as_playful_social_event() -> None:
    appraisal = SocialAppraisalEngine().appraise(user_text="hí hí. chắc thế")
    # Regression cho đúng câu đã xuất hiện trong run V40. Accent + khoảng trắng
    # không được làm playful signal biến mất rồi writer tự "hí hí" ngoài state.
    assert appraisal.playful.value >= 0.45


def test_v43_playful_user_turn_moves_current_mood_before_writer() -> None:
    host = _Host()
    runtime = AffectiveDynamicsRuntime(host, logger=host.logger)
    before = effective_affect_values(host.state)

    receipt = runtime.ingest_user_turn(
        "hí hí. chắc thế",
        turn_id="turn:playful",
        previous_assistant_text="Ừ, tôi vẫn ở đây.",
        now=1_000.0,
    )
    after = effective_affect_values(host.state)

    assert receipt.applied is True
    assert host.state.affective_state["version"] == AFFECTIVE_DYNAMICS_VERSION
    assert after["playfulness"] > before["playfulness"]
    assert after["delight"] > before["delight"]
    assert after["valence"] > before["valence"]
    assert host.state.affective_state["last_event_id"] == "cogevt:user:turn:playful"


def test_v43_same_user_turn_retry_cannot_pump_mood_twice() -> None:
    host = _Host()
    runtime = AffectiveDynamicsRuntime(host, logger=host.logger)
    first = runtime.ingest_user_turn("hí hí", turn_id="turn:same", now=2_000.0)
    snapshot = host.state.to_dict()
    second = runtime.ingest_user_turn("hí hí", turn_id="turn:same", now=2_000.2)

    assert first.applied is True
    assert second.applied is False
    assert second.reason == "duplicate_event"
    assert host.state.to_dict() == snapshot


def test_v43_boundary_event_can_raise_irritation_and_lower_playfulness() -> None:
    host = _Host()
    runtime = AffectiveDynamicsRuntime(host, logger=host.logger)
    playful = runtime.ingest_user_turn("hí hí", turn_id="turn:joy", now=3_000.0)
    assert playful.applied is True
    before = effective_affect_values(host.state)

    boundary = runtime.ingest_user_turn(
        "đừng chọc nữa, để tôi yên",
        turn_id="turn:boundary",
        previous_assistant_text="Tôi đang trêu một chút.",
        now=3_005.0,
    )
    after = effective_affect_values(host.state)
    assert boundary.applied is True
    assert after["irritation"] > before["irritation"]
    assert after["playfulness"] < before["playfulness"]
    assert after["social_energy"] < before["social_energy"]


def test_v43_background_decay_changes_state_without_creating_fake_event() -> None:
    host = _Host()
    runtime = AffectiveDynamicsRuntime(host, logger=host.logger)
    runtime.ingest_user_turn("hí hí", turn_id="turn:decay", now=4_000.0)
    before_meta = dict(host.state.affective_state)
    before = effective_affect_values(host.state)

    decayed = runtime.decay_state(host.state, now=4_240.0)
    after = effective_affect_values(decayed)

    assert decayed.affective_state["event_count"] == before_meta["event_count"]
    assert decayed.affective_state["last_event_id"] == before_meta["last_event_id"]
    assert after["playfulness"] < before["playfulness"]
    assert abs(float(decayed.affective_state["impulse"]["playfulness"])) < abs(float(before_meta["impulse"]["playfulness"]))


def test_v43_personality_mood_is_slow_disposition_not_current_override() -> None:
    state = PersistentInternalState(
        affective_state={
            "version": AFFECTIVE_DYNAMICS_VERSION,
            "mood": {
                "delight": 0.76,
                "sadness": 0.08,
                "anxiety": 0.10,
                "playfulness": 0.64,
                "yearning": 0.20,
                "embarrassment": 0.08,
            },
            "impulse": {"playfulness": 0.18, "delight": 0.12},
            "last_event_at": 5_000.0,
            "last_event_kind": "user_turn",
        }
    )
    tracker = AffectiveVoiceTracker()
    voice = tracker.project(
        internal_state=state,
        # Cố tình đưa personality mood trái ngược. Dynamic state phải thắng.
        personality=_personality(delight=0.02, mischief=0.01),
        relationship=_relationship(),
        channel="chat",
        now=5_001.0,
    )
    assert voice.version == AFFECTIVE_VOICE_VERSION
    assert voice.delight > 0.70
    assert voice.playfulness > 0.60
    assert voice.affect_event_kind == "user_turn"
    assert voice.affect_impulse_strength >= 0.18


def test_v43_personality_disposition_is_decay_target_not_instant_reset() -> None:
    engine = AffectiveDynamicsEngine()
    state = PersistentInternalState(
        affective_state={
            "version": AFFECTIVE_DYNAMICS_VERSION,
            "last_decay_at": 6_000.0,
            "mood": {
                "delight": 0.82,
                "sadness": 0.10,
                "anxiety": 0.12,
                "playfulness": 0.70,
                "yearning": 0.20,
                "embarrassment": 0.10,
            },
            "impulse": {},
        }
    )
    disposition = {"mood": {"delight": 0.34, "mischief": 0.22}}
    decayed = engine.decay(state, now=6_000.0 + 24 * 60.0, disposition=disposition)
    delight = float(decayed.affective_state["mood"]["delight"])
    playfulness = float(decayed.affective_state["mood"]["playfulness"])
    assert 0.34 < delight < 0.82
    assert 0.22 < playfulness < 0.70


def test_v43_internal_curiosity_event_nudges_interest_without_inventing_joy() -> None:
    engine = AffectiveDynamicsEngine()
    event = CognitiveEvent(
        event_id="cogevt:seed:curiosity",
        origin="seed",
        kind="curiosity_question",
        subject="một câu hỏi nội bộ mới",
        occurred_at=7_000.0,
        metadata={"seed_quality": 0.9, "seed_motive_strength": 0.8},
    )
    delta = engine.appraise_cognitive_event(event)
    assert delta.mood.get("curiosity", 0.0) > 0.0
    assert delta.impulse.get("curiosity", 0.0) > 0.0
    assert delta.mood.get("delight", 0.0) == 0.0
    assert delta.impulse.get("playfulness", 0.0) == 0.0


def test_v43_affective_state_survives_persistence_roundtrip() -> None:
    state = PersistentInternalState(
        affective_state={
            "version": AFFECTIVE_DYNAMICS_VERSION,
            "last_event_id": "cogevt:user:t",
            "event_count": 3,
            "mood": {"delight": 0.51},
            "impulse": {"playfulness": 0.22},
        }
    )
    restored = PersistentInternalState.from_dict(state.to_dict())
    assert restored.affective_state["last_event_id"] == "cogevt:user:t"
    assert restored.affective_state["event_count"] == 3
    assert restored.affective_state["mood"]["delight"] == pytest.approx(0.51)


def test_v43_live2d_bridge_reads_same_effective_impulse() -> None:
    state = PersistentInternalState(
        valence=0.10,
        affective_state={
            "version": AFFECTIVE_DYNAMICS_VERSION,
            "mood": {
                "delight": 0.50,
                "sadness": 0.10,
                "anxiety": 0.10,
                "playfulness": 0.45,
                "yearning": 0.20,
                "embarrassment": 0.10,
            },
            "impulse": {"valence": 0.20, "playfulness": 0.25, "delight": 0.20},
        },
    )
    host = _Host(state)
    effective = effective_affect_values(state)
    stage = _extract_affect(host)
    assert stage["valence"] == pytest.approx(effective["valence"], abs=1e-6)
    assert stage["playfulness"] == pytest.approx(effective["playfulness"], abs=1e-6)
    assert stage["delight"] == pytest.approx(effective["delight"], abs=1e-6)


def test_v43_tts_panel_debug_uses_effective_current_affect() -> None:
    state = PersistentInternalState(
        valence=0.02,
        affective_state={
            "version": AFFECTIVE_DYNAMICS_VERSION,
            "mood": {
                "delight": 0.60,
                "sadness": 0.10,
                "anxiety": 0.10,
                "playfulness": 0.52,
                "yearning": 0.20,
                "embarrassment": 0.10,
            },
            "impulse": {"valence": 0.34, "arousal": 0.20, "playfulness": 0.24, "delight": 0.18},
        },
    )

    class Harness(PublicOutputRuntimeMixin):
        def __init__(self):
            self._state = state
            self.last_semantic_frame = {}
            self.fe_style_memory = None
            self._debug_overrides = {}
            self.private_mind_digest = {}
            self.private_life_digest = {}

        def _get_internal_state(self):
            return self._state

    payload = Harness()._build_emotion_debug_payload()
    effective = effective_affect_values(state)
    assert payload["summary"]["valence"] == pytest.approx(effective["valence"], abs=1e-3)
    assert payload["summary"]["arousal"] == pytest.approx(effective["arousal"], abs=1e-3)
    assert payload["summary"]["playfulness"] == pytest.approx(effective["playfulness"], abs=1e-3)
    assert payload["summary"]["delight"] == pytest.approx(effective["delight"], abs=1e-3)


def test_v43_mood_biases_selection_lightly_before_neuro_without_granting_speech() -> None:
    from cortex.affective_dynamics import affective_selection_bias
    from runtime.subject_proposals import AutonomousProposalSource

    state = PersistentInternalState(
        curiosity=0.86,
        social_energy=0.18,
        fatigue=0.30,
        affective_state={
            "version": AFFECTIVE_DYNAMICS_VERSION,
            "mood": {
                "delight": 0.30,
                "sadness": 0.12,
                "anxiety": 0.15,
                "playfulness": 0.16,
                "yearning": 0.20,
                "embarrassment": 0.10,
            },
            "impulse": {},
        },
    )
    bias = affective_selection_bias(state)
    candidate = SimpleNamespace(
        kind="curiosity_question",
        topic="một câu hỏi mới đủ cụ thể",
        source="random_chat_seed",
        confidence=0.72,
        relevance=0.68,
        urgency=0.20,
        requires_grounding=False,
        intrusion_risk=0.18,
        seed_intrusion_risk=0.18,
        seed_quality_score=0.0,
        seed_specificity=0.0,
        seed_motive_strength=0.0,
        seed_semantic_completeness=0.0,
        seed_id="",
        seed_epistemic_mode="",
        seed_temporal_context={},
        evidence_refs=("turn:43",),
        uncertainty=0.20,
        speech_act="curiosity_probe",
        tone="neutral",
        expression_commitment={
            "commitment_id": "cmt:v43",
            "origin": "seed",
            "source_id": "seed:43",
            "semantic_target": "curiosity_probe",
        },
    )
    affordance = SimpleNamespace(
        affordance_id="aff:v43:curiosity",
        candidate=candidate,
        owner="seed",
        metadata={},
    )
    plain = AutonomousProposalSource.from_affordance(affordance, turn_id="auto:43")
    biased = AutonomousProposalSource.from_affordance(
        affordance,
        turn_id="auto:43",
        affective_bias=bias,
    )
    assert plain is not None and biased is not None
    assert biased["action_kind"] == "speak"  # candidate type không bị mood đổi action.
    assert biased["expected_epistemic_value"] > plain["expected_epistemic_value"]
    assert biased["substrate_cost"] > plain["substrate_cost"]  # social energy thấp làm speech tốn hơn.
    assert biased["arguments"]["affective_selection_bias"]["exploration_pull"] > 0.0


def test_v43_affect_event_emits_same_state_to_stage_immediately() -> None:
    class Bus:
        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(event)
            return True

    host = _Host()
    host.stage_event_bus = Bus()
    runtime = AffectiveDynamicsRuntime(host, logger=host.logger)
    receipt = runtime.ingest_user_turn("hí hí", turn_id="turn:body", now=8_000.0)
    assert receipt.applied is True
    assert host.stage_event_bus.events
    payload = host.stage_event_bus.events[-1].to_payload()
    assert payload["type"] == "affect_state"
    assert payload["data"]["source"] == "affect_v43"
    assert payload["data"]["affect"]["playfulness"] == pytest.approx(
        effective_affect_values(host.state)["playfulness"], abs=1e-4
    )
