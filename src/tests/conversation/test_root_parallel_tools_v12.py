# -*- coding: utf-8 -*-
"""FEAT-002: PARALLEL non-finalize tool batch trong một iteration.

Model có thể trả MỘT kind=batch chứa nhiều action nội bộ ĐỘC LẬP (chỉ
non-finalize). Runtime chạy hết, gom tool_results, rồi gọi model một lần kế.
Dedup/giới hạn continue_thinking/preempt/fail-soft áp dụng xuyên batch. Batch
KHÔNG tự finalize; move/no_reply không bao giờ nằm trong batch.

Batch chỉ kích hoạt trên nhánh one-shot invoke. Với FakeRouter (không có
root_session thật), ask() trả None nên fallback invoke được dùng → batch khả
dụng. Move finalize TRỰC TIẾP (không còn lượt SELF-VERIFY thứ hai).
"""
from __future__ import annotations

from types import SimpleNamespace

from runtime.conversation.agent import (
    ROOT_BATCH_STEP_SCHEMA,
    ROOT_STEP_SCHEMA,
    RuntimeModelRootAgent,
)
from runtime.conversation.context import (
    RootCapabilityRegistry,
    RootMemoryRecallTools,
)
from runtime.conversation.contracts import ConversationEvent, NextConversationMove, NoReply


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


# --- schema ----------------------------------------------------------------

def test_root_internal_guide_and_batch_schemas_do_not_add_public_moves():
    # Internal guide reading adds one non-public action.
    assert len(ROOT_STEP_SCHEMA["oneOf"]) == 8
    kinds = set()
    for schema in ROOT_STEP_SCHEMA["oneOf"]:
        kinds.update(schema["properties"]["kind"].get("enum", []))
    assert "batch" not in kinds

    # ROOT_BATCH_STEP_SCHEMA = ROOT_STEP_SCHEMA + đúng một kind='batch'.
    assert len(ROOT_BATCH_STEP_SCHEMA["oneOf"]) == 9
    batch_schema = ROOT_BATCH_STEP_SCHEMA["oneOf"][-1]
    assert batch_schema["properties"]["kind"]["enum"] == ["batch"]
    actions = batch_schema["properties"]["actions"]
    assert actions["minItems"] == 1 and actions["maxItems"] == 4
    item_kinds = set()
    for item in actions["items"]["oneOf"]:
        item_kinds.update(item["properties"]["kind"].get("enum", []))
    # Non-finalize only; move/no_reply bị cấm trong actions.
    assert item_kinds == {"use_capability", "read_tool_guide", "recall_memory", "continue_thinking", "set_note", "clear_note"}
    assert "move" not in item_kinds and "no_reply" not in item_kinds


def test_fallback_invoke_uses_batch_schema():
    router = FakeRouter({"kind": "no_reply", "reason": "done"})
    decide(router)
    from runtime.conversation.agent import root_step_schema
    assert router.calls[0]["schema"] == root_step_schema()


def test_system_prompt_mentions_batch():
    router = FakeRouter({"kind": "no_reply", "reason": "done"})
    decide(router)
    assert "kind=batch" in router.calls[0]["system"]


# --- acceptance: batch chạy hết các action non-finalize ---------------------

def test_batch_runs_all_non_finalize_actions():
    cap_calls = []
    mem_calls = []

    class Tool:
        def invoke_for_root(self, **kwargs):
            cap_calls.append(kwargs)
            return {"status": "completed", "summary": "đã nghiên cứu"}

    registry = RootCapabilityRegistry({"research": Tool()})
    memory = RootMemoryRecallTools(
        recall=lambda q: mem_calls.append(q) or {"status": "found", "text": "ký ức X"})
    router = FakeRouter(
        {"kind": "batch", "actions": [
            {"kind": "recall_memory", "query": "user thích gì"},
            {"kind": "use_capability", "capability": "research", "arguments": {"topic": "A"}},
            {"kind": "continue_thinking", "focus": "cân nhắc hướng nói"},
        ]},
        {"kind": "no_reply", "reason": "done"},
    )
    result = decide(router, context={
        "__root_capabilities": registry,
        "__root_memory_recall_tools": memory,
        "__conversation_run_id": "run:1",
    })

    assert isinstance(result, NoReply)
    assert mem_calls == ["user thích gì"]
    assert len(cap_calls) == 1
    # Lượt model kế (thứ 2) nhận TOÀN BỘ tool_results từ batch.
    observed = router.calls[1]["payload"]["tool_results"]
    kinds = [r["kind"] for r in observed]
    assert kinds == ["memory_result", "capability_result", "reflection_result"]
    assert observed[0]["result"] == {"status": "found", "text": "ký ức X"}
    assert observed[1]["result"]["status"] == "completed"
    assert observed[2]["status"] == "continued"
    assert len(router.calls) == 2


# --- acceptance: dedup / reflection limit / preempt / fail-soft -------------

def test_batch_dedup_recall_memory_runs_real_once():
    mem_calls = []
    memory = RootMemoryRecallTools(
        recall=lambda q: mem_calls.append(q) or {"status": "found"})
    router = FakeRouter(
        {"kind": "batch", "actions": [
            {"kind": "recall_memory", "query": "cùng một query"},
            {"kind": "recall_memory", "query": "cùng một query"},
        ]},
        {"kind": "no_reply", "reason": "done"},
    )
    result = decide(router, context={"__root_memory_recall_tools": memory})

    assert isinstance(result, NoReply)
    assert mem_calls == ["cùng một query"]  # recall thật chỉ chạy một lần
    observed = router.calls[1]["payload"]["tool_results"]
    assert observed[0]["result"]["status"] == "found"
    assert observed[1]["result"]["status"] == "duplicate"


def test_batch_reflection_limit_is_shared_across_batch():
    router = FakeRouter(
        {"kind": "batch", "actions": [
            {"kind": "continue_thinking", "focus": "1"},
            {"kind": "continue_thinking", "focus": "2"},
            {"kind": "continue_thinking", "focus": "3"},
        ]},
        {"kind": "no_reply", "reason": "done"},
    )
    result = decide(router)

    assert isinstance(result, NoReply)
    observed = router.calls[1]["payload"]["tool_results"]
    assert [r["status"] for r in observed] == ["continued", "continued", "limit_reached"]


def test_batch_reflection_limit_counts_prior_reflection():
    # Một continue_thinking trước (single) rồi batch 2 cái nữa -> cái thứ 3 vượt.
    router = FakeRouter(
        {"kind": "continue_thinking", "focus": "0"},
        {"kind": "batch", "actions": [
            {"kind": "continue_thinking", "focus": "1"},
            {"kind": "continue_thinking", "focus": "2"},
        ]},
        {"kind": "no_reply", "reason": "done"},
    )
    result = decide(router)

    assert isinstance(result, NoReply)
    batch_results = router.calls[2]["payload"]["tool_results"][-2:]
    assert [r["status"] for r in batch_results] == ["continued", "limit_reached"]


def test_batch_preempt_between_actions_stops_and_returns_preempted():
    active = [True]
    mem_calls = []

    def recall(query):
        mem_calls.append(query)
        # Sau action đầu, user chen vào: action thứ 2 không được chạy.
        active[0] = False
        return {"status": "found"}

    memory = RootMemoryRecallTools(recall=recall)
    router = FakeRouter(
        {"kind": "batch", "actions": [
            {"kind": "recall_memory", "query": "đầu"},
            {"kind": "recall_memory", "query": "sau"},
        ]},
    )
    result = decide(router, context={
        "__root_memory_recall_tools": memory,
        "__root_run_active": lambda: active[0],
    })

    assert isinstance(result, NoReply)
    assert result.reason == "root_turn_preempted"
    assert mem_calls == ["đầu"]  # phần tử thứ 2 không chạy
    assert len(router.calls) == 1  # không gọi model lượt kế


def test_batch_failsoft_one_action_error_does_not_stop_batch():
    cap_calls = []

    class Boom:
        def invoke_for_root(self, **kwargs):
            cap_calls.append(kwargs)
            raise RuntimeError("boom")

    mem_calls = []
    registry = RootCapabilityRegistry({"work": Boom()})
    memory = RootMemoryRecallTools(
        recall=lambda q: mem_calls.append(q) or {"status": "found"})
    router = FakeRouter(
        {"kind": "batch", "actions": [
            {"kind": "use_capability", "capability": "work", "arguments": {}},
            {"kind": "recall_memory", "query": "vẫn chạy"},
        ]},
        {"kind": "no_reply", "reason": "done"},
    )
    result = decide(router, context={
        "__root_capabilities": registry,
        "__root_memory_recall_tools": memory,
        "__conversation_run_id": "run:1",
    })

    assert isinstance(result, NoReply)
    assert len(cap_calls) == 1
    observed = router.calls[1]["payload"]["tool_results"]
    assert observed[0]["kind"] == "capability_result"
    assert observed[0]["result"]["status"] == "failed"
    assert observed[1]["kind"] == "memory_result"  # action sau vẫn chạy
    assert mem_calls == ["vẫn chạy"]


# --- acceptance: finalize không bao giờ nằm trong batch ---------------------

def test_batch_ignores_finalize_actions_defensively():
    # Nếu move/no_reply lọt vào actions (dù schema cấm) thì bị bỏ qua, batch
    # KHÔNG finalize từ bên trong.
    router = FakeRouter(
        {"kind": "batch", "actions": [
            {"kind": "move", "intent": "x", "say": "y", "delivery": "z", "after": "wait_user"},
            {"kind": "continue_thinking", "focus": "chỉ cái này chạy"},
            {"kind": "no_reply", "reason": "cũng bị bỏ"},
        ]},
        {"kind": "no_reply", "reason": "done"},
    )
    result = decide(router)

    assert isinstance(result, NoReply)
    assert result.reason == "done"  # finalize đến từ lượt model kế, KHÔNG từ batch
    observed = router.calls[1]["payload"]["tool_results"]
    assert [r["kind"] for r in observed] == ["reflection_result"]


def test_batch_set_note_updates_working_note_for_next_turn():
    notes = []
    from runtime.conversation.context import ConversationNoteTools
    tools = ConversationNoteTools(set_note=notes.append, clear_note=lambda: notes.append(""))
    router = FakeRouter(
        {"kind": "batch", "actions": [
            {"kind": "set_note", "text": "giữ context X"},
        ]},
        {"kind": "no_reply", "reason": "done"},
    )
    result = decide(router, context={"__conversation_note_tools": tools})

    assert isinstance(result, NoReply)
    assert notes == ["giữ context X"]
    # Note mới được cache và xuất hiện ở payload lượt kế.
    assert router.calls[1]["payload"]["conversation_note"] == "giữ context X"


def test_batch_then_move_finalizes_directly():
    # Sau batch, model trả move -> finalize TRỰC TIẾP (không còn SELF-VERIFY).
    # Tổng 2 lượt: batch + move.
    memory = RootMemoryRecallTools(recall=lambda q: {"status": "found"})
    router = FakeRouter(
        {"kind": "batch", "actions": [{"kind": "recall_memory", "query": "q"}]},
        {"kind": "move", "intent": "nói", "say": "câu nói", "delivery": "tự nhiên", "after": "wait_user"},
    )
    result = decide(router, context={"__root_memory_recall_tools": memory})

    assert isinstance(result, NextConversationMove)
    assert result.say == "câu nói"
    assert len(router.calls) == 2
