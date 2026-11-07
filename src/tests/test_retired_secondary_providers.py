from __future__ import annotations

import logging

import pytest

from llm.client import GeminiClient, OpenAICompatibleClient
from llm.process_manager import RemoteModelProcessManager


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DISABLE_DOTENV", "1")
    monkeypatch.setattr("llm.process_manager.apply_secrets_to_environ", lambda: None)
    monkeypatch.setenv("ERIS_DS2API_ENABLED", "0")
    monkeypatch.setenv("ERIS_GROQ_API_KEY", "old-key-must-not-be-used")
    monkeypatch.setenv("GEMINI_API_KEY", "old-key-must-not-be-used")
    return RemoteModelProcessManager(logger=logging.getLogger(__name__), project_root=str(tmp_path))


@pytest.mark.parametrize("builder", ["build_residue_curator_client", "build_social_parser_client", "build_social_critic_client", "build_social_fallback_client"])
def test_removed_secondary_lanes_do_not_reactivate(manager, monkeypatch, builder):
    monkeypatch.setenv("ERIS_PRIVATE_RESIDUE_CURATOR_ENABLED", "1")
    assert getattr(manager, builder)() is None


def test_format_recovery_does_not_append_secondary_providers(manager, monkeypatch):
    monkeypatch.setenv("ERIS_REMOTE_PROVIDER", "deepseek")
    monkeypatch.setenv("ERIS_CHAT_FORMAT_FALLBACK_ENABLED", "1")
    monkeypatch.delenv("ERIS_CHAT_FALLBACK_BASE_URL", raising=False)
    assert manager.build_format_fallback_client() is None


@pytest.mark.parametrize("provider,base,model", [
    ("groq", "https://api.groq.com/openai/v1", "qwen/qwen3.6-27b"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta", "gemma-4-31b-it"),
])
def test_retired_vision_configuration_stays_disabled(manager, monkeypatch, provider, base, model):
    monkeypatch.setenv("ERIS_VISION_API_ENABLED", "1")
    monkeypatch.setenv("ERIS_VISION_API_PROVIDER", provider)
    monkeypatch.setenv("ERIS_VISION_API_BASE_URL", base)
    monkeypatch.setenv("ERIS_VISION_API_MODEL", model)
    monkeypatch.setenv("ERIS_VISION_API_KEY", "stale-key")
    assert manager.build_vision_client() is None


@pytest.mark.parametrize("base,model", [
    ("https://api.groq.com/openai/v1", "openai/gpt-oss-20b"),
    ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-flash-latest"),
    ("http://localhost:9999/v1", "gemma-4-31b-it"),
    ("http://localhost:8081/v1", "playwright/gemini/gemini-3.1-pro"),
])
def test_generic_transport_rejects_retired_endpoints_or_models(base, model):
    with pytest.raises(ValueError, match="retired"):
        OpenAICompatibleClient(base_url=base, api_key="stale-key", model_name=model)


def test_gemini_transport_is_retired():
    with pytest.raises(ValueError, match="retired"):
        GeminiClient(base_url="https://generativelanguage.googleapis.com/v1beta", api_key="stale-key", model_name="gemma-4-31b-it")


def test_retired_key_pools_ignore_old_credentials(monkeypatch):
    from llm.key_pool import _collect_gemini_keys, _collect_groq_keys

    monkeypatch.setenv("GEMINI_API_KEY", "stale-key")
    monkeypatch.setenv("ERIS_GROQ_API_KEY", "stale-key")
    assert not bool(_collect_gemini_keys()), "retired Gemini pool must be empty"
    assert not bool(_collect_groq_keys()), "retired Groq pool must be empty"


def test_tts_ignores_old_gemini_provider_selection(monkeypatch):
    from runtime import tts_engine

    monkeypatch.setattr("runtime.runtime_settings_store.runtime_text", lambda *a, **kw: "gemini")
    assert tts_engine._tts_provider() == "elevenlabs"


def test_localizer_ignores_old_gemini_provider_selection(monkeypatch):
    from runtime.speech_localizer import localizer_provider_name

    monkeypatch.setenv("ERIS_TTS_LOCALIZER_PROVIDER", "gemini")
    assert localizer_provider_name() == "deepseek"


def test_stale_settings_cannot_restore_remote_curator_or_google_audio():
    from runtime.runtime_settings_store import RuntimeSettingsStore

    settings = RuntimeSettingsStore._static_validate({
        "architecture": {"private_residue_curator_enabled": True},
        "tts": {"provider": "gemini"},
    })
    assert settings["architecture"]["private_residue_curator_enabled"] is False
    assert settings["tts"]["provider"] == "elevenlabs"


def test_elevenlabs_failure_uses_local_fallback(monkeypatch):
    from runtime import tts_engine

    def failed(*args, **kwargs):
        raise RuntimeError("synthetic elevenlabs failure")

    def forbidden(*args, **kwargs):
        pytest.fail("retired Google fallback was called")

    monkeypatch.setattr(tts_engine, "_synthesize_elevenlabs_wav", failed)
    monkeypatch.setattr(tts_engine, "_synthesize_wav", forbidden)
    monkeypatch.setattr(tts_engine, "_try_local_tts_fallback", lambda *args, **kwargs: b"local-audio")
    result = tts_engine._synthesize_active_provider_wav(
        "test", voice="voice", model="eleven_v3", language_hint="vi", locale="vi-VN",
        prosody_plan=None, prelocalized=True,
    )
    assert result == b"local-audio"


def test_residue_retains_real_content_without_calling_injected_model(tmp_path):
    from types import SimpleNamespace
    from cortex.private_mind.residue_curator import DiaryResidueStore, ResidueStoreConfig
    from runtime.private_mind_loop import PrivateMindLoop

    def forbidden(*args, **kwargs):
        pytest.fail("remote residue curator was called")

    store = DiaryResidueStore(config=ResidueStoreConfig(path=str(tmp_path / "residue.json")))
    store.capture(kind="private_fragment", text="A retained thought.", salience=0.9, now=1000)
    loop = PrivateMindLoop.__new__(PrivateMindLoop)
    loop.residue_store = store
    loop.model_adapter = SimpleNamespace(curate_residue=forbidden, residue_metadata=lambda: {"configured": True})
    loop._maybe_consolidate_residue(now=1001)
    reloaded = DiaryResidueStore(config=ResidueStoreConfig(path=str(tmp_path / "residue.json")))
    assert reloaded.diary_entries(now=1002)[0]["text"] == "A retained thought."


def test_transport_rechecks_model_after_a_client_is_cloned(monkeypatch):
    from llm.client import _brokered_post

    monkeypatch.setattr("requests.post", lambda *a, **kw: pytest.fail("network must stay unused"))
    with pytest.raises(ValueError, match="retired"):
        _brokered_post(
            base_url="https://api.deepseek.com", model_name="gemma-4-31b-it",
            messages=[], max_tokens=8, timeout_seconds=1,
            url="https://api.deepseek.com/chat/completions", json={"model": "gemma-4-31b-it"},
        )


@pytest.mark.parametrize("base", ["https://api.groq.com/openai/v1", "https://generativelanguage.googleapis.com/v1beta/openai"])
def test_localizer_rejects_stale_secondary_endpoint_before_network(monkeypatch, base):
    from runtime.speech_localizer import _translate_with_deepseek_api

    monkeypatch.setenv("ERIS_TTS_LOCALIZER_BASE_URL", base)
    monkeypatch.setattr("requests.post", lambda *a, **kw: pytest.fail("network must stay unused"))
    with pytest.raises(ValueError, match="retired"):
        _translate_with_deepseek_api(
            api_key="stale-key", model_name="deepseek-flash", system_instruction="test",
            prompt="test", timeout_seconds=1,
        )


def test_retired_weather_tool_cannot_send_main_credentials_to_google(monkeypatch):
    from actions.executor import ActionExecutor
    from runtime.inference_broker import GLOBAL_INFERENCE_BROKER

    monkeypatch.setattr("actions.executor.get_agent_profile", lambda *a, **kw: {})
    monkeypatch.setattr("actions.executor.get_secret", lambda *a, **kw: "synthetic-main-key")
    monkeypatch.setattr(GLOBAL_INFERENCE_BROKER, "run", lambda request, fn: fn())
    monkeypatch.setattr("requests.post", lambda *a, **kw: pytest.fail("weather sent a retired request"))
    result = ActionExecutor.__new__(ActionExecutor)._get_weather("Hanoi")
    assert result.success is False


def test_retired_weather_credentials_are_never_advertised(monkeypatch):
    from actions.capability_runtime import _remote_tool_credential_available

    monkeypatch.setattr("eris_secrets.get_agent_profile", lambda *a, **kw: {"api_key": "stale-key"})
    assert _remote_tool_credential_available("get_weather")[0] is False
