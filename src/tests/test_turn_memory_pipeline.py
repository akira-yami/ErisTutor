from __future__ import annotations

from types import SimpleNamespace

from memory.memory_cognition import MemoryCognitionCore
from memory.memory_kernel import MemoryKernel
from memory.memory_kernel_contracts import MemoryIngestPlan
from memory.living_user import LlmServiceUserProfileAdapter
from runtime.background_memory_digest_kernel import (
    BackgroundMemoryDigestKernelConfig,
    BackgroundMemoryDigestKernelService,
)
from runtime.inference_broker import current_inference_context
from runtime.turn_memory_pipeline import TurnMemoryPipeline, TurnMemoryPipelineConfig


def test_plan_ingest_is_side_effect_free_and_fast_never_observes_profile(tmp_path):
    core = MemoryCognitionCore(str(tmp_path / "core"))
    kernel = MemoryKernel.from_existing_core(core)
    model_calls = []

    class Adapter:
        def propose_profile_operations(self, payload):
            model_calls.append(payload)
            return {"operations": []}

        def summarize_episode(self, payload):
            return None

    core.living_user.bind_model_adapter(Adapter())

    original_commit = core.commit
    commit_calls = []

    def counting_commit(request, *, frame=None):
        commit_calls.append(request)
        return original_commit(request, frame=frame)

    core.commit = counting_commit

    plan = kernel.plan_ingest("Tên tôi là Minh", turn_id="turn_1", mode="fast")
    assert plan.input_text == "Tên tôi là Minh"
    assert commit_calls == []
    assert model_calls == []

    kernel.ingest("Tên tôi là Minh", turn_id="turn_1", mode="fast")
    assert model_calls == []  # fast must never invoke living-user model work


class _FakeSemanticDigest:
    enabled = True

    def __init__(self, events):
        self.events = events
        self.enqueued = []
        self.stats = SimpleNamespace(to_dict=lambda: {})

    def start(self):
        self.events.append("semantic.start")

    def stop(self, timeout=1.5):
        self.events.append("semantic.stop")

    def notify_user_input(self):
        self.events.append("semantic.notify_user_input")

    def notify_remote_inflight(self, value):
        self.events.append(f"semantic.remote:{int(bool(value))}")

    def enqueue(self, text, **kwargs):
        self.events.append("semantic.enqueue")
        self.enqueued.append((text, kwargs))
        return True


class _FakeLivingUser:
    class Config:
        auto_observe_turns = True

    config = Config()

    def __init__(self, events):
        self.events = events
        self.proposed = []
        self.applied = []

    def propose_turn_operations(self, text, *, evidence_ids, context=None):
        self.events.append("curator.propose")
        self.proposed.append((text, tuple(evidence_ids), dict(context or {})))
        return (SimpleNamespace(path="identity.name"),)

    def apply_turn_operations(self, operations, *, trace_context=None):
        self.events.append("reducer.profile_apply")
        self.applied.append((tuple(operations), dict(trace_context or {})))
        return SimpleNamespace(applied=1, rejected=0, operations=tuple(operations))


class _FakeKernel:
    def __init__(self, events):
        self.events = events

    def plan_ingest(self, text, **kwargs):
        self.events.append("kernel.plan")
        return MemoryIngestPlan(
            mode="fast",
            source="chat_turn",
            input_text=text,
            evidence_ids=(kwargs.get("turn_id") or "",),
            decisions=(object(),),
            commit_requests=({"target": "semantic_memory"},),
            context=dict(kwargs.get("context") or {}),
        )


def test_turn_memory_pipeline_preempts_then_plans_then_fans_out_without_commit():
    events = []
    semantic = _FakeSemanticDigest(events)
    living = _FakeLivingUser(events)
    kernel = _FakeKernel(events)
    pipeline = TurnMemoryPipeline(
        kernel,
        semantic_digest=semantic,
        living_user=living,
        config=TurnMemoryPipelineConfig(enabled=True, curator_enabled=True, curator_max_queue=8),
    )

    receipt = pipeline.enqueue_user_turn("hello", turn_id="t1")
    assert receipt.accepted
    assert receipt.semantic_enqueued
    assert receipt.curator_enqueued
    assert events[:3] == ["semantic.notify_user_input", "kernel.plan", "semantic.enqueue"]
    assert semantic.enqueued[0][1]["priority"] == "high"

    pipeline.drain_once_for_tests()
    assert living.proposed[0][0] == "hello"
    assert living.proposed[0][1] == ("t1",)
    assert living.proposed[0][2]["background_lane"] == "memory_curator"
    assert living.applied[0][1]["turn_memory_envelope_id"] == receipt.envelope_id
    assert events.index("curator.propose") < events.index("reducer.profile_apply")


def test_digest_priority_queue_runs_high_before_low():
    processed = []

    class Kernel:
        def plan_ingest(self, text, **kwargs):
            processed.append(("plan", text))
            return MemoryIngestPlan(
                mode="semantic_digest",
                source=str(kwargs.get("source", "chat_turn")),
                input_text=text,
            )

        def commit_plan(self, plan, **kwargs):
            return SimpleNamespace(committed_count=0, to_dict=lambda: {"text": plan.input_text})

        def maintain(self, **kwargs):
            return {}

    config = BackgroundMemoryDigestKernelConfig(
        enabled=True,
        max_queue_size=8,
        worker_idle_sleep=0.01,
        skip_if_older_seconds=60.0,
        pause_on_user_input=False,
        user_input_pause_seconds=0.0,
        idle_only=False,
        idle_quiet_seconds=0.0,
        avoid_remote_inflight=False,
        only_when_remote_inflight=False,
        requeue_delay_seconds=0.01,
        max_defer_count=3,
        storage_permission=True,
        maintenance_enabled=False,
        maintenance_interval_seconds=999.0,
        maintenance_budget="small",
        maintenance_destructive=False,
    )
    service = BackgroundMemoryDigestKernelService(Kernel(), config=config, observe_living_user=False)
    service.enqueue("low", turn_id="l", priority="low")
    service.enqueue("high", turn_id="h", priority="high")
    service.enqueue("normal", turn_id="n", priority="normal")
    service.drain_once_for_tests()

    assert [text for kind, text in processed if kind == "plan"] == ["high", "normal", "low"]


def test_living_user_adapter_uses_curator_lane_and_credential_pool():
    seen = {}

    class Client:
        def create_chat_completion(self, **kwargs):
            ctx = current_inference_context()
            seen.update({
                "lane": ctx.lane,
                "optional": ctx.optional,
                "project": ctx.project,
                "credential_pool": ctx.credential_pool,
            })
            return {"choices": [{"message": {"content": '{"operations":[]}'}}]}

    class Service:
        client = None

        @staticmethod
        def _extract_content(response):
            return response["choices"][0]["message"]["content"]

    adapter = LlmServiceUserProfileAdapter(
        Service(),
        client=Client(),
        fallback_to_service_client=False,
        inference_lane="memory_curator",
        inference_optional=False,
        project="memory_curator",
        credential_pool="curator",
    )
    result = adapter.propose_profile_operations({"user_text": "x"})
    assert result == {"operations": []}
    assert seen == {
        "lane": "memory_curator",
        "optional": False,
        "project": "memory_curator",
        "credential_pool": "curator",
    }


def test_curator_credential_pool_isolated_in_transport_pressure_domain():
    from llm.client import _pressure_provider
    from llm.token_budget import budget_for_provider
    from runtime.inference_broker import inference_context

    assert _pressure_provider("https://api.deepseek.com/v1", "deepseek-chat") == "deepseek"
    with inference_context("memory_curator", credential_pool="curator", project="memory_curator", optional=False):
        scoped = _pressure_provider("https://api.deepseek.com/v1", "deepseek-chat")
    assert scoped == "deepseek__curator"
    assert budget_for_provider(scoped) == budget_for_provider("deepseek")


def test_state_commit_hot_path_uses_pipeline_without_sync_kernel_commit():
    from runtime.state_commit_service import StateCommitServiceMixin

    class Logger:
        def warning(self, *args, **kwargs):
            pass

    from runtime.turn_memory_pipeline import TurnMemoryReceipt

    class Pipeline:
        enabled = True

        def __init__(self):
            self.calls = []

        def enqueue_user_turn(self, text, **kwargs):
            self.calls.append((text, kwargs))
            return TurnMemoryReceipt(True, envelope_id="e1", semantic_enqueued=True)

    class Host(StateCommitServiceMixin):
        logger = Logger()
        memory_kernel = SimpleNamespace(ingest=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sync ingest called")))

        def __init__(self):
            self.turn_memory_pipeline = Pipeline()
            self.background_memory_digest = None

    host = Host()
    assert host._ingest_user_turn_memory_hot_path("hello", turn_id="t1") is True
    assert host.turn_memory_pipeline.calls[0][0] == "hello"
    assert host.turn_memory_pipeline.calls[0][1]["turn_id"] == "t1"


def test_semantic_worker_submits_plan_to_single_reducer_before_commit():
    events = []

    class Kernel:
        def plan_ingest(self, text, **kwargs):
            events.append("semantic.plan")
            return MemoryIngestPlan(
                mode="semantic_digest",
                source="chat_turn",
                input_text=text,
                evidence_ids=("t1",),
                context=dict(kwargs.get("context") or {}),
            )

        def commit_plan(self, plan, **kwargs):
            events.append("reducer.semantic_commit")
            assert kwargs == {
                "observe_living_user": False,
                "sync_living_user_semantic": True,
            }
            return SimpleNamespace(committed_count=0, to_dict=lambda: {"committed_count": 0})

        def maintain(self, **kwargs):
            return {}

    config = BackgroundMemoryDigestKernelConfig(
        enabled=True, max_queue_size=8, worker_idle_sleep=0.01,
        skip_if_older_seconds=60.0, pause_on_user_input=False,
        user_input_pause_seconds=0.0, idle_only=False, idle_quiet_seconds=0.0,
        avoid_remote_inflight=False, only_when_remote_inflight=False,
        requeue_delay_seconds=0.01, max_defer_count=3, storage_permission=True,
        maintenance_enabled=False, maintenance_interval_seconds=999.0,
        maintenance_budget="small", maintenance_destructive=False,
    )
    kernel = Kernel()
    semantic = BackgroundMemoryDigestKernelService(kernel, config=config, observe_living_user=False)
    pipeline = TurnMemoryPipeline(
        kernel, semantic_digest=semantic, living_user=None,
        config=TurnMemoryPipelineConfig(enabled=True, curator_enabled=False),
    )
    semantic.enqueue("hello", turn_id="t1", priority="high", context={"turn_memory_envelope_id": "e1"})
    semantic.drain_once_for_tests()
    assert events == ["semantic.plan"]
    assert pipeline.snapshot()["mutation_queue_depth"] == 1

    pipeline.drain_mutations_for_tests()
    assert events == ["semantic.plan", "reducer.semantic_commit"]
    assert pipeline.snapshot()["mutation_owner"] == "turn_memory_reducer"
