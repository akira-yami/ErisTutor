# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from types import SimpleNamespace

from runtime.ai_orchestrated.orchestrator import (
    AIAutonomousOrchestrator,
)
from runtime.conversation.contracts import (
    ConversationAfter,
    ConversationEvent,
    NextConversationMove,
    NoReply,
)
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.delivery import SurfaceEnvelope
from runtime.conversation.writer import RenderedConversationMove


class _Agent:
    def __init__(self, decision):
        self.decision = decision
        self.events = []

    def decide(self, *, event, recent_history, note, runtime_context):
        self.events.append(event)
        return self.decision


class _Writer:
    def __init__(self):
        self.calls = 0

    def realize(self, *, turn, event, recent_history, runtime_context):
        self.calls += 1
        return RenderedConversationMove(
            surface=SurfaceEnvelope(
                display_text="A worthwhile observation.",
                speech_text="A worthwhile observation.",
            ),
            metadata={},
        )


class _Delivery:
    def __init__(self):
        self.calls = 0

    def deliver(self, *, turn, rendered, delivery_id):
        self.calls += 1


def test_idle_autonomous_signal_can_end_in_no_reply_without_surface_calls():
    agent = _Agent(NoReply(reason="nothing_worth_saying"))
    writer = _Writer()
    delivery = _Delivery()
    kernel = ConversationKernel(agent, writer=writer, delivery=delivery)

    result = kernel.handle_event(
        ConversationEvent(event_id="auto:1", session_id="foreground", kind="autonomous_signal")
    )

    assert result.status == "no_reply"
    assert writer.calls == 0
    assert delivery.calls == 0


def test_positive_autonomous_signal_uses_kernel_writer_and_delivery():
    move = NextConversationMove(
        intent="Share a grounded observation.",
        say="Mention the useful change without inventing context.",
        after=ConversationAfter.WAIT_USER,
    )
    agent = _Agent(move)
    writer = _Writer()
    delivery = _Delivery()
    kernel = ConversationKernel(agent, writer=writer, delivery=delivery)

    result = kernel.handle_event(
        ConversationEvent(event_id="auto:2", session_id="foreground", kind="autonomous_signal")
    )

    assert result.status == "delivering"
    assert writer.calls == 1
    assert delivery.calls == 1
    assert agent.events[0].kind == "autonomous_signal"


def test_autonomous_signal_does_not_overlap_active_delivery():
    agent = _Agent(NextConversationMove(intent="answer", say="answer"))
    kernel = ConversationKernel(agent)
    turn = kernel.begin_user_turn(session_id="foreground:primary", user_text="hello")
    assert kernel.mark_delivery_started(turn, delivery_id="active") is True
    host = SimpleNamespace(
        conversation_kernel=kernel,
        chat_history=[],
        running=True,
    )
    runtime = AIAutonomousOrchestrator.__new__(AIAutonomousOrchestrator)
    runtime.host = host

    result = runtime._run_v12_signal("autonomous_signal", sleep_after=False)

    assert result is None
    assert len(agent.events) == 1


def test_opening_signal_is_an_opportunity_not_force_speak():
    agent = _Agent(NoReply(reason="no_opening_needed"))
    kernel = ConversationKernel(agent, writer=_Writer(), delivery=_Delivery())

    result = kernel.handle_event(
        ConversationEvent(event_id="opening:1", session_id="foreground", kind="opening_signal")
    )

    assert result.status == "no_reply"
    assert agent.events[0].kind == "opening_signal"


def test_social_signal_reenters_root_without_a_social_writer():
    agent = _Agent(NoReply(reason="social_signal_not_worth_speaking"))
    kernel = ConversationKernel(agent, writer=_Writer(), delivery=_Delivery())
    host = SimpleNamespace(conversation_kernel=kernel, chat_history=[])
    runtime = AIAutonomousOrchestrator.__new__(AIAutonomousOrchestrator)
    runtime.host = host

    result = runtime.run_social_signal({"source": "social_observer"})

    assert result.status == "no_reply"
    assert agent.events[0].kind == "social_signal"
    assert agent.events[0].metadata["source"] == "social_observer"


def test_orchestrator_has_no_unused_direct_body_transport_path():
    # P2: helper body_only cũ không có consumer và bypass Kernel.
    # Khi cần motion không lời sẽ bổ sung stage-specific port, không gọi TTS.
    assert not hasattr(AIAutonomousOrchestrator, "_emit_body_only")


def test_production_autonomous_iteration_enters_kernel_instead_of_self_turn_pipeline(monkeypatch):
    host = SimpleNamespace()
    runtime = AIAutonomousOrchestrator.__new__(AIAutonomousOrchestrator)
    runtime.host = host
    runtime.trace_store = None
    seen = []
    monkeypatch.setattr(runtime, "_run_v12_signal", lambda kind, **kwargs: seen.append(kind) or True)

    assert runtime.run_iteration() is True
    assert seen == ["autonomous_signal"]


def test_autonomous_social_opening_modules_cannot_commit_public_output():
    public_entries = (
        AIAutonomousOrchestrator.run_iteration,
        AIAutonomousOrchestrator.run_startup,
        AIAutonomousOrchestrator.run_force_speak,
        AIAutonomousOrchestrator.run_social_signal,
    )
    source = "\n".join(inspect.getsource(entry) for entry in public_entries)

    assert "_run_v12_signal" in source
    for forbidden in (
        "generate_autonomous_reply",
        "generate_opening_plan",
        "generate_opening_surface",
        "_commit_public",
        "_send_decision_to_body",
        "record_assistant",
        "_append_chat_history",
    ):
        assert forbidden not in source
