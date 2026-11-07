from __future__ import annotations

import json
import logging
import threading

import pytest

from llm.deepseek_workers import (
    JsonlContractError,
    JsonlWorkerClient,
    RoleWorkerBusyError,
    canonical_json_line,
    load_worker_spec,
    normalize_worker_role,
)
from llm.process_manager import RemoteModelProcessManager
from llm.service import LlmService
from runtime.settings_control_runtime import SettingsControlRuntimeMixin


def test_deepseek_role_workers_do_not_share_provider_circuit():
    from llm.client import OpenAICompatibleClient

    companion = OpenAICompatibleClient(
        base_url="http://127.0.0.1:6011/v1",
        api_key="companion-key",
        model_name="deepseek-v4-flash",
        provider_circuit_id="http://127.0.0.1:6011/v1#deepseek_web:companion",
    )
    inner = OpenAICompatibleClient(
        base_url="http://127.0.0.1:6011/v1",
        api_key="inner-key",
        model_name="deepseek-v4-flash",
        provider_circuit_id="http://127.0.0.1:6011/v1#deepseek_web:inner",
    )

    assert companion._provider_circuit is not inner._provider_circuit


class _FakeClient:
    base_url = "http://127.0.0.1:6011/v1"
    model_name = "deepseek-v4-flash"
    configured_model_name = "deepseek-v4-flash"
    key_pool = None

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        content = self.outputs.pop(0)
        return {"choices": [{"message": {"content": content}}]}


class _BlockingClient:
    base_url = "http://127.0.0.1:6011/v1"
    model_name = "deepseek-v4-flash"
    configured_model_name = "deepseek-v4-flash"
    key_pool = None

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = []

    def create_chat_completion(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        assert self.release.wait(2.0)
        return {"choices": [{"message": {"content": "ok"}}]}


def _call(client, *, response_format=None):
    return client.create_chat_completion(
        messages=[{"role": "user", "content": "return json"}],
        temperature=0.0,
        frequency_penalty=0.0,
        presence_penalty=0.0,
        max_tokens=64,
        response_format=response_format,
    )


def test_worker_spec_uses_exact_role_environment(monkeypatch):
    monkeypatch.delenv("ERIS_DS_OBSERVER_BASE_URL", raising=False)
    monkeypatch.delenv("ERIS_DS_OBSERVER_MODEL", raising=False)
    monkeypatch.delenv("ERIS_DS2API_BASE_URL", raising=False)
    monkeypatch.delenv("ERIS_DS2API_MODEL", raising=False)
    monkeypatch.setenv("ERIS_DS_BASE_URL", "http://localhost:6011/v1")
    monkeypatch.setenv("ERIS_DS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("ERIS_DS_OBSERVER_API_KEY", "observer-gateway-key")

    spec = load_worker_spec("observer")

    assert spec.role == "observer"
    assert spec.api_key == "observer-gateway-key"
    assert spec.base_url == "http://localhost:6011/v1"
    assert spec.model == "deepseek-v4-flash"
    assert spec.jsonl_enabled is True


def test_surface_worker_has_its_own_gateway_key(monkeypatch):
    monkeypatch.setenv("ERIS_DS_BASE_URL", "http://localhost:6011/v1")
    monkeypatch.setenv("ERIS_DS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("ERIS_DS_SURFACE_API_KEY", "surface-gateway-key")

    spec = load_worker_spec("surface")

    assert spec.role == "surface"
    assert spec.api_key == "surface-gateway-key"
    assert spec.jsonl_enabled is True


def test_surface_worker_ignores_example_placeholder(monkeypatch):
    monkeypatch.setenv("ERIS_DS_BASE_URL", "http://localhost:6011/v1")
    monkeypatch.setenv("ERIS_DS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("ERIS_DS_SURFACE_API_KEY", "GATEWAY_KEY_SURFACE")

    spec = load_worker_spec("surface")

    assert spec.api_key == ""
    assert spec.configured is False


def test_surface_worker_does_not_borrow_generic_gateway_key(monkeypatch):
    import llm.deepseek_workers as dw

    monkeypatch.delenv("ERIS_DS_SURFACE_API_KEY", raising=False)
    monkeypatch.setenv("ERIS_DS2API_ENABLED", "1")
    monkeypatch.setenv("ERIS_DS2API_API_KEY", "shared-key")
    monkeypatch.setenv("ERIS_DS_SURFACE_ALLOW_SHARED_KEY", "0")
    monkeypatch.setattr(dw, "get_secret", lambda *names, default="": "shared-key" if "ERIS_DS2API_API_KEY" in names else "")

    spec = load_worker_spec("surface")

    assert spec.api_key == ""
    assert spec.configured is False


def test_jsonl_worker_canonicalizes_structured_content():
    raw = _FakeClient(['```json\n{ "b": 2, "a": 1 }\n```'])
    worker = JsonlWorkerClient(raw, role="observer", jsonl_enabled=True)

    payload = _call(worker, response_format={"type": "json_object"})

    content = payload["choices"][0]["message"]["content"]
    assert content == '{"b":2,"a":1}'
    assert "\n" not in content
    assert json.loads(content) == {"b": 2, "a": 1}


def test_jsonl_worker_repairs_once():
    raw = _FakeClient(["not json", '{"ok":true}'])
    worker = JsonlWorkerClient(raw, role="critic", jsonl_enabled=True, repair_attempts=1)

    payload = _call(worker, response_format={"type": "json_object"})

    assert payload["choices"][0]["message"]["content"] == '{"ok":true}'
    assert len(raw.calls) == 2
    assert "ONE valid JSON value" in raw.calls[1]["messages"][-1]["content"]


def test_jsonl_worker_repairs_valid_json_with_wrong_schema_shape():
    raw = _FakeClient([
        '{"turn_id":"1","observation":{"user_act":"request","confidence":0.8}}',
        '{"user_act":"request","confidence":0.8}',
    ])
    worker = JsonlWorkerClient(raw, role="observer", jsonl_enabled=True, repair_attempts=1)
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "observer",
            "schema": {
                "type": "object",
                "required": ["user_act", "confidence"],
                "properties": {
                    "user_act": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        },
    }

    payload = _call(worker, response_format=response_format)

    assert payload["choices"][0]["message"]["content"] == '{"user_act":"request","confidence":0.8}'
    assert len(raw.calls) == 2
    repair_prompt = raw.calls[1]["messages"][-1]["content"]
    assert "Required top-level keys: user_act, confidence" in repair_prompt
    assert "schema_required_missing" in repair_prompt


def test_jsonl_worker_does_not_touch_free_text():
    raw = _FakeClient(["plain style text"])
    worker = JsonlWorkerClient(raw, role="companion", jsonl_enabled=False)

    payload = _call(worker, response_format=None)

    assert payload["choices"][0]["message"]["content"] == "plain style text"


def test_jsonl_worker_raises_after_failed_repair():
    raw = _FakeClient(["bad", "still bad"])
    worker = JsonlWorkerClient(raw, role="inner", jsonl_enabled=True, repair_attempts=1)

    with pytest.raises(JsonlContractError):
        _call(worker, response_format={"type": "json_object"})


def test_inner_worker_drops_when_actor_is_busy(monkeypatch):
    monkeypatch.setenv("ERIS_DS_ROLE_ACTORS", "1")
    monkeypatch.setenv("ERIS_DS_INNER_DROP_IF_BUSY", "1")
    raw = _BlockingClient()
    worker = JsonlWorkerClient(raw, role="inner", jsonl_enabled=False)
    result: list[object] = []

    def _first_call():
        try:
            result.append(_call(worker, response_format=None))
        except Exception as exc:  # noqa: BLE001
            result.append(exc)

    thread = threading.Thread(target=_first_call)
    thread.start()
    assert raw.started.wait(2.0)

    with pytest.raises(RoleWorkerBusyError):
        _call(worker, response_format=None)

    raw.release.set()
    thread.join(2.0)
    assert result and not isinstance(result[0], Exception)
    assert len(raw.calls) == 1


def test_process_manager_builds_and_caches_role_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DS_BASE_URL", "http://127.0.0.1:6011/v1")
    monkeypatch.setenv("ERIS_DS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("ERIS_DS_RESEARCH_API_KEY", "research-gateway-key")
    manager = RemoteModelProcessManager(logger=logging.getLogger("test"), project_root=str(tmp_path))

    first = manager.build_deepseek_worker_client("research")
    second = manager.build_deepseek_worker_client("research")

    assert first is second
    assert first.worker_role == "research"
    assert first.api_key == "research-gateway-key"


def test_style_client_routes_to_critic_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DS2API_ENABLED", "1")
    monkeypatch.setenv("ERIS_DS_CRITIC_API_KEY", "critic-gateway-key")
    monkeypatch.setenv("ERIS_DS_CRITIC_BASE_URL", "http://127.0.0.1:6014/v1")
    monkeypatch.setenv("ERIS_DS_MODEL", "deepseek-v4-flash")
    manager = RemoteModelProcessManager(logger=logging.getLogger("test"), project_root=str(tmp_path))

    client = manager.build_style_client()

    assert client is not None
    assert client.worker_role == "critic"
    assert client.api_key == "critic-gateway-key"


def test_style_is_not_a_deepseek_pool_role():
    with pytest.raises(ValueError):
        normalize_worker_role("style")


def test_llm_service_exposes_named_workers():
    companion = object()
    observer = object()
    critic = object()
    inner = object()
    surface = object()
    style = object()
    research = object()

    service = LlmService(
        companion,
        style_client=style,
        observer_client=observer,
        critic_client=critic,
        inner_client=inner,
        surface_client=surface,
        research_client=research,
    )

    assert service.companion_client is companion
    assert service.observer_client is observer
    assert service.critic_client is critic
    assert service.inner_client is inner
    assert service.surface_client is surface
    assert service.style_client is style
    assert service.research_client is research
    assert service.social_parser_client is observer
    assert service.social_critic_client is critic


def test_research_client_no_longer_falls_back_to_main_or_fallback():
    companion = object()
    fallback = object()

    service = LlmService(companion, fallback_client=fallback)

    assert service.research_client is None


def test_process_manager_research_client_is_retired(monkeypatch, tmp_path):
    monkeypatch.setenv("ERIS_DS_BASE_URL", "http://127.0.0.1:6011/v1")
    monkeypatch.setenv("ERIS_DS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("ERIS_DS_RESEARCH_API_KEY", "research-gateway-key")
    manager = RemoteModelProcessManager(logger=logging.getLogger("test"), project_root=str(tmp_path))

    assert manager.build_research_client() is None


def test_companion_key_can_boot_without_official_deepseek_key(monkeypatch, tmp_path):
    import llm.deepseek_workers as dw
    import llm.process_manager as pm

    monkeypatch.setenv("ERIS_REMOTE_PROVIDER", "deepseek")
    monkeypatch.setenv("ERIS_REMOTE_API_ENABLED", "1")
    monkeypatch.setenv("ERIS_DS2API_ENABLED", "1")
    monkeypatch.setenv("ERIS_DS_COMPANION_API_KEY", "companion-gateway-key")
    monkeypatch.setenv("ERIS_DS_BASE_URL", "http://127.0.0.1:6011/v1")
    monkeypatch.setenv("ERIS_DS_MODEL", "deepseek-v4-flash")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("ERIS_REMOTE_API_KEY", raising=False)
    monkeypatch.delenv("ERIS_DS2API_API_KEY", raising=False)
    monkeypatch.setattr(dw, "get_secret", lambda *names, default="": "")
    monkeypatch.setattr(pm, "get_provider_profile", lambda *args, **kwargs: {})
    monkeypatch.setattr(pm, "get_secret", lambda *names, default="": "")
    monkeypatch.setattr(pm, "_bootstrap_pool_key", lambda pool: "")

    manager = RemoteModelProcessManager(logger=logging.getLogger("test"), project_root=str(tmp_path))
    client = manager.build_client()

    assert client.worker_role == "companion"
    assert client.base_url == "http://127.0.0.1:6011/v1"


def test_ds2api_disabled_flag_keeps_companion_key_off_main_route(monkeypatch, tmp_path):
    import llm.deepseek_workers as dw
    import llm.process_manager as pm

    monkeypatch.setenv("ERIS_REMOTE_PROVIDER", "deepseek")
    monkeypatch.setenv("ERIS_REMOTE_API_ENABLED", "1")
    monkeypatch.setenv("ERIS_DS2API_ENABLED", "0")
    monkeypatch.setenv("ERIS_DS_COMPANION_API_KEY", "companion-gateway-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-official")
    monkeypatch.setattr(dw, "get_secret", lambda *names, default="": "")
    monkeypatch.setattr(pm, "apply_secrets_to_environ", lambda: None)
    monkeypatch.setattr(pm, "get_provider_profile", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        pm,
        "get_secret",
        lambda *names, default="": "sk-official" if "DEEPSEEK_API_KEY" in names else default,
    )
    monkeypatch.setattr(pm, "_bootstrap_pool_key", lambda pool: "")

    manager = RemoteModelProcessManager(logger=logging.getLogger("test"), project_root=str(tmp_path))
    client = manager.build_client()

    assert client.base_url == "https://api.deepseek.com"
    assert client.api_key == "sk-official"
    assert manager.build_deepseek_worker_client("companion") is None


def test_surface_worker_waits_instead_of_dropping_by_default(monkeypatch):
    # Eloquence: surface không còn drop mặc định khi actor bận. Lượt user thà
    # chờ actor rảnh (tối đa _actor_acquire_timeout, mặc định ~2s) còn hơn rơi
    # fallback thô. Env override ERIS_DS_SURFACE_DROP_IF_BUSY vẫn bật lại được.
    monkeypatch.setenv("ERIS_DS_ROLE_ACTORS", "1")
    monkeypatch.delenv("ERIS_DS_SURFACE_DROP_IF_BUSY", raising=False)
    raw = _BlockingClient()
    worker = JsonlWorkerClient(raw, role="surface", jsonl_enabled=False)
    assert worker._drop_if_busy is False

    result: list[object] = []

    def _first_call():
        try:
            result.append(_call(worker, response_format=None))
        except Exception as exc:  # noqa: BLE001
            result.append(exc)

    thread = threading.Thread(target=_first_call)
    thread.start()
    assert raw.started.wait(2.0)

    # Second caller does not drop; it waits for the actor, then proceeds once
    # the first call releases.
    def _second_call():
        try:
            result.append(_call(worker, response_format=None))
        except Exception as exc:  # noqa: BLE001
            result.append(exc)

    second = threading.Thread(target=_second_call)
    second.start()
    raw.release.set()
    thread.join(2.0)
    second.join(5.0)

    assert len(result) == 2
    assert all(not isinstance(item, Exception) for item in result)
    assert len(raw.calls) == 2


def test_surface_worker_drop_if_busy_env_override_still_works(monkeypatch):
    monkeypatch.setenv("ERIS_DS_ROLE_ACTORS", "1")
    monkeypatch.setenv("ERIS_DS_SURFACE_DROP_IF_BUSY", "1")
    raw = _BlockingClient()
    worker = JsonlWorkerClient(raw, role="surface", jsonl_enabled=False)
    assert worker._drop_if_busy is True
    result: list[object] = []

    def _first_call():
        try:
            result.append(_call(worker, response_format=None))
        except Exception as exc:  # noqa: BLE001
            result.append(exc)

    thread = threading.Thread(target=_first_call)
    thread.start()
    assert raw.started.wait(2.0)

    with pytest.raises(RoleWorkerBusyError):
        _call(worker, response_format=None)

    raw.release.set()
    thread.join(2.0)
    assert result and not isinstance(result[0], Exception)
    assert len(raw.calls) == 1


def test_surface_worker_repairs_jsonl_once_by_default(monkeypatch):
    monkeypatch.setenv("ERIS_DS_SURFACE_API_KEY", "surface-gateway-key")
    monkeypatch.delenv("ERIS_DS_SURFACE_JSONL_REPAIR_ATTEMPTS", raising=False)
    monkeypatch.delenv("ERIS_DS_JSONL_REPAIR_ATTEMPTS", raising=False)

    spec = load_worker_spec("surface")

    assert spec.jsonl_repair_attempts == 1


def test_surface_worker_repair_attempts_still_allow_role_override(monkeypatch):
    monkeypatch.setenv("ERIS_DS_SURFACE_API_KEY", "surface-gateway-key")
    monkeypatch.setenv("ERIS_DS_SURFACE_JSONL_REPAIR_ATTEMPTS", "0")

    spec = load_worker_spec("surface")

    assert spec.jsonl_repair_attempts == 0


def test_settings_health_exposes_deepseek_worker_wiring_without_raw_keys(monkeypatch):
    monkeypatch.setenv("ERIS_DS_SEED_API_KEY", "seed-gateway-key")
    monkeypatch.setenv("ERIS_DS_SEED_BASE_URL", "http://127.0.0.1:6015/v1")
    monkeypatch.setenv("ERIS_DS_MODEL", "deepseek-v4-flash")

    class Harness(SettingsControlRuntimeMixin):
        pass

    harness = Harness()
    harness.llm_service = type(
        "Service",
        (),
        {"seed_client": type("SeedWorker", (), {"worker_role": "seed"})()},
    )()

    snapshot = harness._deepseek_workers_health_snapshot()
    seed = snapshot["roles"]["seed"]

    assert snapshot["available"] is True
    assert seed["configured"] is True
    assert seed["ready"] is True
    assert seed["base_url"] == "http://127.0.0.1:6015/v1"
    assert seed["key_fingerprint"]
    assert seed["key_fingerprint"] != "seed-gateway-key"


def test_curator_worker_uses_dedicated_gateway_key(monkeypatch):
    monkeypatch.setenv("ERIS_DS_BASE_URL", "http://localhost:6011/v1")
    monkeypatch.setenv("ERIS_DS_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("ERIS_DS2API_API_KEY", "shared-key")
    monkeypatch.setenv("ERIS_DS_CURATOR_API_KEY", "curator-gateway-key")

    spec = load_worker_spec("curator")

    assert spec.role == "curator"
    assert spec.api_key == "curator-gateway-key"
    assert spec.jsonl_enabled is True


def test_llm_service_keeps_curator_as_distinct_lane():
    companion = object()
    curator = object()
    service = LlmService(companion, curator_client=curator)

    assert service.client is companion
    assert service.curator_client is curator
