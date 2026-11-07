# -*- coding: utf-8 -*-
"""Regression V77: Continuous Self Dynamics.

Các test này tập trung vào invariant, không test prose. Nếu invariant vỡ thì ERIS
có thể quay lại kiểu "mọi private thought đều xếp hàng xin micro" rất nhanh.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.ai_orchestrated.executive_cortex import (
    ChoiceSemanticAppraisal, ExecutiveCortex, NeuroSemanticAppraisal,
)
from runtime.continuous_self_dynamics import ContinuousSelfDynamics, ContinuousSelfState
from runtime.persistent_individual import PersistentIndividualRuntime
from runtime.subject_ingress import SubjectIngress


def _plan() -> dict:
    return {
        "active": True,
        "plan_id": "plan:mood:1",
        "goal_kind": "mood_lift",
        "objective": "Giúp không khí nhẹ hơn mà không ép user phản hồi.",
        "source_ref": "u1",
        "progress": 0.10,
        "expires_at": 100.0 + 5400.0,
        "effective_preferred_modes": ["tease", "mutter", "comment", "silence"],
        "avoid_modes": ["ask"],
        "status": "active",
    }


def test_user_commitment_survives_multiple_ticks_without_being_recreated() -> None:
    rt = ContinuousSelfDynamics(ContinuousSelfState(created_at=100.0, updated_at=100.0))
    rt.observe_user_turn("làm tôi vui lên", turn_ref="u1", relational_plan=_plan(), now=100.0)
    first = rt.projection(now=100.0)["active_commitment"]
    assert first["goal_kind"] == "mood_lift"
    commitment_id = first["commitment_id"]
    strength0 = first["strength"]

    rt.tick(now=700.0, relational_plan=_plan(), interaction_ecology={"public_fatigue": 0.0})
    rt.tick(now=1300.0, relational_plan=_plan(), interaction_ecology={"public_fatigue": 0.0})
    second = rt.projection(now=1300.0)["active_commitment"]
    assert second["commitment_id"] == commitment_id
    # Đọc lại plan mỗi tick không được cộng strength vô hạn.
    assert second["strength"] < strength0


def test_assistant_public_speech_spends_pressure_and_never_recharges_commitment() -> None:
    rt = ContinuousSelfDynamics(ContinuousSelfState(created_at=100.0, updated_at=100.0))
    rt.observe_user_turn("làm tôi vui lên", turn_ref="u1", relational_plan=_plan(), now=100.0)
    rt.tick(
        now=101.0,
        relational_plan=_plan(),
        persistent_internal={"social_pressure": 0.8},
        interaction_ecology={"ambient_appetite": 0.7},
    )
    before = rt.projection(now=101.0)
    strength0 = before["active_commitment"]["strength"]
    pressure0 = before["public_action_pressure"]

    rt.observe_public_action(mode="mutter", autonomous=True, response_expectation="none", now=102.0)
    rt.observe_public_action(mode="comment", autonomous=True, response_expectation="none", now=103.0)
    after = rt.projection(now=103.0)
    assert after["active_commitment"]["strength"] <= strength0
    assert after["active_commitment"]["public_attempts"] == 2
    assert after["public_action_pressure"] < pressure0


def test_private_thought_can_settle_without_any_public_surface() -> None:
    rt = ContinuousSelfDynamics(ContinuousSelfState(created_at=100.0, updated_at=100.0))
    for i in range(6):
        rt.observe_private_development(
            summary="Cùng một ý được diễn đạt lại, không có semantic gain đáng kể.",
            material_kind="reflection",
            cause_kind="active_concern",
            source_refs=("concern.abc",),
            semantic_gain=0.05,
            topic_id="identity",
            now=101.0 + i,
        )
    view = rt.projection(now=108.0)
    thought = next(x for x in rt.state.unfinished_thoughts if x.topic_id == "identity")
    assert thought.status in {"settled", "dormant"}
    assert thought.surfaced_count == 0
    assert view["private_cognition_pressure"] >= 0.0


def test_two_hours_of_silence_changes_state_instead_of_freezing_snapshot() -> None:
    state = ContinuousSelfState(
        created_at=100.0, updated_at=100.0,
        public_action_pressure=0.70, private_cognition_pressure=0.65,
    )
    rt = ContinuousSelfDynamics(state)
    rt.observe_private_development(
        summary="Một ý chưa xong", material_kind="reflection", semantic_gain=0.35,
        topic_id="t1", now=101.0,
    )
    p1 = rt.projection(now=101.0)
    rt.tick(now=101.0 + 7200.0, interaction_ecology={"public_fatigue": 0.0})
    p2 = rt.projection(now=7301.0)
    assert p2["revision"] > p1["revision"]
    assert p2["public_action_pressure"] != p1["public_action_pressure"]
    assert p2["top_unfinished_thought"].get("pressure", 0.0) < p1["top_unfinished_thought"].get("pressure", 1.0)


def test_private_pressure_can_be_high_while_public_pressure_is_low() -> None:
    rt = ContinuousSelfDynamics(ContinuousSelfState(created_at=100.0, updated_at=100.0))
    rt.observe_private_development(
        summary="Một vấn đề cần nghĩ thêm nhưng không cần nói ngay.",
        semantic_gain=0.85, topic_id="deep", now=101.0,
    )
    for i in range(3):
        rt.observe_public_action(mode="comment", autonomous=True, now=102.0 + i)
    rt.tick(
        now=106.0,
        persistent_internal={"cognitive_tension": 0.95, "fatigue": 0.75, "restraint_pressure": 0.8},
        interaction_ecology={"public_fatigue": 0.95, "unsolicited_streak": 3},
    )
    view = rt.projection(now=106.0)
    assert view["private_cognition_pressure"] > view["public_action_pressure"]
    assert view["orientation"] in {"quiet_recovery", "quiet_presence", "private_concern", "reflective"}


def test_persistent_individual_roundtrip_keeps_continuous_self(tmp_path: Path) -> None:
    path = tmp_path / "individual.json"
    rt = PersistentIndividualRuntime(path=path)
    rt.observe_user_turn("làm tôi vui lên", turn_ref="u1", now=100.0)
    rt.observe_private_development(
        summary="Có thể dùng một comment màn hình thay vì hỏi user.",
        material_kind="reflection", semantic_gain=0.72, topic_id="company", now=101.0,
    )
    first = rt.projection(now=102.0)["continuous_self"]
    assert first["active_commitment"]["goal_kind"] == "mood_lift"
    assert first["top_unfinished_thought"]

    rt2 = PersistentIndividualRuntime(path=path)
    second = rt2.projection(now=103.0)["continuous_self"]
    assert second["active_commitment"]["commitment_id"] == first["active_commitment"]["commitment_id"]
    assert second["top_unfinished_thought"]["thought_id"] == first["top_unfinished_thought"]["thought_id"]


def test_executive_continuous_self_rewards_silence_when_public_pressure_low() -> None:
    service = SubjectService(config=SubjectServiceConfig(subject_id="eris", deterministic=True))
    persistent = {
        "interaction_ecology": {"question_fatigue": 0.0, "public_fatigue": 0.0},
        "relational_plan": {"active": False},
        "operational_self_model": {},
        "continuous_self": {
            "orientation": "reflective",
            "public_action_pressure": 0.08,
            "private_cognition_pressure": 0.86,
            "social_availability": 0.55,
            "active_commitment": {},
        },
    }
    session = SubjectIngress(service).prepare_autonomous_session(
        {}, (), created_at=1000.0, turn_id="auto-v77",
        planning_context={"persistent_individual": persistent},
    )
    base = session.frame.coalitions[0]
    speak = replace(
        base, coalition_id="coalition:speak", primary_proposal_ref="proposal:speak",
        proposal_refs=("proposal:speak",), action_kind="speak",
        arguments={**dict(base.arguments), "speech_act": "share_self_originated_thought", "candidate_kind": "inner_thought", "semantic_target": "Tôi nghĩ ra thêm một ý."},
    )
    silence = replace(
        base, coalition_id="coalition:silence", primary_proposal_ref="proposal:silence",
        proposal_refs=("proposal:silence",), action_kind="silence",
        arguments={**dict(base.arguments), "speech_act": "silence", "candidate_kind": "silence", "semantic_target": ""},
    )
    frame = replace(session.frame, coalitions=(speak, silence))
    raw = NeuroSemanticAppraisal(
        choices=(
            ChoiceSemanticAppraisal(0, motive_validity=0.75, novelty=0.7, continuity_fit=0.7, epistemic_support=0.7, reversibility=0.7),
            ChoiceSemanticAppraisal(1, motive_validity=0.40, novelty=0.1, continuity_fit=0.5, epistemic_support=1.0, reversibility=1.0),
        ), confidence=0.8, reason="test",
    )
    fixed = ExecutiveCortex._apply_continuous_self_appraisal(frame, raw)
    by_idx = {x.index: x for x in fixed.choices}
    assert by_idx[0].motive_validity < 0.50
    assert by_idx[1].motive_validity > 0.65
    assert "private_cognition_does_not_imply_public_speech" in fixed.semantic_commitments


def test_direct_user_turn_is_not_suppressed_by_simulated_self_state() -> None:
    service = SubjectService(config=SubjectServiceConfig(subject_id="eris", deterministic=True))
    session = SubjectIngress(service).prepare_user_session(
        "giúp tôi sửa lỗi này", created_at=1000.0, turn_id="u1",
        planning_context={"persistent_individual": {"continuous_self": {"public_action_pressure": 0.0, "orientation": "quiet_recovery"}}},
    )
    raw = NeuroSemanticAppraisal(
        choices=tuple(
            ChoiceSemanticAppraisal(i, motive_validity=0.8, novelty=0.5, continuity_fit=0.8, epistemic_support=0.8, reversibility=0.8)
            for i in range(len(session.frame.coalitions))
        ), confidence=0.8, reason="test",
    )
    fixed = ExecutiveCortex._apply_continuous_self_appraisal(session.frame, raw)
    assert fixed == raw


def test_continuous_self_can_materialize_one_plan_step_candidate_without_question() -> None:
    from runtime.subject_initiative_runtime import _continuous_self_affordance
    ctx = {
        "continuous_self": {
            "orientation": "playful_company",
            "public_action_pressure": 0.72,
            "private_cognition_pressure": 0.30,
            "continuity_strength": 0.76,
            "active_commitment": {
                "commitment_id": "commitment:abc",
                "goal_kind": "mood_lift",
                "preferred_modes": ["mutter", "comment", "silence"],
                "avoid_modes": ["ask"],
            },
            "top_unfinished_thought": {},
        }
    }
    affordance = _continuous_self_affordance(ctx, revision=3, now=100.0)
    assert affordance is not None
    assert affordance.owner == "continuous_self"
    assert affordance.candidate.speech_act == "ambient_mutter"
    assert "question" in affordance.candidate.topic.casefold()
    assert affordance.candidate.eris_motive_kind == "carry_self_state"


def test_continuous_self_does_not_materialize_public_candidate_when_pressure_low() -> None:
    from runtime.subject_initiative_runtime import _continuous_self_affordance
    affordance = _continuous_self_affordance({
        "continuous_self": {
            "orientation": "reflective",
            "public_action_pressure": 0.16,
            "private_cognition_pressure": 0.91,
            "active_commitment": {"commitment_id": "c1", "goal_kind": "mood_lift"},
        }
    }, revision=1, now=100.0)
    assert affordance is None
