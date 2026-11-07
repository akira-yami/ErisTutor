# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from memory.living_user.gemini_adapter import GeminiWebUserProfileAdapter
from memory.living_user.runtime_adapter import CuratorInferenceError
from memory.memory_ai_curator import MemoryAICurator
from runtime.memory_gc_service import MemoryGCService, MemoryGCResult


def _response(text='{"operations": [], "action_operations": []}', finish="stop"):
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}]}


def _post(monkeypatch, data=None, error=None):
    response = Mock()
    response.json.return_value = data if data is not None else _response()
    response.raise_for_status.side_effect = error
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    return post


def test_service_factory_needs_no_runtime_cookies_or_package(monkeypatch):
    monkeypatch.setenv("ERIS_MEMORY_GEMINI_ENABLED", "1")
    monkeypatch.setenv("ERIS_MEMORY_GEMINI_BASE_URL", "http://127.0.0.1:8000/v1/")
    monkeypatch.setenv("ERIS_MEMORY_GEMINI_MODEL", "gemini-flash-lite")
    monkeypatch.setattr(requests, "post", lambda **kw: pytest.fail("network during bootstrap"))
    adapter = GeminiWebUserProfileAdapter.create_if_available()
    assert adapter.base_url == "http://127.0.0.1:8000/v1"
    assert adapter.model_name == "gemini-flash-lite"


def test_factory_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ERIS_MEMORY_GEMINI_ENABLED", "0")
    assert GeminiWebUserProfileAdapter.create_if_available() is None


def test_profile_and_episode_use_existing_service(monkeypatch):
    post = _post(monkeypatch, _response('{"operations": [{"op":"add","path":"interests.gaming","value":"Pokemmo"}],"action_operations":[]}'))
    adapter = GeminiWebUserProfileAdapter()
    result = adapter.propose_profile_operations({"user_text": "chơi pokemmo"})
    assert result["operations"][0]["value"] == "Pokemmo"
    assert post.call_args.args == ("http://127.0.0.1:8000/v1/chat/completions",)
    assert post.call_args.kwargs["json"]["model"] == "gemini-flash"
    assert post.call_args.kwargs["json"]["messages"][1]["content"].find("pokemmo") > 0
    _post(monkeypatch, _response('{"title":"Bàn về game","summary":"Người dùng kể về game"}'))
    assert adapter.summarize_episode({"turns": []})["title"] == "Bàn về game"


@pytest.mark.parametrize("raw", ["", "not json", "{}", '{"operations":{}}'])
def test_bad_output_is_retryable_not_success_empty(monkeypatch, raw):
    _post(monkeypatch, _response(raw))
    with pytest.raises(CuratorInferenceError):
        GeminiWebUserProfileAdapter().propose_profile_operations({"evidence_ids": ["turn:1"]})


@pytest.mark.parametrize("error", [requests.Timeout(), requests.HTTPError(), requests.ConnectionError()])
def test_provider_failure_is_retryable(monkeypatch, error):
    _post(monkeypatch, error=error)
    with pytest.raises(CuratorInferenceError):
        GeminiWebUserProfileAdapter().propose_profile_operations({})


def test_truncated_output_is_not_applied(monkeypatch):
    _post(monkeypatch, _response('{"operations":[]}', finish="length"))
    with pytest.raises(CuratorInferenceError, match="incomplete_output"):
        GeminiWebUserProfileAdapter().propose_profile_operations({})


def test_all_cleanup_owners_share_gemini_and_never_borrow_deepseek(monkeypatch):
    adapter = GeminiWebUserProfileAdapter()
    ds = Mock()
    llm = SimpleNamespace(curator_client=ds, inner_client=ds, client=ds, critic_client=ds)
    core = SimpleNamespace(living_user=SimpleNamespace(model_adapter=adapter))
    gc = MemoryGCService(memory_core=core, llm_service=llm)
    assert gc._get_curator_client() is adapter
    assert gc._get_critic_client() is adapter
    monkeypatch.setattr(GeminiWebUserProfileAdapter, "create_if_available", classmethod(lambda cls: adapter))
    _post(monkeypatch)
    assert MemoryAICurator(memory_core=core, llm_service=llm)._call_llm("system", "synthetic")
    ds.create_chat_completion.assert_not_called()
    assert gc.status_snapshot()["provider"] == "gemini_web"


def test_disabled_gemini_does_not_restore_legacy_ds_fallback(monkeypatch):
    monkeypatch.setenv("ERIS_MEMORY_GEMINI_ENABLED", "0")
    monkeypatch.setenv("ERIS_MEMORY_GC_ALLOW_MODEL_FALLBACK", "1")
    ds = Mock()
    llm = SimpleNamespace(curator_client=ds, inner_client=ds, client=ds, critic_client=ds)
    gc = MemoryGCService(memory_core=SimpleNamespace(living_user=None), llm_service=llm)
    assert gc._get_curator_client() is None
    assert MemoryAICurator(llm_service=llm)._call_llm("system", "synthetic") is None
    ds.create_chat_completion.assert_not_called()


def test_process_manager_curator_does_not_build_deepseek_worker(monkeypatch):
    from llm.process_manager import RemoteModelProcessManager
    monkeypatch.setenv("ERIS_MEMORY_GEMINI_ENABLED", "1")
    manager = object.__new__(RemoteModelProcessManager)
    manager.build_deepseek_worker_client = Mock(side_effect=AssertionError("wrong provider"))
    assert isinstance(manager.build_curator_client(), GeminiWebUserProfileAdapter)


@pytest.mark.parametrize("reply", [None, "not json", "{}"])
def test_failed_profile_review_is_not_throttled_for_a_day(tmp_path, monkeypatch, reply):
    from memory.living_user import LivingUserMemoryService
    living = LivingUserMemoryService(tmp_path)
    living.correct("user.name", "Khang", evidence_ids=("synthetic:1",))
    gc = MemoryGCService(memory_core=SimpleNamespace(living_user=living))
    adapter = GeminiWebUserProfileAdapter()
    calls = []

    def call(*args, **kwargs):
        calls.append(True)
        return reply

    monkeypatch.setattr(gc, "_call_model", call)
    for _ in range(2):
        result = MemoryGCResult()
        gc._run_living_user_review(result, adapter)
        assert result.warnings
    assert len(calls) == 2
