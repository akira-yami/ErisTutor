# -*- coding: utf-8 -*-
from runtime.conversation import InterruptKind, classify_interrupt


def test_backchannel_does_not_cancel_audio_or_continuation():
    result = classify_interrupt("ừm")
    assert result.kind == InterruptKind.BACKCHANNEL
    assert result.cancel_audio is False
    assert result.cancel_generation is False
    assert result.preserve_continuation is True


def test_stop_and_steering_preempt():
    stop = classify_interrupt("khoan, dừng lại")
    assert stop.kind == InterruptKind.STOP
    assert stop.cancel_audio and stop.cancel_generation

    steer = classify_interrupt("không, chuyển qua phần khác")
    assert steer.kind == InterruptKind.STEERING
    assert steer.cancel_audio and steer.cancel_generation


def test_voice_manager_vad_backchannel_never_supersedes_audio():
    from runtime.voice_manager import VoiceManager

    class _TTS:
        def __init__(self):
            self.cancelled = 0

        def supersede_all(self, *, reason=""):
            self.cancelled += 1
            return True

    tts = _TTS()
    manager = VoiceManager(tts_engine=tts)

    assert manager.on_vad_start(timestamp=1.0).strategy == "pending"
    partial = manager.on_asr_partial("đúng rồi", confidence=0.9, timestamp=1.1)
    assert partial.strategy == "continue_short"
    assert not partial.committed
    final = manager.on_asr_final("đúng rồi", confidence=0.99, timestamp=1.2)
    assert final.strategy == "continue_short"
    assert not final.cancelled_audio
    assert tts.cancelled == 0


def test_voice_manager_partial_stop_commits_fast_barge_in():
    from runtime.voice_manager import VoiceManager

    class _TTS:
        def __init__(self):
            self.cancelled = 0

        def supersede_all(self, *, reason=""):
            self.cancelled += 1
            return True

    tts = _TTS()
    manager = VoiceManager(tts_engine=tts)
    manager.on_vad_start(timestamp=1.0)
    result = manager.on_asr_partial("dừng", confidence=0.8, timestamp=1.1)

    assert result.committed
    assert result.cancelled_audio
    assert result.strategy == "yield_repair"
    assert tts.cancelled == 1


def test_user_control_voice_path_uses_tentative_vad_not_unconditional_hard_cancel():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    source = (root / "runtime/user_control_runtime.py").read_text(encoding="utf-8")
    method = source.split("    def _handle_voice_observation_event", 1)[1].split(
        "    def _handle_audio_playback_aborted", 1
    )[0]
    assert "on_vad_start" in method
    assert "on_asr_partial" in method
    assert "on_asr_final" in method
    assert ".interrupt_signal(" not in method
