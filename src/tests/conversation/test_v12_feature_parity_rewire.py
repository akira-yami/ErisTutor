# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from runtime.capabilities import ActionCapability, ResearchCapability
from runtime.conversation.context import RootCapabilityRegistry
from runtime.public_reply_context import compile_public_reply_context


def test_wide_context_mode_keeps_large_retrieved_blocks():
    compiled = compile_public_reply_context(
        personality="P" * 9000,
        relationship="R" * 9000,
        grounding="G" * 20000,
        memory="M" * 20000,
        total_limit=96000,
        budget_mode="wide",
    )
    assert len(compiled.personality) == 9000
    assert len(compiled.relationship) == 9000
    assert len(compiled.grounding) == 20000
    assert len(compiled.memory) == 20000
    assert compiled.dropped == ()
    assert compiled.truncated == ()


def test_action_capability_reaches_existing_executor_without_public_writer():
    calls = []
    result = SimpleNamespace(
        success=True,
        status=SimpleNamespace(value="completed"),
        message="ok",
        data={"volume": 30},
        evidence=("executor:confirmed",),
        error_kind="",
        observer_reason="",
        to_public_meta=lambda: {"verified": True},
    )
    executor = SimpleNamespace(execute_capability=lambda action_id, args: calls.append((action_id, args)) or result)
    capability = ActionCapability(executor)
    payload = capability.invoke_for_root(
        arguments={"action_id": "set_volume", "arguments": {"volume": 30}},
        event=SimpleNamespace(text=""),
        recent_history=(),
    )
    assert calls == [("set_volume", {"volume": 30})]
    assert payload["success"] is True
    assert payload["evidence"] == ["executor:confirmed"]


def test_research_capability_can_start_existing_research_runtime():
    calls = []
    host = SimpleNamespace(_fire_research_followup=lambda topic, **kwargs: calls.append((topic, kwargs)))
    capability = ResearchCapability(host)
    result = capability.invoke_for_root(
        arguments={"operation": "start", "topic": "memory agents", "context": "eris"},
        event=SimpleNamespace(text=""),
        recent_history=(),
    )
    assert result["status"] == "started"
    assert calls == [("memory agents", {"context": "eris", "mode": "research"})]


def test_root_registry_tracks_capability_per_event_for_delivery_receipt():
    registry = RootCapabilityRegistry({"tutor": object()})
    registry.record_invocation("tutor", "event:1")
    registry.record_invocation("tutor", "event:2")
    assert registry.invocations_for("event:1") == ("tutor",)
    assert registry.invocations_for("event:2") == ("tutor",)
