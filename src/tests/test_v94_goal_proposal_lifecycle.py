# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from runtime.ai_orchestrated.contracts import RuntimeMindState, StateEvent
from runtime.ai_orchestrated.state_reducer import StateReducer
from runtime.proposal_lifecycle import ProposalLifecycleRuntime
from runtime.subject_initiative_runtime import filter_affordances_by_proposal_lifecycle


def _observation_event(
    *,
    turn_seq: int,
    open_loop: str = "",
    owner: str = "unknown",
    relationship_meaning: str = "neutral",
    event_kind: str = "chat",
) -> StateEvent:
    return StateEvent(
        kind="cognitive_observation",
        turn_id=f"turn:{turn_seq}",
        turn_seq=turn_seq,
        base_version=-1,
        source="test",
        payload={
            "event_kind": event_kind,
            "confidence": 0.92,
            "ambiguity": 0.08,
            "relationship_meaning": relationship_meaning,
            "open_loop": open_loop,
            "open_loop_owner": owner,
            "evidence": [f"turn:{turn_seq}"],
        },
    )


def test_v94_goal_record_suspends_after_two_unconfirmed_user_turns() -> None:
    reducer = StateReducer(initial_state=RuntimeMindState())
    try:
        assert reducer.apply_now(_observation_event(
            turn_seq=1,
            open_loop="User wants ERIS to continue checking the GitPilot failure.",
            owner="user",
        ))
        active = reducer.snapshot()
        assert active.open_loops == ("User wants ERIS to continue checking the GitPilot failure.",)
        assert len(active.goal_records) == 1
        assert active.goal_records[0].state == "active"
        assert active.goal_records[0].unconfirmed_user_turns == 0

        assert reducer.apply_now(_observation_event(turn_seq=2))
        after_one = reducer.snapshot()
        assert after_one.goal_records[0].state == "active"
        assert after_one.goal_records[0].unconfirmed_user_turns == 1
        assert after_one.open_loops

        assert reducer.apply_now(_observation_event(turn_seq=3))
        suspended = reducer.snapshot()
        assert suspended.goal_records[0].state == "suspended"
        assert suspended.goal_records[0].unconfirmed_user_turns == 2
        # V94: goal suspended không được tiếp tục materialize thành shared debt.
        assert suspended.open_loops == ()
    finally:
        reducer.close()


def test_v94_same_goal_confirmation_reactivates_suspended_goal() -> None:
    reducer = StateReducer(initial_state=RuntimeMindState())
    try:
        reducer.apply_now(_observation_event(turn_seq=1, open_loop="Continue repo review", owner="shared"))
        reducer.apply_now(_observation_event(turn_seq=2))
        reducer.apply_now(_observation_event(turn_seq=3))
        assert reducer.snapshot().goal_records[0].state == "suspended"

        reducer.apply_now(_observation_event(turn_seq=4, open_loop="Continue repo review", owner="shared"))
        revived = reducer.snapshot()
        assert revived.goal_records[0].state == "active"
        assert revived.goal_records[0].unconfirmed_user_turns == 0
        assert revived.goal_records[0].last_confirmed_turn_seq == 4
        assert revived.open_loops == ("Continue repo review",)
    finally:
        reducer.close()


def test_v94_explicit_closure_resolves_active_goal() -> None:
    reducer = StateReducer(initial_state=RuntimeMindState())
    try:
        reducer.apply_now(_observation_event(turn_seq=1, open_loop="Continue repo review", owner="user"))
        reducer.apply_now(_observation_event(turn_seq=2, relationship_meaning="closure"))
        closed = reducer.snapshot()
        assert closed.goal_records[0].state == "resolved"
        assert closed.open_loops == ()
    finally:
        reducer.close()


def _affordance(*, topic: str = "continue goal", evidence_refs=("goal:1",)):
    candidate = SimpleNamespace(
        available=True,
        source="subject_initiative",
        source_kind="subject_initiative",
        speech_act="resume_shared_goal",
        anchor_id="intention:contribute:abc",
        topic=topic,
        evidence_refs=tuple(evidence_refs),
    )
    return SimpleNamespace(
        affordance_id="subject-intention:intention:contribute:abc",
        owner="subject_initiative",
        candidate=candidate,
    )


def _plan(*, evidence_refs=("goal:1",)):
    return SimpleNamespace(
        source_kind="subject_initiative",
        source_ref="intention:contribute:abc",
        evidence_refs=tuple(evidence_refs),
        semantics=SimpleNamespace(speech_act="resume_shared_goal"),
        topic="continue goal",
    )


def test_v94_proposal_silence_creates_exponential_backoff() -> None:
    runtime = ProposalLifecycleRuntime()
    affordance = _affordance()
    assert runtime.allow_affordance(affordance, now=100.0).allowed is True

    first = runtime.note_plan_outcome(_plan(), outcome="silence", now=100.0)
    assert first.state == "deferred"
    assert first.silence_count == 1
    assert first.next_eligible_at > 100.0
    assert runtime.allow_affordance(affordance, now=first.next_eligible_at - 0.01).allowed is False
    assert runtime.allow_affordance(affordance, now=first.next_eligible_at + 0.01).allowed is True

    second = runtime.note_plan_outcome(_plan(), outcome="silence", now=first.next_eligible_at + 0.02)
    assert second.silence_count == 2
    assert (second.next_eligible_at - second.updated_at) > (first.next_eligible_at - first.updated_at)


def test_v94_new_evidence_resets_deferred_proposal() -> None:
    runtime = ProposalLifecycleRuntime()
    runtime.note_plan_outcome(_plan(evidence_refs=("goal:1",)), outcome="silence", now=100.0)
    blocked = runtime.allow_affordance(_affordance(evidence_refs=("goal:1",)), now=101.0)
    assert blocked.allowed is False

    # Goal được user xác nhận lại ở turn mới -> fingerprint evidence đổi -> proposal được thử lại.
    refreshed = runtime.allow_affordance(_affordance(evidence_refs=("goal:1", "goal-confirmed:4")), now=101.0)
    assert refreshed.allowed is True
    assert refreshed.reason == "evidence_changed"


def test_v94_affordance_filter_drops_only_deferred_duplicate() -> None:
    host = SimpleNamespace()
    runtime = ProposalLifecycleRuntime()
    host.proposal_lifecycle_runtime = runtime
    item = _affordance()
    runtime.note_plan_outcome(_plan(), outcome="silence", now=100.0)

    kept = filter_affordances_by_proposal_lifecycle(host, (item,), now=101.0)
    assert kept == ()

    changed = _affordance(evidence_refs=("goal:1", "goal-confirmed:9"))
    kept_after_evidence = filter_affordances_by_proposal_lifecycle(host, (changed,), now=101.0)
    assert kept_after_evidence == (changed,)


def test_v94_neuro_silence_can_backoff_affordance_before_plan_exists() -> None:
    runtime = ProposalLifecycleRuntime()
    item = _affordance()
    record = runtime.note_affordance_outcome(item, outcome="silence", now=200.0)
    assert record is not None
    assert record.state == "deferred"
    assert record.silence_count == 1
    assert runtime.allow_affordance(item, now=201.0).allowed is False


def test_v94_goal_records_roundtrip_and_active_projection() -> None:
    reducer = StateReducer(initial_state=RuntimeMindState())
    try:
        reducer.apply_now(_observation_event(turn_seq=1, open_loop="Continue repo review", owner="user"))
        state = reducer.snapshot()
        restored = RuntimeMindState.from_mapping(state.to_dict())
        assert len(restored.goal_records) == 1
        assert restored.goal_records[0].state == "active"
        assert restored.open_loops == ("Continue repo review",)
    finally:
        reducer.close()


def test_v94_suspended_goal_does_not_materialize_active_goal_context() -> None:
    from runtime.subject_initiative_runtime import build_initiative_runtime_context

    reducer = StateReducer(initial_state=RuntimeMindState())
    try:
        reducer.apply_now(_observation_event(turn_seq=1, open_loop="Continue repo review", owner="shared"))
        reducer.apply_now(_observation_event(turn_seq=2))
        reducer.apply_now(_observation_event(turn_seq=3))
        host = SimpleNamespace(
            ai_state_reducer=SimpleNamespace(snapshot=reducer.snapshot),
            private_mind_daemon=None,
            private_mind_system=None,
            private_mind_loop=None,
            _startup_greeting_spoken_at=0.0,
            _temporal_last_user_turn_at=0.0,
            _temporal_last_assistant_turn_at=0.0,
            last_autonomous_spoken_at=0.0,
        )
        context = build_initiative_runtime_context(host)
        assert context["active_goal_text"] == ""
        assert context["contribution_signal"] == 0.0
        assert context["active_goal_state"] == ""
    finally:
        reducer.close()
