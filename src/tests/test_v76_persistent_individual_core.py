# -*- coding: utf-8 -*-
"""Regression V76 Persistent Individual Core.

Các test ở đây cố tình đánh vào failure mode đã thấy trong log thật:
- assistant tự nuôi topic;
- hỏi user liên tục vì "tò mò";
- mọi câu nói đều tạo nợ trả lời;
- một relational goal chết sau đúng một câu;
- memory cũ bị hỏi lại từ đầu;
- startup luôn cư xử như phiên trắng.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cortex.subject.neuro_control import ChoiceSemanticAppraisal, NeuroSemanticAppraisal
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.ai_orchestrated.executive_cortex import ExecutiveCortex
from runtime.conversation_courtesy import ConversationCourtesyContract
from runtime.conversation_trajectory import ConversationTrajectoryRuntime
from runtime.interaction_ecology import (
    InteractionEcologyRuntime,
    MODE_ASK,
    MODE_COMMENT,
    MODE_MUTTER,
    MODE_SILENCE,
    RESPONSE_NONE,
    RESPONSE_INVITED,
)
from runtime.persistent_individual import PersistentIndividualRuntime
from runtime.relational_plan import RelationalPlanManager
from runtime.subject_ingress import SubjectIngress
from runtime.temporal_memory_policy import assess_memory_record


def test_assistant_cannot_self_recharge_topic_energy_or_public_budget() -> None:
    rt = ConversationTrajectoryRuntime()
    user = rt.observe_user_turn("nói tiếp chuyện identity của eris đi", turn_ref="u1", now=100.0)
    energy0 = user.energy
    budget0 = user.public_autonomous_budget
    first = rt.observe_assistant_commit(
        "Nếu cùng ký ức được copy sang hai instance thì memory có lẽ chưa đủ.",
        turn_ref="a1", autonomous=True, response_expectation="none", now=101.0,
    )
    second = rt.observe_assistant_commit(
        "Như vậy continuity cần thêm lịch sử causal chứ không chỉ snapshot.",
        turn_ref="a2", autonomous=True, response_expectation="none", now=102.0,
    )
    assert first.energy < energy0
    assert second.energy < first.energy
    assert first.public_autonomous_budget == budget0 - 1
    assert second.public_autonomous_budget == 0
    # Quan trọng nhất: assistant commit không reset budget theo revision.
    assert second.revision > first.revision
    assert second.public_autonomous_budget < first.public_autonomous_budget


def test_private_development_can_die_without_becoming_public_speech() -> None:
    rt = ConversationTrajectoryRuntime()
    rt.observe_user_turn("một chủ đề nhỏ thôi", turn_ref="u1", now=100.0)
    states = []
    for i in range(6):
        states.append(rt.observe_private_development(semantic_gain=0.05, now=101.0 + i))
    final = states[-1]
    assert final.private_budget == 0
    assert final.private_development_count >= 6
    assert final.lifecycle_state in {"simmering", "satiated"}
    # Private cognition không hề tạo assistant turn hay pending question.
    assert final.last_assistant_text == ""
    assert final.awaiting_user_answer is False


def test_response_expectation_none_does_not_create_reply_debt_even_with_question_mark() -> None:
    courtesy = ConversationCourtesyContract(timeout_seconds=180)
    state = courtesy.observe_assistant_commit(
        "Compiler lại giận rồi nhỉ?", speech_act="ambient_mutter",
        response_expectation=RESPONSE_NONE, turn_ref="a1", now=100.0,
    )
    assert state.awaiting_user_answer is False
    assert courtesy.runtime_context(now=101.0)["courtesy_hold"] is False


def test_question_fatigue_makes_third_question_far_less_competitive_than_comment() -> None:
    ecology = InteractionEcologyRuntime()
    ecology.observe_user_turn(now=100.0)
    ecology.observe_public_action(mode=MODE_ASK, response_expectation=RESPONSE_INVITED, autonomous=True, now=101.0)
    ecology.observe_public_action(mode=MODE_ASK, response_expectation=RESPONSE_INVITED, autonomous=True, now=102.0)
    ask = ecology.bias_candidate(
        speech_act="user_probe", candidate_kind="user_probe", text="Anh làm nghề gì?",
        autonomous=True, seconds_since_user=120.0,
    )
    comment = ecology.bias_candidate(
        speech_act="ambient_comment", candidate_kind="commentary", text="Cái build này dai thật.",
        autonomous=True, seconds_since_user=120.0,
    )
    assert ask.mode == MODE_ASK
    assert ask.multiplier < 0.20
    assert comment.mode == MODE_COMMENT
    assert comment.multiplier > ask.multiplier


def test_mood_lift_is_multi_turn_plan_and_avoids_interrogation() -> None:
    manager = RelationalPlanManager()
    plan = manager.observe_user_turn("làm tôi vui lên", turn_ref="u1", now=100.0)
    assert plan is not None and plan.goal_kind == "mood_lift"
    view = manager.projection(now=101.0)
    assert view["active"] is True
    assert MODE_ASK in view["avoid_modes"]
    assert MODE_MUTTER in view["effective_preferred_modes"]
    # Hai ambient action không có user reply vẫn không làm plan biến mất.
    manager.observe_public_action(mode=MODE_COMMENT, autonomous=True, now=102.0)
    manager.observe_public_action(mode=MODE_MUTTER, autonomous=True, now=103.0)
    manager.note_user_outcome("no_response", now=104.0)
    view2 = manager.projection(now=105.0)
    assert view2["active"] is True
    assert view2["status"] in {"active", "simmering"}
    assert view2["autonomous_attempts"] == 2


def test_old_volatile_preference_is_remembered_softly_not_reset_to_unknown() -> None:
    year = 365.0 * 86400.0
    record = {
        "id": "food-1",
        "path": "preferences.food.favorite",
        "value": "phở",
        "confidence": 0.94,
        "last_confirmed_at": 100.0,
        "updated_at": 100.0,
        "metadata": {},
    }
    assessment = assess_memory_record(record, now=100.0 + year)
    assert assessment.age_days >= 364.0
    assert assessment.use_mode in {"soft_recall", "reconfirm_if_relevant"}
    assert assessment.use_mode != "do_not_assume"


def test_stable_identity_memory_ages_more_slowly_than_food_preference() -> None:
    year = 365.0 * 86400.0
    food = assess_memory_record({
        "path": "preferences.food", "confidence": 0.95,
        "last_confirmed_at": 100.0,
    }, now=100.0 + year)
    name = assess_memory_record({
        "path": "identity.name", "confidence": 0.95,
        "last_confirmed_at": 100.0,
    }, now=100.0 + year)
    assert name.volatility < food.volatility
    assert name.freshness > food.freshness
    assert name.stale_probability < food.stale_probability


def test_persistent_individual_roundtrip_and_learning_flags_default_off(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ERIS_V76_SELF_MODEL_LEARNING", raising=False)
    monkeypatch.delenv("ERIS_V76_HABIT_LEARNING", raising=False)
    path = tmp_path / "state.json"
    rt = PersistentIndividualRuntime(path=path)
    rt.observe_user_turn("làm tôi vui lên", turn_ref="u1", context_key="late_night_code", now=100.0)
    result = rt.observe_assistant_commit(
        "Cái compiler này có vẻ ghét cả hai chúng ta.",
        speech_act="ambient_comment", autonomous=True, context_key="late_night_code", now=101.0,
    )
    assert result["response_expectation"] == RESPONSE_NONE
    assert path.is_file()
    rt2 = PersistentIndividualRuntime(path=path)
    view = rt2.projection(now=102.0, context_key="late_night_code")
    assert view["relational_plan"]["active"] is True
    assert view["relational_plan"]["goal_kind"] == "mood_lift"
    assert view["learning_flags"] == {"self_model_learning": False, "habit_learning": False}


def test_curiosity_values_observation_over_asking_when_answer_can_be_inferred(tmp_path: Path) -> None:
    rt = PersistentIndividualRuntime(path=tmp_path / "state.json")
    low = rt.curiosity_ask_value(
        epistemic_value=0.8, future_usefulness=0.5, relationship_fit=0.5,
        can_observe_instead=True, can_infer_instead=True, memory_already_has_answer=True,
    )
    high = rt.curiosity_ask_value(
        epistemic_value=0.95, future_usefulness=0.95, relationship_fit=0.9,
        can_observe_instead=False, can_infer_instead=False, memory_already_has_answer=False,
    )
    assert low < 0.0
    assert high > low


def test_startup_appraisal_can_choose_contextual_mutter_or_silence(tmp_path: Path, monkeypatch) -> None:
    rt = PersistentIndividualRuntime(path=tmp_path / "state.json")
    # 2024-01-01 02:00 local nếu TZ test runner là UTC; monkeypatch localtime để test deterministic.
    class _T:
        tm_hour = 2
    monkeypatch.setattr("runtime.persistent_individual.time.localtime", lambda _now: _T())
    view = rt.startup_appraisal(now=1000.0, reentry_gap_seconds=7200.0)
    assert view["late_night"] is True
    assert view["avoid_generic_greeting"] is True
    assert MODE_MUTTER in view["opening_modes"]
    assert MODE_SILENCE in view["opening_modes"]


def test_executive_interaction_ecology_penalizes_ask_and_rewards_silence() -> None:
    service = SubjectService(config=SubjectServiceConfig(subject_id="eris", deterministic=True))
    persistent = {
        "interaction_ecology": {
            "question_fatigue": 0.92,
            "public_fatigue": 0.80,
            "unsolicited_streak": 2,
            "questions_since_user_contribution": 2,
            "mode_habituation": {"ask": 0.65},
        },
        "relational_plan": {
            "active": True,
            "effective_preferred_modes": ["comment", "mutter", "silence"],
            "avoid_modes": ["ask"],
        },
        "operational_self_model": {"over_questioning": 0.70},
    }
    session = SubjectIngress(service).prepare_user_session(
        "x", created_at=1000.0, turn_id="seed",
        planning_context={"persistent_individual": persistent},
    )
    base = session.frame.coalitions[0]
    ask = replace(
        base, coalition_id="coalition:ask", primary_proposal_ref="proposal:ask",
        proposal_refs=("proposal:ask",), action_kind="speak",
        arguments={**dict(base.arguments), "speech_act": "user_probe", "candidate_kind": "user_probe", "semantic_target": "Anh làm nghề gì?"},
    )
    silence = replace(
        base, coalition_id="coalition:silence", primary_proposal_ref="proposal:silence",
        proposal_refs=("proposal:silence",), action_kind="silence",
        arguments={**dict(base.arguments), "speech_act": "silence", "candidate_kind": "silence", "semantic_target": ""},
    )
    frame = replace(session.frame, coalitions=(ask, silence))
    raw = NeuroSemanticAppraisal(
        choices=(
            ChoiceSemanticAppraisal(0, motive_validity=0.80, novelty=0.7, continuity_fit=0.7, epistemic_support=0.6, reversibility=0.8),
            ChoiceSemanticAppraisal(1, motive_validity=0.45, novelty=0.2, continuity_fit=0.5, epistemic_support=1.0, reversibility=1.0),
        ),
        confidence=0.8, reason="test",
    )
    fixed = ExecutiveCortex._apply_interaction_ecology_appraisal(frame, raw)
    by_idx = {x.index: x for x in fixed.choices}
    assert by_idx[0].motive_validity < 0.20
    assert by_idx[1].motive_validity > 0.45
    assert "questions_are_not_default_connection" in fixed.semantic_commitments


def test_small_contact_is_ambient_mutter_not_an_implicit_question() -> None:
    from runtime.interaction_ecology import classify_interaction_mode, response_expectation_for_mode
    mode = classify_interaction_mode(
        speech_act="ambient_mutter", candidate_kind="random_chat",
        action_kind="speak", text="Cái build này lì thật.",
    )
    assert mode == MODE_MUTTER
    assert response_expectation_for_mode(mode, speech_act="ambient_mutter") == RESPONSE_NONE


def test_story_fragment_exists_as_imagination_material_not_fake_memory() -> None:
    from cortex.mental_material import MATERIAL_KINDS, MATERIAL_TO_EPISTEMIC
    assert "story_fragment" in MATERIAL_KINDS
    assert MATERIAL_TO_EPISTEMIC["story_fragment"] == "imagination"
