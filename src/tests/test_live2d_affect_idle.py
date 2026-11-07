# -*- coding: utf-8 -*-
from __future__ import annotations

import time

from live2d_body.stage_state import Live2DStageState
from runtime.live2d_affect_bridge import emit_runtime_affect
from runtime.stage_protocol_adapter import affect_state_event


def _run(state: Live2DStageState, *, start: float, seconds: float, fps: int = 60):
    snapshot = None
    frames = max(1, int(seconds * fps))
    for index in range(frames + 1):
        snapshot = state.advance(1.0 / fps, now=start + index / fps)
    assert snapshot is not None
    return snapshot


def test_affect_state_warms_idle_face_without_explicit_chat_emotion() -> None:
    state = Live2DStageState(idle_seed=3)
    state.handle_payload({
        "protocol": "eris.stage",
        "type": "affect_state",
        "data": {
            "source": "private_mind:autonomous",
            "thought_pressure": 0.22,
            "confidence": 0.8,
            "affect": {
                "valence": 0.52,
                "tenderness": 0.72,
                "attachment": 0.60,
                "curiosity": 0.34,
                "irritation": 0.02,
            },
        },
    })
    start = time.monotonic()
    snapshot = _run(state, start=start, seconds=2.0)
    assert snapshot.parameters["ParamMouthForm"] > 0.08
    assert snapshot.affect_source == "private_mind:autonomous"
    assert snapshot.emotion == "warm_soft"
    assert snapshot.speaking is False


def test_explicit_chat_emotion_fades_back_to_affect_residue() -> None:
    state = Live2DStageState(idle_seed=11)
    state.handle_payload({
        "protocol": "eris.stage",
        "type": "affect_state",
        "data": {
            "source": "private_mind:chat",
            "affect": {
                "valence": 0.48,
                "tenderness": 0.66,
                "attachment": 0.54,
                "irritation": 0.04,
            },
        },
    })
    state.handle_payload({
        "protocol": "eris.stage",
        "type": "stage_segment",
        "emotion": "annoyed_soft",
        "motion": "look_aside",
    })
    start = time.monotonic()
    early = _run(state, start=start, seconds=0.6)
    assert early.parameters["ParamMouthForm"] < -0.10
    assert early.explicit_emotion_weight > 0.80

    late = _run(state, start=start + 0.6, seconds=48.0, fps=20)
    assert late.explicit_emotion_weight < 0.05
    assert late.parameters["ParamMouthForm"] > 0.04
    assert late.emotion == "warm_soft"


def test_idle_director_schedules_non_stiff_micro_gestures() -> None:
    state = Live2DStageState(idle_seed=5)
    state.handle_payload({
        "protocol": "eris.stage",
        "type": "affect_state",
        "data": {
            "source": "private_mind:autonomous",
            "thought_pressure": 0.72,
            "confidence": 0.75,
            "affect": {"curiosity": 0.82, "arousal": 0.34, "fatigue": 0.10},
        },
    })
    start = time.monotonic()
    actions: set[str] = set()
    max_gaze = 0.0
    max_angle_delta = 0.0
    for index in range(500):
        snapshot = state.advance(0.05, now=start + index * 0.05)
        actions.add(snapshot.idle_action)
        max_gaze = max(max_gaze, abs(snapshot.gaze_bias_x), abs(snapshot.gaze_bias_y))
        max_angle_delta = max(
            max_angle_delta,
            abs(snapshot.parameters["ParamAngleX"]),
            abs(snapshot.parameters["ParamAngleZ"]),
        )
    assert actions - {"settle"}
    assert max_gaze > 0.015
    assert max_angle_delta > 0.35


def test_affect_protocol_never_carries_private_prose() -> None:
    event = affect_state_event(
        affect={
            "valence": 2.0,
            "curiosity": 0.7,
            "dominant_judgment": "PRIVATE TEXT MUST NOT LEAK",
        },
        source="private_mind:autonomous",
        thought_pressure=0.6,
        confidence=0.9,
        diary_updated=True,
    )
    payload = event.to_payload()
    assert payload["type"] == "affect_state"
    assert payload["data"]["affect"]["valence"] == 1.0
    assert "dominant_judgment" not in payload["data"]["affect"]
    assert "PRIVATE TEXT" not in str(payload)


def test_runtime_affect_bridge_buckets_and_rate_limits() -> None:
    class FakeBus:
        def __init__(self) -> None:
            self.events = []

        def emit(self, event):
            self.events.append(event)
            return True

    class FakeState:
        def to_dict(self):
            return {
                "valence": 0.22,
                "curiosity": 0.63,
                "irritation": 0.08,
                "private_note": "do not forward",
            }

    class Owner:
        def __init__(self) -> None:
            self.stage_event_bus = FakeBus()

        def _get_internal_state(self):
            return FakeState()

    owner = Owner()
    assert emit_runtime_affect(owner, source="public_turn:chat", force=True) is True
    assert emit_runtime_affect(owner, source="public_turn:chat") is False
    payload = owner.stage_event_bus.events[0].to_payload()
    assert payload["data"]["affect"]["curiosity"] == 0.63
    assert "private_note" not in str(payload)
