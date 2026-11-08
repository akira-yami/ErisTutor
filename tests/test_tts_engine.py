import os
import pytest
from runtime.tts_engine import TtsEngine, _tts_provider, _voice, _synthesize_edge_tts_wav
from runtime.tts_budget import _normalize_provider, GLOBAL_TTS_REQUEST_BUDGET
from runtime.ai_orchestrated.orchestrator import AIAutonomousOrchestrator

def test_tts_provider_detection():
    assert _normalize_provider("edge-tts") == "edge-tts"
    assert _normalize_provider("edge") == "edge-tts"
    assert GLOBAL_TTS_REQUEST_BUDGET.transcript_limit("edge-tts") >= 900

def test_edge_tts_voice_and_init(monkeypatch):
    monkeypatch.setenv("ERIS_TTS_PROVIDER", "edge-tts")
    monkeypatch.setenv("ERIS_TTS_LANGUAGE", "vi")
    assert _tts_provider() == "edge-tts"
    voice = _voice("vi")
    assert "vi-VN" in voice

    engine = TtsEngine()
    assert engine._ok is True
    assert "edge-tts" in engine.cache_signature()

def test_orchestrator_run_force_speak_accepts_argument():
    class DummyHost:
        def __init__(self):
            self.calls = []
        def _run_v12_signal(self, signal, metadata=None, sleep_after=False):
            self.calls.append((signal, metadata))

    # Mock instance without running full bootstrap
    orchestrator = object.__new__(AIAutonomousOrchestrator)
    orchestrator.host = DummyHost()
    orchestrator._run_v12_signal = orchestrator.host._run_v12_signal

    # Should not raise TypeError when argument is passed
    orchestrator.run_force_speak("Xin chào!")
    assert len(orchestrator.host.calls) == 1
    assert orchestrator.host.calls[0][1]["prompt_hint"] == "Xin chào!"
