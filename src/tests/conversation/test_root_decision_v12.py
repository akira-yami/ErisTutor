# -*- coding: utf-8 -*-
from __future__ import annotations

from runtime.conversation import (
    ConversationEvent,
    NextConversationMove,
    NoReply,
    RuntimeModelRootAgent,
)
from runtime.conversation.context import ConversationNoteTools, RootCapabilityRegistry


class _Router:
    def __init__(self, result=None, *, results=None, error: Exception | None = None) -> None:
        self.executive_client = object()
        self.result = result
        self.results = list(results or [])
        self.error = error
        self.requests: list[dict] = []

    def invoke_structured_worker(self, _client, **kwargs):
        self.requests.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        if self.results:
            return self.results.pop(0)
        return self.result


def _event(kind: str, *, text: str = "") -> ConversationEvent:
    return ConversationEvent(
        event_id=f"event:{kind}",
        session_id="s",
        kind=kind,
        text=text,
    )


def test_root_decision_parses_explicit_no_reply():
    router = _Router({"kind": "no_reply", "reason": "nothing_worth_saying"})

    decision = RuntimeModelRootAgent(router).decide(
        event=_event("autonomous_signal"),
        recent_history=(),
        note="",
        runtime_context={},
    )

    assert isinstance(decision, NoReply)
    assert decision.reason == "nothing_worth_saying"
    assert set(decision.to_dict()) == {"schema", "kind", "decision_id", "reason"}


def test_non_user_model_failure_fails_closed_to_no_reply():
    router = _Router(error=RuntimeError("provider unavailable"))

    decision = RuntimeModelRootAgent(router).decide(
        event=_event("opening_signal"),
        recent_history=(),
        note="",
        runtime_context={},
    )

    assert isinstance(decision, NoReply)
    assert decision.reason == "root_model_unavailable"
    payload = decision.to_dict()
    assert "say" not in payload
    assert "delivery" not in payload
    assert "after" not in payload


def test_user_model_failure_uses_root_owned_degraded_move():
    router = _Router(error=RuntimeError("provider unavailable"))

    decision = RuntimeModelRootAgent(router).decide(
        event=_event("user_turn", text="giúp tôi với"),
        recent_history=(),
        note="",
        runtime_context={},
    )

    assert isinstance(decision, NextConversationMove)
    assert "giúp tôi với" in decision.say


def test_simple_turn_finalizes_without_calling_capability():
    calls = []

    class _Capability:
        def invoke_for_root(self, **kwargs):
            calls.append(kwargs)
            return {"active": True}

    router = _Router({
        "kind": "move",
        "intent": "bắt lại câu đùa",
        "say": "trêu lại nhẹ",
        "delivery": "vui",
        "after": "wait_user",
    })
    decision = RuntimeModelRootAgent(router).decide(
        event=_event("user_turn", text="hen hen"),
        recent_history=(),
        note="",
        runtime_context={"__root_capabilities": RootCapabilityRegistry({"tutor": _Capability()})},
    )

    assert isinstance(decision, NextConversationMove)
    # Move finalize TRỰC TIẾP: chỉ một lượt model (không còn SELF-VERIFY).
    assert len(router.requests) == 1
    assert calls == []


def test_root_can_call_capability_then_finalize_one_move():
    calls = []

    class _Capability:
        def invoke_for_root(self, **kwargs):
            calls.append(kwargs)
            return {"active": True, "teaching_move": {"kind": "explain_concept"}}

    router = _Router(results=[
        {"kind": "use_capability", "capability": "tutor", "arguments": {"user_text": "transaction là gì"}},
        {
            "kind": "move",
            "intent": "giải thích transaction theo advice",
            "say": "giải thích transaction bằng ví dụ ngắn",
            "delivery": "rõ ràng",
            "after": "wait_user",
        },
    ])
    decision = RuntimeModelRootAgent(router).decide(
        event=_event("user_turn", text="transaction là gì"),
        recent_history=(),
        note="",
        runtime_context={"__root_capabilities": RootCapabilityRegistry({"tutor": _Capability()})},
    )

    assert isinstance(decision, NextConversationMove)
    assert len(calls) == 1
    # use_capability + move = 2 lượt model; move finalize trực tiếp (không verify).
    assert len(router.requests) == 2
    second_payload = router.requests[1]["payload"]
    assert second_payload["tool_results"][0]["capability"] == "tutor"


def test_root_note_tool_is_optional_and_not_a_public_field():
    notes = []
    router = _Router(results=[
        {"kind": "set_note", "text": "Đang đào transaction isolation; user quan tâm dirty read."},
        {
            "kind": "move",
            "intent": "tiếp tục giải thích",
            "say": "giải thích dirty read",
            "delivery": "tự nhiên",
            "after": "wait_user",
        },
    ])
    tools = ConversationNoteTools(set_note=notes.append, clear_note=lambda: notes.append(""))

    decision = RuntimeModelRootAgent(router).decide(
        event=_event("user_turn", text="dirty read là gì"),
        recent_history=(),
        note="",
        runtime_context={"__conversation_note_tools": tools},
    )

    assert isinstance(decision, NextConversationMove)
    assert notes == ["Đang đào transaction isolation; user quan tâm dirty read."]
    assert "note" not in decision.to_dict()
