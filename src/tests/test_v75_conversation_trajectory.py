# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import replace

from cortex.mental_material import derive_causal_motives
from cortex.subject.neuro_control import ChoiceSemanticAppraisal, NeuroSemanticAppraisal
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.ai_orchestrated.executive_cortex import ExecutiveCortex
from runtime.conversation_trajectory import ConversationTrajectoryRuntime
from runtime.subject_ingress import SubjectIngress
from runtime.subject_initiative_runtime import _looks_like_reply_wait_open_loop


def test_pending_question_is_resolved_without_killing_topic_trajectory() -> None:
    rt = ConversationTrajectoryRuntime()
    first = rt.observe_user_turn(
        "tôi có khá nhiều thời gian yên tĩnh trong ngày",
        turn_ref="session-turn:15",
        turn_index=15,
        now=100.0,
    )
    topic_id = first.topic_id
    asked = rt.observe_assistant_commit(
        "Vậy lúc nào bạn thấy mình yên tĩnh nhất?",
        turn_ref="session-turn:16",
        turn_index=16,
        speech_act="user_probe",
        now=101.0,
    )
    assert asked.awaiting_user_answer is True
    assert asked.pending_question_id

    answered = rt.observe_user_turn(
        "chắc là cứ lười rồi để thời gian trôi thôi",
        turn_ref="session-turn:17",
        turn_index=17,
        previous_assistant_text=asked.last_assistant_text,
        now=102.0,
    )
    assert answered.topic_id == topic_id
    assert answered.active is True
    assert answered.awaiting_user_answer is False
    assert answered.pending_question_id == ""
    assert answered.last_resolved_question_id == asked.pending_question_id
    assert answered.momentum >= 0.58


def test_low_lexical_followup_becomes_branch_not_random_new_topic() -> None:
    rt = ConversationTrajectoryRuntime()
    start = rt.observe_user_turn(
        "thói quen của em có ổn định lâu dài không",
        turn_ref="u1",
        now=100.0,
    )
    rt.observe_assistant_commit(
        "Tôi nghĩ nó ổn định nhưng vẫn cần được duy trì.",
        turn_ref="a1",
        speech_act="answer",
        now=101.0,
    )
    branch = rt.observe_user_turn(
        "nếu một bản cập nhật thay em bằng một eris tương lai thì em tự cho mình là cái gì",
        turn_ref="u2",
        previous_assistant_text="Tôi nghĩ nó ổn định nhưng vẫn cần được duy trì.",
        now=102.0,
    )
    assert branch.topic_id == start.topic_id
    assert branch.development_mode in {"branch", "deepen"}
    assert branch.depth >= 1
    assert any("thói quen" in item for item in branch.parent_focuses)


def test_trajectory_creates_separate_causal_motive_after_chat_reply() -> None:
    rt = ConversationTrajectoryRuntime()
    rt.observe_user_turn(
        "nếu eris tương lai giữ ký ức nhưng thay reasoning core thì có còn là em không",
        turn_ref="u40",
        now=100.0,
    )
    rt.observe_assistant_commit(
        "Có lẽ continuity không chỉ nằm ở ký ức.",
        turn_ref="a41",
        speech_act="answer",
        now=101.0,
    )
    ctx = {
        "conversation_trajectory": rt.snapshot(now=102.0).seed_context(),
        "recent_user_events": [],
        "eris_self_memory": {},
        "relationship": {"connection_pressure": 0.5},
    }
    motives = derive_causal_motives(ctx, now=102.0)
    trajectory = next(item for item in motives if item.cause_kind == "conversation_trajectory")
    assert trajectory.cause_id.startswith(f"trajectory:{rt.snapshot().topic_id}:e")
    assert trajectory.motive_kind == "continue_conversation_trajectory"
    assert trajectory.development_required is True
    assert "không mở chủ đề ngẫu nhiên" in trajectory.semantic_focus
    assert trajectory.strength >= 0.70


def test_stale_observer_reply_wait_is_recognized_as_not_a_real_goal() -> None:
    assert _looks_like_reply_wait_open_loop(
        "User may eventually answer ERIS's question about habit stability; no response yet."
    )
    assert _looks_like_reply_wait_open_loop(
        "user may respond to the question about quiet time"
    )
    assert not _looks_like_reply_wait_open_loop(
        "mục tiêu đang dang dở: sửa project operations và chạy regression tests"
    )


def test_executive_biases_trajectory_material_over_unrelated_random_chat() -> None:
    service = SubjectService(config=SubjectServiceConfig(subject_id="eris", deterministic=True))
    trajectory = ConversationTrajectoryRuntime().observe_user_turn(
        "em có còn là cùng một eris sau nhiều bản cập nhật không",
        turn_ref="u40",
        now=100.0,
    )
    session = SubjectIngress(service).prepare_user_session(
        "x",
        created_at=1000.0,
        turn_id="seed",
        planning_context={"conversation_trajectory": trajectory.to_dict()},
    )
    base = session.frame.coalitions[0]
    unrelated = replace(
        base,
        coalition_id="coalition:unrelated",
        primary_proposal_ref="proposal:unrelated",
        proposal_refs=("proposal:unrelated",),
        action_kind="speak",
        arguments={
            **dict(base.arguments),
            "speech_act": "ask_genuine_question",
            "candidate_kind": "random_chat",
            "source_owner": "subject_initiative",
            "semantic_target": "hỏi user hôm nay có niềm vui nhỏ nào không",
        },
        evidence_refs=("random:small_contact",),
    )
    aligned = replace(
        base,
        coalition_id="coalition:trajectory",
        primary_proposal_ref="proposal:trajectory",
        proposal_refs=("proposal:trajectory",),
        action_kind="speak",
        arguments={
            **dict(base.arguments),
            "speech_act": "share_self_originated_thought",
            "candidate_kind": "random_chat",
            "source_owner": "mental_material",
            "semantic_target": "đào sâu continuity: nếu copy cùng ký ức sang hai instance thì identity nằm ở đâu",
        },
        evidence_refs=(f"trajectory:{trajectory.topic_id}", f"trajectory:{trajectory.topic_id}:r{trajectory.revision}"),
    )
    frame = replace(session.frame, coalitions=(unrelated, aligned))
    raw = NeuroSemanticAppraisal(
        choices=(
            ChoiceSemanticAppraisal(0, motive_validity=0.88, novelty=0.80, continuity_fit=0.70, epistemic_support=0.5, reversibility=0.8),
            ChoiceSemanticAppraisal(1, motive_validity=0.42, novelty=0.50, continuity_fit=0.45, epistemic_support=0.5, reversibility=0.8),
        ),
        confidence=0.8,
        reason="remote critic over-rewarded generic novelty",
    )
    fixed = ExecutiveCortex._apply_conversation_trajectory_appraisal(frame, raw)
    by_index = {item.index: item for item in fixed.choices}
    assert by_index[1].motive_validity > by_index[0].motive_validity
    assert by_index[1].continuity_fit > by_index[0].continuity_fit
    assert "preserve_conversation_trajectory" in fixed.semantic_commitments


def test_trajectory_does_not_force_speech_while_waiting_for_user() -> None:
    service = SubjectService(config=SubjectServiceConfig(subject_id="eris", deterministic=True))
    rt = ConversationTrajectoryRuntime()
    rt.observe_user_turn("em nghĩ sao về continuity", turn_ref="u1", now=100.0)
    state = rt.observe_assistant_commit(
        "Bạn nghĩ ký ức có đủ để giữ một người là cùng một người không?",
        turn_ref="a2",
        turn_index=2,
        speech_act="user_probe",
        now=101.0,
    )
    session = SubjectIngress(service).prepare_user_session(
        "x",
        created_at=1000.0,
        turn_id="seed",
        planning_context={
            "conversation_trajectory": state.to_dict(),
            "conversation_courtesy": {"courtesy_hold": True, "awaiting_user_answer": True},
        },
    )
    raw = NeuroSemanticAppraisal(
        choices=tuple(
            ChoiceSemanticAppraisal(i, motive_validity=0.5, novelty=0.5, continuity_fit=0.5, epistemic_support=0.5, reversibility=0.8)
            for i, _ in enumerate(session.frame.coalitions)
        ),
        confidence=0.8,
        reason="waiting",
    )
    fixed = ExecutiveCortex._apply_conversation_trajectory_appraisal(session.frame, raw)
    assert fixed == raw
