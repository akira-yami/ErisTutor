from __future__ import annotations

import pytest

from live2d_body.speech_animation import SpeechAnimator, SpeechContext
from live2d_body.stage_state import Live2DStageState


def _start_payload(*, envelope=(), duration_ms=0, frame_ms=40):
    return {
        "protocol": "eris.stage",
        "type": "audio_playback_started",
        "turn_id": "voice-turn",
        "seq": 1,
        "tts_text": "Xin chào, tôi đang nói đây.",
        "data": {
            "audio_envelope": list(envelope),
            "audio_frame_ms": frame_ms,
            "audio_duration_ms": duration_ms,
        },
    }


def test_speaking_hint_does_not_start_mouth_before_audio(monkeypatch):
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: 10.0)
    state = Live2DStageState()
    payload = _start_payload()
    payload["type"] = "speaking"
    state.handle_payload(payload)

    snapshot = state.advance(0.0, now=10.14)
    assert snapshot.speaking is False
    assert snapshot.parameters["ParamMouthOpenY"] == 0.0


def test_playback_without_amplitude_does_not_invent_mouth_movements(monkeypatch):
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: 10.0)
    state = Live2DStageState()
    state.handle_payload(_start_payload())

    snapshot = state.advance(0.0, now=10.14)
    assert snapshot.speaking is True
    assert snapshot.mouth_open_ratio == 0.0
    assert snapshot.parameters["ParamMouthOpenY"] == 0.0


def test_silent_audio_keeps_mouth_closed():
    animator = SpeechAnimator()
    animator.start(now=0.0, context=SpeechContext(
        text="Xin chào, tôi đang nói đây.",
        audio_envelope=(0.0,) * 5,
        audio_frame_ms=40,
        audio_duration_ms=200,
    ))

    assert animator.sample(now=0.14).mouth_open_ratio == 0.0
    assert animator.sample(now=0.40).mouth_open_ratio == 0.0


def test_sparse_audio_keeps_real_silence_and_only_opens_at_sound():
    animator = SpeechAnimator()
    animator.start(now=0.0, context=SpeechContext(
        text="Xin chào, tôi đang nói đây.",
        audio_envelope=tuple([0.0] * 10 + [0.82] + [0.0] * 9),
        audio_frame_ms=40,
        audio_duration_ms=800,
    ))

    assert animator.sample(now=0.14).mouth_open_ratio == 0.0
    assert animator.sample(now=0.40).mouth_open_ratio == pytest.approx(0.82)
    assert animator.sample(now=0.60).mouth_open_ratio == 0.0


def test_mouth_stops_at_audio_duration_even_if_envelope_has_extra_frames():
    animator = SpeechAnimator()
    animator.start(now=0.0, context=SpeechContext(
        text="Xin chào.",
        audio_envelope=(0.72,) * 5,
        audio_frame_ms=40,
        audio_duration_ms=120,
    ))

    assert animator.sample(now=0.10).mouth_open_ratio == pytest.approx(0.72)
    assert animator.sample(now=0.12).mouth_open_ratio == 0.0


@pytest.mark.parametrize("should_speak", [True, False])
def test_text_payload_does_not_restart_or_stop_active_audio(monkeypatch, should_speak):
    clock = [10.0]
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: clock[0])
    state = Live2DStageState()
    state.handle_payload(_start_payload(envelope=(0.0, 0.0, 0.72, 0.72, 0.0), duration_ms=200))
    clock[0] = 10.08
    state.handle_payload({"text": "Xin chào.", "should_speak": should_speak})

    snapshot = state.advance(0.0, now=10.10)
    assert snapshot.speaking is True
    assert snapshot.mouth_open_ratio == pytest.approx(0.72)


@pytest.mark.parametrize("terminal", ["audio_playback_done", "interrupted", "audio_failed"])
def test_audio_terminal_event_closes_active_mouth(monkeypatch, terminal):
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: 10.0)
    state = Live2DStageState()
    state.handle_payload(_start_payload(envelope=(0.72,) * 5, duration_ms=200))
    assert state.advance(0.0, now=10.10).mouth_open_ratio > 0.5

    state.handle_payload({"protocol": "eris.stage", "type": terminal, "turn_id": "voice-turn", "seq": 1})
    snapshot = state.advance(0.0, now=10.11)
    assert snapshot.speaking is False
    assert snapshot.mouth_open_ratio == 0.0
    assert snapshot.parameters["ParamMouthOpenY"] == 0.0


def test_audio_duration_closes_mouth_when_done_packet_is_missing(monkeypatch):
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: 10.0)
    state = Live2DStageState()
    state.handle_payload(_start_payload(envelope=(0.72,) * 5, duration_ms=200))

    snapshot = state.advance(0.0, now=10.21)
    assert snapshot.speaking is False
    assert snapshot.parameters["ParamMouthOpenY"] == 0.0
