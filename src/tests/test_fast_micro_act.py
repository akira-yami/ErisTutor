# -*- coding: utf-8 -*-
"""Test fast micro-act reflex (phản xạ tức thì < 100ms: tiếng thở, ừm, ểh + Live2D múa)."""

import os
import threading
import time
from unittest.mock import MagicMock

from runtime.interaction_dynamics import DynamicsDecision, DynamicsAction, MicroActKind, MicroActRouter
from runtime.public_output_runtime import PublicOutputRuntimeMixin
from runtime.stage_protocol import StageProtocolEvent


class _DummyCoordinator(PublicOutputRuntimeMixin):
    def __init__(self, stage_event_bus=None, transport=None):
        self.stage_event_bus = stage_event_bus
        self.transport = transport
        self.logger = MagicMock()
        self.interaction_clock = MagicMock(speech_floor_busy=False)


def test_fast_micro_act_emits_stage_event_and_live2d_cue(monkeypatch, tmp_path):
    # Setup dummy vocal cache dir with a sample WAV
    cache_dir = tmp_path / "tts_vocal_cache"
    hum_dir = cache_dir / "voice_director_soft_hum"
    hum_dir.mkdir(parents=True, exist_ok=True)
    
    # Simple valid 1-sample WAV PCM mono
    import wave
    wav_file = hum_dir / "sample.wav"
    with wave.open(str(wav_file), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 480) # 20ms of audio

    monkeypatch.setenv("ERIS_TTS_VOCAL_CACHE_DIR", str(cache_dir))

    emitted_events: list[StageProtocolEvent] = []
    dummy_bus = MagicMock()
    dummy_bus.emit_live2d = lambda evt: emitted_events.append(evt) or True

    dummy_transport = MagicMock()
    transport_payloads = []
    dummy_transport.send_json = lambda p: transport_payloads.append(p)

    coord = _DummyCoordinator(stage_event_bus=dummy_bus, transport=dummy_transport)

    # Test emitting RECEIPT ("ừm" / tiếng thở)
    result = coord._emit_fast_micro_act("Ừm.", act_kind="RECEIPT", timestamp=100.0)
    assert result is True
    assert len(emitted_events) >= 1
    
    start_event = emitted_events[0]
    assert start_event.type == "audio_playback_started"
    assert start_event.motion == "nod_small"
    assert start_event.emotion == "warm_soft"
    assert start_event.display_text == ""  # Bubble is clean as requested
    assert "audio_envelope" in start_event.data

    # Transport received notification
    assert len(transport_payloads) == 1
    assert transport_payloads[0]["type"] == "fast_micro_act"
    assert transport_payloads[0]["act_kind"] == "RECEIPT"

    # Test supersede cancels micro act
    stop_event = getattr(coord, "_current_micro_act_stop", None)
    assert isinstance(stop_event, threading.Event)
    assert not stop_event.is_set()

    coord._supersede_tts_for_new_user_turn("new_user_input")
    assert stop_event.is_set()


def test_fast_micro_act_repair_request(monkeypatch, tmp_path):
    cache_dir = tmp_path / "tts_vocal_cache"
    curious_dir = cache_dir / "curious_hm"
    curious_dir.mkdir(parents=True, exist_ok=True)
    
    import wave
    wav_file = curious_dir / "sample_curious.wav"
    with wave.open(str(wav_file), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 480)

    monkeypatch.setenv("ERIS_TTS_VOCAL_CACHE_DIR", str(cache_dir))

    emitted_events: list[StageProtocolEvent] = []
    dummy_bus = MagicMock()
    dummy_bus.emit_live2d = lambda evt: emitted_events.append(evt) or True

    coord = _DummyCoordinator(stage_event_bus=dummy_bus)
    result = coord._emit_fast_micro_act("Hả?", act_kind="REPAIR_REQUEST", timestamp=101.0)
    assert result is True
    assert len(emitted_events) >= 1
    
    start_event = emitted_events[0]
    assert start_event.motion == "tilt_head"
    assert start_event.emotion == "curious"


def test_fast_micro_act_playful_cry_huhu(monkeypatch, tmp_path):
    cache_dir = tmp_path / "tts_vocal_cache"
    cry_dir = cache_dir / "fake_cry_refusal"
    cry_dir.mkdir(parents=True, exist_ok=True)
    
    import wave
    wav_file = cry_dir / "cry.wav"
    with wave.open(str(wav_file), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 480)

    monkeypatch.setenv("ERIS_TTS_VOCAL_CACHE_DIR", str(cache_dir))

    emitted_events: list[StageProtocolEvent] = []
    dummy_bus = MagicMock()
    dummy_bus.emit_live2d = lambda evt: emitted_events.append(evt) or True

    coord = _DummyCoordinator(stage_event_bus=dummy_bus)
    result = coord._emit_fast_micro_act("Huhu...", act_kind="PLAYFUL_CRY", timestamp=102.0, user_text="sao bắt nạt tui huhu")
    assert result is True
    assert len(emitted_events) >= 1
    
    start_event = emitted_events[0]
    assert start_event.motion == "shake_head_small"
    assert start_event.emotion == "sad_empathy"


def test_interaction_dynamics_detects_emotional_micro_acts():
    from runtime.interaction_dynamics import InteractionDynamicsController
    ctrl = InteractionDynamicsController()

    # User trêu / mếu máo
    d1 = ctrl.evaluate_incoming("sao bắt nạt em huhu", speech_busy=False)
    assert d1.micro_act == MicroActKind.PLAYFUL_CRY

    # User cười
    d2 = ctrl.evaluate_incoming("haha buồn cười quá", speech_busy=False)
    assert d2.micro_act == MicroActKind.PLAYFUL_CHUCKLE

    # User trách yêu / dỗi
    d3 = ctrl.evaluate_incoming("hứ đáng ghét thật", speech_busy=False)
    assert d3.micro_act == MicroActKind.SULK

    # User thở dài / cạn lời
    d4 = ctrl.evaluate_incoming("haizz mệt mỏi ghê", speech_busy=False)
    assert d4.micro_act == MicroActKind.DEADPAN

    # User ngơ ngác / hỏi ngắn
    d5 = ctrl.evaluate_incoming("ủa?", speech_busy=False)
    assert d5.micro_act == MicroActKind.REPAIR_REQUEST


def test_ambient_bio_reflex_when_idle(monkeypatch, tmp_path):
    from runtime.presence_controller import PresenceControllerMixin
    from runtime.micro_reflex_system import MicroReflexEngine

    cache_dir = tmp_path / "tts_vocal_cache"
    sigh_dir = cache_dir / "voice_director_soft_sigh"
    sigh_dir.mkdir(parents=True, exist_ok=True)
    import wave
    with wave.open(str(sigh_dir / "sigh.wav"), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(24000)
        wf.writeframes(b"\x00\x00" * 480)

    monkeypatch.setenv("ERIS_TTS_VOCAL_CACHE_DIR", str(cache_dir))

    class _FullCoordinator(PresenceControllerMixin, PublicOutputRuntimeMixin):
        def __init__(self, bus):
            self.stage_event_bus = bus
            self.logger = MagicMock()
            self.interaction_clock = MagicMock()
            self.interaction_clock.speech_floor_busy = False
            self.interaction_clock.busy = False
            self.interaction_clock.last_public_activity_at = 1000.0

    emitted_events: list[StageProtocolEvent] = []
    dummy_bus = MagicMock()
    dummy_bus.emit_live2d = lambda evt: emitted_events.append(evt) or True

    coord = _FullCoordinator(bus=dummy_bus)

    # 1. User mới chat cách đây 20s (< 45s) -> Không phát ambient
    res_recent = coord._maybe_emit_ambient_micro_reflex(now=1020.0)
    assert res_recent is False
    assert len(emitted_events) == 0

    # 2. User idle 60s (>= 45s) -> Tự động phát ambient reflex (thở dài nhẹ)
    res_idle = coord._maybe_emit_ambient_micro_reflex(now=1060.0)
    assert res_idle is True
    assert len(emitted_events) >= 1

    event = emitted_events[0]
    assert event.type == "audio_playback_started"
    assert event.source == "fast_micro_act"
    assert event.display_text == ""
    assert event.data["category"] == "AMBIENT"
    assert event.data["micro_act"] == "AMBIENT_SIGH"
    assert event.data["param_offsets"].get("ParamBreath") == 0.3

    # 3. Thử phát lại ngay sau 5s -> Bị chặn bởi Category Cooldown (35s)
    res_spam = coord._maybe_emit_ambient_micro_reflex(now=1065.0)
    assert res_spam is False


def test_ambient_drowsy_at_late_night():
    from runtime.micro_reflex_system import MicroReflexEngine
    engine = MicroReflexEngine()
    # Ban đêm 23h, idle > 45s -> AMBIENT_DROWSY
    act = engine.evaluate_ambient(idle_seconds=60.0, hour_of_day=23, now=5000.0)
    assert act == MicroActKind.AMBIENT_DROWSY


def test_cognitive_leakage_from_private_mind():
    from runtime.private_mind_daemon import PrivateMindDaemon, PrivateMindDaemonConfig
    from types import SimpleNamespace

    daemon = PrivateMindDaemon.__new__(PrivateMindDaemon)
    daemon.logger = MagicMock()

    emitted_acts = []
    mock_owner = SimpleNamespace(
        interaction_clock=SimpleNamespace(speech_floor_busy=False),
        _emit_fast_micro_act=lambda surface, act_kind, timestamp: emitted_acts.append(act_kind) or True,
    )

    # 1. Result có confidence cao (eureka)
    eureka_result = SimpleNamespace(
        judgment=SimpleNamespace(pressure=0.1, confidence=0.92, dominant_judgment="Tìm ra rồi!"),
    )
    ok_eureka = daemon._maybe_leak_cognition(eureka_result, mock_owner)
    assert ok_eureka is True
    assert MicroActKind.LEAK_EUREKA in emitted_acts

    # 2. Result có open questions (ponder)
    ponder_result = SimpleNamespace(
        judgment=SimpleNamespace(pressure=0.1, confidence=0.5, open_questions=("Ủa cái này là sao ta?",), dominant_judgment=""),
    )
    ok_ponder = daemon._maybe_leak_cognition(ponder_result, mock_owner)
    assert ok_ponder is True
    assert MicroActKind.LEAK_PONDER in emitted_acts


