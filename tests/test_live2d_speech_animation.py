# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import math
import wave
from types import SimpleNamespace

from live2d_body.speech_animation import SpeechAnimator, SpeechContext
from live2d_body.stage_state import Live2DStageState
from runtime.audio_lip_sync import build_lip_sync_envelope
from runtime.stage_audio_bus import StageAudioEvent
from runtime.stage_protocol_adapter import playback_event


def test_text_context_without_waveform_cannot_animate_mouth() -> None:
    animator = SpeechAnimator()
    animator.start(now=100.0, context=SpeechContext(text="Xin ch?o, b?n ?n ch??", prosody_style="warm_soft"))
    ratios = [animator.sample(now=100.0 + index / 100.0).mouth_open_ratio for index in range(360)]
    assert max(ratios) == 0.0


def test_question_and_stage_actions_use_brows_eyes_head_and_gaze(monkeypatch) -> None:
    clock = {"now": 10.0}
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: clock["now"])

    state = Live2DStageState(idle_seed=1)
    state.handle_payload({
        "protocol": "eris.stage",
        "type": "stage_segment",
        "turn_id": "turn-live2d",
        "seq": 2,
        "tts_text": "Hửm?",
        "emotion": "curious",
        "motion": "tilt_head",
        "data": {
            "prosody_style": "curious_soft",
            "speech_act": "question",
            "actions": ["tilt_head", "wink"],
        },
    })
    state.handle_payload({
        "protocol": "eris.stage",
        "type": "audio_playback_started",
        "turn_id": "turn-live2d",
        "seq": 2,
        "tts_text": "Hửm?",
        "emotion": "curious",
        "motion": "tilt_head",
        "data": {
            "prosody_style": "curious_soft", "speech_act": "question",
            "audio_envelope": [0.72] * 20,
            "audio_frame_ms": 40,
            "audio_duration_ms": 800,
        },
    })

    clock["now"] = 10.14
    snapshot = state.advance(0.0, now=clock["now"])
    params = snapshot.parameters

    assert snapshot.speaking is True
    assert snapshot.mouth_open_ratio > 0.25
    assert params["ParamMouthOpenY"] > -0.5
    assert params["ParamBrowLAngle"] > params["ParamBrowRAngle"]
    assert params["ParamAngleZ"] > 0.0
    assert snapshot.gaze_bias_y > 0.0
    assert snapshot.eye_open_left < snapshot.eye_open_right
    assert "question" in snapshot.speech_cue
    assert "wink" in snapshot.speech_cue

    assert -30.0 <= params["ParamAngleX"] <= 30.0
    assert -1.0 <= params["ParamMouthForm"] <= 1.0
    assert 0.0 <= params["ParamMouthOpenY"] <= 1.0
    assert 0.0 <= snapshot.eye_open_left <= 1.0
    assert 0.0 <= snapshot.eye_open_right <= 1.0

    clock["now"] = 10.8
    state.handle_payload({
        "protocol": "eris.stage",
        "type": "audio_playback_done",
        "turn_id": "turn-live2d",
        "seq": 2,
    })
    stopped = state.advance(0.0, now=clock["now"])
    assert stopped.speaking is False
    assert stopped.parameters["ParamMouthOpenY"] == 0.0
    assert stopped.speech_cue == "silent"


def test_prosody_changes_face_without_changing_waveform_amplitude() -> None:
    quiet = SpeechAnimator()
    focused = SpeechAnimator()
    text = "Eris đang kiểm tra tham số."
    quiet.start(now=0.0, context=SpeechContext(text=text, prosody_style="low_quiet", audio_envelope=(.72,) * 100, audio_frame_ms=40))
    focused.start(now=0.0, context=SpeechContext(text=text, prosody_style="focused", audio_envelope=(.72,) * 100, audio_frame_ms=40))

    quiet_peak = max(quiet.sample(now=index / 120.0).mouth_open_ratio for index in range(360))
    focused_peak = max(focused.sample(now=index / 120.0).mouth_open_ratio for index in range(360))
    assert quiet_peak == focused_peak == .72
    assert quiet.sample(now=.4).eye_open_left_scale < focused.sample(now=.4).eye_open_left_scale


def test_stage_cues_do_not_leak_across_turns(monkeypatch) -> None:
    clock = {"now": 20.0}
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: clock["now"])
    state = Live2DStageState()

    state.handle_payload({
        "protocol": "eris.stage",
        "type": "stage_segment",
        "turn_id": "old-turn",
        "seq": 1,
        "tts_text": "Nháy mắt.",
        "data": {"actions": ["wink"], "prosody_style": "dry_tease"},
    })
    state.handle_payload({
        "protocol": "eris.stage",
        "type": "audio_playback_started",
        "turn_id": "new-turn",
        "seq": 1,
        "tts_text": "Câu mới bình thường.",
        "data": {"prosody_style": "neutral"},
    })

    clock["now"] = 20.4
    snapshot = state.advance(0.0, now=clock["now"])
    assert "wink" not in snapshot.speech_cue
    assert abs(snapshot.eye_open_left - snapshot.eye_open_right) < 0.02


def _synthetic_wav() -> bytes:
    sample_rate = 8000
    samples: list[int] = []
    for index in range(int(sample_rate * 0.48)):
        time_s = index / sample_rate
        if 0.12 <= time_s < 0.32:
            value = int(math.sin(time_s * math.tau * 220.0) * 12500)
        else:
            value = 0
        samples.append(value)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"".join(value.to_bytes(2, "little", signed=True) for value in samples))
    return buffer.getvalue()


def test_pcm_audio_envelope_tracks_sound_and_silence() -> None:
    envelope = build_lip_sync_envelope(_synthetic_wav(), frame_ms=40)

    assert envelope.duration_ms == 480
    assert envelope.frame_ms == 40
    assert len(envelope.values) == 12
    assert max(envelope.values[3:8]) > 0.50
    assert envelope.values[0] == 0.0
    assert envelope.values[-1] < 0.10


def test_playback_protocol_carries_compact_audio_envelope() -> None:
    event = StageAudioEvent(
        type="audio_start",
        turn_id="turn-envelope",
        segment_id=3,
        text="Xin chào.",
        audio_envelope=(0.0, 0.62, 0.21),
        audio_frame_ms=40,
        audio_duration_ms=120,
        audio_mouth_shape=(0.0, .65, .1),
    )
    frame = SimpleNamespace(
        turn_id="turn-envelope",
        segment_id=3,
        tts_text="Xin chào.",
        display_text="Xin chào.",
        prosody_style="warm_soft",
        speech_act="statement",
        emotion="soft_smile",
        motion="stand",
        interruptible=True,
        source="test",
    )

    payload = playback_event(event, stage_frame=frame).to_payload()

    assert payload["type"] == "audio_playback_started"
    assert payload["data"]["audio_envelope"] == [0.0, 0.62, 0.21]
    assert payload["data"]["audio_frame_ms"] == 40
    assert payload["data"]["audio_duration_ms"] == 120
    assert payload["data"]["audio_mouth_shape"] == [0.0, .65, .1]


def test_equal_level_pcm_can_drive_different_shapes_and_close_at_a_short_gap():
    rate = 8000
    samples = [
        int(10000 * math.sin(math.tau * frequency * index / rate)) if frequency else 0
        for frequency in (1000, 0, 2400)
        for index in range(480 if frequency else 160)
    ]
    wav = io.BytesIO()
    with wave.open(wav, "wb") as stream:
        stream.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(b"".join(value.to_bytes(2, "little", signed=True) for value in samples))
    lip = build_lip_sync_envelope(wav.getvalue())
    assert lip.frame_ms == 20
    context = SpeechContext.from_payload({"data": lip.to_payload()})
    animator = SpeechAnimator()
    animator.start(now=0, context=context)
    low, gap, high = (animator.sample(now=t) for t in (.02, .06, .10))
    assert abs(low.mouth_open_ratio - high.mouth_open_ratio) < .001
    assert low.mouth_shape > .5 and high.mouth_shape < .05
    assert gap.mouth_open_ratio == gap.mouth_shape == 0
    assert animator.sample(now=.14).mouth_shape == 0


def test_speech_animator_prefers_real_audio_envelope() -> None:
    animator = SpeechAnimator()
    animator.start(
        now=50.0,
        context=SpeechContext(
            text="A a.",
            audio_envelope=(0.0, 0.0, 0.72, 0.72, 0.0),
            audio_frame_ms=40,
            audio_duration_ms=200,
        ),
    )

    assert animator.sample(now=50.02).mouth_open_ratio < 0.01
    assert animator.sample(now=50.10).mouth_open_ratio > 0.65
    assert animator.sample(now=50.20).mouth_open_ratio == 0.0


def test_stage_state_merges_segment_expression_with_audio_envelope(monkeypatch) -> None:
    clock = {"now": 70.0}
    monkeypatch.setattr("live2d_body.stage_state.time.monotonic", lambda: clock["now"])
    state = Live2DStageState(idle_seed=2)

    state.handle_payload({
        "protocol": "eris.stage",
        "type": "stage_segment",
        "turn_id": "turn-merge",
        "seq": 4,
        "tts_text": "Thật à?",
        "emotion": "curious",
        "motion": "tilt_head",
        "data": {
            "prosody_style": "curious_soft",
            "speech_act": "question",
            "actions": ["tilt_head"],
        },
    })
    state.handle_payload({
        "protocol": "eris.stage",
        "type": "audio_playback_started",
        "turn_id": "turn-merge",
        "seq": 4,
        "tts_text": "Thật à?",
        "data": {
            "audio_envelope": [0.0, 0.75, 0.0],
            "audio_frame_ms": 40,
            "audio_duration_ms": 120,
        },
    })

    clock["now"] = 70.04
    snapshot = state.advance(0.0, now=clock["now"])
    assert abs(snapshot.mouth_open_ratio - 0.75) < 1e-9
    assert "question" in snapshot.speech_cue
    assert "tilt" in snapshot.speech_cue
    assert snapshot.gaze_bias_y > 0.0


def test_short_audio_envelope_closes_without_inventing_missing_frames() -> None:
    animator = SpeechAnimator()
    animator.start(
        now=10.0,
        context=SpeechContext(
            text="Miệng vẫn phải sống khi metadata audio lệch.",
            audio_envelope=(0.0, 0.7, 0.5, 0.0),
            audio_frame_ms=40,
            audio_duration_ms=1600,  # expected ~40 frame nhưng chỉ nhận 4
        ),
    )

    assert animator.sample(now=10.04).mouth_open_ratio > 0.65
    assert animator.sample(now=10.14).mouth_open_ratio == 0.0
    assert animator.sample(now=10.70).mouth_open_ratio == 0.0
