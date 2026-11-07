"""Regressions for the audited native INFJ domains and stage behavior."""
import json
import math
from pathlib import Path

import pytest

from cortex.greeting_presets import GREETING_PRESETS
from live2d_body.affect import AffectVector
from live2d_body.idle import IdleDirector
from live2d_body.parameter_profile import INFJ_PARAMETER_PROFILE as profile
from live2d_body.speech_animation import SpeechAnimator, SpeechContext
from live2d_body.stage_state import ExpressionTarget, Live2DStageState


@pytest.mark.parametrize("ratio,want", [(0, 0), (.5, .5), (1, 1)])
def test_mouth_linear_pose_matches_audited_native_domain(ratio, want):
    metadata = json.loads((Path(__file__).resolve().parents[2] / "docs/reports/2026-10-14-live2d-model-metadata.json").read_text())
    mouth = next(item for item in metadata["parameters"] if item["id"] == "ParamMouthOpenY")
    assert profile.mouth_open_from_ratio(ratio) == want
    assert mouth["min"] <= want <= mouth["max"]


def test_voiced_curve_opens_continuously_from_silence_to_the_wide_vowel_pose():
    assert profile.mouth_open_from_speech_ratio(.025) == 0
    values = [profile.mouth_open_from_speech_ratio(.025 + .975 * index / 1000) for index in range(1, 1001)]
    assert 0 < values[0] < .01
    assert values[-1] == pytest.approx(.84)
    assert all(a <= b for a, b in zip(values, values[1:]))
    assert profile.compose_speaking_mouth(mood_form=1, speech_ratio=0)[1] == 0


@pytest.mark.parametrize("param,low,high", [
    ("ParamErisMouthCornerL", -1, 1), ("ParamErisMouthCornerR", -1, 1),
    ("ParamErisLipPress", 0, 1), ("ParamErisTongue", 0, 1),
])
def test_custom_channels_clamp_to_native_domains(param, low, high):
    assert profile.sanitize(param, -9) == low
    assert profile.sanitize(param, 9) == high


def test_neutral_target_explicitly_resets_every_custom_channel():
    params = ExpressionTarget().parameters()
    for name in ("ParamErisMouthCornerL", "ParamErisMouthCornerR", "ParamErisLipPress", "ParamErisTongue"):
        assert params.get(name) == 0


def test_affect_projects_custom_corners_and_lip_pressure_into_stage():
    warm = AffectVector(valence=.7, tenderness=.8, irritation=0).visual_pose()
    tense = AffectVector(valence=-.5, irritation=.8, cognitive_tension=.9).visual_pose()
    warm_params = ExpressionTarget.from_affect_pose(warm).parameters()
    tense_params = ExpressionTarget.from_affect_pose(tense).parameters()
    assert warm_params.get("ParamErisMouthCornerL", 0) > .05
    assert warm_params.get("ParamErisMouthCornerR", 0) > .05
    assert tense_params.get("ParamErisLipPress", 0) > .15
    assert tense_params.get("ParamErisMouthCornerL", 0) < 0
    assert tense_params["ParamErisTongue"] == 0


@pytest.mark.parametrize("label,want", [
    ("warm_playful", "mischief_soft"), ("mischief", "mischief_soft"),
    ("deadpan", "flat_calm"), ("quiet_soft", "quiet_low"),
    ("curious_soft", "curious"), ("tired_soft", "quiet_low"), ("flat_cold", "distant"),
    ("calm", "calm"), ("warm", "warm"), ("dry", "dry"), ("low", "low"),
    ("amused", "amused"), ("skeptical", "skeptical"),
    ("concerned", "concerned"), ("surprised_soft", "surprised_soft"),
])
def test_emitted_emotions_keep_intended_semantics(monkeypatch, label, want):
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: 10)
    state = Live2DStageState()
    state.handle_payload({"protocol": "eris.stage", "type": "stage_segment", "emotion": label})
    snap = state.advance(0, now=10)
    assert snap.emotion == want
    assert snap.parameters["ParamMouthOpenY"] == 0


@pytest.mark.parametrize("style,cue", [
    ("restrained_warmth", "warm"), ("gentle_low", "quiet"),
    ("cold_precision", "firm"), ("low_direct", "focused"),
    ("surprised_soft", "surprised"),
])
def test_planner_prosody_changes_face_while_missing_audio_stays_closed(style, cue):
    animator = SpeechAnimator()
    animator.start(now=0, context=SpeechContext(text="A sentence.", prosody_style=style), audio_playback=True)
    pose = animator.sample(now=.4)
    assert cue in pose.cue
    assert pose.mouth_open_ratio == 0


@pytest.mark.parametrize("motion", ["small_smile", "blink_slow", "nod_small", "focus", "still", "lean_back_small"])
def test_planner_motion_has_idle_choreography(monkeypatch, motion):
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: 10)
    state = Live2DStageState()
    state.handle_payload({"protocol": "eris.stage", "type": "stage_segment", "motion": motion})
    first = state.advance(0, now=10.3)
    assert first.idle_action == motion
    assert first.parameters["ParamMouthOpenY"] == 0


def test_stage_mouth_response_uses48_opening64_closing(monkeypatch):
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: 10)
    state = Live2DStageState()
    state.handle_payload({"protocol": "eris.stage", "type": "affect_state", "data": {"affect": {
        "valence": 0, "tenderness": 0, "attachment": 0, "irritation": 0, "fatigue": 0,
    }}})
    state.advance(0, now=10)
    state.handle_payload({"protocol": "eris.stage", "type": "audio_playback_started", "data": {
        "audio_envelope": [1, 1, 0, 0], "audio_frame_ms": 100, "audio_duration_ms": 400,
    }})
    opening = state.advance(.01, now=10)
    desired = .84
    assert opening.parameters["ParamMouthOpenY"] == pytest.approx(desired * (1 - math.exp(-.48)))
    closing = state.advance(.01, now=10.2)
    assert closing.parameters["ParamMouthOpenY"] == pytest.approx(opening.parameters["ParamMouthOpenY"] * math.exp(-.64))


def test_terminal_surprise_does_not_reopen_closed_audio_mouth(monkeypatch):
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: 10)
    state = Live2DStageState()
    state.handle_payload({"protocol": "eris.stage", "type": "audio_playback_started", "emotion": "surprise", "data": {
        "audio_envelope": [.7] * 4, "audio_frame_ms": 100, "audio_duration_ms": 400,
    }})
    state.advance(0, now=10.1)
    state.handle_payload({"protocol": "eris.stage", "type": "audio_playback_done"})
    assert state.advance(0, now=10.2).parameters["ParamMouthOpenY"] == 0


def test_speech_head_uses_visible_parameter_units_without_large_face_offsets():
    animator = SpeechAnimator()
    animator.start(now=0, context=SpeechContext(text="Eris is speaking.", audio_envelope=(.8,) * 200, audio_frame_ms=40), audio_playback=True)
    poses = [animator.sample(now=index / 60) for index in range(480)]
    peak = max(max(abs(pose.angle_x), abs(pose.angle_z)) for pose in poses)
    assert 4.8 <= peak <= 6
    assert max(abs(pose.mouth_form_offset) for pose in poses) <= .16


def test_repeated_event_labels_expose_distinct_native_playback_edges(monkeypatch):
    clock = [10]
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: clock[0])
    state = Live2DStageState()
    payload = {"protocol": "eris.stage", "type": "stage_segment", "emotion": "warm_soft"}
    state.handle_payload(payload)
    first = state.advance(0, now=10)
    clock[0] = 11
    state.handle_payload(payload)
    second = state.advance(0, now=11)
    assert second.last_event_at > first.last_event_at
