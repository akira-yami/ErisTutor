# -*- coding: utf-8 -*-
"""FEAT (rescue): Root finalize move TRỰC TIẾP, không còn bước SELF-VERIFY.

Trước đây khi model trả kind=move, Root gửi lại chính move đó cho một lượt model
thứ hai (self-verify) rồi mới finalize. Lượt đó làm phẳng personality nên đã bị
gỡ. Giờ Root finalize NGAY move đã chọn qua NextConversationMove.from_mapping,
chỉ còn tôn trọng preempt (user chen vào giữa chừng -> NoReply('root_turn_preempted')).

Anti-fabrication KHÔNG dựa vào self-verify: claim_manifest + grounding gate +
enforce_completed_action_claims ở runtime_writer vẫn là net độc lập.

Fake router chỉ trả JSON đã định trước; test bảo đảm runtime finalize đúng một
lượt model và giữ biên NoReply|NextConversationMove.
"""
from __future__ import annotations

from runtime.conversation.agent import RuntimeModelRootAgent
from runtime.conversation.contracts import ConversationEvent, NextConversationMove, NoReply


class FakeRouter:
    """Trả lần lượt từ hàng đợi. Chỉ nên bị gọi MỘT lần cho một finalize."""

    executive_client = object()

    def __init__(self, *results) -> None:
        self.results = list(results)
        self.requests: list[dict] = []

    def invoke_structured_worker(self, _client, **kwargs):
        self.requests.append(dict(kwargs))
        assert self.results, "Root called model more times than expected"
        return self.results.pop(0)


def _event(text: str = "chào Eris", kind: str = "user_turn") -> ConversationEvent:
    return ConversationEvent(event_id="e1", session_id="s", kind=kind, text=text)


def _move(say: str = "trả lời trực tiếp câu user") -> dict:
    return {"kind": "move", "intent": "đáp lượt user", "say": say,
            "delivery": "tự nhiên", "after": "wait_user"}


def _decide(router, *, event=None, context=None) -> object:
    return RuntimeModelRootAgent(router).decide(
        event=event or _event(), recent_history=(), note="",
        runtime_context=context or {},
    )


def test_move_finalizes_directly_without_second_model_call():
    router = FakeRouter(_move("giữ nguyên câu này"))
    decision = _decide(router)
    assert isinstance(decision, NextConversationMove)
    assert "giữ nguyên câu này" in decision.say
    # Đúng MỘT lượt model: move. Không có lượt verify thứ hai.
    assert len(router.requests) == 1


def test_edgy_move_is_not_altered():
    # Câu edgy/trêu: finalize byte-for-byte, không có bước nào trung hòa nó.
    edgy = "hừ, lại hỏi nữa à? được thôi, nghe cho kỹ đây nhóc 😏"
    router = FakeRouter(_move(edgy))
    decision = _decide(router)
    assert isinstance(decision, NextConversationMove)
    assert decision.say == edgy
    assert len(router.requests) == 1


def test_preempt_midway_returns_preempted():
    # still_active True lúc gọi model, False NGAY sau khi move trả về (user chen
    # vào). Finalize phải trả NoReply('root_turn_preempted'), không stale move.
    active = [True]

    class Router(FakeRouter):
        def invoke_structured_worker(self, client, **kwargs):
            answer = super().invoke_structured_worker(client, **kwargs)
            active[0] = False  # user chen vào ngay sau khi move được tạo
            return answer

    router = Router(_move("câu bị chen ngang"))
    decision = _decide(router, context={"__root_run_active": lambda: active[0]})
    assert isinstance(decision, NoReply)
    assert decision.reason == "root_turn_preempted"
    assert len(router.requests) == 1


def test_no_reply_is_not_finalized_as_move():
    router = FakeRouter({"kind": "no_reply", "reason": "nothing_worth_saying"})
    decision = _decide(router, event=_event(kind="autonomous_signal"))
    assert isinstance(decision, NoReply)
    assert decision.reason == "nothing_worth_saying"
    assert len(router.requests) == 1
