from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from runtime.capabilities.canvas import CanvasCapability
from runtime.capabilities.work import WorkCapability
from runtime.conversation.bootstrap import ensure_conversation_kernel
from runtime.conversation.contracts import ConversationAfter, ConversationEvent, NextConversationMove, RunPhase
from runtime.conversation.delivery import SurfaceEnvelope
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.writer import RenderedConversationMove
from runtime.learning_canvas import LearningCanvasService
from runtime.learning_canvas.events import connect_canvas_host, allow_canvas_result, scope_is_current
from runtime.learning_core import LearningStore, LearningService

A, B, C = "javadi:v1:constructor", "javadi:v1:testing", "javadi:v1:composition-root"
SID = "foreground:primary"


class Agent:
    def __init__(self):
        self.events = []

    def decide(self, **kwargs):
        self.events.append(kwargs["event"])
        return NextConversationMove(intent="teach", say="Explain current node", after=ConversationAfter.CONTINUE_SELF)


class Writer:
    def realize(self, **kwargs):
        return RenderedConversationMove(surface=SurfaceEnvelope(display_text="Nội dung Root chọn"))


class Delivery:
    def __init__(self):
        self.calls = []

    def deliver(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture
def host(tmp_path):
    store = LearningStore(tmp_path / "learning.sqlite3")
    canvas = LearningCanvasService(LearningService(store), store=store)
    canvas.focus.navigate(A, base_revision=0)
    agent, delivery = Agent(), Delivery()
    kernel = ConversationKernel(agent, writer=Writer(), delivery=delivery)
    sent, wakes = [], []
    host = SimpleNamespace(learning_canvas_service=canvas, conversation_kernel=kernel, chat_history=[], running=True,
        _send_runtime_json=lambda packet, **_: sent.append(packet) or True, _wake_autonomous_loop=wakes.append,
        sent=sent, wakes=wakes, agent=agent, delivery=delivery)
    connect_canvas_host(host)
    ensure_conversation_kernel(host)
    yield host
    canvas.close()
    store.close()


def scope(host):
    focus = host.learning_canvas_service.focus.snapshot()
    return {"node_id": focus["node_id"], "focus_revision": focus["focus_revision"]}


def navigate(host, node):
    canvas = host.learning_canvas_service
    return canvas.handle({"request_id": f"nav-{canvas.focus.snapshot()['revision']}", "action": "navigate",
                          "node_id": node, "base_revision": canvas.focus.snapshot()["revision"]})


def event():
    return ConversationEvent(event_id="canvas:A", session_id=SID, kind="tutor_result")


@pytest.mark.parametrize("phase", [RunPhase.THINKING, RunPhase.WORKING, RunPhase.WRITING])
def test_uncommitted_phase_is_invalidated_without_generation_change(host, phase):
    kernel = host.conversation_kernel
    kernel.store.mutate(SID, lambda prior, now: replace(prior, phase=phase, generation=4, active_run_id="A"))
    assert navigate(host, B)["status"] == "ok"
    state = kernel.snapshot(SID)
    assert state.generation == 4 and not state.active_run_id and state.phase == RunPhase.WAITING_USER
    assert not host.delivery.calls


@pytest.mark.parametrize("blocked_stage", ["root", "writer"])
def test_click_during_actual_blocked_run_discards_uncommitted_output(host, blocked_stage):
    entered, release = threading.Event(), threading.Event()
    kernel = host.conversation_kernel
    target = kernel.agent if blocked_stage == "root" else kernel.writer
    name = "decide" if blocked_stage == "root" else "realize"
    original = getattr(target, name)

    def blocked(**kwargs):
        entered.set()
        assert release.wait(3)
        return original(**kwargs)

    setattr(target, name, blocked)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(kernel.handle_event, event(), runtime_context={"__canvas_scope": scope(host)})
        assert entered.wait(1)
        assert navigate(host, C)["status"] == "ok"
        assert navigate(host, B)["status"] == "ok"
        release.set()
        assert future.result(timeout=3).status == "stale"
    assert not host.delivery.calls
    pending = host._pending_conversation_capability_events
    assert len(pending) == 1 and pending[0]["metadata"]["canvas_capability"]["node_id"] == B


@pytest.mark.parametrize("audio_started", [False, True])
def test_committed_queued_or_playing_audio_is_preserved_and_lease_not_revived(host, audio_started):
    kernel = host.conversation_kernel
    result = kernel.handle_event(event(), runtime_context={"__canvas_scope": scope(host)})
    if audio_started:
        assert kernel.mark_audio_started(delivery_id=result.delivery_id)
    before = kernel.snapshot(SID)
    navigate(host, C)
    navigate(host, B)
    after = kernel.snapshot(SID)
    assert after.phase == RunPhase.DELIVERING
    assert (after.generation, after.active_delivery_id, after.active_run_id) == (before.generation, before.active_delivery_id, before.active_run_id)
    assert kernel.settle_delivery_id(delivery_id=result.delivery_id, successful=True)
    assert kernel.continuation.peek(SID) is None
    assert "capability_events_after_delivery" in host.wakes
    assert host._pending_conversation_capability_events[0]["metadata"]["canvas_capability"]["node_id"] == B


def test_continuation_pending_is_cancelled_on_navigation(host):
    kernel = host.conversation_kernel
    result = kernel.handle_event(event(), runtime_context={"__canvas_scope": scope(host)})
    kernel.settle_delivery_id(delivery_id=result.delivery_id, successful=True)
    assert kernel.continuation.peek(SID) is not None
    navigate(host, B)
    assert kernel.continuation.peek(SID) is None
    assert not kernel._canvas_lease_scopes


@pytest.mark.parametrize("kind,key", [("research_result", "research_capability"), ("work_result", "work_capability")])
def test_late_result_is_badged_and_survives_restart(host, kind, key):
    canvas = host.learning_canvas_service
    old = scope(host)
    navigate(host, B)
    payload = {"canvas_scope": old, key: {"summary": "Kết quả thật của A"}}
    assert not allow_canvas_result(host, kind, payload, event_id="late:A")
    assert not allow_canvas_result(host, kind, payload, event_id="late:A")
    state = canvas.state_packet()["learning_canvas"]
    assert next(n for n in state["nodes"] if n["id"] == A)["result_count"] == 1
    assert state["focus"]["node_id"] == B and not host.delivery.calls
    store = LearningStore(canvas.focus.store._path)
    try:
        restored = LearningCanvasService(LearningService(store), store=store)
        result = restored.handle({"request_id": "late-read", "action": "open", "node_id": A})["learning_canvas"]
        assert result["late_results"][0]["summary"] == "Kết quả thật của A"
        restored.close()
    finally:
        store.close()


def test_ack_is_not_a_new_focus_revision(host):
    canvas = host.learning_canvas_service
    # Accept a suggestion to release the initial user pin.
    suggestion = canvas.focus.eris_focus(B, base_revision=1, cause_id="suggest:B")
    accepted = canvas.focus.navigate(B, base_revision=suggestion["revision"])
    before = {"node_id": B, "focus_revision": accepted["focus_revision"]}
    after = canvas.focus.acknowledge(base_revision=accepted["revision"], cause_id="suggest:B")
    assert after["revision"] > accepted["revision"] and after["focus_revision"] == accepted["focus_revision"]
    assert scope_is_current(host, before)


def test_pin_suggestion_click_and_verbal_acceptance(host):
    host.learning_canvas_service._view_session("open", "manual-browser-test")
    cap = CanvasCapability(host)
    auto = ConversationEvent(event_id="auto", session_id=SID, kind="tutor_result")
    result = cap.invoke_for_root(arguments={"operation": "focus", "node_id": B}, event=auto)
    assert not result["applied"] and result["reason"] == "user_pinned"
    assert host.learning_canvas_service.focus.snapshot()["node_id"] == A
    accepted = navigate(host, B)["learning_canvas"]["focus"]
    assert not accepted["pinned"] and accepted["set_by"] == "eris"
    navigate(host, A)
    cap.invoke_for_root(arguments={"operation": "focus", "node_id": B}, event=auto)
    bad = cap.invoke_for_root(arguments={"operation": "accept_focus", "node_id": B}, event=auto)
    assert bad["status"] == "failed"
    user = ConversationEvent.user_turn(session_id=SID, text="Đi theo gợi ý ấy nhé")
    good = cap.invoke_for_root(arguments={"operation": "accept_focus", "node_id": B}, event=user)
    assert good["applied"] and not good["learning_canvas"]["focus"]["pinned"]


def test_work_scope_is_captured_before_execution(host):
    kernel = host.conversation_kernel
    kernel.store.mutate(SID, lambda prior, now: replace(prior, active_run_id="root:A", phase=RunPhase.WORKING))
    old = scope(host)
    kernel.bind_canvas_scope(SID, old)
    calls = []
    cap = WorkCapability(SimpleNamespace(invoke=lambda *_a, **kw: calls.append(kw) or {"status": "started"}), host=host)
    cap.start_work(request="work on A", session_id=SID)
    task_id = calls[0]["task_id"]
    navigate(host, B)
    assert host._canvas_work_scopes[task_id] == old


def test_stale_cas_does_not_cancel_an_active_delivery(host):
    kernel = host.conversation_kernel
    result = kernel.handle_event(event(), runtime_context={"__canvas_scope": scope(host)})
    before = kernel.snapshot(SID)
    reply = host.learning_canvas_service.handle({"request_id": "stale", "action": "navigate", "node_id": B, "base_revision": 0})
    assert reply["status"] == "conflict"
    assert kernel.snapshot(SID) == before
    assert kernel.settle_delivery_id(delivery_id=result.delivery_id, successful=True)
    assert kernel.continuation.peek(SID) is not None


def test_research_becoming_stale_during_root_is_saved_without_speech(host):
    from runtime.tutor_research_runtime import TutorResearchRuntimeMixin
    old = scope(host)
    original = host.agent.decide

    def change_focus(**kwargs):
        navigate(host, B)
        return original(**kwargs)

    host.agent.decide = change_focus
    TutorResearchRuntimeMixin._deliver_research_followup(host, "Constructor", "Kết quả nghiên cứu A",
                                                        job_id="research-A", canvas_scope=old)
    assert host.last_conversation_run["status"] == "stale"
    assert not host.delivery.calls
    data = host.learning_canvas_service.focus.store.get_meta("learning_canvas_results_v1", {})
    assert data[A][0]["summary"] == "Kết quả nghiên cứu A"


def test_concurrent_eris_and_user_focus_uses_revision_then_user_retry(host):
    canvas = host.learning_canvas_service
    cap = CanvasCapability(host)
    auto = ConversationEvent(event_id="concurrent", session_id=SID, kind="tutor_result")
    # Release the initial pin via an explicit accepted proposal.
    canvas.focus.eris_focus(C, base_revision=1, cause_id="suggest:C")
    navigate(host, C)
    base = canvas.focus.snapshot()["revision"]
    gate = threading.Barrier(2)

    def eris():
        gate.wait(timeout=2)
        return cap.invoke_for_root(arguments={"operation": "focus", "node_id": A, "base_revision": base}, event=auto)

    def user():
        gate.wait(timeout=2)
        return canvas.handle({"request_id": "concurrent-user", "action": "navigate", "node_id": B, "base_revision": base})

    with ThreadPoolExecutor(max_workers=2) as pool:
        eris_run, user_run = pool.submit(eris), pool.submit(user)
        eris_result, user_result = eris_run.result(timeout=3), user_run.result(timeout=3)
    if user_result["status"] == "conflict":
        assert eris_result["status"] == "completed"
        assert navigate(host, B)["status"] == "ok"
    else:
        assert user_result["status"] == "ok" and eris_result["status"] == "failed"
    final = canvas.focus.snapshot()
    assert final["node_id"] == B and final["pinned"]
