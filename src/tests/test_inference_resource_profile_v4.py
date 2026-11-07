from __future__ import annotations

import time

from llm.deepseek_workers import JsonlWorkerClient
from runtime.ai_orchestrated.model_router import AIRuntimeModelRouter
from runtime.model_budget_policy import ModelBudgetPolicy, ModelBudgetPolicyConfig, ResourceSnapshot


class _RawDeepSeekClient:
    base_url = "http://127.0.0.1:6011/v1"
    model_name = "deepseek-v4-flash"
    configured_model_name = "deepseek-v4-flash"
    key_pool = None

    def create_chat_completion(self, **kwargs):
        return {"choices": [{"message": {"content": "{}"}}]}


def _busy_gpu_snapshot() -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_percent=10.0,
        ram_percent=20.0,
        gpu_util_percent=99.0,
        vram_used_mb=3900.0,
        vram_total_mb=4096.0,
        gpu_temperature_c=60.0,
    )


def _policy() -> ModelBudgetPolicy:
    cfg = ModelBudgetPolicyConfig(
        enabled=True,
        queue_enabled=False,
        resource_guard_enabled=True,
        remote_api_enabled=True,
        gpu_block_percent=70.0,
        vram_block_percent=72.0,
        four_b_cooldown_seconds=60.0,
    )
    return ModelBudgetPolicy(config=cfg)


def test_ds2api_worker_declares_remote_resource_profile():
    """Localhost gateway != local model.

    Day la regression quan trong nhat cua V4: DS2API chay o 127.0.0.1 nhung
    DeepSeek inference that la remote, nen scheduler khong duoc xem no la local 4B.
    """
    worker = JsonlWorkerClient(_RawDeepSeekClient(), role="executive")

    assert worker.inference_backend == "ds2api_deepseek_web"
    assert worker.inference_resource_class == "remote_api"

    resource_class, backend = AIRuntimeModelRouter._client_inference_profile(worker)
    assert resource_class == "remote_api"
    assert backend == "ds2api_deepseek_web"


def test_remote_api_subconscious_legacy_tier_does_not_use_local_4b_vram_guard():
    policy = _policy()
    policy.last_4b_started_at = time.time()

    remote = policy.can_run(
        "subconscious_4b",
        resource_snapshot=_busy_gpu_snapshot(),
        reason="eris_agent_turn_executive",
        resource_class="remote_api",
        backend="ds2api_deepseek_web",
        cognitive_lane="turn_executive",
    )
    local = policy.can_run(
        "subconscious_4b",
        resource_snapshot=_busy_gpu_snapshot(),
        reason="legacy_local_worker",
        resource_class="local_heavy",
        backend="local_gguf",
        cognitive_lane="background_reasoning",
    )

    assert remote.allowed is True
    assert remote.cooldown_seconds == 0.0
    assert remote.resource_class == "remote_api"
    assert remote.debug["uses_remote_api"] is True
    assert remote.debug["heavy_tier"] is False

    # Cung mot legacy tier, nhung neu resource_class la local thi van phai bi
    # guard/cooldown nhu truoc. Nhu vay V4 sua nham nghia, khong pha bao ve VRAM.
    assert local.allowed is False
    assert local.resource_class == "local_heavy"


def test_remote_api_request_does_not_dirty_local_4b_cooldown_markers():
    policy = _policy()
    policy.last_4b_started_at = 123.0
    policy.last_4b_finished_at = 456.0

    result = policy.run_queued(
        "subconscious_4b",
        lambda: "ok",
        resource_snapshot=ResourceSnapshot(cpu_percent=1.0, ram_percent=1.0),
        resource_class="remote_api",
        backend="ds2api_deepseek_web",
        cognitive_lane="turn_executive",
    )

    assert result == "ok"
    assert policy.last_4b_started_at == 123.0
    assert policy.last_4b_finished_at == 456.0


def test_remote_api_lease_reserves_zero_local_vram():
    policy = _policy()

    lease = policy.acquire_lease(
        "subconscious_4b",
        resource_snapshot=ResourceSnapshot(cpu_percent=1.0, ram_percent=1.0),
        resource_class="remote_api",
        backend="ds2api_deepseek_web",
        cognitive_lane="tutor:assessment",
    )
    try:
        state = policy.queue_state()
        assert state["active"][0]["resource_class"] == "remote_api"
        assert state["active"][0]["backend"] == "ds2api_deepseek_web"
        assert policy._reserved_vram_for_active_leases_locked() == 0.0
    finally:
        policy.release_lease(lease)


def test_legacy_subconscious_without_explicit_profile_keeps_old_local_semantics():
    """Caller cu chua migrate van phai duoc bao ve y het truoc V4."""
    policy = _policy()
    decision = policy.can_run(
        "subconscious_4b",
        resource_snapshot=_busy_gpu_snapshot(),
        reason="legacy_call",
    )

    assert decision.resource_class == "local_heavy"
    assert decision.debug["uses_remote_api"] is False
    assert decision.allowed is False


def test_root_agent_prefers_dedicated_executive_worker():
    from runtime.conversation import ConversationEvent, RuntimeModelRootAgent

    executive = object()
    critic = object()

    class _Router:
        executive_client = executive
        deep_client = critic
        seed_client = None
        surface_client = None
        llm_service = None

        def __init__(self):
            self.seen_client = None
            self.seen_kwargs = None

        def invoke_structured_worker(self, client, **kwargs):
            self.seen_client = client
            self.seen_kwargs = dict(kwargs)
            return {"kind": "no_reply", "reason": "test"}

    router = _Router()
    reasoner = RuntimeModelRootAgent(router)
    reasoner.decide(
        event=ConversationEvent.user_turn(session_id="s", text="phan tich kien truc nay"),
        recent_history=(),
        note="",
        runtime_context={},
    )

    assert router.seen_client is executive
    assert router.seen_kwargs["cognitive_lane"] == "root_conversation"
    assert router.seen_kwargs["inference_tier"] == "subconscious_4b"


def test_root_agent_has_no_legacy_worker_timeout_setting():
    import inspect
    from runtime.conversation import RuntimeModelRootAgent

    assert "ERIS_AGENT_TURN_WORKER_TIMEOUT_SEC" not in inspect.getsource(RuntimeModelRootAgent)


def test_turn_executive_cognitive_lane_is_required_foreground_broker_lane(monkeypatch):
    """Không để legacy tier kéo root executive xuống post_observer optional."""
    from contextlib import contextmanager
    import runtime.inference_broker as broker

    seen = {}

    @contextmanager
    def fake_context(lane, **kwargs):
        seen["lane"] = lane
        seen["kwargs"] = kwargs
        yield

    monkeypatch.setattr(broker, "inference_context", fake_context)

    class _Budget:
        def run_queued(self, tier, func, **kwargs):
            seen["tier"] = tier
            seen["budget_kwargs"] = kwargs
            return func()

    router = AIRuntimeModelRouter(object(), budget_policy=_Budget())
    out = router._run_inference(
        "subconscious_4b",
        lambda: {"ok": True},
        reason="eris_agent_turn_executive",
        priority=68,
        resource_class="remote_api",
        backend="ds2api_deepseek_web",
        cognitive_lane="turn_executive",
    )

    assert out == {"ok": True}
    assert seen["lane"] == "chat_tool_required"
    assert seen["kwargs"]["optional"] is False
    assert seen["budget_kwargs"]["resource_class"] == "remote_api"
    assert seen["budget_kwargs"]["backend"] == "ds2api_deepseek_web"
    assert seen["budget_kwargs"]["cognitive_lane"] == "turn_executive"


def test_character_claim_verifier_is_required_foreground_broker_lane(monkeypatch):
    """Verifier bắt buộc KHÔNG được rơi vào post_observer optional.

    Trước hotfix cognitive_lane=public_claim_verifier + tier=subconscious_4b
    bị xếp vào post_observer. Chat có thể loại job này, khiến Eris im lặng.
    """
    from contextlib import contextmanager
    import runtime.inference_broker as broker

    seen = {}

    @contextmanager
    def fake_context(lane, **kwargs):
        seen['lane'] = lane
        seen['optional'] = kwargs['optional']
        yield

    monkeypatch.setattr(broker, 'inference_context', fake_context)
    router = AIRuntimeModelRouter(object())
    out = router._run_inference(
        'subconscious_4b', lambda: {'ok': True},
        reason='eris_character_claim_verifier_v1', priority=35,
        resource_class='remote_api', backend='ds2api_deepseek_web',
        cognitive_lane='public_claim_verifier',
    )
    assert out == {'ok': True}
    assert seen == {'lane': 'public_claim_verifier', 'optional': False}


def test_regular_observer_remains_optional_background_lane(monkeypatch):
    """Hotfix không đổi toàn bộ observer thành request foreground."""
    from contextlib import contextmanager
    import runtime.inference_broker as broker

    seen = {}

    @contextmanager
    def fake_context(lane, **kwargs):
        seen.update(lane=lane, optional=kwargs['optional'])
        yield

    monkeypatch.setattr(broker, 'inference_context', fake_context)
    router = AIRuntimeModelRouter(object())
    router._run_inference(
        'subconscious_4b', lambda: None,
        reason='eris_post_observer', priority=35,
        cognitive_lane='post_observer',
    )
    assert seen == {'lane': 'post_observer', 'optional': True}
