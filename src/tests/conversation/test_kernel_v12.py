# -*- coding: utf-8 -*-
from __future__ import annotations

from runtime.conversation import (
    ConversationAfter,
    ConversationEvent,
    ConversationKernel,
    DeterministicRootAgent,
    NextConversationMove,
    NoReply,
    RunPhase,
    SurfaceEnvelope,
)
from runtime.conversation.writer import RenderedConversationMove


class _Agent:
    def __init__(self, moves):
        self.moves = list(moves)
        self.calls = []

    def decide(self, **kwargs):
        self.calls.append(dict(kwargs))
        return self.moves.pop(0)


class _Writer:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.turns = []

    def realize(self, **kwargs):
        self.turns.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        turn = kwargs["turn"]
        return RenderedConversationMove(
            surface=SurfaceEnvelope(display_text=turn.move.say),
            decision_payload=turn.move.to_dict(),
            metadata={"source": "test"},
        )


class _Delivery:
    def __init__(self):
        self.deliveries = []

    def deliver(self, **kwargs):
        self.deliveries.append(dict(kwargs))


def test_user_turn_has_one_root_move_and_no_semantic_runtime_state():
    agent = _Agent([
        NextConversationMove(
            intent="bắt lại nhịp đùa",
            say="trêu lại nhẹ, không giải thích",
            delivery="hơi ngơ rồi bật cười",
            after=ConversationAfter.WAIT_USER,
        )
    ])
    kernel = ConversationKernel(agent)
    turn = kernel.begin_user_turn(session_id="s", user_text="hen hen")

    assert len(agent.calls) == 1
    assert turn.move.say == "trêu lại nhẹ, không giải thích"
    state = kernel.snapshot("s")
    assert state.phase == RunPhase.WRITING
    payload = state.to_dict()
    # Mechanical state không được tái sinh semantic scheduler V8-V11.
    for forbidden in ("planned_threads", "desired_shape", "floor", "next_intent", "public_beats"):
        assert forbidden not in payload


def test_delivery_receipt_is_idempotent_and_schedules_single_lease():
    agent = _Agent([
        NextConversationMove(
            intent="nói tiếp một nhịp",
            say="ý hiện tại",
            after=ConversationAfter.CONTINUE_SELF,
        )
    ])
    kernel = ConversationKernel(agent)
    turn = kernel.begin_user_turn(session_id="s", user_text="x")
    assert kernel.mark_delivery_started(turn, delivery_id="d1")
    assert kernel.settle_delivery(turn, delivery_id="d1", successful=True)
    assert not kernel.settle_delivery(turn, delivery_id="d1", successful=True)
    lease = kernel.continuation.peek("s")
    assert lease is not None
    assert lease.source_move_id == turn.move.move_id
    assert kernel.snapshot("s").phase == RunPhase.CONTINUATION_PENDING


def test_new_user_generation_preempts_old_continuation():
    agent = _Agent([
        NextConversationMove(intent="a", say="a", after=ConversationAfter.CONTINUE_SELF),
        NextConversationMove(intent="b", say="b", after=ConversationAfter.WAIT_USER),
    ])
    kernel = ConversationKernel(agent)
    first = kernel.begin_user_turn(session_id="s", user_text="one")
    kernel.mark_delivery_started(first, delivery_id="d1")
    kernel.settle_delivery(first, delivery_id="d1", successful=True)
    assert kernel.continuation.peek("s") is not None

    second = kernel.begin_user_turn(session_id="s", user_text="two")
    assert second.generation > first.generation
    assert kernel.continuation.peek("s") is None


def test_stale_delivery_cannot_schedule_after_user_steers():
    agent = _Agent([
        NextConversationMove(intent="a", say="a", after=ConversationAfter.CONTINUE_SELF),
        NextConversationMove(intent="b", say="b", after=ConversationAfter.WAIT_USER),
    ])
    kernel = ConversationKernel(agent)
    old = kernel.begin_user_turn(session_id="s", user_text="old")
    kernel.mark_delivery_started(old, delivery_id="old-d")
    new = kernel.begin_user_turn(session_id="s", user_text="new")

    assert not kernel.settle_delivery(old, delivery_id="old-d", successful=True)
    assert kernel.continuation.peek("s") is None
    assert kernel.snapshot("s").active_move_id == new.move.move_id


def test_crash_state_never_contains_public_text_for_replay():
    kernel = ConversationKernel(DeterministicRootAgent())
    turn = kernel.begin_user_turn(session_id="s", user_text="hello")
    kernel.mark_delivery_started(turn, delivery_id="d")
    state = kernel.snapshot("s").to_dict()
    assert "public_text" not in state
    assert "last_public_text" not in state
    assert "speech_text" not in state


def test_no_reply_never_calls_writer_or_delivery():
    agent = _Agent([NoReply(reason="nothing_worth_saying")])
    writer = _Writer()
    delivery = _Delivery()
    kernel = ConversationKernel(agent, writer=writer, delivery=delivery)

    result = kernel.handle_event(
        ConversationEvent(event_id="idle:1", session_id="s", kind="autonomous_signal")
    )

    assert isinstance(result.decision, NoReply)
    assert result.status == "no_reply"
    assert writer.turns == []
    assert delivery.deliveries == []
    assert kernel.snapshot("s").phase == RunPhase.WAITING_USER


def test_positive_move_calls_writer_and_delivery_once():
    move = NextConversationMove(intent="answer", say="the answer")
    agent = _Agent([move])
    writer = _Writer()
    delivery = _Delivery()
    kernel = ConversationKernel(agent, writer=writer, delivery=delivery)

    result = kernel.handle_event(ConversationEvent.user_turn(session_id="s", text="question"))

    assert result.status == "delivering"
    assert result.turn is not None
    assert result.rendered is not None
    assert len(writer.turns) == 1
    assert len(delivery.deliveries) == 1
    assert delivery.deliveries[0]["delivery_id"] == result.delivery_id
    assert kernel.snapshot("s").phase == RunPhase.DELIVERING


def test_writer_failure_creates_no_delivery_or_continuation():
    agent = _Agent([NextConversationMove(intent="answer", say="answer")])
    writer = _Writer(error=RuntimeError("writer unavailable"))
    delivery = _Delivery()
    kernel = ConversationKernel(agent, writer=writer, delivery=delivery)

    result = kernel.handle_event(ConversationEvent.user_turn(session_id="s", text="question"))

    assert result.status == "writer_failed"
    assert delivery.deliveries == []
    assert kernel.continuation.peek("s") is None
    assert kernel.snapshot("s").phase == RunPhase.FAILED


def test_stale_event_cannot_reach_writer():
    agent = _Agent([
        NextConversationMove(intent="first", say="first"),
        NextConversationMove(intent="newest", say="newest"),
    ])
    writer = _Writer()
    delivery = _Delivery()
    kernel = ConversationKernel(agent, writer=writer, delivery=delivery)
    kernel.begin_user_turn(session_id="s", user_text="first")
    kernel.begin_user_turn(session_id="s", user_text="newest")
    current = kernel.snapshot("s").generation
    calls_before = len(agent.calls)

    result = kernel.handle_event(
        ConversationEvent(
            event_id="old:1",
            session_id="s",
            kind="autonomous_signal",
            generation=current - 1,
        )
    )

    assert result.status == "stale"
    assert result.decision is None
    assert len(agent.calls) == calls_before
    assert writer.turns == []
    assert delivery.deliveries == []


def test_durable_session_note_and_delivery_receipt_survive_restart(tmp_path):
    from runtime.ai_orchestrated.atomic_state import SQLiteAtomicStateStore
    from runtime.conversation import ConversationSessionStore, DeliveryReceipt, DeliveryReceiptLedger

    db = tmp_path / "conversation.sqlite3"
    atomic_a = SQLiteAtomicStateStore(db)
    sessions_a = ConversationSessionStore(atomic_store=atomic_a)
    sessions_a.set_note("s", "ghi chú plain text qua restart")
    receipt_a = DeliveryReceiptLedger(atomic_store=atomic_a, owner_id="worker-a")
    receipt = DeliveryReceipt(
        delivery_id="delivery:durable",
        session_id="s",
        generation=1,
        move_id="move:1",
        successful=True,
    )
    assert receipt_a.commit_once(receipt)

    atomic_b = SQLiteAtomicStateStore(db)
    sessions_b = ConversationSessionStore(atomic_store=atomic_b)
    receipt_b = DeliveryReceiptLedger(atomic_store=atomic_b, owner_id="worker-b")
    assert sessions_b.load("s").note == "ghi chú plain text qua restart"
    assert not receipt_b.commit_once(receipt)


def test_restart_abandons_transient_run_without_replaying_public_text(tmp_path):
    from dataclasses import replace

    from runtime.ai_orchestrated.atomic_state import SQLiteAtomicStateStore
    from runtime.conversation import ConversationSessionStore, RunPhase

    db = tmp_path / "conversation.sqlite3"
    atomic_a = SQLiteAtomicStateStore(db)
    store_a = ConversationSessionStore(atomic_store=atomic_a)
    store_a.mutate(
        "s",
        lambda prior, _now: replace(
            prior,
            generation=7,
            active_run_id="run:old",
            active_move_id="move:old",
            active_delivery_id="delivery:old",
            phase=RunPhase.DELIVERING,
        ),
    )

    # Process mới: first load phải coi run cũ là abandoned. Không có public text
    # nào trong state để phát lại, và generation vẫn giữ nguyên để stale callback
    # cũ tiếp tục bị loại.
    atomic_b = SQLiteAtomicStateStore(db)
    store_b = ConversationSessionStore(atomic_store=atomic_b)
    recovered = store_b.load("s")

    assert recovered.generation == 7
    assert recovered.phase == RunPhase.WAITING_USER
    assert recovered.active_run_id == ""
    assert recovered.active_move_id == ""
    assert recovered.active_delivery_id == ""
    payload = recovered.to_dict()
    assert "public_text" not in payload
    assert "speech_text" not in payload
