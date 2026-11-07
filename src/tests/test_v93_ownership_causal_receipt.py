# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from runtime.ai_orchestrated.contracts import CognitiveObservation, RuntimeMindState, StateEvent
from runtime.ai_orchestrated.state_reducer import StateReducer
from runtime.conversation_handoff import ConversationHandoffRuntime
from runtime.public_causal_receipt import (
    PublicCausalReceipt,
    build_public_causal_receipt,
    normalize_semantic_owner,
)


def test_owner_normalization_fails_closed_instead_of_guessing_user() -> None:
    assert normalize_semantic_owner("ERIS") == "eris"
    assert normalize_semantic_owner("shared") == "shared"
    assert normalize_semantic_owner("mystery-person") == "unknown"


def test_autonomous_observer_open_loop_is_owned_by_eris_and_not_promoted_to_shared_goal() -> None:
    observation = CognitiveObservation.from_mapping(
        {
            "event_kind": "autonomous",
            "user_act": "none",
            "relationship_meaning": "neutral",
            "confidence": 0.92,
            "open_loop": "ERIS tự hỏi vì sao mình cứ đẩy ranh giới ra xa.",
        },
        turn_id="auto:1",
        turn_seq=1,
        source="observer",
    )
    assert observation.open_loop_owner == "eris"

    reducer = StateReducer()
    reducer.apply_now(StateEvent.from_observation(observation, base_version=reducer.snapshot().version))
    state = reducer.snapshot()
    assert "ERIS tự hỏi" not in " ".join(state.open_loops)


def test_chat_observer_open_loop_remains_shared_continuity() -> None:
    observation = CognitiveObservation.from_mapping(
        {
            "event_kind": "chat",
            "user_act": "request",
            "relationship_meaning": "neutral",
            "confidence": 0.92,
            "open_loop": "User đang chờ ERIS trả lời phần kiểm tra repo.",
        },
        turn_id="chat:2",
        turn_seq=2,
        source="observer",
    )
    assert observation.open_loop_owner == "shared"

    reducer = StateReducer()
    reducer.apply_now(StateEvent.from_observation(observation, base_version=reducer.snapshot().version))
    assert reducer.snapshot().open_loops == ("User đang chờ ERIS trả lời phần kiểm tra repo.",)




def test_public_causal_receipt_keeps_self_generated_autonomous_origin() -> None:
    receipt = build_public_causal_receipt(
        channel="autonomous",
        speech_act="share_self_originated_thought",
        semantic_owner="eris",
        trigger_owner="eris",
        source_kind="expression_urge",
        source_ref="urge:boundary",
        motive="revisit",
        why_now="active concern resurfaced with enough pressure",
        user_triggered=False,
        confidence=0.78,
        evidence_refs=("concern:boundary",),
    )
    payload = receipt.to_dict()
    assert payload["semantic_owner"] == "eris"
    assert payload["trigger_owner"] == "eris"
    assert payload["user_triggered"] is False
    assert payload["source_kind"] == "expression_urge"


def test_handoff_keeps_last_public_causal_receipt_for_self_explanation() -> None:
    runtime = ConversationHandoffRuntime()
    receipt = PublicCausalReceipt(
        channel="autonomous",
        semantic_owner="eris",
        trigger_owner="eris",
        source_kind="expression_urge",
        source_ref="urge:boundary",
        motive="revisit",
        why_now="một concern cũ vừa trồi lại",
        user_triggered=False,
        confidence=0.82,
        speech_act="share_self_originated_thought",
        evidence_refs=("concern:boundary",),
        created_at=100.0,
    )
    state = runtime.observe_assistant_commit(
        "Tôi lại nhớ ra chuyện mình cứ đẩy ranh giới ra xa.",
        turn_ref="assistant:9",
        turn_index=9,
        speech_act="share_self_originated_thought",
        causal_receipt=receipt,
        now=100.0,
    )
    assert state.last_public_semantic_owner == "eris"
    assert state.last_public_causal_receipt["motive"] == "revisit"

    prompt = runtime.prompt_context()
    assert "PUBLIC_CAUSAL_RECEIPT" in prompt
    assert '"semantic_owner":"eris"' in prompt
    assert "provenance" in prompt.lower()


def test_user_why_question_marks_self_explanation_request_without_reassigning_owner() -> None:
    runtime = ConversationHandoffRuntime()
    receipt = build_public_causal_receipt(
        channel="autonomous",
        speech_act="share_self_originated_thought",
        semantic_owner="eris",
        trigger_owner="eris",
        source_kind="expression_urge",
        source_ref="urge:boundary",
        motive="revisit",
        why_now="active concern resurfaced",
        user_triggered=False,
        confidence=0.8,
    )
    runtime.observe_assistant_commit(
        "Tôi lại nhớ ra chuyện mình cứ đẩy ranh giới ra xa.",
        turn_ref="assistant:9",
        causal_receipt=receipt,
    )
    state = runtime.observe_user_turn(
        "ý là sao em lại nghĩ về đặt ranh giới, em đang muốn nói gì vậy?",
        turn_ref="user:10",
        previous_assistant_text="Tôi lại nhớ ra chuyện mình cứ đẩy ranh giới ra xa.",
        previous_assistant_turn_ref="assistant:9",
    )
    assert state.self_explanation_requested is True
    assert state.last_public_semantic_owner == "eris"


def test_chat_intention_plan_defaults_to_shared_semantics_triggered_by_user() -> None:
    from runtime.ai_orchestrated.contracts import IntentionPlan

    plan = IntentionPlan.from_mapping(
        {
            "speak": True,
            "motive": "respond_to_user",
            "semantics": {"speech_act": "answer", "subject_key": "current_user_turn"},
        },
        channel="chat",
    )
    assert plan.semantic_owner == "shared"
    assert plan.trigger_owner == "user"
    assert plan.user_triggered is True


def test_expression_urge_candidate_plan_carries_eris_ownership() -> None:
    from runtime.ai_orchestrated.initiative import InitiativeController
    from runtime.ai_orchestrated.social_candidate import SocialSpeechCandidate

    candidate = SocialSpeechCandidate(
        decision="candidate",
        kind="random_chat",
        topic="Tôi lại nghĩ tới một chuyện cũ.",
        speech_act="share_self_originated_thought",
        anchor_type="internal",
        anchor_id="concern:self:1",
        evidence_refs=("concern:self:1",),
        confidence=0.8,
        relevance=0.7,
        urgency=0.5,
        source="expression_urge",
        eris_motive_kind="want_to_revisit",
        eris_motive_why_now="một concern cũ vừa trồi lại",
    )
    plan, *_ = InitiativeController._plan_for_candidate(candidate, SimpleNamespace(now=100.0))
    assert plan.semantic_owner == "eris"
    assert plan.trigger_owner == "eris"
    assert plan.source_kind == "expression_urge"
    assert plan.source_ref == "concern:self:1"






def test_autonomous_open_loop_never_becomes_subject_shared_goal_context() -> None:
    from runtime.subject_initiative_runtime import build_initiative_runtime_context

    observation = CognitiveObservation.from_mapping(
        {
            "event_kind": "autonomous",
            "user_act": "none",
            "confidence": 0.95,
            "open_loop": "ERIS tự nhận cơ chế đẩy ranh giới.",
        },
        turn_id="auto:9",
        turn_seq=9,
        source="observer",
    )
    reducer = StateReducer()
    try:
        reducer.apply_now(StateEvent.from_observation(observation, base_version=reducer.snapshot().version))
        context = __import__("runtime.subject_initiative_runtime", fromlist=["build_initiative_runtime_context"]).build_initiative_runtime_context(
            SimpleNamespace(ai_state_reducer=reducer)
        )
        assert context["active_goal_text"] == ""
        assert context["active_goal_owner"] == "unknown"
    finally:
        reducer.close()
