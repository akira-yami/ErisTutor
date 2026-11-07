# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from types import SimpleNamespace

from domain import Decision
from runtime.conversation import (
    ConversationAfter,
    ConversationEvent,
    ConversationKernel,
    KernelTurn,
    NextConversationMove,
    RenderedConversationMove,
    RunPhase,
    SurfaceEnvelope,
)
from runtime.conversation.bootstrap import ensure_conversation_kernel
from runtime.conversation.delivery_adapter import RuntimeConversationDeliveryAdapter
from runtime.session_turn_ledger import SessionTurnLedger
from runtime.semantic_frame import AssistantAct, SemanticFrame
from runtime.ai_orchestrated.orchestrator import AIChatOrchestrator


class _Parser:
    def parse(self, raw: str, **_kwargs):
        payload = json.loads(raw)
        return Decision(
            text=str(payload.get("text", "")),
            animation="stand",
            should_speak=True,
            speech_text=str(payload.get("speech_text", "")),
        )


class _Host:
    def __init__(self) -> None:
        self.parser = _Parser()
        self.chat_history: list[dict] = []
        self.sent: list[tuple[Decision, str]] = []
        self.position_updates: list[Decision] = []
        self.ledger = SessionTurnLedger()
        self.logger = SimpleNamespace(debug=lambda *_args, **_kwargs: None)
        self.ai_stage_planner = SimpleNamespace(apply=lambda *_args, **_kwargs: None)
        self.last_text = ""
        self.last_chat_answer = ""

    def _ensure_session_turn_ledger(self):
        return self.ledger

    def _append_chat_history(self, role: str, text: str, **_kwargs) -> None:
        self.chat_history.append({"role": role, "content": text})

    def _apply_position_update(self, decision: Decision) -> None:
        self.position_updates.append(decision)

    def _send_decision_to_body(self, decision: Decision, *, channel: str | None = None) -> None:
        self.sent.append((decision, str(channel or "")))


def _turn(*, after: ConversationAfter = ConversationAfter.WAIT_USER) -> KernelTurn:
    return KernelTurn(
        session_id="s",
        generation=1,
        run_id="run:1",
        move=NextConversationMove(
            intent="answer",
            say="answer from evidence",
            delivery="calm",
            after=after,
            move_id="move:1",
        ),
        note="",
    )


def _rendered(text: str = "Public answer") -> RenderedConversationMove:
    return RenderedConversationMove(
        surface=SurfaceEnvelope(display_text=text, speech_text=text, language="vi"),
        decision_payload=_turn().move.to_dict(),
        metadata={
            "event_kind": "user_turn",
            "user_text": "question",
            "provider_payload": {"text": text, "speech_text": text},
        },
    )


def test_delivery_adapter_commits_assistant_and_transport_once():
    host = _Host()
    adapter = RuntimeConversationDeliveryAdapter(host)

    adapter.deliver(turn=_turn(), rendered=_rendered(), delivery_id="delivery:1")
    adapter.deliver(turn=_turn(), rendered=_rendered(), delivery_id="delivery:1")

    assert host.chat_history == [{"role": "assistant", "content": "Public answer"}]
    assert [turn.text for turn in host.ledger.assistant_turns()] == ["Public answer"]
    assert len(host.sent) == 1
    decision, channel = host.sent[0]
    assert decision.utterance_turn_id == "delivery:1"
    assert channel == "chat"


def test_interaction_choice_is_recorded_only_after_delivered_receipt():
    from dataclasses import replace
    from runtime.ai_orchestrated.state_reducer import StateReducer
    from runtime.conversation.speech_outcome import note_public_delivery_outcome
    host = _Host()
    reducer = StateReducer()
    host.ai_state_reducer = reducer
    choice = {"situation": "conversation", "strategy": "dry_comment", "context": "banter",
              "expected_effect": "Share a joke", "listener_action": ""}
    turn = _turn()
    turn = replace(turn, move=replace(turn.move, interaction_choice=choice))
    adapter = RuntimeConversationDeliveryAdapter(host)
    try:
        adapter.deliver(turn=turn, rendered=_rendered(), delivery_id="delivery:choice")
        assert reducer.wait_idle()
        assert not reducer.snapshot().identity.pending_strategies
        note_public_delivery_outcome(host, delivery_id="delivery:choice", status="delivered")
        assert reducer.wait_idle()
        assert reducer.snapshot().identity.pending_strategies[0].strategy == "dry_comment"
        assert reducer.snapshot().identity.pending_strategies[0].assistant_text == "Public answer"
    finally:
        reducer.close()


def test_cancelled_choice_never_becomes_learning_experience():
    from dataclasses import replace
    from runtime.ai_orchestrated.state_reducer import StateReducer
    from runtime.conversation.speech_outcome import note_public_delivery_outcome
    host = _Host()
    reducer = StateReducer()
    host.ai_state_reducer = reducer
    turn = _turn()
    turn = replace(turn, move=replace(turn.move, interaction_choice={
        "situation": "conversation", "strategy": "dry_comment", "context": "banter",
        "expected_effect": "Share a joke", "listener_action": ""}))
    try:
        RuntimeConversationDeliveryAdapter(host).deliver(turn=turn, rendered=_rendered(), delivery_id="delivery:cancelled-choice")
        note_public_delivery_outcome(host, delivery_id="delivery:cancelled-choice", status="cancelled")
        assert reducer.wait_idle()
        assert not reducer.snapshot().identity.pending_strategies
    finally:
        reducer.close()


def test_delivery_adapter_converts_typed_provider_payload_before_json_boundary():
    """Regression: typed semantic metadata không được làm chết delivery V12."""

    host = _Host()
    adapter = RuntimeConversationDeliveryAdapter(host)
    frame = SemanticFrame(user_act="confirm")
    act = AssistantAct()
    rendered = RenderedConversationMove(
        surface=SurfaceEnvelope(display_text="Có.", speech_text="はい。", language="vi"),
        decision_payload=_turn().move.to_dict(),
        metadata={
            "event_kind": "user_turn",
            "user_text": "thật hả",
            "provider_payload": {
                "text": "Có.",
                "speech_text": "はい。",
                "semantic_frame": frame,
                "assistant_act": act,
                "_typed_semantic_frame": frame,
                "_typed_assistant_act": act,
            },
        },
    )

    adapter.deliver(turn=_turn(), rendered=rendered, delivery_id="delivery:typed")

    assert len(host.sent) == 1
    decision, channel = host.sent[0]
    assert decision.text == "Có."
    assert channel == "chat"


def test_no_tts_receipt_before_send_returns_settles_once():
    host = _Host()

    class _Agent:
        def decide(self, **_kwargs):
            return NextConversationMove(
                intent="continue",
                say="first part",
                after=ConversationAfter.CONTINUE_SELF,
            )

    class _Writer:
        def realize(self, **_kwargs):
            return _rendered("First part")

    adapter = RuntimeConversationDeliveryAdapter(host)
    kernel = ConversationKernel(_Agent(), writer=_Writer(), delivery=adapter)
    host.conversation_kernel = kernel
    receipts: list[bool] = []

    def send_and_finish(decision: Decision, *, channel: str | None = None) -> None:
        host.sent.append((decision, str(channel or "")))
        receipts.append(kernel.settle_delivery_id(
            session_id="s",
            delivery_id=decision.utterance_turn_id,
            successful=True,
        ))

    host._send_decision_to_body = send_and_finish
    result = kernel.handle_event(ConversationEvent.user_turn(session_id="s", text="go"))

    assert receipts == [True]
    assert kernel.snapshot("s").phase == RunPhase.CONTINUATION_PENDING
    assert kernel.continuation.peek("s") is not None
    assert kernel.settle_delivery_id(
        session_id="s", delivery_id=result.delivery_id, successful=True,
    ) is False


def test_duplicate_audio_finished_receipt_is_ignored():
    host = _Host()
    adapter = RuntimeConversationDeliveryAdapter(host)

    class _Agent:
        def decide(self, **_kwargs):
            return NextConversationMove(intent="answer", say="answer")

    class _Writer:
        def realize(self, **_kwargs):
            return _rendered()

    kernel = ConversationKernel(_Agent(), writer=_Writer(), delivery=adapter)
    result = kernel.handle_event(ConversationEvent.user_turn(session_id="s", text="question"))

    assert kernel.settle_delivery_id(session_id="s", delivery_id=result.delivery_id, successful=True)
    assert not kernel.settle_delivery_id(session_id="s", delivery_id=result.delivery_id, successful=True)
    assert len(kernel.receipts._seen) == 1


def test_bootstrap_reuses_one_conversation_kernel():
    host = _Host()
    host.ai_model_router = SimpleNamespace()

    first = ensure_conversation_kernel(host)
    second = ensure_conversation_kernel(host)

    assert first is second
    assert host.conversation_kernel is first


def test_public_chat_entry_uses_v12_kernel_path():
    calls: list[tuple[str, float]] = []
    host = SimpleNamespace(
        last_semantic_frame={},
        logger=SimpleNamespace(debug=lambda *_args, **_kwargs: None),
        _preempt_background_for_chat=lambda _reason: None,
    )
    orchestrator = AIChatOrchestrator(host)
    orchestrator._run_v12 = lambda user_text, **kwargs: calls.append(
        (user_text, float(kwargs["cycle_start"]))
    )
    orchestrator._run_unscoped = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("legacy foreground path called")
    )

    orchestrator.run("hello", cycle_start=123.0)

    assert calls == [("hello", 123.0)]


def test_suppressed_transport_does_not_create_false_public_commit():
    """Regression V12: stage suppression không được ghi như Eris đã nói."""
    host = _Host()
    host._send_decision_to_body = lambda _decision, channel=None: False
    adapter = RuntimeConversationDeliveryAdapter(host)

    try:
        adapter.deliver(turn=_turn(), rendered=_rendered("Không được phát"), delivery_id="delivery:suppressed")
    except RuntimeError as exc:
        assert str(exc) == "public_transport_suppressed"
    else:
        raise AssertionError("suppressed transport must fail delivery")

    assert host.chat_history == []
    assert host.ledger.assistant_turns() == []
    assert host.last_text == ""
    assert host.last_chat_answer == ""


def test_tutor_receipt_binding_uses_committed_move_and_verified_bundle():
    """Adapter phải chuyển đúng identity của move + bundle sang Tutor.

    Không lấy identity từ nội dung do model tự sinh. Terminal receipt sau
    đó chỉ có thể được Tutor chấp nhận nếu khớp các định danh này.
    """
    from dataclasses import replace

    host = _Host()
    bound = []
    host.tutor_capability = SimpleNamespace(bind_pending_delivery=lambda **kwargs: bound.append(kwargs))
    turn = _turn()
    turn = replace(turn, move=replace(turn.move, tool_artifact_bundles=("bundle:verified",)))
    rendered = _rendered("Bài đã có trong workspace")
    rendered = replace(rendered, metadata={
        **rendered.metadata,
        "root_capabilities_used": ("tutor",),
        "event_id": "user:verified",
    })
    RuntimeConversationDeliveryAdapter(host).deliver(
        turn=turn, rendered=rendered, delivery_id="delivery:verified",
    )
    assert bound == [{
        "event_id": "user:verified",
        "session_id": "s", "generation": 1,
        "delivery_id": "delivery:verified", "move_id": "move:1",
        "artifact_bundle_ids": ("bundle:verified",),
    }]
