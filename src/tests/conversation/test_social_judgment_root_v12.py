# -*- coding: utf-8 -*-
"""Kiểm thử P0–P2: EQ theo chứng cứ, Root là người duy nhất chọn cách nói."""
from __future__ import annotations

import time
from types import SimpleNamespace

from runtime.ai_orchestrated.contracts import CognitiveObservation, StateEvent, RuntimeMindState
from runtime.ai_orchestrated.identity import IdentityProfile, StrategyRecord
from runtime.ai_orchestrated.personality import ConsequenceAssessment
from runtime.ai_orchestrated.state_reducer import StateReducer
from runtime.conversation.agent import RuntimeModelRootAgent, root_step_schema, _bounded_context
from runtime.conversation.context import RootCapabilityRegistry
from runtime.conversation.context_assembler import ConversationContextAssembler
from runtime.conversation.interaction_choice import record_delivered_choice, record_user_reaction
from runtime.conversation.social_situation import project_social_situation
from runtime.conversation_handoff import ConversationHandoffRuntime
from runtime.source_grounding import SourceGroundingEngine, SourceGroundingReport


def _project(**changes):
    args = dict(event_kind="user_turn", user_text="Sao bạn cứ hỏi lỗi kỹ thuật?",
                session_id="s", recent_history=(), handoff={})
    args.update(changes)
    return project_social_situation(**args)


def test_root_prompt_influences_choice_but_does_not_add_a_required_form(monkeypatch):
    monkeypatch.setenv("ERIS_ROOT_INTERACTION_CHOICES", "0")
    prompt = RuntimeModelRootAgent._system_prompt(RootCapabilityRegistry({}))
    assert "PHÁN ĐOÁN XÃ HỘI CỦA ERIS" in prompt
    assert "không chỉ thêm câu đồng cảm" in prompt
    assert "Thiếu bằng chứng về nguyên nhân kỹ thuật" in prompt
    assert "interaction_choice" not in prompt
    move = root_step_schema()["oneOf"][0]
    assert set(move["required"]) == {"kind", "intent", "say", "delivery", "after"}
    assert "social_situation_evidence" not in move["properties"]
    assert len(root_step_schema()["oneOf"]) == 9  # Bản runtime hiện tại có thêm batch tool action.


def test_root_receives_social_evidence_without_rewriting_model_response(monkeypatch):
    monkeypatch.setenv("ERIS_ROOT_INTERACTION_CHOICES", "0")
    response = {"kind": "move", "intent": "cùng chia sẻ câu chuyện",
                "say": "Ừ, nghe kiểu đó khó chịu thật đấy.", "delivery": "tự nhiên", "after": "wait_user"}
    calls = []
    router = SimpleNamespace(executive_client=object(),
        invoke_structured_worker=lambda *_a, **kw: calls.append(kw) or response)
    from runtime.conversation.contracts import ConversationEvent
    event = ConversationEvent.user_turn(session_id="s", text="Tôi chỉ đang kể thôi")
    move = RuntimeModelRootAgent(router).decide(
        event=event, note="", recent_history=(),
        runtime_context={"social_situation_evidence": _project()})
    assert len(calls) == 1
    assert "social_situation_evidence" in calls[0]["payload"]["runtime_context"]
    assert move.say == response["say"]
    assert "social_situation_evidence" in _bounded_context({"social_situation_evidence": _project()})
    assert not hasattr(move, "social_situation_evidence")


def test_history_does_not_present_queued_or_failed_audio_as_spoken():
    evidence = _project(recent_history=(
        {"role": "assistant", "content": "Câu cũ đã nói", "delivery_status": "delivered"},
        {"role": "assistant", "content": "Câu mới chưa phát", "delivery_status": "queued_for_audio"},
    ))
    assert evidence["previous_assistant"]["excerpt"] == "Câu cũ đã nói"
    assert evidence["previous_assistant"]["delivery_confirmed"] is True
    unknown = _project(recent_history=({"role": "assistant", "content": "Không có receipt"},))
    assert unknown["previous_assistant"]["delivery_confirmed"] is False
    assert "previous_assistant" not in _project(recent_history=(
        {"role": "assistant", "content": "Lỗi TTS", "delivery_status": "delivery_failed"},
    ))
    assert "previous_assistant" not in _project(recent_history=(
        {"role": "assistant", "content": "Âm thanh mới bắt đầu", "delivery_status": "delivering"},
    ))


def test_stale_handoff_and_autonomous_do_not_receive_user_feedback_label():
    handoff = {"last_user_text": "Một câu từ lượt khác", "repair_needed": True,
               "user_stance": "approach_feedback", "causal_hypotheses": [{"kind": "stale"}]}
    assert "current_handoff" not in _project(handoff=handoff)
    assert project_social_situation(event_kind="continuation_due", user_text="nối tiếp",
                                    session_id="s", handoff=handoff) == {}
    assert _project(handoff={"last_user_text": "Sao bạn cứ hỏi lỗi kỹ thuật?",
                             "repair_needed": True, "user_stance": "approach_feedback"}
                    )["current_handoff"]["repair_signal"] is True


def test_handoff_observes_explicit_approach_correction_but_does_not_choose_speech():
    rt = ConversationHandoffRuntime()
    state = rt.observe_user_turn(
        "Sao bạn cứ hỏi lỗi kỹ thuật? Tôi đang kể chuyện mà!",
        turn_ref="user:2", turn_index=2, previous_assistant_text="Lỗi Kiro ở module nào?",
        previous_assistant_turn_ref="assistant:1", previous_assistant_turn_index=1)
    assert state.user_stance == "approach_feedback"
    assert state.repair_needed
    assert state.causal_hypotheses[0].kind == "reaction_to_eris_approach"
    assert state.causal_hypotheses[0].evidence_refs[0] == "user:2"
    assert "next_move_owner" not in state.to_dict()

    ordinary = ConversationHandoffRuntime().observe_user_turn(
        "Cho mình hỏi log Kiro nằm đâu?", previous_assistant_text="Lỗi gì?",
        turn_ref="user:3", turn_index=3)
    assert ordinary.user_stance != "approach_feedback"
    assert not ordinary.repair_needed


def _learned_state(*, confidence=.92, source="post_turn_observer"):
    record = StrategyRecord(experiences=({
        "assistant_ref": "delivery:1", "reaction_ref": "user:2",
        "session_id": "s", "settled_at": 1000.0,
        "situation": "user đang tâm sự", "goal": "phản hồi câu chuyện",
        "assistant_text": "Có lỗi gì, gửi log đây?",
        "reaction_text": "Tôi đang kể cho bạn nghe mà.",
        "outcome": "rejected", "confidence": confidence,
        "interpretation_source": source,
    },))
    return RuntimeMindState(identity=IdentityProfile(strategies={"root_move": record}))


def test_p2_quotes_scoped_cases_without_turning_them_into_user_preferences():
    result = _project(state=_learned_state())
    assert result["authority"] == "evidence_only_root_decides"
    assert len(result["past_interactions"]) == 1
    case = result["past_interactions"][0]
    assert case["assistant_said"] == "Có lỗi gì, gửi log đây?"
    assert case["user_replied"] == "Tôi đang kể cho bạn nghe mà."
    assert case["outcome_hypothesis"] == "rejected"
    assert case["provenance"] == "post_turn_observer"
    assert case["scope"] == "same_session"
    assert "permanent user preference" in result["past_interactions_scope"]
    assert not any(key.startswith("must_") for key in case)
    # P3: khác phiên và không giống tình huống thì KHÔNG kéo ký ức cũ
    # vào chỉ vì nó mới nhất. Bản P2 từng cho qua trường hợp này.
    cross = _project(state=_learned_state(), session_id="another")
    assert "past_interactions" not in cross


def test_p2_ignores_unverified_outcome_and_opt_out_learning():
    assert "past_interactions" not in _project(state=_learned_state(confidence=.3))
    assert "past_interactions" not in _project(state=_learned_state(source="guess"))
    state = RuntimeMindState(identity=_learned_state().identity, identity_learning_enabled=False)
    assert "past_interactions" not in _project(state=state)


def test_real_reducer_delivery_reaction_observation_returns_to_root_context():
    reducer = StateReducer()
    try:
        host = SimpleNamespace(ai_state_reducer=reducer)
        assert record_delivered_choice(host, choice={}, delivery_id="delivery:one",
                                       session_id="s", turn_seq=2,
                                       goal="hỏi lỗi kỹ thuật", assistant_text="Lỗi Kiro gì?",
                                       user_text="Tôi chỉ muốn kể chuyện")
        assert reducer.wait_idle()  # Hết commit thì mới có phản ứng user thực tế.
        now = time.time()
        assert record_user_reaction(host, session_id="s", turn_seq=3,
                                    turn_ref="user:3", user_text="Tôi chỉ đang kể mà.",
                                    now=now, previous_assistant_turn_seq=2)
        assert reducer.wait_idle()
        assessment = ConsequenceAssessment(strategy_use_id="delivery:one",
                                            context="story", outcome="rejected",
                                            reinforcement=-1, confidence=.9,
                                            evidence=("user:3",))
        observation = CognitiveObservation(turn_id="assistant:4", turn_seq=4,
                                           event_kind="chat", confidence=.9,
                                           consequence=assessment)
        assert reducer.submit(StateEvent.from_observation(observation))
        assert reducer.wait_idle()
        result = _project(state=reducer.snapshot())
        assert result["past_interactions"][0]["user_replied"] == "Tôi chỉ đang kể mà."
        assert result["past_interactions"][0]["outcome_hypothesis"] == "rejected"
        assert result["past_interactions"][0]["assessment_confidence"] == .9
    finally:
        reducer.close()


def test_grounding_separates_uncertain_external_cause_from_user_experience():
    engine = SourceGroundingEngine()
    report = SourceGroundingReport(user_text="tôi chỉ đang kể mà", primary_source="user_text", confidence_cap=.4,
                                  probe_allowed=True, missing_evidence=["file_evidence"])
    prompt = engine.render_prompt_context(report)
    assert "A probe is permitted, not required" in prompt
    assert "User's account grounds what they report" in prompt
    assert "Missing evidence forbids authoritative claims" in prompt
    rules = engine._derive_answer_style_rules("user_text", missing_evidence=["file_evidence"])
    assert any("không bắt buộc chỉ vì còn khoảng trống" in rule for rule in rules)


def test_context_assembler_preserves_mind_state_when_handoff_also_has_state():
    # Hồi quy: snapshot handoff không được ghi đè biến StateReducer trong assembler.
    state = _learned_state()
    previous = ConversationHandoffRuntime()
    previous.observe_user_turn(
        "Sao bạn cứ hỏi lỗi kỹ thuật?", turn_ref="user:5", turn_index=5,
        previous_assistant_text="Bạn bị lỗi ở file nào?", previous_assistant_turn_ref="assistant:4")
    host = SimpleNamespace(
        ai_state_reducer=SimpleNamespace(snapshot=lambda: state),
        conversation_handoff_runtime=previous,
        conversation_id="s", memory_kernel=None, logger=None,
    )
    context = ConversationContextAssembler(host).assemble(
        event=SimpleNamespace(kind="user_turn"), user_text="Sao bạn cứ hỏi lỗi kỹ thuật?",
        recent_history=({"role": "assistant", "content": "Bạn bị lỗi ở file nào?",
                         "delivery_status": "delivered"},),
    )
    social = context["social_situation_evidence"]
    assert social["current_handoff"]["repair_signal"] is True
    assert social["past_interactions"][0]["reaction_ref"] == "user:2"
    assert social["previous_assistant"]["delivery_confirmed"] is True
