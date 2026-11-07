# -*- coding: utf-8 -*-
"""Acceptance nhỏ cho V58 Initiative Controller.

Các test này cố ý kiểm hành vi SPEAK/WAIT/DROP thay vì chỉ kiểm class tồn tại.
"""
from types import SimpleNamespace

from cortex.subject.initiative_control import InitiativeArbiter, InitiativeControlAction
from runtime.subject_initiative_runtime import build_initiative_runtime_context, build_subject_initiative_affordances


def _intention(**overrides):
    base = {
        "intention_id": "intention:contribute:test",
        "motive_kind": "contribute",
        "semantic_goal": "tiếp tục mục tiêu đang dang dở của user bằng một bước nhỏ: hoàn thiện memory replay continuity",
        "target": "shared_task",
        "pressure": 0.72,
        "commitment_strength": 0.74,
        "created_at": 1000.0,
        "updated_at": 1000.0,
        "evidence_refs": ["runtime-mind:open_loop:0"],
        "status": "pending",
    }
    base.update(overrides)
    return base


def _relational(**overrides):
    base = {
        "opportunity": 0.82,
        "social_curiosity": 0.25,
        "interaction_satiety": 0.10,
        "interruption_cost": 0.10,
    }
    base.update(overrides)
    return base


def _runtime(**overrides):
    base = {
        "active_goal_text": "hoàn thiện memory replay continuity",
        "user_response_rate": 0.9,
        "social_refractory_cost": 0.0,
        "recent_unsolicited_speech": 0.0,
        "last_public_turn_unanswered": False,
        "startup_social_cooldown_active": False,
        "seconds_since_last_user_turn": 180.0,
    }
    base.update(overrides)
    return base


def test_v58_contentful_persistent_goal_can_speak_without_new_user_command():
    decision = InitiativeArbiter().decide(
        _intention(), relational=_relational(), runtime_context=_runtime(),
        now=1040.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.SPEAK
    assert decision.score >= 0.52


def test_v66_user_busy_is_context_not_expression_boundary():
    decision = InitiativeArbiter().decide(
        _intention(), relational=_relational(), runtime_context=_runtime(),
        now=1040.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.SPEAK
    assert decision.reason == "self_owned_expression"


def test_v66_unanswered_user_does_not_revoke_expression():
    decision = InitiativeArbiter().decide(
        _intention(), relational=_relational(),
        runtime_context=_runtime(recent_unsolicited_speech=1.0, last_public_turn_unanswered=True),
        now=1040.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.SPEAK
    assert decision.reason == "self_owned_expression"


def test_v58_generic_curiosity_does_not_turn_into_npc_question():
    generic = _intention(
        intention_id="intention:understand:test",
        motive_kind="understand_user",
        semantic_goal="thăm dò một điều còn chưa biết về user",
        pressure=0.95,
        commitment_strength=0.90,
    )
    decision = InitiativeArbiter().decide(
        generic, relational=_relational(), runtime_context=_runtime(),
        now=1040.0, contentful=False,
    )
    assert decision.action is InitiativeControlAction.WAIT
    assert decision.reason == "no_contentful_object"


def test_v58_stale_weak_intention_is_dropped():
    decision = InitiativeArbiter().decide(
        _intention(pressure=0.18, commitment_strength=0.22, created_at=100.0),
        relational=_relational(), runtime_context=_runtime(),
        now=2000.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.DROP


def test_v58_runtime_context_uses_real_open_loop_as_active_goal():
    state = SimpleNamespace(open_loops=("Hoàn thiện module memory replay, đang kẹt continuity",))
    host = SimpleNamespace(
        ai_state_reducer=SimpleNamespace(snapshot=lambda: state),
        private_mind_daemon=None, private_mind_system=None, private_mind_loop=None,
        _startup_greeting_spoken_at=0.0,
        _temporal_last_user_turn_at=0.0,
        _temporal_last_assistant_turn_at=0.0,
        last_autonomous_spoken_at=0.0,
    )
    context = build_initiative_runtime_context(host)
    assert context["active_goal_text"].startswith("Hoàn thiện module memory replay")
    assert context["contribution_signal"] >= 0.60
    assert context["active_goal_evidence_refs"] == ("runtime-mind:open_loop:0",)


def test_v58_bridge_only_materializes_when_controller_says_speak():
    notes = []
    class Subject:
        def initiative_view(self):
            return {"revision": 4, "pending_intentions": [_intention()]}
        def relational_view(self, *, now=None):
            return _relational()
        def note_initiative_control(self, intention_id, **kwargs):
            notes.append((intention_id, kwargs))

    affordances = build_subject_initiative_affordances(
        Subject(), runtime_context=_runtime(), now=1040.0,
    )
    assert len(affordances) == 1
    affordance = affordances[0]
    assert affordance.candidate.speech_act == "resume_shared_goal"
    assert affordance.candidate.kind == "followup_reminder"
    assert affordance.metadata["initiative_control"]["action"] == "speak"
    assert notes[-1][1]["action"] == "speak"


def test_v66_reply_expected_is_not_a_hard_expression_boundary():
    notes = []
    class Subject:
        def initiative_view(self):
            return {"revision": 4, "pending_intentions": [_intention()]}
        def relational_view(self, *, now=None):
            return _relational()
        def note_initiative_control(self, intention_id, **kwargs):
            notes.append(kwargs)

    affordances = build_subject_initiative_affordances(
        Subject(), runtime_context=_runtime(reply_expected=True), now=1040.0,
    )
    # Eris từng hỏi không có nghĩa user đang giữ lượt. Self-rhythm/collision mới
    # quyết định DEFER; silence của user không chặn intention contentful.
    assert len(affordances) == 1
    assert affordances[0].metadata["initiative_control"]["action"] == "speak"
    assert notes[-1]["action"] == "speak"


def test_v66_recent_user_turn_is_not_a_veto_after_baton_release():
    decision = InitiativeArbiter().decide(
        _intention(), relational=_relational(),
        runtime_context=_runtime(seconds_since_last_user_turn=20.0),
        now=1040.0, contentful=True,
    )
    assert decision.action is InitiativeControlAction.SPEAK
    assert decision.reason == "self_owned_expression"


def test_v58_drop_changes_pending_intention_to_abandoned():
    from cortex.subject.initiative import InitiativeEngine, InitiativeState, IntentionStatus, MotiveKind, PendingIntention
    from cortex.subject.contracts import ObservationOrigin

    item = PendingIntention(
        intention_id="intention:drop:test", motive_kind=MotiveKind.CONTRIBUTE,
        semantic_goal="mục tiêu cũ", target="shared_task", pressure=0.1,
        commitment_strength=0.2, created_at=1.0, updated_at=1.0,
        evidence_refs=("test:goal",), source_origin=ObservationOrigin.INTERNAL,
    )
    state = InitiativeState(revision=1, updated_at=1.0, pending_intentions=(item,))
    engine = InitiativeEngine(lambda: "id")
    after = engine.mark_abandoned(state, intention_id=item.intention_id, now=1000.0)
    assert after.pending_intentions[0].status is IntentionStatus.ABANDONED
    assert after.pending_intentions[0].cooldown_until > 1000.0
