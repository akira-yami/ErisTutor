# -*- coding: utf-8 -*-
"""ERIS-02: test Root loop thật ở ranh giới model/tool, không fake quyền speaker.

Fake router chỉ trả JSON action được định trước; test không tuyên bố DeepSeek
thật đã biết dẫn chuyện hay tự suy nghĩ hay. Phần quyết định semantic vẫn do
model trong RootAgent tạo, runtime chỉ thi hành action đã chọn và chặn stale.
"""
from __future__ import annotations

from types import SimpleNamespace

from runtime.conversation.agent import ROOT_STEP_SCHEMA, RuntimeModelRootAgent
from runtime.conversation.context import (
    RootCapabilityRegistry,
    RootMemoryRecallTools,
    memory_recall_tools_for_host,
)
from runtime.conversation.contracts import ConversationEvent, NextConversationMove, NoReply
from runtime.conversation.kernel import ConversationKernel


class FakeRouter:
    executive_client = object()

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def invoke_structured_worker(self, _client, **kwargs):
        self.calls.append(kwargs)
        assert self.steps, "Root called model more times than expected"
        return self.steps.pop(0)


def event(text="", kind="user_turn"):
    return ConversationEvent(event_id="e1", session_id="session:1", kind=kind, text=text)


def decide(router, *, text="", kind="user_turn", context=None):
    return RuntimeModelRootAgent(router).decide(
        event=event(text, kind), recent_history=(), note="",
        runtime_context=context or {},
    )


def move(topic="một kỷ niệm rõ ràng"):
    return {"kind": "move", "intent": "Tự khởi một đề tài cụ thể",
            "say": topic, "delivery": "tò mò, hồn nhiên", "after": "may_continue"}


def test_root_can_choose_topic_directly_without_another_planner_or_user_choice():
    router = FakeRouter(
        move("Eris muốn hỏi về cách vẽ pixel tóc chuyển động trong game"),
    )
    result = decide(router, text="Bạn phải tự chọn chuyện nói chứ, đừng hỏi tui chọn nữa")
    assert isinstance(result, NextConversationMove)
    assert "pixel tóc" in result.say
    assert result.after.value == "may_continue"
    # Move finalize TRỰC TIẾP: chỉ một lượt model (không còn SELF-VERIFY).
    assert len(router.calls) == 1
    prompt = router.calls[0]["system"]
    assert "CHỌN MỘT đề tài cụ thể" in prompt
    assert "KHÔNG hỏi ngược user chọn đề tài" in prompt
    assert "Khi user chê cuộc nói chuyện nhạt" in prompt
    assert "event" in router.calls[0]["payload"]


def test_recall_memory_is_on_demand_and_root_observes_result_before_speaking():
    calls = []
    def recall(query):
        calls.append(query)
        return {"status": "found", "text": "User thích hình pixel tóc tách lớp", "degraded": False}
    router = FakeRouter(
        {"kind": "recall_memory", "query": "user thích nhân vật pixel thế nào"},
        move("Eris đề nghị thử vẽ tóc từng lớp và lắc như con lắc"),
    )
    result = decide(router, text="Tự kiếm chuyện đi", context={
        "__root_memory_recall_tools": RootMemoryRecallTools(recall=recall),
    })
    assert isinstance(result, NextConversationMove)
    assert calls == ["user thích nhân vật pixel thế nào"]
    # recall_memory + move = 2 lượt; move finalize trực tiếp (không verify).
    assert len(router.calls) == 2
    observations = router.calls[1]["payload"]["tool_results"]
    assert observations == [{"kind": "memory_result", "query": calls[0],
                             "result": {"status": "found", "text": "User thích hình pixel tóc tách lớp", "degraded": False}}]
    assert "tool_results" not in result.to_dict()


def test_root_can_continue_thinking_and_then_choose_silence_in_autonomous_mode():
    router = FakeRouter(
        {"kind": "continue_thinking", "focus": "Vừa chào xong, thêm câu nữa có gây phiền không?"},
        {"kind": "no_reply", "reason": "nothing_new_after_reflection"},
    )
    decision = decide(router, kind="autonomous_signal")
    assert isinstance(decision, NoReply)
    assert len(router.calls) == 2
    assert router.calls[1]["payload"]["tool_results"][-1]["kind"] == "reflection_result"
    assert not any("say" in tool for tool in router.calls[1]["payload"]["tool_results"])


def test_root_repeated_memory_query_does_not_execute_recall_twice():
    calls = []
    # Bước đầu trả 'found' có nghĩa (tiến triển) nên lần recall thứ hai cùng
    # query chỉ 'duplicate' (streak=1) không chạm trần no-progress của FEAT-003.
    tools = RootMemoryRecallTools(
        recall=lambda q: calls.append(q) or {"status": "found", "text": "một ký ức"})
    router = FakeRouter(
        {"kind": "recall_memory", "query": "khung cảnh đang nói"},
        {"kind": "recall_memory", "query": "khung cảnh đang nói"},
        move("Eris tự giới thiệu một chủ đề mới thay vì hỏi lại"),
    )
    result = decide(router, context={"__root_memory_recall_tools": tools})
    assert isinstance(result, NextConversationMove)
    assert calls == ["khung cảnh đang nói"]
    assert router.calls[2]["payload"]["tool_results"][-1]["result"]["status"] == "duplicate"


def test_root_repeated_side_effect_capability_is_blocked_per_event():
    calls = []
    class Tool:
        def invoke_for_root(self, **kwargs):
            calls.append(kwargs)
            return {"status": "completed", "summary": "đã nghiên cứu"}
    registry = RootCapabilityRegistry({"research": Tool()})
    router = FakeRouter(
        {"kind": "use_capability", "capability": "research", "arguments": {"topic": "A"}},
        {"kind": "use_capability", "capability": "research", "arguments": {"topic": "A"}},
        move("kết quả nghiên cứu A"),
    )
    result = decide(router, context={"__root_capabilities": registry, "__conversation_run_id": "run:1"})
    assert isinstance(result, NextConversationMove)
    assert len(calls) == 1
    assert registry.result_receipts_for("e1", run_id="run:1", generation=0)
    assert router.calls[2]["payload"]["tool_results"][-1]["result"]["status"] == "duplicate"


def test_preempt_between_model_and_tool_prevents_stale_side_effect():
    class Router(FakeRouter):
        def invoke_structured_worker(self, client, **kwargs):
            answer = super().invoke_structured_worker(client, **kwargs)
            active[0] = False  # user bắt đầu lượt mới khi DeepSeek còn xử lý
            return answer
    calls = []
    active = [True]
    registry = RootCapabilityRegistry({"work": SimpleNamespace(
        invoke_for_root=lambda **kwargs: calls.append(kwargs) or {"status": "completed"})})
    router = Router({"kind": "use_capability", "capability": "work", "arguments": {}})
    result = decide(router, context={"__root_capabilities": registry,
                                     "__root_run_active": lambda: active[0]})
    assert isinstance(result, NoReply)
    assert result.reason == "root_turn_preempted"
    assert calls == []


def test_memory_adapter_returns_public_safe_typed_selection_from_existing_kernel():
    seen = []
    def build(query, **kwargs):
        seen.append((query, kwargs))
        return SimpleNamespace(text="một ký ức", records=(1, 2), degraded=True)
    host = SimpleNamespace(memory_kernel=SimpleNamespace(build_public_context=build))
    result = memory_recall_tools_for_host(host, session_id="s:bound").query(" câu chuyện cũ ")
    assert result == {"status": "found", "text": "một ký ức", "degraded": True, "source_count": 2}
    assert seen == [("câu chuyện cũ", {"session_id": "s:bound", "user_context_token_budget": 1500, "max_lines": 24})]
    assert memory_recall_tools_for_host(SimpleNamespace(), session_id="s").query("x")["status"] == "unavailable"


def test_kernel_single_writer_after_root_uses_two_private_actions():
    class Writer:
        def __init__(self):
            self.calls = 0
        def realize(self, **kwargs):
            from runtime.conversation.writer import RenderedConversationMove
            from runtime.conversation.delivery import SurfaceEnvelope
            self.calls += 1
            return RenderedConversationMove(surface=SurfaceEnvelope(display_text="chuyện về game"))
    class Delivery:
        def __init__(self):
            self.calls = 0
        def deliver(self, **kwargs):
            self.calls += 1
    router = FakeRouter(
        {"kind": "continue_thinking", "focus": "Chủ đề nên cụ thể và phù hợp"},
        {"kind": "recall_memory", "query": "game user làm"},
        move("Trò chuyện về animation game pixel mà user đang làm"),
    )
    writer, delivery = Writer(), Delivery()
    kernel = ConversationKernel(RuntimeModelRootAgent(router), writer=writer, delivery=delivery)
    result = kernel.handle_event(event("Tự kiếm chuyện"), runtime_context={
        "__root_memory_recall_tools": RootMemoryRecallTools(recall=lambda q: {"status": "found", "text": "game pixel"}),
    })
    assert result.status == "delivering"
    # continue_thinking + recall_memory + move = 3 lượt (không còn SELF-VERIFY).
    assert len(router.calls) == 3
    assert writer.calls == 1 and delivery.calls == 1
    assert kernel.settle_delivery_id(delivery_id=result.delivery_id, successful=True)
    assert kernel.continuation.peek("session:1") is not None


def test_root_step_schema_has_internal_actions_but_public_move_contract_unmodified():
    kinds = set()
    for schema in ROOT_STEP_SCHEMA["oneOf"]:
        kinds.update(schema["properties"]["kind"].get("enum", []))
    assert kinds == {"move", "no_reply", "use_capability", "read_tool_guide", "set_note", "clear_note", "recall_memory", "continue_thinking"}
    fields = ROOT_STEP_SCHEMA["oneOf"][0]["properties"]
    assert set(ROOT_STEP_SCHEMA["oneOf"][0]["required"]) == {"kind", "intent", "say", "delivery", "after"}
    assert set(fields) == {"kind", "intent", "say", "delivery", "after", "document_source", "presentation_blocks",
                           "canvas_segments", "interaction_choice"}


def test_background_event_cannot_override_active_public_delivery_at_kernel_boundary():
    router = FakeRouter(
        move("Root nói câu đang phát"),
    )
    from runtime.conversation.writer import RenderedConversationMove
    from runtime.conversation.delivery import SurfaceEnvelope
    writer = SimpleNamespace(realize=lambda **kwargs: RenderedConversationMove(
        surface=SurfaceEnvelope(display_text="Lời đang nói")))
    delivery = SimpleNamespace(deliver=lambda **kwargs: None)
    kernel = ConversationKernel(RuntimeModelRootAgent(router), writer=writer, delivery=delivery)
    foreground = kernel.handle_event(event("Hãy kể chuyện"))
    assert foreground.status == "delivering"
    before = kernel.snapshot("session:1")
    surprise = ConversationEvent(event_id="auto:2", session_id="session:1", kind="autonomous_signal")
    blocked = kernel.handle_event(surprise)
    assert blocked.status == "busy"
    after = kernel.snapshot("session:1")
    assert after.active_run_id == before.active_run_id
    assert after.active_delivery_id == before.active_delivery_id
    # Move finalize trực tiếp (không còn SELF-VERIFY) nên chỉ 1 lượt model.
    assert len(router.calls) == 1


def test_background_event_cannot_override_root_while_root_is_thinking():
    events = []

    class ReentrantRouter(FakeRouter):
        def invoke_structured_worker(self, client, **kwargs):
            # Tick autonomous khác xảy ra ngay khi request root còn pending.
            rival = kernel.handle_event(ConversationEvent(
                event_id="auto:rival", session_id="session:1", kind="autonomous_signal"))
            events.append(rival.status)
            return super().invoke_structured_worker(client, **kwargs)

    router = ReentrantRouter({"kind": "no_reply", "reason": "wait"})
    kernel = ConversationKernel(RuntimeModelRootAgent(router))
    result = kernel.handle_event(ConversationEvent(
        event_id="auto:original", session_id="session:1", kind="autonomous_signal"))
    assert result.status == "no_reply"
    assert events == ["busy"]
    assert len(router.calls) == 1


def test_due_continuation_releases_lease_if_governor_busy(monkeypatch):
    from runtime.conversation.contracts import ConversationAfter
    from runtime.conversation.kernel import ConversationRunResult
    kernel = ConversationKernel(RuntimeModelRootAgent(FakeRouter()))
    import time
    now = time.time()
    kernel.continuation.schedule(
        session_id="session:1", generation=0, move_id="m1",
        after=ConversationAfter.CONTINUE_SELF, now=now,
    )
    seen = []
    def busy_handler(ev, **kwargs):
        seen.append(ev.kind)
        return ConversationRunResult(event=ev, decision=None, status="busy")
    monkeypatch.setattr(kernel, "handle_event", busy_handler)
    result = kernel.handle_due_continuation(session_id="session:1", now=now + 0.4)
    assert result is not None and result.status == "busy"
    assert seen == ["continuation_due"]
    lease = kernel.continuation.peek("session:1")
    assert lease is not None and lease.status == "pending"  # Không làm mất wake.
