# -*- coding: utf-8 -*-
"""Regression cho refactor native Expression Commitment V66."""
from __future__ import annotations

from types import SimpleNamespace

from cortex.subject.expression_commitment import commitment_payload, expression_rhythm
from cortex.subject.initiative_control import InitiativeArbiter, InitiativeControlAction
from cortex.subject.neuro_control import (
    ChoiceSemanticAppraisal,
    NeuroActionSelector,
    NeuroSemanticAppraisal,
    SelectionResult,
    WorkspaceBroadcast,
)


def _intention():
    return {
        "intention_id": "intention:self:v66",
        "motive_kind": "share",
        "semantic_goal": "nói một điều Eris thực sự đang muốn biểu đạt",
        "target": "self_expression",
        "pressure": 0.44,
        "commitment_strength": 0.51,
        "created_at": 100.0,
        "status": "pending",
    }


def _coalition(action: str, score: float, args: dict | None = None):
    return SimpleNamespace(
        action_kind=action,
        score=score,
        expected_pragmatic_value=0.5,
        expected_epistemic_value=0.3,
        uncertainty=0.2,
        arguments=dict(args or {}),
        proposal_refs=(),
    )


def _frame(coalitions, runtime=None):
    return SimpleNamespace(
        coalitions=tuple(coalitions),
        appraisals=(), activations=(), proposals=(),
        substrate=SimpleNamespace(processing_capacity=1.0),
        observation=SimpleNamespace(context={"runtime_context": dict(runtime or {})}, origin="internal"),
    )


def test_quiet_busy_and_unanswered_are_not_expression_boundaries():
    decision = InitiativeArbiter().decide(
        _intention(),
        relational={"interruption_cost": 0.99, "social_curiosity": 0.1},
        runtime_context={
            "user_response_rate": 0.0,
            "recent_unsolicited_speech": 1.0,
            "last_public_turn_unanswered": True,
            "startup_social_cooldown_active": True,
            "seconds_since_last_autonomous_speech": 100.0,
        },
        now=130.0,
        contentful=True,
    )
    assert decision.action is InitiativeControlAction.SPEAK
    assert decision.reason == "self_owned_expression"


def test_hard_boundary_still_defers_expression():
    decision = InitiativeArbiter().decide(
        _intention(), relational={},
        runtime_context={"explicit_quiet_request": True, "seconds_since_last_autonomous_speech": 100.0},
        now=130.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.WAIT
    assert decision.reason == "hard_boundary:explicit_quiet_request"


def test_self_rhythm_defers_without_reading_user_response():
    rhythm = expression_rhythm({
        "seconds_since_last_autonomous_speech": 7.0,
        "autonomous_expression_streak": 4,
        "user_response_rate": 0.0,
    })
    assert rhythm.allow is False
    assert rhythm.reason == "self_rhythm_defer"
    assert rhythm.min_gap_seconds == 18.0


def test_committed_speech_family_excludes_silence_from_neuro_vote():
    commitment = commitment_payload(
        origin="expression_urge", source_id="urge:v66", semantic_target="ý mới của Eris",
        pressure=0.41, freshness=0.82, persistence=0.9,
    )
    speak = _coalition("speak", 0.10, {"expression_commitment": commitment})
    silence = _coalition("silence", 5.0)
    frame = _frame((speak, silence))
    result = NeuroActionSelector().select(frame)
    assert result.winner_index == 0
    assert result.probabilities[0] == 1.0
    assert result.probabilities[1] == 0.0


def test_semantic_critic_can_defer_weak_committed_expression_without_dropping_it():
    commitment = commitment_payload(
        origin="expression_urge", source_id="urge:v66", semantic_target="ý mới của Eris",
        pressure=0.8, freshness=0.9, persistence=0.9,
    )
    speak = _coalition("speak", 0.55, {"expression_commitment": commitment})
    silence = _coalition("silence", 0.54)
    frame = _frame((speak, silence))
    selector = NeuroActionSelector()
    broadcast = WorkspaceBroadcast(signals=(), surprise=0.0, precision=0.7, resource=1.0, social_drive=0.5)
    baseline = SelectionResult(
        winner_index=0, scores=(0.55, 0.54), probabilities=(1.0, 0.0), margin=0.01,
        entropy=0.0, requires_deliberation=False, parallel_review_recommended=False,
        reason="fast_gate:test", broadcast=broadcast,
    )
    appraisal = NeuroSemanticAppraisal(
        choices=(
            ChoiceSemanticAppraisal(index=0, motive_validity=0.1, novelty=0.1, continuity_fit=0.1, epistemic_support=0.2, reversibility=0.4),
            ChoiceSemanticAppraisal(index=1, motive_validity=0.9, novelty=0.9, continuity_fit=0.9, epistemic_support=1.0, reversibility=1.0),
        ),
        boundary_pressure=1.0,
        interruption_cost=1.0,
        confidence=1.0,
        reason="model_thinks_quiet_is_safer",
    )
    merged = selector.merge_semantic_appraisal(frame, baseline, appraisal)
    # Slow semantic review may DEFER a weak/unsupported expression by choosing
    # silence for this tick. The underlying ExpressionCommitment remains in its
    # store and may surface later when motive/evidence becomes strong enough.
    assert merged.winner_index == 1
    assert merged.reason.startswith("semantic_critique_deferred_commitment:")


def test_explicit_runtime_boundary_releases_commitment_lock():
    commitment = commitment_payload(
        origin="expression_urge", source_id="urge:v66", semantic_target="ý mới của Eris"
    )
    speak = _coalition("speak", 0.1, {"expression_commitment": commitment})
    silence = _coalition("silence", 2.0)
    frame = _frame((speak, silence), {"explicit_quiet_request": True})
    result = NeuroActionSelector().select(frame)
    assert result.winner(frame).action_kind == "silence"


def test_vision_bootstrap_no_longer_installs_monkey_patch():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "vision.py").read_text(encoding="utf-8")
    assert "install_expression_commitment_authority" not in source
    assert "from expression_commitment_authority" not in source


def test_surface_is_realization_only_not_a_second_action_voter():
    """The retired autonomous surface has no callable public writer entry."""
    from runtime.ai_orchestrated import model_router

    assert not hasattr(model_router.AIRuntimeModelRouter, "realize_surface_speech")
    assert not hasattr(model_router, "SURFACE_INTENT_SCHEMA")
    assert not hasattr(model_router.AIRuntimeModelRouter, "plan_surface_intent")


def test_user_acknowledgement_does_not_punish_expressed_intention():
    """Một tiếng 'ừ' không được biến thành low-engagement punishment."""
    from cortex.subject.contracts import ObservationOrigin
    from cortex.subject.initiative import (
        InitiativeEngine,
        InitiativeState,
        IntentionStatus,
        MotiveKind,
        PendingIntention,
    )

    intention = PendingIntention(
        intention_id="intention:understand:v66",
        motive_kind=MotiveKind.UNDERSTAND_USER,
        semantic_goal="hiểu thêm một góc của user",
        target="user",
        pressure=0.72,
        commitment_strength=0.81,
        created_at=10.0,
        updated_at=20.0,
        evidence_refs=("conversation:1",),
        source_origin=ObservationOrigin.INTERNAL,
        status=IntentionStatus.EXPRESSED,
        last_expressed_at=20.0,
        cooldown_until=200.0,
    )
    state = InitiativeState(revision=1, updated_at=20.0, pending_intentions=(intention,))
    result = InitiativeEngine(lambda: "id").observe_user_outcome(state, user_text="ừ", now=30.0)
    after = result.pending_intentions[0]

    assert after.status is IntentionStatus.EXPRESSED
    assert after.pressure == intention.pressure
    assert after.commitment_strength == intention.commitment_strength
    assert after.cooldown_until == intention.cooldown_until
    assert after.outcome_count == 1


def test_reply_expected_is_conversation_context_not_user_floor_ownership():
    decision = InitiativeArbiter().decide(
        _intention(), relational={},
        runtime_context={
            "reply_expected": True,
            "seconds_since_last_autonomous_speech": 100.0,
        },
        now=130.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.SPEAK
    assert decision.reason == "self_owned_expression"


def test_expression_boundary_only_changes_on_explicit_user_directive():
    from runtime.expression_boundary_preference import detect_expression_boundary_directive

    assert detect_expression_boundary_directive("im đi, tôi đang họp") == "disable"
    assert detect_expression_boundary_directive("cứ nói đi, mở lên là muốn có tiếng") == "enable"
    assert detect_expression_boundary_directive("tôi chưa trả lời thôi") == ""
    assert detect_expression_boundary_directive("hôm nay tôi hơi bận") == ""
