# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

from runtime.conversation import (
    ConversationAfter,
    ConversationEvent,
    ConversationKernel,
    ContinuationLeaseScheduler,
    NextConversationMove,
    NoReply,
    RenderedConversationMove,
    SurfaceEnvelope,
)


ROOT = Path(__file__).resolve().parents[2]


class _Agent:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls: list[dict] = []

    def decide(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.decisions.pop(0)


class _Writer:
    def __init__(self):
        self.calls: list[dict] = []

    def realize(self, **kwargs):
        self.calls.append(dict(kwargs))
        return RenderedConversationMove(
            surface=SurfaceEnvelope(display_text="continued"),
            decision_payload=kwargs["turn"].move.to_dict(),
        )


class _Delivery:
    def __init__(self):
        self.calls: list[dict] = []

    def deliver(self, **kwargs):
        self.calls.append(dict(kwargs))


def _due_kernel(decision):
    scheduler = ContinuationLeaseScheduler()
    agent = _Agent([decision])
    writer = _Writer()
    delivery = _Delivery()
    kernel = ConversationKernel(
        agent,
        continuation=scheduler,
        writer=writer,
        delivery=delivery,
    )
    generation = kernel.preempt_for_user("s").generation
    scheduler.schedule(
        session_id="s",
        generation=generation,
        move_id="move:source",
        after=ConversationAfter.CONTINUE_SELF,
        now=10.0,
        continue_self_delay=0.0,
    )
    return kernel, agent, writer, delivery


def test_due_lease_reenters_kernel_and_root_can_choose_no_reply():
    kernel, agent, writer, delivery = _due_kernel(NoReply(reason="thread_complete"))

    result = kernel.handle_due_continuation(session_id="s", now=11.0)

    assert result is not None and result.status == "no_reply"
    assert agent.calls[0]["event"].kind == "continuation_due"
    assert writer.calls == []
    assert delivery.calls == []
    assert kernel.continuation.peek("s") is None


def test_user_generation_beats_claimed_continuation_before_writer():
    writer = _Writer()
    delivery = _Delivery()

    class _RacingAgent:
        kernel = None

        def decide(self, **_kwargs):
            self.kernel.preempt_for_user("s")
            return NextConversationMove(intent="stale", say="must not render")

    agent = _RacingAgent()
    scheduler = ContinuationLeaseScheduler()
    kernel = ConversationKernel(agent, continuation=scheduler, writer=writer, delivery=delivery)
    agent.kernel = kernel
    generation = kernel.preempt_for_user("s").generation
    scheduler.schedule(
        session_id="s", generation=generation, move_id="source",
        after=ConversationAfter.CONTINUE_SELF, now=10.0, continue_self_delay=0.0,
    )

    result = kernel.handle_due_continuation(session_id="s", now=11.0)

    assert result is not None and result.status == "stale"
    assert writer.calls == []
    assert delivery.calls == []
    assert kernel.continuation.peek("s") is None


def test_duplicate_due_wakes_claim_only_one_lease():
    kernel, agent, _writer, _delivery = _due_kernel(NoReply(reason="done"))

    first = kernel.handle_due_continuation(session_id="s", now=11.0)
    second = kernel.handle_due_continuation(session_id="s", now=11.0)

    assert first is not None
    assert second is None
    assert len(agent.calls) == 1


def test_lease_schema_contains_no_semantic_content():
    kernel, _agent, _writer, _delivery = _due_kernel(NoReply(reason="done"))
    payload = kernel.continuation.peek("s").to_dict()

    for forbidden in (
        "topic", "objective", "say", "speech_act", "public_text",
        "teaching_chunk", "planned_thread", "next_intent",
    ):
        assert forbidden not in payload


def test_only_continuation_lease_scheduler_is_bootstrapped_for_public_continuation():
    bootstrap = (ROOT / "runtime/ai_orchestrated/bootstrap.py").read_text(encoding="utf-8")
    orchestrator = (ROOT / "runtime/ai_orchestrated/orchestrator.py").read_text(encoding="utf-8")
    signal_handler = orchestrator.split("    def _run_v12_signal(", 1)[1].split(
        "    def run_social_signal(", 1
    )[0]

    assert "SocialContinuationRuntime(" not in bootstrap
    assert "ConversationArcRuntime(" not in bootstrap
    assert "runtime.agent_loop" not in signal_handler
    assert "handle_due_continuation" in signal_handler


def test_durable_lease_survives_restart_and_expired_lease_is_dropped(tmp_path):
    """Lease được khôi phục như quyền re-evaluate, không phải replay lời cũ."""
    from runtime.ai_orchestrated.atomic_state import SQLiteAtomicStateStore

    db = tmp_path / "conversation.sqlite3"
    store_a = SQLiteAtomicStateStore(db)
    scheduler_a = ContinuationLeaseScheduler(
        atomic_store=store_a,
        lease_ttl_seconds=5.0,
    )
    lease = scheduler_a.schedule(
        session_id="s",
        generation=3,
        move_id="move:source",
        after=ConversationAfter.CONTINUE_SELF,
        now=10.0,
        continue_self_delay=0.0,
    )
    assert lease is not None

    # Mô phỏng process restart: instance mới đọc đúng lease cơ học từ SQLite.
    store_b = SQLiteAtomicStateStore(db)
    scheduler_b = ContinuationLeaseScheduler(atomic_store=store_b)
    restored = scheduler_b.peek("s")
    assert restored is not None
    assert restored.lease_id == lease.lease_id
    assert "say" not in restored.to_dict()

    claimed = scheduler_b.claim_due(session_id="s", generation=3, now=11.0)
    assert claimed is not None
    scheduler_b.consume(claimed)
    assert scheduler_b.peek("s") is None

    # Lease quá hạn sau suspend/restart phải biến mất, không thức dậy muộn.
    scheduler_a.schedule(
        session_id="expired",
        generation=4,
        move_id="move:expired",
        after=ConversationAfter.MAY_CONTINUE,
        now=20.0,
        may_continue_delay=0.0,
    )
    assert scheduler_b.claim_due(session_id="expired", generation=4, now=30.0) is None
    assert scheduler_b.peek("expired") is None


def test_continuation_runaway_breaker_is_mechanical_not_speech_policy():
    scheduler = ContinuationLeaseScheduler(
        max_consecutive_self_wakes=2,
        max_chain_seconds=60.0,
    )
    first = scheduler.schedule(
        session_id="s",
        generation=1,
        move_id="m1",
        after=ConversationAfter.CONTINUE_SELF,
        now=10.0,
        continue_self_delay=0.0,
    )
    assert first is not None and first.consecutive_self_wakes == 1
    second = scheduler.schedule(
        session_id="s",
        generation=1,
        move_id="m2",
        after=ConversationAfter.CONTINUE_SELF,
        now=11.0,
        continue_self_delay=0.0,
        parent_chain_started_at=first.chain_started_at,
        parent_self_wakes=first.consecutive_self_wakes,
    )
    assert second is not None and second.consecutive_self_wakes == 2

    blocked = scheduler.schedule(
        session_id="s",
        generation=1,
        move_id="m3",
        after=ConversationAfter.CONTINUE_SELF,
        now=12.0,
        continue_self_delay=0.0,
        parent_chain_started_at=second.chain_started_at,
        parent_self_wakes=second.consecutive_self_wakes,
    )
    assert blocked is None
    assert scheduler.peek("s") is None
