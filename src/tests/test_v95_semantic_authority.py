# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from cortex.subject.expression_commitment import commitment_payload
from cortex.subject.neuro_control import (
    ChoiceSemanticAppraisal,
    NeuroActionSelector,
    NeuroSemanticAppraisal,
)
from cortex.subject.service import SubjectService, SubjectServiceConfig
from runtime.subject_ingress import SubjectIngress


class _Determinism:
    def __init__(self) -> None:
        self.now = 1000.0
        self.n = 0

    def clock(self) -> float:
        self.now += 0.01
        return self.now

    def id(self) -> str:
        self.n += 1
        return f"id-{self.n}"


def _base_frame():
    d = _Determinism()
    service = SubjectService(
        config=SubjectServiceConfig(subject_id="eris", deterministic=True),
        clock=d.clock,
        id_factory=d.id,
    )
    session = SubjectIngress(service).prepare_user_session(
        "test", created_at=d.clock(), turn_id="turn-v95"
    )
    return d, service, session.frame


def _autonomous_frame(*, goal_state: str, goal_owner: str, goal_text: str):
    _d, _service, frame = _base_frame()
    assert len(frame.coalitions) >= 2
    speak = replace(
        frame.coalitions[0],
        action_kind="speak",
        score=1.25,
        arguments={
            **dict(frame.coalitions[0].arguments),
            "speech_act": "resume_shared_goal",
            "source_owner": "subject_initiative",
            "candidate_kind": "followup_reminder",
            "semantic_target": "continue the pending shared goal",
            "expression_commitment": commitment_payload(
                origin="subject_initiative",
                source_id="intention:goal-1",
                semantic_target="continue the pending shared goal",
                motive="contribute",
                pressure=0.9,
                freshness=0.9,
                persistence=0.9,
            ),
        },
    )
    silence = replace(
        frame.coalitions[1],
        action_kind="silence",
        score=0.35,
        arguments={**dict(frame.coalitions[1].arguments)},
    )
    runtime_context = {
        "initiative_runtime_context": {
            "active_goal_text": goal_text,
            "active_goal_owner": goal_owner,
            "active_goal_state": goal_state,
            "active_goal_id": "goal-1" if goal_text else "",
        },
    }
    observation = replace(
        frame.observation,
        kind="autonomous_opportunity",
        context={"runtime_context": runtime_context},
    )
    return replace(frame, observation=observation, coalitions=(speak, silence))


def test_v95_stale_resume_shared_goal_is_removed_before_neuro_competition() -> None:
    from runtime.semantic_authority_v95 import validate_autonomous_motives

    frame = _autonomous_frame(goal_state="suspended", goal_owner="shared", goal_text="Continue repo review")
    result = validate_autonomous_motives(frame)

    assert result.changed is True
    assert len(result.frame.coalitions) == 1
    assert result.frame.coalitions[0].action_kind == "silence"
    assert result.rejections[0].code == "resume_goal_not_active"


def test_v95_self_owned_goal_cannot_masquerade_as_shared_goal() -> None:
    from runtime.semantic_authority_v95 import validate_autonomous_motives

    frame = _autonomous_frame(goal_state="active", goal_owner="eris", goal_text="My private concern")
    result = validate_autonomous_motives(frame)

    assert result.changed is True
    assert [item.action_kind for item in result.frame.coalitions] == ["silence"]
    assert result.rejections[0].code == "resume_goal_owner_not_shared"


def test_v95_active_user_goal_survives_lifecycle_validation() -> None:
    from runtime.semantic_authority_v95 import validate_autonomous_motives

    frame = _autonomous_frame(goal_state="active", goal_owner="user", goal_text="Continue repo review")
    result = validate_autonomous_motives(frame)

    assert result.changed is False
    assert tuple(item.coalition_id for item in result.frame.coalitions) == tuple(
        item.coalition_id for item in frame.coalitions
    )


def test_v95_semantic_critic_can_defer_committed_speech_at_log_level_confidence() -> None:
    frame = _autonomous_frame(goal_state="active", goal_owner="shared", goal_text="Continue repo review")
    selector = NeuroActionSelector()
    baseline = selector.select(frame)
    # Tái tạo pattern log 14:36: motive cực yếu, continuity vừa, evidence yếu,
    # confidence chỉ 0.62. V94 threshold 0.65 khiến fast pressure vẫn ép SPEAK.
    appraisal = NeuroSemanticAppraisal(
        choices=(
            ChoiceSemanticAppraisal(
                index=0,
                motive_validity=0.087,
                novelty=0.38,
                continuity_fit=0.395,
                epistemic_support=0.28,
                reversibility=0.78,
            ),
            ChoiceSemanticAppraisal(
                index=1,
                motive_validity=0.87,
                novelty=0.12,
                continuity_fit=0.82,
                epistemic_support=0.40,
                reversibility=0.94,
            ),
        ),
        confidence=0.62,
        reason="critic says motive is too weak for public speech",
    )

    merged = selector.merge_semantic_appraisal(frame, baseline, appraisal)

    assert merged.winner(frame).action_kind == "silence"
    assert merged.reason.startswith("semantic_critique_deferred_commitment:v95_authority:")


def test_v95_low_confidence_critic_does_not_gain_hard_defer_authority() -> None:
    frame = _autonomous_frame(goal_state="active", goal_owner="shared", goal_text="Continue repo review")
    selector = NeuroActionSelector()
    baseline = selector.select(frame)
    appraisal = NeuroSemanticAppraisal(
        choices=(
            ChoiceSemanticAppraisal(index=0, motive_validity=0.10, continuity_fit=0.20, epistemic_support=0.15),
            ChoiceSemanticAppraisal(index=1, motive_validity=0.80, continuity_fit=0.80, epistemic_support=0.60),
        ),
        confidence=0.30,
        reason="too uncertain",
    )

    merged = selector.merge_semantic_appraisal(frame, baseline, appraisal)

    # Critic confidence thấp chỉ modulation; không được tự biến thành veto cứng.
    assert "v95_authority" not in merged.reason


def test_v95_strong_semantic_support_keeps_valid_committed_speech() -> None:
    frame = _autonomous_frame(goal_state="active", goal_owner="shared", goal_text="Continue repo review")
    selector = NeuroActionSelector()
    baseline = selector.select(frame)
    appraisal = NeuroSemanticAppraisal(
        choices=(
            ChoiceSemanticAppraisal(index=0, motive_validity=0.92, continuity_fit=0.94, epistemic_support=0.82, reversibility=0.90),
            ChoiceSemanticAppraisal(index=1, motive_validity=0.30, continuity_fit=0.30, epistemic_support=0.50, reversibility=0.95),
        ),
        confidence=0.82,
        reason="speech is well motivated and grounded",
    )

    merged = selector.merge_semantic_appraisal(frame, baseline, appraisal)

    assert merged.winner(frame).action_kind == "speak"
    assert "v95_authority" not in merged.reason


def test_v95_executive_never_commits_stale_resume_goal_even_when_fast_score_is_high() -> None:
    from runtime.ai_orchestrated.executive_cortex import ExecutiveCortex

    frame = _autonomous_frame(goal_state="suspended", goal_owner="shared", goal_text="Continue repo review")
    # SPEAK có score cao hơn nhiều nhưng lifecycle validator phải loại nó trước Neuro.
    assert frame.coalitions[0].score > frame.coalitions[1].score
    executive = ExecutiveCortex(
        SimpleNamespace(),
        None,
        invoke_structured=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale motive must not reach remote semantic deliberation")
        ),
    )

    commit = executive.commit_subject_frame(frame, mode="autonomous", allow_remote_deliberation=False)

    assert commit is not None
    assert commit.selected_coalition_id == frame.coalitions[1].coalition_id
    assert commit.source == "neuro_fast_gate"
