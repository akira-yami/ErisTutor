# -*- coding: utf-8 -*-
"""FEAT-003: Trần bước ĐỘNG + phát hiện 'không tiến triển'.

max_root_steps vẫn là trần CỨNG TUYỆT ĐỐI (clamp 2..12, default nâng 6->8).
Thêm no_progress_streak: nếu 2 bước LIÊN TIẾP không sinh tool_result có nghĩa
(toàn duplicate/empty/failed/limit_reached/unavailable/invalid hoặc trùng kết
quả cùng loại bước trước) -> ép finalize sớm (degraded cho user_turn, NoReply
'no_progress' cho khác). Đang tiến triển thì cho chạy tới trần cứng.
still_active() vẫn preempt ĐẦU mỗi bước.

Batch chỉ kích hoạt trên nhánh one-shot invoke; với FakeRouter ask() trả None
nên fallback invoke được dùng. Các test không-tiến-triển kết thúc trước khi có
move nên không kéo lượt SELF-VERIFY.
"""
from __future__ import annotations

from runtime.conversation.agent import RuntimeModelRootAgent
from runtime.conversation.context import RootCapabilityRegistry, RootMemoryRecallTools
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


def move(topic="một đề tài cụ thể"):
    return {"kind": "move", "intent": "nói một điều cụ thể",
            "say": topic, "delivery": "tự nhiên", "after": "wait_user"}


# --- trần cứng + clamp -----------------------------------------------------

def test_default_max_root_steps_is_eight_and_clamped():
    router = FakeRouter()
    assert RuntimeModelRootAgent(router).max_root_steps == 8
    assert RuntimeModelRootAgent(router, max_root_steps=100).max_root_steps == 12
    assert RuntimeModelRootAgent(router, max_root_steps=0).max_root_steps == 8
    assert RuntimeModelRootAgent(router, max_root_steps=1).max_root_steps == 2


# --- không tiến triển -> cắt sớm -------------------------------------------

def test_no_progress_streak_forces_early_finalize_user_turn_memory():
    # Lần 1: recall thật trả 'empty' (vô nghĩa). Lần 2: cùng query -> dedup
    # 'duplicate' (vô nghĩa). Sau 2 bước trì trệ -> cắt sớm, degraded move cho
    # user_turn. KHÔNG chạy đủ 8 lượt model.
    calls = []
    tools = RootMemoryRecallTools(recall=lambda q: calls.append(q) or {"status": "empty"})
    router = FakeRouter(
        {"kind": "recall_memory", "query": "cùng một truy vấn"},
        {"kind": "recall_memory", "query": "cùng một truy vấn"},
        # Các bước sau không được chạy tới vì đã cắt sớm.
        move("không nên tới đây"),
        {"kind": "confirm"},
    )
    result = decide(router, text="giúp tôi",
                    context={"__root_memory_recall_tools": tools})
    assert isinstance(result, NextConversationMove)
    assert "giúp tôi" in result.say  # degraded move dựa trên event.text
    assert len(router.calls) == 2  # cắt sau 2 bước, nhỏ hơn trần cứng
    assert calls == ["cùng một truy vấn"]  # recall thật chỉ chạy 1 lần


def test_no_progress_streak_forces_no_reply_for_autonomous_capability():
    # Hai capability khác args nhưng đều 'failed' -> 2 bước trì trệ -> NoReply
    # 'no_progress' cho event không phải user_turn.
    class Tool:
        def invoke_for_root(self, **kwargs):
            raise RuntimeError("boom")
    registry = RootCapabilityRegistry({"research": Tool()})
    router = FakeRouter(
        {"kind": "use_capability", "capability": "research", "arguments": {"topic": "A"}},
        {"kind": "use_capability", "capability": "research", "arguments": {"topic": "B"}},
        move("không nên tới đây"),
    )
    result = decide(router, kind="autonomous_signal",
                    context={"__root_capabilities": registry, "__conversation_run_id": "run:1"})
    assert isinstance(result, NoReply)
    assert result.reason == "no_progress"
    assert len(router.calls) == 2


# --- đang tiến triển -> KHÔNG cắt oan --------------------------------------

def test_progress_allows_up_to_hard_ceiling():
    # Mỗi recall trả 'found' với nội dung KHÁC NHAU -> luôn meaningful ->
    # streak không bao giờ chạm 2. Loop chạy tới khi model finalize (move),
    # không bị no_progress cắt oan.
    seq = iter(range(100))
    tools = RootMemoryRecallTools(
        recall=lambda q: {"status": "found", "text": f"ký ức số {next(seq)}"})
    router = FakeRouter(
        {"kind": "recall_memory", "query": "q1"},
        {"kind": "recall_memory", "query": "q2"},
        {"kind": "recall_memory", "query": "q3"},
        move("tổng hợp các ký ức khác nhau"),
    )
    result = decide(router, text="kể tôi nghe",
                    context={"__root_memory_recall_tools": tools})
    assert isinstance(result, NextConversationMove)
    assert "tổng hợp" in result.say
    # 3 recall + 1 move = 4 lượt; move finalize trực tiếp (không còn verify).
    assert len(router.calls) == 4


def test_repeated_identical_recall_result_counts_as_no_progress():
    # 'found' nhưng CÙNG query mỗi lần: lần đầu meaningful, lần hai dedup
    # 'duplicate', lần ba vẫn 'duplicate' -> hai bước vô nghĩa liên tiếp -> cắt.
    tools = RootMemoryRecallTools(
        recall=lambda q: {"status": "found", "text": "cùng một ký ức"})
    router = FakeRouter(
        {"kind": "recall_memory", "query": "cùng truy vấn"},
        {"kind": "recall_memory", "query": "cùng truy vấn"},
        {"kind": "recall_memory", "query": "cùng truy vấn"},
        move("không nên tới đây"),
    )
    result = decide(router, kind="autonomous_signal",
                    context={"__root_memory_recall_tools": tools})
    assert isinstance(result, NoReply)
    assert result.reason == "no_progress"
    # Bước 1 found (progress), bước 2+3 duplicate (2 liên tiếp) -> cắt sau 3.
    assert len(router.calls) == 3


def test_single_no_progress_step_does_not_cut():
    # Một bước vô nghĩa rồi một bước có nghĩa -> streak reset, không cắt.
    outcomes = iter([{"status": "empty"}, {"status": "found", "text": "có ích"}])
    tools = RootMemoryRecallTools(recall=lambda q: next(outcomes))
    router = FakeRouter(
        {"kind": "recall_memory", "query": "q1"},
        {"kind": "recall_memory", "query": "q2"},
        move("nói điều hữu ích"),
    )
    result = decide(router, text="hỏi gì đó",
                    context={"__root_memory_recall_tools": tools})
    assert isinstance(result, NextConversationMove)
    # 2 recall + 1 move = 3 lượt; move finalize trực tiếp (không còn verify).
    assert len(router.calls) == 3


# --- preempt vẫn hoạt động (không hồi quy) ---------------------------------

def test_still_active_preempts_each_step():
    active = [True]

    class Router(FakeRouter):
        def invoke_structured_worker(self, client, **kwargs):
            answer = super().invoke_structured_worker(client, **kwargs)
            active[0] = False  # user chen vào ngay sau lượt model đầu
            return answer

    tools = RootMemoryRecallTools(recall=lambda q: {"status": "found", "text": "x"})
    router = Router(
        {"kind": "recall_memory", "query": "q1"},
        {"kind": "recall_memory", "query": "q2"},
    )
    result = decide(router, context={
        "__root_memory_recall_tools": tools,
        "__root_run_active": lambda: active[0],
    })
    assert isinstance(result, NoReply)
    assert result.reason == "root_turn_preempted"
    assert len(router.calls) == 1


# --- batch cũng cập nhật streak --------------------------------------------

def test_batch_of_only_non_progress_results_counts_toward_streak():
    # Mỗi batch gồm 2 recall cùng query: lần đầu 'empty', dedup lần hai
    # 'duplicate' -> cả batch vô nghĩa. Hai batch liên tiếp -> cắt sớm.
    tools = RootMemoryRecallTools(recall=lambda q: {"status": "empty"})
    router = FakeRouter(
        {"kind": "batch", "actions": [
            {"kind": "recall_memory", "query": "same"},
            {"kind": "recall_memory", "query": "same"},
        ]},
        {"kind": "batch", "actions": [
            {"kind": "recall_memory", "query": "same"},
            {"kind": "recall_memory", "query": "same"},
        ]},
        move("không nên tới đây"),
    )
    result = decide(router, kind="autonomous_signal",
                    context={"__root_memory_recall_tools": tools})
    assert isinstance(result, NoReply)
    assert result.reason == "no_progress"
    assert len(router.calls) == 2
