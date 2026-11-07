import sys
import os
import pytest

sys.path.insert(0, os.path.abspath("src"))


def test_attention_overseer_initialization(tmp_path, monkeypatch):
    monkeypatch.setenv("ERIS_HOME", str(tmp_path / ".eris_home"))
    monkeypatch.setenv("ERIS_REMOTE_API_ENABLED", "1")
    monkeypatch.setenv("ERIS_DS2API_ENABLED", "0")
    monkeypatch.setenv("ERIS_TTS_ENABLED", "0")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-mock-key-for-bootstrap-validation")

    from vision import AttentionOverseer

    overseer = AttentionOverseer(model_path="", n_ctx=2048)
    try:
        assert overseer.transport is not None
        assert overseer.coordinator is not None
        assert overseer.memory_core is not None
        assert overseer.action_executor is not None
        assert overseer.telemetry_service is not None
        assert overseer.decision_parser is not None
        assert overseer.llm_service is not None
    finally:
        overseer.stop("Test teardown")
