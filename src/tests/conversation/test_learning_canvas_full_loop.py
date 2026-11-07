"""Task 10 acceptance: real SQLite/MC, fake model/delivery/Java; no live claims."""
from types import SimpleNamespace

from runtime.ai_orchestrated.orchestrator import AIAutonomousOrchestrator
from runtime.capabilities.canvas import CanvasCapability
from runtime.conversation.bootstrap import ensure_conversation_kernel
from runtime.conversation.contracts import ConversationEvent, NextConversationMove, RunPhase
from runtime.conversation.delivery import SurfaceEnvelope
from runtime.conversation.kernel import ConversationKernel
from runtime.conversation.writer import RenderedConversationMove
from runtime.learning_canvas import LearningCanvasService
from runtime.learning_canvas.events import connect_canvas_host, allow_canvas_result
from runtime.learning_core import LearningService, LearningStore


def test_full_learning_loop_and_branch_after_sqlite_restart(tmp_path):
    a, b = "javadi:v1:constructor", "javadi:v1:testing"
    path = tmp_path / "learning.sqlite3"

    class JavaFixture:
        def execute(self, code, definition):
            assert code == definition["solution"]
            return {"status": "completed", "result": "correct",
                    "tests": [{**test, "passed": True} for test in definition["tests"]]}

    host = SimpleNamespace(chat_history=[], running=True, sent=[], delivered=[], wakes=[],
        _sleep_interruptible=lambda *_: None)
    host._send_runtime_json = lambda packet, **_: host.sent.append(packet) or True
    host._wake_autonomous_loop = host.wakes.append

    class Agent:
        def decide(self, *, event, **kwargs):
            if event.kind == "user_turn":
                result = CanvasCapability(host).invoke_for_root(arguments={"operation": "open_group", "group_id": "java-di"}, event=event)
                assert result["status"] == "completed"
            return NextConversationMove(intent="teach from data", say="Explain selected node / assessment")

    def install(store):
        canvas = LearningCanvasService(LearningService(store), store=store, java_workspace=JavaFixture())
        host.learning_canvas_service = canvas
        host.conversation_kernel = ConversationKernel(Agent(),
            writer=SimpleNamespace(realize=lambda **_: RenderedConversationMove(surface=SurfaceEnvelope(display_text="Root-owned speech"))),
            delivery=SimpleNamespace(deliver=lambda **kw: host.delivered.append(kw)))
        connect_canvas_host(host)
        ensure_conversation_kernel(host)
        return canvas, host.conversation_kernel

    store = LearningStore(path)
    canvas, kernel = install(store)
    seq = 0

    def request(action, **fields):
        nonlocal seq
        seq += 1
        reply = host.learning_canvas_service.handle({"request_id": f"e2e:{seq}", "action": action, **fields})
        assert reply["status"] == "ok", reply
        return reply

    first = kernel.handle_event(ConversationEvent.user_turn(session_id="foreground:primary", text="DI là gì?"))
    # P3.7: Root không tự mở Learning; người dùng mở panel khi muốn.
    assert first.status == "delivering" and not host.sent
    kernel.settle_delivery_id(delivery_id=first.delivery_id, successful=True)
    request("navigate", node_id=a, base_revision=0)
    runtime = AIAutonomousOrchestrator.__new__(AIAutonomousOrchestrator)
    runtime.host = host
    runtime.run_iteration()
    assert host.delivered[-1]["turn"].canvas_scope["node_id"] == a
    kernel.settle_delivery_id(delivery_id=kernel.snapshot("foreground:primary").active_delivery_id, successful=True)

    for exercise_id in ("di.constructor.choice", "di.constructor.code"):
        activity = request("exercise", node_id=a, exercise_id=exercise_id)["exercise_update"]
        answer = {"answer": "a"} if activity["type"] == "multiple_choice" else {
            "code": canvas.pack.node(a)["exercises"][1]["solution"]}
        request("submit", activity_id=activity["activity_id"], nonce=activity["nonce"], **answer)
        if activity["type"] == "java":
            canvas.exercises._future.result(timeout=3)
            request("exercise_status", activity_id=activity["activity_id"], nonce=activity["nonce"])
    assert len(canvas.learning.attempts()) == 2
    assert canvas.learning.get_mastery(a).stage != "unknown"
    host._pending_conversation_capability_events.clear()
    canvas.close()
    store.close()

    reopened = LearningStore(path)
    canvas, kernel = install(reopened)
    try:
        assert len(canvas.learning.attempts()) == 2
        assert canvas.focus.snapshot()["node_id"] == a and canvas.focus.snapshot()["pinned"]
        run = kernel.handle_event(ConversationEvent.user_turn(session_id="foreground:primary", text="Giảng tiếp Constructor"))
        assert run.status == "delivering" and kernel.mark_audio_started(delivery_id=run.delivery_id)
        source = dict(run.turn.canvas_scope)
        request("navigate", node_id=b, base_revision=canvas.focus.snapshot()["revision"])
        assert kernel.snapshot("foreground:primary").phase == RunPhase.DELIVERING
        assert not allow_canvas_result(host, "research_result", {"canvas_scope": source,
            "research_capability": {"summary": "Late answer about constructor"}}, event_id="late:e2e")
        assert kernel.settle_delivery_id(delivery_id=run.delivery_id, successful=True)
        runtime.run_iteration()
        assert host.delivered[-1]["turn"].canvas_scope["node_id"] == b
        assert canvas.focus.snapshot()["node_id"] == b
        assert next(n for n in canvas.state_packet()["learning_canvas"]["nodes"] if n["id"] == a)["result_count"] == 1
    finally:
        canvas.close()
        reopened.close()
