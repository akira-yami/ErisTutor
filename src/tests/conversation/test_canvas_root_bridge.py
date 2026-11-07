from __future__ import annotations

from collections import deque
from dataclasses import replace
from types import SimpleNamespace

import pytest

from runtime.ai_orchestrated.orchestrator import AIAutonomousOrchestrator
from runtime.capabilities.canvas import CanvasCapability
from runtime.conversation.agent import RuntimeModelRootAgent, ROOT_STEP_SCHEMA
from runtime.conversation.context import RootCapabilityRegistry
from runtime.conversation.contracts import ConversationEvent, NextConversationMove, NoReply, RunPhase
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.writer import RenderedConversationMove
from runtime.conversation.delivery import SurfaceEnvelope
from runtime.conversation.tool_guides import TOOL_GUIDES
from runtime.learning_canvas.events import enqueue_canvas_event
from runtime.learning_canvas.service import LearningCanvasService
from runtime.learning_core import LearningService, LearningStore


@pytest.fixture
def host(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    canvas = LearningCanvasService(LearningService(store), store=store)
    sent, wakes = [], []
    host = SimpleNamespace(learning_canvas_service=canvas, chat_history=[], running=True,
                           _send_runtime_json=lambda packet, **_: sent.append(packet) or True,
                           _wake_autonomous_loop=wakes.append)
    host.sent, host.wakes = sent, wakes
    canvas.on_event = lambda data: enqueue_canvas_event(host, data)
    yield host
    canvas.close()
    store.close()


class Root:
    def __init__(self, decision=None):
        self.events = []
        self.decision = decision or NoReply(reason="not_needed")

    def decide(self, **kwargs):
        self.events.append(kwargs["event"])
        return self.decision


class Writer:
    def realize(self, **kwargs):
        return RenderedConversationMove(surface=SurfaceEnvelope(display_text="Root explains the selected node", speech_text="Root explains the selected node"))


class Delivery:
    def __init__(self):
        self.turns = []

    def deliver(self, **kwargs):
        self.turns.append(kwargs)


def invoke(host, **arguments):
    return CanvasCapability(host).invoke_for_root(arguments=arguments,
        event=ConversationEvent(event_id="root-tool", session_id="s", kind="user_turn"))


def test_root_canvas_library_open_and_invalid_group(host):
    library = invoke(host, operation="library")
    assert library["groups"][0]["group_id"] == "java-di"
    assert invoke(host, operation="open_group", group_id="unknown")["status"] == "failed"
    assert not host.sent
    result = invoke(host, operation="open_group", group_id="java-di")
    assert result["status"] == "completed"
    # P3.7: Root không tự bật trang học khi panel chưa mở.
    assert not host.sent and result["panel_active"] is False
    host.learning_canvas_service._view_session("open", "manual-browser-test")
    assert invoke(host, operation="open_group", group_id="java-di")["status"] == "completed"
    assert host.sent[0]["open_panel"] is False and host.sent[0]["type"] == "learning_canvas_state"
    assert host.sent[0]["should_speak"] is False
    assert not getattr(host, "_pending_conversation_capability_events", None)


def test_eris_focus_does_not_create_user_ui_event(host):
    host.learning_canvas_service._view_session("open", "manual-browser-test")
    result = invoke(host, operation="focus", node_id="javadi:v1:constructor")
    focus = result["learning_canvas"]["focus"]
    assert focus["set_by"] == "eris" and focus["cause_id"] and not focus["pinned"]
    assert not getattr(host, "_pending_conversation_capability_events", None)


def test_navigation_enters_root_as_tutor_result_without_bumping_generation(host):
    root, delivery = Root(NextConversationMove(intent="teach", say="Explain the selected node")), Delivery()
    kernel = ConversationKernel(root, writer=Writer(), delivery=delivery)
    host.conversation_kernel = kernel
    canvas = host.learning_canvas_service
    canvas.handle({"request_id": "click", "action": "navigate", "node_id": "javadi:v1:constructor", "base_revision": 0})
    pending = host._pending_conversation_capability_events
    assert len(pending) == 1 and pending[0]["kind"] == "tutor_result"
    assert pending[0]["metadata"]["origin"] == "user_ui"
    assert not root.events and not delivery.turns
    runtime = AIAutonomousOrchestrator.__new__(AIAutonomousOrchestrator)
    runtime.host = host
    before = kernel.snapshot("foreground:primary").generation
    item = pending[0]
    result = runtime._run_v12_signal(item["kind"], event_id=item["event_id"], metadata=item["metadata"], sleep_after=False)
    assert result.status == "delivering"
    assert kernel.snapshot("foreground:primary").generation == before
    assert root.events[0].metadata["canvas_capability"]["node_id"] == "javadi:v1:constructor"
    assert len(delivery.turns) == 1


def test_busy_kernel_event_is_not_cached_and_can_be_retried():
    root = Root()
    kernel = ConversationKernel(root)
    kernel.store.mutate("s", lambda prior, now: replace(prior, phase=RunPhase.DELIVERING))
    event = ConversationEvent(event_id="same-ui-event", session_id="s", kind="tutor_result")
    assert kernel.handle_event(event).status == "busy"
    kernel.store.mutate("s", lambda prior, now: replace(prior, phase=RunPhase.WAITING_USER))
    assert kernel.handle_event(event).status == "no_reply"
    assert len(root.events) == 1


@pytest.mark.parametrize("kind", ["work_result", "tutor_result"])
def test_busy_queue_item_is_kept_until_handled(host, monkeypatch, kind):
    host.conversation_kernel = ConversationKernel(Root())
    item = {"kind": kind, "event_id": "stable", "metadata": {}}
    host._pending_conversation_capability_events = deque([item])
    runtime = AIAutonomousOrchestrator.__new__(AIAutonomousOrchestrator)
    runtime.host = host
    statuses = iter(["busy", "no_reply"])
    monkeypatch.setattr(runtime, "_run_v12_signal", lambda *_a, **_k: SimpleNamespace(status=next(statuses)))
    runtime.run_iteration()
    assert list(host._pending_conversation_capability_events) == [item]
    runtime.run_iteration()
    assert not host._pending_conversation_capability_events


def test_queue_coalesces_clicks_without_removing_work_or_assessment(host):
    host._pending_conversation_capability_events = deque([{"event_id": "work", "kind": "work_result", "metadata": {}}])
    for node in ("A", "C", "B"):
        enqueue_canvas_event(host, {"action": "navigate", "node_id": node})
    enqueue_canvas_event(host, {"action": "assessment", "node_id": "B"})
    pending = list(host._pending_conversation_capability_events)
    assert [e["kind"] for e in pending] == ["work_result", "tutor_result", "tutor_result"]
    assert pending[1]["metadata"]["canvas_capability"]["node_id"] == "B"


def test_consuming_earlier_click_does_not_pop_the_new_click(host, monkeypatch):
    host.conversation_kernel = ConversationKernel(Root())
    enqueue_canvas_event(host, {"action": "navigate", "node_id": "A"})
    runtime = AIAutonomousOrchestrator.__new__(AIAutonomousOrchestrator)
    runtime.host = host

    def complete_while_user_clicks(*_a, **_k):
        enqueue_canvas_event(host, {"action": "navigate", "node_id": "B"})
        return SimpleNamespace(status="no_reply")

    monkeypatch.setattr(runtime, "_run_v12_signal", complete_while_user_clicks)
    runtime.run_iteration()
    assert host._pending_conversation_capability_events[0]["metadata"]["canvas_capability"]["node_id"] == "B"


def test_root_schema_and_guide_expose_canvas_tool():
    schema = next(row for row in ROOT_STEP_SCHEMA["oneOf"] if "capability" in row.get("properties", {}))
    assert "canvas" in schema["properties"]["capability"]["enum"]
    assert "open_group" in TOOL_GUIDES["canvas"]


def test_question_di_to_model_tool_open_then_click_to_root_delivery(host):
    class Router:
        executive_client = object()

        def __init__(self):
            self.steps = iter([
                {"kind": "read_tool_guide", "guide": "canvas"},
                {"kind": "use_capability", "capability": "canvas", "arguments": {"operation": "library"}},
                {"kind": "use_capability", "capability": "canvas", "arguments": {"operation": "open_group", "group_id": "java-di"}},
                {"kind": "move", "intent": "intro", "say": "Explain DI and invite choosing a node", "after": "wait_user"},
                {"kind": "move", "intent": "teach", "say": "Explain constructor injection using the selected Java block", "after": "wait_user"},
            ])

        def invoke_structured_worker(self, _client, **kwargs):
            return next(self.steps)

    delivery = Delivery()
    kernel = ConversationKernel(RuntimeModelRootAgent(Router()), writer=Writer(), delivery=delivery)
    host.conversation_kernel = kernel
    context = {"__root_capabilities": RootCapabilityRegistry({"canvas": CanvasCapability(host)})}
    first = kernel.handle_event(ConversationEvent.user_turn(session_id="foreground:primary", text="DI là gì?"), runtime_context=context)
    assert first.status == "delivering" and not host.sent  # Không auto-open panel.
    assert kernel.settle_delivery(first.turn, delivery_id=first.delivery_id, successful=True)
    canvas = host.learning_canvas_service
    canvas.handle({"request_id": "manual-open", "action": "open", "client_id": "manual-browser-test"})
    canvas.handle({"request_id": "e2e-click", "action": "navigate", "node_id": "javadi:v1:constructor", "base_revision": 0})
    item = host._pending_conversation_capability_events[0]
    second = kernel.handle_event(ConversationEvent(event_id=item["event_id"], session_id="foreground:primary",
        kind=item["kind"], metadata=item["metadata"]), runtime_context=context)
    assert second.status == "delivering" and len(delivery.turns) == 2
    assert second.turn.generation == first.turn.generation
