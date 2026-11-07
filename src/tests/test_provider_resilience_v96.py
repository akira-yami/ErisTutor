# -*- coding: utf-8 -*-
"""V96 regression: provider health dùng chung + deadline foreground tuyệt đối."""
from __future__ import annotations

import time

import pytest


def test_child_inference_context_never_extends_parent_deadline() -> None:
    from runtime.inference_broker import current_inference_context, inference_context

    with inference_context("chat_main", timeout_seconds=0.8):
        parent_deadline = current_inference_context().deadline_at
        assert parent_deadline is not None
        with inference_context("chat_main", timeout_seconds=30.0):
            child_deadline = current_inference_context().deadline_at
            assert child_deadline is not None
            # Child/fallback không được tự reset đồng hồ và sống lâu hơn user turn cha.
            assert child_deadline <= parent_deadline + 0.001


def test_provider_health_offline_allows_only_one_due_foreground_probe() -> None:
    from runtime.provider_health import ProviderHealthRegistry, ProviderHealthState

    now = [100.0]
    health = ProviderHealthRegistry(clock=lambda: now[0], backoff_steps=(5.0, 15.0, 30.0))

    health.record_failure("remote_llm", RuntimeError("all tiers failed"), hard=True)
    assert health.snapshot("remote_llm").state == ProviderHealthState.OFFLINE

    # Trong cooldown: cả background lẫn foreground đều fail-fast, không đập provider tiếp.
    assert health.acquire("remote_llm", lane="autonomous").allowed is False
    assert health.acquire("remote_llm", lane="chat_main").allowed is False

    now[0] += 5.1
    first = health.acquire("remote_llm", lane="chat_main")
    second = health.acquire("remote_llm", lane="chat_main")
    assert first.allowed is True and first.probe is True
    assert second.allowed is False
    assert health.snapshot("remote_llm").state == ProviderHealthState.PROBING

    health.record_success("remote_llm")
    assert health.snapshot("remote_llm").state == ProviderHealthState.HEALTHY
    assert health.acquire("remote_llm", lane="autonomous").allowed is True


def test_chained_fallback_respects_remaining_foreground_budget() -> None:
    from llm.client import ChainedFallbackClient
    from runtime.inference_broker import inference_context
    from runtime.provider_health import ProviderHealthRegistry

    seen: list[tuple[str, float]] = []

    class LocalFail:
        base_url = "http://127.0.0.1:6011/v1"
        model_name = "deepseek-v4-flash"
        key_pool = None
        worker_role = "companion"

        def create_chat_completion(self, **kwargs):
            seen.append(("local", float(kwargs.get("timeout") or 0.0)))
            raise RuntimeError("local down")

    class OfficialOk:
        base_url = "https://api.deepseek.com"
        model_name = "deepseek-v4-flash"
        key_pool = None

        def create_chat_completion(self, **kwargs):
            seen.append(("official", float(kwargs.get("timeout") or 0.0)))
            return {"ok": True}

    health = ProviderHealthRegistry(backoff_steps=(5.0,))
    chain = ChainedFallbackClient(
        [LocalFail(), OfficialOk()],
        health_registry=health,
        health_key="unit:chain",
    )

    with inference_context("chat_main", timeout_seconds=2.0):
        result = chain.create_chat_completion(timeout=30.0)

    assert result == {"ok": True}
    assert [name for name, _ in seen] == ["local", "official"]
    # Caller xin 30s nhưng parent foreground chỉ còn <=2s: không tier nào được nới thành 90s.
    assert all(0.0 < timeout <= 2.05 for _, timeout in seen)


def test_chained_fallback_full_failure_opens_shared_health_and_next_background_call_is_fast() -> None:
    from llm.client import ChainedFallbackClient
    from runtime.inference_broker import inference_context
    from runtime.provider_health import ProviderHealthRegistry, ProviderHealthState, ProviderUnavailableFast

    calls = [0]

    class Fail:
        base_url = "http://127.0.0.1:6011/v1"
        model_name = "deepseek-v4-flash"
        key_pool = None
        worker_role = "companion"

        def create_chat_completion(self, **kwargs):
            calls[0] += 1
            raise RuntimeError("provider dead")

    health = ProviderHealthRegistry(backoff_steps=(30.0,))
    chain = ChainedFallbackClient([Fail()], health_registry=health, health_key="unit:offline")

    with inference_context("chat_main", timeout_seconds=1.0):
        with pytest.raises(RuntimeError, match="provider dead"):
            chain.create_chat_completion(timeout=1.0)

    assert health.snapshot("unit:offline").state == ProviderHealthState.OFFLINE
    before = calls[0]
    with inference_context("autonomous", optional=True, timeout_seconds=1.0):
        with pytest.raises(ProviderUnavailableFast):
            chain.create_chat_completion(timeout=1.0)
    assert calls[0] == before, "offline background call phải bị chặn trước network/provider"


def test_fallback_success_after_primary_failure_keeps_chain_usable_but_degraded() -> None:
    from llm.client import ChainedFallbackClient
    from runtime.inference_broker import inference_context
    from runtime.provider_health import ProviderHealthRegistry, ProviderHealthState

    class LocalFail:
        base_url = "http://127.0.0.1:6011/v1"
        model_name = "deepseek-v4-flash"
        key_pool = None
        worker_role = "companion"
        def create_chat_completion(self, **kwargs):
            raise RuntimeError("local unavailable")

    class OfficialOk:
        base_url = "https://api.deepseek.com"
        model_name = "deepseek-v4-flash"
        key_pool = None
        def create_chat_completion(self, **kwargs):
            return {"ok": True}

    health = ProviderHealthRegistry(backoff_steps=(5.0,))
    chain = ChainedFallbackClient(
        [LocalFail(), OfficialOk()], health_registry=health, health_key="unit:degraded"
    )
    with inference_context("chat_main", timeout_seconds=2.0):
        assert chain.create_chat_completion(timeout=2.0) == {"ok": True}

    assert health.snapshot("unit:degraded").state == ProviderHealthState.DEGRADED
    # DEGRADED vẫn dùng được; chỉ OFFLINE mới fail-fast.
    assert health.acquire("unit:degraded", lane="autonomous").allowed is True


def test_chat_orchestrator_installs_one_foreground_deadline_for_whole_turn(monkeypatch) -> None:
    from types import SimpleNamespace
    from runtime.ai_orchestrated.orchestrator import AIChatOrchestrator
    from runtime.inference_broker import current_inference_context

    monkeypatch.setenv("ERIS_FOREGROUND_HARD_DEADLINE_SEC", "7.0")
    seen = {}
    host = SimpleNamespace(last_semantic_frame={})
    orchestrator = AIChatOrchestrator(host)

    def fake_run_unscoped(*args, **kwargs):
        ctx = current_inference_context()
        seen["lane"] = ctx.lane
        seen["deadline_at"] = ctx.deadline_at
        seen["cancel_scope"] = ctx.cancel_scope
        return None

    monkeypatch.setattr(orchestrator, "_run_unscoped", fake_run_unscoped)
    started = time.time()
    orchestrator.run("xin chào", cycle_start=started)

    assert seen["lane"] == "chat_main"
    assert seen["deadline_at"] is not None
    assert started + 6.5 <= float(seen["deadline_at"]) <= started + 7.1
    assert str(seen["cancel_scope"]).startswith("user_turn:")


def test_foreground_hard_deadline_accepts_ninety_seconds(monkeypatch) -> None:
    from runtime.ai_orchestrated.orchestrator import _foreground_hard_deadline_seconds

    monkeypatch.setenv("ERIS_FOREGROUND_HARD_DEADLINE_SEC", "90")

    assert _foreground_hard_deadline_seconds() == 90.0


def test_broker_policy_rejection_does_not_poison_provider_health() -> None:
    from llm.client import ChainedFallbackClient
    from runtime.inference_broker import InferenceRejected, inference_context
    from runtime.provider_health import ProviderHealthRegistry, ProviderHealthState

    class PolicyRejected:
        base_url = "http://127.0.0.1:6011/v1"
        model_name = "deepseek-v4-flash"
        key_pool = None
        worker_role = "companion"
        def create_chat_completion(self, **kwargs):
            raise InferenceRejected("shed_optional_user_active")

    health = ProviderHealthRegistry(backoff_steps=(30.0,))
    chain = ChainedFallbackClient(
        [PolicyRejected()], health_registry=health, health_key="unit:policy-reject"
    )
    with inference_context("autonomous", optional=True, timeout_seconds=1.0):
        with pytest.raises(InferenceRejected):
            chain.create_chat_completion(timeout=1.0)

    snapshot = health.snapshot("unit:policy-reject")
    assert snapshot.state == ProviderHealthState.HEALTHY
    assert snapshot.total_failures == 0


def test_default_provider_health_key_is_scoped_by_worker_role() -> None:
    from llm.client import ChainedFallbackClient

    class Companion:
        base_url = "http://127.0.0.1:6011/v1"
        model_name = "deepseek-v4-flash"
        key_pool = None
        worker_role = "companion"
        def create_chat_completion(self, **kwargs):
            return {"ok": True}

    chain = ChainedFallbackClient([Companion()])
    assert chain._provider_health_key == "remote_llm:companion"
