# -*- coding: utf-8 -*-
"""Regression cho ConversationHandoff sau V12 hard cut.

Tên file lịch sử được giữ để dễ truy vết Git, nhưng expectation đã đổi: handoff là
observer causal/provenance, không còn là semantic authority hay speech producer.
"""
from __future__ import annotations

from types import SimpleNamespace

from runtime.ai_orchestrated.orchestrator import AIAutonomousOrchestrator
from runtime.conversation_handoff import ConversationHandoffRuntime


def _abstract_eris_line() -> str:
    return (
        "Đôi khi nhìn thấy hai lớp đó lại hóa thành một lớp thứ ba, cách tôi tự soi chiếu "
        "cái cách tôi đang đọc, và liệu tôi có thể ngừng nhìn chằm chằm vào lăng kính "
        "của chính mình để thực sự thấy bạn không?"
    )


def test_self_deprecation_after_abstract_line_is_observation_not_action() -> None:
    runtime = ConversationHandoffRuntime()
    previous = _abstract_eris_line()
    runtime.observe_assistant_commit(previous, turn_ref="assistant:20", turn_index=20)

    state = runtime.observe_user_turn(
        "chết dở rồi eris ơi . tôi bị ngu",
        turn_ref="user:21",
        turn_index=21,
        previous_assistant_text=previous,
        previous_assistant_turn_ref="assistant:20",
        previous_assistant_turn_index=20,
    )

    assert state.repair_needed is True
    assert state.self_caused_reaction_probability >= 0.75
    assert state.causal_hypotheses[0].kind == "reaction_to_previous_eris_move"

    # Hard-cut invariant: observation không chứa/khôi phục control state cũ.
    payload = state.to_dict()
    for retired in (
        "next_move_owner",
        "continuity_pressure",
        "pending_shared_goal",
        "unresolved_intent",
        "conversation_move_hint",
    ):
        assert retired not in payload
    assert not hasattr(runtime, "repair_action_proposal")


def test_explicit_confusion_links_to_previous_eris_move_with_high_confidence() -> None:
    runtime = ConversationHandoffRuntime()
    previous = _abstract_eris_line()
    state = runtime.observe_user_turn(
        "là do tôi không hiểu em nói gì hết",
        turn_ref="user:23",
        previous_assistant_text=previous,
        previous_assistant_turn_ref="assistant:22",
    )
    assert state.user_stance == "confused"
    assert state.repair_needed is True
    assert state.self_caused_reaction_probability >= 0.90


def test_explicit_external_cause_does_not_become_fake_eris_self_cause() -> None:
    runtime = ConversationHandoffRuntime()
    state = runtime.observe_user_turn(
        "tôi bị ngu vì tôi làm sai bài toán",
        turn_ref="user:external",
        previous_assistant_text="Bạn ổn chứ?",
        previous_assistant_turn_ref="assistant:previous",
    )
    assert state.self_caused_reaction_probability < 0.35
    assert state.repair_needed is False
    assert any(item.kind == "external_or_unstated_cause" for item in state.causal_hypotheses)


def test_prompt_context_declares_handoff_as_evidence_only() -> None:
    runtime = ConversationHandoffRuntime()
    runtime.observe_user_turn(
        "tôi không hiểu câu em vừa nói",
        previous_assistant_text=_abstract_eris_line(),
        previous_assistant_turn_ref="assistant:1",
    )
    prompt = runtime.prompt_context()
    assert "evidence only" in prompt.lower()
    assert "not a planner" in prompt.lower()
    assert "required next speaker" in prompt.lower()
    assert "next_move_owner" not in prompt


def test_startup_primes_only_observation_and_never_commits_fake_public_turn() -> None:
    logs: list[str] = []
    state = SimpleNamespace(
        open_loops=("mục tiêu cũ từng tồn tại",),
        latest_observation={},
    )
    host = SimpleNamespace(
        _ai_startup_ran=False,
        ai_state_reducer=SimpleNamespace(snapshot=lambda: state),
        _chat_history_for_llm=lambda limit=8: [
            {"role": "user", "content": "đổi chủ đề mới mẻ hơn đi"},
            {"role": "assistant", "content": "tôi sẽ rẽ sang hướng khác"},
        ],
        logger=SimpleNamespace(
            info=lambda fmt, *args: logs.append(fmt % args if args else fmt),
            warning=lambda *args, **kwargs: None,
            debug=lambda *args, **kwargs: None,
        ),
    )
    runtime = AIAutonomousOrchestrator(host)
    runtime._commit_public = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("startup must never create a public commit")
    )

    assert runtime.run_startup() is False
    handoff = host.conversation_handoff_runtime.snapshot()
    assert handoff.last_user_text == "đổi chủ đề mới mẻ hơn đi"
    assert handoff.last_assistant_text == "tôi sẽ rẽ sang hướng khác"
    payload = handoff.to_dict()
    assert "pending_shared_goal" not in payload
    assert "next_move_owner" not in payload
    assert any("opening_signal=kernel" in line for line in logs)
    assert host.last_conversation_run["status"] == "no_reply"
