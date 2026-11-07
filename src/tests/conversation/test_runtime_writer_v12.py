# -*- coding: utf-8 -*-
from __future__ import annotations

from runtime.conversation import (
    ConversationAfter,
    ConversationEvent,
    ConversationKernel,
    KernelTurn,
    NextConversationMove,
)
from runtime.conversation.runtime_writer import RuntimeConversationWriter


class _Router:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[dict] = []

    def generate_public_reply(self, **kwargs):
        self.calls.append(dict(kwargs))
        return dict(self.payload)


class _Host:
    def __init__(self, router):
        self.ai_model_router = router


def _turn(*, after: ConversationAfter = ConversationAfter.CONTINUE_SELF) -> KernelTurn:
    return KernelTurn(
        session_id="s",
        generation=3,
        run_id="run:3",
        move=NextConversationMove(
            intent="explain the result",
            say="state the result and its evidence",
            delivery="calm and concise",
            after=after,
            move_id="move:3",
        ),
        note="",
    )


def test_runtime_writer_projects_exact_root_move_into_writer_context():
    router = _Router({
        "text": "Kết quả đây.",
        "speech_text": "Kết quả đây.",
        "display_language": "vi",
        "speech_language": "vi",
    })
    writer = RuntimeConversationWriter(_Host(router))
    event = ConversationEvent(event_id="user:1", session_id="s", kind="user_turn", text="xem kết quả")

    rendered = writer.realize(
        turn=_turn(),
        event=event,
        recent_history=({"role": "user", "content": "xem kết quả"},),
        runtime_context={"grounding_context": "artifact:test"},
    )

    assert rendered.surface.display_text == "Kết quả đây."
    assert rendered.surface.speech_text == "Kết quả đây."
    request = router.calls[0]
    assert request["user_text"] == "xem kết quả"
    assert '"move_id":"move:3"' in request["discourse_context"]
    assert '"after":"continue_self"' in request["discourse_context"]
    assert rendered.decision_payload == _turn().move.to_dict()


def test_runtime_writer_disables_thinking_by_default_for_public_surface():
    # Lane user-visible default TẮT thinking để trả lời nhanh (thinking ON làm
    # chậm 1.5–2× và nới cửa drop). Giữ reasoning_effort thấp; caller vẫn override
    # được qua runtime_context["thinking_mode"].
    router = _Router({"text": "Kết quả đây.", "speech_text": "Kết quả đây."})
    writer = RuntimeConversationWriter(_Host(router))
    event = ConversationEvent(event_id="user:1", session_id="s", kind="user_turn", text="xem kết quả")

    writer.realize(
        turn=_turn(),
        event=event,
        recent_history=(),
        runtime_context={},
    )

    request = router.calls[0]
    assert request["thinking_mode"] == "disabled"
    assert request["reasoning_effort"] == "low"


def test_runtime_writer_thinking_mode_still_overridable():
    router = _Router({"text": "Kết quả đây.", "speech_text": "Kết quả đây."})
    writer = RuntimeConversationWriter(_Host(router))
    event = ConversationEvent(event_id="user:1", session_id="s", kind="user_turn", text="xem kết quả")

    writer.realize(
        turn=_turn(),
        event=event,
        recent_history=(),
        runtime_context={"thinking_mode": "disabled"},
    )

    assert router.calls[0]["thinking_mode"] == "disabled"


def test_runtime_writer_thinking_mode_overridable_to_enabled():
    # Override cả chiều ngược lại: caller ép thinking enabled vẫn phải được tôn trọng.
    router = _Router({"text": "Kết quả đây.", "speech_text": "Kết quả đây."})
    writer = RuntimeConversationWriter(_Host(router))
    event = ConversationEvent(event_id="user:1", session_id="s", kind="user_turn", text="xem kết quả")

    writer.realize(
        turn=_turn(),
        event=event,
        recent_history=(),
        runtime_context={"thinking_mode": "enabled"},
    )

    assert router.calls[0]["thinking_mode"] == "enabled"


def test_runtime_writer_cannot_change_after_or_open_a_second_move():
    router = _Router({
        "text": "Một câu hoàn chỉnh.",
        "after": "wait_user",
        "next_intent": "invented by writer",
    })
    writer = RuntimeConversationWriter(_Host(router))
    turn = _turn(after=ConversationAfter.CONTINUE_SELF)

    rendered = writer.realize(
        turn=turn,
        event=ConversationEvent(event_id="event:1", session_id="s", kind="autonomous_signal"),
        recent_history=(),
        runtime_context={},
    )

    assert turn.move.after == ConversationAfter.CONTINUE_SELF
    assert rendered.decision_payload["after"] == "continue_self"
    assert "next_intent" not in rendered.decision_payload


def test_stale_generation_discards_rendered_surface():
    deliveries: list[dict] = []

    class _Agent:
        def decide(self, **_kwargs):
            return NextConversationMove(intent="answer", say="answer")

    class _Writer:
        def __init__(self):
            self.kernel = None

        def realize(self, **kwargs):
            self.kernel.preempt_for_user(kwargs["turn"].session_id)
            return RuntimeConversationWriter(
                _Host(_Router({"text": "stale answer"}))
            ).realize(**kwargs)

    class _Delivery:
        def deliver(self, **kwargs):
            deliveries.append(dict(kwargs))

    writer = _Writer()
    kernel = ConversationKernel(_Agent(), writer=writer, delivery=_Delivery())
    writer.kernel = kernel

    result = kernel.handle_event(ConversationEvent.user_turn(session_id="s", text="question"))

    assert result.status == "stale"
    assert deliveries == []


def test_runtime_writer_rewrites_once_when_grounding_gate_rejects_first_draft():
    from runtime.source_grounding import SourceGroundingEngine

    class _SequenceRouter:
        def __init__(self):
            self.calls = []
        def generate_public_reply(self, **kwargs):
            self.calls.append(dict(kwargs))
            if len(self.calls) == 1:
                return {"text": "Tôi thấy trên màn hình của bạn có lỗi đỏ."}
            return {"text": "Nếu bạn đang nói tới lỗi đỏ đó, gửi phần log hoặc cho tôi xem màn hình để tôi xác nhận."}

    router = _SequenceRouter()
    writer = RuntimeConversationWriter(_Host(router))
    engine = SourceGroundingEngine()
    report = engine.analyze(
        "nhìn màn hình giúp tôi",
        screen_facts=(),
        allow_web=False,
        attach_to_frame=False,
    )
    rendered = writer.realize(
        turn=_turn(),
        event=ConversationEvent(event_id="user:ground", session_id="s", kind="user_turn", text="nhìn màn hình giúp tôi"),
        recent_history=(),
        runtime_context={
            "grounding_context": report.prompt_context,
            "__source_grounding_engine": engine,
            "__source_grounding_report": report,
        },
    )

    assert len(router.calls) == 1
    assert 'GROUNDING_REWRITE_V12' not in router.calls[0]['discourse_context']
    assert rendered.surface.display_text.startswith('Tôi thấy')
