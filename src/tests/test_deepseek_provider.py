from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace


class _Response:
    status_code = 200
    reason = "OK"
    url = "https://api.deepseek.com/chat/completions"
    headers = {}
    text = ""

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [
                {
                    "message": {
                        "reasoning_content": "private reasoning",
                        "content": '{"ok":true}',
                    },
                    "finish_reason": "stop",
                }
            ]
        }


def _capture_deepseek_request(monkeypatch, *, thinking: str | None = None, model: str = "deepseek-v4-flash"):
    import llm.client as client_module

    captured = {}

    def fake_post(**kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(client_module, "_brokered_post", fake_post)
    if thinking is None:
        monkeypatch.delenv("ERIS_DEEPSEEK_THINKING", raising=False)
        monkeypatch.delenv("ERIS_DEEPSEEK_THINKING_MODE", raising=False)
    else:
        monkeypatch.setenv("ERIS_DEEPSEEK_THINKING", thinking)

    client = client_module.OpenAICompatibleClient(
        base_url="https://api.deepseek.com",
        api_key="sk-test-key",
        model_name=model,
        key_pool=None,
    )
    response = client.create_chat_completion(
        messages=[{"role": "user", "content": "Return JSON."}],
        temperature=0.4,
        frequency_penalty=0.1,
        presence_penalty=0.2,
        max_tokens=128,
        response_format={"type": "json_object"},
        reasoning_effort="medium",
    )
    return client, captured["json"], response


def test_deepseek_is_default_remote_provider(monkeypatch) -> None:
    import llm.process_manager as process_manager

    class _Pool:
        def acquire(self, allow_cooling=False):
            return SimpleNamespace(key="sk-deepseek-main")

    captured = {}

    class _Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    for name in ("ERIS_REMOTE_PROVIDER", "ERIS_REMOTE_BASE_URL", "ERIS_REMOTE_MODEL", "ERIS_REMOTE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-env")
    monkeypatch.setattr(process_manager, "apply_secrets_to_environ", lambda: None)
    monkeypatch.setattr(process_manager, "get_provider_profile", lambda *a, **k: {})
    monkeypatch.setattr(
        process_manager,
        "get_secret",
        lambda *names, default="": "sk-deepseek-env" if "DEEPSEEK_API_KEY" in names else default,
    )
    monkeypatch.setattr(process_manager, "get_key_pool", lambda kind: _Pool() if kind == "remote_main" else None)
    monkeypatch.setattr(process_manager, "OpenAICompatibleClient", _Client)

    manager = process_manager.RemoteModelProcessManager(logger=logging.getLogger("test"), project_root=".")
    manager._build_remote_client()

    assert captured["base_url"] == "https://api.deepseek.com"
    assert captured["model_name"] == "deepseek-v4-flash"
    assert captured["api_key"] == "sk-deepseek-main"


def test_deepseek_non_thinking_default_preserves_sampling(monkeypatch) -> None:
    client, payload, response = _capture_deepseek_request(monkeypatch)

    assert client.model_name == "deepseek-v4-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["temperature"] == 0.4
    assert payload["frequency_penalty"] == 0.1
    assert payload["presence_penalty"] == 0.2
    assert payload["max_tokens"] == 128
    assert payload["response_format"] == {"type": "json_object"}
    assert response["choices"][0]["message"]["content"] == '{"ok":true}'


def test_deepseek_thinking_payload_uses_supported_effort(monkeypatch) -> None:
    _client, payload, _response = _capture_deepseek_request(monkeypatch, thinking="enabled")

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"
    assert "temperature" not in payload
    assert "frequency_penalty" not in payload
    assert "presence_penalty" not in payload


def test_retired_deepseek_model_names_are_migrated(monkeypatch) -> None:
    client, payload, _response = _capture_deepseek_request(
        monkeypatch,
        thinking=None,
        model="deepseek-reasoner",
    )
    assert client.model_name == "deepseek-v4-flash"
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["thinking"] == {"type": "enabled"}


def test_deepseek_reasoning_content_is_telemetry_only() -> None:
    from llm.service import LlmService

    service = object.__new__(LlmService)
    response = {
        "choices": [
            {"message": {"reasoning_content": "hidden thought", "content": "public answer"}}
        ]
    }
    assert service._extract_reasoning(response) == "hidden thought"
    assert service._extract_content(response) == "public answer"


def test_deepseek_provider_detection_and_vision_capability() -> None:
    from llm.deepseek import supports_image_input
    from llm.token_budget import detect_provider

    assert detect_provider("https://api.deepseek.com", "deepseek-v4-flash") == "deepseek"
    assert supports_image_input("https://api.deepseek.com", "deepseek-v4-pro") is False


def test_legacy_aggregator_runtime_dependency_is_removed() -> None:
    root = Path(__file__).resolve().parents[1]
    critical_files = (
        root / "llm/process_manager.py",
        root / "llm/key_pool.py",
        root / "runtime/private_mind_model_adapter.py",
        root / "runtime/screen_vision_runtime.py",
    )
    forbidden = ("shop" + "aikey", "api." + "shop" + "aikey", "eris_" + "aggregator_api_key", "eris_" + "aggregator_base_url")
    for path in critical_files:
        text = path.read_text(encoding="utf-8").lower()
        assert not any(token in text for token in forbidden), path


def test_ds2api_fallback_integration(monkeypatch) -> None:
    import llm.deepseek_workers as deepseek_workers
    import llm.process_manager as process_manager
    from llm.deepseek_workers import WORKER_ROLES
    from llm.client import ChainedFallbackClient

    class _Pool:
        def acquire(self, allow_cooling=False):
            return SimpleNamespace(key="sk-deepseek-main")

    for name in (
        "ERIS_REMOTE_PROVIDER", "ERIS_REMOTE_BASE_URL", "ERIS_REMOTE_MODEL", "ERIS_REMOTE_API_KEY",
        "ERIS_DS2API_ENABLED", "ERIS_DS2API_BASE_URL", "ERIS_DS2API_API_KEY", "ERIS_DS2API_MODEL"
    ):
        monkeypatch.delenv(name, raising=False)
    for role in WORKER_ROLES:
        monkeypatch.delenv(f"ERIS_DS_{role.upper()}_API_KEY", raising=False)

    monkeypatch.setenv("ERIS_REMOTE_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-official")
    monkeypatch.setenv("ERIS_DS2API_ENABLED", "1")
    monkeypatch.setenv("ERIS_DS2API_BASE_URL", "http://localhost:6011/v1")
    monkeypatch.setenv("ERIS_DS2API_API_KEY", "sk-ds2api-key")
    monkeypatch.setenv("ERIS_DS2API_MODEL", "gpt-4o")

    monkeypatch.setattr(process_manager, "apply_secrets_to_environ", lambda: None)
    monkeypatch.setattr(process_manager, "get_provider_profile", lambda *a, **k: {})
    monkeypatch.setattr(
        process_manager,
        "get_secret",
        lambda *names, default="": (
            "sk-ds2api-key" if "ERIS_DS2API_API_KEY" in names else (
                "sk-deepseek-official" if "DEEPSEEK_API_KEY" in names else default
            )
        ),
    )
    monkeypatch.setattr(
        deepseek_workers,
        "get_secret",
        lambda *names, default="": (
            "sk-ds2api-key" if "ERIS_DS2API_API_KEY" in names else (
                "http://localhost:6011/v1" if "ERIS_DS2API_BASE_URL" in names else (
                    "gpt-4o" if "ERIS_DS2API_MODEL" in names else default
                )
            )
        ),
    )
    monkeypatch.setattr(process_manager, "get_key_pool", lambda kind: _Pool() if kind == "remote_main" else None)

    manager = process_manager.RemoteModelProcessManager(logger=logging.getLogger("test"), project_root=".")
    client = manager._build_remote_client()

    assert isinstance(client, ChainedFallbackClient)
    assert len(client._clients) == 2

    c1 = client._clients[0]
    c2 = client._clients[1]

    assert c1.base_url == "http://localhost:6011/v1"
    assert c1.model_name == "gpt-4o"
    assert c1.api_key == "sk-ds2api-key"

    assert c2.base_url == "https://api.deepseek.com"
    assert c2.model_name == "deepseek-v4-flash"
    assert c2.api_key == "sk-deepseek-main"


def test_ds2api_warning_logging() -> None:
    from llm.client import ChainedFallbackClient

    class FailingLocalClient:
        base_url = "http://localhost:6011/v1"
        model_name = "deepseek-chat"
        key_pool = None
        def create_chat_completion(self, **kwargs):
            raise RuntimeError("Connection refused")

    class SuccessfulOfficialClient:
        base_url = "https://api.deepseek.com"
        model_name = "deepseek-v4-flash"
        key_pool = None
        def create_chat_completion(self, **kwargs):
            return {"ok": True}

    captured_warnings = []
    class DummyLogger:
        def warning(self, msg, *args):
            captured_warnings.append(msg % args if args else msg)

    chained = ChainedFallbackClient(
        [FailingLocalClient(), SuccessfulOfficialClient()],
        logger=DummyLogger(),
    )
    result = chained.create_chat_completion()

    assert result == {"ok": True}
    assert any("WARNING: local DeepSeek Web proxy (Whale2API/DS2API) failed (Connection refused), falling back to Official API." in w for w in captured_warnings)


def test_openai_client_429_record_pressure(monkeypatch) -> None:
    import requests
    import llm.client as client_module

    class Fake429Response:
        status_code = 429
        reason = "Too Many Requests"
        url = "http://localhost:6011/v1/chat/completions"
        headers = {}
        text = '{"error": "rate limit"}'

        def raise_for_status(self):
            raise requests.HTTPError("429 Too Many Requests", response=self)

    def fake_post(**kwargs):
        return Fake429Response()

    monkeypatch.setattr(client_module, "_brokered_post", fake_post)

    client = client_module.OpenAICompatibleClient(
        base_url="http://localhost:6011/v1",
        api_key="sk-test",
        model_name="gpt-4o",
        key_pool=None,
    )

    try:
        client.create_chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            frequency_penalty=0,
            presence_penalty=0,
            max_tokens=10,
        )
    except requests.HTTPError as exc:
        assert exc.response.status_code == 429


def test_openai_client_empty_output_raises_runtime_error(monkeypatch) -> None:
    import llm.client as client_module

    class FakeEmptyResponse:
        status_code = 200
        reason = "OK"
        url = "http://localhost:6011/v1/chat/completions"
        headers = {}
        text = '{"choices": [{"message": {"content": ""}}]}'

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": ""}}]}

    monkeypatch.setattr(client_module, "_brokered_post", lambda **kwargs: FakeEmptyResponse())

    client = client_module.OpenAICompatibleClient(
        base_url="http://localhost:6011/v1",
        api_key="sk-test",
        model_name="gpt-4o",
        key_pool=None,
    )

    try:
        client.create_chat_completion(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            frequency_penalty=0,
            presence_penalty=0,
            max_tokens=10,
        )
        raise AssertionError("Should have raised RuntimeError for empty output")
    except RuntimeError as exc:
        assert "Empty output content" in str(exc)


def test_ds2api_forces_thinking_mode_enabled(monkeypatch) -> None:
    import llm.client as client_module

    captured = {}

    def fake_post(**kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(client_module, "_brokered_post", fake_post)
    monkeypatch.delenv("ERIS_DEEPSEEK_THINKING", raising=False)

    client = client_module.OpenAICompatibleClient(
        base_url="http://localhost:6011/v1",
        api_key="sk-test-key",
        model_name="deepseek-v4-flash",
        key_pool=None,
    )
    client.create_chat_completion(
        messages=[{"role": "user", "content": "Return JSON."}],
        temperature=0.4,
        frequency_penalty=0.1,
        presence_penalty=0.2,
        max_tokens=128,
    )

    assert captured["json"]["thinking"] == {"type": "enabled"}


def _capture_localhost_request(monkeypatch, *, role_context: str | None = None):
    import llm.client as client_module
    from llm.raw_trace import worker_role_context

    captured = {}

    def fake_post(**kwargs):
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(client_module, "_brokered_post", fake_post)
    monkeypatch.delenv("ERIS_DEEPSEEK_THINKING", raising=False)

    client = client_module.OpenAICompatibleClient(
        base_url="http://localhost:6011/v1",
        api_key="sk-test-key",
        model_name="deepseek-v4-flash",
        key_pool=None,
    )

    def _call():
        client.create_chat_completion(
            messages=[{"role": "user", "content": "Return JSON."}],
            temperature=0.4,
            frequency_penalty=0.1,
            presence_penalty=0.2,
            max_tokens=128,
        )

    if role_context is None:
        _call()
    else:
        with worker_role_context(role_context):
            _call()
    return captured["json"]


def test_ds2api_surface_role_disables_thinking_by_default(monkeypatch) -> None:
    # Surface lane đẩy role qua ContextVar (production path). Default env unset =>
    # ERIS_SURFACE_THINKING_DISABLED mặc định "1" => thinking tắt dù localhost force.
    monkeypatch.delenv("ERIS_SURFACE_THINKING_DISABLED", raising=False)
    payload = _capture_localhost_request(monkeypatch, role_context="surface")

    assert payload["thinking"] == {"type": "disabled"}
    # Disabled-mode branch giữ lại sampling controls.
    assert payload["temperature"] == 0.4
    assert payload["frequency_penalty"] == 0.1
    assert payload["presence_penalty"] == 0.2


def test_ds2api_surface_thinking_reenabled_when_flag_off(monkeypatch) -> None:
    monkeypatch.setenv("ERIS_SURFACE_THINKING_DISABLED", "0")
    payload = _capture_localhost_request(monkeypatch, role_context="surface")

    # Opt lại vào localhost force => thinking enabled.
    assert payload["thinking"] == {"type": "enabled"}


def test_ds2api_executive_carveout_unchanged(monkeypatch) -> None:
    # Không có executive/surface context + không set worker_role attribute => behavior
    # byte-for-byte như trước: localhost vẫn force thinking enabled.
    monkeypatch.setenv("ERIS_EXECUTIVE_THINKING_DISABLED", "1")
    monkeypatch.delenv("ERIS_SURFACE_THINKING_DISABLED", raising=False)
    payload = _capture_localhost_request(monkeypatch, role_context=None)

    assert payload["thinking"] == {"type": "enabled"}


def test_ds2api_executive_contextvar_disables_thinking_when_flag_on(monkeypatch) -> None:
    # ContextVar executive path tôn trọng ERIS_EXECUTIVE_THINKING_DISABLED=1 trên live router/worker.
    monkeypatch.setenv("ERIS_EXECUTIVE_THINKING_DISABLED", "1")
    monkeypatch.delenv("ERIS_SURFACE_THINKING_DISABLED", raising=False)
    payload = _capture_localhost_request(monkeypatch, role_context="executive")

    assert payload["thinking"] == {"type": "disabled"}

